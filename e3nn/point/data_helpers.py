# pylint: disable=arguments-differ, redefined-builtin, missing-docstring, no-member, invalid-name, line-too-long, not-callable
import collections

import torch
import torch_geometric as tg
from ase import Atoms, neighborlist
from pymatgen.core.structure import Structure

from e3nn import o3, rs
from e3nn.tensor import SphericalTensor


class DataNeighbors(tg.data.Data):
    def __init__(self, x, pos, r_max, self_interaction=True, **kwargs):
        edge_index, edge_attr = _neighbor_list_and_relative_vec(pos, r_max, self_interaction)
        super().__init__(x=x, edge_index=edge_index, edge_attr=edge_attr, pos=pos, **kwargs)


class DataPeriodicNeighbors(tg.data.Data):
    def __init__(self, x, pos, lattice, r_max, self_interaction=True, **kwargs):
        edge_index, edge_attr = _neighbor_list_and_relative_vec_lattice(pos, lattice, r_max, self_interaction)
        super().__init__(x=x, edge_index=edge_index, edge_attr=edge_attr, pos=pos, lattice=lattice, **kwargs)


class DataEdgeNeighbors(tg.data.Data):
    """Constructs graph to perform edge convolutions.

    Symmetric edges have not yet been implemented for this class.

    Args:
        x (torch.tensor shape [N, rs.dim(Rs_in)]): Node features.
        Rs_in (rs.TY_RS_STRICT): Representation list of input.
        pos (torch.tensor shape [N, 3]): Cartesian coordinates of nodes.
        r_max (float): Radial cutoff for edges.
        lmax (int > 0): Maximum L to use for SphericalTensor projection of radial distance vectors
        self_interaction (bool, optional): Include self interactions of nodes. Defaults to True.
        symmetric_edges (bool, optional): Constrain edge features to be symmetric in node index. Defaults to False.
        self_edge (float, optional): L=0 feature for self edges. Defaults to 1.
    """
    def __init__(self, x, Rs_in, pos, r_max, lmax,
                 self_interaction=True, symmetric_edges=False, self_edge=1., **kwargs):
        edge_index, edge_attr = _neighbor_list_and_relative_vec(pos, r_max, self_interaction)
        edge_index_dict, edge_edges, edge_edge_index = _get_edge_edges_and_index(edge_index, symmetric_edges=symmetric_edges)
        edge_edge_attr = []
        for _, edge2 in edge_edges:
            target2, source2 = edge2
            edge_edge_attr.append(
                pos[source2] - pos[target2]
            )

        edge_edge_index = torch.LongTensor(edge_edge_index).transpose(0, 1)
        edge_edge_attr = torch.stack(edge_edge_attr, dim=0)

        edge_x, Rs_in_edge = _initialize_edges(x, Rs_in, pos, edge_index_dict, lmax, self_edge=self_edge)

        super(DataEdgeNeighbors, self).__init__(
            x=x, edge_x=edge_x, edge_index=edge_index, edge_edge_index=edge_edge_index,
            edge_attr=edge_attr, edge_edge_attr=edge_edge_attr, pos=pos, Rs_in=Rs_in,
            Rs_in_edge=Rs_in_edge, edge_index_dict=edge_index_dict, **kwargs)


class DataEdgePeriodicNeighbors(tg.data.Data):
    """Constructs periodic graph to perform edge convolutions.

    symmetric_edges has not yet been implemented for this class.

    Args:
        x (torch.tensor shape [N, rs.dim(Rs_in)]): Node features.
        Rs_in (rs.TY_RS_STRICT): Representation list of input.
        pos (torch.tensor shape [N, 3]): Cartesian coordinates of nodes.
        lattice (torch.tensor shape [3, 3]): Lattice vectors of unit cell.
        r_max (float): Radial cutoff for edges.
        lmax (int > 0): Maximum L to use for SphericalTensor projection of radial distance vectors
        self_interaction (bool, optional): Include self interactions of nodes. Defaults to True.
        self_edge (float, optional): L=0 feature for self edges. Defaults to 1.
    """
    def __init__(self, x, Rs_in, pos, lattice, r_max, lmax,
                 self_interaction=True, self_edge=1., **kwargs):
        edge_index, edge_attr = _neighbor_list_and_relative_vec_lattice(pos, lattice, r_max, self_interaction)
        edge_index_dict, edge_edges, edge_edge_index = _get_edge_edges_and_index(edge_index, symmetric_edges=False)
        edge_edge_attr = []
        for _, edge2 in edge_edges:
            target2, source2 = edge2
            edge_edge_attr.append(
                pos[source2] - pos[target2]
            )

        edge_edge_index = torch.LongTensor(edge_edge_index).transpose(0, 1)
        edge_edge_attr = torch.stack(edge_edge_attr, dim=0)

        edge_x, Rs_in_edge = _initialize_edges(x, Rs_in, pos, edge_index_dict, lmax, self_edge=self_edge)

        super(DataEdgePeriodicNeighbors, self).__init__(
            x=x, edge_x=edge_x, edge_index=edge_index, edge_edge_index=edge_edge_index,
            edge_attr=edge_attr, edge_edge_attr=edge_edge_attr, pos=pos, Rs_in=Rs_in,
            Rs_in_edge=Rs_in_edge, edge_index_dict=edge_index_dict, **kwargs)


def _neighbor_list_and_relative_vec(pos, r_max, self_interaction=True):
    """Create neighbor list and neighbor vectors based on radial cutoff.

    Create neighbor list (``edge_index``) and relative vectors
    (``edge_attr``) based on radial cutoff.

    Edges are given by the following convention:
    - ``edge_index[0]`` is the *source* (convolution center).
    - ``edge_index[1]`` is the *target* (neighbor).

    Thus, ``edge_index`` has the same convention as the relative vectors:
    :math:`\\vec{r}_{source, target}`

    Args:
        pos (torch.tensor shape [N, 3]): Positional coordinates.
        r_max (float): Radial cutoff distance for neighbor finding.
        self_interaction (bool): Whether or not to include self-edges in the neighbor list.

    Returns:
        edge_index (torch.tensor shape [2, num_edges]): List of edges.
        edge_attr (torch.tensor shape [num_edges, 3]): Relative vectors corresponding to each edge.

    """
    N, _ = pos.shape
    assert _ == 3
    atoms = Atoms(symbols=['H'] * N, positions=pos.cpu().detach().numpy())
    nl = neighborlist.NeighborList(
        [r_max / 2.] * N,  # NeighborList looks for intersecting spheres
        self_interaction=self_interaction,
        bothways=True,
        skin=0.0,
    )
    nl.update(atoms)

    nei_list = []
    geo_list = []

    for i, p in enumerate(pos):
        indices, _displacements = nl.get_neighbors(i)
        if self_interaction:
            assert indices[-1] == i
            indices = indices[:-1]  # Remove extra self edge
        nei_list.append(torch.LongTensor([[i, target] for target in indices]))
        geo_list.append(pos[indices] - p)
    edge_index = torch.cat(nei_list, dim=0).transpose(1, 0)
    edge_attr = torch.cat(geo_list, dim=0)
    return edge_index, edge_attr


def _neighbor_list_and_relative_vec_lattice(pos, lattice, r_max, self_interaction=True, r_min=1e-8):
    """Create neighbor list and neighbor vectors based on radial cutoff and periodic lattice.

    Create neighbor list (``edge_index``) and relative vectors
    (``edge_attr``) based on radial cutoff.

    Edges are given by the following convention:
    - ``edge_index[0]`` is the *source* (convolution center).
    - ``edge_index[1]`` is the *target* (neighbor).

    Thus, ``edge_index`` has the same convention as the relative vectors:
    :math:`\\vec{r}_{source, target}`

    Relative vectors are given for the different images of the neighbor point within ``r_max``.

    Args:
        pos (torch.tensor shape [N, 3]): Positional coordinates.
        lattice (torch.tensor shape [3, 3]): Lattice vectors.
        r_max (float): Radial cutoff distance for neighbor finding.
        self_interaction (bool): Whether or not to include self-edges in the neighbor list.
        r_min (float): Numerical tolerance for determining if points coincide.

    Returns:
        edge_index (torch.tensor shape [2, num_edges]): List of edges.
        edge_attr (torch.tensor shape [num_edges, 3]): Relative vectors corresponding to each edge.

    """
    N, _ = pos.shape
    structure = Structure(lattice, ['H'] * N, pos, coords_are_cartesian=True)

    nei_list = []
    geo_list = []

    neighbors = structure.get_all_neighbors(
        r_max,
        include_index=True,
        include_image=True,
        numerical_tol=r_min
    )
    for i, (site, neis) in enumerate(zip(structure, neighbors)):
        indices, cart = zip(*[(n.index, n.coords) for n in neis])
        cart = torch.tensor(cart)
        indices = torch.LongTensor([[i, target] for target in indices])
        dist = cart - torch.tensor(site.coords)
        if self_interaction:
            self_index = torch.LongTensor([[i, i]])
            indices = torch.cat([self_index, indices], dim=0)
            self_dist = pos.new_zeros(1, 3, dtype=dist.dtype)
            dist = torch.cat([self_dist, dist], dim=0)
        nei_list.append(indices)
        geo_list.append(dist)

    edge_index = torch.cat(nei_list, dim=0).transpose(1, 0)
    edge_attr = torch.cat(geo_list, dim=0)
    return edge_index, edge_attr


def _initialize_edges(x, Rs_in, pos, edge_index_dict, lmax, self_edge=1., symmetric_edges=False):
    """Initialize edge features of DataEdgeNeighbors using node features and SphericalTensor.

    Args:
        x (torch.tensor shape [N, rs.dim(Rs_in)]): Node features.
        Rs_in (rs.TY_RS_STRICT): Representation list of input.
        pos (torch.tensor shape [N, 3]): Cartesian coordinates of nodes.
        edge_index (torch.LongTensor shape [2, num_edges]): Edges described by index of node target then node source.
        lmax (int > 0): Maximum L to use for SphericalTensor projection of radial distance vectors
        self_edge (float, optional): L=0 feature for self edges. Defaults to 1.
        symmetric_edges (bool, optional): Constrain edge features to be symmetric in node index. Defaults to False

    Returns:
        edge_x: Edge features.
        Rs_edge (rs.TY_RS_STRICT): Representation list of edge features.
    """
    edge_x = []
    if symmetric_edges:
        Rs, Q = rs.reduce_tensor('ij=ji', i=Rs_in)
    else:
        Rs, Q = rs.reduce_tensor('ij', i=Rs_in, j=Rs_in)
    Q = Q.reshape(-1, rs.dim(Rs_in), rs.dim(Rs_in))
    Rs_sph = [(1, l, (-1)**l) for l in range(lmax + 1)]
    tp_kernel = rs.TensorProduct(Rs, Rs_sph, o3.selection_rule)
    keys, values = list(zip(*edge_index_dict.items()))
    sorted_edges = sorted(zip(keys, values), key=lambda x: x[1])
    for (target, source), _ in sorted_edges:
        Ia = x[target]
        Ib = x[source]
        vector = (pos[source] - pos[target]).reshape(-1, 3)
        if torch.allclose(vector, torch.zeros(vector.shape)):
            signal = torch.zeros(rs.dim(Rs_sph))
            signal[0] = self_edge
        else:
            signal = SphericalTensor.from_geometry(vector, lmax=lmax).signal
            if symmetric_edges:
                signal += SphericalTensor.from_geometry(-vector, lmax=lmax).signal
                signal *= 0.5
        output = torch.einsum('kij,i,j->k', Q, Ia, Ib)
        output = tp_kernel(output, signal)
        edge_x.append(output)
    edge_x = torch.stack(edge_x, dim=0)
    return edge_x, tp_kernel.Rs_out


def _get_edge_edges_and_index(edge_index, symmetric_edges=False):
    """Given edge_index, construct edge_edges and edge_edge_index.

    Args:
        edge_index (torch.LongTensor shape [2, num_edges]): Edges described by index of node target then node source.
        symmetric_edges (bool, optional): Constrain edge features to be symmetric in node index. Defaults to False

    Returns:
        edge_index_dict: Dictionary of edge in terms of node indices and edge index.
        edge_edges: Pairs of edges over which to do edge convolutions using node indices. [num_edge_edges, 2, 2]
        edge_edge_index: Pairs of edges over which to do edge convolutions using edge indices. [num_edge_edges, 2]
    """
    edge_edges = []
    for target1, source1 in edge_index.transpose(1, 0).numpy():
        for target2, source2 in edge_index.transpose(1, 0).numpy():
            if target1 == target2:
                edge_edges.append(
                    [[target1, source1], [target2, source2]]
                )
    if symmetric_edges:
        distinct_edges = sorted(set(map(tuple,
                                        torch.sort(edge_index.transpose(1, 0),
                                                   dim=-1)[0].numpy().tolist())))
        print(distinct_edges)
        edge_index_dict = collections.OrderedDict(zip(distinct_edges, range(len(distinct_edges))))
        edge_edge_index = [
            [edge_index_dict[tuple(sorted(edge1))], edge_index_dict[tuple(sorted(edge2))]]
            for edge1, edge2 in edge_edges
        ]
    else:
        edge_index_dict = collections.OrderedDict(zip(map(tuple, edge_index.transpose(1, 0).numpy()), range(edge_index.shape[-1])))
        edge_edge_index = [
            [edge_index_dict[tuple(edge1)], edge_index_dict[tuple(edge2)]]
            for edge1, edge2 in edge_edges
        ]
    return edge_index_dict, edge_edges, edge_edge_index

# pylint: disable=C,R,E1101
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm
from mpl_toolkits.mplot3d import Axes3D  # pylint: disable=W
from lie_learn.representations.SO3.spherical_harmonics import sh  # real valued by default
from se3_cnn.basis_kernels import _basis_transformation_Q_J
from se3_cnn.util.cache_file import cached_dirpklgz


def beta_alpha(n):
    beta = np.linspace(0, np.pi / 2, n)
    alpha = np.arange(2 * n) * 2 * np.pi / (2 * n)
    beta, alpha = np.meshgrid(beta, alpha, indexing='ij')
    return beta, alpha


@cached_dirpklgz("Y_sphere_cache")
def _sample_Y(n, J):
    beta, alpha = beta_alpha(n)
    
    Y_J = np.zeros((2 * J + 1, len(beta.flatten())))
    for idx_m in range(2 * J + 1):
        m = idx_m - J
        for idx, (b, a) in enumerate(zip(beta.flatten(), alpha.flatten())):
            Y_J[idx_m, idx] = sh(J, m, b, a)
            
    return Y_J


def _sample_sh_sphere(n, order_in, order_out):
    order_irreps = range(abs(order_in - order_out), order_in + order_out + 1)
    
    sh_spheres = []
    for J in order_irreps:
        Y_J = _sample_Y(n, J)

        # compute basis transformation matrix Q_J
        Q_J = _basis_transformation_Q_J(J, order_in, order_out)
        K_J = np.einsum('mn,n...->m...', Q_J, Y_J)
        K_J = K_J.reshape(2 * order_out + 1, 2 * order_in + 1, n, 2 * n)
        sh_spheres.append(K_J)

    return np.array(sh_spheres)


def plot_sphere(beta, alpha, f):
    alpha = np.concatenate((alpha, alpha[:, :1]), axis=1)
    beta = np.concatenate((beta, beta[:, :1]), axis=1)
    f = np.concatenate((f, f[:, :1]), axis=1)

    x = np.sin(beta) * np.cos(alpha)
    y = np.sin(beta) * np.sin(alpha)
    z = np.cos(beta)

    fc = cm.gray(f)
    fc = plt.get_cmap("bwr")(f)

    #fig = plt.figure(figsize=(5, 3))
    #ax = fig.add_subplot(111, projection='3d', aspect=1)
    ax = plt.gca()
    ax.plot_surface(x, y, z, rstride=1, cstride=1, facecolors=fc)  # cm.gray(f))
    # Turn off the axis planes
    ax.view_init(azim=0, elev=90)
    ax.set_axis_off()
    a = 0.6
    ax.set_xlim3d(-a, a)
    ax.set_ylim3d(-a, a)
    ax.set_zlim3d(-a, a)


def main():
    scale = 1.5
    n = 50

    f = _sample_sh_sphere(n, 1, 1)
    f = (f - np.min(f)) / (np.max(f) - np.min(f))

    beta, alpha = beta_alpha(n)
    alpha = alpha - np.pi / (2 * n)

    nbase = f.shape[0]
    dim_out = f.shape[1]
    dim_in = f.shape[2]

    w = 1
    fig = plt.figure(figsize=(scale * (nbase * dim_in + (nbase - 1) * w), scale * dim_out))

    for base in range(nbase):
        for i in range(dim_out):
            for j in range(dim_in):
                width = 1 / (nbase * dim_in + (nbase - 1) * w)
                height = 1 / dim_out
                rect = [
                    (base * (dim_in + w) + j) * width,
                    (dim_out - i - 1) * height,
                    width,
                    height
                ]
                fig.add_axes(rect, projection='3d', aspect=1)
                plot_sphere(beta, alpha, f[base, i, j])

    plt.savefig("kernels.png")


main()
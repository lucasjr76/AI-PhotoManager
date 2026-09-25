"""DBSCAN with cosine distance, computed in tiles so the N x N matrix never exists.

Same result as sklearn.cluster.DBSCAN(metric="cosine") (see tests/test_dbscan.py):
a point is core when at least `min_samples` points (itself included) lie within
`eps`; core points within `eps` of each other share a cluster; a border point takes
the lowest-numbered cluster among its core neighbours, which is the one sklearn's
in-order expansion reaches first. Clusters are numbered by their lowest point index.
"""

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

DEFAULT_BLOCK = 2048


def _ranges(n: int, block: int) -> list[tuple[int, int]]:
    return [(start, min(start + block, n)) for start in range(0, n, block)]


def _upper_tiles(n: int, block: int) -> Iterator[tuple[int, int, int, int]]:
    ranges = _ranges(n, block)
    for a, (i0, i1) in enumerate(ranges):
        for j0, j1 in ranges[a:]:
            yield i0, i1, j0, j1


def _within(a: NDArray[np.floating], b: NDArray[np.floating], eps: float) -> NDArray[np.bool_]:
    distance: NDArray[np.floating] = 1.0 - a @ b.T
    return distance <= eps


def _compress(parent: NDArray[np.int64]) -> None:
    """Point every node straight at its root (pointer jumping)."""
    while True:
        grand = parent[parent]
        if np.array_equal(grand, parent):
            return
        parent[:] = grand


def _union(parent: NDArray[np.int64], gi: NDArray[np.int64], gj: NDArray[np.int64]) -> None:
    """Merge the components of every (gi[k], gj[k]) edge; roots stay the lowest index."""
    while gi.size:
        ri, rj = parent[gi], parent[gj]
        keep = ri != rj
        if not keep.any():
            return
        gi, gj, ri, rj = gi[keep], gj[keep], ri[keep], rj[keep]
        np.minimum.at(parent, np.maximum(ri, rj), np.minimum(ri, rj))
        _compress(parent)


def dbscan_cosine(
    x: NDArray[np.floating], *, eps: float, min_samples: int, block: int = DEFAULT_BLOCK
) -> NDArray[np.int64]:
    """Cluster L2-normalized rows of `x`. Returns labels, -1 for noise.

    Peak extra memory is O(block^2) per tile plus O(N) bookkeeping.
    """
    n = len(x)
    labels = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return labels

    # Pass 1: neighbour counts -> core points. Symmetric, so upper tiles only.
    counts = np.zeros(n, dtype=np.int64)
    for i0, i1, j0, j1 in _upper_tiles(n, block):
        near = _within(x[i0:i1], x[j0:j1], eps)
        counts[i0:i1] += near.sum(axis=1)
        if i0 != j0:
            counts[j0:j1] += near.sum(axis=0)
    core = counts >= min_samples

    # Pass 2: connected components of the core-core graph (union-find, vectorized).
    parent = np.arange(n, dtype=np.int64)
    for i0, i1, j0, j1 in _upper_tiles(n, block):
        rows_core, cols_core = core[i0:i1], core[j0:j1]
        if not rows_core.any() or not cols_core.any():
            continue
        near = _within(x[i0:i1], x[j0:j1], eps)
        near &= rows_core[:, None] & cols_core[None, :]
        r, c = np.nonzero(near)
        _union(parent, r.astype(np.int64) + i0, c.astype(np.int64) + j0)

    core_idx = np.flatnonzero(core)
    if core_idx.size == 0:
        return labels
    roots = parent[core_idx]
    # Roots are the lowest index of each component, so sorting them gives sklearn's order.
    unique_roots, cluster_of_core = np.unique(roots, return_inverse=True)
    labels[core_idx] = cluster_of_core
    del unique_roots

    # Pass 3: border points take the lowest cluster among their core neighbours.
    border_idx = np.flatnonzero(~core)
    no_cluster = np.iinfo(np.int64).max
    for b0, b1 in _ranges(border_idx.size, block):
        rows = border_idx[b0:b1]
        best = np.full(rows.size, no_cluster, dtype=np.int64)
        for c0, c1 in _ranges(core_idx.size, block):
            near = _within(x[rows], x[core_idx[c0:c1]], eps)
            candidate = np.where(near, labels[core_idx[c0:c1]][None, :], no_cluster).min(axis=1)
            np.minimum(best, candidate, out=best)
        labels[rows] = np.where(best == no_cluster, -1, best)
    return labels

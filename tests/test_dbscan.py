import tracemalloc

import numpy as np
import pytest
from sklearn.cluster import DBSCAN
from sklearn.metrics import adjusted_rand_score

from aipdm.core.cluster import dbscan_cosine


def synthetic(
    n_clusters: int, per_cluster: int, n_noise: int, dim: int, spread: float, seed: int
) -> np.ndarray:
    """Gaussian clusters around random unit centers, plus uniform noise; all L2-normalized."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(n_clusters, dim))
    points = [c + rng.normal(scale=spread, size=(per_cluster, dim)) for c in centers]
    points.append(rng.normal(size=(n_noise, dim)))
    x = np.concatenate(points)
    x = x[rng.permutation(len(x))]
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float64)


@pytest.mark.parametrize(
    ("seed", "eps", "min_samples", "block"),
    [
        (0, 0.35, 3, 2048),
        (1, 0.35, 3, 64),  # many tiles, clusters split across tile borders
        (2, 0.25, 5, 100),  # block not a divisor of N
        (3, 0.45, 2, 7),
    ],
)
def test_parity_with_sklearn(seed: int, eps: float, min_samples: int, block: int) -> None:
    x = synthetic(n_clusters=8, per_cluster=60, n_noise=80, dim=128, spread=0.06, seed=seed)
    expected = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(x)
    got = dbscan_cosine(x, eps=eps, min_samples=min_samples, block=block)
    assert adjusted_rand_score(expected, got) == 1.0
    assert np.array_equal(expected == -1, got == -1)  # same noise points


def test_parity_with_touching_clusters() -> None:
    """Loose clusters with shared border points exercise border-assignment order."""
    x = synthetic(n_clusters=5, per_cluster=80, n_noise=40, dim=16, spread=0.25, seed=7)
    for eps, min_samples in [(0.3, 4), (0.4, 6), (0.5, 8)]:
        expected = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(x)
        got = dbscan_cosine(x, eps=eps, min_samples=min_samples, block=33)
        assert adjusted_rand_score(expected, got) == 1.0


@pytest.mark.parametrize("seed", range(6))
def test_border_point_shared_by_two_clusters(seed: int) -> None:
    """A non-core point within eps of cores of two clusters: sklearn gives it the lower label."""
    angles = [0.0, 0.01, 0.02, 0.03, 0.49, 0.50, 0.51, 0.52, 0.26, 1.5, 2.5]
    x = np.array([[np.cos(a), np.sin(a)] for a in angles])
    x = x[np.random.default_rng(seed).permutation(len(x))]
    expected = DBSCAN(eps=0.027, min_samples=4, metric="cosine").fit_predict(x)
    border = int(np.flatnonzero(np.isclose(np.arctan2(x[:, 1], x[:, 0]), 0.26))[0])
    assert expected[border] != -1 and expected.max() == 1 and (expected == -1).sum() == 2
    got = dbscan_cosine(x, eps=0.027, min_samples=4, block=3)
    assert np.array_equal(got, expected)


def test_labels_follow_sklearn_numbering() -> None:
    x = synthetic(n_clusters=4, per_cluster=30, n_noise=10, dim=32, spread=0.05, seed=3)
    expected = DBSCAN(eps=0.3, min_samples=3, metric="cosine").fit_predict(x)
    assert np.array_equal(dbscan_cosine(x, eps=0.3, min_samples=3, block=16), expected)


def test_edge_cases() -> None:
    assert dbscan_cosine(np.empty((0, 128), np.float32), eps=0.3, min_samples=3).size == 0
    one = np.ones((1, 4), np.float32) / 2
    assert dbscan_cosine(one, eps=0.3, min_samples=1).tolist() == [0]
    assert dbscan_cosine(one, eps=0.3, min_samples=2).tolist() == [-1]


def test_memory_50k_vectors() -> None:
    """50k x 128-d must cluster without an N x N matrix (that alone would be 10 GB)."""
    x = synthetic(n_clusters=100, per_cluster=450, n_noise=5000, dim=128, spread=0.05, seed=11)
    x = x.astype(np.float32)
    assert x.shape == (50_000, 128)

    tracemalloc.start()
    labels = dbscan_cosine(x, eps=0.3, min_samples=3)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < 300 * 1024**2, f"pico de {peak / 1024**2:.0f} MB"
    assert len(set(labels.tolist()) - {-1}) == 100

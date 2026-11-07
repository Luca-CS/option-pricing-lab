"""Correlation matrices: the repair of a matrix that lost its positive semi-definiteness (a shifted, skewed or
measured-across-zones matrix)."""

from __future__ import annotations

import numpy as np


def nearest_correlation(c, iterations: int = 50) -> np.ndarray:
    """The closest positive semi-definite matrix with a unit diagonal, by alternating projections."""
    x = np.array(c, dtype=float)
    x = 0.5 * (x + x.T)
    for _ in range(iterations):
        lam, vec = np.linalg.eigh(x)
        if lam.min() >= -1e-12 and np.allclose(np.diag(x), 1.0, atol=1e-12):
            break
        x = (vec * np.maximum(lam, 0.0)) @ vec.T
        np.fill_diagonal(x, 1.0)
    lam, vec = np.linalg.eigh(x)
    if lam.min() < 0:
        x = (vec * np.maximum(lam, 1e-10)) @ vec.T
        d = np.sqrt(np.diag(x))
        x = x / np.outer(d, d)
    x = 0.5 * (x + x.T)
    np.fill_diagonal(x, 1.0)
    return x

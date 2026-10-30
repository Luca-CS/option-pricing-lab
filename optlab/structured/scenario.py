"""Scenarios: spots of named underlyings at the simulation times, one batch of paths at a time.

The evaluator only sees this interface, so the source of paths (NumPy `simulate`, later a GPU or multi-asset
engine) can be swapped without touching the language.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from ..market import Market
from ..models import Model, simulate


class Scenario:
    """One batch of antithetic paths, stored as [first half, mirrored half]."""

    def __init__(self, name: str, times, paths, spot0: float):
        self.name = name
        self.times = np.asarray(times, dtype=float)
        self.paths = paths
        self.spot0 = float(spot0)
        self.n_paths = paths.shape[0]
        self._col = {float(t): j for j, t in enumerate(self.times)}

    def _check(self, name: str):
        if name != self.name:
            raise KeyError(f"no paths for underlying {name!r} (this scenario simulates {self.name!r})")

    def spot(self, name: str, t: float):
        """Spot at time t; times <= 0 are the valuation date, where the spot is known."""
        self._check(name)
        if t <= 0:
            return self.spot0
        return self.paths[:, self._col[float(t)]]

    def fixings(self, name: str, times) -> np.ndarray:
        """(n_paths, len(times)) spots, the known spot repeated for times <= 0."""
        self._check(name)
        times = np.asarray(times, dtype=float)
        cols = [self._col[float(t)] for t in times if t > 0]
        out = self.paths[:, cols]
        n_past = int(np.sum(times <= 0))
        if n_past:
            out = np.hstack([np.full((self.n_paths, n_past), self.spot0), out])
        return out


def batch_sizes(n_paths: int, batch: int | None) -> list[int]:
    if n_paths % 2:
        raise ValueError("n_paths must be even (antithetic pairs)")
    batch = n_paths if batch is None else batch
    if batch % 2 or batch <= 0:
        raise ValueError("batch must be a positive even number")
    sizes = [batch] * (n_paths // batch)
    if n_paths % batch:
        sizes.append(n_paths % batch)
    return sizes


def simulated_scenarios(model: Model, market: Market, name: str, times, n_paths: int, seed=None,
                        batch: int | None = None, **sim_kwargs) -> Iterator[Scenario]:
    """Batches of `simulate` paths. With a single batch the generator is `default_rng(seed)`, exactly as in the
    pricers of `products.py`; with several, batch b uses `SeedSequence(seed).spawn(n_batches)[b]`. Prices
    therefore depend on (seed, n_paths, batch)."""
    sizes = batch_sizes(n_paths, batch)
    if len(sizes) == 1:
        rngs = [np.random.default_rng(seed)]
    else:
        rngs = [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(len(sizes))]
    times = np.asarray(times, dtype=float)
    for size, rng in zip(sizes, rngs):
        paths = simulate(model, market, times, size, rng, **sim_kwargs) if len(times) else np.empty((size, 0))
        yield Scenario(name, times, paths, market.spot)

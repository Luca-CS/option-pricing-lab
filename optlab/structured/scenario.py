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
    """One batch of antithetic paths, stored as [first half, mirrored half]. `history` maps past times (< 0, and
    optionally 0) to known fixings; the valuation date otherwise reads `spot0`."""

    def __init__(self, name: str, times, paths, spot0: float, history: dict | None = None):
        self.name = name
        self.times = np.asarray(times, dtype=float)
        self.paths = paths
        self.spot0 = float(spot0)
        self.history = {round(float(t), 12): float(v) for t, v in (history or {}).items()}
        self.n_paths = paths.shape[0]
        self._col = {float(t): j for j, t in enumerate(self.times)}

    def _past(self, t: float) -> float:
        key = round(float(t), 12)
        if key in self.history:
            return self.history[key]
        if t == 0:
            return self.spot0
        raise KeyError(f"no fixing of {self.name} at t = {t:.6f} (before the valuation date): give it in fixings")

    def _check(self, name: str):
        if name != self.name:
            raise KeyError(f"no paths for underlying {name!r} (this scenario simulates {self.name!r})")

    def spot(self, name: str, t: float):
        """Spot at time t; times <= 0 are known (history, or the spot on the valuation date)."""
        self._check(name)
        if t <= 0:
            return self._past(t)
        return self.paths[:, self._col[float(t)]]

    def fixings(self, name: str, times) -> np.ndarray:
        """(n_paths, len(times)) spots, known fixings repeated on every path for times <= 0."""
        self._check(name)
        times = np.asarray(times, dtype=float)
        cols = [self._col[float(t)] for t in times if t > 0]
        out = self.paths[:, cols]
        past = [self._past(t) for t in times if t <= 0]
        if past:
            out = np.hstack([np.tile(np.array(past), (self.n_paths, 1)), out])
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
                        batch: int | None = None, initial_fixing: float | None = None,
                        history: dict | None = None, **sim_kwargs) -> Iterator[Scenario]:
    """Batches of `simulate` paths. With a single batch the generator is `default_rng(seed)`, exactly as in the
    pricers of `products.py`; with several, batch b uses `SeedSequence(seed).spawn(n_batches)[b]`. Prices
    therefore depend on (seed, n_paths, batch). `initial_fixing` replaces the market spot for reads at t <= 0
    (a product already struck, priced on a bumped market for Greeks); `history` holds past fixings by time."""
    sizes = batch_sizes(n_paths, batch)
    if len(sizes) == 1:
        rngs = [np.random.default_rng(seed)]
    else:
        rngs = [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(len(sizes))]
    times = np.asarray(times, dtype=float)
    for size, rng in zip(sizes, rngs):
        paths = simulate(model, market, times, size, rng, **sim_kwargs) if len(times) else np.empty((size, 0))
        yield Scenario(name, times, paths, market.spot if initial_fixing is None else initial_fixing, history)

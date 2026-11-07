"""Randomised quasi-Monte Carlo: scrambled Sobol points with a Brownian bridge, fed to `models.simulate`.

`simulate` draws `rng.standard_normal(half)` once per time step (twice with a variance process). `SobolNormals`
is a Generator whose `standard_normal` serves, step by step, the normalised increments of Brownian paths built by
a bridge on the simulation grid from one scrambled Sobol point per path: the first coordinates, which Sobol fills
best, carry the terminal value and the coarse shape of each path. The antithetic mirror of `simulate` is kept.
The standard error comes from R independent scramblings (the spread of the R estimates), not from the pairs.
"""

from __future__ import annotations

import time

import numpy as np
from scipy.special import ndtri
from scipy.stats import qmc

from optlab.core.models import BlackScholesModel, time_grid
from optlab.core.products import MCResult
from optlab.structured.cashflows import Note
from optlab.structured.engine import Evaluation, _summary
from optlab.structured.pricer import StructuredResult, _program, check_fixing_scale, history_times
from optlab.structured.scenario import Scenario
from optlab.structured.script.timeline import Clock, build_timeline


def bridge_order(times: np.ndarray):
    """Construction order of a Brownian bridge on times[1:] (times[0] = 0): the last point, then midpoints by
    index bisection, level by level. Returns (point, left, right) index triples; left/right of the last point are
    (0, -1) meaning W(0) = 0 alone."""
    n = len(times) - 1
    steps = [(n, 0, -1)]
    level = [(0, n)]
    while level:
        nxt = []
        for lo, hi in level:
            if hi - lo > 1:
                mid = (lo + hi) // 2
                steps.append((mid, lo, hi))
                nxt += [(lo, mid), (mid, hi)]
        level = nxt
    return steps


def bridge_normals(z: np.ndarray, times: np.ndarray) -> np.ndarray:
    """Standard normal increments per step, (paths, steps), of Brownian paths built from z (paths, steps) in
    bridge order on `times` (starting at 0)."""
    n_paths, n = z.shape
    W = np.zeros((n_paths, n + 1))
    for k, (i, lo, hi) in enumerate(bridge_order(times)):
        if hi < 0:
            W[:, i] = np.sqrt(times[i]) * z[:, k]
            continue
        tl, ti, tr = times[lo], times[i], times[hi]
        W[:, i] = ((tr - ti) * W[:, lo] + (ti - tl) * W[:, hi]) / (tr - tl) + np.sqrt(
            (ti - tl) * (tr - ti) / (tr - tl)
        ) * z[:, k]
    return np.diff(W, axis=1) / np.sqrt(np.diff(times))


class SobolNormals(np.random.Generator):
    """A Generator for `simulate` whose standard normals come from scrambled Sobol points through a bridge."""

    def __init__(self, n_half: int, grid: np.ndarray, n_factors: int, seed):
        super().__init__(np.random.PCG64(seed))
        steps = len(grid) - 1
        sobol = qmc.Sobol(steps * n_factors, scramble=True, seed=np.random.default_rng(seed))
        m = int(np.log2(n_half))
        if 2**m != n_half:
            raise ValueError("RQMC needs a power of two of path pairs")
        u = np.clip(sobol.random_base2(m), 1e-12, 1 - 1e-12)
        z = ndtri(u)
        # bridge coordinates of the factors interleaved, so that every factor gets the best Sobol dimensions
        self._z = [bridge_normals(z[:, f::n_factors], grid) for f in range(n_factors)]
        self._n_factors, self._step, self._factor, self._n_steps = n_factors, 0, 0, steps

    def standard_normal(self, size=None, dtype=np.float64, out=None):
        if self._step >= self._n_steps:
            raise RuntimeError("the simulation asked for more steps than the Sobol grid has")
        z = self._z[self._factor][:, self._step]
        if size is not None and np.prod(size) != len(z):
            raise ValueError(f"asked for {size} normals, the Sobol block has {len(z)}")
        self._factor += 1
        if self._factor == self._n_factors:
            self._factor, self._step = 0, self._step + 1
        return z.copy()

    @property
    def consumed(self) -> bool:
        return self._step == self._n_steps


def simulation_grid(model, times, dt_max=1 / 252, dt_min=1 / 2000) -> np.ndarray:
    """The grid `models.simulate` uses (Black-Scholes collapses to one step per observation)."""
    if isinstance(model, BlackScholesModel):
        return time_grid(times, np.inf, None)
    return time_grid(times, dt_max, dt_min)


def price_rqmc(
    script,
    model,
    market,
    params: dict | None = None,
    fmt=None,
    m: int = 14,
    replications: int = 16,
    seed=0,
    valuation=None,
    calendar=None,
    fixings: dict | None = None,
    initial_fixing: float | None = None,
) -> StructuredResult:
    """RQMC price of a single-underlying script: `replications` scramblings of 2^m antithetic pairs each. The SE
    is the standard deviation of the scrambled estimates over sqrt(replications)."""
    from optlab.core.models import simulate

    t0 = time.perf_counter()
    fmt = fmt or Note()
    program = _program(script)
    (name,) = program.underlying_names
    clock = Clock(valuation, calendar)
    timeline = build_timeline(program, clock)
    history = history_times(fixings, name, clock)
    check_fixing_scale(history, market.spot, name)
    merged = {**program.param_values, **(params or {})}
    N = float(merged.get("N", 1.0))
    grid = simulation_grid(model, timeline.sim_times)
    n_factors = 2 if model.has_variance else 1
    spot0 = market.spot if initial_fixing is None else initial_fixing
    ev = Evaluation(program, timeline, params or {}, fmt, N)
    estimates, pairs = [], []
    for ss in np.random.SeedSequence(seed).spawn(replications):
        gen = SobolNormals(2**m, grid, n_factors, ss)
        paths = simulate(model, market, timeline.sim_times, 2 * 2**m, gen)
        if not gen.consumed:
            raise RuntimeError("the simulation used fewer steps than the Sobol grid: grids differ")
        summary = _summary(ev, Scenario(name, timeline.sim_times, paths, spot0, history), market)
        pairs.append(summary[0])
        estimates.append(float(summary[0].mean()))
    est = np.array(estimates)
    value = MCResult(float(est.mean()), float(est.std(ddof=1) / np.sqrt(replications)), replications * 2 ** (m + 1))
    seconds = time.perf_counter() - t0
    n_ev = len(program.events)
    return StructuredResult(
        value=value,
        price=100 * value.price / N,
        stderr=100 * value.stderr / N,
        fmt=fmt.name,
        notional=N,
        n_paths=value.n_paths,
        seconds=seconds,
        timings={"rqmc": seconds, "dimensions": len(grid[1:]) * n_factors},
        event_times=timeline.event_times,
        termination=np.zeros(n_ev),
        expected_flows={},
        expected_life=float(timeline.event_times[-1]),
        redemption_probabilities={},
        warnings=[f"RQMC: {replications} scramblings of 2^{m} pairs; SE from the scramblings"],
        method="rqmc",
        pairs=np.concatenate(pairs),
    )

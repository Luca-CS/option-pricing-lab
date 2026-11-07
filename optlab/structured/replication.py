"""Static replication of a European-class program on the implied volatility surface.

When the flows depend on the fixing at one date T only, the price is a one-dimensional integral against the
risk-neutral law of S_T, which the SVI slice at T gives in closed form. With k = ln(K/F), w(k) the total implied
variance and d1,2 = -k / sqrt(w) +- sqrt(w) / 2:
    P(S_T < K)        = N(-d2) + phi(d2) w'(k) / (2 sqrt(w))      (minus the strike derivative of the call)
    E[S_T 1{S_T < K}] = F - (F N(d1) - K N(d2)) - K P(S_T > K)    (from the undiscounted call)
Each cell in k is represented by its conditional mean of S_T, so a payoff linear in S_T on a cell is integrated
exactly; cells where the payoff is not linear (a strike or a barrier inside) are split, recursively. No Monte
Carlo noise, and any smile-consistent model gives the same price.
"""

from __future__ import annotations

import time

import numpy as np
from scipy.stats import norm

from optlab.market import Market
from optlab.products import MCResult
from optlab.structured.cashflows import Note
from optlab.structured.scenario import Scenario
from optlab.structured.script.evaluator import Evaluator
from optlab.structured.script.timeline import Timeline

TAIL = 1e-13


def terminal_cdf(surface, market: Market, T: float, k):
    """P(S_T < F e^k) and E[S_T 1{S_T < F e^k}] on the surface."""
    return _laws(surface, market, T, k)[0]


def _laws(surface, market, T, k):
    k = np.asarray(k, dtype=float)
    F = float(market.F(T))
    w, wk, _, _ = surface.derivatives(k, T)
    sw = np.sqrt(w)
    d1, d2 = -k / sw + sw / 2, -k / sw - sw / 2
    cdf = norm.cdf(-d2) + norm.pdf(d2) * wk / (2 * sw)
    K = F * np.exp(k)
    partial = F - (F * norm.cdf(d1) - K * norm.cdf(d2)) - K * (1 - cdf)
    return cdf, partial


def _bounds(surface, market, T):
    lo, hi = -1.0, 0.5
    while terminal_cdf(surface, market, T, [lo])[0] > TAIL and lo > -60:
        lo *= 1.5
    while 1 - terminal_cdf(surface, market, T, [hi])[0] > TAIL and hi < 20:
        hi *= 1.5
    return lo, hi


def _cells(surface, market, T, a, b, tails=False):
    """Probability and conditional mean of S_T on the cells [a, b] in k."""
    F = float(market.F(T))
    ca, ma = _laws(surface, market, T, a)
    cb, mb = _laws(surface, market, T, b)
    prob, mass = cb - ca, mb - ma
    if (prob < -1e-12).any():
        raise ValueError(f"the surface gives a decreasing CDF at T = {T:.3f}: it has butterfly arbitrage")
    if tails:  # the mass outside the grid goes to the end cells
        prob[0], mass[0] = prob[0] + ca[0], mass[0] + ma[0]
        prob[-1], mass[-1] = prob[-1] + 1 - cb[-1], mass[-1] + F - mb[-1]
    prob = np.maximum(prob, 0.0)
    sa, sb = F * np.exp(a), F * np.exp(b)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = mass / prob
    inside = np.isfinite(mean) & (mean >= sa) & (mean <= sb)
    if tails:
        inside[[0, -1]] = np.isfinite(mean[[0, -1]]) & (mean[[0, -1]] > 0)
    return prob, np.where(inside, mean, F * np.exp(0.5 * (a + b)))


def quadrature(surface, market: Market, T: float, n_cells: int = 4_000):
    """Spot nodes (conditional means) and probabilities of S_T on uniform cells in k, without refinement."""
    lo, hi = _bounds(surface, market, T)
    edges = np.linspace(lo, hi, n_cells + 1)
    prob, mean = _cells(surface, market, T, edges[:-1], edges[1:], tails=True)
    return mean, prob


def nodes(
    f, surface, market: Market, T: float, n_cells: int = 4_000, levels: int = 4, split: int = 16, tol: float = 1e-10
):
    """Nodes and weights for E[f(S_T)]: cells where f is not linear in S (checked at the edges and the node) are
    split into `split` sub-cells, up to `levels` times."""
    F = float(market.F(T))
    lo, hi = _bounds(surface, market, T)
    edges = np.linspace(lo, hi, n_cells + 1)
    a, b = edges[:-1], edges[1:]
    prob, mean = _cells(surface, market, T, a, b, tails=True)
    fixed = np.zeros(len(a), dtype=bool)
    fixed[[0, -1]] = True  # tail cells are never split
    out_s, out_w = [], []
    for level in range(levels + 1):
        if level == levels:
            out_s.append(mean)
            out_w.append(prob)
            break
        sa, sb = F * np.exp(a), F * np.exp(b)
        fa, fb, fm = f(sa), f(sb), f(mean)
        x = (mean - sa) / (sb - sa)
        bent = (np.abs(fm - (fa + x * (fb - fa))) > tol) & (prob > 0) & ~fixed
        out_s.append(mean[~bent])
        out_w.append(prob[~bent])
        if not bent.any():
            break
        grid = a[bent, None] + (b - a)[bent, None] * np.linspace(0.0, 1.0, split + 1)[None, :]
        a, b = grid[:, :-1].ravel(), grid[:, 1:].ravel()
        prob, mean = _cells(surface, market, T, a, b)
        fixed = np.zeros(len(a), dtype=bool)
    return np.concatenate(out_s), np.concatenate(out_w)


def replication_result(
    program,
    timeline: Timeline,
    surface,
    market: Market,
    T_obs: float,
    fmt=None,
    params=None,
    notional=None,
    n_cells: int = 4_000,
    levels: int = 6,
    initial_fixing: float | None = None,
    history: dict | None = None,
    ramps: dict | None = None,
):
    """Price on the surface. The numerical error is the change when the coarse grid is halved."""
    from optlab.structured.pricer import StructuredResult

    t0 = time.perf_counter()
    fmt = fmt or Note()
    ev = Evaluator(program, timeline, params, ramps=ramps)
    N = float(notional if notional is not None else ev.params.get("N", 1.0))
    col = int(np.searchsorted(timeline.sim_times, T_obs))
    name = program.underlying_names[0]

    def run(spots):
        cols = np.full((len(spots), len(timeline.sim_times)), np.nan)
        if col < len(timeline.sim_times) and timeline.sim_times[col] == T_obs:
            cols[:, col] = spots
        spot0 = market.spot if initial_fixing is None else initial_fixing
        return ev.run(Scenario(name, timeline.sim_times, cols, spot0, history))

    def pv(spots):
        return fmt.present_values(run(spots), market, N)

    def value(n):
        s, w = nodes(pv, surface, market, T_obs, n, levels, tol=1e-12 * N)
        return s, w, float(np.dot(w, pv(s)))

    s, w, v = value(n_cells)
    _, _, v_half = value(n_cells // 2)
    out = run(s)
    flows = {k: float(np.dot(w, np.broadcast_to(f, w.shape))) for k, f in out.flows.items()}
    red = {t: float(np.dot(w, np.broadcast_to(x, w.shape))) for t, x in out.redemption_weights.items()}
    seconds = time.perf_counter() - t0
    return StructuredResult(
        value=MCResult(v, 0.0, 0),
        price=100 * v / N,
        stderr=0.0,
        fmt=fmt.name,
        notional=N,
        n_paths=0,
        seconds=seconds,
        timings={"replication": seconds},
        event_times=timeline.event_times,
        termination=np.zeros(len(program.events)),
        expected_flows=flows,
        expected_life=float(timeline.event_times[-1]),
        redemption_probabilities=red,
        warnings=[],
        method="replication",
        numerical_error=100 * abs(v - v_half) / N,
    )

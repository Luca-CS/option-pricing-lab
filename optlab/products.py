"""Payoffs and Monte Carlo pricing: European, Asian (arithmetic and geometric), single barrier and
American (Longstaff-Schwartz). Every function takes a model and a market and returns
(price, standard error)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .blackscholes import geometric_asian_price
from .market import Market
from .models import BlackScholesModel, Model, simulate


@dataclass
class MCResult:
    price: float
    stderr: float
    n_paths: int

    def __iter__(self):
        return iter((self.price, self.stderr))


def _phi(cp) -> float:
    return 1.0 if str(cp).lower().startswith("c") else -1.0


def _result(discounted, n_paths) -> MCResult:
    """Antithetic pairs are averaged first so the standard error accounts for their correlation."""
    d = np.asarray(discounted)
    if len(d) % 2 == 0:
        d = 0.5 * (d[: len(d) // 2] + d[len(d) // 2:])
    return MCResult(float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))), n_paths)


def european_mc(model: Model, market: Market, K, T, cp="call", n_paths=100_000, rng=None, dt_max=1 / 252):
    S = simulate(model, market, [T], n_paths, rng, dt_max)[:, -1]
    return _result(market.D(T) * np.maximum(_phi(cp) * (S - K), 0.0), n_paths)


def fixing_dates(T, n_fixings):
    return np.linspace(T / n_fixings, T, n_fixings)


def asian_mc(model: Model, market: Market, K, T, n_fixings=12, cp="call", average="arithmetic",
             n_paths=100_000, rng=None, dt_max=1 / 252, control_variate=True):
    """Fixed-strike Asian on n equally spaced fixings, paid at T.

    Under Black-Scholes, the arithmetic price uses the geometric Asian (closed form) as a control
    variate, with the optimal coefficient estimated on the same paths.
    """
    times = fixing_dates(T, n_fixings)
    S = simulate(model, market, times, n_paths, rng, dt_max)
    phi, D = _phi(cp), market.D(T)
    geo = np.exp(np.log(S).mean(axis=1))
    geo_pay = D * np.maximum(phi * (geo - K), 0.0)
    if average == "geometric":
        return _result(geo_pay, n_paths)
    arith_pay = D * np.maximum(phi * (S.mean(axis=1) - K), 0.0)
    if not (control_variate and isinstance(model, BlackScholesModel)):
        return _result(arith_pay, n_paths)
    r, q = market.zero_rate(T), market.zero_rate(T) - market.carry_rate(T)
    exact = float(geometric_asian_price(market.spot, K, r, q, model.vol, times, cp))
    c = np.cov(arith_pay, geo_pay)
    beta = c[0, 1] / c[1, 1] if c[1, 1] > 0 else 0.0
    return _result(arith_pay - beta * (geo_pay - exact), n_paths)


def barrier_mc(model: Model, market: Market, K, H, T, kind="down-out", cp="call", n_monitor=252,
               n_paths=100_000, rng=None, dt_max=1 / 252):
    """Single barrier monitored on n_monitor equally spaced dates (daily by default), no rebate."""
    times = np.linspace(T / n_monitor, T, n_monitor)
    S = simulate(model, market, times, n_paths, rng, dt_max)
    direction, style = kind.split("-")
    hit = (S.min(axis=1) <= H) if direction == "down" else (S.max(axis=1) >= H)
    alive = ~hit if style == "out" else hit
    pay = market.D(T) * np.maximum(_phi(cp) * (S[:, -1] - K), 0.0) * alive
    return _result(pay, n_paths)


def _basis(s, v=None):
    cols = [np.ones_like(s), s, s * s, s**3]
    if v is not None:
        cols += [v, v * v, s * v]
    return np.column_stack(cols)


def american_lsm(model: Model, market: Market, K, T, cp="put", n_exercise=50, n_paths=100_000,
                 rng=None, dt_max=1 / 252):
    """Longstaff-Schwartz with a cubic polynomial basis in S/K (plus v, v^2 and S v when the model
    has a variance factor), regressing on in-the-money paths only.

    The exercise rule is estimated on one set of paths and applied to an independent set, so the
    estimate is a true lower bound up to Monte Carlo error (no foresight bias).
    """
    rng = np.random.default_rng(rng)
    times = np.linspace(T / n_exercise, T, n_exercise)
    phi = _phi(cp)

    def paths():
        S, V = simulate(model, market, times, n_paths, rng, dt_max, return_variance=True)
        return S / K, (V if model.has_variance else None)

    def stopped_value(s, v, coefs):
        cash = np.maximum(phi * (s[:, -1] - 1.0), 0.0)
        tau = np.full(len(s), n_exercise - 1)
        for j in range(n_exercise - 2, -1, -1):
            ex = np.maximum(phi * (s[:, j] - 1.0), 0.0)
            itm = ex > 0
            if coefs[j] is None or not itm.any():
                continue
            X = _basis(s[itm, j], None if v is None else v[itm, j])
            cont = X @ coefs[j]
            exercise = np.zeros(len(s), dtype=bool)
            exercise[np.flatnonzero(itm)[ex[itm] > cont]] = True
            cash = np.where(exercise, ex, cash)
            tau = np.where(exercise, j, tau)
        return K * cash * market.D(times[tau])

    # pass 1: backward regression of discounted continuation values
    s, v = paths()
    coefs = [None] * n_exercise
    disc = market.D(times)
    value = np.maximum(phi * (s[:, -1] - 1.0), 0.0) * disc[-1]  # discounted to 0
    for j in range(n_exercise - 2, -1, -1):
        ex = np.maximum(phi * (s[:, j] - 1.0), 0.0)
        itm = ex > 0
        if itm.sum() > 50:
            X = _basis(s[itm, j], None if v is None else v[itm, j])
            beta, *_ = np.linalg.lstsq(X, value[itm] / disc[j], rcond=None)
            coefs[j] = beta
            cont = X @ beta
            idx = np.flatnonzero(itm)[ex[itm] > cont]
            value[idx] = ex[idx] * disc[j]

    # pass 2: apply the rule to fresh paths
    s, v = paths()
    pay = stopped_value(s, v, coefs)
    # exercising at t = 0 is also allowed
    res = _result(pay, n_paths)
    immediate = K * max(phi * (market.spot / K - 1.0), 0.0)
    if immediate > res.price:
        return MCResult(immediate, 0.0, n_paths)
    return res

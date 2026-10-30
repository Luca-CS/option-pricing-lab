"""Greeks by bump and reprice on common random numbers, with smoothed digitals for Monte Carlo.

The product keeps its initial fixing (it is struck); the market spot is bumped with sticky moneyness (forwards
scale with the spot, the surface and the SLV leverage are read in ln(S/F)). Vega is a parallel shift of the
implied volatilities: the local vol is rebuilt from the shifted surface; under SLV the leverage would have to be
recalibrated, which is not done here, so SLV and Heston report no vega.
Units: delta is the change of the price (in % of notional) for a +1 % spot move, vega for +1 vol point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import BlackScholesModel, LocalVolModel
from ..svi import g_function
from .cashflows import Note
from .pricer import price_many
from .replication import replication_result
from .script.timeline import Clock, build_timeline


class ShiftedSurface:
    """Implied volatilities shifted by a constant: sigma'(k, T) = sigma(k, T) + shift."""

    def __init__(self, surface, shift: float):
        self.base, self.shift = surface, shift
        self.maturities = surface.maturities

    def derivatives(self, k, T):
        w, wk, wkk, wT = self.base.derivatives(k, T)
        T = np.broadcast_to(np.asarray(T, dtype=float), w.shape)
        s = np.sqrt(np.maximum(w, 1e-300) / T)
        sk = wk / (2 * T * s)
        skk = (wkk - 2 * T * sk**2) / (2 * T * s)
        sT = (wT - s**2) / (2 * T * s)
        s2 = s + self.shift
        return T * s2**2, 2 * T * s2 * sk, 2 * T * (sk**2 + s2 * skk), s2**2 + 2 * T * s2 * sT

    def w(self, k, T):
        return self.derivatives(k, T)[0]

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = g_function(w, wk, wkk, np.broadcast_to(k, w.shape))
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)


@dataclass
class Greeks:
    delta: float  # % of notional per +1 % spot
    delta_se: float
    vega: float | None  # % of notional per +1 vol point
    vega_se: float | None
    smoothing: float
    smoothing_bias: float  # smoothed - exact price, in % of notional
    smoothing_bias_se: float
    method: str
    model: str
    seconds: dict
    note: str = ""


def _diff(up, down, scale):
    if up.pairs is None:
        return scale * (up.price - down.price), 0.0
    d = (up.pairs - down.pairs) * 100 / up.notional * scale
    return float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d)))


def greeks(program, ctx, model: str, method: str = "monte_carlo", params=None, fmt=None, valuation=None,
           n_paths: int = 200_000, seed: int = 2026, batch: int | None = 25_000, smoothing: float = 0.005,
           h_spot: float = 0.01, h_vol: float = 0.01, T_obs: float | None = None) -> Greeks:
    """Delta and vega of a program under `model` ('bs', 'lv', 'slv', 'heston') or by replication on the surface
    (method 'replication'), with central differences. Monte Carlo digitals are smoothed (ramp of relative width
    `smoothing`); the bias this adds to the price is measured on the same paths."""
    import time
    fmt = fmt or Note()
    valuation = valuation or ctx.valuation
    S0 = ctx.market.spot
    timeline = build_timeline(program, Clock(valuation))
    horizon = timeline.horizon
    seconds = {}

    def run(spot_factor=1.0, vol_shift=0.0, eps=smoothing):
        market = ctx.market.scaled(S0 * spot_factor) if spot_factor != 1.0 else ctx.market
        surface = ShiftedSurface(ctx.surface, vol_shift) if vol_shift else ctx.surface
        if method == "replication":
            return replication_result(program, timeline, surface, market, T_obs, fmt, params, initial_fixing=S0)
        if model == "bs":
            m = BlackScholesModel(ctx.atm_vol(horizon) + vol_shift)
        elif model == "lv":
            m = LocalVolModel(surface)
        else:
            if vol_shift:
                return None
            m = ctx.model(model, horizon)
        return price_many(program, m, market, [params or {}], fmt, n_paths, seed, batch, smoothing=eps,
                          valuation=valuation, initial_fixing=S0)[0]

    t0 = time.perf_counter()
    eps = 0.0 if method == "replication" else smoothing
    base_exact = run(eps=0.0)
    base = run() if eps else base_exact
    bias, bias_se = _diff(base, base_exact, 1.0)
    seconds["base"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    up, down = run(1 + h_spot, eps=eps), run(1 - h_spot, eps=eps)
    delta, delta_se = _diff(up, down, 0.01 / (2 * h_spot))
    seconds["delta"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    vu, vd = run(vol_shift=h_vol, eps=eps), run(vol_shift=-h_vol, eps=eps)
    vega = vega_se = None
    note = ""
    if vu is not None:
        vega, vega_se = _diff(vu, vd, 0.01 / (2 * h_vol))
    else:
        note = f"no vega under {model.upper()}: a vol bump needs a recalibration of the model"
    seconds["vega"] = time.perf_counter() - t0
    return Greeks(delta, delta_se, vega, vega_se, eps, bias, bias_se, method, model, seconds, note)

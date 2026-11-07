"""Greeks by bump and reprice on common random numbers, with smoothed digitals for Monte Carlo.

The product keeps its initial fixing (it is struck); the market spot is bumped with sticky moneyness (forwards
scale with the spot, the surface and the SLV leverage are read in ln(S/F)). Vega is a parallel shift of the
implied volatilities: the local vol is rebuilt from the shifted surface; under SLV the leverage is recalibrated on
each shifted surface when `slv_vega=True` (otherwise no vega), and Heston reports none.
Units (`units.GREEK_UNITS`): delta is the change of the price (in % of notional) for a +1 % spot move, vega for +1
vol point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from optlab.config import DEFAULT_SEED
from optlab.models import BlackScholesModel, LocalVolModel, SLVModel
from optlab.structured.cashflows import Note
from optlab.structured.pricer import price_many
from optlab.structured.replication import replication_result
from optlab.structured.script.timeline import Clock, build_timeline
from optlab.structured.units import GREEK_UNITS
from optlab.svi import g_function


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

    def theta(self, T):
        """ATM total implied variance (used by the SLV leverage calibration)."""
        T = np.asarray(T, dtype=float)
        return T * (np.sqrt(self.base.theta(T) / T) + self.shift) ** 2

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = g_function(w, wk, wkk, np.broadcast_to(k, w.shape))
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)


def _recalibrated_slv(ctx, surface, horizon: float, n_particles: int, seed: int):
    from optlab.slv import calibrate_leverage

    T_max = min(max(horizon, 1 / 12), ctx.t_max)
    return calibrate_leverage(
        ctx.heston, surface, ctx.market, T_max, mix=ctx.slv.mix, n_particles=n_particles, rng=seed
    )


class StickyStrikeSurface:
    """The surface seen after a spot move by a factor (1 + h) when implied vols stay fixed at absolute strikes:
    log-moneyness k against the new forward is k + ln(1 + h) against the old one."""

    def __init__(self, base, h: float):
        self.base, self.c = base, float(np.log1p(h))
        self.maturities = base.maturities

    def derivatives(self, k, T):
        return self.base.derivatives(np.asarray(k, dtype=float) + self.c, T)

    def w(self, k, T):
        return self.derivatives(k, T)[0]

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def theta(self, T):
        return self.base.w(self.c, T)

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        return self.base.local_var(np.asarray(k, dtype=float) + self.c, T, floor, cap)


class ShiftedLeverage:
    """SLV leverage fixed in absolute spot after a spot move by (1 + h): L_new(t, x) = L(t, x + ln(1 + h))."""

    def __init__(self, leverage, h: float):
        self.base, self.c = leverage, float(np.log1p(h))
        self.times = leverage.times

    def __call__(self, t, x):
        return self.base(t, np.asarray(x, dtype=float) + self.c)


STICKY = ("moneyness", "strike", "model")


def sticky_convention(sticky: str, model: str, method: str) -> str:
    """'model' means the model's own dynamics: sticky strike for local vol and SLV (their local vol or leverage is a
    function of the absolute spot) and for the surface replication, sticky moneyness for the homogeneous BS and
    Heston."""
    if sticky not in STICKY:
        raise ValueError(f"unknown delta convention {sticky!r}, expected one of {', '.join(STICKY)}")
    if sticky != "model":
        return sticky
    return "strike" if method == "replication" or model in ("lv", "slv") else "moneyness"


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
    units: dict = field(default_factory=lambda: dict(GREEK_UNITS))
    per_name: dict | None = None  # baskets: {name: {"delta", "delta_se", "vega", "vega_se"}}
    sticky: str = "moneyness"  # smile dynamics of the delta: moneyness or strike


def _diff(up, down, scale):
    if up.pairs is None:
        return scale * (up.price - down.price), 0.0
    d = (up.pairs - down.pairs) * 100 / up.notional * scale
    return float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d)))


def greeks(
    program,
    ctx,
    model: str,
    method: str = "monte_carlo",
    params=None,
    fmt=None,
    valuation=None,
    n_paths: int = 200_000,
    seed: int = DEFAULT_SEED,
    batch: int | None = 25_000,
    smoothing: float = 0.005,
    h_spot: float = 0.01,
    h_vol: float = 0.01,
    T_obs: float | None = None,
    slv_vega: bool = False,
    n_particles: int = 200_000,
    calendar=None,
    workers: int | None = None,
    sticky: str = "moneyness",
) -> Greeks:
    """Delta and vega of a program under `model` ('bs', 'lv', 'slv', 'heston') or by replication on the surface
    (method 'replication'), with central differences. Monte Carlo digitals are smoothed (ramp of relative width
    `smoothing`); the bias this adds to the price is measured on the same paths. With `slv_vega`, the SLV
    leverage is recalibrated (particle method, `n_particles`, same seed up and down) on each shifted surface:
    about half a minute per calibration at 200,000 particles. Under Monte Carlo, the exact and smoothed base and
    the two spot bumps are evaluated on one simulation: at sticky moneyness a spot bump multiplies the paths
    exactly, so only the vega needs new paths. `sticky` sets the smile dynamics of the delta: "moneyness" (the
    default: the surface and the leverage move with the forward), "strike" (implied vols, local vol and leverage
    stay at absolute strikes) or "model" (strike for local vol, SLV and replication, moneyness for BS and Heston)."""
    import time

    fmt = fmt or Note()
    valuation = valuation or ctx.valuation
    S0 = ctx.market.spot
    timeline = build_timeline(program, Clock(valuation, calendar))
    horizon = timeline.horizon
    if method == "replication" and T_obs is None:
        from optlab.structured.profile import profile

        times = profile(program, timeline).observation_times
        T_obs = times[-1] if times else horizon
    seconds = {}

    def mc_model(vol_shift=0.0):
        surface = ShiftedSurface(ctx.surface, vol_shift) if vol_shift else ctx.surface
        if model == "bs":
            return BlackScholesModel(ctx.atm_vol(horizon) + vol_shift)
        if model == "lv":
            return LocalVolModel(surface)
        if vol_shift and not (model == "slv" and slv_vega):
            return None
        return _recalibrated_slv(ctx, surface, horizon, n_particles, seed) if vol_shift else ctx.model(model, horizon)

    convention = sticky_convention(sticky, model, method)
    homogeneous = method != "replication" and model in ("bs", "heston")  # no smile to hold: both conventions agree

    def run(spot_factor=1.0, vol_shift=0.0, eps=smoothing):
        market = ctx.market.scaled(S0 * spot_factor) if spot_factor != 1.0 else ctx.market
        surface = ShiftedSurface(ctx.surface, vol_shift) if vol_shift else ctx.surface
        strike_bump = convention == "strike" and spot_factor != 1.0 and not homogeneous
        if strike_bump:
            surface = StickyStrikeSurface(surface, spot_factor - 1.0)
        if method == "replication":
            return replication_result(program, timeline, surface, market, T_obs, fmt, params, initial_fixing=S0)
        if model == "bs":
            m = BlackScholesModel(ctx.atm_vol(horizon) + vol_shift)
        elif model == "lv":
            m = LocalVolModel(surface)
        else:
            if vol_shift and not (model == "slv" and slv_vega):
                return None
            m = _recalibrated_slv(ctx, surface, horizon, n_particles, seed) if vol_shift else ctx.model(model, horizon)
            if strike_bump and model == "slv":
                m = SLVModel(m.params, ShiftedLeverage(m.leverage, spot_factor - 1.0), m.mix)
        return price_many(
            program,
            m,
            market,
            [params or {}],
            fmt,
            n_paths,
            seed,
            batch,
            smoothing=eps,
            valuation=valuation,
            initial_fixing=S0,
            calendar=calendar,
            workers=workers,
        )[0]

    t0 = time.perf_counter()
    eps = 0.0 if method == "replication" else smoothing
    if method == "replication":
        base_exact = base = run(eps=0.0)
        bias, bias_se = 0.0, 0.0
        seconds["base"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        up, down = run(1 + h_spot, eps=0.0), run(1 - h_spot, eps=0.0)
    elif convention == "strike" and not homogeneous:  # the bumped models differ: two more simulations
        p = params or {}
        base_exact, base = price_many(
            program,
            mc_model(),
            ctx.market,
            [p, p],
            fmt,
            n_paths,
            seed,
            batch,
            smoothings=[0.0, eps],
            valuation=valuation,
            initial_fixing=S0,
            calendar=calendar,
            workers=workers,
        )
        bias, bias_se = _diff(base, base_exact, 1.0)
        seconds["base"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        up, down = run(1 + h_spot, eps=eps), run(1 - h_spot, eps=eps)
    else:
        p = params or {}
        base_exact, base, up, down = price_many(
            program,
            mc_model(),
            ctx.market,
            [p, p, p, p],
            fmt,
            n_paths,
            seed,
            batch,
            smoothings=[0.0, eps, eps, eps],
            spot_factors=[1.0, 1.0, 1 + h_spot, 1 - h_spot],
            valuation=valuation,
            initial_fixing=S0,
            calendar=calendar,
            workers=workers,
        )
        bias, bias_se = _diff(base, base_exact, 1.0)
        seconds["base"] = time.perf_counter() - t0
        t0 = time.perf_counter()
    delta, delta_se = _diff(up, down, 0.01 / (2 * h_spot))
    seconds["delta"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    vu, vd = run(vol_shift=h_vol, eps=eps), run(vol_shift=-h_vol, eps=eps)
    vega = vega_se = None
    note = ""
    if vu is not None:
        vega, vega_se = _diff(vu, vd, 0.01 / (2 * h_vol))
    else:
        note = f"no vega under {model.upper()}: a vol bump needs a recalibration of the model" + (
            " (pass slv_vega=True to recalibrate the leverage)" if model == "slv" else ""
        )
    seconds["vega"] = time.perf_counter() - t0
    return Greeks(delta, delta_se, vega, vega_se, eps, bias, bias_se, method, model, seconds, note, sticky=convention)

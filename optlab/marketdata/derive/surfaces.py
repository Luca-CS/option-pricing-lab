"""Historical implied surfaces, in three layers that are never mixed.

- observed: SVI fitted on a real chain of that day (a fit, with its error on the quotes).
- reconstructed: no chain is read. The level and the term structure up to one year come from volatility indices of
  that day, the smile's shape from a prior, the slope optionally from the SKEW index. Beyond one year (the end of VIX1Y)
  the term structure is an extrapolation, and the products of 3 to 5 years rest on it: stored and shown as such.
- synthetic: no implied volatility at all (realised volatility with an assumed premium).

The volatility indices are model-free variance levels, not at-the-money vols. A slice's at-the-money variance is solved
so that the slice's own model-free variance equals the index: the skew's contribution is not taken for level.

The shape prior used for the past comes from one calibration (27/10/2026), so it is an assumption on the shape, known
later than the dates it is applied to: no market level of those dates depends on it, but "reconstructed" does not mean
"what was known then" for the shape. The test of section 4 of the plan measures what it costs.

That shape comes from a calm day, and its slope grows too fast with the level: at a VIX above 60 it overstated the put skew by
6 points of volatility, and since the variance of the index is fixed, the at-the-money level came out 7 points too low. So the
slope falls slowly with the day's own 30-day level (`LEVEL_POWER`), a number known at that date. Fitted on real chains
away from the 2020 crash and tested on it (reports/surface_reconstruction.md).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares
from scipy.special import ndtr

from ...log import get_logger
from ...pipeline import _weights
from ...svi import SVISurface, fit_svi_surface, ssvi_slice_to_svi, svi_arbitrage_report
from ..lake import Lake, new_run_id, sql_str
from ..quotes import clean_day
from ..universe import instrument

LOG = get_logger(__name__)
TERM_INDICES = {"VIX9D": 9 / 365, "VIX": 30 / 365, "VIX3M": 93 / 365, "VIX6M": 183 / 365, "VIX1Y": 365 / 365}
EXTRAPOLATED = (1.5, 2.0, 3.0, 4.0, 5.0)
LEVEL_POWER, LEVEL_REF = 0.12, 0.12  # 0.118 fitted without the crash, 0.121 with it; the reference sits at the bound of the fit
LAST_OBSERVABLE = 1.0  # VIX1Y: beyond it the term structure is an assumption
OBSERVED_TARGETS = (1 / 24, 1 / 12, 1 / 6, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
Z = np.linspace(-8.0, 8.0, 321)  # integration grid, in at-the-money standard deviations


@dataclass(frozen=True)
class ShapePrior:
    rho: float
    eta: float
    gamma: float
    v_inf: float  # long-run forward variance beyond one year
    kappa: float = 0.7  # speed at which the forward variance goes there
    source: str = ""
    level_power: float = 0.0  # the slope falls as the 30-day level rises above level_ref, by (level / level_ref) ^ -level_power
    level_ref: float = 0.2

    def scaled_rho(self, factor: float) -> "ShapePrior":
        return replace(self, rho=self.rho * factor)

    def eta_at(self, level: float, rho: float | None = None) -> float:
        """The curvature parameter at a 30-day level (fraction), kept free of butterfly arbitrage for every theta."""
        rho = self.rho if rho is None else rho
        return min(self.eta * max(level / self.level_ref, 1.0) ** -self.level_power, 2.0 / (1 + abs(rho)))


def _phi(theta, eta, gamma):
    return eta / (theta**gamma * (1 + theta) ** (1 - gamma))


def ssvi_w(k, theta, rho, eta, gamma):
    """Total variance of a power-law SSVI slice."""
    psi = theta * _phi(theta, eta, gamma)
    return 0.5 * (theta + rho * psi * k + np.sqrt((psi * k + rho * theta) ** 2 + theta**2 * (1 - rho**2)))


def fit_shape(surface: SVISurface, kappa: float = 0.7, source: str = "") -> ShapePrior:
    """Power-law shape of a calibrated surface, with its at-the-money variances kept, and the long-run forward variance
    read on its last maturities."""
    Ts = surface.maturities
    thetas = np.array([float(surface.theta(T)) for T in Ts])
    grids = [np.linspace(-2.0, 1.5, 15) * np.sqrt(th) for th in thetas]
    targets = [np.asarray(surface.iv(k, T)) for k, T in zip(grids, Ts)]

    def residuals(x):
        rho, eta, gamma = x
        return np.concatenate([np.sqrt(np.maximum(ssvi_w(k, th, rho, eta, gamma), 1e-10) / T) - iv
                               for k, th, T, iv in zip(grids, thetas, Ts, targets)])

    sol = least_squares(residuals, [-0.7, 1.0, 0.3], bounds=([-0.99, 1e-3, 0.01], [-0.01, 3.0, 0.5]))
    rho, eta, gamma = sol.x
    eta = min(eta, 2.0 / (1 + abs(rho)))  # butterfly-free for every theta
    long_end = Ts >= 0.9
    v_inf = float((thetas[-1] - thetas[long_end][0]) / (Ts[-1] - Ts[long_end][0])) if long_end.sum() >= 2 else float(
        thetas[-1] / Ts[-1])
    return ShapePrior(float(rho), float(eta), float(gamma), v_inf, kappa, source)


def model_free_variance(thetas, rho, eta, gamma) -> np.ndarray:
    """-2 E[ln(S/F)] of slices: twice the integral of out-of-the-money prices over K^2, for F = 1. Vectorised on theta."""
    thetas = np.atleast_1d(thetas)
    sd = np.sqrt(thetas)[:, None]
    k = Z[None, :] * sd
    w = np.maximum(ssvi_w(k, thetas[:, None], rho, eta, gamma), 1e-12)
    s = np.sqrt(w)
    d1 = -k / s + 0.5 * s
    d2 = d1 - s
    call = ndtr(d1) - np.exp(k) * ndtr(d2)
    put = np.exp(k) * ndtr(-d2) - ndtr(-d1)
    otm = np.where(k >= 0, call, put)
    return 2.0 * np.trapezoid(otm * np.exp(-k), k, axis=1)


def model_free_skewness(theta, rho, eta, gamma) -> float:
    """Risk-neutral skewness of the log-return of a slice (Bakshi, Kapadia and Madan, with F = 1), as in the SKEW index."""
    sd = np.sqrt(theta)
    k = Z * sd
    w = np.maximum(ssvi_w(k, theta, rho, eta, gamma), 1e-12)
    s = np.sqrt(w)
    d1 = -k / s + 0.5 * s
    d2 = d1 - s
    call = ndtr(d1) - np.exp(k) * ndtr(d2)
    put = np.exp(k) * ndtr(-d2) - ndtr(-d1)
    otm = np.where(k >= 0, call, put) * np.exp(-k)  # price / K^2 dK = price e^-k dk
    V = np.trapezoid(2 * (1 - k) * otm, k)
    W = np.trapezoid((6 * k - 3 * k**2) * otm, k)
    X = np.trapezoid((12 * k**2 - 4 * k**3) * otm, k)
    mu = -0.5 * V - W / 6 - X / 24  # the exp(rT) terms equal 1: prices are undiscounted here
    return float((W - 3 * mu * V + 2 * mu**3) / (V - mu**2) ** 1.5)


def rho_from_skew(skew_index: float, theta_30d: float, prior: ShapePrior, bounds=(-0.98, -0.05)) -> float:
    """The correlation whose 30-day slice has the skewness of the SKEW index: SKEW = 100 - 10 S."""
    target = (100.0 - skew_index) / 10.0

    def gap(rho):
        return model_free_skewness(theta_30d, rho, min(prior.eta, 2.0 / (1 + abs(rho))), prior.gamma) - target

    lo, hi = gap(bounds[0]), gap(bounds[1])
    if lo * hi > 0:  # out of the shape's reach: the closest end
        return bounds[0] if abs(lo) < abs(hi) else bounds[1]
    return float(brentq(gap, *bounds, xtol=1e-4))


def solve_thetas(levels, Ts, rho, eta, gamma, iterations: int = 8) -> np.ndarray:
    """At-the-money total variances whose model-free variance equals each index level (fractions), non-decreasing."""
    target = np.asarray(levels) ** 2 * np.asarray(Ts)
    theta = 0.85 * target
    for _ in range(iterations):
        theta = theta * target / model_free_variance(theta, rho, eta, gamma)
    return np.maximum.accumulate(theta)


def extrapolate_thetas(theta_last: float, fv_last: float, T_last: float, prior: ShapePrior, Ts) -> np.ndarray:
    """Beyond the last observable maturity, the forward variance goes to v_inf at speed kappa. An assumption."""
    Ts = np.asarray(Ts, dtype=float)
    d = Ts - T_last
    return theta_last + prior.v_inf * d + (fv_last - prior.v_inf) * (1 - np.exp(-prior.kappa * d)) / prior.kappa


@dataclass
class Reconstruction:
    surface: SVISurface
    rho: float
    extrapolated_from: float
    inputs: dict


def reconstruct(levels: dict, prior: ShapePrior, skew: float | None = None, scale: float = 1.0) -> Reconstruction:
    """A surface from index levels of one day ({index: level in percent}, as in TERM_INDICES). `scale` multiplies them,
    for a name quoted only at 30 days; `skew` is the SKEW index, or None to keep the prior's slope."""
    names = [n for n in TERM_INDICES if levels.get(n) is not None and np.isfinite(levels[n])]
    if len(names) < 2:
        raise ValueError(f"not enough volatility indices: {levels}")
    Ts = np.array([TERM_INDICES[n] for n in names])
    vols = np.array([levels[n] for n in names]) * scale / 100.0
    rho = prior.rho
    th30 = float(np.interp(30 / 365, Ts, vols**2 * Ts))
    if skew is not None:
        rho = rho_from_skew(skew, th30, prior)
    eta = prior.eta_at(np.sqrt(th30 / (30 / 365)), rho)
    thetas = solve_thetas(vols, Ts, rho, eta, prior.gamma)
    fv_last = (thetas[-1] - thetas[-2]) / (Ts[-1] - Ts[-2])
    T_ext = np.array(EXTRAPOLATED)
    T_ext = T_ext[T_ext > Ts[-1]]
    th_ext = extrapolate_thetas(thetas[-1], fv_last, Ts[-1], prior, T_ext)
    all_T, all_th = np.concatenate([Ts, T_ext]), np.maximum.accumulate(np.concatenate([thetas, th_ext]))
    all_th = np.maximum(all_th, np.concatenate([[all_th[0]], all_th[:-1] + 1e-7]))
    params = np.array([ssvi_slice_to_svi(th, th * _phi(th, eta, prior.gamma), rho) for th in all_th])
    return Reconstruction(SVISurface(all_T, params), float(rho), float(Ts[-1]),
                          {"levels": {n: float(levels[n]) for n in names}, "scale": float(scale),
                           "skew": skew, "prior": asdict(prior)})


def static_prior(path=None) -> ShapePrior:
    """The shape of the versioned SPX calibration (27/10/2026)."""
    from ...config import paths
    cal = json.loads((path or paths().results / "spx_calibration.json").read_text(encoding="utf-8"))
    shape = fit_shape(SVISurface.from_dict(cal["surface"]), source=f"calibration of {cal['meta'].get('snapshot', '')[:10]}")
    return replace(shape, level_power=LEVEL_POWER, level_ref=LEVEL_REF)


EQUITY_RHO_FACTOR = 0.6  # single names have less skew than indices: an assumption of the reconstruction


def prior_for(name: str, prior: ShapePrior) -> ShapePrior:
    return prior.scaled_rho(EQUITY_RHO_FACTOR) if instrument(name).asset_class == "equity" else prior


def index_panel(lake: Lake, start=None, end=None, names=None) -> pd.DataFrame:
    """Levels per date and index, forward-filled for at most five days: only past levels can fill a gap. It stops at the last
    VIX: every other name is scaled by it, so a day when the other files are already out and the VIX is not would mix two days."""
    where = ["source <> 'dolthub'"]
    if names:
        where.append("index_name IN (" + ", ".join(f"'{n}'" for n in names) + ")")
    rows = lake.read("vol_indices", where=" AND ".join(where), columns="index_name, obs_date, close")
    panel = rows.pivot_table(index="obs_date", columns="index_name", values="close").sort_index()
    panel.index = pd.to_datetime(panel.index)
    if "VIX" in panel.columns and panel["VIX"].notna().any():
        panel = panel[panel.index <= panel["VIX"].last_valid_index()]
    panel = panel.ffill(limit=5)
    if start is not None:
        panel = panel[panel.index >= pd.Timestamp(start)]
    if end is not None:
        panel = panel[panel.index <= pd.Timestamp(end)]
    return panel


def reconstruct_on(panel_row: pd.Series, name: str, prior: ShapePrior, use_skew: bool = False) -> Reconstruction:
    """A name's reconstruction from one row of the panel; a name other than SPX scales the SPX term structure by the
    ratio of its 30-day index to the VIX."""
    inst = instrument(name)
    levels = {n: panel_row.get(n) for n in TERM_INDICES}
    scale = 1.0
    if name != "SPX":
        own, vix = panel_row.get(inst.vol_index), panel_row.get("VIX")
        if own is None or not np.isfinite(own) or vix is None or not np.isfinite(vix):
            raise ValueError(f"no {inst.vol_index} on this date")
        scale = float(own / vix)
    skew = panel_row.get("SKEW") if use_skew and name == "SPX" else None
    return reconstruct(levels, prior_for(name, prior), skew, scale)


def build_reconstructed(lake: Lake, names, start="2018-01-01", end=None, prior: ShapePrior | None = None, step: int = 1,
                        use_skew: bool = False) -> list[dict]:
    """Daily reconstructed surfaces, stored with their inputs."""
    prior = prior or static_prior()
    panel = index_panel(lake, start, end)
    out = []
    run = new_run_id("reconstruct")
    for name in names:
        rows = []
        for day, row in panel.iloc[::step].iterrows():
            try:
                rec = reconstruct_on(row, name, prior, use_skew)
            except ValueError as exc:
                LOG.debug("%s %s: %s", name, day.date(), exc)
                continue
            rows.append({"underlying": name, "layer": "reconstructed", "obs_date": day.date(),
                         "surface": json.dumps(rec.surface.to_dict()), "t_max_observed": rec.extrapolated_from,
                         "extrapolated_from": rec.extrapolated_from, "inputs": json.dumps(rec.inputs), "fit": None})
        if rows:
            out.append(lake.ingest("vol_surfaces", pd.DataFrame(rows), "vol_indices+prior", "reconstructed", run))
    return out


def fit_observed(lake: Lake, name: str, day, source: str | None = None, targets=OBSERVED_TARGETS) -> dict:
    """SVI on a day's chain: the stored row, with the fit's error against mid and bid-ask, and the arbitrage report."""
    from ...data import select_maturities
    cleaned = clean_day(lake, name, day, source)
    slices = select_maturities(cleaned.slices, targets)
    surface, _ = fit_svi_surface(slices, [_weights(s) for s in slices])
    errors = [surface.iv(s["k"], s["T"]) - s["iv"] for s in slices]
    within = [np.abs(e) <= 0.5 * np.nan_to_num(s["spread_iv"], nan=0.0) for e, s in zip(errors, slices)]
    fit = {"rmse_bp": float(np.sqrt(np.mean(np.concatenate(errors) ** 2)) * 1e4),
           "within_spread": float(np.mean(np.concatenate(within))), "n_quotes": int(sum(len(s["k"]) for s in slices)),
           "spot": cleaned.spot, "arbitrage": svi_arbitrage_report(surface)}
    t_max = float(surface.maturities[-1])
    return {"underlying": name, "layer": "observed", "obs_date": cleaned.day, "surface": json.dumps(surface.to_dict()),
            "t_max_observed": t_max, "extrapolated_from": t_max, "inputs": json.dumps({"source": cleaned.source}),
            "fit": json.dumps(fit)}


def build_observed(lake: Lake, name: str, days, source: str, log=LOG.info) -> dict:
    rows = []
    for day in days:
        try:
            rows.append(fit_observed(lake, name, day, source))
        except (KeyError, ValueError) as exc:
            log(f"{name} {day}: {exc}")
    return lake.ingest("vol_surfaces", pd.DataFrame(rows), f"svi_on_{source}", "observed", new_run_id("observed_fit")) if rows \
        else {"n_new": 0, "n_changed": 0, "n_input": 0}


def synthetic_surface(realised_vol: float, prior: ShapePrior, premium: float = 1.1) -> Reconstruction:
    """No implied volatility at all: a flat level from realised volatility with an assumed premium, on the prior's shape."""
    level = 100.0 * realised_vol * premium
    rec = reconstruct({n: level for n in TERM_INDICES}, prior)
    rec.inputs.update(realised_vol=float(realised_vol), premium=premium)
    return rec


@dataclass
class SurfaceAt:
    surface: SVISurface
    layer: str
    origin: str
    obs_date: dt.date
    extrapolated_from: float
    meta: dict


LAYER_ORDER = ("observed", "reconstructed", "synthetic")


def surface_at(lake: Lake, name: str, day, as_of=None, max_age_days: int = 5, min_t_max: float = 1.0,
               layer: str | None = None) -> SurfaceAt:
    """The best surface of a name at a date: an observed fit of that very day when it reaches `min_t_max`, else the
    latest reconstruction not older than `max_age_days`. Never a later date. `layer` asks for one layer only: a replay
    stays on one, since switching between layers would show up as P&L."""
    day = pd.Timestamp(day).date()
    rows = lake.read("vol_surfaces", where=f"underlying = {sql_str(name)} AND obs_date <= DATE '{day}' AND obs_date >= DATE '{day}'"
                     f" - INTERVAL {max_age_days} DAY", as_of=as_of, order="obs_date DESC")
    if layer:
        rows = rows[rows["layer"] == layer]
    if rows.empty:
        raise KeyError(f"no {layer or 'usable'} surface of {name} around {day}")
    observed = rows[(rows["layer"] == "observed") & (pd.to_datetime(rows["obs_date"]).dt.date == day)
                    & (rows["t_max_observed"] >= min_t_max)]
    r = observed.iloc[0] if len(observed) else None
    if r is None:
        fallback = rows[rows["layer"] == "reconstructed"]
        if fallback.empty:
            fallback = rows[rows["layer"] == "synthetic"]
        if fallback.empty:
            raise KeyError(f"no usable surface of {name} around {day}")
        r = fallback.iloc[0]
    return SurfaceAt(SVISurface.from_dict(json.loads(r["surface"])), r["layer"], r["origin"], pd.Timestamp(r["obs_date"]).date(),
                     float(r["extrapolated_from"]), {"inputs": r["inputs"], "fit": r["fit"], "source": r["source"]})

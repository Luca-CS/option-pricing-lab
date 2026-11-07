"""Point in time: the market as it stood at a date, built only from rows dated at or before it.

`market_at(lake, name, day, as_of)` returns a MarketSnapshot in the model's scale (spot 100) with the real level in
`spot_scale`, so that it plugs into `PricingContext.from_snapshot`. `day` is the market date; `as_of` is what the lake
knew (the ingestion date), by default everything loaded so far. Each part records its origin in the provenance.

The shape prior of a reconstructed surface is fixed configuration, and the Heston dynamics are too in the default "prior"
mode: they do not move when later data arrives, which the anti-look-ahead test checks, but they were estimated in 2026.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from optlab.market import Market
from optlab.marketdata.derive.carry import DividendStream, dividend_stream
from optlab.marketdata.derive.correl import correlation_at
from optlab.marketdata.derive.fx import quanto_link_at
from optlab.marketdata.derive.surfaces import surface_at
from optlab.marketdata.lake import TABLES, Lake, sql_str
from optlab.marketdata.quotes import discount_function
from optlab.marketdata.snapshot import MarketSnapshot
from optlab.marketdata.universe import instrument

PILLARS = np.array([1 / 52, 1 / 12, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0])
STEPS_UP_TO = 3.0  # years over which a forward keeps its dividend steps; beyond it, the pillars smooth them
ONE_DAY = 1 / 365
MODEL_SPOT = 100.0
MIX = 0.5
PRIOR_DYNAMICS = {"kappa": 3.09, "xi": 1.31, "rho": -0.69}  # of the versioned SPX calibration (27/10/2026)


def real_close(lake: Lake, name: str, day, as_of=None, max_age_days: int = 5) -> tuple[float, dt.date]:
    """The last close at or before `day`, not older than `max_age_days`: (level, its date)."""
    day = pd.Timestamp(day).date()
    rows = lake.read(
        "prices_eod",
        where=f"instrument = {sql_str(name)} AND obs_date <= DATE '{day}' AND obs_date >= DATE '{day}'"
        f" - INTERVAL {max_age_days} DAY",
        columns="obs_date, close",
        order="obs_date DESC LIMIT 1",
        as_of=as_of,
    )
    if rows.empty:
        raise KeyError(f"no close of {name} in the {max_age_days} days up to {day}")
    return float(rows["close"].iloc[0]), pd.Timestamp(rows["obs_date"].iloc[0]).date()


def real_fixings(lake: Lake, name: str, start, end, as_of=None) -> dict:
    """{iso date: real close} of a name between two dates."""
    s, e = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    rows = lake.read(
        "prices_eod",
        where=f"instrument = {sql_str(name)} AND obs_date >= DATE '{s}' AND obs_date <= DATE '{e}'",
        columns="obs_date, close",
        order="obs_date",
        as_of=as_of,
    )
    return {pd.Timestamp(d).date().isoformat(): float(v) for d, v in zip(rows["obs_date"], rows["close"])}


def curve_pillars(stream: DividendStream) -> np.ndarray:
    """The usual pillars, and one on each side of a dividend while it can still be told apart."""
    near = stream.times[stream.times <= STEPS_UP_TO]
    near = near[near > ONE_DAY]
    return np.unique(
        np.concatenate([PILLARS, near - ONE_DAY, near])
    )  # exact times: a rounded one could fall before its dividend


def market_curves(lake: Lake, name: str, day, as_of=None) -> tuple[Market, float, dict]:
    """(market at spot 100, real spot, provenance): discount curve and carry known at `day`."""
    inst = instrument(name)
    day = pd.Timestamp(day).date()
    spot, spot_day = real_close(lake, name, day, as_of)
    disc = discount_function(lake, inst.currency, day, as_of)
    stream = dividend_stream(lake, name, day, spot, as_of)
    pillars = curve_pillars(stream)
    discounts = np.asarray(disc(pillars), dtype=float)
    forwards = MODEL_SPOT * stream.factor(pillars) / discounts
    return (
        Market(MODEL_SPOT, pillars, discounts, forwards),
        spot,
        {
            "spot": {"origin": "observed", "date": spot_day.isoformat(), "source": "prices_eod"},
            "discount": {"origin": "derived", "curve": inst.currency},
            "carry": {"origin": stream.origin, "method": stream.method, "dividend_yield": stream.annual_yield},
        },
    )


def flat_leverage(T_max: float) -> dict:
    """A leverage of one everywhere: the SLV model is then a plain Heston. Only for contexts priced under local vol."""
    return {"times": [0.0], "x_grids": [[-1.0, 1.0]], "values": [[1.0, 1.0]]}


def quick_heston(surface) -> dict:
    """Heston with the prior's dynamics, its variance levels set by the day's surface: fast and stable for a daily loop.
    The SLV leverage is what brings the vanillas back on the surface."""
    v0 = float(surface.theta(1 / 12)) * 12
    theta_ = max(float(surface.theta(2.0)) / 2.0, 1e-4)
    return {
        "v0": max(v0, 1e-4),
        "kappa": PRIOR_DYNAMICS["kappa"],
        "theta": theta_,
        "xi": PRIOR_DYNAMICS["xi"],
        "rho": PRIOR_DYNAMICS["rho"],
    }


def fitted_heston(surface, market: Market, x0: dict | None = None) -> dict:
    """Heston fitted to the surface on five maturities and nine strikes."""
    from optlab.heston import HestonParams, calibrate_heston

    slices = []
    for T in (1 / 12, 0.25, 0.5, 1.0, 2.0):
        sd = float(np.sqrt(surface.theta(T)))
        k = np.linspace(-1.5, 1.0, 9) * sd
        F = float(market.F(T))
        slices.append({"T": T, "F": F, "D": float(market.D(T)), "K": F * np.exp(k), "iv": np.asarray(surface.iv(k, T))})
    params, _ = calibrate_heston(slices, HestonParams(**x0) if x0 else None)
    return params.to_dict()


def market_at(
    lake: Lake,
    name: str,
    day,
    as_of=None,
    heston: str = "prior",
    leverage: str = "flat",
    T_max: float = 2.0,
    n_particles: int = 100_000,
    seed: int = 2026,
    mix: float = MIX,
    layer: str | None = None,
    payment: str | None = None,
    fx_window: int = 120,
) -> MarketSnapshot:
    """The market of a name at a date. `heston`: prior (dynamics fixed, levels from the surface) or fit. `leverage`: flat
    (for local vol and replication), or calibrate (particles, up to T_max, several seconds). `layer` forces a surface layer
    instead of the best one of the day. `payment` other than the name's currency gives the quanto market: forward adjusted, discount
    curve of that currency, correlation and FX volatility known at `day` with their origins in the provenance."""
    day = pd.Timestamp(day).date()
    market, spot, prov = market_curves(lake, name, day, as_of)
    got = surface_at(lake, name, day, as_of, layer=layer)
    pay = payment or instrument(name).currency
    if pay != instrument(name).currency:
        from optlab.structured.quanto import quanto_market

        link = quanto_link_at(lake, name, pay, day, fx_window, as_of)
        market = quanto_market(market, discount_function(lake, pay, day, as_of), link.rho, link.vol_fx, got.surface)
        prov.update(discount={"origin": "derived", "curve": pay}, quanto=link.meta)
    prov["payment"] = pay
    h = quick_heston(got.surface) if heston == "prior" else fitted_heston(got.surface, market)
    if leverage == "calibrate":
        from optlab.heston import HestonParams
        from optlab.slv import calibrate_leverage

        fit = calibrate_leverage(
            HestonParams(**h), got.surface, market, T_max, mix=mix, n_particles=n_particles, rng=seed
        )
        lev = fit.leverage.to_dict()
    else:
        lev = flat_leverage(T_max)
    prov.update(
        surface={
            "origin": got.origin,
            "layer": got.layer,
            "date": got.obs_date.isoformat(),
            "extrapolated_from": got.extrapolated_from,
        },
        heston={"origin": "derived" if heston == "fit" else "synthetic", "mode": heston},
        leverage={"origin": "derived" if leverage == "calibrate" else "synthetic", "mode": leverage},
    )
    snap = MarketSnapshot(
        name,
        f"{day.isoformat()}T16:00:00",
        day,
        f"lake:{got.layer}",
        market.to_dict(),
        got.surface.to_dict(),
        h,
        {"mix": mix, "leverage": lev},
        prov,
    )
    return snap.with_spot_scale(spot)


def context_at(lake: Lake, name: str, day, as_of=None, **kw):
    """(PricingContext, MarketSnapshot) at a date."""
    from optlab.structured.context import PricingContext

    snap = market_at(lake, name, day, as_of, **kw)
    return PricingContext.from_snapshot(snap), snap


def basket_at(
    lake: Lake,
    names,
    day,
    as_of=None,
    window: int = 120,
    implied: bool = True,
    layer: str | None = None,
    payment: str | None = None,
):
    """(BasketContext, {name: snapshot}, correlation meta): each name on its own surface, correlation known at `day`. Everything is
    paid in `payment`, by default the first name's currency, and a name in another currency is on its quanto market."""
    from optlab.structured.context import PricingContext
    from optlab.structured.multiasset import BasketContext

    pay = payment or instrument(names[0]).currency
    snaps = {n: market_at(lake, n, day, as_of, layer=layer, payment=pay, fx_window=window) for n in names}
    corr, meta = correlation_at(lake, names, day, window, implied, as_of)
    ctxs = {n: PricingContext.from_snapshot(s) for n, s in snaps.items()}
    ctx = BasketContext(
        tuple(names),
        {n: c.market for n, c in ctxs.items()},
        {n: c.surface for n, c in ctxs.items()},
        corr,
        pd.Timestamp(day).date(),
        ctxs,
        payment_currency=pay,
    )
    return (
        ctx,
        snaps,
        {
            **meta,
            "payment": pay,
            "quanto": {n: s.provenance["quanto"] for n, s in snaps.items() if "quanto" in s.provenance},
        },
    )


def truncated_copy(lake: Lake, target, day, tables=None, where_extra: dict | None = None) -> Lake:
    """A new lake holding only rows dated at or before `day`: what the anti-look-ahead test compares with."""
    target = Lake(target)
    day = pd.Timestamp(day).date()
    for table in tables or TABLES:
        if table in ("ingestion_runs", "quality_issues") or not lake.has(table):
            continue
        clause = f"obs_date <= DATE '{day}'" + (
            f" AND {where_extra[table]}" if where_extra and table in where_extra else ""
        )
        rows = lake.read(table, where=clause)
        for (source, origin), group in rows.groupby(["source", "origin"]):
            target.ingest(table, group, source, origin)
    return target

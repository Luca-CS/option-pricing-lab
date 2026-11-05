"""FX: forwards by covered parity, realised volatility, correlation of a name with a pair, and what a quanto reads at a date.

Forwards use the desk's two curves, Treasury and AAA, not OIS or cross-currency curves, so they carry no basis. A pair without a curve
on both sides has spots, volatility and correlation but no forward: it is reported as a quality issue, not filled.

No free source quotes FX implied volatility: Cboe refuses EVZ and Yahoo no longer serves it. The volatility is therefore realised, with
origin = derived and method = realized, and a quanto carries both labels up to its provenance. Nothing here is an implied level.

An FX fix is hours away from any index close, so a name against a pair uses the overlapping multi-day returns of pairs across zones
(`rolling_pair`). With the rate quote per base and a name in currency f paid in d, X = d per f is the pair itself when the pair is fd
and its inverse when it is df: the inverse keeps the volatility and flips the correlation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..lake import Lake, new_run_id, sql_str
from ..universe import enabled, enabled_names, instrument, pair_currencies
from .carry import cip_forward
from .correl import WINDOWS, closes_wide, rolling_pair
from .curves import CURVE_OF_CURRENCY

FX_TENORS = (1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)
SPOT_SOURCE = "ecb"
TRADING_DAYS = 252
CURVE_MAX_AGE = 10  # days, as `discount_function`
READ_MAX_AGE = 7  # days: an older volatility or correlation is refused, as a stale close is


def spot_series(lake: Lake, pair: str, day=None, as_of=None, source: str = SPOT_SOURCE) -> pd.Series:
    """The rates of a pair from one source, oldest first, up to `day` when given."""
    where = f"pair = {sql_str(pair)} AND source = {sql_str(source)}"
    if day is not None:
        where += f" AND obs_date <= DATE '{pd.Timestamp(day).date()}'"
    rows = lake.read("fx_spots", where=where, columns="obs_date, rate", order="obs_date", as_of=as_of)
    return pd.Series(rows["rate"].to_numpy(dtype=float), index=pd.to_datetime(rows["obs_date"]), name=pair)


def _curve_days(lake: Lake, currency: str, as_of=None) -> dict:
    rows = lake.read("discount_curves", where=f"curve = {sql_str(CURVE_OF_CURRENCY[currency])}",
                     columns="obs_date, tenor, zero_rate", order="obs_date, tenor", as_of=as_of)
    rows["obs_date"] = pd.to_datetime(rows["obs_date"])
    return {d: (g["tenor"].to_numpy(dtype=float), g["zero_rate"].to_numpy(dtype=float)) for d, g in rows.groupby("obs_date")}


def _last_curve(curves: dict, days: np.ndarray, day: pd.Timestamp):
    i = np.searchsorted(days, np.datetime64(day), side="right") - 1
    if i < 0:
        return None
    last = pd.Timestamp(days[i])
    return curves[last] if (day - last).days <= CURVE_MAX_AGE else None


def forward_rows(spots: pd.Series, base_curves: dict, quote_curves: dict, tenors=FX_TENORS) -> pd.DataFrame:
    """obs_date, tenor, forward, points. The base currency is the foreign one of `cip_forward`. Curves are read as
    `discount_function` reads them: the last within ten days, flat zero rate outside the pillars. A day without both is skipped."""
    T = np.asarray(tenors, dtype=float)
    base_days, quote_days = np.array(sorted(base_curves), dtype="datetime64[ns]"), np.array(sorted(quote_curves), dtype="datetime64[ns]")
    frames = []
    for day, spot in spots.items():
        base, quote = _last_curve(base_curves, base_days, day), _last_curve(quote_curves, quote_days, day)
        if base is None or quote is None:
            continue
        forward = cip_forward(spot, np.exp(-np.interp(T, *quote) * T), np.exp(-np.interp(T, *base) * T))
        frames.append(pd.DataFrame({"obs_date": day, "tenor": T, "forward": forward, "points": forward - spot}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["obs_date", "tenor", "forward", "points"])


def build_fx_forwards(lake: Lake, pairs=None) -> list[dict]:
    """Forwards of the pairs with a curve on both sides. The others get a quality issue."""
    run = new_run_id("derive_fx")
    out, issues = [], []
    for pair in pairs if pairs is not None else [i.id for i in enabled("fx")]:
        spots = spot_series(lake, pair)
        if spots.empty:
            continue
        base, quote = pair_currencies(pair)
        missing = [c for c in (base, quote) if c not in CURVE_OF_CURRENCY]
        if missing:
            issues.append({"dataset": "fx_forwards", "item": pair, "check_name": "no_curve", "severity": "warning",
                           "detail": f"no forward: no discount curve in {', '.join(missing)}", "obs_date": spots.index[-1].date()})
            continue
        rows = forward_rows(spots, _curve_days(lake, base), _curve_days(lake, quote))
        out.append(lake.ingest("fx_forwards", rows.assign(pair=pair), "cip_on_ecb", "derived", run))
    if issues:
        lake.ingest("quality_issues", pd.DataFrame(issues), "derive_fx", "derived", run)
    return out


def realised_vol(spots: pd.Series, window: int) -> pd.Series:
    """Annualised standard deviation of the last `window` daily log returns."""
    return np.log(spots).diff().rolling(window).std().mul(np.sqrt(TRADING_DAYS)).dropna()


def build_fx_vols(lake: Lake, pairs=None) -> list[dict]:
    run = new_run_id("derive_fx")
    rows = []
    for pair in pairs if pairs is not None else [i.id for i in enabled("fx")]:
        spots = spot_series(lake, pair)
        for w in WINDOWS:
            s = realised_vol(spots, w)
            rows.append(pd.DataFrame({"pair": pair, "window": w, "method": "realized", "obs_date": s.index, "vol": s.to_numpy()}))
    return [lake.ingest("fx_vols", pd.concat(rows, ignore_index=True), "fx_spots", "derived", run)] if rows else []


def name_pair_series(closes: pd.DataFrame, spots: pd.Series, name: str, pair: str, window: int) -> pd.Series:
    """Rolling correlation of a name's returns with a pair's, on overlapping multi-day returns."""
    return rolling_pair(closes[[name]].join(spots.rename(pair), how="outer"), name, pair, window)


def build_fx_correl(lake: Lake, names=None, pairs=None) -> list[dict]:
    """Realised correlation of each name with each pair, in `correl_realized` with name_a the name and name_b the pair."""
    names = tuple(names if names is not None else enabled_names())
    closes = closes_wide(lake, names)
    run = new_run_id("derive_fx")
    rows = []
    for pair in pairs if pairs is not None else [i.id for i in enabled("fx")]:
        spots = spot_series(lake, pair)
        for name in closes.columns:
            for w in WINDOWS:
                s = name_pair_series(closes, spots, name, pair, w)
                rows.append(pd.DataFrame({"name_a": name, "name_b": pair, "window": w, "obs_date": s.index, "correlation": s.to_numpy()}))
    return [lake.ingest("correl_realized", pd.concat(rows, ignore_index=True), "prices_eod+fx_spots", "derived", run)] if rows else []


def _latest(rows: pd.DataFrame, what: str, day: pd.Timestamp):
    if rows.empty or (day - pd.Timestamp(rows["obs_date"].iloc[0])).days > READ_MAX_AGE:
        raise KeyError(f"no {what} in the {READ_MAX_AGE} days up to {day.date()}: run scripts/lake_update.py --only fx and scripts/lake_derive.py --only fx")
    return rows.iloc[0]


def fx_vol_at(lake: Lake, pair: str, day, window: int = 120, as_of=None) -> tuple[float, str, str]:
    """(volatility, origin, method): the latest stored at or before `day`."""
    day = pd.Timestamp(day)
    rows = lake.read("fx_vols", where=f"pair = {sql_str(pair)} AND \"window\" = {int(window)} AND obs_date <= DATE '{day.date()}'",
                     order="obs_date DESC LIMIT 1", as_of=as_of)
    r = _latest(rows, f"{pair} volatility", day)
    return float(r["vol"]), str(r["origin"]), str(r["method"])


def fx_correlation_at(lake: Lake, name: str, pair: str, day, window: int = 120, as_of=None) -> tuple[float, str]:
    """(correlation of the name with the pair as quoted, origin): the latest stored at or before `day`."""
    day = pd.Timestamp(day)
    rows = lake.read("correl_realized", where=f"name_a = {sql_str(name)} AND name_b = {sql_str(pair)} AND \"window\" = {int(window)} "
                     f"AND obs_date <= DATE '{day.date()}'", order="obs_date DESC LIMIT 1", as_of=as_of)
    r = _latest(rows, f"{name}-{pair} correlation", day)
    return float(r["correlation"]), str(r["origin"])


def quoted_pair(foreign: str, domestic: str) -> tuple[str, float]:
    """(pair, sign): sign +1 when the pair quotes domestic per foreign, -1 when it is the inverse."""
    for i in enabled("fx"):
        if pair_currencies(i.id) == (foreign, domestic):
            return i.id, 1.0
        if pair_currencies(i.id) == (domestic, foreign):
            return i.id, -1.0
    raise KeyError(f"no enabled pair between {foreign} and {domestic}")


@dataclass(frozen=True)
class QuantoLink:
    """What links a name to its payment currency at a date. `rho` is the correlation of the name with X = domestic per foreign."""
    rho: float
    vol_fx: float
    meta: dict


def quanto_link_at(lake: Lake, name: str, payment: str, day, window: int = 120, as_of=None) -> QuantoLink:
    """Correlation and FX volatility known at `day`, with their origins. Raises KeyError rather than assuming a value."""
    foreign = instrument(name).currency
    pair, sign = quoted_pair(foreign, payment)
    vol, vol_origin, method = fx_vol_at(lake, pair, day, window, as_of)
    corr, corr_origin = fx_correlation_at(lake, name, pair, day, window, as_of)
    rho = sign * corr
    return QuantoLink(rho, vol, {"foreign": foreign, "domestic": payment, "pair": pair, "inverted": sign < 0, "window": window, "rho": rho,
                                 "rho_origin": corr_origin, "vol_fx": vol, "vol_origin": vol_origin, "vol_method": method})

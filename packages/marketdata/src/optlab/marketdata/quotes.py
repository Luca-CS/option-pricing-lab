"""A day's option quotes from the lake, through the one cleaning chain (`optlab.marketdata.chains.clean_chain`), whatever the source."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from optlab.core.instruments import _BY_ID
from optlab.core.market import Market
from optlab.marketdata.chains import clean_chain
from optlab.marketdata.derive.curves import CURVE_OF_CURRENCY
from optlab.marketdata.lake import Lake, sql_str
from optlab.marketdata.sources.archives import snapshot_time


def discount_function(lake: Lake, currency: str, day: dt.date, as_of=None):
    """T -> D(T) from the stored discount curve on `day`, or the last one in the 10 days before (flat zero rate
    outside the pillars)."""
    curve = CURVE_OF_CURRENCY[currency]
    rows = lake.read(
        "discount_curves",
        where=f"curve = '{curve}' AND obs_date <= DATE '{day}' AND obs_date >= DATE '{day}' - INTERVAL 10 DAY",
        columns="obs_date, tenor, zero_rate",
        order="obs_date, tenor",
        as_of=as_of,
    )
    if rows.empty:
        raise KeyError(f"no {curve} curve around {day}")
    last = rows[rows["obs_date"] == rows["obs_date"].max()]
    t, z = last["tenor"].to_numpy(), last["zero_rate"].to_numpy()
    return lambda T: np.exp(-np.interp(np.asarray(T, dtype=float), t, z) * np.asarray(T, dtype=float))


def chain_rows(lake: Lake, underlying: str, day, source: str | None = None) -> pd.DataFrame:
    day = pd.Timestamp(day).date()
    where = f"underlying = {sql_str(underlying)} AND obs_date = DATE '{day}'"
    if source:
        where += f" AND source = {sql_str(source)}"
    return lake.read("option_quotes", where=where)


def as_clean_input(rows: pd.DataFrame) -> pd.DataFrame:
    """The columns the cleaning reads, from option_quotes rows."""
    return pd.DataFrame(
        {
            "root": rows["root"].to_numpy(),
            "strike": rows["strike"].to_numpy(),
            "bid": rows["bid"].to_numpy(),
            "ask": rows["ask"].to_numpy(),
            "type": np.where(rows["cp"] == "C", "call", "put"),
            "expiry": pd.to_datetime(rows["expiry"]).dt.strftime("%Y-%m-%d").to_numpy(),
        }
    )


@dataclass
class CleanDay:
    underlying: str
    day: dt.date
    source: str
    spot: float
    market: Market
    slices: list
    table: pd.DataFrame
    meta: dict
    origin: str = "observed"  # the quotes' origin in the lake: a synthetic chain says so on the vol lab's screens
    spot_source: str = "the file's close"  # where the spot came from: the file, the lake's close, or put-call parity

    @property
    def maturities(self) -> np.ndarray:
        return np.array([s["T"] for s in self.slices])


PARITY_MIN_DAYS = 3  # the shortest expiry the parity spot reads: a day or two carries next to no time value to read


def parity_spot(rows: pd.DataFrame, day, discount) -> tuple[float, str] | None:
    """(spot, how) implied by put-call parity, C - P = D (F - K), on the shortest expiry of at least three days quoted on
    both sides at three strikes or more: F is the median of K + (C - P) / D at the five strikes where C and P are
    closest, and the spot F D, the carry beyond the rate (dividends, repo) neglected over that expiry (0.03 % for a
    week at a dividend yield of 1.5 %). None when no expiry allows it."""
    from optlab.marketdata.chains import _year_fraction

    q = as_clean_input(rows)
    q = q[(q["bid"] > 0) & (q["ask"] >= q["bid"])].assign(mid=lambda f: 0.5 * (f["bid"] + f["ask"]))
    t0 = snapshot_time(pd.Timestamp(day).date())
    for expiry in sorted(q["expiry"].unique()):
        T = _year_fraction(t0, expiry)
        if T < PARITY_MIN_DAYS / 365:
            continue
        e = q[q["expiry"] == expiry]
        calls = e[e["type"] == "call"].groupby("strike")["mid"].median()
        puts = e[e["type"] == "put"].groupby("strike")["mid"].median()
        both = pd.concat([calls.rename("c"), puts.rename("p")], axis=1, join="inner")
        if len(both) < 3:
            continue
        D = float(discount(T))
        near = both.assign(gap=lambda f: (f["c"] - f["p"]).abs()).nsmallest(5, "gap")
        F = float(np.median(near.index.to_numpy() + (near["c"] - near["p"]).to_numpy() / D))
        days = round(T * 365)
        return F * D, f"put-call parity on the {expiry} expiry ({days} days; its dividends neglected)"
    return None


def clean_day(
    lake: Lake,
    underlying: str,
    day,
    source: str | None = None,
    spot: float | None = None,
    max_rel_spread: float = 0.5,
    min_strikes: int = 8,
) -> CleanDay:
    """The cleaned chain of a day, on the lake's discount curve. The spot defaults to the file's underlying close, else
    the lake's close, else the one put-call parity implies on the chain's shortest expiry (`parity_spot`)."""
    rows = chain_rows(lake, underlying, day, source)
    if rows.empty:
        raise KeyError(f"no quotes of {underlying} on {day}" + (f" from {source}" if source else ""))
    used = source or str(rows["source"].iloc[0])
    rows = rows[rows["source"] == used]
    day = pd.Timestamp(day).date()
    currency = _BY_ID[underlying].currency if underlying in _BY_ID else "USD"
    spot, spot_source = _spot_of(lake, rows, underlying, day, currency, spot)
    market, slices, table, meta = clean_chain(
        as_clean_input(rows),
        snapshot_time(day),
        spot,
        max_rel_spread,
        discount=discount_function(lake, currency, day),
        min_strikes=min_strikes,
    )
    origin = str(rows["origin"].iloc[0]) if "origin" in rows else "observed"
    return CleanDay(underlying, day, used, spot, market, slices, table, meta, origin, spot_source)


def _spot_of(lake: Lake, rows: pd.DataFrame, underlying: str, day: dt.date, currency: str, spot):
    """(spot, where it came from): given, the file's underlying close, the lake's close, or put-call parity."""
    if spot is not None:
        return float(spot), "given"
    closes = pd.to_numeric(rows["underlying_close"], errors="coerce").dropna()
    if len(closes):
        return float(closes.iloc[0]), "the file's close"
    if lake.has("prices_eod"):
        px = lake.read(
            "prices_eod", where=f"instrument = {sql_str(underlying)} AND obs_date = DATE '{day}'", columns="close"
        )
        if not px.empty:
            return float(px["close"].iloc[0]), "the lake's close"
    implied = parity_spot(rows, day, discount_function(lake, currency, day))
    if implied is None:
        raise KeyError(f"no close of {underlying} on {day}, and no expiry to read it from by put-call parity")
    return implied


def quote_days(lake: Lake, underlying: str, source: str | None = None) -> list:
    """The dates with quotes of a name, oldest first."""
    where = f"underlying = {sql_str(underlying)}" + (f" AND source = {sql_str(source)}" if source else "")
    con = lake.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT obs_date FROM read_parquet('{lake._pattern('option_quotes')}', hive_partitioning"  # noqa: S608 (identifiers from the code; values through sql_str or bound)
            f"=true, hive_types_autocast=false) WHERE {where} ORDER BY obs_date"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]

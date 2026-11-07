"""A day's option quotes from the lake, through the one cleaning chain (`optlab.data.clean_chain`), whatever the source."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data import clean_chain
from ..market import Market
from .derive.curves import CURVE_OF_CURRENCY
from .lake import Lake, sql_str
from .sources.archives import snapshot_time
from .universe import _BY_ID


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

    @property
    def maturities(self) -> np.ndarray:
        return np.array([s["T"] for s in self.slices])


def clean_day(
    lake: Lake,
    underlying: str,
    day,
    source: str | None = None,
    spot: float | None = None,
    max_rel_spread: float = 0.5,
    min_strikes: int = 8,
) -> CleanDay:
    """The cleaned chain of a day, on the lake's discount curve. The spot defaults to the file's underlying close,
    else the lake's close."""
    rows = chain_rows(lake, underlying, day, source)
    if rows.empty:
        raise KeyError(f"no quotes of {underlying} on {day}" + (f" from {source}" if source else ""))
    used = source or str(rows["source"].iloc[0])
    rows = rows[rows["source"] == used]
    day = pd.Timestamp(day).date()
    if spot is None:
        closes = pd.to_numeric(rows["underlying_close"], errors="coerce").dropna()
        if len(closes):
            spot = float(closes.iloc[0])
        else:
            px = lake.read(
                "prices_eod", where=f"instrument = {sql_str(underlying)} AND obs_date = DATE '{day}'", columns="close"
            )
            if px.empty:
                raise KeyError(f"no close of {underlying} on {day}: give the spot")
            spot = float(px["close"].iloc[0])
    currency = _BY_ID[underlying].currency if underlying in _BY_ID else "USD"
    market, slices, table, meta = clean_chain(
        as_clean_input(rows),
        snapshot_time(day),
        spot,
        max_rel_spread,
        discount=discount_function(lake, currency, day),
        min_strikes=min_strikes,
    )
    return CleanDay(underlying, day, used, spot, market, slices, table, meta)


def quote_days(lake: Lake, underlying: str, source: str | None = None) -> list:
    """The dates with quotes of a name, oldest first."""
    where = f"underlying = {sql_str(underlying)}" + (f" AND source = {sql_str(source)}" if source else "")
    con = lake.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT obs_date FROM read_parquet('{lake._pattern('option_quotes')}', hive_partitioning"
            f"=true, hive_types_autocast=false) WHERE {where} ORDER BY obs_date"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]

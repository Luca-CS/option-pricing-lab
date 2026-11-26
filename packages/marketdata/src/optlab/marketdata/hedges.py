"""What the hedging instruments read from the lake: the issuer's funding curve and the overnight fixings of swaps.

- `funding_curve(lake, day)`: the fictitious bank's spreads at every pillar on the last date at or before `day` (within
  ten days), as a `SpreadCurve` (origin `synthetic`).
- `current_rate(lake, swap, day, market)`: the floating rate of a swap's current period, compounded in arrears: the
  overnight fixings of the period up to `day` (SOFR, €STR or SONIA, ACT/360), the curve's rate for the rest. None when the
  period has not started or the lake holds no fixing, so the instrument reads the curve instead.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from optlab.marketdata.derive.bank import ENTITY
from optlab.marketdata.lake import Lake
from optlab.structured.dates import add_months, year_fraction
from optlab.structured.hedges import SpreadCurve

OVERNIGHT = {"USD": "USD.SOFR", "EUR": "EUR.ESTR", "GBP": "GBP.SONIA"}


def funding_curve(lake: Lake, day, entity: str = ENTITY) -> SpreadCurve:
    """The entity's spread curve at `day` or the last date before it (within ten days); KeyError without one."""
    day = pd.Timestamp(day).date()
    if not lake.has("credit_spreads"):
        raise KeyError("the lake holds no credit spreads")
    rows = lake.read(
        "credit_spreads",
        where=f"entity = '{entity}' AND obs_date <= DATE '{day}' AND obs_date >= DATE '{day}' - INTERVAL 10 DAY",
        order="obs_date",
    )
    if rows.empty:
        raise KeyError(f"no spread of {entity} around {day}")
    last = rows[rows["obs_date"] == rows["obs_date"].max()].sort_values("tenor")
    return SpreadCurve(entity, tuple(last["tenor"].astype(float)), tuple(last["spread"].astype(float)), "synthetic")


def _current_period(swap, day: dt.date) -> tuple[dt.date, dt.date] | None:
    """(start, end) of the floating period that contains `day`, None before the start or after the end."""
    if day < swap.start or day >= swap.end:
        return None
    k, end = 0, swap.end
    while True:
        start = max(add_months(swap.end, -swap.float_months * (k + 1)), swap.start)
        if start <= day < end:
            return start, end
        if start <= swap.start:
            return None
        end, k = start, k + 1


def current_rate(lake: Lake, swap, day, market) -> float | None:
    """The current period's floating rate: the overnight fixings compounded up to `day`, the curve's discount to the end."""
    day = pd.Timestamp(day).date()
    period = _current_period(swap, day)
    curve = OVERNIGHT.get(swap.currency)
    if period is None or curve is None or not lake.has("rate_points"):
        return None
    start, end = period
    if start >= day:
        return None
    rows = lake.read(
        "rate_points",
        where=f"curve = '{curve}' AND obs_date >= DATE '{start}' AND obs_date < DATE '{day}'",
        columns="obs_date, rate",
        order="obs_date",
    )
    if rows.empty:
        return None
    fixes = {pd.Timestamp(d).date(): float(r) / 100 for d, r in zip(rows["obs_date"], rows["rate"])}
    growth, d, last = 1.0, start, None
    while d < day:  # each calendar day accrues at the last fixing on or before it
        last = fixes.get(d, last)
        if last is not None:
            growth *= 1 + last / 360
        d += dt.timedelta(days=1)
    rest = 1.0 / float(market.D(year_fraction(day, end)))
    tau = year_fraction(start, end)
    return (growth * rest - 1.0) / tau if tau > 0 else None

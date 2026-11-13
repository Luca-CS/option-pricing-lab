"""Calendar days as pandas reads them, typed: pandas types `Timestamp(x).date()` as a date or NaT, the lake's columns
as Hashable or Series; these helpers say what the code already holds, without changing a value."""

from __future__ import annotations

import datetime as dt
from typing import cast

import pandas as pd


def as_day(x) -> dt.date:
    """`pd.Timestamp(x).date()`: a date, a datetime, an ISO string or a Timestamp as its calendar day (a NaT passes
    through, as before)."""
    return cast(dt.date, pd.Timestamp(x).date())


def shifted_day(x, business_days: int) -> dt.date:
    """The calendar day `business_days` weekdays before x (pandas' BDay, no holidays)."""
    return cast(dt.date, (cast(pd.Timestamp, pd.Timestamp(x)) - pd.tseries.offsets.BDay(business_days)).date())

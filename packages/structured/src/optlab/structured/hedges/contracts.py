"""Listed contracts: the index futures and dividend futures a hedge may use, their multipliers and their expiries.

Index futures expire on the third Friday of March, June, September and December (the quarterly cycle). Dividend futures
are modelled per calendar year: the exchange's period runs from one December expiry to the next, a few days off the
calendar year, which a model of the dividend stream cannot tell apart. The multipliers are the exchanges' (currency per
index point); a contract on a name without an entry here is refused.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexFuture:
    underlying: str
    code: str
    multiplier: float  # currency per index point
    currency: str
    tick: float  # index points


@dataclass(frozen=True)
class DividendFuture:
    underlying: str
    code: str
    multiplier: float  # currency per index point of dividends
    currency: str
    tick: float


INDEX_FUTURES = {
    "SPX": IndexFuture("SPX", "ES", 50.0, "USD", 0.25),
    "NDX": IndexFuture("NDX", "NQ", 20.0, "USD", 0.25),
    "RUT": IndexFuture("RUT", "RTY", 50.0, "USD", 0.1),
    "SX5E": IndexFuture("SX5E", "FESX", 10.0, "EUR", 1.0),
}


@dataclass(frozen=True)
class ListedOption:
    underlying: str
    code: str
    multiplier: float  # currency per index point
    currency: str


LISTED_OPTIONS = {
    "SPX": ListedOption("SPX", "SPX", 100.0, "USD"),
    "NDX": ListedOption("NDX", "NDX", 100.0, "USD"),
    "RUT": ListedOption("RUT", "RUT", 100.0, "USD"),
    "SX5E": ListedOption("SX5E", "OESX", 10.0, "EUR"),
}
DIVIDEND_FUTURES = {
    "SPX": DividendFuture("SPX", "SDA", 250.0, "USD", 0.01),
    "SX5E": DividendFuture("SX5E", "FEXD", 100.0, "EUR", 0.1),
}
QUARTER_MONTHS = (3, 6, 9, 12)


def third_friday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def quarterly_expiries(after: dt.date, count: int = 4) -> list[dt.date]:
    """The next `count` quarterly expiries strictly after `after`."""
    out, y, m = [], after.year, after.month
    while len(out) < count:
        if m in QUARTER_MONTHS:
            d = third_friday(y, m)
            if d > after:
                out.append(d)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def index_future(name: str) -> IndexFuture:
    try:
        return INDEX_FUTURES[name]
    except KeyError:
        raise KeyError(f"no listed index future on {name}: {', '.join(INDEX_FUTURES)} have one") from None


def listed_option(name: str) -> ListedOption:
    try:
        return LISTED_OPTIONS[name]
    except KeyError:
        raise KeyError(f"no listed European option on {name}: {', '.join(LISTED_OPTIONS)} have one") from None


def dividend_future(name: str) -> DividendFuture:
    try:
        return DIVIDEND_FUTURES[name]
    except KeyError:
        raise KeyError(f"no listed dividend future on {name}: {', '.join(DIVIDEND_FUTURES)} have one") from None

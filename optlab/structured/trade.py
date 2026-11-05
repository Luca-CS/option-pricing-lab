"""Trades: a product (term sheet or script) with its quantity and dates, serialisable to JSON.

A trade stores what was dealt, not market data: past fixings live in a fixings store, because real index levels
from Yahoo are personal-use data that must not be versioned. The strike date is part of the trade, so a stored
trade never re-strikes on a later valuation date.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field

from .schema import TermSheet, TermSheetError

STATUSES = ("live", "called", "matured", "cancelled")


def _date(d) -> dt.date:
    return dt.date.fromisoformat(d) if isinstance(d, str) else d


@dataclass(frozen=True)
class Trade:
    trade_id: str
    product: dict  # {"kind": "term_sheet", "term_sheet": {...}} or {"kind": "script", "script", "params", "format"}
    quantity: float  # signed: > 0 when the book holds the investor side
    trade_date: dt.date
    strike_date: dt.date
    book: str = "default"
    counterparty: str = ""
    status: str = "live"
    version: int = 1
    tags: tuple = field(default_factory=tuple)
    reserves: dict | None = None  # what the client price held above the base, frozen at issue (reserves.py)

    def __post_init__(self):
        object.__setattr__(self, "trade_date", _date(self.trade_date))
        object.__setattr__(self, "strike_date", _date(self.strike_date))
        object.__setattr__(self, "tags", tuple(self.tags))
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}, expected one of {', '.join(STATUSES)}")
        kind = self.product.get("kind")
        if kind == "term_sheet":
            sheet = TermSheet.from_dict(self.product["term_sheet"])
            if sheet.get("general.strike_date") != self.strike_date:
                raise TermSheetError(f"the term sheet strikes on {sheet.get('general.strike_date')}, the trade on "
                                     f"{self.strike_date}: build it with Trade.from_term_sheet")
        elif kind == "script":
            if not isinstance(self.product.get("script"), str):
                raise ValueError("a script product needs its text under 'script'")
        else:
            raise ValueError("product kind must be 'term_sheet' or 'script'")

    @classmethod
    def from_term_sheet(cls, trade_id: str, sheet: TermSheet, quantity: float, trade_date, strike_date=None,
                        **kw) -> "Trade":
        """A trade on a term sheet; the strike date (default: the sheet's, else the trade date) is written into
        the sheet, so that the stored product is fully determined."""
        strike = _date(strike_date or sheet.get("general.strike_date") or trade_date)
        sheet = sheet.replace(**{"general.strike_date": strike})
        return cls(trade_id, {"kind": "term_sheet", "term_sheet": sheet.to_dict(json_safe=True)}, quantity,
                   _date(trade_date), strike, **kw)

    def term_sheet(self) -> TermSheet | None:
        return TermSheet.from_dict(self.product["term_sheet"]) if self.product["kind"] == "term_sheet" else None

    def to_position(self, fixings: dict | None = None):
        from .portfolio import Position
        if self.product["kind"] == "term_sheet":
            return Position(self.trade_id, self.term_sheet(), self.quantity, fixings=fixings)
        return Position(self.trade_id, self.product["script"], self.quantity, dict(self.product.get("params", {})),
                        fixings, self.product.get("format", "note"))

    def to_json(self) -> str:
        d = asdict(self)
        d.update(trade_date=self.trade_date.isoformat(), strike_date=self.strike_date.isoformat(), tags=list(self.tags))
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Trade":
        return cls(**json.loads(s))

"""Trades: a product (term sheet, script or hedging instrument) with its quantity and dates, serialisable to JSON.

A trade stores what was dealt, not market data: past fixings live in a fixings store, because real index levels
from Yahoo are personal-use data that must not be versioned. The strike date is part of the trade, so a stored
trade never re-strikes on a later valuation date.

An instrument trade (ADR-0025) holds `{"kind": "instrument", "instrument": {...}, "fill", "fees"}`: what was dealt, the
cash paid per unit at the fill (negative: received) and the fees of the whole trade. Its strike date is its trade date.
A `simulated` trade is a what-if: never in the official EOD nor in the P&L history.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field

from optlab.structured.schema import TermSheet, TermSheetError

STATUSES = ("live", "called", "matured", "cancelled", "simulated", "closed")


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
                raise TermSheetError(
                    f"the term sheet strikes on {sheet.get('general.strike_date')}, the trade on "
                    f"{self.strike_date}: build it with Trade.from_term_sheet"
                )
        elif kind == "script":
            if not isinstance(self.product.get("script"), str):
                raise ValueError("a script product needs its text under 'script'")
        elif kind == "instrument":
            from optlab.structured.hedges import parse

            parse(self.product.get("instrument"))
            for k in ("fill", "fees"):
                x = self.product.get(k, 0.0)
                if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or abs(x) == float("inf"):
                    raise ValueError(f"an instrument trade's {k} is a finite number")
        else:
            raise ValueError("product kind must be 'term_sheet', 'script' or 'instrument'")

    @classmethod
    def from_term_sheet(
        cls, trade_id: str, sheet: TermSheet, quantity: float, trade_date, strike_date=None, **kw
    ) -> Trade:
        """A trade on a term sheet; the strike date (default: the sheet's, else the trade date) is written into
        the sheet, so that the stored product is fully determined."""
        strike = _date(strike_date or sheet.get("general.strike_date") or trade_date)
        sheet = sheet.replace(**{"general.strike_date": strike})
        return cls(
            trade_id,
            {"kind": "term_sheet", "term_sheet": sheet.to_dict(json_safe=True)},
            quantity,
            _date(trade_date),
            strike,
            **kw,
        )

    @classmethod
    def from_instrument(
        cls, trade_id: str, instrument, quantity: float, trade_date, fill: float = 0.0, fees: float = 0.0, **kw
    ) -> Trade:
        """A trade on a hedging instrument (an `Instrument` or its JSON form), struck on its trade date."""
        from optlab.structured.hedges import parse

        inst = parse(instrument) if isinstance(instrument, dict) else instrument
        d = _date(trade_date)
        product = {"kind": "instrument", "instrument": inst.to_dict(), "fill": float(fill), "fees": float(fees)}
        return cls(trade_id, product, quantity, d, d, **kw)

    def term_sheet(self) -> TermSheet | None:
        return TermSheet.from_dict(self.product["term_sheet"]) if self.product["kind"] == "term_sheet" else None

    def instrument(self):
        """The hedging instrument of an instrument trade, else None."""
        if self.product["kind"] != "instrument":
            return None
        from optlab.structured.hedges import parse

        return parse(self.product["instrument"])

    @property
    def desk_side(self) -> str:
        """`short` when the desk sold the product (a negative quantity: the book holds the issuer's side), else `long`."""
        return "short" if self.quantity < 0 else "long"

    def side_check(self) -> dict | None:
        """A warning when the quantity's sign contradicts the sheet's `costs.side`: a note priced as sold but booked
        long, or the reverse. Not a refusal, since buying back a sold note is legitimate."""
        sheet = self.term_sheet()
        if sheet is None or not self.quantity:
            return None
        priced = sheet.get("costs.side")
        expected = {"sell": "short", "buy": "long"}.get(priced)
        if expected is None or expected == self.desk_side:
            return None
        verb = "sold" if priced == "sell" else "bought"
        return {
            "priced_side": priced,
            "desk_side": self.desk_side,
            "message": f"priced as {verb} by the desk, but booked desk {self.desk_side} (quantity {self.quantity:+g}): "
            f"a {verb} note has a {'negative' if priced == 'sell' else 'positive'} quantity",
        }

    def to_position(self, fixings: dict | None = None):
        from optlab.structured.portfolio import Position

        if self.product["kind"] == "term_sheet":
            return Position(self.trade_id, self.term_sheet(), self.quantity, fixings=fixings)
        if self.product["kind"] == "instrument":
            return Position(self.trade_id, self.instrument(), self.quantity)
        return Position(
            self.trade_id,
            self.product["script"],
            self.quantity,
            dict(self.product.get("params", {})),
            fixings,
            self.product.get("format", "note"),
        )

    def to_json(self) -> str:
        d = asdict(self)
        d.update(trade_date=self.trade_date.isoformat(), strike_date=self.strike_date.isoformat(), tags=list(self.tags))
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> Trade:
        return cls(**json.loads(s))

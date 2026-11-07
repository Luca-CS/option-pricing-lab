"""Reserves: what a client price held above the base, frozen at issue, line by line, and released over the product's life.

A trade is valued at the base price. The lines of its quote stay with it as they were on the day it was dealt: they are not priced again. They amortise
linearly over the contractual life, by calendar days, and what is left is released when the product is called or matures. The reserve is a liability of
the desk, whichever side it took: a book's value net of reserves is the value of its positions less what remains.

A trade booked without costs has no reserve, which is not the same as a reserve of zero: the screens say so.
"""

from __future__ import annotations

import datetime as dt

METHOD = "linear over the contractual life, the rest released at a call or at maturity"


def frozen(desk: dict, issue, end, quote_id: str | None = None) -> dict:
    """The record kept with a trade, from a desk price as a dict (`DeskPrice.to_dict`), in % of notional."""
    lines = [
        {"key": x["key"], "label": x["label"], "kind": x["kind"], "cost": float(x["cost"]), "se": float(x["se"])}
        for x in desk["lines"]
    ]
    return {
        "issue": _iso(issue),
        "end": _iso(end),
        "side": desk["side"],
        "method": METHOD,
        "total": sum(x["cost"] for x in lines),
        "lines": lines,
        "base_price": desk["base_price"],
        "client_price": desk["final_price"],
        "quote_id": quote_id,
    }


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, dt.date) else str(d)


def _date(d) -> dt.date:
    return dt.date.fromisoformat(d) if isinstance(d, str) else d


def fraction_left(reserves: dict, day, ended=None) -> float:
    """What is left of the reserve at the close of a day. `ended` is the date of a call."""
    day, start, end = _date(day), _date(reserves["issue"]), _date(reserves["end"])
    if day >= end or (ended is not None and day >= _date(ended)):
        return 0.0
    if day <= start:
        return 1.0
    return (end - day).days / (end - start).days


def remaining(trade, day, ended=None) -> dict | None:
    """The reserve of a trade at a date in currency, in total and per line, or None for a trade booked without one."""
    r = trade.reserves
    if r is None:
        return None
    sheet = trade.term_sheet()
    N = float(sheet.get("general.notional")) if sheet is not None else 100.0
    size = abs(trade.quantity) * N / 100.0
    left = 0.0 if trade.status in ("called", "matured", "cancelled") else fraction_left(r, day, ended)
    return {
        "amount": size * r["total"] * left,
        "initial": size * r["total"],
        "fraction": left,
        "method": r["method"],
        "lines": [{**x, "amount": size * x["cost"] * left, "initial": size * x["cost"]} for x in r["lines"]],
    }

"""Trading: trades (every edit a new version), the book's valuation, lifecycle events, fixings, the pricing policy."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from optlab.server.report import book_report, desk_snapshot
from optlab.server.routers._common import ACCEPTED, Id, Page, audited, desk, page, report, submit
from optlab.server.schemas import CostDefaultsIn, FixingsFromLakeIn, FixingsIn, TradeIn, TradeUpdate, ValuationIn, body
from optlab.server.web import access

router = APIRouter(prefix="/trading", tags=["trading"])


# --- trades ---------------------------------------------------------------------------------------------------------
@router.get("/trades", **access(), response_model=Page)
def list_trades(
    request: Request,
    book: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=200),
):
    """Live and past trades (the latest version of each, deleted ones left out), in booking order."""
    items = desk(request).trades()["trades"]
    items = [
        t for t in items if (book is None or t.get("book") == book) and (status is None or t.get("status") == status)
    ]
    return page(items, cursor, limit)


@router.get("/trades/{trade_id}", **access())
def get_trade(trade_id: Id, request: Request):
    """A trade, its versions and the quote it came from."""
    return desk(request).trade(trade_id)


@router.post("/trades", **access("trader"), status_code=201)
def create_trade(t: TradeIn, request: Request):
    """Book a trade on a term sheet (from a quote: its sheet and its solved coupon)."""
    d = desk(request)
    return audited(request, "create_trade", t.trade_id, lambda: d.create_trade(body(t)))


@router.put("/trades/{trade_id}", **access("trader"))
def update_trade(trade_id: Id, t: TradeUpdate, request: Request):
    """Edit a trade: a new version, the old ones stay readable."""
    d = desk(request)
    return audited(
        request, "update_trade", trade_id, lambda: d.update_trade(trade_id, body(t)), before=d.trade(trade_id)
    )


@router.delete("/trades/{trade_id}", **access("trader"))
def delete_trade(trade_id: Id, request: Request):
    """Soft delete: `restore` undoes it."""
    d = desk(request)
    return audited(request, "delete_trade", trade_id, lambda: d.delete_trade(trade_id), before=d.trade(trade_id))


@router.post("/trades/{trade_id}/restore", **access("trader"))
def restore_trade(trade_id: Id, request: Request):
    d = desk(request)
    return audited(request, "restore_trade", trade_id, lambda: d.restore_trade(trade_id))


@router.post("/trades/{trade_id}/valuations", **access(), status_code=202, responses=ACCEPTED)
def value_trade(trade_id: Id, v: ValuationIn, request: Request):
    """Value one trade at the market in use."""
    return submit(request, "value_trade", lambda: desk(request).value_trade(trade_id, body(v)), target=trade_id)


@router.get("/trades/{trade_id}/events", **access())
def trade_events(trade_id: Id, request: Request):
    """Lifecycle events of a trade from its fixings: observations, coupons, calls, knock-ins."""
    return desk(request).trade_events(trade_id)


# --- the book -------------------------------------------------------------------------------------------------------
@router.post("/book/valuations", **access(), status_code=202, responses=ACCEPTED)
def value_book(v: ValuationIn, request: Request):
    """Value the book in independent groups, with delta, vega and the reserves held at issue."""
    return submit(request, "value_book", lambda: desk(request).price_book(body(v)))


@router.get("/events/upcoming", **access())
def upcoming(request: Request, days: int = Query(45, ge=1, le=3660)):
    """Observations and payments to come."""
    return desk(request).upcoming(days)


@router.get("/book/report", **access(), response_class=HTMLResponse)
def book_report_page(request: Request):
    """A standalone HTML report of the book and its reserves (the last valuation)."""
    return report(book_report(desk(request)), "book")


@router.get("/desk-snapshot", **access(), response_class=HTMLResponse)
def desk_snapshot_page(request: Request):
    """The whole desk on one page."""
    return report(desk_snapshot(desk(request)), "desk")


# --- fixings --------------------------------------------------------------------------------------------------------
@router.get("/fixings", **access())
def list_fixings(request: Request, underlying: str | None = Query(None, pattern=r"^[A-Z0-9._-]{1,16}$")):
    """Fixings in the model's scale (every market is rescaled to a spot of 100)."""
    return desk(request).fixings(underlying)


@router.post("/fixings", **access("trader"))
def add_fixings(f: FixingsIn, request: Request):
    """Add fixings: pasted text or CSV (parsed on the server), or levels; real levels with `real_spot`."""
    d = desk(request)
    return audited(request, "add_fixings", f.underlying, lambda: d.put_fixings(body(f)), before=d.fixings(f.underlying))


@router.delete("/fixings/{underlying}/{date}", **access("trader"))
def delete_fixing(underlying: str, date: dt.date, request: Request):
    d = desk(request)
    return audited(
        request,
        "delete_fixing",
        f"{underlying}/{date}",
        lambda: d.delete_fixing(underlying, date.isoformat()),
        before=d.fixings(underlying),
    )


@router.post("/fixings/from-lake", **access("trader"))
def fixings_from_lake(f: FixingsFromLakeIn, request: Request):
    """Real closes of the lake as fixings, converted to the model's scale."""
    d = desk(request)
    return audited(
        request, "fixings_from_lake", f.underlying, lambda: d.fixings_from_lake(body(f)), before=d.fixings(f.underlying)
    )


# --- pricing policy -------------------------------------------------------------------------------------------------
@router.get("/cost-defaults", **access())
def cost_defaults(request: Request):
    """The table of hedging-cost defaults by group of underlyings and size, and its versions."""
    return desk(request).cost_defaults()


@router.put("/cost-defaults", **access("trader"))
def put_cost_defaults(c: CostDefaultsIn, request: Request):
    """Save a new version of the table."""
    d = desk(request)
    return audited(request, "put_cost_defaults", None, lambda: d.put_cost_defaults(body(c)), before=d.cost_defaults())

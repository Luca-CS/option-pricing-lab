"""Market: the context in use, snapshots and their smile and term structure, the lake's coverage, universe and quality,
and the other underlyings typed for worst-ofs."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam

from optlab.server.routers._common import ACCEPTED, Page, audited, desk, page, submit
from optlab.server.schemas import MarketFromLakeIn, MarketInUse, OtherUnderlyingsIn, body
from optlab.server.web import access

router = APIRouter(prefix="/market", tags=["market"])
SNAPSHOT = r"^[\w.+-]{1,128}$"


@router.get("/context", **access())
def context(request: Request):
    """The market in use: valuation date, spot, curves, ATM vols, its snapshot and origin."""
    return desk(request).context()


@router.get("/snapshots", **access(), response_model=Page)
def list_snapshots(request: Request, cursor: str | None = None, limit: int = Query(100, ge=1, le=200)):
    """Stored market snapshots, the one in use flagged `active`."""
    items = desk(request).snapshots()["snapshots"]
    return page(items, cursor, limit)


@router.put("/in-use", **access("trader"))
def use_market(m: MarketInUse, request: Request):
    """Switch the desk to a stored snapshot (jobs already submitted keep theirs)."""
    d = desk(request)
    return audited(
        request, "use_market", m.snapshot_id, lambda: d.use_snapshot(body(m)), before={"snapshot_id": d.ctx.snapshot_id}
    )


@router.get("/snapshots/{snapshot_id}/smile", **access())
def smile(request: Request, snapshot_id: str = PathParam(..., pattern=SNAPSHOT), T: float = Query(1.0, gt=0, le=30)):
    """Implied volatilities of a snapshot's surface at T (`in-use` for the market in use)."""
    return desk(request).smile(T, snapshot_id)


@router.get("/snapshots/{snapshot_id}/term", **access())
def term(request: Request, snapshot_id: str = PathParam(..., pattern=SNAPSHOT)):
    """ATM term structure, forward and discount of a snapshot (`in-use` for the market in use)."""
    return desk(request).term_structure(snapshot_id)


@router.post("/snapshots/from-lake", **access("trader"), status_code=202, responses=ACCEPTED)
def market_from_lake(m: MarketFromLakeIn, request: Request):
    """Build the market of a past date from the lake (point in time), optionally with its SLV leverage."""
    return submit(request, "market_from_lake", lambda: desk(request).market_from_lake(body(m)), target=m.underlying)


@router.get("/other-underlyings", **access())
def other_underlyings(request: Request):
    """Names typed for worst-ofs beyond the calibrated one: flat markets (vol, dividend, rate) and correlations."""
    return desk(request).basket_settings()


@router.put("/other-underlyings", **access("trader"))
def put_other_underlyings(o: OtherUnderlyingsIn, request: Request):
    d = desk(request)
    return audited(request, "put_other_underlyings", None, lambda: d.put_basket(body(o)), before=d.basket_settings())


# --- the lake ---------------------------------------------------------------------------------------------------------
def _overview(request: Request) -> dict:
    return desk(request).data_overview()


@router.get("/lake/coverage", **access())
def lake_coverage(request: Request):
    """What the lake holds, table by table: first and last dates, rows, origin and source."""
    o = _overview(request)
    return {k: o.get(k) for k in ("available", "hint", "coverage", "raw_files", "lake") if k in o}


@router.get("/universe", **access())
def universe(request: Request):
    """The instrument registry and, for each name, the last close the lake holds."""
    o = _overview(request)
    return {"available": o.get("available", False), "instruments": o.get("instruments", [])}


@router.get("/lake/quality", **access())
def lake_quality(request: Request):
    """Open data-quality issues by table and check, and the latest ones."""
    o = _overview(request)
    return {
        "available": o.get("available", False),
        "issues": o.get("issues", []),
        "recent_issues": o.get("recent_issues", []),
    }


@router.get("/lake/vol-indices/{name}", **access())
def vol_index(
    request: Request, name: str = PathParam(..., pattern=r"^[A-Za-z0-9.]{1,16}$"), start: dt.date | None = None
):
    """A volatility index (VIX, VSTOXX...) since a date."""
    return desk(request).data_vol_index(name, start.isoformat() if start else None)


@router.get("/lake/basket-quotes", **access())
def basket_quotes(request: Request):
    """CvC and PvP pseudo-quotes (origin synthetic), the last year."""
    return desk(request).data_baskets()

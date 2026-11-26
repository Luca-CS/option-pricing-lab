"""Hedging (ADR-0025): instruments booked as trades, their marks at the market in use, and their lifecycle (promote a
simulated trade, discard it, roll a future). Booking and lifecycle go to the trader, marks to the roles that see the book."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from optlab.server.payloads import InstrumentMark, Roll, Trade, documented
from optlab.server.routers._common import Id, audited, desk
from optlab.server.schemas import HedgeIn, MarkIn, body
from optlab.server.web import Problem, access
from optlab.structured.hedges import HedgeError

router = APIRouter(prefix="/trading", tags=["trading"])


def _hedges(request: Request):
    return desk(request).hedges


def _checked(call: Callable[[], dict]) -> Callable[[], dict]:
    """An instrument the library refuses is the request's fault: 422 with the reason."""

    def run() -> dict:
        try:
            return call()
        except HedgeError as exc:
            raise Problem(422, str(exc)) from exc

    return run


@router.post("/hedges", **access("trader"), status_code=201, responses=documented(Trade, "the hedge booked", 201))
def create_hedge(h: HedgeIn, request: Request):
    """Book an instrument as a trade, live or simulated (a what-if), tagged `hedge`."""
    hedges = _hedges(request)
    return audited(request, "create_trade", h.trade_id, _checked(lambda: hedges.create(body(h))))


@router.post("/hedges/marks", **access(), responses=documented(InstrumentMark))
def mark_instrument(m: MarkIn, request: Request):
    """An instrument at the market in use: its value and fair level per unit, its half-spread, for a size."""
    return _checked(lambda: _hedges(request).mark(body(m)))()


@router.post("/trades/{trade_id}/promote", **access("trader"), responses=documented(Trade, "the trade, now live"))
def promote_trade(trade_id: Id, request: Request):
    """A simulated trade booked: live, a new version."""
    d = desk(request)
    return audited(
        request, "promote_trade", trade_id, _checked(lambda: d.hedges.promote(trade_id)), before=d.trade(trade_id)
    )


@router.post("/trades/{trade_id}/discard", **access("trader"))
def discard_trade(trade_id: Id, request: Request):
    """A simulated trade removed (a soft delete)."""
    d = desk(request)
    return audited(
        request, "discard_trade", trade_id, _checked(lambda: d.hedges.discard(trade_id)), before=d.trade(trade_id)
    )


@router.post("/trades/{trade_id}/roll", **access("trader"), responses=documented(Roll, "the closed and opened trades"))
def roll_trade(trade_id: Id, request: Request):
    """A live future closed at its level and the same size opened on the next quarterly expiry, in one audited call."""
    d = desk(request)
    return audited(request, "roll_trade", trade_id, _checked(lambda: d.hedges.roll(trade_id)), before=d.trade(trade_id))

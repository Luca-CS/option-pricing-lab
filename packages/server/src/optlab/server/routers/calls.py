"""The issuer's calls on the desk's notes (ADR-0026): the call dates to come, the review job that prices each note going
on and called, and the call itself, recorded on the trade. The call goes to the trader, the rest to the roles that see
the book."""

from __future__ import annotations

from fastapi import APIRouter, Request

from optlab.server.payloads import CallList, Trade, documented
from optlab.server.routers._common import ACCEPTED, Id, audited, desk, submit
from optlab.server.schemas import CallIn, CallReviewIn, body
from optlab.server.web import access

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/calls", **access(), responses=documented(CallList))
def calls(request: Request):
    """The next call date of each live issuer callable, with its notice date and call price, the soonest first."""
    return desk(request).calls.upcoming()


@router.post("/calls/reviews", **access("trader", "risk"), status_code=202, responses=ACCEPTED)
def review_calls(r: CallReviewIn, request: Request):
    """A job `call_review`: each note priced going on and called at its next date, on the same random numbers."""
    return submit(request, "review_calls", lambda: desk(request).calls.review(body(r)))


@router.post("/trades/{trade_id}/call", **access("trader"), responses=documented(Trade, "the trade, called"))
def call_trade(trade_id: Id, c: CallIn, request: Request):
    """The issuer's call of a note at its next call date: status called, the amount kept on the trade (audited)."""
    d = desk(request)
    return audited(request, "call_trade", trade_id, lambda: d.calls.call(trade_id, body(c)), before=d.trade(trade_id))

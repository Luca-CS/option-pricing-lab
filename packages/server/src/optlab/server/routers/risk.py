"""Risk: Greeks, ladders, scenarios (signed by the authenticated user), historical VaR, EOD runs and the explained P&L."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from optlab.server.report import risk_report
from optlab.server.routers._common import ACCEPTED, Id, Page, audited, desk, page, report, submit
from optlab.server.schemas import EodIn, GreeksIn, LadderIn, ScenarioRunIn, ScenariosIn, VarIn, body
from optlab.server.web import access, current_user

router = APIRouter(prefix="/risk", tags=["risk"])


def _risk(request: Request):
    return desk(request).risk


@router.post("/greeks", **access("trader", "risk"), status_code=202, responses=ACCEPTED)
def greeks(g: GreeksIn, request: Request):
    """Greeks of the book by name and bucket (node vegas, curves, cross Greeks, theta), with their SEs."""
    return submit(request, "greeks", _risk(request).run(body(g)))


@router.get("/runs", **access(), response_model=Page)
def list_runs(
    request: Request, kind: str | None = None, cursor: str | None = None, limit: int = Query(100, ge=1, le=200)
):
    """Stored risk runs (EOD, Greeks, ladders, scenarios, VaR), without their payload."""
    return page(_risk(request).runs(kind)["runs"], cursor, limit)


@router.get("/runs/{run_id}", **access())
def get_run(run_id: Id, request: Request):
    return _risk(request).run_get(run_id)


@router.post("/ladder", **access("trader", "risk"), status_code=202, responses=ACCEPTED)
def ladder(lad: LadderIn, request: Request):
    """Spot ladder under the vol rules (sticky moneyness, sticky strike, realistic)."""
    return submit(request, "ladder", _risk(request).ladder(body(lad)))


@router.get("/scenarios", **access())
def scenarios(request: Request):
    """The scenario library: its versions and who signed them."""
    return _risk(request).scenarios()


@router.put("/scenarios", **access("risk"))
def put_scenarios(s: ScenariosIn, request: Request):
    """A new version of the library, signed by the authenticated user."""
    r, user = _risk(request), current_user(request)
    payload = {**body(s), "signed_by": user.name}
    return audited(request, "put_scenarios", None, lambda: r.put_scenarios(payload), before=r.scenarios())


@router.post("/scenarios/runs", **access("trader", "risk"), status_code=202, responses=ACCEPTED)
def run_scenarios(s: ScenarioRunIn, request: Request):
    """Run the library (or the scenarios named) on the book: P&L by scenario and by trade."""
    return submit(request, "run_scenarios", _risk(request).run_scenarios(body(s)))


@router.post("/var", **access("risk"), status_code=202, responses=ACCEPTED)
def var(v: VarIn, request: Request):
    """Historical VaR by full revaluation: 1 day, 10 days, stressed, and the contributions."""
    return submit(request, "var", _risk(request).var(body(v)), detail={"days": v.days})


@router.get("/pnl", **access())
def pnl(
    request: Request, start: dt.date | None = Query(None, alias="from"), end: dt.date | None = Query(None, alias="to")
):
    """The cumulated explained P&L of the EOD runs between two dates."""
    return _risk(request).pnl(start.isoformat() if start else None, end.isoformat() if end else None)


@router.post("/eod-runs", **access("risk"), status_code=202, responses=ACCEPTED)
def eod_runs(e: EodIn, request: Request):
    """EOD runs of a period (a backfill is idempotent: a run asked again with the same key is the same run)."""
    return submit(
        request,
        "eod_runs",
        _risk(request).pnl_eod(body(e)),
        detail={"from": e.from_.isoformat(), "to": e.to.isoformat()},
    )


@router.get("/report", **access(), response_class=HTMLResponse)
def risk_report_page(request: Request):
    """A standalone HTML report: the last Greeks, ladder, scenarios and VaR."""
    return report(risk_report(_risk(request)), "risk")

"""Structuring: the term-sheet schema and its form, presets, quotes and payoff scripts."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from optlab.server.present import estimates
from optlab.server.report import quote_report
from optlab.server.routers._common import ACCEPTED, Id, Page, desk, page, report, submit
from optlab.server.schemas import QuoteIn, ScriptIn, ScriptPriceIn, TermSheetIn, body
from optlab.server.web import access, problem

router = APIRouter(prefix="/structuring", tags=["structuring"])


@router.get("/presets", **access())
def presets(request: Request):
    """The presets and their filled term sheets."""
    return desk(request).presets()


@router.get("/schema", **access())
def schema(request: Request):
    """Sections, fields (with `visible_if`), masks, and the JSON Schema of a term sheet: forms come from here."""
    return desk(request).schema()


@router.post("/termsheet/form", **access(), responses={422: {"description": "the sheet has a field in error"}})
def termsheet_form(sheet: TermSheetIn, request: Request):
    """A partial sheet in; out, the validated sheet, its visible form (fields shown by `visible_if`), the bounds and
    the costs that apply. A field in error answers 422 problem+json: `errors` [{field, message}] with the dotted path,
    and the members `form`, `bounds` and `costs` of the sheet without that field, so the form stays on screen."""
    out = desk(request).form(body(sheet))
    err = out.get("error")
    if err:
        return problem(
            422,
            err["message"],
            errors=[{"field": err.get("field") or "", "message": err["message"]}],
            extra={k: out[k] for k in ("type", "form", "bounds", "costs")},
        )
    return out


@router.post("/quotes", **access("structurer"), status_code=202, responses=ACCEPTED)
def create_quote(q: QuoteIn, request: Request):
    """Quote a sheet with the pricing policy (base, provisions, costs, optional Greeks and mix band)."""
    return submit(request, "create_quote", lambda: desk(request).quote(body(q)), detail={"type": q.sheet.type})


@router.get("/quotes", **access(), response_model=Page)
def list_quotes(request: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=200)):
    """Saved quotes, the latest first."""
    items = desk(request).repo.quotes(limit=10_000)
    return page(items, cursor, limit)


@router.get("/quotes/{quote_id}", **access())
def get_quote(quote_id: Id, request: Request):
    """A saved quote: its sheet and its result, Monte Carlo numbers as {value, se} (the job's result shape)."""
    q = desk(request).saved_quote(quote_id)
    return {**q, "result": estimates(q["result"])}


@router.get("/quotes/{quote_id}/report", **access(), response_class=HTMLResponse)
def quote_report_page(quote_id: Id, request: Request):
    """A standalone HTML report of a saved quote."""
    return report(quote_report(desk(request).saved_quote(quote_id)), f"quote-{quote_id}")


@router.post("/script/check", **access())
def script_check(s: ScriptIn, request: Request):
    """Parse and validate a payoff script: errors at their line and column."""
    return desk(request).script_check(body(s))


@router.post("/script/price", **access("structurer"), status_code=202, responses=ACCEPTED)
def script_price(s: ScriptPriceIn, request: Request):
    """Price a payoff script under a chosen model."""
    return submit(request, "script_price", lambda: desk(request).script_price(body(s)), detail={"model": s.model})

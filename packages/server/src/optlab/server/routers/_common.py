"""What the routers share: the desk, job submission (202 with the job id, its owner and an audit line), audited
writes, cursor pages and HTML reports with their own CSP."""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Path, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from optlab.server.jobs import IdempotencyConflict, submitting
from optlab.server.web import CSP_REPORT, Problem, State, current_user, state

MAX_PAGE = 200
ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
Id = Annotated[str, Path(pattern=ID_PATTERN, description="a stored id (trade, quote, run, job), never a file path")]
IDEMPOTENCY = "Idempotency-Key"
KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class JobAccepted(BaseModel):
    job_id: str = Field(..., description="follow it on GET /jobs/{job_id} or GET /jobs/{job_id}/events")
    reused: bool = Field(
        False,
        description="an existing job was given back: the same Idempotency-Key, or the same request still queued or "
        "running",
    )


# the 202 of a route that submits a job, for the OpenAPI document
ACCEPTED: dict[int | str, dict[str, Any]] = {202: {"model": JobAccepted, "description": "a job computes it"}}


class Page(BaseModel):
    items: list[dict]
    next: str | None = Field(None, description="the cursor of the next page, or null on the last one")


def desk(request: Request):
    return state(request).desk


def submit(
    request: Request, action: str, call: Callable[[], dict], target: str | None = None, detail: dict | None = None
):
    """Run the desk's submission as the caller (the job's owner is in its row) with the request's Idempotency-Key:
    202 {"job_id", "reused"}, and the submission in the audit log."""
    st: State = state(request)
    user = current_user(request)
    key = request.headers.get(IDEMPOTENCY)
    if key is not None and not KEY.fullmatch(key):
        raise Problem(422, f"{IDEMPOTENCY}: 1 to 128 letters, digits, '.', '_', ':' or '-'")
    try:
        with submitting(user.user_id, key):
            out = call()
    except IdempotencyConflict as exc:
        raise Problem(422, str(exc)) from None
    job_id, reused = out["job"], bool(out.get("reused"))
    st.audit.record(user.user_id, action, target, detail={"job_id": job_id, "reused": reused, **(detail or {})})
    return JSONResponse({"job_id": job_id, "reused": reused}, status_code=202)


def audited(
    request: Request, action: str, target: str | None, call: Callable[[], dict], before=None, detail: dict | None = None
) -> dict:
    """Run a write and keep it in the audit log, with the hash of the state before (if given) and of the result."""
    out = call()
    st = state(request)
    st.audit.record(current_user(request).user_id, action, target, before=before, after=out, detail=detail)
    return out


def page(items: list, cursor: str | None, limit: int) -> dict:
    """A page of a list in a stable order; the cursor is opaque to the client (an offset today)."""
    limit = max(1, min(int(limit), MAX_PAGE))
    try:
        start = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    except ValueError, UnicodeError:
        raise Problem(400, "bad cursor") from None
    if start < 0:
        raise Problem(400, "bad cursor")
    chunk = items[start : start + limit]
    nxt = start + limit
    return {"items": chunk, "next": base64.urlsafe_b64encode(str(nxt).encode()).decode() if nxt < len(items) else None}


def report(html: str, name: str) -> HTMLResponse:
    """A standalone report: inline CSS and SVG, no script, its own CSP."""
    return HTMLResponse(
        html,
        headers={"content-security-policy": CSP_REPORT, "content-disposition": f'inline; filename="{name}.html"'},
    )

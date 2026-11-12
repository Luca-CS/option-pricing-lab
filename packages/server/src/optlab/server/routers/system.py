"""Health, readiness, accounts and jobs: the routes every space of the front uses."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from optlab.server.routers._common import Id, desk
from optlab.server.schemas import Job, JobList, LoginIn, Ok
from optlab.server.security import COOKIE, LoginError
from optlab.server.web import Problem, access, current_user, state

router = APIRouter()


# --- health ---------------------------------------------------------------------------------------------------------
class Health(BaseModel):
    ok: bool = True


@router.get("/health", **access(public=True), response_model=Health)
def health():
    """The process answers (a liveness probe)."""
    return Health()


@router.get("/ready", **access(public=True))
def ready(request: Request):
    """The database answers and the lake folder is there (a readiness probe)."""
    st = state(request)
    with st.desk.db.read() as c:
        c.execute(text("SELECT 1"))
    return {"ok": True, "database": True, "lake": st.desk.lake.root.is_dir(), "revision": st.desk.db.revision()}


# --- auth -----------------------------------------------------------------------------------------------------------
class Me(BaseModel):
    user_id: str
    name: str
    roles: list[str]
    permissions: list[str] = Field(..., description="the operation ids this user may call")
    csrf_token: str = Field(..., description="send it back in X-CSRF-Token on every state-changing request")


def _permissions(request: Request, roles: frozenset[str]) -> list[str]:
    """The operation ids these roles may call, read from the OpenAPI document (each operation carries x-roles)."""
    from optlab.server.app import operations

    out = []
    for op in operations(request.app):
        allowed = op["roles"] or []
        if "public" in allowed or "admin" in roles or "viewer" in allowed or roles & set(allowed):
            out.append(op["id"])
    return sorted(out)


def _me(request: Request, user, csrf: str) -> Me:
    return Me(
        user_id=user.user_id,
        name=user.name,
        roles=sorted(user.roles),
        permissions=_permissions(request, user.roles),
        csrf_token=csrf,
    )


@router.post("/auth/login", **access(public=True), response_model=Me)
def login(body: LoginIn, request: Request, response: Response):
    """Sign in: a session cookie (HttpOnly, SameSite=Strict, Secure under TLS) and the CSRF token."""
    st = state(request)
    address = request.client.host if request.client else "unknown"
    try:
        user = st.accounts.authenticate(body.name, body.password, address)
    except LoginError as exc:
        st.audit.record(None, "login_failed", body.name[:64], detail={"address": address, "status": exc.status})
        raise Problem(exc.status, str(exc)) from None
    old = request.cookies.get(COOKIE)
    if old:  # a new session at every login: an old cookie cannot be fixed on someone
        st.accounts.close_session(old)
    token, csrf = st.accounts.open_session(user)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
        max_age=st.settings.session_absolute_hours * 3600,
    )
    st.audit.record(user.user_id, "login", user.name, detail={"address": address})
    return _me(request, user, csrf)


class LogoutIn(BaseModel):
    everywhere: bool = Field(False, description="end every session of this user, on every browser")


@router.post("/auth/logout", **access(), response_model=Ok)
def logout(request: Request, response: Response, body: LogoutIn | None = None):
    st, user = state(request), current_user(request)
    if body is not None and body.everywhere:
        st.accounts.close_all(user.user_id)
    else:
        st.accounts.close_session(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    st.audit.record(user.user_id, "logout", user.name, detail={"everywhere": bool(body and body.everywhere)})
    return {"ok": True}


@router.get("/auth/me", **access(), response_model=Me)
def me(request: Request):
    found = state(request).accounts.session(request.cookies.get(COOKIE))
    if found is None:
        raise Problem(401, "sign in first")
    return _me(request, *found)


# --- jobs -----------------------------------------------------------------------------------------------------------
def _own(request: Request, job_id: str) -> None:
    user, owner = current_user(request), state(request).job_owner.get(job_id)
    if "admin" not in user.roles and owner != user.user_id:
        raise Problem(404, f"no job {job_id!r}")  # someone else's job is not even said to exist


@router.get("/jobs", **access(), response_model=JobList)
def jobs(request: Request):
    """The jobs queued or running, without their results (the status bar)."""
    return {"jobs": desk(request).jobs.running()}


@router.get("/jobs/{job_id}", **access(), response_model=Job)
def job(job_id: Id, request: Request):
    """State, elapsed time, and the result or the error (the submitter or an admin)."""
    _own(request, job_id)
    return desk(request).job(job_id)


@router.post("/jobs/{job_id}/cancel", **access(), response_model=Ok)
def cancel(job_id: Id, request: Request):
    """A queued job is dropped; a running one cannot be interrupted, its result is dropped when it ends."""
    _own(request, job_id)
    out = desk(request).cancel(job_id)
    state(request).audit.record(current_user(request).user_id, "cancel_job", job_id)
    return out


RANK = {"queued": 1, "running": 2}  # the end of a job is 3


@router.get("/jobs/{job_id}/events", **access(), response_class=StreamingResponse)
async def job_events(job_id: Id, request: Request):
    """Server-Sent Events: `state` on every change (queued, running), then `done`, `error` or `cancelled` with the
    job, and the stream ends. An event's id is the rank of its state (queued 1, running 2, the end 3), the same on
    every connection, so a reconnect with Last-Event-ID gets only what it missed."""
    _own(request, job_id)
    d = desk(request)
    try:
        last_seen = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        last_seen = 0

    async def stream():
        previous, started = None, time.monotonic()
        while True:
            j = d.job(job_id)
            if j["state"] != previous:
                previous = j["state"]
                final = j["state"] in ("done", "error", "cancelled")
                n = 3 if final else RANK.get(j["state"], 1)
                if n > last_seen:
                    payload = j if final else {k: j[k] for k in ("id", "kind", "state", "elapsed")}
                    yield f"id: {n}\nevent: {j['state'] if final else 'state'}\ndata: {json.dumps(payload)}\n\n"
                if final:
                    return
            if await request.is_disconnected():
                return
            if time.monotonic() - started > 3600:
                yield "event: timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-store"})

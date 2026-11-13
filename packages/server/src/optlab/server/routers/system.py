"""Health, readiness, accounts and jobs: the routes every space of the front uses."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from optlab.server import present
from optlab.server.jobs import FINAL, Workers
from optlab.server.routers._common import Id, desk
from optlab.server.schemas import CancelOut, Job, JobList, LoginIn, Ok
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
def ready(request: Request, response: Response):
    """The database answers, the lake folder is there and a worker serves the queue (a readiness probe: 503 when
    there is no worker, since every computation waits for one)."""
    st = state(request)
    with st.desk.db.read() as c:
        c.execute(text("SELECT 1"))
    worker = bool(Workers(st.desk.db).alive())
    if not worker:
        response.status_code = 503
    return {
        "ok": worker,
        "database": True,
        "lake": st.desk.lake.root.is_dir(),
        "worker": worker,
        "revision": st.desk.db.revision(),
    }


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
    user = current_user(request)
    try:
        owner = desk(request).jobs.owner(job_id)
    except KeyError:
        owner = None
    if "admin" not in user.roles and (owner is None or owner != user.user_id):
        raise Problem(404, f"no job {job_id!r}")  # someone else's job (or the system's) is not even said to exist


def shown(j: dict) -> dict:
    """A job as the API shows it: its envelope with every Monte Carlo number as {value, se} (api.md §5)."""
    return {**j, "result": present.result(j["kind"], j.get("result"))}


@router.get("/jobs", **access(), response_model=JobList)
def jobs(request: Request):
    """The caller's jobs queued or running (an admin's: everyone's), with their progress, without results (the
    status bar)."""
    user = current_user(request)
    return {"jobs": desk(request).jobs.active(user.user_id, everyone="admin" in user.roles)}


@router.get("/jobs/{job_id}", **access(), response_model=Job)
def job(job_id: Id, request: Request):
    """State, progress, step, ETA, and the result {market, method, result} or the error (the submitter or an
    admin)."""
    _own(request, job_id)
    return shown(desk(request).jobs.get(job_id))


@router.post("/jobs/{job_id}/cancel", **access(), response_model=CancelOut)
def cancel(job_id: Id, request: Request):
    """A queued job ends `cancelled` at once; a running one stops at its next batch and ends `cancelled`."""
    _own(request, job_id)
    out = desk(request).cancel(job_id)
    state(request).audit.record(current_user(request).user_id, "cancel_job", job_id, detail={"state": out["state"]})
    return out


EVENT_FIELDS = ("id", "kind", "state", "progress", "step", "eta", "elapsed")
KEEPALIVE_SECONDS = 15.0


@router.get("/jobs/{job_id}/events", **access(), response_class=StreamingResponse)
async def job_events(job_id: Id, request: Request):
    """Server-Sent Events: `state` when the job is queued or starts, `progress` while it runs (fraction, step, ETA),
    then `done`, `error` or `cancelled` with the whole job, and the stream ends (or `unauthorized` when the session
    ends meanwhile, checked every 15 s). An event's id is the job's version
    (+1 at every change of its row), so a reconnect with Last-Event-ID gets the job's latest state only if it changed
    since; the last event always comes."""
    _own(request, job_id)
    queue = desk(request).jobs
    accounts, token = state(request).accounts, request.cookies.get(COOKIE)
    try:
        last_seen = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        last_seen = 0

    async def stream():
        seen, started, quiet = last_seen, time.monotonic(), time.monotonic()
        previous_state = None
        while True:
            j = await asyncio.to_thread(queue.get, job_id)
            final = j["state"] in FINAL
            if j["version"] > seen:
                seen = j["version"]
                if final:
                    yield f"id: {seen}\nevent: {j['state']}\ndata: {json.dumps(shown(j))}\n\n"
                    return
                kind = "state" if j["state"] != previous_state else "progress"
                previous_state = j["state"]
                yield f"id: {seen}\nevent: {kind}\ndata: {json.dumps({k: j[k] for k in EVENT_FIELDS})}\n\n"
                quiet = time.monotonic()
            elif final:  # the client had the end already (Last-Event-ID at the last version)
                return
            if await request.is_disconnected():
                return
            if time.monotonic() - started > 3600:
                yield "event: timeout\ndata: {}\n\n"
                return
            if time.monotonic() - quiet > KEEPALIVE_SECONDS:  # a comment, so that proxies keep the stream open
                if await asyncio.to_thread(accounts.session, token) is None:  # logged out or revoked meanwhile
                    yield "event: unauthorized\ndata: {}\n\n"
                    return
                yield ": keepalive\n\n"
                quiet = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-store"})

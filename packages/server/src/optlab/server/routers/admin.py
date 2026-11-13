"""Admin: the system, the workers and their schedules, every job, the network checks, the users and the audit log
(admin only)."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam

from optlab.server.jobs import Workers, iso
from optlab.server.netcheck import lan_blockers
from optlab.server.routers._common import ACCEPTED, Page, audited, desk, page, submit
from optlab.server.schedules import Schedules
from optlab.server.schemas import (
    AuditPage,
    Pipeline,
    PipelineList,
    PipelineUpdate,
    UserIn,
    UserList,
    UserOut,
    UserUpdate,
)
from optlab.server.web import Problem, access, current_user, state

router = APIRouter(prefix="/admin", tags=["admin"])
NAME = PathParam(..., pattern=r"^[a-z_]{1,32}$", description="a schedule's name")


def _backups(folder: Path) -> list[dict]:
    files = sorted((folder / "nightly").glob("optlab-*.sqlite"), reverse=True)
    return [{"file": f.name, "bytes": f.stat().st_size, "at": iso(f.stat().st_mtime)} for f in files[:30]]


@router.get("/system", **access("admin"))
def system(request: Request):
    """Version, market in use, the workers (with the engine and the GPU as each sees them), the jobs by state, the
    last backups, the paths of the settings."""
    st = state(request)
    d = desk(request)
    return {
        **d.health(),
        "version": version("optlab-server"),
        "settings": st.settings.describe(),
        "database_revision": d.db.revision(),
        "workers": Workers(d.db).recent(),
        "job_counts": d.jobs.counts(),
        "backups": _backups(Path(st.settings.backups)),
    }


# --- the worker's schedules -----------------------------------------------------------------------------------------
def _schedules(request: Request) -> Schedules:
    s = Schedules(desk(request))
    s.ensure_defaults()  # an api with an external worker shows them before the worker's first start
    return s


@router.get("/pipelines", **access("admin"), response_model=PipelineList)
def pipelines(request: Request):
    """The schedules of the worker (backup, housekeeping, lake update, risk EOD): days, time, on or off, last and next
    run, and the state of the last job."""
    d = desk(request)
    out = []
    for s in _schedules(request).all():
        last = None
        if s["last_job_id"]:
            try:
                j = d.jobs.get(s["last_job_id"])
                last = {"id": j["id"], "state": j["state"], "finished_at": j["finished_at"], "error": j["error"]}
            except KeyError:
                last = None
        out.append({**s, "last_run_at": iso(s["last_run_at"]), "next_run_at": iso(s["next_run_at"]), "last_job": last})
    return {"pipelines": out}


@router.patch("/pipelines/{name}", **access("admin"), response_model=Pipeline)
def update_pipeline(p: PipelineUpdate, request: Request, name: str = NAME):
    """Turn a schedule on or off, move it, or change its parameters (audited)."""
    sch = _schedules(request)
    try:
        before = sch.get(name)
    except KeyError as exc:
        raise Problem(404, str(exc).strip("'\"")) from None
    user = current_user(request)
    try:
        after = audited(
            request,
            "update_pipeline",
            name,
            lambda: sch.update(name, p.model_dump(exclude_unset=True), user.user_id),
            before=before,
        )
    except ValueError as exc:
        raise Problem(422, str(exc)) from None
    return {
        **after,
        "last_run_at": iso(after["last_run_at"]),
        "next_run_at": iso(after["next_run_at"]),
        "last_job": None,
    }


@router.post("/pipelines/{name}/run", **access("admin"), status_code=202, responses=ACCEPTED)
def run_pipeline(request: Request, name: str = NAME):
    """Submit a schedule's job now, whether it is on or off (audited; the job is the admin's)."""
    sch = _schedules(request)
    try:
        sch.get(name)
    except KeyError as exc:
        raise Problem(404, str(exc).strip("'\"")) from None
    user = current_user(request)
    return submit(request, "run_pipeline", lambda: sch.submit(name, user.user_id), target=name)


@router.get("/jobs", **access("admin"), response_model=Page)
def all_jobs(
    request: Request,
    kind: str | None = Query(None, pattern=r"^[a-z_]{1,32}$"),
    cursor: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
):
    """Every job, the newest first, without results: who submitted it (or which schedule), its state and times."""
    return page(desk(request).jobs.history(everyone=True, kind=kind), cursor, limit)


@router.get("/network", **access("admin"))
def network(request: Request):
    """What would stop other machines from reaching this one on the LAN (Windows firewall and network profile;
    read-only, fixed commands)."""
    return {"lan": state(request).settings.lan, "blockers": lan_blockers()}


@router.get("/users", **access("admin"), response_model=UserList)
def list_users(request: Request):
    return {"users": state(request).accounts.all()}


@router.post("/users", **access("admin"), status_code=201, response_model=UserOut)
def create_user(u: UserIn, request: Request):
    st = state(request)
    try:
        user = st.accounts.create(u.name, u.password, u.roles)
    except ValueError as exc:
        raise Problem(409 if "exists" in str(exc) else 422, str(exc)) from None
    st.audit.record(current_user(request).user_id, "create_user", u.name, detail={"roles": sorted(user.roles)})
    return st.accounts.get(u.name)


@router.patch("/users/{name}", **access("admin"), response_model=UserOut)
def update_user(name: str, u: UserUpdate, request: Request):
    """Roles, disabled, password: each change ends every session of the user."""
    st = state(request)
    before = st.accounts.get(name)
    if before is None:
        raise Problem(404, f"no user {name!r}")
    try:
        if u.roles is not None:
            st.accounts.set_roles(name, u.roles)
        if u.disabled is not None:
            st.accounts.set_disabled(name, u.disabled)
        if u.password is not None:
            st.accounts.set_password(name, u.password)
    except ValueError as exc:
        raise Problem(422, str(exc)) from None
    after = st.accounts.get(name)
    changed = sorted(k for k in ("roles", "disabled", "password") if getattr(u, k) is not None)
    st.audit.record(
        current_user(request).user_id, "update_user", name, before=before, after=after, detail={"changed": changed}
    )
    return after


@router.get("/audit", **access("admin"), response_model=AuditPage)
def audit(request: Request, before: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=200)):
    """The audit log, the latest first; `next` is the `before` of the next page."""
    rows = state(request).audit.entries(limit, before)
    return {"items": rows, "next": rows[-1]["audit_id"] if len(rows) == limit else None}

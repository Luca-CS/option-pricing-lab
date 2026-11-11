"""Admin: the system, the network checks, the users and the audit log (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from optlab.server.netcheck import lan_blockers
from optlab.server.routers._common import desk
from optlab.server.schemas import AuditPage, UserIn, UserList, UserOut, UserUpdate
from optlab.server.web import Problem, access, current_user, state

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/system", **access("admin"))
def system(request: Request):
    """Version, market in use, workers, jobs, the engine and the GPU, the paths of the settings."""
    st = state(request)
    return {**desk(request).health(), "settings": st.settings.describe(), "database_revision": st.desk.db.revision()}


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

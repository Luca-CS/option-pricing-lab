"""The authorisation matrix (ADR-0020, handoff §4.2): every operation of the OpenAPI document x every role, and
anonymous, against the roles the route declares. Generated from the route table, so no route escapes it: a new route
is in the matrix the day it is added. Jobs are not run (their submission is replaced by a stub): the matrix checks who
gets through, not what the computation gives (the routers' own tests do)."""

import re
from typing import Any

import pytest
from _api import ROLES, api

from optlab.server.app import operations

PLACEHOLDER = {
    "trade_id": "T-0000-none",
    "quote_id": "none",
    "job_id": "none",
    "run_id": "none",
    "snapshot_id": "in-use",
    "name": "nobody",
    "underlying": "SPX",
    "date": "2025-01-06",
}
DENIED = {401, 403}


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    with api(tmp_path_factory.mktemp("matrix")) as a:
        a.state.desk._submit = lambda kind, params, frozen=None: {"job": "stub-job", "reused": False}
        clients: dict[str | None, Any] = {role: a.client(role) for role in ROLES}
        clients[None] = a.client()
        yield a, clients


def _url(path: str) -> str:
    return re.sub(r"\{(\w+)(?::path)?\}", lambda m: PLACEHOLDER.get(m.group(1), "x"), path)


def _call(client, op):
    kwargs: dict[str, Any] = {"json": {}} if op["method"] in ("POST", "PUT", "PATCH") else {}
    return client.request(op["method"], _url(op["path"]), **kwargs)


def test_every_operation_declares_who_may_call_it(setup):
    a, _ = setup
    ops = operations(a.app)
    assert len(ops) >= 70 and all(op["roles"] for op in ops)
    assert {r for op in ops for r in op["roles"]} <= {*ROLES, "public"}


def test_the_matrix(setup):
    a, clients = setup
    failures = []
    for op in operations(a.app):
        if op["path"].endswith("/auth/login") or op["path"].endswith("/auth/logout"):
            continue  # they open and close sessions: tested on their own
        allowed = set(op["roles"])
        for role, client in clients.items():
            status = _call(client, op).status_code
            if "public" in allowed:
                ok = status not in DENIED
            elif role is None:
                ok = status == 401
            elif role == "admin" or "viewer" in allowed or role in allowed:
                ok = status not in DENIED and status < 500
            else:
                ok = status == 403
            if not ok:
                failures.append(f"{op['method']} {op['path']} as {role}: {status}")
    assert failures == [], "\n".join(failures)

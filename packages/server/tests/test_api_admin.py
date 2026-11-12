"""API v1, admin router: system, network checks, users (each change ends the user's sessions), the audit log; and the
accounts command line (`python -m optlab.server.users`), which Luca uses to create his own admin account."""

import io
import secrets

import pytest
from _api import api

from optlab.server import users as users_cli

PASSWORD = secrets.token_urlsafe(18)  # generated for the run, never written anywhere


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-admin")) as a:
        yield a


@pytest.fixture(scope="module")
def admin(a):
    return a.client("admin")


def test_system_and_network(a, admin):
    s = admin.get("/api/v1/admin/system").json()
    assert s["ok"] and s["database_revision"] == a.state.desk.db.revision() and s["settings"]
    assert all(PASSWORD not in str(v) for v in s["settings"].values())
    n = admin.get("/api/v1/admin/network").json()
    assert n["lan"] is False and isinstance(n["blockers"], list)


def test_users_crud_through_the_api(a, admin):
    r = admin.post("/api/v1/admin/users", json={"name": "zoe", "password": PASSWORD, "roles": ["trader"]})
    assert r.status_code == 201 and r.json()["roles"] == ["trader"] and "password_hash" not in r.json()
    assert (
        admin.post("/api/v1/admin/users", json={"name": "zoe", "password": PASSWORD, "roles": ["trader"]}).status_code
        == 409
    )
    weak = admin.post("/api/v1/admin/users", json={"name": "yan", "password": "short", "roles": ["viewer"]})
    assert weak.status_code == 422 and weak.json()["errors"][0]["field"] == "password"
    bad_role = admin.post("/api/v1/admin/users", json={"name": "yan", "password": PASSWORD, "roles": ["root"]})
    assert bad_role.status_code == 422
    zoe = a.client()
    assert zoe.post("/api/v1/auth/login", json={"name": "zoe", "password": PASSWORD}).status_code == 200
    r = admin.patch("/api/v1/admin/users/zoe", json={"roles": ["viewer", "risk"]})
    assert r.status_code == 200 and r.json()["roles"] == ["risk", "viewer"]
    assert zoe.get("/api/v1/auth/me").status_code == 401  # its sessions ended with the change
    assert admin.patch("/api/v1/admin/users/nobody", json={"disabled": True}).status_code == 404
    names = [u["name"] for u in admin.get("/api/v1/admin/users").json()["users"]]
    assert "zoe" in names and all("hash" not in k for u in admin.get("/api/v1/admin/users").json()["users"] for k in u)
    assert a.client("trader").get("/api/v1/admin/users").status_code == 403


def test_the_audit_log_pages_and_keeps_no_secret(a, admin):
    page = admin.get("/api/v1/admin/audit", params={"limit": 3}).json()
    assert len(page["items"]) == 3 and page["next"]
    more = admin.get("/api/v1/admin/audit", params={"limit": 3, "before": page["next"]}).json()
    assert more["items"][0]["audit_id"] < page["items"][-1]["audit_id"]
    everything = admin.get("/api/v1/admin/audit", params={"limit": 200}).json()["items"]
    assert {"create_user", "update_user", "login"} <= {e["action"] for e in everything}
    assert all(PASSWORD not in str(e) for e in everything)


def test_the_accounts_command_line(a, monkeypatch, capsys):
    db = str(a.state.desk.db.path)

    def run(*argv, password=None):
        if password is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
        return users_cli.main([*argv, "--database", db, *(["--stdin"] if password is not None else [])])

    assert run("create", "luc", "--roles", "admin,trader", password=PASSWORD) == 0
    assert "created luc (admin, trader)" in capsys.readouterr().out
    assert run("create", "luc", "--roles", "admin", password=PASSWORD) == 1
    assert run("create", "lux", "--roles", "admin", password="short") == 1
    assert run("roles", "luc", "risk") == 0
    assert run("disable", "luc") == 0
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "luc" in out and "disabled" in out and PASSWORD not in out
    assert run("password", "luc", password=secrets.token_urlsafe(18)) == 0
    assert run("enable", "nobody") == 1
    entries = [e for e in a.state.audit.entries(50) if e["action"].startswith("cli_")]
    assert {e["action"] for e in entries} >= {
        "cli_create_user",
        "cli_set_roles",
        "cli_disable_user",
        "cli_set_password",
    }
    assert all(e["user_id"] is None for e in entries)  # the system, not a signed-in user

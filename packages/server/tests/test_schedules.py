"""The worker's schedules (ADR-0006): when they fire, the admin's routes to see, change and run them, the nightly
backup and its retention, the housekeeping, and the jobs a schedule submits (the system's: admins only)."""

import datetime as dt
import os
import time
from zoneinfo import ZoneInfo

import pytest
from _api import api
from _tasks import wait_state

from optlab.server.schedules import DEFAULTS, Schedules, next_run

PARIS = ZoneInfo("Europe/Paris")


def _at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=PARIS).timestamp()


def test_next_run_on_the_days_and_at_the_local_time():
    weekdays = "mon,tue,wed,thu,fri"
    friday_late = _at(2026, 11, 13, 23, 30)  # a Friday
    assert next_run(weekdays, "22:30", "Europe/Paris", friday_late) == _at(2026, 11, 16, 22, 30)  # Monday
    assert next_run(weekdays, "23:45", "Europe/Paris", friday_late) == _at(2026, 11, 13, 23, 45)  # later the same day
    every = DEFAULTS["backup"]["days"]
    assert next_run(every, "03:00", "Europe/Paris", _at(2026, 3, 28, 12, 0)) == _at(2026, 3, 29, 3, 0)  # over DST
    with pytest.raises(ValueError):
        next_run("monday", "03:00", "Europe/Paris", friday_late)


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("schedules")) as a:
        yield a


def test_the_pipelines_and_their_defaults(a):
    admin = a.client("admin")
    rows = {p["name"]: p for p in admin.get("/api/v1/admin/pipelines").json()["pipelines"]}
    assert set(rows) == {"backup", "housekeeping", "lake_backup", "lake", "eod"}
    assert rows["lake_backup"]["enabled"] and rows["lake_backup"]["days"] == "sun"
    assert rows["backup"]["enabled"] and rows["backup"]["next_run_at"] and rows["housekeeping"]["enabled"]
    assert not rows["eod"]["enabled"] and rows["eod"]["next_run_at"] is None and not rows["lake"]["enabled"]
    assert a.client("risk").get("/api/v1/admin/pipelines").status_code == 403


def test_an_admin_turns_a_schedule_on_moves_it_and_is_audited(a):
    admin = a.client("admin")
    r = admin.patch("/api/v1/admin/pipelines/eod", json={"enabled": True, "at": "23:15", "days": "mon,wed,fri"})
    assert r.status_code == 200, r.text
    eod = r.json()
    assert eod["enabled"] and eod["at"] == "23:15" and eod["days"] == "mon,wed,fri" and eod["next_run_at"]
    when = dt.datetime.fromisoformat(eod["next_run_at"]).astimezone(PARIS)
    assert when.weekday() in (0, 2, 4) and (when.hour, when.minute) == (23, 15)
    entry = next(e for e in a.state.audit.entries(10) if e["action"] == "update_pipeline")
    assert entry["target"] == "eod" and entry["before_hash"] != entry["after_hash"]
    assert admin.patch("/api/v1/admin/pipelines/eod", json={"at": "25:00"}).status_code == 422
    assert admin.patch("/api/v1/admin/pipelines/eod", json={"params": {"shell": "rm"}}).status_code == 422
    assert admin.patch("/api/v1/admin/pipelines/backup", json={"params": {"keep_days": 0}}).status_code == 422
    huge = {"params": {"options": {"n_paths": 10**9}}}  # the bounds of POST /risk/eod-runs hold here too
    assert admin.patch("/api/v1/admin/pipelines/eod", json=huge).status_code == 422
    fine = {"params": {"greeks": False, "options": {"precision": "quick"}}}
    assert admin.patch("/api/v1/admin/pipelines/eod", json=fine).status_code == 200
    assert admin.patch("/api/v1/admin/pipelines/nothing", json={"enabled": True}).status_code == 404
    admin.patch("/api/v1/admin/pipelines/eod", json={"enabled": False})


def test_the_nightly_backup_and_its_retention(a):
    folder = a.state.settings.backups / "nightly"
    folder.mkdir(parents=True, exist_ok=True)
    stale = folder / "optlab-20000101-000000.sqlite"
    stale.write_bytes(b"old")
    month_ago = time.time() - 30 * 86400
    os.utime(stale, (month_ago, month_ago))
    admin = a.client("admin")
    r = admin.post("/api/v1/admin/pipelines/backup/run", headers={"Idempotency-Key": "backup-now"})
    assert r.status_code == 202
    again = admin.post("/api/v1/admin/pipelines/backup/run", headers={"Idempotency-Key": "backup-now"})
    assert again.json() == {"job_id": r.json()["job_id"], "reused": True}  # a retried click backs up once
    out = a.wait(admin, r.json()["job_id"])["result"]
    copy = folder / out["file"]
    assert copy.is_file() and out["bytes"] == copy.stat().st_size > 0 and out["removed"] == [stale.name]
    import sqlite3

    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:  # a database at the same revision, with the accounts in it
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == a.state.desk.db.revision()
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 1
    finally:
        con.close()
    system = admin.get("/api/v1/admin/system").json()
    assert system["backups"][0]["file"] == out["file"] and system["workers"][0]["alive"]


def test_a_due_schedule_fires_once_as_the_system(a):
    desk = a.state.desk
    sch = Schedules(desk, clock=lambda: time.time() + 8 * 86400)  # eight days later: every enabled one is due
    fired = sch.fire()
    assert set(fired) == {"backup", "housekeeping", "lake_backup"}  # the ones on by default
    assert sch.fire() == []  # moved to their next time: once only
    row = sch.get("housekeeping")
    j = wait_state(desk, row["last_job_id"])
    assert j["state"] == "done", j["error"]
    assert desk.jobs.owner(row["last_job_id"]) is None  # the system's job: admins see it, nobody else
    assert a.client("viewer").get(f"/api/v1/jobs/{row['last_job_id']}").status_code == 404
    assert a.client("admin").get(f"/api/v1/jobs/{row['last_job_id']}").status_code == 200
    wait_state(desk, sch.get("backup")["last_job_id"])
    copy = wait_state(desk, sch.get("lake_backup")["last_job_id"])  # this test's lake is empty or missing
    assert copy["state"] in ("done", "error") and (copy["state"] == "done" or "no lake" in copy["error"]["message"])
    listed = a.client("admin").get("/api/v1/admin/jobs", params={"kind": "housekeeping"}).json()["items"]
    assert listed[0]["schedule"] == "housekeeping" and listed[0]["user_id"] is None

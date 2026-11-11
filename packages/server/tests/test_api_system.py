"""API v1, system router: health, readiness, who am I, and jobs (state, owner, cancel, the SSE stream)."""

import json
import threading

import pytest
from _api import api


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-system")) as a:
        yield a


def test_health_and_ready_need_no_session(a):
    c = a.client()
    assert c.get("/api/v1/health").json() == {"ok": True}
    r = c.get("/api/v1/ready").json()
    assert r["ok"] and r["database"] and r["revision"] == a.state.desk.db.revision()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_me_lists_roles_and_permissions(a):
    viewer, risk = a.client("viewer"), a.client("risk", "quant")
    v = viewer.get("/api/v1/auth/me").json()
    assert v["roles"] == ["viewer"] and "get_trade" in v["permissions"] and "create_trade" not in v["permissions"]
    assert "health" in v["permissions"] and "list_users" not in v["permissions"]
    r = risk.get("/api/v1/auth/me").json()
    assert r["roles"] == ["quant", "risk"] and {"var", "vollab_fit", "put_scenarios"} <= set(r["permissions"])
    assert "create_quote" not in r["permissions"]


def _own_job(a, client, fn):
    job = a.state.desk.jobs.submit("test", fn)
    a.state.job_owner[job] = a.state.accounts.session(client.cookies.get("optlab_session"))[0].user_id
    return job


def test_a_job_and_its_error(a):
    c = a.client("viewer")
    ok = _own_job(a, c, lambda: {"answer": 42})
    assert a.wait(c, ok) == {"answer": 42}

    def boom():
        raise RuntimeError("no luck")

    bad = _own_job(a, c, boom)
    with pytest.raises(AssertionError):
        a.wait(c, bad)
    j = c.get(f"/api/v1/jobs/{bad}").json()
    assert j["state"] == "error" and j["error"] == {"type": "RuntimeError", "message": "no luck"}
    assert c.get("/api/v1/jobs/none-such").status_code == 404


def _events(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        e = dict(line.split(": ", 1) for line in block.splitlines())
        out.append({"id": int(e.get("id", 0)), "event": e["event"], "data": json.loads(e["data"])})
    return out


def test_the_event_stream_follows_a_job_and_resumes(a):
    c = a.client("viewer")
    gate = threading.Event()
    job = _own_job(a, c, lambda: gate.wait(30) and {"done": True})
    threading.Timer(0.6, gate.set).start()
    r = c.get(f"/api/v1/jobs/{job}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert [e["event"] for e in events][-1] == "done" and events[-1]["data"]["result"] == {"done": True}
    assert [e["id"] for e in events] == sorted({e["id"] for e in events}) and events[-1]["id"] == 3
    assert all(e["event"] == "state" and "result" not in e["data"] for e in events[:-1])
    last = events[-1]["id"]
    again = _events(c.get(f"/api/v1/jobs/{job}/events", headers={"Last-Event-ID": str(last - 1)}).text)
    assert [e["id"] for e in again] == [last]  # a reconnect gets only what it missed


def test_cancel_and_the_list_of_running_jobs(a):
    c = a.client("viewer")
    gate = threading.Event()
    jobs = [_own_job(a, c, lambda: gate.wait(30)) for _ in range(4)]  # more than the pool's threads: some queue
    running = c.get("/api/v1/jobs").json()["jobs"]
    assert {j["id"] for j in running} >= set(jobs) and all("result" not in j for j in running)
    queued = jobs[-1]
    assert c.post(f"/api/v1/jobs/{queued}/cancel").status_code == 200
    gate.set()
    assert c.get(f"/api/v1/jobs/{queued}").json()["state"] == "cancelled"
    assert any(e["action"] == "cancel_job" and e["target"] == queued for e in a.state.audit.entries(20))

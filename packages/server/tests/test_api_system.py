"""API v1, system router: health, readiness, who am I, and jobs (state, owner, cancel, the SSE stream)."""

import json

import pytest
from _api import SCRIPT_EXAMPLE, api
from _tasks import gate, submit_as, wait_state


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-system")) as a:
        yield a


def test_health_and_ready_need_no_session(a):
    c = a.client()
    assert c.get("/api/v1/health").json() == {"ok": True}
    r = c.get("/api/v1/ready").json()
    assert r["ok"] and r["database"] and r["worker"] and r["revision"] == a.state.desk.db.revision()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_me_lists_roles_and_permissions(a):
    viewer, risk = a.client("viewer"), a.client("risk", "quant")
    v = viewer.get("/api/v1/auth/me").json()
    assert v["roles"] == ["viewer"] and "get_trade" in v["permissions"] and "create_trade" not in v["permissions"]
    assert "health" in v["permissions"] and "list_users" not in v["permissions"]
    r = risk.get("/api/v1/auth/me").json()
    assert r["roles"] == ["quant", "risk"] and {"var", "vollab_fit", "put_scenarios"} <= set(r["permissions"])
    assert "create_quote" not in r["permissions"]


def _own_job(a, client, kind, params, key=None):
    return submit_as(a.state.desk, a.user_id(client), kind, params, key)


def test_a_job_its_envelope_and_its_error(a):
    c = a.client("viewer")
    ok = _own_job(a, c, "test_answer", {"answer": 42})
    assert a.wait(c, ok) == {"market": None, "method": {"name": "test"}, "result": {"answer": 42}}
    j = c.get(f"/api/v1/jobs/{ok}").json()
    assert j["progress"] == 1.0 and j["submitted_at"].endswith("+00:00") and j["version"] >= 3

    bad = _own_job(a, c, "test_boom", {"message": "no luck"})
    with pytest.raises(AssertionError):
        a.wait(c, bad)
    j = c.get(f"/api/v1/jobs/{bad}").json()
    assert j["state"] == "error" and j["error"] == {"type": "RuntimeError", "message": "no luck"}
    assert c.get("/api/v1/jobs/none-such").status_code == 404
    assert a.client("viewer").get(f"/api/v1/jobs/{ok}").status_code == 404  # another user's job does not exist
    assert a.client("admin").get(f"/api/v1/jobs/{ok}").status_code == 200


def test_monte_carlo_numbers_come_as_value_and_se(a):
    c = a.client("viewer")
    job = _own_job(a, c, "test_slow", {"steps": 2, "dt": 0.01})
    assert a.wait(c, job)["result"] == {"steps": 2, "price": {"value": 1.0, "se": 0.1}}


def _events(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        e = dict(line.split(": ", 1) for line in lines)
        out.append({"id": int(e.get("id", 0)), "event": e["event"], "data": json.loads(e["data"])})
    return out


def test_the_event_stream_shows_progress_and_resumes(a):
    c = a.client("viewer")
    job = _own_job(a, c, "test_slow", {"steps": 12, "dt": 0.12})
    r = c.get(f"/api/v1/jobs/{job}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    kinds = [e["event"] for e in events]
    assert kinds[-1] == "done" and events[-1]["data"]["result"]["result"]["price"] == {"value": 1.0, "se": 0.1}
    assert "progress" in kinds and kinds[0] == "state"
    fractions = [e["data"]["progress"] for e in events[:-1] if e["event"] == "progress"]
    assert fractions == sorted(fractions) and 0 < fractions[-1] < 1  # never backwards
    assert any(e["data"]["eta"] is not None for e in events if e["event"] == "progress")
    assert all("result" not in e["data"] for e in events[:-1])
    ids = [e["id"] for e in events]
    assert ids == sorted(set(ids))
    again = _events(c.get(f"/api/v1/jobs/{job}/events", headers={"Last-Event-ID": str(ids[-2])}).text)
    assert [e["id"] for e in again] == [ids[-1]]  # a reconnect gets only what it missed
    assert _events(c.get(f"/api/v1/jobs/{job}/events", headers={"Last-Event-ID": str(ids[-1])}).text) == []


def test_cancel_a_queued_and_a_running_job_and_the_list(a):
    c = a.client("viewer")
    gate("cancel-list").clear()
    blockers = [_own_job(a, c, "test_gate", {"gate": "cancel-list", "n": k}) for k in range(2)]
    slow = _own_job(a, c, "test_slow", {"steps": 400, "dt": 0.05})  # queued behind the two gates
    listed = c.get("/api/v1/jobs").json()["jobs"]
    assert {j["id"] for j in listed} >= {*blockers, slow} and all("result" not in j for j in listed)
    assert all(j["id"] not in {*blockers, slow} for j in a.client("viewer").get("/api/v1/jobs").json()["jobs"])
    gate("cancel-list").set()
    wait_state(a.state.desk, slow, ("running",))
    r = c.post(f"/api/v1/jobs/{slow}/cancel")
    assert r.status_code == 200 and r.json()["state"] in ("running", "cancelled")
    j = wait_state(a.state.desk, slow)
    assert j["state"] == "cancelled" and j["progress"] < 0.5  # stopped at a tick, long before its end
    q = _own_job(a, c, "test_gate", {"gate": "never-opened", "timeout": 30})
    q2 = _own_job(a, c, "test_gate", {"gate": "never-opened-2", "timeout": 30})
    q3 = _own_job(a, c, "test_answer", {"answer": "queued behind two gates"})
    assert c.post(f"/api/v1/jobs/{q3}/cancel").json()["state"] == "cancelled"
    assert c.get(f"/api/v1/jobs/{q3}").json()["state"] == "cancelled"
    gate("never-opened").set()
    gate("never-opened-2").set()
    wait_state(a.state.desk, q)
    wait_state(a.state.desk, q2)
    assert any(e["action"] == "cancel_job" and e["target"] == q3 for e in a.state.audit.entries(20))


def test_submission_is_idempotent(a):
    c = a.client("viewer")
    gate("idem").clear()
    first = _own_job(a, c, "test_gate", {"gate": "idem"})
    assert _own_job(a, c, "test_gate", {"gate": "idem"}) == first  # the same request still running: given back
    assert _own_job(a, a.client("viewer"), "test_gate", {"gate": "idem"}) != first  # not another user's
    gate("idem").set()
    wait_state(a.state.desk, first)
    assert _own_job(a, c, "test_gate", {"gate": "idem"}) != first  # done: asking again computes again
    keyed = _own_job(a, c, "test_answer", {"answer": 1}, key="k-1")
    assert _own_job(a, c, "test_answer", {"answer": 1}, key="k-1") == keyed  # the same key, even once done
    from optlab.server.jobs import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        _own_job(a, c, "test_answer", {"answer": 2}, key="k-1")


def test_idempotency_key_on_a_route(a):
    s = a.client("structurer")
    body = {"script": SCRIPT_EXAMPLE, "model": "bs", "n_paths": 2000}
    r1 = s.post("/api/v1/structuring/script/price", json=body, headers={"Idempotency-Key": "quote-1"})
    r2 = s.post("/api/v1/structuring/script/price", json=body, headers={"Idempotency-Key": "quote-1"})
    assert r1.status_code == r2.status_code == 202 and r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.json()["reused"] is True
    other = s.post(
        "/api/v1/structuring/script/price", json={**body, "n_paths": 4000}, headers={"Idempotency-Key": "quote-1"}
    )
    assert other.status_code == 422 and "Idempotency-Key" in other.json()["detail"]
    bad = s.post("/api/v1/structuring/script/price", json=body, headers={"Idempotency-Key": "a b"})
    assert bad.status_code == 422
    a.wait(s, r1.json()["job_id"])


def test_the_app_leaves_the_process_engine_as_it_found_it(tmp_path):
    """The Desk sets the process-wide engine; the bit-for-bit tests of the other packages run after these ones."""
    from optlab.core import engines

    before = engines.default_engine()
    with api(tmp_path, engine="cpu"):
        assert engines.default_engine() == "cpu"
    assert engines.default_engine() == before

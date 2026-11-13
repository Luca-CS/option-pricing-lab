"""The job queue on its own (ADR-0006): a row's life (submit, claim, progress, finish), its version, the sweep of a
dead worker's jobs, the worker lease, idempotent submission, the prune; a fake clock, no worker."""

import pytest

from optlab.server.jobs import (
    INTERRUPTED,
    STALE_SECONDS,
    IdempotencyConflict,
    JobQueue,
    Workers,
    fingerprint,
    submitting,
)
from optlab.server.persistence import Database
from optlab.server.present import estimates


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def q(tmp_path):
    db = Database(tmp_path / "jobs.sqlite")
    clock = Clock()
    yield JobQueue(db, clock), Workers(db, clock), clock
    db.close()


def test_a_job_from_submission_to_its_result(q):
    queue, _, clock = q
    with submitting("u1"):
        job, reused = queue.submit("quote", {"n": 1}, {"snapshot_id": "S"})
    assert not reused and queue.owner(job) == "u1" and queue.get(job)["state"] == "queued"
    v0 = queue.get(job)["version"]
    assert queue.claim("w1", "numpy") == job and queue.claim("w1") is None
    clock.t += 2
    assert queue.report(job, 0.5, "pricing", 2.0) is False
    j = queue.get(job)
    assert j["state"] == "running" and j["progress"] == 0.5 and j["step"] == "pricing" and j["eta"] == 2.0
    assert j["elapsed"] == pytest.approx(2.0) and j["engine"] == "numpy" and j["version"] > v0
    clock.t += 1
    queue.finish(job, "done", result={"market": None, "method": {"name": "x"}, "result": {"price": 1.0}})
    j = queue.get(job)
    assert j["state"] == "done" and j["progress"] == 1.0 and j["eta"] is None and j["elapsed"] == pytest.approx(3.0)
    assert j["result"]["result"] == {"price": 1.0} and queue.payload(job) == ("quote", {"n": 1}, {"snapshot_id": "S"})
    queue.finish(job, "error", error={"type": "X", "message": "late"})  # a finished job never changes again
    assert queue.get(job)["state"] == "done"


def test_cancel_queued_now_running_at_its_next_step(q):
    queue, _, _ = q
    a, _ = queue.submit("x", {"a": 1}, {})
    b, _ = queue.submit("x", {"b": 1}, {})
    assert queue.cancel(a) == "cancelled" and queue.get(a)["state"] == "cancelled"
    assert queue.claim("w") == b  # the cancelled one is never claimed
    assert queue.cancel(b) == "running" and queue.get(b)["cancel_requested"]
    assert queue.report(b, 0.1, None, None) is True  # the worker learns it at its next write
    with pytest.raises(KeyError):
        queue.cancel("none")


def test_a_dead_workers_jobs_end_interrupted_and_queued_ones_wait(q):
    queue, workers, clock = q
    assert workers.register("w1", "host", 1, "embedded", "numpy", 2)
    running, _ = queue.submit("x", {"r": 1}, {})
    waiting, _ = queue.submit("x", {"w": 1}, {})
    queue.claim("w1")
    assert not workers.register("w2", "host", 2, "external", "gpu", 2)  # w1 holds the lease
    clock.t += STALE_SECONDS + 1  # w1 died without a word
    assert workers.register("w2", "host", 2, "external", "gpu", 2)
    assert queue.sweep() == [running]
    j = queue.get(running)
    assert j["state"] == "error" and j["error"] == INTERRUPTED
    assert queue.get(waiting)["state"] == "queued" and queue.claim("w2") == waiting  # a restart forgets nothing
    assert [w["worker_id"] for w in workers.alive()] == ["w2"]
    workers.stop("w2")
    assert workers.alive() == []


def test_submission_is_idempotent_by_key_and_by_fingerprint(q):
    queue, _, _ = q
    with submitting("u1"):
        a, _ = queue.submit("x", {"p": 1}, {"snapshot_id": "S"})
        assert queue.submit("x", {"p": 1}, {"snapshot_id": "S"}) == (a, True)  # still queued: the same job
        b, reused = queue.submit("x", {"p": 1}, {"snapshot_id": "T"})  # another market: another job
        assert b != a and not reused
    with submitting("u2"):
        assert queue.submit("x", {"p": 1}, {"snapshot_id": "S"})[0] != a  # another user: another job
    queue.claim("w")
    queue.finish(a, "done", result={})
    with submitting("u1"):
        assert queue.submit("x", {"p": 1}, {"snapshot_id": "S"})[0] != a  # done: asked again, computed again
    with submitting("u1", "key-1"):
        k, _ = queue.submit("x", {"p": 2}, {})
        assert queue.submit("x", {"p": 2}, {}) == (k, True)
        with pytest.raises(IdempotencyConflict):
            queue.submit("x", {"p": 3}, {})
    with submitting("u2", "key-1"):  # a key is the user's own
        assert queue.submit("x", {"p": 3}, {})[0] != k
    assert fingerprint("x", {"a": 1, "b": 2}, {}) == fingerprint("x", {"b": 2, "a": 1}, {})


def test_prune_keeps_the_recent_and_the_unfinished(q):
    queue, _, clock = q
    old = [queue.submit("x", {"i": i}, {})[0] for i in range(5)]
    for j in old[:4]:
        queue.claim("w")
        queue.finish(j, "done", result={})
    clock.t += 40 * 86400
    new, _ = queue.submit("x", {"i": "new"}, {})
    assert queue.prune(30, keep=3) == 3  # the newest three jobs stay whatever their age: old[3] among them
    assert queue.get(old[3])["state"] == "done"
    assert queue.get(old[4])["state"] == "queued" and queue.get(new)["state"] == "queued"
    with pytest.raises(KeyError):
        queue.get(old[0])


def test_monte_carlo_numbers_as_value_and_se():
    assert estimates({"price": 1.0, "stderr": 0.1, "method": "mc"}) == {
        "price": {"value": 1.0, "se": 0.1},
        "method": "mc",
    }
    assert estimates({"unit_price": 98.0, "unit_stderr": 0.2, "value": 5.0, "stderr": 1.0}) == {
        "unit_price": {"value": 98.0, "se": 0.2},
        "value": {"value": 5.0, "se": 1.0},
    }
    g = estimates({"delta": {"SPX": 0.5}, "delta_se": {"SPX": 0.01}, "vega": None, "vega_se": None, "label": "x"})
    assert g == {"delta": {"SPX": {"value": 0.5, "se": 0.01}}, "vega": None, "label": "x"}
    assert estimates({"positions": [1.0, 2.0], "positions_se": [0.1, 0.2]}) == {
        "positions": [{"value": 1.0, "se": 0.1}, {"value": 2.0, "se": 0.2}]
    }
    risk = estimates({"book": {"delta|SPX|": [3.0, 0.5]}, "grid": {"tenors": [0.5, 1.0]}, "label": "a"}, risk=True)
    assert risk == {"book": {"delta|SPX|": {"value": 3.0, "se": 0.5}}, "grid": {"tenors": [0.5, 1.0]}, "label": "a"}
    assert estimates({"book": {"delta|SPX|": [3.0, 0.5]}}) == {"book": {"delta|SPX|": [3.0, 0.5]}}  # rule 3: risk only
    assert estimates({"name": "x", "name_se": 1.0}) == {"name": "x", "name_se": 1.0}  # a word has no error
    desk = {"base_price": 98.0, "base_se": 0.1, "lines": [{"key": "vega", "cost": 0.3, "se": 0.05}]}
    assert estimates(desk) == {
        "base_price": {"value": 98.0, "se": 0.1},
        "lines": [{"key": "vega", "cost": {"value": 0.3, "se": 0.05}}],
    }

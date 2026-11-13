"""Job kinds for the tests of the queue, registered in this process (the embedded worker runs them): an answer, an
error, a job that waits for a gate, a job that ticks its progress step by step."""

from __future__ import annotations

import threading
import time

from optlab.core import progress
from optlab.server.jobs import submitting
from optlab.server.tasks import envelope, register

GATES: dict[str, threading.Event] = {}


def gate(name: str) -> threading.Event:
    return GATES.setdefault(name, threading.Event())


def _answer(desk, p, f):
    return envelope(None, "test", {"answer": p["answer"]})


def _boom(desk, p, f):
    raise RuntimeError(p.get("message", "no luck"))


def _gate(desk, p, f):
    return envelope(None, "test", {"done": gate(p["gate"]).wait(p.get("timeout", 30))})


def _slow(desk, p, f):
    n = int(p["steps"])
    progress.step("counting", 0, 1)
    for i in range(n):
        time.sleep(float(p.get("dt", 0.05)))
        progress.tick(i + 1, n, "steps")
    return envelope(None, "test", {"steps": n, "price": 1.0, "stderr": 0.1})


register("test_answer", _answer)
register("test_boom", _boom)
register("test_gate", _gate)
register("test_slow", _slow)


def submit_as(desk, user_id: str | None, kind: str, params: dict, key: str | None = None) -> str:
    with submitting(user_id, key):
        return desk._submit(kind, params)["job"]


def wait_state(desk, job_id: str, states=("done", "error", "cancelled"), timeout: float = 30.0) -> dict:
    t0 = time.perf_counter()
    while (j := desk.jobs.get(job_id))["state"] not in states:
        assert time.perf_counter() - t0 < timeout, f"job {job_id} stayed {j['state']}"
        time.sleep(0.02)
    return j

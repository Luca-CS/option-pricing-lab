"""The worker as its own process (ADR-0006): an api that leaves the queue to
`optlab worker`, a job done by that process, and a worker killed in the middle of a job (a crash): the
next worker waits for the dead one's lease to expire, marks the job interrupted, and serves the queue again."""

import os
import subprocess
import sys
import time
from typing import Any

import pytest
from _api import SCRIPT_EXAMPLE, api

from optlab.server.jobs import INTERRUPTED, Workers


def _worker(folder):
    env = {
        **os.environ,
        "OPTLAB_DATA": str(folder),
        "OPTLAB_LAKE": str(folder / "lake"),
        "OPTLAB_ENGINE": "numpy",
        "OPTLAB_WORKER": "external",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "optlab.server.cli", "worker", "--concurrency", "1", "--wait-lease", "60"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _until(check, timeout, what):
    t0 = time.monotonic()
    while not (got := check()):
        assert time.monotonic() - t0 < timeout, f"{what} after {timeout} s"
        time.sleep(0.2)
    return got


def test_an_external_worker_its_crash_and_the_next_one(tmp_path):
    with api(tmp_path, worker="external") as a:
        s = a.client("structurer")
        desk = a.state.desk
        assert desk.worker is None and s.get("/api/v1/ready").status_code == 503  # nobody serves the queue
        quick: dict[str, Any] = {"script": SCRIPT_EXAMPLE, "model": "bs", "n_paths": 4000}
        first = s.post("/api/v1/structuring/script/price", json=quick).json()["job_id"]
        time.sleep(1.0)
        assert s.get(f"/api/v1/jobs/{first}").json()["state"] == "queued"  # it waits for a worker

        one = _worker(tmp_path)
        try:
            _until(lambda: s.get("/api/v1/ready").status_code == 200, 90, "no worker")
            res = a.wait(s, first, timeout=120)
            assert 50 < res["result"]["price"]["value"] < 130 and res["method"]["model"] == "bs"
            (w,) = Workers(desk.db).alive()  # its pid is the interpreter's (the venv's python.exe is a launcher)
            assert w["role"] == "external" and w["engine"] == "numpy" and w["concurrency"] == 1

            long = {"script": SCRIPT_EXAMPLE, "model": "lv", "n_paths": 500_000}
            victim = s.post("/api/v1/structuring/script/price", json=long).json()["job_id"]
            _until(lambda: s.get(f"/api/v1/jobs/{victim}").json()["state"] == "running", 60, "never ran")
        finally:
            one.kill()  # a crash: no time to release the lease or to finish the job
            one.wait(30)
        assert s.get(f"/api/v1/jobs/{victim}").json()["state"] == "running"  # nobody says otherwise yet
        queued = s.post("/api/v1/structuring/script/price", json={**quick, "n_paths": 6000}).json()["job_id"]

        two = _worker(tmp_path)
        try:
            j = _until(
                lambda: (x := s.get(f"/api/v1/jobs/{victim}").json())["state"] != "running" and x, 90, "not swept"
            )
            assert j["state"] == "error" and j["error"] == INTERRUPTED  # never resumed: it is run again
            assert a.wait(s, queued, timeout=120)["result"]["price"]["se"] > 0  # a restart forgets nothing
        finally:
            two.kill()
            two.wait(30)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send: terminate() kills the process")
def test_docker_stop_s_sigterm_is_a_clean_stop(tmp_path):
    """`docker stop` sends SIGTERM: the worker stops as on Ctrl+C, so its running job ends interrupted at once and its
    lease is free for the next process (a restoration right after a deploy's stop needs it), not 15 s later."""
    import signal

    with api(tmp_path, worker="external") as a:
        s = a.client("structurer")
        desk = a.state.desk
        one = _worker(tmp_path)
        try:
            _until(lambda: Workers(desk.db).alive(), 90, "no worker")
            long = {"script": SCRIPT_EXAMPLE, "model": "lv", "n_paths": 500_000}
            job = s.post("/api/v1/structuring/script/price", json=long).json()["job_id"]
            _until(lambda: s.get(f"/api/v1/jobs/{job}").json()["state"] == "running", 60, "never ran")
            one.send_signal(signal.SIGTERM)
            assert one.wait(30) == 0
        finally:
            if one.poll() is None:
                one.kill()
                one.wait(30)
        assert Workers(desk.db).alive() == []  # released, not left to expire
        j = s.get(f"/api/v1/jobs/{job}").json()
        assert j["state"] == "error" and j["error"] == INTERRUPTED


def test_an_api_whose_lease_is_taken_serves_the_queue_once_it_frees(tmp_path, monkeypatch):
    """An api restarted right after a kill finds the dead process's lease still fresh. It waits for that lease in the
    background and serves the queue once it frees (it used to leave the queue to nobody until its next restart)."""
    import optlab.server.api as desk_module
    from optlab.server.api import Desk

    monkeypatch.setattr(desk_module, "LEASE_RETRY_SECONDS", 0.05)
    with api(tmp_path) as a:
        first = a.state.desk
        assert first.worker is not None
        second = Desk(settings=first.settings, engine="numpy")
        try:
            assert second.worker is None  # the first one serves this database
            first.worker.stop()  # its lease released
            _until(lambda: second.worker is not None, 30, "the second api never took the queue over")
            assert [w["role"] for w in Workers(second.db).alive()] == ["embedded"]
        finally:
            second.close()
        assert Workers(first.db).alive() == []

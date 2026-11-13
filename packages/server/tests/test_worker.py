"""The worker as its own process (handoff §4.3, ADR-0006): an api that leaves the queue to
`python -m optlab.server.worker`, a job done by that process, and a worker killed in the middle of a job (a crash): the
next worker waits for the dead one's lease to expire, marks the job interrupted, and serves the queue again."""

import os
import subprocess
import sys
import time

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
        [sys.executable, "-m", "optlab.server.worker", "--concurrency", "1", "--wait-lease", "60"],
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
        quick = {"script": SCRIPT_EXAMPLE, "model": "bs", "n_paths": 4000}
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

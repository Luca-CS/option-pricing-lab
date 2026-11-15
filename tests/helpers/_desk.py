"""Helpers of the tests that drive a Desk directly: a job's result once done, a term sheet as the services' body."""

import time


def wait(desk, job_id: str, timeout: float = 300.0, poll: float = 0.05):
    """The result of a job once it is done; an assertion error if it fails or outlasts `timeout` seconds."""
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout, f"job {job_id} did not finish"
        time.sleep(poll)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def sheet_body(sheet) -> dict:
    """A term sheet as the body the services take: {"type": ..., "values": {...}}."""
    d = sheet.to_dict(json_safe=True)
    t = d.pop("type")
    return {"type": t, "values": d}

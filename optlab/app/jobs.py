"""Background jobs: a quote or a book run takes seconds, so the API answers with a job id and the page polls it."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..log import get_logger

LOG = get_logger(__name__)


class Jobs:
    def __init__(self, max_workers: int = 2, keep: int = 200):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="optlab-job")
        self._jobs: dict = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, fn, *args, **kwargs) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "kind": kind, "state": "queued", "created": time.time(), "started": None,
               "finished": None, "result": None, "error": None, "cancelled": False}
        with self._lock:
            self._jobs[job_id] = job
            if len(self._jobs) > self._keep:
                for old in sorted(self._jobs.values(), key=lambda j: j["created"])[: len(self._jobs) - self._keep]:
                    if old["state"] in ("done", "error", "cancelled"):
                        self._jobs.pop(old["id"], None)

        def run():
            if job["cancelled"]:
                return
            job["state"], job["started"] = "running", time.time()
            try:
                result = fn(*args, **kwargs)
                if job["cancelled"]:
                    job["state"] = "cancelled"
                else:
                    job["result"], job["state"] = result, "done"
            except Exception as exc:  # reported to the page, with the message the library gives
                job["error"] = {"type": type(exc).__name__, "message": str(exc)}
                job["state"] = "error"
                LOG.warning("job %s (%s) failed: %s\n%s", job_id, kind, exc, traceback.format_exc())
            finally:
                job["finished"] = time.time()

        self._pool.submit(run)
        return job_id

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"no job {job_id!r}")
        now = time.time()
        elapsed = (job["finished"] or now) - (job["started"] or now) if job["started"] else 0.0
        return {k: job[k] for k in ("id", "kind", "state", "result", "error")} | {"elapsed": round(elapsed, 2)}

    def cancel(self, job_id: str) -> None:
        """A running computation cannot be interrupted; its result is dropped when it ends."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"no job {job_id!r}")
        job["cancelled"] = True
        if job["state"] == "queued":
            job["state"] = "cancelled"

    def running(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [{"id": j["id"], "kind": j["kind"], "state": j["state"]} for j in jobs
                if j["state"] in ("queued", "running")]

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

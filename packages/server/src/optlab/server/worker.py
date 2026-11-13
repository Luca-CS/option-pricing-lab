"""The worker (ADR-0006): claims the queue's jobs, runs them, writes their progress and results; runs the schedules.

    python -m optlab.server.worker [--concurrency 2] [--engine auto] [--workers N] [--store FILE]

One worker serves a database (it holds a lease, renewed every few seconds), so one process owns the GPU. By default
the api runs it in its own process (`Settings.worker = "embedded"`); `OPTLAB_WORKER=external` leaves the queue to this
command (the `worker` container of A7). When a worker starts it takes the lease and marks the jobs a dead worker left
`running` as interrupted: a computation is never resumed, it is run again.

A job runs in a thread of this process with a reporter (`optlab.core.progress`): the library's loops tick it, the desk
names its steps, and it writes the fraction done, the step and an ETA to the row at most twice a second. The same write
reads the cancel flag, so a cancelled job stops at its next batch.
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from optlab.core import engines, progress
from optlab.core.log import get_logger
from optlab.server import tasks
from optlab.server.jobs import Workers
from optlab.server.serialize import clean

LOG = get_logger("optlab.server.worker")  # not __name__: run as -m, it is __main__
BEAT_SECONDS = 5.0
SCHEDULE_SECONDS = 20.0
GPU_SECONDS = 60.0


class Reporter:
    """Progress of one job: steps named by the desk, ticks from the library, written to the row now and then."""

    def __init__(
        self, queue, job_id: str, stopping: threading.Event | None = None, every: float = 0.5, clock=time.monotonic
    ):
        self.queue, self.job_id, self.every, self.clock = queue, job_id, every, clock
        self.stopping = stopping or threading.Event()
        self.start = clock()
        self.name: str | None = None
        self.index, self.count = 0, 1
        self.fraction = 0.0
        self._written = -1e9
        self.cancelled = False  # someone asked this job to stop
        self.stopped = False  # the worker is stopping: the job is interrupted, not cancelled

    def step(self, name: str, index: int, count: int) -> None:
        self.name, self.index, self.count = name, max(int(index), 0), max(int(count), 1)
        self._advance(self.index / self.count)
        self._write(force=True)

    def tick(self, done: int, total: int, unit: str | None) -> None:
        within = min(max(done / total, 0.0), 1.0) if total > 0 else 0.0
        self._advance(min((self.index + within) / self.count, (self.index + 1) / self.count))
        self._write()

    def _advance(self, fraction: float) -> None:
        self.fraction = max(self.fraction, min(fraction, 1.0))  # never backwards: a step may run several loops

    def _write(self, force: bool = False) -> None:
        if self.stopping.is_set():
            self.stopped = True
            raise progress.Cancelled(f"job {self.job_id} interrupted: the worker is stopping")
        now = self.clock()
        if not force and now - self._written < self.every:
            return
        self._written = now
        elapsed = now - self.start
        eta = elapsed * (1 - self.fraction) / self.fraction if self.fraction >= 0.02 else None
        if self.queue.report(self.job_id, round(self.fraction, 4), self.name, None if eta is None else round(eta, 1)):
            self.cancelled = True
            raise progress.Cancelled(f"job {self.job_id} cancelled")


def nvidia_smi() -> Path | None:
    """nvidia-smi where the driver installs it, never from the current folder or the PATH (where Windows would look in
    the working directory first, so a planted nvidia-smi.exe there would run)."""
    if os.name == "nt":
        root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        places = [root / "System32" / "nvidia-smi.exe"]
        places.append(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
        )
    else:
        places = [Path("/usr/bin/nvidia-smi"), Path("/usr/local/bin/nvidia-smi")]
    return next((p for p in places if p.is_file()), None)


def gpu_status() -> list[dict] | None:
    """The GPUs as nvidia-smi reports them (fixed arguments, no shell); None without nvidia-smi."""
    exe = nvidia_smi()
    if exe is None:
        return None
    fields = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,clocks.sm"
    try:
        out = subprocess.run(  # noqa: S603 (the driver's own program by its full path, fixed arguments, no shell)
            [str(exe), f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except OSError, subprocess.SubprocessError:
        return None
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            continue
        name, util, used, total, temp, clock = parts

        def num(x):
            try:
                return float(x)
            except ValueError:
                return None

        rows.append(
            {
                "name": name,
                "utilization": num(util),
                "memory_used_mb": num(used),
                "memory_total_mb": num(total),
                "temperature_c": num(temp),
                "sm_clock_mhz": num(clock),
            }
        )
    return rows


def engine_summary() -> dict | None:
    try:
        from optlab import native

        return native.summary()
    except Exception as exc:  # the native module is optional: the worker still runs on NumPy
        LOG.info("no native engine summary: %s", exc)
        return None


class Worker:
    """Runs a desk's queue: `start()` takes the lease and starts the loop in a thread, `stop()` ends it."""

    def __init__(
        self, desk, role: str = "external", concurrency: int | None = None, poll: float = 0.25, schedules: bool = True
    ):
        from optlab.server.schedules import Schedules

        self.desk, self.queue, self.role, self.poll = desk, desk.jobs, role, poll
        self.concurrency = int(concurrency or desk.settings.job_concurrency)
        self.leases = Workers(desk.db)
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
        self.schedules = Schedules(desk) if schedules else None
        self._pool = ThreadPoolExecutor(self.concurrency, thread_name_prefix="optlab-job")
        self._running: dict[str, Reporter] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        engine = engines.default_engine()
        if not self.leases.register(
            self.worker_id, socket.gethostname(), os.getpid(), self.role, engine, self.concurrency
        ):
            LOG.warning("another worker serves %s: this process leaves the queue to it", self.desk.db.path)
            return False
        swept = self.queue.sweep()
        if swept:
            LOG.warning("%d job(s) interrupted by the last stop: %s", len(swept), ", ".join(swept))
        if self.schedules is not None:
            self.schedules.ensure_defaults()
        self._thread = threading.Thread(target=self._loop, name="optlab-worker", daemon=True)
        self._thread.start()
        LOG.info("worker %s (%s, %d at a time, engine %s)", self.worker_id, self.role, self.concurrency, engine)
        return True

    def _info(self, with_gpu: bool) -> dict:
        info: dict = {"engine": engine_summary()}
        if with_gpu:
            info["gpu"] = gpu_status()
        return info

    def _loop(self) -> None:
        last_beat = last_schedule = last_gpu = -1e9
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now - last_beat >= BEAT_SECONDS:
                    gpu = now - last_gpu >= GPU_SECONDS
                    self.leases.beat(self.worker_id, self._info(gpu) if gpu else None)
                    last_beat, last_gpu = now, now if gpu else last_gpu
                if self.schedules is not None and now - last_schedule >= SCHEDULE_SECONDS:
                    self.schedules.fire()
                    last_schedule = now
                with self._lock:
                    free = len(self._running) < self.concurrency
                if free:
                    job_id = self.queue.claim(self.worker_id, engines.default_engine())
                    if job_id is not None:
                        reporter = Reporter(self.queue, job_id, self._stop)
                        with self._lock:
                            self._running[job_id] = reporter
                        self._pool.submit(self._run, job_id, reporter)
                        continue
            except Exception:  # the database busy beyond its timeout, a schedule that fails: log it, keep serving
                LOG.exception("worker loop")
            self._stop.wait(self.poll)

    def _run(self, job_id: str, reporter: Reporter) -> None:
        try:
            kind, params, context = self.queue.payload(job_id)
            with progress.reporting(reporter):
                out = tasks.run(self.desk, kind, params, context)
            self.queue.finish(job_id, "done", result=clean(out))
        except progress.Cancelled:
            if reporter.stopped:
                self.queue.interrupt([job_id])
            else:
                self.queue.finish(job_id, "cancelled")
        except Exception as exc:  # reported to the client with the library's message; the trace stays in the log
            LOG.warning("job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
            message = str(exc).strip("'\"") if isinstance(exc, KeyError) else str(exc)
            self.queue.finish(job_id, "error", error={"type": type(exc).__name__, "message": message})
        finally:
            with self._lock:
                self._running.pop(job_id, None)

    def running(self) -> list[str]:
        with self._lock:
            return list(self._running)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop claiming; the running jobs stop at their next batch and end interrupted (not cancelled: nobody asked);
        wait up to `timeout`, mark what is still computing interrupted too, release the lease."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        deadline = time.monotonic() + timeout
        while self.running() and time.monotonic() < deadline:
            time.sleep(0.05)
        left = self.running()  # still computing: their results would come too late
        if left:
            self.queue.interrupt(left)
        self._pool.shutdown(wait=False, cancel_futures=True)
        try:
            self.leases.stop(self.worker_id)
        except Exception:  # the database may already be closed in a test's teardown
            LOG.debug("lease not released", exc_info=True)


def main(argv=None) -> None:
    from optlab.core.log import configure
    from optlab.server.api import Desk
    from optlab.server.logs import to_files
    from optlab.server.settings import Settings

    ap = argparse.ArgumentParser(prog="python -m optlab.server.worker", description=__doc__)
    ap.add_argument("--concurrency", type=int, default=None, help="jobs at a time (default: the settings', 2)")
    ap.add_argument("--engine", default=None, choices=("auto", "gpu", "cpu", "numpy"))
    ap.add_argument("--workers", type=int, default=None, help="process pool for Monte Carlo batches on NumPy")
    ap.add_argument("--store", help="the database file (default: the settings')")
    ap.add_argument(
        "--wait-lease",
        type=float,
        default=30.0,
        help="seconds to wait for the lease of a worker that stopped without releasing it (a crash: its lease expires "
        "after 15 s of silence)",
    )
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    configure(args.log)
    settings = Settings.load(
        engine=args.engine, workers=args.workers, database=args.store, job_concurrency=args.concurrency
    )
    to_files(settings, "worker", args.log)
    desk = Desk(settings=settings, engine=settings.engine, workers=settings.workers, run_jobs=False)
    desk.shared = settings.lan  # the same rule as the api's for raw quotes and levels (none of its jobs reads it today)
    worker = Worker(desk, role="external", concurrency=settings.job_concurrency)
    deadline = time.monotonic() + args.wait_lease
    while not worker.start():
        if time.monotonic() > deadline:
            desk.close()
            raise SystemExit("another worker serves this database: stop it first")
        print("another worker holds the lease: waiting for it to expire", flush=True)
        time.sleep(2)
    print(f"worker {worker.worker_id} on {desk.db.path}: {worker.concurrency} job(s) at a time, Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("stopping: running jobs are asked to stop")
    finally:
        worker.stop()
        desk.close()


if __name__ == "__main__":
    main()

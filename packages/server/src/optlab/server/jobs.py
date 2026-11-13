"""The job queue (ADR-0006): a table of the operational database, written by the api and claimed by the worker.

A route validates its request, freezes what the desk holds (the market in use, the typed settings, the trades and their
fixings: `optlab.server.tasks.Frozen`) and writes one row; the worker claims rows one at a time, runs them and writes
their progress and their result. Everything is in the row, so a restart forgets nothing: queued jobs run when a worker
comes back, and a job a crash interrupted is marked `error` (Interrupted) when the next worker starts, never resumed.

Parameters, context and results are JSON; nothing is ever unpickled from the table. Who submits comes from
`submitting(...)`, set by the router around the desk's call: the owner is in the row from the first write.

Submission is idempotent: the same `Idempotency-Key` from the same user gives back the job it first created (a
different request under that key is refused), and without a key an identical job of the same user that is still queued
or running is given back instead of computed twice. "Identical" is the fingerprint: the hash of the kind, the
parameters and the frozen context, so the same request on another market or another book is another job.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, insert, literal_column, select, update

from optlab.server.persistence.database import Database
from optlab.server.persistence.schema import jobs, workers

STATES = ("queued", "running", "done", "error", "cancelled")
FINAL = ("done", "error", "cancelled")
ACTIVE = ("queued", "running")
INTERRUPTED = {
    "type": "Interrupted",
    "message": "the worker stopped while this job ran (a restart or a crash): run it again",
}
STALE_SECONDS = 15.0  # a worker that has not beaten for this long is gone
ROWID = literal_column("jobs.rowid")  # the order of submission when two share a clock tick (15 ms on Windows)


class IdempotencyConflict(ValueError):
    """An Idempotency-Key used again for a different request."""


@dataclass(frozen=True)
class Submitter:
    user_id: str | None = None
    idem_key: str | None = None
    schedule: str | None = None


_SUBMITTER: ContextVar[Submitter | None] = ContextVar("optlab_submitter", default=None)


@contextmanager
def submitting(user_id: str | None, idem_key: str | None = None, schedule: str | None = None) -> Iterator[None]:
    """Jobs submitted inside belong to this user (None: the system) and carry this key."""
    token = _SUBMITTER.set(Submitter(user_id, idem_key, schedule))
    try:
        yield
    finally:
        _SUBMITTER.reset(token)


def canonical(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(kind: str, params: dict, context: dict) -> str:
    return hashlib.sha256(canonical({"kind": kind, "params": params, "context": context}).encode()).hexdigest()


def _loads(text: str | None):
    return None if text is None else json.loads(text)


def iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds")


def view(row, now: float | None = None) -> dict:
    """A job as the API shows it: no parameters, no context; times in ISO 8601, durations in seconds."""
    now = time.time() if now is None else now
    started, finished = row.started_at, row.finished_at
    elapsed = ((finished or now) - started) if started else 0.0
    return {
        "id": row.job_id,
        "kind": row.kind,
        "state": row.state,
        "progress": row.progress,
        "step": row.step,
        "eta": None if row.state != "running" else row.eta,
        "elapsed": round(max(elapsed, 0.0), 2),
        "submitted_at": iso(row.submitted_at),
        "started_at": iso(started),
        "finished_at": iso(finished),
        "engine": row.engine,
        "version": row.version,
        "cancel_requested": bool(row.cancel_requested),
        "result": _loads(row.result),
        "error": _loads(row.error),
    }


class JobQueue:
    """The table seen by the api (submit, read, cancel) and by the worker (claim, report, finish, sweep)."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock

    # --- the api ---------------------------------------------------------------------------------------------------
    def submit(self, kind: str, params: dict, context: dict) -> tuple[str, bool]:
        """(job id, whether an existing job was given back)."""
        sub = _SUBMITTER.get() or Submitter()
        fp = fingerprint(kind, params, context)
        owner = jobs.c.user_id.is_not_distinct_from(sub.user_id)
        with self.db.write() as c:  # BEGIN IMMEDIATE: two identical submissions cannot both insert
            if sub.idem_key is not None:
                row = c.execute(
                    select(jobs.c.job_id, jobs.c.fingerprint).where(owner, jobs.c.idem_key == sub.idem_key)
                ).first()
                if row is not None:
                    if row.fingerprint != fp:
                        raise IdempotencyConflict(
                            "this Idempotency-Key was already used for another request: use a new key"
                        )
                    return row.job_id, True
            else:
                same = c.execute(
                    select(jobs.c.job_id)
                    .where(owner, jobs.c.fingerprint == fp, jobs.c.state.in_(ACTIVE))
                    .order_by(jobs.c.submitted_at, ROWID)
                    .limit(1)
                ).scalar()
                if same is not None:
                    return same, True
            job_id = secrets.token_hex(8)
            c.execute(
                insert(jobs).values(
                    job_id=job_id,
                    kind=kind,
                    state="queued",
                    user_id=sub.user_id,
                    schedule=sub.schedule,
                    params=canonical(params),
                    context=canonical(context),
                    fingerprint=fp,
                    idem_key=sub.idem_key,
                    submitted_at=self.clock(),
                    version=1,
                )
            )
        return job_id, False

    def _row(self, job_id: str):
        with self.db.read() as c:
            row = c.execute(select(jobs).where(jobs.c.job_id == job_id)).first()
        if row is None:
            raise KeyError(f"no job {job_id!r}")
        return row

    def get(self, job_id: str) -> dict:
        return view(self._row(job_id), self.clock())

    def owner(self, job_id: str) -> str | None:
        return self._row(job_id).user_id

    def payload(self, job_id: str) -> tuple[str, dict, dict]:
        """(kind, params, context) of a job, for the worker."""
        row = self._row(job_id)
        return row.kind, json.loads(row.params), json.loads(row.context)

    def active(self, user_id: str | None = None, everyone: bool = False) -> list[dict]:
        q = select(jobs.c.job_id, jobs.c.kind, jobs.c.state, jobs.c.progress, jobs.c.step).where(
            jobs.c.state.in_(ACTIVE)
        )
        if not everyone:
            q = q.where(jobs.c.user_id.is_not_distinct_from(user_id))
        with self.db.read() as c:
            rows = c.execute(q.order_by(jobs.c.submitted_at, ROWID)).all()
        return [
            {"id": r.job_id, "kind": r.kind, "state": r.state, "progress": r.progress, "step": r.step} for r in rows
        ]

    def history(
        self, user_id: str | None = None, everyone: bool = False, kind: str | None = None, limit: int = 1000
    ) -> list[dict]:
        """Jobs newest first, without their results (a page of the admin's or the user's list)."""
        cols = [jobs.c[k] for k in ("job_id", "kind", "state", "user_id", "schedule", "submitted_at")]
        cols += [jobs.c[k] for k in ("started_at", "finished_at", "engine", "progress", "step", "error")]
        q = select(*cols)
        if not everyone:
            q = q.where(jobs.c.user_id.is_not_distinct_from(user_id))
        if kind:
            q = q.where(jobs.c.kind == kind)
        with self.db.read() as c:
            rows = c.execute(q.order_by(jobs.c.submitted_at.desc(), ROWID.desc()).limit(limit)).all()
        return [
            {
                "id": r.job_id,
                "kind": r.kind,
                "state": r.state,
                "user_id": r.user_id,
                "schedule": r.schedule,
                "submitted_at": iso(r.submitted_at),
                "started_at": iso(r.started_at),
                "finished_at": iso(r.finished_at),
                "engine": r.engine,
                "progress": r.progress,
                "step": r.step,
                "error": _loads(r.error),
            }
            for r in rows
        ]

    def latest(self, kind: str, match: Callable[[dict], bool] | None = None, scan: int = 20) -> dict | None:
        """The result of the newest finished job of a kind whose context passes `match`."""
        with self.db.read() as c:
            rows = c.execute(
                select(jobs.c.context, jobs.c.result)
                .where(jobs.c.kind == kind, jobs.c.state == "done")
                .order_by(jobs.c.finished_at.desc())
                .limit(scan)
            ).all()
        for r in rows:
            if match is None or match(json.loads(r.context)):
                return _loads(r.result)
        return None

    def cancel(self, job_id: str) -> str:
        """A queued job ends `cancelled` at once; a running one is asked to stop at its next step (the worker
        checks between batches). Returns the state after the request."""
        with self.db.write() as c:
            row = c.execute(select(jobs.c.state).where(jobs.c.job_id == job_id)).first()
            if row is None:
                raise KeyError(f"no job {job_id!r}")
            if row.state == "queued":
                c.execute(
                    update(jobs)
                    .where(jobs.c.job_id == job_id)
                    .values(state="cancelled", finished_at=self.clock(), version=jobs.c.version + 1)
                )
                return "cancelled"
            if row.state == "running":
                c.execute(
                    update(jobs)
                    .where(jobs.c.job_id == job_id, jobs.c.cancel_requested == 0)
                    .values(cancel_requested=1, version=jobs.c.version + 1)
                )
            return row.state

    # --- the worker ------------------------------------------------------------------------------------------------
    def claim(self, worker_id: str, engine: str | None = None) -> str | None:
        """The oldest queued job, now running on this worker; None when the queue is empty."""
        now = self.clock()
        with self.db.write() as c:
            job_id = c.execute(
                select(jobs.c.job_id).where(jobs.c.state == "queued").order_by(jobs.c.submitted_at, ROWID).limit(1)
            ).scalar()
            if job_id is None:
                return None
            c.execute(
                update(jobs)
                .where(jobs.c.job_id == job_id, jobs.c.state == "queued")
                .values(
                    state="running",
                    worker_id=worker_id,
                    engine=engine,
                    started_at=now,
                    heartbeat_at=now,
                    progress=0.0,
                    version=jobs.c.version + 1,
                )
            )
        return job_id

    def report(self, job_id: str, progress: float | None, step: str | None, eta: float | None) -> bool:
        """Write the progress of a running job; True when someone asked it to stop."""
        with self.db.write() as c:
            c.execute(
                update(jobs)
                .where(jobs.c.job_id == job_id, jobs.c.state == "running")
                .values(progress=progress, step=step, eta=eta, heartbeat_at=self.clock(), version=jobs.c.version + 1)
            )
            flag = c.execute(select(jobs.c.cancel_requested).where(jobs.c.job_id == job_id)).scalar()
        return bool(flag)

    def cancel_requested(self, job_id: str) -> bool:
        with self.db.read() as c:
            return bool(c.execute(select(jobs.c.cancel_requested).where(jobs.c.job_id == job_id)).scalar())

    def finish(self, job_id: str, state: str, result: dict | None = None, error: dict | None = None) -> None:
        assert state in FINAL
        values: dict = {"state": state, "finished_at": self.clock(), "version": jobs.c.version + 1, "eta": None}
        if result is not None:
            values["result"] = canonical(result)
        if error is not None:
            values["error"] = canonical(error)
        if state == "done":
            values["progress"] = 1.0
        with self.db.write() as c:
            c.execute(update(jobs).where(jobs.c.job_id == job_id, jobs.c.state == "running").values(**values))

    def sweep(self) -> list[str]:
        """Jobs left `running` by a worker that is gone end `error` (Interrupted). Called by a worker that has just
        taken the lease, so no live worker owns them."""
        with self.db.read() as c:
            ids = list(c.execute(select(jobs.c.job_id).where(jobs.c.state == "running")).scalars())
        self.interrupt(ids)
        return ids

    def interrupt(self, job_ids: list[str]) -> None:
        """These running jobs end `error` (Interrupted): their worker stopped, or is stopping without them."""
        if not job_ids:
            return
        with self.db.write() as c:
            c.execute(
                update(jobs)
                .where(jobs.c.job_id.in_(job_ids), jobs.c.state == "running")
                .values(
                    state="error",
                    error=canonical(INTERRUPTED),
                    finished_at=self.clock(),
                    eta=None,
                    version=jobs.c.version + 1,
                )
            )

    def prune(self, older_than_days: float, keep: int = 500) -> int:
        """Delete finished jobs older than this, keeping the newest `keep` whatever their age."""
        cutoff = self.clock() - older_than_days * 86400
        with self.db.write() as c:
            kept = (
                select(jobs.c.job_id).order_by(jobs.c.submitted_at.desc(), ROWID.desc()).limit(keep).scalar_subquery()
            )
            res = c.execute(
                delete(jobs).where(
                    jobs.c.state.in_(FINAL),
                    jobs.c.finished_at < cutoff,
                    jobs.c.job_id.not_in(kept),
                )
            )
        return res.rowcount or 0

    def counts(self) -> dict:
        with self.db.read() as c:
            rows = c.execute(select(jobs.c.state, func.count()).group_by(jobs.c.state)).all()
        return {r[0]: r[1] for r in rows}


class Workers:
    """The worker leases: one live worker per database owns the queue (and the GPU)."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock

    def register(self, worker_id: str, host: str, pid: int, role: str, engine: str | None, concurrency: int) -> bool:
        """Take the lease; False when another worker is alive on this database."""
        now = self.clock()
        with self.db.write() as c:
            alive = c.execute(
                select(workers.c.worker_id).where(
                    workers.c.stopped_at.is_(None),
                    workers.c.heartbeat_at > now - STALE_SECONDS,
                    workers.c.worker_id != worker_id,
                )
            ).first()
            if alive is not None:
                return False
            c.execute(  # the ones that died without saying so
                update(workers).where(workers.c.stopped_at.is_(None)).values(stopped_at=now)
            )
            c.execute(
                insert(workers).values(
                    worker_id=worker_id,
                    host=host,
                    pid=pid,
                    role=role,
                    engine=engine,
                    concurrency=concurrency,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
        return True

    def beat(self, worker_id: str, info: dict | None = None) -> None:
        values: dict = {"heartbeat_at": self.clock()}
        if info is not None:
            values["info"] = canonical(info)
        with self.db.write() as c:
            c.execute(update(workers).where(workers.c.worker_id == worker_id).values(**values))

    def stop(self, worker_id: str) -> None:
        with self.db.write() as c:
            c.execute(update(workers).where(workers.c.worker_id == worker_id).values(stopped_at=self.clock()))

    def alive(self) -> list[dict]:
        now = self.clock()
        with self.db.read() as c:
            rows = c.execute(
                select(workers).where(
                    and_(workers.c.stopped_at.is_(None), workers.c.heartbeat_at > now - STALE_SECONDS)
                )
            ).all()
        return [self._row(r, now) for r in rows]

    def recent(self, limit: int = 10) -> list[dict]:
        now = self.clock()
        with self.db.read() as c:
            rows = c.execute(select(workers).order_by(workers.c.started_at.desc()).limit(limit)).all()
        return [self._row(r, now) for r in rows]

    @staticmethod
    def _row(r, now: float) -> dict:
        alive = r.stopped_at is None and r.heartbeat_at > now - STALE_SECONDS
        return {
            "worker_id": r.worker_id,
            "host": r.host,
            "pid": r.pid,
            "role": r.role,
            "engine": r.engine,
            "concurrency": r.concurrency,
            "started_at": iso(r.started_at),
            "heartbeat_at": iso(r.heartbeat_at),
            "stopped_at": iso(r.stopped_at),
            "alive": alive,
            "info": _loads(r.info),
        }


__all__ = [
    "ACTIVE",
    "FINAL",
    "INTERRUPTED",
    "IdempotencyConflict",
    "JobQueue",
    "STATES",
    "Workers",
    "fingerprint",
    "submitting",
    "view",
]

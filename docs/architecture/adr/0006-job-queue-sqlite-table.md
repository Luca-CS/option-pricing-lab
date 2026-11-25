# ADR-0006: A home-made job table on SQLite, not huey

- Status: Accepted, 7 November 2026

## Context

The first desk ran its jobs in a thread pool of the HTTP process: a restart lost them, a crash killed the desk with
them, and the GPU and the VaR's process pool lived in the server. The rule: the simplest technology that is enough,
no Redis.

A job must carry its id, kind, parameters, the market context frozen at submission, its user, state, progress, step,
ETA, engine, times, result and error. Everything persists; a job a crash interrupted ends in error (it is run again,
never resumed). One worker owns the GPU. A scheduler runs the end-of-day risk, the lake update, the nightly backup and
the housekeeping; schedules are editable by admins and audited.

## Comparison

| Criterion | Home-made table (`jobs`, `schedules` in the operational SQLite) | `huey` with `SqliteHuey` |
|---|---|---|
| Code to maintain | a claim (`UPDATE ... RETURNING`), a heartbeat, progress, a cancel flag, a sweep at start, a schedule loop | huey's queue plus a `jobs` table anyway (the front lists jobs by user and kind, with their market and progress, which huey's result store does not hold) and the glue between them |
| Progress, step, ETA | columns of the row | not provided: the table again |
| Cancelling a running job | a cooperative flag in the row | `revoke` stops a task before it starts; a running one needs the same flag |
| A crash | `running` rows of a dead worker end in error at the next start | a task lost mid-run is not marked failed without the table |
| Schedules edited at run time, audited | rows of `schedules` | decorators in code; run-time edits need the table |
| Dependencies | none | huey |

## Decision

**A home-made job table and scheduler** in `optlab.server.jobs`, on the operational database
([ADR-0007](0007-storage-sqlite-alembic.md)), run by the worker. Less code than huey plus the table the front needs
anyway, and no extra moving part.

- **A job is JSON**: its validated parameters and what the desk held at submission (`tasks.Frozen`: the snapshot id of
  the market in use, the basket settings, the cost table's version, the trades by id and version, their fixings). Each
  desk method that submits has a `run_<kind>` twin that rebuilds the same objects in the worker, bit for bit. The
  worker never unpickles anything.
- **One worker per database**: a lease row renewed every 5 s; a worker silent for 15 s is gone. The worker runs inside
  the api's process by default, or alone (`optlab worker`, the `worker` container).
- **Progress and cancellation** go through `optlab.core.progress`, a context variable that is silent unless the worker
  sets a reporter. The batch loops tick it; the same write reads the cancel flag, so a cancelled job stops at its next
  batch.
- **Idempotency**: the same user's identical job still queued or running is given back, and an `Idempotency-Key`
  gives back its job for good.
- **Schedules**: `backup` (03:00, `VACUUM INTO`, 14 days kept; on), `housekeeping` (03:30; on), `lake_backup` (weekly;
  on), `lake` (weekdays 22:30, the public sources; off), `eod` (weekdays 23:00, the day's risk; off). A time missed while
  no worker ran fires once at the next start.

## Consequences

- The worker is a plain loop: claim, run, write. The VaR keeps its process pool inside the job, with the engine passed
  explicitly.
- SQLite's single writer is enough: writes are short, and WAL lets the api read during them. The worker polls every
  250 ms when idle, so a job starts in under a second.
- Several machines running workers would need a server database, and a new ADR.

## Alternatives considered

- huey on SQLite: compared above.
- Celery, RQ, Dramatiq: need Redis or RabbitMQ.
- APScheduler for the schedules: one more dependency for what a loop over a few rows does.

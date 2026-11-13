# ADR-0006: A home-made job table on SQLite, not huey

- Status: Accepted (A0 checkpoint, 7 November 2026); the comparison asked by the handoff is below
- Handoff §1: Jobs; §4.3

## Context

v0 runs jobs in a thread pool of the HTTP process (`app/jobs.py`, 80 lines): a restart loses them, a crash kills the
desk with them, the GPU and the VaR's process pool live in the server. Answer 9: the simplest technology that is
enough, no Redis.

What a job must carry: id, kind, parameters, the market context frozen at submission, the user, state, progress, step,
ETA, engine, times, a result reference, an error. Everything persists; a job interrupted by a crash is marked
`failed` (no automatic resume: it is run again). One worker owns the GPU; concurrency is configurable. A scheduler
runs the EOD (off by default), the lake update (off by default; Yahoo chains during US hours only), the nightly SQLite
backup (on) and the log rotation; schedules are editable by the admin role and audited.

## Comparison

| Criterion | Home-made table (`jobs`, `schedules` in the operational SQLite) | `huey` with `SqliteHuey` |
|---|---|---|
| Code to maintain | about 300-400 lines: claim (`UPDATE ... SET state='running' WHERE id = (SELECT ... LIMIT 1) RETURNING`), heartbeat, progress, cancel flag, crash sweep at start, a schedule loop | huey's queue plus our own `jobs` table anyway (the UI lists jobs by user and kind, with the market context and progress, which huey's result store does not hold) and the glue between them |
| Progress, step, ETA | columns of the row, written by the job | not provided: our table again |
| Cancellation of a running job | cooperative flag in the row, checked by the job (as v0) | `revoke` stops a task before it starts; a running one needs the same cooperative flag |
| Persistence, crash | rows survive; `running` rows with a stale heartbeat become `failed` at worker start | messages persist; a task lost mid-run is not marked failed without our table |
| Schedules editable at runtime and audited | rows of `schedules` (kind, cron-like spec, enabled, last run) | periodic tasks are decorators in code; runtime edits need our table |
| SSE | the api reads the row | the api reads our table |
| Tests | plain SQLite in a temp folder, a fake clock | huey's immediate mode, plus our table |
| Dependencies | none new | huey (small, maintained) |

## Decision

**A home-made job table and scheduler** in `optlab.server.jobs`, on the operational SQLite database (ADR-0007), run
by the `worker` process. It is less code than huey plus the table that the UI needs anyway, and it has no extra moving
part. Polling interval of the worker: 250 ms when idle (a job starts in under a second); the api's SSE reads the row
at the same rate.

Safety notes: job parameters are JSON validated by the router's Pydantic model before the row is written; the worker
never unpickles anything from the table. Results are JSON (or a reference to a stored run).

## Consequences

- The worker is a plain loop: claim, run, write. The VaR keeps its spawn process pool inside the job, with the
  engine passed explicitly.
- SQLite's single writer is enough: writes are short (claims, progress every second at most), and WAL lets the api
  read during them.
- If one day several machines run workers, a server database would be needed; a new ADR then.

## Implementation (A6, 13 November 2026)

- Tables `jobs`, `workers`, `schedules` (Alembic `0003`, tables added only). `optlab.server.jobs.JobQueue` (submit,
  claim, report, finish, cancel, sweep, prune) and `Workers` (the lease): about 450 lines with the worker loop
  (`worker.py`), the schedules (`schedules.py`) and what a job carries (`tasks.py`), close to the estimate.
- **A job is JSON**: its validated parameters and `tasks.Frozen`, what the desk held at submission (the snapshot id
  of the market in use, the typed basket settings, the version of the cost table, the trades by id and version, their
  fixings). Each desk method that submitted a closure in v0 is now a pair: the method validates and freezes, its
  `run_<kind>` twin rebuilds the same objects in the worker. The same bits come out: the showcase golden values the
  book through this path, bit for bit.
- **One worker per database**: a lease row renewed every 5 s; a worker that has been silent for 15 s is gone. The
  worker runs in the api's process by default (`Settings.worker = "embedded"`, one command to start the desk on
  Windows) or alone (`python -m optlab.server.worker`, `OPTLAB_WORKER=external`, the container of A7); a second one
  waits for the lease (`--wait-lease`) instead of sharing the GPU. At start the lease holder marks every `running` row
  `error` (Interrupted).
- **Progress and cancellation** come from `optlab.core.progress`, a context variable that is silent unless the worker
  sets a reporter: the batch loop of `structured.engine`, the chunks of the VaR and the days of the EOD backfill tick
  it, the desk names its steps. The reporter writes the fraction (never backwards), the step and an ETA at most twice a
  second, and the same write reads the cancel flag: a cancelled job stops at its next batch (the batches not started
  are dropped, the VaR's pool cancels its pending chunks). A stopping worker interrupts its jobs the same way and marks
  them `error` (Interrupted), not `cancelled`.
- **Idempotency**: the fingerprint of a job is the sha256 of its kind, parameters and frozen context; the same user's
  identical job still queued or running is given back, and an `Idempotency-Key` gives back its job for good (the same
  principle as the risk store's run keys: a run is keyed by what determines it).
- **Results** are the envelope `{market, method, result}`, stored as computed (the library's shapes); the API shows
  every Monte Carlo number as `{value, se}` (`optlab.server.present`, api.md §5).
- **Schedules**: `backup` (03:00, `VACUUM INTO` in `<backups>/nightly`, 14 days kept; on), `housekeeping` (03:30: jobs
  over 30 days, sessions a week past their expiry, log files; on), `lake` (Mon-Fri 22:30, the public sources; off),
  `eod` (Mon-Fri 23:00, the risk EOD of the day; off). Local time of `Settings.timezone`; a time missed while no worker
  ran fires once at the next start. Edited and run by admins (`/api/v1/admin/pipelines`), every change audited.
- Log files: JSON lines in `<data>/logs/api.log` and `worker.log`, rotated at midnight, 14 kept (`optlab.server.logs`).
- Not here: the weekly copy of the lake and `optlab backup|restore` come with the CLI (A7); the Yahoo option chains
  stay a manual step (US hours only, see `schedules.py`).

## Alternatives considered

- huey on SQLite: compared above.
- Celery, RQ, Dramatiq: need Redis or RabbitMQ. Excluded by answer 9.
- APScheduler for the schedules: one more dependency for what a loop over a dozen rows does.

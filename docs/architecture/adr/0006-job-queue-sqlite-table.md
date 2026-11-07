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

## Alternatives considered

- huey on SQLite: compared above.
- Celery, RQ, Dramatiq: need Redis or RabbitMQ. Excluded by answer 9.
- APScheduler for the schedules: one more dependency for what a loop over a dozen rows does.

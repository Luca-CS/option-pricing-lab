# Operations

How the desk runs on this machine: start and stop it, accounts, backups and their restoration, the demo data, the job
queue, and what to do when something goes wrong. Every command is `optlab ...` (`uv run optlab --help` lists them);
`--data DIR` before a command, or `OPTLAB_DATA`, picks the data folder, and every other path comes from its settings
(`<data>/config/optlab.env`, ADR-0012). The containers, the LAN profile and the start after a reboot are in the
second part (step A7, containers), once they are measured.

## Where things are

| What | Where (default) |
|---|---|
| data folder | `optlab-data` beside the main checkout (`C:\Users\Luca\Code\projets\optlab-data`) |
| database (trades, fixings, quotes, accounts, audit, jobs, schedules, risk runs) | `<data>/store/optlab.sqlite` |
| lake (Parquet and its raw files) | `<data>/lake` |
| showcase book | `<data>/showcase/optlab.sqlite` |
| logs (JSON lines, a file a day, 14 kept) | `<data>/logs/api.log`, `worker.log` |
| backups | `<data>/backups/nightly`, `manual`, `pre-restore`, `lake` |
| settings file (outside the repository) | `<data>/config/optlab.env` |

## Start and stop

```bash
uv sync --locked --all-extras                       # after a pull: the environment of the lock
uv run optlab serve                                 # http://127.0.0.1:8765/api/v1, its worker in the same process
uv run optlab serve --store <data>/showcase/optlab.sqlite --market <snapshot id> --workers 6 --open
```

Ctrl+C stops it: the running jobs stop at their next batch and end `error` (Interrupted), never resumed; queued jobs
wait for the next start. The database migrates itself to the code's schema when it is opened (`optlab migrate` does
it alone, and imports a v0 folder with `--v0`).

To run the worker on its own (the GPU in one process, the API in another):

```bash
OPTLAB_WORKER=external uv run optlab serve
uv run optlab worker --concurrency 2 --engine auto
```

One worker serves a database: a second one waits for the lease (15 s after the first one stops answering).
`GET /api/v1/ready` answers 503 while no worker serves the queue.

## Accounts

```bash
uv run optlab users create <name> --roles admin      # the password is asked twice, never an argument
uv run optlab users list
uv run optlab users roles <name> trader,risk         # its sessions are closed
uv run optlab users password <name>                  # a lost admin password is reset here
uv run optlab users disable <name>
```

Every command is in the audit log, as the system. `--database FILE` acts on another database (the showcase's).

## Backups

| Copy | When | Kept | Where |
|---|---|---|---|
| the database (`VACUUM INTO`, consistent while the desk runs) | every night at 03:00 (schedule `backup`) | 14 days | `<data>/backups/nightly` |
| the lake (unchanged files are hard links to the last copy) | Sundays at 04:00 (schedule `lake_backup`) | 4 copies | `<data>/backups/lake/<stamp>` |
| either, now | `optlab backup [--lake]` | until removed | `<data>/backups/manual`, `.../lake` |

A missed schedule (the machine was off) runs once at the next start of a worker. An admin edits or runs them:
`GET /api/v1/admin/pipelines`, `PATCH .../{name}` (`enabled`, `days`, `at`, `params`: `keep_days`, `keep`),
`POST .../{name}/run`; `GET /api/v1/admin/system` lists the last copies. A lake copy waits when the lake is being
updated. Its `MANIFEST.json`, written last, holds the size and sha256 of every file: a folder without it is an
unfinished copy, removed at the next one.

All of these live on the same disk as the data: a copy elsewhere (another disk) is Luca's to make, from
`<data>/backups`.

## Restore

```bash
uv run optlab restore latest                         # the newest copy of the database (nightly or manual)
uv run optlab restore <data>/backups/nightly/optlab-20261113-030000-000.sqlite
uv run optlab restore latest --lake                  # the newest copy of the lake
```

Stop the API and the worker first: a restoration refuses while a worker serves the database. It checks the copy
(SQLite's integrity check, a schema revision this code knows), asks for a confirmation (`--yes` skips it), saves the
database it replaces in `<data>/backups/pre-restore`, then migrates the copy to the code's revision. A lake copy is
checked file by file against its manifest; the current lake is moved aside (`<data>/lake.before-restore-<stamp>`),
never deleted. Both are tested (`packages/server/tests/test_cli.py`).

## Demo data

`optlab serve --demo-data` serves a data folder made only of made-up numbers (the synthetic lake, the simulated
calibration, a book of four trades struck on its past markets, an account `demo` whose password is printed once),
built in `<temp>/optlab-demo` the first time (about two minutes). `optlab demo data --to DIR [--password-stdin]`
builds it elsewhere, for the front's development and its end-to-end tests (handoff §7.7); then
`optlab --data DIR serve`. Nothing in it comes from a vendor.

## The job queue

```bash
uv run optlab jobs list                              # every user's, newest first
uv run optlab jobs wait <id>                         # follows its progress
uv run optlab jobs cancel <id>
uv run optlab risk var --as-job                      # leave a long run to the worker that serves the database
```

## When something goes wrong

| Symptom | Where to look, what to do |
|---|---|
| a job ended `error` | its message: `optlab jobs show <id>` or `GET /api/v1/jobs/{id}`; its trace: `<data>/logs/worker.log` (or `api.log` with the embedded worker) |
| jobs stay `queued` | no worker: `GET /api/v1/ready` (503), `GET /api/v1/admin/system` (`workers`); start `optlab worker` or the API without `OPTLAB_WORKER=external` |
| "another worker serves this database" | one is running (another terminal, a container); or wait 15 s after a crash |
| the database is damaged | `optlab restore latest` (after stopping everything) |
| a command prints `error: ...` | the library's message: a wrong option, a missing file or table (`optlab lake derive` before the steps that read it) |
| an account is locked | it unlocks after `OPTLAB_LOGIN_LOCKOUT_MINUTES` (15); or `optlab users password <name>` |

What Luca sets himself: the machine's sleep and power settings (a sleeping machine runs no schedule; a missed one
runs at the next start), any Windows firewall rule, and the LAN profile (ADR-0002).

# ADR-0007: SQLite in WAL with SQLAlchemy Core and Alembic

- Status: Accepted (A0 checkpoint, 7 November 2026)
- Handoff §1: Stockage; §4.4

## Context

v0 keeps its operational state in several SQLite files written with raw `sqlite3` and `CREATE TABLE IF NOT EXISTS`:
`data/store/desk.sqlite` (trades versioned as JSON, fixings, quotes, settings, cost defaults), `market/index.sqlite`
(snapshots), `risk.sqlite` (risk runs, scenarios), the showcase's own copies, and replay runs as JSON files. No schema
migration exists. Answer 10: the simplest storage unless a decisive gain. The lake (339 MB of Parquet, DuckDB views)
works and is out of scope.

## Decision

- **One operational SQLite database** (`optlab.sqlite`) in **WAL** mode, `synchronous=NORMAL`, `foreign_keys=ON`,
  a busy timeout; one schema module per domain: trades (versioned), fixings, quotes, baskets, cost defaults
  (versioned), market snapshots, replay and vol lab runs, risk records and runs, scenarios, users, sessions, audit,
  jobs, schedules.
- **SQLAlchemy Core** (tables and queries, no ORM session) and **Alembic** for migrations, run by `optlab deploy` and
  at worker start (`alembic upgrade head`), never by the api.
- Domain packages that store something declare a small repository protocol; `optlab.server.persistence` implements
  it. SQL is always parameterised (SQLAlchemy binds).
- **Migration of the v0 stores** by one idempotent command (`optlab migrate v0 --from ...`), tried on copies first,
  with a backup before (`optlab-data/backups/pre-refactor/` exists since A0), and verified by the showcase golden
  numbers.
- **The lake keeps its format**: Parquet append-only, `origin` on every row, bitemporal loads, DuckDB in memory. Only
  its access changes: one interface, paths from `Settings`.
- The database file lives on a Linux filesystem in containers (a Docker volume on WSL's ext4), never on `/mnt/c`: WAL
  needs shared memory that the 9P mount does not provide reliably.
- Postgres only if a need is measured (several machines, concurrent heavy writers). Not today.

## Implementation (A4)

- `optlab.server.persistence`: `database.py` (one file, WAL, `synchronous=NORMAL`, foreign keys, a 30 s busy
  timeout; reads `BEGIN`, writes `BEGIN IMMEDIATE`, so two writers that read a version and insert the next one wait
  for each other: a test runs four threads on one key), `schema.py` (SQLAlchemy Core), `migrations/` (Alembic, the
  first revision written by hand; a test checks it builds exactly the declared metadata), and the repositories:
  `desk.py`, `snapshots.py`, `replays.py`, `risk.py`, with the methods of the v0 stores they replace.
- The domain packages declare protocols (`risk.store.RiskStorage`, `marketdata.store.SnapshotStorage`) and hold no SQL;
  the risk engine keeps its code version and run keys.
- Users, sessions, audit, jobs and schedules arrive with their steps (A5, A6) as new revisions.
- A v0 store file is refused as a database, with the command that imports it; `migrate_v0` reads a v0 folder
  read-only, copies rows and JSON payloads as text (bit for bit), inserts only new keys (idempotent), and checks every
  source row against the target. Tested on synthetic v0 folders, then on copies of the real showcase and desk stores,
  then through the showcase's golden numbers, which now run on a migrated copy.
- Migrations run when the desk opens its database, until the worker (A6) and `optlab deploy` (A9) take that over.

## Consequences

- Backups are one file: `VACUUM INTO` nightly, 14 days kept (ADR-0006 schedules it); restore is tested (`optlab
  restore`).
- Alembic adds a dependency and a `migrations/` folder to `optlab-server`; in exchange every schema change is a
  reviewed, reversible step.
- The showcase becomes a database of its own (`--store`), migrated by the same command.

## Alternatives considered

- **Raw sqlite3 as in v0**: no migrations; schema drift across stores already exists (desk vs showcase).
- **SQLAlchemy ORM**: heavier, hides the SQL; Core is enough for a dozen tables.
- **Postgres in a container**: one more service to run, back up and secure, for one writer.

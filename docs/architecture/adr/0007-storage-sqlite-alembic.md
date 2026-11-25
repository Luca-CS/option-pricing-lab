# ADR-0007: SQLite in WAL with SQLAlchemy Core and Alembic

- Status: Accepted, 7 November 2026

## Context

The first desk kept its state in several SQLite files written with raw `sqlite3` and `CREATE TABLE IF NOT EXISTS`
(trades, fixings, quotes, snapshots, risk runs, scenarios), plus replay runs as JSON files. There were no migrations,
and the schemas had drifted between copies. The rule: the simplest storage unless a decisive gain. The lake (Parquet,
DuckDB views) works and keeps its format.

## Decision

- **One operational SQLite database** (`optlab.sqlite`) in **WAL** mode, `synchronous=NORMAL`, `foreign_keys=ON`,
  a 30 s busy timeout. Reads `BEGIN`, writes `BEGIN IMMEDIATE`, so two writers that read a version and insert the next
  wait for each other.
- **SQLAlchemy Core** (tables and queries, no ORM session) and **Alembic** for migrations, run at the worker's start
  and by the deploy (`optlab migrate`), never by the api.
- Domain packages that store something declare a small repository protocol (`risk.store.RiskStorage`,
  `marketdata.store.SnapshotStorage`); `optlab.server.persistence` implements it. SQL is always bound.
- An old data folder is imported by one idempotent command (`optlab migrate v0 --from ...`) that reads it read-only,
  copies rows and JSON payloads as text, inserts only new keys, and checks every source row against the target.
- **The lake keeps its format**: append-only Parquet, `origin` on every row, bitemporal loads, DuckDB in memory, with
  its paths from `Settings`.
- In containers the database lives on a Linux filesystem (a Docker volume on WSL's ext4), never on `/mnt/c`: WAL needs
  shared memory that the Windows file share does not provide reliably.
- Postgres only if a need is measured (several machines, concurrent heavy writers).

## Consequences

- A backup is one file: `VACUUM INTO` nightly, 14 days kept; a restoration is checked (`optlab restore`).
- Every schema change is a reviewed, reversible Alembic revision; a test checks the revisions build exactly the
  declared metadata.

## Alternatives considered

- **Raw `sqlite3`**: no migrations, and schema drift across copies.
- **The SQLAlchemy ORM**: heavier, hides the SQL; Core is enough for a few dozen tables.
- **Postgres in a container**: one more service to run, back up and secure, for one writer.

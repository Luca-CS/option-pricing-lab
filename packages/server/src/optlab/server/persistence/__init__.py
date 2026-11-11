"""The server's persistence (ADR-0007): one SQLite database, SQLAlchemy Core tables (`schema`), Alembic migrations
(`migrations/`), and the repositories the desk and the domain packages are given (`desk`, `snapshots`, `replays`,
`risk`). `migrate_v0` imports the stores of v0 (a desk folder: desk.sqlite, risk.sqlite, market/, replay/)."""

from optlab.server.persistence.database import Database

__all__ = ["Database"]

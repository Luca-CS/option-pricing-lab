"""The operational database (ADR-0007): one SQLite file in WAL mode, SQLAlchemy Core, the schema by Alembic.

Transactions are explicit. A read is `BEGIN` (deferred); a write is `BEGIN IMMEDIATE`, which takes the write lock at
once, so two processes that both read a version and insert the next one wait for each other instead of colliding
(the busy timeout covers the wait). This follows SQLAlchemy's recipe for pysqlite: the driver's own transaction
handling is off, and the engine issues BEGIN itself.

Migrations run when a `Database` opens with `upgrade=True` (the desk does, until the worker of A6 takes it over;
`optlab deploy` will in A9). A file another process upgraded is simply at head.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL

MIGRATIONS = Path(__file__).with_name("migrations")
BUSY_TIMEOUT_MS = 30_000


def make_engine(path: Path) -> Engine:
    engine = create_engine(URL.create("sqlite", database=str(path)))

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None  # the engine begins transactions itself (below)
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn: Connection):
        conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.get_execution_options().get("optlab_write") else "BEGIN")

    return engine


def _refuse_v0(path: Path) -> None:
    """A v0 store file (desk.sqlite or risk.sqlite: tables, no Alembic version) is never opened as a database: its
    content is imported by `persistence.migrate_v0` into a new file, and the old one stays as it was."""
    if not path.is_file() or path.stat().st_size == 0:
        return
    import sqlite3

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    if names and "alembic_version" not in names:
        raise ValueError(
            f"{path} is a v0 store ({', '.join(sorted(names))}), not an optlab database: import it with "
            "`python -m optlab.server.persistence.migrate_v0 <its folder> <new database file>`"
        )


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["engine"] = engine
    return cfg


class Database:
    """One database file. `read()` and `write()` give a connection inside a transaction."""

    def __init__(self, path: str | Path, upgrade: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _refuse_v0(self.path)
        self.engine = make_engine(self.path)
        if upgrade:
            self.upgrade()

    def upgrade(self, revision: str = "head") -> None:
        command.upgrade(alembic_config(self.engine), revision)

    def revision(self) -> str | None:
        """The schema's current Alembic revision (None for an empty file)."""
        with self.engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def head(self) -> str | None:
        return ScriptDirectory.from_config(alembic_config(self.engine)).get_current_head()

    @contextmanager
    def read(self) -> Iterator[Connection]:
        with self.engine.connect() as conn, conn.begin():
            yield conn

    @contextmanager
    def write(self) -> Iterator[Connection]:
        with self.engine.connect().execution_options(optlab_write=True) as conn, conn.begin():
            yield conn

    def close(self) -> None:
        """Release the file (Windows cannot remove a file a connection still holds)."""
        self.engine.dispose()

    def backup(self, target: str | Path) -> Path:
        """A consistent copy of the whole database, by `VACUUM INTO` (the nightly backup of ADR-0006)."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        raw = self.engine.raw_connection()  # VACUUM cannot run inside the transaction a Connection would begin
        try:
            raw.cursor().execute("VACUUM INTO ?", (str(target),))
        finally:
            raw.close()
        return target

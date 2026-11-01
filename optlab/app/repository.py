"""Local store of the desk: trades (every version kept), fixings, saved quotes, settings. SQLite under data/store,
which is git-ignored like all of data/: real fixings from Yahoo are personal-use data."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..config import paths
from ..structured.trade import Trade

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS trades (trade_id TEXT, version INTEGER, book TEXT, status TEXT, payload TEXT,
       deleted INTEGER DEFAULT 0, recorded_at TEXT, PRIMARY KEY (trade_id, version))""",
    """CREATE TABLE IF NOT EXISTS fixings (underlying TEXT, date TEXT, level REAL, source TEXT, recorded_at TEXT,
       PRIMARY KEY (underlying, date))""",
    """CREATE TABLE IF NOT EXISTS quotes (quote_id TEXT PRIMARY KEY, created_at TEXT, sheet TEXT, result TEXT)""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, recorded_at TEXT)""",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else paths().store / "desk.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as con:
            for stmt in SCHEMA:
                con.execute(stmt)

    @contextmanager
    def _db(self):
        """A connection that commits on success and is always closed (sqlite3's own context manager only commits,
        and an open file cannot be removed on Windows)."""
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- trades ---
    def save_trade(self, trade: Trade) -> Trade:
        """Store a trade; an existing id gets a new version (the old ones stay readable)."""
        with self._lock, self._db() as con:
            row = con.execute("SELECT MAX(version) FROM trades WHERE trade_id = ?", (trade.trade_id,)).fetchone()
            version = (row[0] or 0) + 1
            stored = Trade.from_json(trade.to_json())
            object.__setattr__(stored, "version", version)
            con.execute("INSERT INTO trades VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (stored.trade_id, version, stored.book, stored.status, stored.to_json(), _now()))
        return stored

    def trade(self, trade_id: str, version: int | None = None) -> Trade:
        with self._db() as con:
            if version is None:
                row = con.execute("SELECT payload FROM trades WHERE trade_id = ? AND deleted = 0 ORDER BY version "
                                  "DESC LIMIT 1", (trade_id,)).fetchone()
            else:
                row = con.execute("SELECT payload FROM trades WHERE trade_id = ? AND version = ?",
                                  (trade_id, version)).fetchone()
        if row is None:
            raise KeyError(f"no trade {trade_id!r}")
        return Trade.from_json(row["payload"])

    def trades(self, book: str | None = None, status: str | None = None) -> list[Trade]:
        """Latest version of every trade not deleted."""
        with self._db() as con:
            rows = con.execute("SELECT t.payload FROM trades t JOIN (SELECT trade_id, MAX(version) v FROM trades "
                               "GROUP BY trade_id) m ON t.trade_id = m.trade_id AND t.version = m.v "
                               "WHERE t.deleted = 0 ORDER BY t.recorded_at").fetchall()
        out = [Trade.from_json(r["payload"]) for r in rows]
        return [t for t in out if (book is None or t.book == book) and (status is None or t.status == status)]

    def versions(self, trade_id: str) -> list[dict]:
        """Every stored version of a trade, oldest first, with what changed between them."""
        with self._db() as con:
            rows = con.execute("SELECT version, payload, deleted, recorded_at FROM trades WHERE trade_id = ? "
                               "ORDER BY version", (trade_id,)).fetchall()
        if not rows:
            raise KeyError(f"no trade {trade_id!r}")
        out = []
        for r in rows:
            t = Trade.from_json(r["payload"])
            out.append({"version": r["version"], "recorded_at": r["recorded_at"], "deleted": bool(r["deleted"]),
                        "quantity": t.quantity, "status": t.status, "book": t.book, "counterparty": t.counterparty})
        return out

    def delete_trade(self, trade_id: str) -> None:
        with self._lock, self._db() as con:
            con.execute("UPDATE trades SET deleted = 1 WHERE trade_id = ?", (trade_id,))

    def restore_trade(self, trade_id: str) -> None:
        with self._lock, self._db() as con:
            con.execute("UPDATE trades SET deleted = 0 WHERE trade_id = ?", (trade_id,))

    def new_trade_id(self, prefix: str = "T") -> str:
        with self._db() as con:
            n = con.execute("SELECT COUNT(DISTINCT trade_id) FROM trades").fetchone()[0]
        return f"{prefix}-{n + 1:04d}-{uuid.uuid4().hex[:4]}"

    # --- fixings (in the model's scale: the market is rescaled to a spot of 100) ---
    def put_fixings(self, underlying: str, levels: dict, source: str = "manual") -> int:
        rows = [(underlying, (d.isoformat() if isinstance(d, dt.date) else str(d)), float(v), source, _now())
                for d, v in levels.items()]
        with self._lock, self._db() as con:
            con.executemany("INSERT OR REPLACE INTO fixings VALUES (?, ?, ?, ?, ?)", rows)
        return len(rows)

    def delete_fixing(self, underlying: str, date: str) -> None:
        with self._lock, self._db() as con:
            con.execute("DELETE FROM fixings WHERE underlying = ? AND date = ?", (underlying, date))

    def fixings(self, underlying: str | None = None) -> dict:
        """{underlying: {date: level}}."""
        query, args = "SELECT underlying, date, level FROM fixings", ()
        if underlying:
            query, args = query + " WHERE underlying = ?", (underlying,)
        with self._db() as con:
            rows = con.execute(query + " ORDER BY underlying, date", args).fetchall()
        out: dict = {}
        for r in rows:
            out.setdefault(r["underlying"], {})[dt.date.fromisoformat(r["date"])] = r["level"]
        return out

    # --- quotes ---
    def save_quote(self, sheet: dict, result: dict) -> str:
        qid = uuid.uuid4().hex[:10]
        with self._lock, self._db() as con:
            con.execute("INSERT INTO quotes VALUES (?, ?, ?, ?)", (qid, _now(), json.dumps(sheet), json.dumps(result)))
        return qid

    def quote(self, quote_id: str) -> dict:
        with self._db() as con:
            row = con.execute("SELECT * FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
        if row is None:
            raise KeyError(f"no quote {quote_id!r}")
        return {"quote_id": row["quote_id"], "created_at": row["created_at"], "sheet": json.loads(row["sheet"]),
                "result": json.loads(row["result"])}

    def quotes(self, limit: int = 20) -> list[dict]:
        with self._db() as con:
            rows = con.execute("SELECT quote_id, created_at, sheet, result FROM quotes ORDER BY created_at DESC "
                               "LIMIT ?", (limit,)).fetchall()
        return [{"quote_id": r["quote_id"], "created_at": r["created_at"], "sheet": json.loads(r["sheet"]),
                 "headline": json.loads(r["result"]).get("headline")} for r in rows]

    # --- settings (JSON values) ---
    def setting(self, key: str, default=None):
        with self._db() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def put_setting(self, key: str, value) -> None:
        with self._lock, self._db() as con:
            con.execute("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)", (key, json.dumps(value), _now()))

"""SQLite store of the risk module: versioned records (spot-vol betas, scenarios, the stressed VaR window) and runs (EOD, VaR,
ladders), each with the code version and the parameters it ran with.

Records are append-only: a `put` adds a version, `get` reads the latest (or a given one). Runs are keyed by a hash of what
determines their numbers (kind, date, trades, market, settings, code): a run asked again with the same key is the same run,
which makes a backfill idempotent. Payloads are JSON; numbers are amounts in the payment currency and % of notional, never
market levels.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, version INTEGER, payload TEXT, meta TEXT, created_at TEXT,
       PRIMARY KEY (kind, key, version))""",
    """CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT, day TEXT, run_key TEXT, version INTEGER, code TEXT,
       params TEXT, payload TEXT, created_at TEXT, seconds REAL)""",
    "CREATE INDEX IF NOT EXISTS runs_by_day ON runs (kind, day)",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def code_version() -> str:
    """The commit of the code, with "+dirty" when the working tree has changes under packages/ (git is asked from this
    file's folder, so it finds the repository the code runs from)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", ":/packages"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return (head or "unknown") + ("+dirty" if dirty else "")
    except OSError, subprocess.SubprocessError:
        return "unknown"


def run_key(**parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


class RiskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            for stmt in SCHEMA:
                db.execute(stmt)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    # --- versioned records ---
    def put(self, kind: str, key: str, payload, meta: dict | None = None) -> int:
        with self._db() as db:
            row = db.execute("SELECT MAX(version) AS v FROM records WHERE kind = ? AND key = ?", (kind, key)).fetchone()
            version = (row["v"] or 0) + 1
            db.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (kind, key, version, json.dumps(payload), json.dumps({"code": code_version(), **(meta or {})}), _now()),
            )
        return version

    def get(self, kind: str, key: str, version: int | None = None) -> dict | None:
        """{"payload", "version", "meta", "created_at"} of the latest (or given) version, None when there is none."""
        with self._db() as db:
            if version is None:
                row = db.execute(
                    "SELECT * FROM records WHERE kind = ? AND key = ? ORDER BY version DESC LIMIT 1", (kind, key)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM records WHERE kind = ? AND key = ? AND version = ?", (kind, key, version)
                ).fetchone()
        if row is None:
            return None
        return {
            "payload": json.loads(row["payload"]),
            "version": row["version"],
            "meta": json.loads(row["meta"]),
            "created_at": row["created_at"],
        }

    def versions(self, kind: str, key: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT version, meta, created_at FROM records WHERE kind = ? AND key = ? ORDER BY version", (kind, key)
            ).fetchall()
        return [{"version": r["version"], "meta": json.loads(r["meta"]), "created_at": r["created_at"]} for r in rows]

    def keys(self, kind: str) -> list[str]:
        with self._db() as db:
            return [
                r["key"] for r in db.execute("SELECT DISTINCT key FROM records WHERE kind = ? ORDER BY key", (kind,))
            ]

    # --- runs ---
    def find_run(self, key: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE run_key = ? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        return self._run(row)

    def save_run(
        self, kind: str, day, key: str, params: dict, payload: dict, seconds: float, code: str | None = None
    ) -> str:
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
        with self._db() as db:
            row = db.execute("SELECT MAX(version) AS v FROM runs WHERE kind = ? AND day = ?", (kind, day)).fetchone()
            version = (row["v"] or 0) + 1
            run_id = f"{kind}-{day}-v{version}"
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    kind,
                    day,
                    key,
                    version,
                    code or code_version(),
                    json.dumps(params, default=str),
                    json.dumps(payload),
                    _now(),
                    float(seconds),
                ),
            )
        return run_id

    def run(self, run_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no risk run {run_id}")
        return self._run(row)

    def runs(self, kind: str | None = None, start=None, end=None, latest: bool = True) -> list[dict]:
        """Runs without their payload, by date; with `latest`, only the last version of each date."""
        q, args = (
            "SELECT run_id, kind, day, run_key, version, code, params, created_at, seconds FROM runs WHERE 1 = 1",
            [],
        )
        if kind:
            q, args = q + " AND kind = ?", args + [kind]
        if start:
            q, args = q + " AND day >= ?", args + [str(start)]
        if end:
            q, args = q + " AND day <= ?", args + [str(end)]
        with self._db() as db:
            rows = [dict(r) for r in db.execute(q + " ORDER BY day, version", args)]
        for r in rows:
            r["params"] = json.loads(r["params"])
        if latest:
            last = {}
            for r in rows:
                last[(r["kind"], r["day"])] = r
            rows = list(last.values())
        return rows

    @staticmethod
    def _run(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"])
        d["payload"] = json.loads(d["payload"])
        return d

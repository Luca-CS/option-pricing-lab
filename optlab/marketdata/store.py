"""Local store of market snapshots: one JSON file per snapshot and a SQLite index, under data/store (git-ignored).

Snapshots are immutable: storing the same id twice is a no-op, storing different content under an existing id is an
error. The local spot scale is kept in the index only, never in a file meant to be versioned.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from pathlib import Path

from optlab.config import paths
from optlab.snapshot import MarketSnapshot

SCHEMA = """CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY, underlying TEXT, valuation TEXT, asof TEXT, source TEXT, path TEXT,
    spot_scale REAL, recorded_at TEXT)"""


class MarketStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else paths().store / "market"

    def _db(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.root / "index.sqlite")
        con.execute(SCHEMA)
        return con

    def put(self, snap: MarketSnapshot) -> str:
        sid = snap.snapshot_id
        folder = self.root / snap.underlying / snap.valuation.isoformat()
        path = folder / f"{sid}.json"
        if path.exists():
            if MarketSnapshot.from_json(path.read_text(encoding="utf-8")) != snap:
                raise ValueError(f"snapshot {sid} already stored with a different content")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(snap.to_json(), encoding="utf-8")
        with self._db() as con:
            con.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    snap.underlying,
                    snap.valuation.isoformat(),
                    snap.asof,
                    snap.source,
                    str(path.relative_to(self.root)),
                    snap.spot_scale,
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                ),
            )
            if snap.spot_scale is not None:
                con.execute("UPDATE snapshots SET spot_scale = ? WHERE snapshot_id = ?", (snap.spot_scale, sid))
        return sid

    def get(self, snapshot_id: str) -> MarketSnapshot:
        with self._db() as con:
            row = con.execute("SELECT path, spot_scale FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(f"no snapshot {snapshot_id!r} in {self.root}")
        snap = MarketSnapshot.from_json((self.root / row[0]).read_text(encoding="utf-8"))
        return snap if row[1] is None else replace(snap, spot_scale=row[1])

    def latest(self, underlying: str, on_or_before: dt.date | str | None = None) -> MarketSnapshot:
        day = (on_or_before.isoformat() if isinstance(on_or_before, dt.date) else on_or_before) or "9999-12-31"
        with self._db() as con:
            row = con.execute(
                "SELECT snapshot_id FROM snapshots WHERE underlying = ? AND valuation <= ? "
                "ORDER BY valuation DESC, asof DESC LIMIT 1",
                (underlying, day),
            ).fetchone()
        if row is None:
            raise KeyError(f"no snapshot of {underlying} on or before {day}")
        return self.get(row[0])

    def list(self, underlying: str | None = None) -> list[dict]:
        query = "SELECT snapshot_id, underlying, valuation, asof, source FROM snapshots"
        args: tuple = ()
        if underlying:
            query, args = query + " WHERE underlying = ?", (underlying,)
        with self._db() as con:
            rows = con.execute(query + " ORDER BY valuation, asof", args).fetchall()
        return [dict(zip(("snapshot_id", "underlying", "valuation", "asof", "source"), r)) for r in rows]

"""Replays of trades on real closes that the desk ran, on the operational database. Each is the JSON of
`marketdata.replay.result_json`, kept under the stem v0 gave its file (`replay-<trade>-<model>`); the versioned replays
of `results/` stay files."""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import insert, select

from optlab.server.persistence.database import Database
from optlab.server.persistence.schema import replays


class ReplayRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, stem: str, payload: dict) -> None:
        """A replay under its stem; running the same trade again replaces it, as v0 rewrote its file."""
        with self.db.write() as c:
            c.execute(
                insert(replays)
                .prefix_with("OR REPLACE")
                .values(
                    stem=stem, payload=json.dumps(payload), recorded_at=dt.datetime.now(dt.timezone.utc).isoformat()
                )
            )

    def get(self, stem: str) -> dict | None:
        with self.db.read() as c:
            payload = c.execute(select(replays.c.payload).where(replays.c.stem == stem)).scalar()
        return None if payload is None else json.loads(payload)

    def all(self) -> list[tuple[str, dict]]:
        """(stem, payload) of every replay, by stem."""
        with self.db.read() as c:
            rows = c.execute(select(replays.c.stem, replays.c.payload).order_by(replays.c.stem)).all()
        return [(r.stem, json.loads(r.payload)) for r in rows]

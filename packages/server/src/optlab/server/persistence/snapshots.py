"""Market snapshots on the operational database (`marketdata.store.SnapshotStorage`). Immutable and identified by their
content: storing the same id twice is a no-op, storing a different content under an existing id is an error. The local
spot scale (real spot / model spot) is kept beside the content, never inside it, as in v0's file store."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from sqlalchemy import insert, select, update

from optlab.core.snapshot import MarketSnapshot
from optlab.server.persistence.database import Database
from optlab.server.persistence.schema import snapshots


class SnapshotRepository:
    def __init__(self, db: Database):
        self.db = db

    def put(self, snap: MarketSnapshot) -> str:
        sid = snap.snapshot_id
        with self.db.write() as c:
            stored = c.execute(select(snapshots.c.payload).where(snapshots.c.snapshot_id == sid)).scalar()
            if stored is not None:
                if MarketSnapshot.from_json(stored) != snap:
                    raise ValueError(f"snapshot {sid} already stored with a different content")
            else:
                c.execute(
                    insert(snapshots).values(
                        snapshot_id=sid,
                        underlying=snap.underlying,
                        valuation=snap.valuation.isoformat(),
                        asof=snap.asof,
                        source=snap.source,
                        payload=snap.to_json(),
                        spot_scale=snap.spot_scale,
                        recorded_at=dt.datetime.now(dt.UTC).isoformat(),
                    )
                )
            if snap.spot_scale is not None:
                c.execute(update(snapshots).where(snapshots.c.snapshot_id == sid).values(spot_scale=snap.spot_scale))
        return sid

    def get(self, snapshot_id: str) -> MarketSnapshot:
        with self.db.read() as c:
            row = c.execute(
                select(snapshots.c.payload, snapshots.c.spot_scale).where(snapshots.c.snapshot_id == snapshot_id)
            ).first()
        if row is None:
            raise KeyError(f"no snapshot {snapshot_id!r}")  # the message reaches the API: no local path in it
        snap = MarketSnapshot.from_json(row.payload)
        return snap if row.spot_scale is None else replace(snap, spot_scale=row.spot_scale)

    def latest(self, underlying: str, on_or_before: dt.date | str | None = None) -> MarketSnapshot:
        day = (on_or_before.isoformat() if isinstance(on_or_before, dt.date) else on_or_before) or "9999-12-31"
        q = (
            select(snapshots.c.snapshot_id)
            .where((snapshots.c.underlying == underlying) & (snapshots.c.valuation <= day))
            .order_by(snapshots.c.valuation.desc(), snapshots.c.asof.desc())
            .limit(1)
        )
        with self.db.read() as c:
            sid = c.execute(q).scalar()
        if sid is None:
            raise KeyError(f"no snapshot of {underlying} on or before {day}")
        return self.get(sid)

    def list(self, underlying: str | None = None) -> list[dict]:
        cols = (
            snapshots.c.snapshot_id,
            snapshots.c.underlying,
            snapshots.c.valuation,
            snapshots.c.asof,
            snapshots.c.source,
        )
        q = select(*cols)
        if underlying:
            q = q.where(snapshots.c.underlying == underlying)
        with self.db.read() as c:
            rows = c.execute(q.order_by(snapshots.c.valuation, snapshots.c.asof)).all()
        return [dict(r._mapping) for r in rows]

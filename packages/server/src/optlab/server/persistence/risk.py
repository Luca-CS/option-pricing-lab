"""The risk engine's storage on the operational database (`risk.store.RiskStorage`): versioned records (spot-vol betas,
scenarios, the stressed VaR window) and runs keyed by what determines their numbers. The same methods and the same
behaviour as v0's `risk.store.RiskStore` (a SQLite file of its own); the code version comes from `risk.store`."""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import func, insert, select

from optlab.risk.store import code_version
from optlab.server.persistence.database import Database
from optlab.server.persistence.schema import risk_records, risk_runs


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class RiskRepository:
    def __init__(self, db: Database):
        self.db = db

    @property
    def path(self):
        return self.db.path

    # --- versioned records ---
    def put(self, kind: str, key: str, payload, meta: dict | None = None) -> int:
        with self.db.write() as c:
            last = c.execute(
                select(func.max(risk_records.c.version)).where(
                    (risk_records.c.kind == kind) & (risk_records.c.key == key)
                )
            ).scalar()
            version = (last or 0) + 1
            c.execute(
                insert(risk_records).values(
                    kind=kind,
                    key=key,
                    version=version,
                    payload=json.dumps(payload),
                    meta=json.dumps({"code": code_version(), **(meta or {})}),
                    created_at=_now(),
                )
            )
        return version

    def get(self, kind: str, key: str, version: int | None = None) -> dict | None:
        """{"payload", "version", "meta", "created_at"} of the latest (or given) version, None when there is none."""
        q = select(risk_records).where((risk_records.c.kind == kind) & (risk_records.c.key == key))
        q = (
            q.order_by(risk_records.c.version.desc()).limit(1)
            if version is None
            else q.where(risk_records.c.version == version)
        )
        with self.db.read() as c:
            row = c.execute(q).first()
        if row is None:
            return None
        return {
            "payload": json.loads(row.payload),
            "version": row.version,
            "meta": json.loads(row.meta),
            "created_at": row.created_at,
        }

    def versions(self, kind: str, key: str) -> list[dict]:
        q = (
            select(risk_records.c.version, risk_records.c.meta, risk_records.c.created_at)
            .where((risk_records.c.kind == kind) & (risk_records.c.key == key))
            .order_by(risk_records.c.version)
        )
        with self.db.read() as c:
            rows = c.execute(q).all()
        return [{"version": r.version, "meta": json.loads(r.meta), "created_at": r.created_at} for r in rows]

    def keys(self, kind: str) -> list[str]:
        q = select(risk_records.c.key).where(risk_records.c.kind == kind).distinct().order_by(risk_records.c.key)
        with self.db.read() as c:
            return list(c.execute(q).scalars())

    # --- runs ---
    def find_run(self, key: str) -> dict | None:
        q = select(risk_runs).where(risk_runs.c.run_key == key).order_by(risk_runs.c.version.desc()).limit(1)
        with self.db.read() as c:
            return self._run(c.execute(q).first())

    def save_run(
        self, kind: str, day, key: str, params: dict, payload: dict, seconds: float, code: str | None = None
    ) -> str:
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
        with self.db.write() as c:
            last = c.execute(
                select(func.max(risk_runs.c.version)).where((risk_runs.c.kind == kind) & (risk_runs.c.day == day))
            ).scalar()
            version = (last or 0) + 1
            run_id = f"{kind}-{day}-v{version}"
            c.execute(
                insert(risk_runs).values(
                    run_id=run_id,
                    kind=kind,
                    day=day,
                    run_key=key,
                    version=version,
                    code=code or code_version(),
                    params=json.dumps(params, default=str),
                    payload=json.dumps(payload),
                    created_at=_now(),
                    seconds=float(seconds),
                )
            )
        return run_id

    def run(self, run_id: str) -> dict:
        with self.db.read() as c:
            row = c.execute(select(risk_runs).where(risk_runs.c.run_id == run_id)).first()
        if row is None:
            raise KeyError(f"no risk run {run_id}")
        out = self._run(row)
        assert out is not None
        return out

    def runs(self, kind: str | None = None, start=None, end=None, latest: bool = True) -> list[dict]:
        """Runs without their payload, by date; with `latest`, only the last version of each date."""
        cols = [c for c in risk_runs.c if c.name != "payload"]
        q = select(*cols)
        if kind:
            q = q.where(risk_runs.c.kind == kind)
        if start:
            q = q.where(risk_runs.c.day >= str(start))
        if end:
            q = q.where(risk_runs.c.day <= str(end))
        with self.db.read() as c:
            rows = [dict(r._mapping) for r in c.execute(q.order_by(risk_runs.c.day, risk_runs.c.version))]
        for r in rows:
            r["params"] = json.loads(r["params"])
        if latest:
            last: dict = {}
            for r in rows:
                last[(r["kind"], r["day"])] = r
            rows = list(last.values())
        return rows

    @staticmethod
    def _run(row) -> dict | None:
        if row is None:
            return None
        d = dict(row._mapping)
        d["params"] = json.loads(d["params"])
        d["payload"] = json.loads(d["payload"])
        return d

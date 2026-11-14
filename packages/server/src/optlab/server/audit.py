"""The audit log (ADR-0020): every write of the API (trades, fixings, scenarios, costs, the market in use, users, job
submissions) and every account command, with who, when, what, and the hashes of the state before and after. The
application only appends to it; reading it is an admin route."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import insert, select

from optlab.server.persistence import Database
from optlab.server.persistence.schema import audit_log


def digest(value: object) -> str | None:
    """sha256 of the canonical JSON of a value, None for None: a change shows without storing the data itself."""
    if value is None:
        return None
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Audit:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        user_id: str | None,
        action: str,
        target: str | None = None,
        before: object = None,
        after: object = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.db.write() as c:
            c.execute(
                insert(audit_log).values(
                    at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                    user_id=user_id,
                    action=action,
                    target=target,
                    before_hash=digest(before),
                    after_hash=digest(after),
                    detail=json.dumps(detail, default=str) if detail else None,
                )
            )

    def entries(self, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        """The latest entries first; `before_id` is the cursor of the next page."""
        q = select(audit_log).order_by(audit_log.c.audit_id.desc()).limit(limit)
        if before_id is not None:
            q = q.where(audit_log.c.audit_id < before_id)
        with self.db.read() as c:
            rows = [dict(r._mapping) for r in c.execute(q)]  # pyright: ignore[reportPrivateUsage] (SQLAlchemy's documented row mapping)
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows

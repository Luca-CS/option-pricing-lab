"""The desk's repository: trades (every version kept), fixings, saved quotes, desk settings and the versioned table of
hedging-cost defaults, on the operational database. The same methods as v0's `server.repository.Repository`; real
fixings from Yahoo are personal-use data, so the database lives under the data folder, never in the repository."""

from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, insert, select, update

from optlab.server.persistence.database import Database
from optlab.server.persistence.schema import cost_defaults, desk_settings, fixings, quotes, trades
from optlab.structured.trade import Trade


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class DeskRepository:
    def __init__(self, db: Database):
        self.db = db

    @property
    def path(self):
        return self.db.path

    # --- trades ---
    def save_trade(self, trade: Trade) -> Trade:
        """Store a trade; an existing id gets a new version (the old ones stay readable)."""
        with self.db.write() as c:
            last = c.execute(select(func.max(trades.c.version)).where(trades.c.trade_id == trade.trade_id)).scalar()
            version = (last or 0) + 1
            stored = Trade.from_json(trade.to_json())
            object.__setattr__(stored, "version", version)
            c.execute(
                insert(trades).values(
                    trade_id=stored.trade_id,
                    version=version,
                    book=stored.book,
                    status=stored.status,
                    payload=stored.to_json(),
                    deleted=0,
                    recorded_at=_now(),
                )
            )
        return stored

    def trade(self, trade_id: str, version: int | None = None) -> Trade:
        q = select(trades.c.payload).where(trades.c.trade_id == trade_id)
        if version is None:
            q = q.where(trades.c.deleted == 0).order_by(trades.c.version.desc()).limit(1)
        else:
            q = q.where(trades.c.version == version)
        with self.db.read() as c:
            payload = c.execute(q).scalar()
        if payload is None:
            raise KeyError(f"no trade {trade_id!r}")
        return Trade.from_json(payload)

    def trades(self, book: str | None = None, status: str | None = None) -> list[Trade]:
        """Latest version of every trade not deleted."""
        last = select(trades.c.trade_id, func.max(trades.c.version).label("v")).group_by(trades.c.trade_id).subquery()
        q = (
            select(trades.c.payload)
            .join(last, (trades.c.trade_id == last.c.trade_id) & (trades.c.version == last.c.v))
            .where(trades.c.deleted == 0)
            .order_by(trades.c.recorded_at)
        )
        with self.db.read() as c:
            out = [Trade.from_json(p) for p in c.execute(q).scalars()]
        return [t for t in out if (book is None or t.book == book) and (status is None or t.status == status)]

    def versions(self, trade_id: str) -> list[dict]:
        """Every stored version of a trade, oldest first, with what changed between them."""
        q = (
            select(trades.c.version, trades.c.payload, trades.c.deleted, trades.c.recorded_at)
            .where(trades.c.trade_id == trade_id)
            .order_by(trades.c.version)
        )
        with self.db.read() as c:
            rows = c.execute(q).all()
        if not rows:
            raise KeyError(f"no trade {trade_id!r}")
        out = []
        for r in rows:
            t = Trade.from_json(r.payload)
            out.append(
                {
                    "version": r.version,
                    "recorded_at": r.recorded_at,
                    "deleted": bool(r.deleted),
                    "quantity": t.quantity,
                    "status": t.status,
                    "book": t.book,
                    "counterparty": t.counterparty,
                }
            )
        return out

    def delete_trade(self, trade_id: str) -> None:
        with self.db.write() as c:
            c.execute(update(trades).where(trades.c.trade_id == trade_id).values(deleted=1))

    def restore_trade(self, trade_id: str) -> None:
        with self.db.write() as c:
            c.execute(update(trades).where(trades.c.trade_id == trade_id).values(deleted=0))

    def new_trade_id(self, prefix: str = "T") -> str:
        with self.db.read() as c:
            n = c.execute(select(func.count(func.distinct(trades.c.trade_id)))).scalar() or 0
        return f"{prefix}-{n + 1:04d}-{uuid.uuid4().hex[:4]}"

    # --- fixings (in the model's scale: the market is rescaled to a spot of 100) ---
    def put_fixings(self, underlying: str, levels: dict, source: str = "manual") -> int:
        rows = [
            {
                "underlying": underlying,
                "date": d.isoformat() if isinstance(d, dt.date) else str(d),
                "level": float(v),
                "source": source,
                "recorded_at": _now(),
            }
            for d, v in levels.items()
        ]
        if rows:
            with self.db.write() as c:
                c.execute(insert(fixings).prefix_with("OR REPLACE"), rows)
        return len(rows)

    def delete_fixing(self, underlying: str, date: str) -> None:
        with self.db.write() as c:
            c.execute(sql_delete(fixings).where((fixings.c.underlying == underlying) & (fixings.c.date == date)))

    def fixings(self, underlying: str | None = None) -> dict:
        """{underlying: {date: level}}."""
        q = select(fixings.c.underlying, fixings.c.date, fixings.c.level)
        if underlying:
            q = q.where(fixings.c.underlying == underlying)
        with self.db.read() as c:
            rows = c.execute(q.order_by(fixings.c.underlying, fixings.c.date)).all()
        out: dict = {}
        for r in rows:
            out.setdefault(r.underlying, {})[dt.date.fromisoformat(r.date)] = r.level
        return out

    # --- quotes ---
    def save_quote(self, sheet: dict, result: dict) -> str:
        qid = uuid.uuid4().hex[:10]
        with self.db.write() as c:
            c.execute(
                insert(quotes).values(
                    quote_id=qid, created_at=_now(), sheet=json.dumps(sheet), result=json.dumps(result)
                )
            )
        return qid

    def quote(self, quote_id: str) -> dict:
        with self.db.read() as c:
            row = c.execute(select(quotes).where(quotes.c.quote_id == quote_id)).first()
        if row is None:
            raise KeyError(f"no quote {quote_id!r}")
        return {
            "quote_id": row.quote_id,
            "created_at": row.created_at,
            "sheet": json.loads(row.sheet),
            "result": json.loads(row.result),
        }

    def quotes(self, limit: int = 20) -> list[dict]:
        with self.db.read() as c:
            rows = c.execute(select(quotes).order_by(quotes.c.created_at.desc()).limit(limit)).all()
        return [
            {
                "quote_id": r.quote_id,
                "created_at": r.created_at,
                "sheet": json.loads(r.sheet),
                "headline": json.loads(r.result).get("headline"),
            }
            for r in rows
        ]

    # --- desk settings (JSON values) ---
    def setting(self, key: str, default=None):
        with self.db.read() as c:
            value = c.execute(select(desk_settings.c.value).where(desk_settings.c.key == key)).scalar()
        return default if value is None else json.loads(value)

    def put_setting(self, key: str, value) -> None:
        with self.db.write() as c:
            c.execute(
                insert(desk_settings)
                .prefix_with("OR REPLACE")
                .values(key=key, value=json.dumps(value), recorded_at=_now())
            )

    # --- defaults of the hedging costs (every version kept) ---
    def save_defaults(self, groups: dict) -> int:
        with self.db.write() as c:
            version = (c.execute(select(func.max(cost_defaults.c.version))).scalar() or 0) + 1
            c.execute(
                insert(cost_defaults).values(
                    version=version, payload=json.dumps({"groups": groups}), recorded_at=_now()
                )
            )
        return version

    def defaults(self, version: int | None = None) -> dict | None:
        """{"version", "groups", "recorded_at"} of the latest table or of a version, None when none was saved."""
        q = select(cost_defaults)
        q = (
            q.order_by(cost_defaults.c.version.desc()).limit(1)
            if version is None
            else q.where(cost_defaults.c.version == version)
        )
        with self.db.read() as c:
            row = c.execute(q).first()
        if row is None:
            return None
        return {"version": row.version, "recorded_at": row.recorded_at, **json.loads(row.payload)}

    def default_versions(self) -> list[dict]:
        with self.db.read() as c:
            rows = c.execute(
                select(cost_defaults.c.version, cost_defaults.c.recorded_at).order_by(cost_defaults.c.version.desc())
            ).all()
        return [{"version": r.version, "recorded_at": r.recorded_at} for r in rows]

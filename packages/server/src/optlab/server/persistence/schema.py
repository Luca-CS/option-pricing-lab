"""The operational database's tables (ADR-0007), in SQLAlchemy Core: one module per domain would be overkill at this
size, so the domains are sections here. Alembic's migrations (`migrations/versions`) create them; a test checks that
the head migration and this metadata describe the same schema.

Payloads stay JSON text, as in v0: the domain objects serialise themselves (trades, snapshots, risk runs), and a
column per field would duplicate their schema for no query that needs it.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, Index, Integer, MetaData, PrimaryKeyConstraint, String, Table, Text

metadata = MetaData()

# --- desk: trades (every version kept), fixings, saved quotes, desk settings, cost defaults (versioned) -------------
trades = Table(
    "trades",
    metadata,
    Column("trade_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("book", String),
    Column("status", String),
    Column("payload", Text, nullable=False),
    Column("deleted", Integer, nullable=False, server_default="0"),
    Column("recorded_at", String, nullable=False),
    PrimaryKeyConstraint("trade_id", "version"),
)
fixings = Table(
    "fixings",
    metadata,
    Column("underlying", String, nullable=False),
    Column("date", String, nullable=False),
    Column("level", Float, nullable=False),
    Column("source", String),
    Column("recorded_at", String, nullable=False),
    PrimaryKeyConstraint("underlying", "date"),
)
quotes = Table(
    "quotes",
    metadata,
    Column("quote_id", String, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("sheet", Text, nullable=False),
    Column("result", Text, nullable=False),
)
desk_settings = Table(
    "desk_settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("recorded_at", String, nullable=False),
)
cost_defaults = Table(
    "cost_defaults",
    metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("payload", Text, nullable=False),
    Column("recorded_at", String, nullable=False),
)

# --- market: snapshots, immutable and content-addressed; the local spot scale beside them --------------------------
snapshots = Table(
    "snapshots",
    metadata,
    Column("snapshot_id", String, primary_key=True),
    Column("underlying", String, nullable=False),
    Column("valuation", String, nullable=False),
    Column("asof", String, nullable=False),
    Column("source", String),
    Column("payload", Text, nullable=False),  # MarketSnapshot.to_json(): the content, without the local scale
    Column("spot_scale", Float),
    Column("recorded_at", String, nullable=False),
)
Index("snapshots_by_name", snapshots.c.underlying, snapshots.c.valuation)

# --- replays of trades on real closes (the desk's; the versioned ones stay files in results/) ----------------------
replays = Table(
    "replays",
    metadata,
    Column("stem", String, primary_key=True),  # replay-<trade>-<model>, as the v0 file name
    Column("payload", Text, nullable=False),
    Column("recorded_at", String, nullable=False),
)

# --- risk: versioned records (scenarios, settings) and runs keyed by what determines them ------------------------
risk_records = Table(
    "risk_records",
    metadata,
    Column("kind", String, nullable=False),
    Column("key", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("meta", Text, nullable=False),
    Column("created_at", String, nullable=False),
    PrimaryKeyConstraint("kind", "key", "version"),
)
risk_runs = Table(
    "risk_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("day", String, nullable=False),
    Column("run_key", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("code", String),
    Column("params", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("seconds", Float),
)
Index("risk_runs_by_day", risk_runs.c.kind, risk_runs.c.day)
Index("risk_runs_by_key", risk_runs.c.run_key)

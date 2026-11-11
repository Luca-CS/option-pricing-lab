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

# --- accounts (ADR-0020): users, sessions (the cookie's hash only), and the audit log, append-only from the app ------
users = Table(
    "users",
    metadata,
    Column("user_id", String, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("password_hash", String, nullable=False),  # argon2id, with its parameters
    Column("roles", Text, nullable=False),  # JSON list
    Column("disabled", Integer, nullable=False, server_default="0"),
    Column("failed_logins", Integer, nullable=False, server_default="0"),
    Column("locked_until", String),
    Column("created_at", String, nullable=False),
    Column("password_changed_at", String, nullable=False),
)
sessions = Table(
    "sessions",
    metadata,
    Column("session_hash", String, primary_key=True),  # sha256 of the cookie's random 256-bit value
    Column("user_id", String, nullable=False),
    Column("csrf_token", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("last_seen", String, nullable=False),
    Column("expires_at", String, nullable=False),  # the absolute expiry; idleness is checked against last_seen
    Column("revoked", Integer, nullable=False, server_default="0"),
)
Index("sessions_by_user", sessions.c.user_id)
audit_log = Table(
    "audit_log",
    metadata,
    Column("audit_id", Integer, primary_key=True, autoincrement=True),
    Column("at", String, nullable=False),
    Column("user_id", String),  # None for the system (a migration, a schedule)
    Column("action", String, nullable=False),  # the route's operation id, or a command
    Column("target", String),
    Column("before_hash", String),
    Column("after_hash", String),
    Column("detail", Text),  # JSON: what the reader of the log needs, never a password or a secret
)
Index("audit_by_time", audit_log.c.at)

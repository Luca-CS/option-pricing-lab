"""The v1 schema: the desk (trades, fixings, quotes, settings, cost defaults), the market snapshots, the replays and the
risk records and runs, in one database (ADR-0007). The v0 stores are imported by `persistence.migrate_v0`.

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("trade_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("book", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("trade_id", "version"),
    )
    op.create_table(
        "fixings",
        sa.Column("underlying", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("level", sa.Float(), nullable=False),
        sa.Column("source", sa.String()),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("underlying", "date"),
    )
    op.create_table(
        "quotes",
        sa.Column("quote_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("sheet", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("quote_id"),
    )
    op.create_table(
        "desk_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "cost_defaults",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("underlying", sa.String(), nullable=False),
        sa.Column("valuation", sa.String(), nullable=False),
        sa.Column("asof", sa.String(), nullable=False),
        sa.Column("source", sa.String()),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("spot_scale", sa.Float()),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index("snapshots_by_name", "snapshots", ["underlying", "valuation"])
    op.create_table(
        "replays",
        sa.Column("stem", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("stem"),
    )
    op.create_table(
        "risk_records",
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "key", "version"),
    )
    op.create_table(
        "risk_runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("run_key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String()),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("seconds", sa.Float()),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("risk_runs_by_day", "risk_runs", ["kind", "day"])
    op.create_index("risk_runs_by_key", "risk_runs", ["run_key"])


def downgrade() -> None:
    op.drop_index("risk_runs_by_key", "risk_runs")
    op.drop_index("risk_runs_by_day", "risk_runs")
    op.drop_index("snapshots_by_name", "snapshots")
    for table in (
        "risk_runs",
        "risk_records",
        "replays",
        "snapshots",
        "cost_defaults",
        "desk_settings",
        "quotes",
        "fixings",
        "trades",
    ):
        op.drop_table(table)

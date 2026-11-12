"""Accounts (ADR-0020): users with argon2id hashes and roles, sessions by the hash of their cookie, the audit log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("roles", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.String()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("password_changed_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "sessions",
        sa.Column("session_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("csrf_token", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_seen", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("revoked", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("session_hash"),
    )
    op.create_index("sessions_by_user", "sessions", ["user_id"])
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", sa.String(), nullable=False),
        sa.Column("user_id", sa.String()),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String()),
        sa.Column("before_hash", sa.String()),
        sa.Column("after_hash", sa.String()),
        sa.Column("detail", sa.Text()),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index("audit_by_time", "audit_log", ["at"])


def downgrade() -> None:
    op.drop_index("audit_by_time", "audit_log")
    op.drop_table("audit_log")
    op.drop_index("sessions_by_user", "sessions")
    op.drop_table("sessions")
    op.drop_table("users")

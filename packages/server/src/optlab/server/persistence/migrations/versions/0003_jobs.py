"""Jobs (ADR-0006): the persistent queue, the workers' leases and the schedules.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("user_id", sa.String()),
        sa.Column("schedule", sa.String()),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("idem_key", sa.String()),
        sa.Column("engine", sa.String()),
        sa.Column("progress", sa.Float()),
        sa.Column("step", sa.String()),
        sa.Column("eta", sa.Float()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String()),
        sa.Column("submitted_at", sa.Float(), nullable=False),
        sa.Column("started_at", sa.Float()),
        sa.Column("finished_at", sa.Float()),
        sa.Column("heartbeat_at", sa.Float()),
        sa.Column("result", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("jobs_by_state", "jobs", ["state", "submitted_at"])
    op.create_index("jobs_by_user", "jobs", ["user_id", "submitted_at"])
    op.create_index("jobs_by_fingerprint", "jobs", ["fingerprint"])
    op.create_index("jobs_by_key", "jobs", ["user_id", "idem_key"], unique=True)
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("engine", sa.String()),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("heartbeat_at", sa.Float(), nullable=False),
        sa.Column("stopped_at", sa.Float()),
        sa.Column("info", sa.Text()),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_table(
        "schedules",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("days", sa.String(), nullable=False),
        sa.Column("at", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.Float()),
        sa.Column("last_job_id", sa.String()),
        sa.Column("next_run_at", sa.Float()),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String()),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_table("workers")
    op.drop_index("jobs_by_key", "jobs")
    op.drop_index("jobs_by_fingerprint", "jobs")
    op.drop_index("jobs_by_user", "jobs")
    op.drop_index("jobs_by_state", "jobs")
    op.drop_table("jobs")

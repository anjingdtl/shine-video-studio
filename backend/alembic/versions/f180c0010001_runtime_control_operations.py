"""Runtime control operations: durable lifecycle intents, executed by the Worker.

One flat table (plan §5.2).  The API inserts intents; the resident Worker
claims them with a compare-and-set and records the terminal outcome.  No
process truth is persisted here: jobs, attempts, leases and OS identities
stay authoritative in their existing tables.

Revision ID: f180c0010001
Revises: f180b0080001
Create Date: 2026-11-11
"""

import sqlalchemy as sa

from alembic import op

revision = "f180c0010001"
down_revision = "f180b0080001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_control_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("installation_id", sa.String(96), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("owner_worker_id", sa.String(96), nullable=True),
        sa.Column("owner_birth", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(160), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("runtime_control_operations")

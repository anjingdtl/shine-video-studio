"""Persistent multi-step support delegations.

Delegation used to live only in ``support_sessions.context_json``, so a
concurrent authorization or a re-read in a new database session could create a
second delegation and resubmit work.  This additive table stores the approved
plan identity, its authorization key and the walk state in one row; the
``authorization_key`` unique constraint is the database-level idempotency gate.

Revision ID: f180f0010005
Revises: f180f0010004
Create Date: 2026-11-17
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010005"
down_revision = "f180f0010004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "support_delegations" in tables:
        return
    op.create_table(
        "support_delegations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=48), nullable=True),
        sa.Column("plan_hash", sa.String(length=64), nullable=True),
        sa.Column("authorization_key", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("waiting_action_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "authorization_key", name="uq_support_delegation_authorization"
        ),
    )
    op.create_index("ix_support_delegations_session_id", "support_delegations", ["session_id"])
    op.create_index(
        "ix_support_delegations_session_created",
        "support_delegations",
        ["session_id", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "support_delegations" in tables:
        op.drop_table("support_delegations")

"""Retryable support-action attempts.

``request_identity`` used to double as "the one and only attempt of this request",
so a failed, expired or rolled-back action kept blocking a legitimate new
preview forever.  The two questions are different:

* *request idempotency* — the same live request must return the same envelope;
* *attempt lifecycle* — once an attempt is terminal, a person may preview again.

This revision adds the stable ``request_key`` plus a monotonic ``attempt_no``;
``request_identity`` stays unique and is now ``key:attempt``.

Revision ID: f180f0010003
Revises: f180f0010002
Create Date: 2026-11-16
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010003"
down_revision = "f180f0010002"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {row["name"] for row in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    columns = _columns(bind, "support_actions")
    if not columns:
        return
    if "request_key" not in columns:
        op.add_column("support_actions", sa.Column("request_key", sa.String(length=80), nullable=True))
    if "attempt_no" not in columns:
        op.add_column(
            "support_actions",
            sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        )
    # Existing rows are their own first attempt.
    op.execute("UPDATE support_actions SET request_key = request_identity WHERE request_key IS NULL")
    with op.batch_alter_table("support_actions") as batch:
        batch.alter_column("request_key", existing_type=sa.String(length=80), nullable=False)
        batch.alter_column("request_identity", existing_type=sa.String(length=80),
                           type_=sa.String(length=96), existing_nullable=False)
    inspector = sa.inspect(bind)
    indexes = {row["name"] for row in inspector.get_indexes("support_actions")}
    if "ix_support_actions_request_key" not in indexes:
        op.create_index("ix_support_actions_request_key", "support_actions", ["request_key"])


def downgrade():
    bind = op.get_bind()
    columns = _columns(bind, "support_actions")
    if not columns:
        return
    inspector = sa.inspect(bind)
    indexes = {row["name"] for row in inspector.get_indexes("support_actions")}
    if "ix_support_actions_request_key" in indexes:
        op.drop_index("ix_support_actions_request_key", table_name="support_actions")
    if "attempt_no" in columns:
        op.drop_column("support_actions", "attempt_no")
    if "request_key" in columns:
        op.drop_column("support_actions", "request_key")

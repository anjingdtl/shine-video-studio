"""V3.1 task-level grant envelope on the existing support tables.

One person clicks once; the copilot keeps working inside a frozen boundary.
That boundary is stored where it already belongs - on ``support_delegations``
(the authorization ledger) - and each autonomous sub-action is a normal
``support_actions`` row carrying its grant identity.  No new production table,
no second task system.

Revision ID: f180f0010007
Revises: f180f0010006
Create Date: 2026-11-19
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010007"
down_revision = "f180f0010006"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table)}


def _uniques(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table)
        if constraint.get("name")
    }


def upgrade():
    delegation_columns = _columns("support_delegations")
    with op.batch_alter_table("support_delegations") as batch:
        if "grant_id" not in delegation_columns:
            batch.add_column(sa.Column("grant_id", sa.String(length=64), nullable=True))
        if "grant_digest" not in delegation_columns:
            batch.add_column(sa.Column("grant_digest", sa.String(length=64), nullable=True))
        if "expires_at" not in delegation_columns:
            batch.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
        if "budget_version" not in delegation_columns:
            batch.add_column(
                sa.Column(
                    "budget_version",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
    if "ix_support_delegations_grant" not in _indexes("support_delegations"):
        op.create_index(
            "ix_support_delegations_grant",
            "support_delegations",
            ["grant_id", "status"],
            unique=False,
        )

    action_columns = _columns("support_actions")
    with op.batch_alter_table("support_actions") as batch:
        if "grant_id" not in action_columns:
            batch.add_column(sa.Column("grant_id", sa.String(length=64), nullable=True))
        if "tool_call_id" not in action_columns:
            batch.add_column(sa.Column("tool_call_id", sa.String(length=96), nullable=True))
        if "grant_tool_key" not in action_columns:
            batch.add_column(sa.Column("grant_tool_key", sa.String(length=96), nullable=True))
    if "ix_support_actions_grant" not in _indexes("support_actions"):
        op.create_index(
            "ix_support_actions_grant",
            "support_actions",
            ["grant_id", "created_at"],
            unique=False,
        )
    # SQLite treats NULLs as distinct, so existing per-click rows (all NULL)
    # keep passing while two autonomous actions for one tool call collide.
    if "uq_support_action_grant_tool" not in _uniques("support_actions"):
        with op.batch_alter_table("support_actions") as batch:
            batch.create_unique_constraint(
                "uq_support_action_grant_tool", ["grant_tool_key"]
            )


def downgrade():
    action_uniques = _uniques("support_actions")
    if "uq_support_action_grant_tool" in action_uniques:
        with op.batch_alter_table("support_actions") as batch:
            batch.drop_constraint("uq_support_action_grant_tool", type_="unique")
    if "ix_support_actions_grant" in _indexes("support_actions"):
        op.drop_index("ix_support_actions_grant", table_name="support_actions")
    action_columns = _columns("support_actions")
    with op.batch_alter_table("support_actions") as batch:
        for name in ("grant_tool_key", "tool_call_id", "grant_id"):
            if name in action_columns:
                batch.drop_column(name)

    if "ix_support_delegations_grant" in _indexes("support_delegations"):
        op.drop_index("ix_support_delegations_grant", table_name="support_delegations")
    delegation_columns = _columns("support_delegations")
    with op.batch_alter_table("support_delegations") as batch:
        for name in ("budget_version", "expires_at", "grant_digest", "grant_id"):
            if name in delegation_columns:
                batch.drop_column(name)

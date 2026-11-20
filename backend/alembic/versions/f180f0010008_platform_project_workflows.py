"""Platform project workflow selection registry (director7 | manual4).

Additive platform table only: existing seven-stage tables and historical rows
are untouched. Projects without a row in ``project_workflows`` implicitly
belong to ``director7``.

Revision ID: f180f0010008
Revises: f180f0010007
Create Date: 2026-11-20
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010008"
down_revision = "f180f0010007"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade():
    if "project_workflows" not in _tables():
        op.create_table(
            "project_workflows",
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("workflow_kind", sa.String(length=24), nullable=False, server_default=sa.text("'director7'")),
            sa.Column("selection_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    if "project_workflows" in _tables():
        op.drop_table("project_workflows")

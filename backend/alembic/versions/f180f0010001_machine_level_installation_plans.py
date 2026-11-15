"""Machine-level installation plans.

Resource preparation belongs to the machine, not to a work: an empty database
must be able to download, verify and register the pinned bytes before any
project exists.  ``installation_plans.project_id`` therefore becomes nullable;
a NULL row is a machine-level plan.  Existing rows keep their project identity
and every existing query by project continues to work unchanged.

Revision ID: f180f0010001
Revises: f180e0010001
Create Date: 2026-11-15
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010001"
down_revision = "f180e0010001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "installation_plans" not in set(sa.inspect(bind).get_table_names()):
        return
    with op.batch_alter_table("installation_plans") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )


def downgrade():
    bind = op.get_bind()
    if "installation_plans" not in set(sa.inspect(bind).get_table_names()):
        return
    # A machine-level plan has no project to go back to; downgrading keeps the
    # rows but refuses to invent a project identity for them.
    with op.batch_alter_table("installation_plans") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
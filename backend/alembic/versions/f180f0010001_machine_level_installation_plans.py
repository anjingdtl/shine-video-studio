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


def _rebuild_nullable(bind, nullable: bool) -> None:
    # installation_attempts references installation_plans, so SQLite's batch
    # rebuild (create-copy-drop-rename) is refused while foreign keys are
    # enforced.  PRAGMA foreign_keys is a no-op inside a transaction, so switch
    # the connection to AUTOCOMMIT (which ends any pending transaction), disable
    # enforcement for the rebuild, then restore it.  No rows are deleted; the
    # copy preserves every plan.
    bind.commit()  # end SQLAlchemy's autobegin transaction before switching mode
    bind.execution_options(isolation_level="AUTOCOMMIT")
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("installation_plans") as batch:
            batch.alter_column(
                "project_id",
                existing_type=sa.String(length=36),
                nullable=nullable,
            )
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade():
    bind = op.get_bind()
    if "installation_plans" not in set(sa.inspect(bind).get_table_names()):
        return
    _rebuild_nullable(bind, True)


def downgrade():
    bind = op.get_bind()
    if "installation_plans" not in set(sa.inspect(bind).get_table_names()):
        return
    # A machine-level plan has no project to go back to; refuse to invent one.
    null_rows = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM installation_plans WHERE project_id IS NULL"
    ).scalar()
    if null_rows:
        raise RuntimeError(
            "存在机器级安装计划(project_id 为空)，无法回退到非空约束；请先处理这些计划。"
        )
    _rebuild_nullable(bind, False)
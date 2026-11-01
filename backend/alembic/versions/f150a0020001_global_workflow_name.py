"""Preserve global workflow-name uniqueness under concurrent inserts."""

import sqlalchemy as sa

from alembic import op

revision = "f150a0020001"
down_revision = "f150a0010001"
branch_labels = None
depends_on = None


def upgrade():
    # Do not rename, merge or delete conflicting historical profiles. Their
    # ids may be pinned by old jobs and require explicit reconciliation.
    collision = op.get_bind().execute(sa.text(
        "SELECT name FROM workflow_profiles WHERE project_id IS NULL "
        "GROUP BY name HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if collision is not None:
        raise RuntimeError("GLOBAL_WORKFLOW_NAME_CONFLICT: reconcile existing global profiles; no rows changed")
    op.create_index("uq_workflow_profile_global_name", "workflow_profiles", ["name"],
                    unique=True, sqlite_where=sa.text("project_id IS NULL"))


def downgrade():
    op.drop_index("uq_workflow_profile_global_name", table_name="workflow_profiles")

"""Scope workflow-profile name uniqueness to a project.

The new-machine wizard registered the two default Ref2VA profiles under a
global name check (api_setup.py) while binding them to the registering
project.  A second project could then not register its own usable copies:
the wizard answered ALREADY_REGISTERED with the first project's private
profile id, and the second project's list stayed empty.

Name uniqueness now follows the same visibility rule the list endpoint
already had (a project sees its own profiles plus global ones): the same
name may exist once per project and once globally.

Historical rows are copied unchanged by batch mode; ids, graphs and
historical job references are preserved.
"""

import sqlalchemy as sa

from alembic import op

revision = "f150a0010001"
down_revision = "f140a0030001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("workflow_profiles") as batch:
        batch.drop_constraint("uq_workflow_profile_name", type_="unique")
        batch.create_unique_constraint("uq_workflow_profile_project_name", ["project_id", "name"])


def downgrade():
    if _has_colliding_names(op.get_bind()):
        raise RuntimeError(
            "Cannot downgrade: project-scoped profile names collide with global profile"
            " names; delete or rename the colliding profiles first."
        )
    with op.batch_alter_table("workflow_profiles") as batch:
        batch.drop_constraint("uq_workflow_profile_project_name", type_="unique")
        batch.create_unique_constraint("uq_workflow_profile_name", ["name"])


def _has_colliding_names(bind):
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM workflow_profiles WHERE project_id IS NOT NULL AND name IN"
                " (SELECT name FROM workflow_profiles WHERE project_id IS NULL) LIMIT 1"
            )
        ).first()
        is not None
    )

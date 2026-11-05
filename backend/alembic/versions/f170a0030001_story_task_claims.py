"""Worker claims for document/outline/section tasks on the existing agent ledger."""
import sqlalchemy as sa

from alembic import op

revision = "f170a0030001"
down_revision = "f170a0020001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_task_records", sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_task_records", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column("agent_task_records", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("agent_task_records", "lease_expires_at")
    op.drop_column("agent_task_records", "lease_owner")
    op.drop_column("agent_task_records", "state_version")

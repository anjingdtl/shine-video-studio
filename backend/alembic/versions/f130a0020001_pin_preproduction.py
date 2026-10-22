"""Bind queued jobs to the exact human-frozen pre-production manifest."""

import sqlalchemy as sa

from alembic import op

revision = "f130a0020001"
down_revision = "f130a0010001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("preproduction_manifest_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("jobs", "preproduction_manifest_hash")

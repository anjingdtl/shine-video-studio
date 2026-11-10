"""Record deleted video bytes without rewriting the job/delivery history.

Deleting a history video removes the file from disk; the artifact row stays as
a tombstone so the job, delivery receipt and audit trail keep pointing at the
same identity instead of silently changing into another version.

Revision ID: f180b0070001
Revises: f180b0060001
Create Date: 2026-11-10
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0070001"
down_revision = "f180b0060001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_artifacts") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("deleted_by", sa.String(64), nullable=True))
        batch.add_column(sa.Column("deleted_reason", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("media_artifacts") as batch:
        batch.drop_column("deleted_reason")
        batch.drop_column("deleted_by")
        batch.drop_column("deleted_at")

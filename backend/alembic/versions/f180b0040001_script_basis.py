"""Pin the project's explicitly chosen script basis.

Revision ID: f180b0040001
Revises: f180b0030001
Create Date: 2026-11-09

An imported document is a candidate until the person selects it or confirms
an outline built from it.  The package records that basis, and readiness
compares the frozen basis with this column.  Existing projects keep ``NULL``
and therefore their original "newest document" behaviour until the next
explicit choice.
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0040001"
down_revision = "f180b0030001"
branch_labels = None
depends_on = None


def upgrade():
    # A plain ADD COLUMN: SQLite supports it natively, and a batch rebuild of
    # ``projects`` would drop the old table while child rows reference it with
    # ON DELETE CASCADE, silently deleting a project's workflow profiles.
    op.add_column("projects", sa.Column("current_document_id", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("projects", "current_document_id")

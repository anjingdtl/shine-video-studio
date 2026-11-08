"""Record the caller's request identity on the job itself.

Revision ID: f180b0050001
Revises: f180b0040001
Create Date: 2026-11-09

A repeated submission is compared by what the caller asked for.  That identity
belongs on the job row: putting it inside ``jobs.request`` made the stored
payload a non-RenderRequest document and the Worker refused to validate it.
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0050001"
down_revision = "f180b0040001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("request_fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("jobs", "request_fingerprint")

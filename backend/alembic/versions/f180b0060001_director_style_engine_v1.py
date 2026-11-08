"""Add immutable director style provenance to existing StyleBible versions.

Revision ID: f180b0060001
Revises: f180b0050001
Create Date: 2026-11-09
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0060001"
down_revision = "f180b0050001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("director_style_draft", sa.JSON(), nullable=True))
    op.add_column("style_versions", sa.Column("director_style_binding", sa.JSON(), nullable=True))
    op.create_table(
        "director_style_profile_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("canonical_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_director_style_profile_version"),
    )
    op.create_index(
        "ix_director_style_profile_revisions_profile_id",
        "director_style_profile_revisions",
        ["profile_id"],
    )


def downgrade():
    op.drop_index("ix_director_style_profile_revisions_profile_id", table_name="director_style_profile_revisions")
    op.drop_table("director_style_profile_revisions")
    op.drop_column("style_versions", "director_style_binding")
    op.drop_column("projects", "director_style_draft")

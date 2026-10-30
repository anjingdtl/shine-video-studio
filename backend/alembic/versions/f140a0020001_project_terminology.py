"""The script terminology and the external subtitle layer.

A Chinese recognizer reads a proper name far more reliably when it is told
which spelling the script uses.  The terminology table is project data that a
human maintains; it is a hint for the recognizer, never a rewrite of a
transcript.

The subtitles live outside the rendered shot, so a human can revise a line or
a timing without rendering the shot again, and the delivery composites the
revision that was approved.

Both tables are additive: no historical row is touched.
"""

import sqlalchemy as sa

from alembic import op

revision = "f140a0020001"
down_revision = "f140a0010001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_terminology",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="human"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "revision", name="uq_project_terminology_revision"),
    )
    op.create_index("ix_project_terminology_project_id", "project_terminology", ["project_id"])
    op.create_table(
        "subtitle_tracks",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
        sa.Column("cues", sa.JSON(), nullable=False),
        sa.Column("srt", sa.Text(), nullable=False, server_default=""),
        sa.Column("ass", sa.Text(), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="alignment"),
        sa.Column("source_artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "revision", name="uq_subtitle_track_revision"),
    )
    op.create_index("ix_subtitle_tracks_project_id", "subtitle_tracks", ["project_id"])


def downgrade():
    op.drop_index("ix_subtitle_tracks_project_id", table_name="subtitle_tracks")
    op.drop_table("subtitle_tracks")
    op.drop_index("ix_project_terminology_project_id", table_name="project_terminology")
    op.drop_table("project_terminology")

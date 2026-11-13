"""Keep user-authored shot prompts, shot subtitles and per-episode subtitle scope.

A person may now write a shot's H3 prompt themselves, configure subtitle lines
per shot, and keep each episode's subtitle revision separate.  All three are new
ledgers; no existing row is rewritten and every table keeps its history.

Revision ID: f180c0030001
Revises: f180c0020001
Create Date: 2026-11-13
"""

import sqlalchemy as sa

from alembic import op

revision = "f180c0030001"
down_revision = "f180c0020001"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    """The tables this database already has.

    A ledger that was created by the current models (``create_all``) already
    carries these tables; a legacy ledger does not.  The migration has to be
    honest about both, so it reads the real schema instead of assuming.
    """
    return set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column in row["name"] for row in inspector.get_columns(table))


def upgrade():
    tables = _existing_tables()
    if "shot_prompt_revisions" not in tables:
        op.create_table(
            "shot_prompt_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shot_revision", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("sections", sa.JSON(), nullable=False),
            sa.Column("prompt_sha256", sa.String(64), nullable=False),
            sa.Column("reference_set", sa.JSON(), nullable=False),
            sa.Column("content_signature", sa.JSON(), nullable=False),
            sa.Column("manifest_sha256", sa.String(64), nullable=False),
            sa.Column("spec_report", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("frozen_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("shot_id", "revision", name="uq_shot_prompt_revision"),
        )
        op.create_index("ix_shot_prompt_revisions_shot_id", "shot_prompt_revisions", ["shot_id"])
        op.create_index("ix_shot_prompt_revisions_project_id", "shot_prompt_revisions", ["project_id"])

    if "shot_subtitles" not in tables:
        op.create_table(
            "shot_subtitles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("episode_id", sa.String(80), nullable=False),
            sa.Column("lines", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("frozen_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("shot_id", name="uq_shot_subtitle_shot"),
        )
        op.create_index("ix_shot_subtitles_project_id", "shot_subtitles", ["project_id"])
        op.create_index("ix_shot_subtitles_episode_id", "shot_subtitles", ["episode_id"])

    if "subtitle_tracks" in tables and not _has_column("subtitle_tracks", "episode_id"):
        with op.batch_alter_table("subtitle_tracks") as batch:
            batch.add_column(sa.Column("episode_id", sa.String(80), nullable=True))


def downgrade():
    tables = _existing_tables()
    if "subtitle_tracks" in tables and _has_column("subtitle_tracks", "episode_id"):
        with op.batch_alter_table("subtitle_tracks") as batch:
            batch.drop_column("episode_id")
    if "shot_subtitles" in tables:
        op.drop_index("ix_shot_subtitles_episode_id", table_name="shot_subtitles")
        op.drop_index("ix_shot_subtitles_project_id", table_name="shot_subtitles")
        op.drop_table("shot_subtitles")
    if "shot_prompt_revisions" in tables:
        op.drop_index("ix_shot_prompt_revisions_project_id", table_name="shot_prompt_revisions")
        op.drop_index("ix_shot_prompt_revisions_shot_id", table_name="shot_prompt_revisions")
        op.drop_table("shot_prompt_revisions")

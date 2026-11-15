"""Explicit human acceptance of delivered reels.

Technical delivery is not artistic acceptance: a reel can be assembled,
probed and delivered while every semantic review is still pending.  This
migration adds the append-only ledger where the person's watched-and-decided
answer lives, so the workbench's "待你确认" has a real action behind it.
Nothing existing is rewritten: assembly provenance, jobs and gates stay as
they were.

Revision ID: f180e0010001
Revises: f180d0010001
Create Date: 2026-11-15
"""

import sqlalchemy as sa

from alembic import op

revision = "f180e0010001"
down_revision = "f180d0010001"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if "delivery_acceptances" in _existing_tables():
        return
    op.create_table(
        "delivery_acceptances",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("artifact_id", sa.String(length=36),
                  sa.ForeignKey("media_artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("watched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_delivery_acceptances_artifact", "delivery_acceptances",
                    ["artifact_id", "created_at"])


def downgrade():
    if "delivery_acceptances" not in _existing_tables():
        return
    op.drop_index("ix_delivery_acceptances_artifact", table_name="delivery_acceptances")
    op.drop_table("delivery_acceptances")
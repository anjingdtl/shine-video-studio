"""V2.2 copilot persona and local long-term memory.

Adds two ordinary SQLite tables (authoritative memories and a rebuildable
vector index) in the existing metadata database.  An FTS5 virtual table is
created when the local SQLite build supports it; the application reports the
real capability and falls back to bounded LIKE recall otherwise.

Revision ID: f180f0010006
Revises: f180f0010005
Create Date: 2026-11-17
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010006"
down_revision = "f180f0010005"
branch_labels = None
depends_on = None

FTS_TABLE = "copilot_memory_fts"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "copilot_memories" not in tables:
        op.create_table(
            "copilot_memories",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("scope", sa.String(length=16), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("memory_type", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("source_session_id", sa.String(length=36), nullable=True),
            sa.Column("source_message_ids", sa.JSON(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("content_revision", sa.Integer(), nullable=False),
            sa.Column("embedding_revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("confirmed", sa.Boolean(), nullable=False),
            sa.Column("supersedes_id", sa.String(length=36), nullable=True),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_reason", sa.String(length=48), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_copilot_memories_project_id", "copilot_memories", ["project_id"])
        op.create_index(
            "ix_copilot_memories_scope_project",
            "copilot_memories",
            ["scope", "project_id", "status"],
        )
        op.create_index(
            "ix_copilot_memories_status_updated",
            "copilot_memories",
            ["status", "updated_at"],
        )
        op.create_index("ix_copilot_memories_content_hash", "copilot_memories", ["content_hash"])
        op.create_index("ix_copilot_memories_source", "copilot_memories", ["source", "status"])

    if "copilot_memory_vectors" not in tables:
        op.create_table(
            "copilot_memory_vectors",
            sa.Column("memory_id", sa.String(length=36), primary_key=True),
            sa.Column("content_revision", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("dim", sa.Integer(), nullable=False),
            sa.Column("vector", sa.LargeBinary(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["memory_id"], ["copilot_memories.id"], ondelete="CASCADE"
            ),
        )

    # FTS5 is optional in a bundled SQLite build.  The migration records the
    # honest capability by creating the index only when the engine supports it;
    # the runtime also retries lazily and reports UNAVAILABLE instead of failing.
    try:
        bind.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
            "USING fts5(memory_id UNINDEXED, content, tokenize='unicode61')"
        )
    except Exception:  # noqa: BLE001 - FTS5 absence is a supported degraded mode
        pass


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    try:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {FTS_TABLE}")
    except Exception:  # noqa: BLE001
        pass
    if "copilot_memory_vectors" in tables:
        op.drop_table("copilot_memory_vectors")
    if "copilot_memories" in tables:
        op.drop_table("copilot_memories")

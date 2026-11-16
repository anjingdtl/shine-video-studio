"""AI copilot support ledger.

Adds the four additive tables the support agent needs: bounded sessions,
messages, previewed/approved actions and engineering reports.  Nothing existing
is altered; a database that already has them (created by ``Base.metadata`` in a
test fixture) is left untouched.

Revision ID: f180f0010002
Revises: f180f0010001
Create Date: 2026-11-16
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010002"
down_revision = "f180f0010001"
branch_labels = None
depends_on = None


def _existing_tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    bind = op.get_bind()
    tables = _existing_tables(bind)
    if "support_sessions" not in tables:
        op.create_table(
            "support_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("page_id", sa.String(length=48), nullable=False),
            sa.Column("episode_id", sa.String(length=80), nullable=True),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("attempt_id", sa.String(length=36), nullable=True),
            sa.Column("task_id", sa.String(length=80), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("actor", sa.String(length=64), nullable=False),
            sa.Column("context_json", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_support_sessions_project_id", "support_sessions", ["project_id"])
    if "support_messages" not in tables:
        op.create_table(
            "support_messages",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(length=36),
                sa.ForeignKey("support_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_support_messages_session_id", "support_messages", ["session_id"])
        op.create_index(
            "ix_support_messages_session_created", "support_messages", ["session_id", "created_at"]
        )
    if "support_actions" not in tables:
        op.create_table(
            "support_actions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("kind", sa.String(length=48), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("request_identity", sa.String(length=80), nullable=False),
            sa.Column("preview_hash", sa.String(length=64), nullable=False),
            sa.Column("snapshot_revision", sa.String(length=64), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("preview", sa.JSON(), nullable=False),
            sa.Column("actor", sa.String(length=64), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("original_task_id", sa.String(length=80), nullable=True),
            sa.Column("verification", sa.JSON(), nullable=False),
            sa.Column("last_error", sa.String(length=300), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("request_identity", name="uq_support_action_identity"),
        )
        op.create_index("ix_support_actions_session_id", "support_actions", ["session_id"])
        op.create_index("ix_support_actions_project_id", "support_actions", ["project_id"])
    if "support_reports" not in tables:
        op.create_table(
            "support_reports",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("filename", sa.String(length=200), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_support_reports_session_id", "support_reports", ["session_id"])
        op.create_index("ix_support_reports_project_id", "support_reports", ["project_id"])
        op.create_index("ix_support_reports_fingerprint", "support_reports", ["fingerprint"])


def downgrade():
    bind = op.get_bind()
    tables = _existing_tables(bind)
    for name in ("support_reports", "support_actions", "support_messages", "support_sessions"):
        if name in tables:
            op.drop_table(name)

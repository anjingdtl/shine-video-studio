"""Persistent material agent input identity; preserve every historical task."""

import sqlalchemy as sa

from alembic import op

revision = "f170a0020001"
down_revision = "f170a0010001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_task_records", sa.Column("request_identity", sa.String(64), nullable=True))
    op.create_index("uq_agent_task_request_identity", "agent_task_records", ["request_identity"], unique=True)
    op.create_table(
        "material_text_consents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("role", sa.String(48), nullable=False),
        sa.Column("provider_fingerprint", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("material_text_consents")
    op.drop_index("uq_agent_task_request_identity", table_name="agent_task_records")
    op.drop_column("agent_task_records", "request_identity")

"""Add immutable film crew proposals and per-project rule locks; preserve existing rows."""

import sqlalchemy as sa

from alembic import op

revision = "e8f1a2b3c4d5"
down_revision = "c3d81f6a2b45"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rule_packs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "project_rule_locks",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("rulepack_id", sa.String(80), sa.ForeignKey("rule_packs.id"), nullable=False),
        sa.Column("rulepack_hash", sa.String(64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "agent_task_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("role_id", sa.String(48), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("style_version_id", sa.String(36), nullable=True),
        sa.Column("asset_manifest_hash", sa.String(64), nullable=False),
        sa.Column("rulepack_id", sa.String(80), nullable=False),
        sa.Column("rulepack_hash", sa.String(64), nullable=False),
        sa.Column("model_profile_id", sa.String(36), nullable=False),
        sa.Column("model_id", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_task_records_project_id", "agent_task_records", ["project_id"])
    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("producer_role", sa.String(48), nullable=False),
        sa.Column("source_task_id", sa.String(36), sa.ForeignKey("agent_task_records.id"), nullable=False),
        sa.Column("input_revision_ids", sa.JSON(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_artifacts_project_id", "agent_artifacts", ["project_id"])


def downgrade():
    op.drop_table("agent_artifacts")
    op.drop_table("agent_task_records")
    op.drop_table("project_rule_locks")
    op.drop_table("rule_packs")

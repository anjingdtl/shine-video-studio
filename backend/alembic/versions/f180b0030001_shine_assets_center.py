"""Add the projectless asset library and its shared-kernel ledgers.

Revision ID: f180b0030001
Revises: f180b0020001
Create Date: 2026-11-08
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0030001"
down_revision = "f180b0020001"
branch_labels = None
depends_on = None


def upgrade():
    # RightsRecord is project-scoped provenance. When a project is deleted,
    # its project-local assets and rights snapshot are removed together; the
    # global asset/version/rights rows remain independent.
    with op.batch_alter_table(
        "rights_records",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        batch_op.drop_constraint("fk_rights_records_project_id_projects", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_rights_records_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "library_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("collection", sa.String(120), nullable=True),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_library_assets_kind_category_status", "library_assets", ["kind", "category", "status"])
    op.create_index("ix_library_assets_created_at", "library_assets", ["created_at"])

    op.create_table(
        "library_rights",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("license_kind", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("allowed_project_ids", sa.JSON(), nullable=False),
        sa.Column("permitted_uses", sa.JSON(), nullable=False),
        sa.Column("commercial_allowed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("recorded_actor", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_library_rights_status_expiry", "library_rights", ["status", "expires_at"])

    op.create_table(
        "library_media",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("rights_id", sa.String(36), sa.ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_library_media_sha256", "library_media", ["sha256"])
    op.create_index("ix_library_media_purpose_kind", "library_media", ["purpose", "kind"])

    op.create_table(
        "library_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "library_asset_id", sa.String(36), sa.ForeignKey("library_assets.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "parent_version_id", sa.String(36), sa.ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("source_mode", sa.String(24), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=True),
        sa.Column("workflow_revision_hash", sa.String(64), nullable=True),
        sa.Column("references_manifest", sa.JSON(), nullable=False),
        sa.Column("references_manifest_hash", sa.String(64), nullable=True),
        sa.Column("rights_id", sa.String(36), sa.ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("qc", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("library_asset_id", "ordinal", name="uq_library_version_ordinal"),
    )
    op.create_index("ix_library_versions_library_asset_id", "library_versions", ["library_asset_id"])
    op.create_index("ix_library_versions_sha256", "library_versions", ["content_sha256"])
    op.create_index("ix_library_versions_asset_status", "library_versions", ["library_asset_id", "status"])

    op.create_table(
        "library_source_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "version_id", sa.String(36), sa.ForeignKey("library_versions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("slot_role", sa.String(32), nullable=False),
        sa.Column("media_id", sa.String(36), sa.ForeignKey("library_media.id", ondelete="RESTRICT"), nullable=True),
        sa.Column(
            "source_version_id", sa.String(36), sa.ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mask_sha256", sa.String(64), nullable=True),
        sa.Column("rights_snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_library_source_reference_order"),
    )
    op.create_index("ix_library_source_references_version_id", "library_source_references", ["version_id"])
    op.create_index("ix_library_source_media", "library_source_references", ["media_id"])

    op.create_table(
        "library_render_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("workflow_revision_hash", sa.String(64), nullable=True),
        sa.Column("runtime_fingerprint", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("submit_intent_at", sa.DateTime(), nullable=True),
        sa.Column("external_prompt_id", sa.String(120), nullable=True),
        sa.Column("external_outcome", sa.String(24), nullable=False),
        sa.Column(
            "result_version_id", sa.String(36), sa.ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_library_task_idempotency"),
    )
    op.create_index("ix_library_tasks_status_created", "library_render_tasks", ["status", "created_at"])
    op.create_index("ix_library_tasks_external_prompt", "library_render_tasks", ["external_prompt_id"])

    op.create_table(
        "library_task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("library_render_tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("submit_stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("external_prompt_id", sa.String(120), nullable=True),
        sa.Column("external_outcome", sa.String(24), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_library_task_attempt_no"),
    )
    op.create_index("ix_library_task_attempts_task_id", "library_task_attempts", ["task_id"])

    op.create_table(
        "library_project_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "library_asset_id", sa.String(36), sa.ForeignKey("library_assets.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "library_version_id",
            sa.String(36),
            sa.ForeignKey("library_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("project_asset_id", sa.String(36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_voice_profile_id",
            sa.String(36),
            sa.ForeignKey("voice_profiles.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=True),
        sa.Column("entity_key", sa.String(80), nullable=False),
        sa.Column("usage_scope", sa.String(32), nullable=False),
        sa.Column("rights_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "library_version_id",
            "role",
            "entity_key",
            "usage_scope",
            name="uq_library_project_semantic_assignment",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_library_project_link_idempotency"),
    )
    op.create_index("ix_library_links_project", "library_project_links", ["project_id"])
    op.create_index(
        "ix_library_links_asset_version", "library_project_links", ["library_asset_id", "library_version_id"]
    )

    op.create_table(
        "library_voice_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "library_asset_id", sa.String(36), sa.ForeignKey("library_assets.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "version_id", sa.String(36), sa.ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "conditioning_media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("voice_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("rights_id", sa.String(36), sa.ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("frozen_by", sa.String(64), nullable=True),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("library_asset_id", "version_id", name="uq_library_voice_version"),
    )
    op.create_index("ix_library_voice_status", "library_voice_profiles", ["status"])

    op.create_table(
        "library_approval_grants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_library_grant_target_action", "library_approval_grants", ["target_id", "action"])


def downgrade():
    op.drop_index("ix_library_grant_target_action", table_name="library_approval_grants")
    op.drop_table("library_approval_grants")
    op.drop_index("ix_library_voice_status", table_name="library_voice_profiles")
    op.drop_table("library_voice_profiles")
    op.drop_index("ix_library_links_asset_version", table_name="library_project_links")
    op.drop_index("ix_library_links_project", table_name="library_project_links")
    op.drop_table("library_project_links")
    with op.batch_alter_table(
        "rights_records",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        batch_op.drop_constraint("fk_rights_records_project_id_projects", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_rights_records_project_id_projects", "projects", ["project_id"], ["id"]
        )
    op.drop_index("ix_library_task_attempts_task_id", table_name="library_task_attempts")
    op.drop_table("library_task_attempts")
    op.drop_index("ix_library_tasks_external_prompt", table_name="library_render_tasks")
    op.drop_index("ix_library_tasks_status_created", table_name="library_render_tasks")
    op.drop_table("library_render_tasks")
    op.drop_index("ix_library_source_media", table_name="library_source_references")
    op.drop_index("ix_library_source_references_version_id", table_name="library_source_references")
    op.drop_table("library_source_references")
    op.drop_index("ix_library_versions_asset_status", table_name="library_versions")
    op.drop_index("ix_library_versions_sha256", table_name="library_versions")
    op.drop_index("ix_library_versions_library_asset_id", table_name="library_versions")
    op.drop_table("library_versions")
    op.drop_index("ix_library_media_purpose_kind", table_name="library_media")
    op.drop_index("ix_library_media_sha256", table_name="library_media")
    op.drop_table("library_media")
    op.drop_index("ix_library_rights_status_expiry", table_name="library_rights")
    op.drop_table("library_rights")
    op.drop_index("ix_library_assets_created_at", table_name="library_assets")
    op.drop_index("ix_library_assets_kind_category_status", table_name="library_assets")
    op.drop_table("library_assets")

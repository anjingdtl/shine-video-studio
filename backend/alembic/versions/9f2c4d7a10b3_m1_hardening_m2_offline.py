"""M1 hardening and offline M2/M3 capabilities

Revision ID: 9f2c4d7a10b3
Revises: 70afa9c775ab
Create Date: 2026-10-21 09:00:00.000000

Adds per-shot asset bindings, project-scoped audit, worker heartbeats,
immutable prompt/QC/artifact records, provider profiles, workflow profiles,
and repair accounting. Existing M1 rows are preserved.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "9f2c4d7a10b3"
down_revision = "70afa9c775ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Direct ALTER TABLE is used deliberately: Alembic's SQLite "move and copy"
    # batch mode drops the old parent table, which triggers ON DELETE CASCADE
    # and can silently delete user rows. SQLite supports ADD COLUMN natively.
    op.add_column("projects", sa.Column("language", sa.String(length=32), nullable=False, server_default="Chinese"))
    op.add_column("projects", sa.Column("allow_remote_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "projects", sa.Column("auto_repair_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
    )

    op.add_column("assets", sa.Column("role", sa.String(length=32), nullable=True))
    op.add_column("assets", sa.Column("entity_id", sa.String(length=80), nullable=True))
    op.add_column("assets", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("assets", sa.Column("duration_s", sa.Float(), nullable=True))
    op.add_column("assets", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("assets", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("assets", sa.Column("derived_from_asset_id", sa.String(length=36), nullable=True))
    op.add_column("assets", sa.Column("provenance", sa.JSON(), nullable=True))
    op.add_column("assets", sa.Column("approved_by", sa.String(length=64), nullable=True))
    op.add_column("assets", sa.Column("approved_at", sa.DateTime(), nullable=True))
    op.add_column("assets", sa.Column("locked_at", sa.DateTime(), nullable=True))

    op.add_column("style_versions", sa.Column("approval_note", sa.Text(), nullable=False, server_default=""))

    op.add_column("jobs", sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("generation_mode", sa.String(length=16), nullable=True))
    op.add_column("jobs", sa.Column("workflow_profile_id", sa.String(length=36), nullable=True))
    op.add_column("jobs", sa.Column("compiled_prompt_sha256", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("artifact_id", sa.String(length=36), nullable=True))
    op.add_column("jobs", sa.Column("last_qc_result", sa.String(length=24), nullable=True))

    op.add_column("job_attempts", sa.Column("progress", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job_attempts", sa.Column("output_path", sa.Text(), nullable=True))
    op.add_column("job_attempts", sa.Column("output_sha256", sa.String(length=64), nullable=True))
    op.add_column("job_attempts", sa.Column("output_bytes", sa.Integer(), nullable=True))
    op.add_column("job_attempts", sa.Column("cost_units", sa.Float(), nullable=False, server_default="0"))
    op.add_column("job_attempts", sa.Column("reconciliation_state", sa.String(length=24), nullable=True))
    op.add_column("job_attempts", sa.Column("reconciled_at", sa.DateTime(), nullable=True))

    op.add_column(
        "job_dependencies", sa.Column("kind", sa.String(length=24), nullable=False, server_default="explicit")
    )

    op.add_column("qc_reports", sa.Column("artifact_id", sa.String(length=36), nullable=True))
    op.add_column("qc_reports", sa.Column("reviewer", sa.String(length=48), nullable=False, server_default="mock"))

    op.add_column("audit_events", sa.Column("project_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_audit_events_project_id"), "audit_events", ["project_id"], unique=False)

    op.create_table(
        "project_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source_format", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_documents_project_id"), "project_documents", ["project_id"], unique=False)

    op.create_table(
        "shot_asset_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("shot_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("use_as", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("frame_role", sa.String(length=16), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shot_id", "asset_id", name="uq_shot_asset_binding"),
    )
    op.create_index(op.f("ix_shot_asset_bindings_shot_id"), "shot_asset_bindings", ["shot_id"], unique=False)
    op.create_index(op.f("ix_shot_asset_bindings_asset_id"), "shot_asset_bindings", ["asset_id"], unique=False)

    op.create_table(
        "media_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_kind", sa.String(length=24), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_artifacts_project_id"), "media_artifacts", ["project_id"], unique=False)
    op.create_index(op.f("ix_media_artifacts_job_id"), "media_artifacts", ["job_id"], unique=False)

    op.create_table(
        "repair_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("root_cause", sa.String(length=500), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("cost_units", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_repair_records_job_id"), "repair_records", ["job_id"], unique=False)

    op.create_table(
        "provider_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("api_key_ref", sa.String(length=160), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("timeout_s", sa.Float(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_provider_profile_name"),
    )

    op.create_table(
        "agent_routes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=48), nullable=False),
        sa.Column("provider_profile_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["provider_profile_id"], ["provider_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role", name="uq_agent_route_role"),
    )

    op.create_table(
        "workflow_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("parameter_map", sa.JSON(), nullable=False),
        sa.Column("required_nodes", sa.JSON(), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=160), nullable=False),
        sa.Column("graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("validated_against", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_workflow_profile_name"),
    )
    op.create_index(op.f("ix_workflow_profiles_project_id"), "workflow_profiles", ["project_id"], unique=False)

    op.create_table(
        "external_job_reconciliations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("provider_job_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_external_job_reconciliations_job_id"), "external_job_reconciliations", ["job_id"], unique=False
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "worker_instances",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("current_phase", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )

    # Backfill project scope for audit rows created before this revision.
    op.execute("UPDATE audit_events SET project_id = entity_id WHERE entity_type = 'project'")
    op.execute(
        "UPDATE audit_events SET project_id = (SELECT project_id FROM assets WHERE assets.id = audit_events.entity_id) "
        "WHERE entity_type = 'asset' AND project_id IS NULL"
    )
    op.execute(
        "UPDATE audit_events SET project_id = (SELECT project_id FROM shots WHERE shots.id = audit_events.entity_id) "
        "WHERE entity_type = 'shot' AND project_id IS NULL"
    )
    op.execute(
        "UPDATE audit_events SET project_id = (SELECT project_id FROM jobs WHERE jobs.id = audit_events.entity_id) "
        "WHERE entity_type = 'job' AND project_id IS NULL"
    )
    op.execute(
        "UPDATE audit_events SET project_id = "
        "(SELECT project_id FROM style_versions WHERE style_versions.id = audit_events.entity_id) "
        "WHERE entity_type = 'style_version' AND project_id IS NULL"
    )


def downgrade() -> None:
    op.drop_table("worker_instances")
    op.drop_table("app_settings")
    op.drop_index(op.f("ix_external_job_reconciliations_job_id"), table_name="external_job_reconciliations")
    op.drop_table("external_job_reconciliations")
    op.drop_index(op.f("ix_workflow_profiles_project_id"), table_name="workflow_profiles")
    op.drop_table("workflow_profiles")
    op.drop_table("agent_routes")
    op.drop_table("provider_profiles")
    op.drop_index(op.f("ix_repair_records_job_id"), table_name="repair_records")
    op.drop_table("repair_records")
    op.drop_index(op.f("ix_media_artifacts_job_id"), table_name="media_artifacts")
    op.drop_index(op.f("ix_media_artifacts_project_id"), table_name="media_artifacts")
    op.drop_table("media_artifacts")
    op.drop_index(op.f("ix_shot_asset_bindings_asset_id"), table_name="shot_asset_bindings")
    op.drop_index(op.f("ix_shot_asset_bindings_shot_id"), table_name="shot_asset_bindings")
    op.drop_table("shot_asset_bindings")
    op.drop_index(op.f("ix_project_documents_project_id"), table_name="project_documents")
    op.drop_table("project_documents")

    op.drop_index(op.f("ix_audit_events_project_id"), table_name="audit_events")
    op.drop_column("audit_events", "project_id")

    op.drop_column("qc_reports", "reviewer")
    op.drop_column("qc_reports", "artifact_id")

    op.drop_column("job_dependencies", "kind")

    for column in (
        "reconciled_at",
        "reconciliation_state",
        "cost_units",
        "output_bytes",
        "output_sha256",
        "output_path",
        "progress",
    ):
        op.drop_column("job_attempts", column)

    for column in (
        "last_qc_result",
        "artifact_id",
        "compiled_prompt_sha256",
        "workflow_profile_id",
        "generation_mode",
        "state_version",
    ):
        op.drop_column("jobs", column)

    op.drop_column("style_versions", "approval_note")

    for column in (
        "locked_at",
        "approved_at",
        "approved_by",
        "provenance",
        "derived_from_asset_id",
        "height",
        "width",
        "duration_s",
        "tags",
        "entity_id",
        "role",
    ):
        op.drop_column("assets", column)

    op.drop_column("projects", "auto_repair_enabled")
    op.drop_column("projects", "allow_remote_review")
    op.drop_column("projects", "language")

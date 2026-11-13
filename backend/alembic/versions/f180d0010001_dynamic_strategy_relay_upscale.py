"""Dynamic per-shot strategy on series plus the RelayVSR upscale ledger.

The dynamic per-shot round freezes the user's production strategy (model
variant, accelerator, optional second pass, optional RelayVSR X2) on the
production series, and the RelayVSR post-production stage gets its own work
items driven by the same resident Worker and the same resource lease
admission - never a second queue or scheduler.

Revision ID: f180d0010001
Revises: f180c0030001
Create Date: 2026-11-13
"""

import sqlalchemy as sa

from alembic import op

revision = "f180d0010001"
down_revision = "f180c0030001"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column in row["name"] for row in inspector.get_columns(table))


def upgrade():
    tables = _existing_tables()
    if "production_series" in tables and not _has_column("production_series", "generation_strategy"):
        with op.batch_alter_table("production_series") as batch:
            batch.add_column(sa.Column("generation_strategy", sa.JSON(), nullable=True))

    if "relay_upscale_items" not in tables:
        op.create_table(
            "relay_upscale_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(36), nullable=True),
            sa.Column("series_id", sa.String(36), nullable=True),
            sa.Column("job_id", sa.String(36), nullable=False),
            sa.Column("shot_id", sa.String(36), nullable=True),
            sa.Column("stage", sa.String(24), nullable=False, server_default="relayvsr_x2"),
            sa.Column("source_artifact_id", sa.String(36), nullable=True),
            sa.Column("source_sha256", sa.String(64), nullable=False),
            sa.Column("source_width", sa.Integer(), nullable=True),
            sa.Column("source_height", sa.Integer(), nullable=True),
            sa.Column("source_duration_s", sa.Float(), nullable=True),
            sa.Column("source_fps", sa.Float(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("worker_id", sa.String(64), nullable=True),
            sa.Column("lease_owner", sa.String(128), nullable=True),
            sa.Column("lease_id", sa.String(36), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("intent_sha256", sa.String(64), nullable=True),
            sa.Column("prompt_id", sa.String(64), nullable=True),
            sa.Column("external_outcome", sa.String(16), nullable=True),
            sa.Column("output_artifact_id", sa.String(36), nullable=True),
            sa.Column("output_sha256", sa.String(64), nullable=True),
            sa.Column("output_width", sa.Integer(), nullable=True),
            sa.Column("output_height", sa.Integer(), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "job_id", "stage", name="uq_relay_upscale_job_stage"),
        )
        op.create_index("ix_relay_upscale_items_project_id", "relay_upscale_items", ["project_id"])
        op.create_index("ix_relay_upscale_items_job_id", "relay_upscale_items", ["job_id"])
        op.create_index("ix_relay_upscale_status", "relay_upscale_items", ["status"])
        op.create_index("ix_relay_upscale_items_run_id", "relay_upscale_items", ["run_id"])


def downgrade():
    tables = _existing_tables()
    if "relay_upscale_items" in tables:
        op.drop_table("relay_upscale_items")
    if "production_series" in tables and _has_column("production_series", "generation_strategy"):
        with op.batch_alter_table("production_series") as batch:
            batch.drop_column("generation_strategy")

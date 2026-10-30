"""Durable production series: one owned, ordered real-H3 run per project.

A series is the unit the resident Worker claims.  It keeps the exclusive GPU
lease across every shot of the run, so two serial H3 model stages can never
overlap, and it is the ownership proof that stops a second Worker from taking
over the same run.
"""

import sqlalchemy as sa

from alembic import op

revision = "f140a0010001"
down_revision = "f130a0030001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "production_series",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
        sa.Column("provider", sa.String(length=24), nullable=False, server_default="comfyui"),
        sa.Column("workflow_profile_id", sa.String(length=36), nullable=True),
        sa.Column("generation_mode", sa.String(length=16), nullable=True),
        sa.Column("self_review_scope", sa.String(length=16), nullable=False, server_default="full"),
        sa.Column("basic_review_resolution", sa.JSON(), nullable=True),
        sa.Column("mock_scenario", sa.String(length=24), nullable=False, server_default="success"),
        sa.Column("shot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("current_item_id", sa.String(length=36), nullable=True),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("finalization_state", sa.String(length=24), nullable=False, server_default="NONE"),
        sa.Column("finalization_note", sa.Text(), nullable=True),
        sa.Column("renderer_identity", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_production_series_project_id", "production_series", ["project_id"])
    op.create_index("ix_production_series_status", "production_series", ["status"])
    op.create_table(
        "production_series_items",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("series_id", sa.String(length=36), nullable=False),
        sa.Column("shot_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("continuity_mode", sa.String(length=32), nullable=False, server_default="cut"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["production_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("series_id", "position", name="uq_series_position"),
    )
    op.create_index("ix_production_series_items_series_id", "production_series_items", ["series_id"])
    op.create_index("ix_production_series_items_shot_id", "production_series_items", ["shot_id"])


def downgrade():
    op.drop_index("ix_production_series_items_shot_id", table_name="production_series_items")
    op.drop_index("ix_production_series_items_series_id", table_name="production_series_items")
    op.drop_table("production_series_items")
    op.drop_index("ix_production_series_status", table_name="production_series")
    op.drop_index("ix_production_series_project_id", table_name="production_series")
    op.drop_table("production_series")

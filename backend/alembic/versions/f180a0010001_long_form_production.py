"""Long-form production: episode plans, the director package and whole-film runs.

Adds the durable shape the director journey needs and widens the existing
automatic-delivery ledger from "one reel per series" to "one reel per delivery
scope", so an episode assembled from several series can be published exactly
once.  Published history is not rewritten: a pre-existing order keeps its
``series`` scope and its ``(series_id, version)`` uniqueness.

The delivery-scope widening is done in batch mode because SQLite cannot drop a
NOT NULL constraint in place.  Alembic recreates ``delivery_orders`` and copies
every row, so existing orders, their versions and their artifacts survive.
"""

import sqlalchemy as sa

from alembic import op

revision = "f180a0010001"
down_revision = "f170a0030001"
branch_labels = None
depends_on = None

ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "RECONCILING", "CANCEL_REQUESTED")


def _active_status_sql() -> str:
    return "status IN (" + ", ".join(f"'{status}'" for status in ACTIVE_RUN_STATUSES) + ")"


def upgrade():
    op.create_table(
        "director_episode_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("target_seconds", sa.Integer(), nullable=False),
        sa.Column("tolerance_seconds", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_director_episode_plan_version"),
    )
    op.create_index("ix_director_episode_plans_project_id", "director_episode_plans", ["project_id"])

    op.create_table(
        "director_package_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("episode_count", sa.Integer(), nullable=False),
        sa.Column("shot_count", sa.Integer(), nullable=False),
        sa.Column("episode_plan_id", sa.String(36), sa.ForeignKey("director_episode_plans.id"), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("stale_reason", sa.String(200), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "revision", name="uq_director_package_revision"),
    )
    op.create_index("ix_director_package_versions_project_id", "director_package_versions", ["project_id"])
    op.create_index(
        "ix_director_package_project_status", "director_package_versions", ["project_id", "status"]
    )

    op.create_table(
        "director_package_episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(36),
            sa.ForeignKey("director_package_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("shot_count", sa.Integer(), nullable=False),
        sa.Column("planned_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("package_id", "episode_id", name="uq_director_package_episode"),
        sa.UniqueConstraint("package_id", "position", name="uq_director_package_episode_position"),
    )
    op.create_index("ix_director_package_episodes_package_id", "director_package_episodes", ["package_id"])
    op.create_index("ix_director_package_episodes_project_id", "director_package_episodes", ["project_id"])

    op.create_table(
        "production_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "package_id",
            sa.String(36),
            sa.ForeignKey("director_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("package_revision", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("claim_owner", sa.String(128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(), nullable=True),
        sa.Column("control_reason", sa.String(200), nullable=True),
        sa.Column("blocker_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_production_run_idempotency"),
    )
    op.create_index("ix_production_runs_project_id", "production_runs", ["project_id"])
    op.create_index("ix_production_runs_package_id", "production_runs", ["package_id"])
    op.create_index("ix_production_runs_status", "production_runs", ["status"])
    op.create_index("ix_production_runs_status_updated", "production_runs", ["status", "updated_at"])
    op.create_index(
        "uq_production_run_active_project",
        "production_runs",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text(_active_status_sql()),
    )

    op.create_table(
        "production_run_episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("production_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("target_seconds", sa.Float(), nullable=False),
        sa.Column("planned_seconds", sa.Float(), nullable=False),
        sa.Column("actual_seconds", sa.Float(), nullable=True),
        sa.Column("shot_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("blocker_code", sa.String(80), nullable=True),
        sa.Column("delivery_order_id", sa.String(36), nullable=True),
        sa.Column("delivered_artifact_id", sa.String(36), nullable=True),
        sa.Column("delivered_sha256", sa.String(64), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "episode_id", name="uq_run_episode_identity"),
        sa.UniqueConstraint("run_id", "position", name="uq_run_episode_position"),
    )
    op.create_index("ix_production_run_episodes_run_id", "production_run_episodes", ["run_id"])
    op.create_index("ix_production_run_episodes_project_id", "production_run_episodes", ["project_id"])
    op.create_index("ix_run_episodes_status", "production_run_episodes", ["run_id", "status"])

    op.create_table(
        "production_run_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("production_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "episode_row_id",
            sa.String(36),
            sa.ForeignKey("production_run_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("episode_id", sa.String(80), nullable=False),
        sa.Column("batch_key", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("shot_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("series_id", sa.String(36), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "batch_key", "input_digest", name="uq_run_batch_input"),
    )
    op.create_index("ix_production_run_batches_run_id", "production_run_batches", ["run_id"])
    op.create_index("ix_production_run_batches_episode_row_id", "production_run_batches", ["episode_row_id"])
    op.create_index("ix_run_batches_status", "production_run_batches", ["run_id", "status"])
    op.create_index(
        "uq_run_batch_series",
        "production_run_batches",
        ["series_id"],
        unique=True,
        sqlite_where=sa.text("series_id IS NOT NULL"),
    )

    op.create_table(
        "production_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("production_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_production_run_events_run_id", "production_run_events", ["run_id"])
    op.create_index("ix_production_run_events_project_id", "production_run_events", ["project_id"])
    op.create_index("ix_run_events_run", "production_run_events", ["run_id", "id"])

    with op.batch_alter_table("delivery_orders") as batch:
        batch.alter_column("series_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("scope_kind", sa.String(24), nullable=False, server_default="series"))
        batch.add_column(sa.Column("scope_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("input_digest", sa.String(64), nullable=True))
    op.create_index(
        "uq_delivery_order_scope_version",
        "delivery_orders",
        ["scope_kind", "scope_id", "version"],
        unique=True,
        sqlite_where=sa.text("scope_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_delivery_order_scope_version", table_name="delivery_orders")
    with op.batch_alter_table("delivery_orders") as batch:
        batch.drop_column("input_digest")
        batch.drop_column("scope_id")
        batch.drop_column("scope_kind")
        batch.alter_column("series_id", existing_type=sa.String(36), nullable=False)

    op.drop_table("production_run_events")
    op.drop_table("production_run_batches")
    op.drop_table("production_run_episodes")
    op.drop_table("production_runs")
    op.drop_table("director_package_episodes")
    op.drop_table("director_package_versions")
    op.drop_table("director_episode_plans")

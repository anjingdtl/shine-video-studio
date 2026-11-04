"""Director AI material preparation: plan purpose isolation and the batch ledger.

Two things happen here and they belong in one revision because the second is
unsafe without the first.

``preproduction_plans.purpose``
    The director-side material plan shares this table with the production
    contract.  Every production selector (``current_plan``, ``readiness``,
    ``enforce``, freeze, revalidate, the Worker's preproduction gate) reads
    through a purpose filter, so a newer ``asset_preparation`` revision can
    never become the frozen H3 manifest.  Existing rows are backfilled to
    ``production`` by the server default; a historical frozen plan keeps its
    id, payload and sha256 untouched, so no evidence or grant is invalidated.

``material_runs`` / ``material_items`` / ``material_image_attempts``
    The durable image batch.  The API only ever creates these rows and returns
    202; the resident Worker claims an item, holds the shared exclusive GPU
    lease for that item alone, persists the submit intent before the external
    ``/prompt`` call and the prompt id immediately after it.  The unique
    constraints are the anti-duplicate-submission proof: one item per
    (project, requirement, generation revision) and at most one unfinished
    attempt per item.
"""

import sqlalchemy as sa

from alembic import op

revision = "f170a0010001"
down_revision = "f160a0010001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("preproduction_plans") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(length=24), nullable=False, server_default="production")
        )
    op.create_index(
        "ix_preproduction_plans_project_purpose", "preproduction_plans", ["project_id", "purpose"]
    )

    op.create_table(
        "material_runs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("confirmation_id", sa.String(length=64), nullable=False),
        sa.Column("confirmation_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["preproduction_plans.id"]),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_material_runs_idempotency"),
    )
    op.create_index("ix_material_runs_project_id", "material_runs", ["project_id"])
    op.create_index("ix_material_runs_plan_id", "material_runs", ["plan_id"])

    op.create_table(
        "material_items",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("requirement_id", sa.String(length=36), nullable=True),
        sa.Column("generation_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("brief_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("current_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["material_runs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["requirement_id"], ["asset_requirements.id"]),
        sa.ForeignKeyConstraint(["brief_artifact_id"], ["agent_artifacts.id"]),
        sa.UniqueConstraint(
            "project_id", "requirement_id", "generation_revision", name="uq_material_items_revision"
        ),
    )
    op.create_index("ix_material_items_run_id", "material_items", ["run_id"])
    op.create_index("ix_material_items_project_id", "material_items", ["project_id"])
    op.create_index("ix_material_items_requirement_id", "material_items", ["requirement_id"])
    op.create_index("ix_material_items_status", "material_items", ["status"])

    op.create_table(
        "material_image_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_intent_key", sa.String(length=80), nullable=True),
        sa.Column("graph_sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_template_version", sa.String(length=32), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("output_node_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("submit_intent_at", sa.DateTime(), nullable=True),
        sa.Column("prompt_id", sa.String(length=128), nullable=True),
        sa.Column("external_outcome", sa.String(length=24), nullable=True),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("reconciliation_state", sa.String(length=40), nullable=True),
        sa.Column("resubmitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("candidate_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["material_items.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["material_runs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidate_assets.id"]),
        sa.UniqueConstraint("item_id", "attempt_no", name="uq_material_image_attempts_no"),
        sa.UniqueConstraint("active_intent_key", name="uq_material_image_attempts_active"),
    )
    op.create_index("ix_material_image_attempts_item_id", "material_image_attempts", ["item_id"])
    op.create_index("ix_material_image_attempts_run_id", "material_image_attempts", ["run_id"])
    op.create_index("ix_material_image_attempts_project_id", "material_image_attempts", ["project_id"])
    op.create_index("ix_material_image_attempts_prompt", "material_image_attempts", ["prompt_id"])


def downgrade():
    # A downgrade closes the feature; it must not delete produced candidates or
    # the user's confirmed material history.
    op.drop_index("ix_material_image_attempts_prompt", table_name="material_image_attempts")
    op.drop_index("ix_material_image_attempts_project_id", table_name="material_image_attempts")
    op.drop_index("ix_material_image_attempts_run_id", table_name="material_image_attempts")
    op.drop_index("ix_material_image_attempts_item_id", table_name="material_image_attempts")
    op.drop_table("material_image_attempts")
    op.drop_index("ix_material_items_status", table_name="material_items")
    op.drop_index("ix_material_items_requirement_id", table_name="material_items")
    op.drop_index("ix_material_items_project_id", table_name="material_items")
    op.drop_index("ix_material_items_run_id", table_name="material_items")
    op.drop_table("material_items")
    op.drop_index("ix_material_runs_plan_id", table_name="material_runs")
    op.drop_index("ix_material_runs_project_id", table_name="material_runs")
    op.drop_table("material_runs")
    op.drop_index("ix_preproduction_plans_project_purpose", table_name="preproduction_plans")
    with op.batch_alter_table("preproduction_plans") as batch:
        batch.drop_column("purpose")

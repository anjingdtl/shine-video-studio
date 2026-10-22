"""V1.3 preparation ledger, additive; legacy production rows are untouched."""

import sqlalchemy as sa

from alembic import op

revision = "f130a0010001"
down_revision = "f121a0010001"
branch_labels = None
depends_on = None

GLOBAL = ("comfy_instances", "environment_snapshots", "model_packages", "node_packages", "workflow_revisions")
PROJECT = (
    "installation_plans",
    "rights_records",
    "candidate_assets",
    "voice_profiles",
    "preproduction_plans",
    "asset_requirements",
    "voice_renders",
)


def upgrade():
    for name in (*GLOBAL, *PROJECT, "installation_attempts"):
        columns = [
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        ]
        if name in PROJECT:
            columns.append(sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False))
        if name == "candidate_assets":
            columns.append(sa.Column("formal_asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=True))
        if name in {"installation_attempts", "asset_requirements"}:
            parent = "installation_plans" if name == "installation_attempts" else "preproduction_plans"
            columns.append(sa.Column("plan_id", sa.String(36), sa.ForeignKey(parent + ".id"), nullable=False))
        if name == "voice_renders":
            columns.append(sa.Column("voice_id", sa.String(36), sa.ForeignKey("voice_profiles.id"), nullable=False))
        op.create_table(name, *columns)
        if name in PROJECT:
            op.create_index("ix_" + name + "_project_id", name, ["project_id"])
        if name in {"installation_attempts", "asset_requirements"}:
            op.create_index("ix_" + name + "_plan_id", name, ["plan_id"])
        if name == "voice_renders":
            op.create_index("ix_voice_renders_voice_id", name, ["voice_id"])
    op.create_table(
        "preparation_approval_grants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(128), unique=True, nullable=False),
        sa.Column("gpu", sa.String(64), nullable=False),
        sa.Column("budget_mb", sa.Integer(), nullable=False),
        sa.Column("exclusive", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_resource_leases_gpu", "resource_leases", ["gpu"])


def downgrade():
    for name in (
        "resource_leases",
        "preparation_approval_grants",
        "installation_attempts",
        *reversed(PROJECT),
        *reversed(GLOBAL),
    ):
        op.drop_table(name)

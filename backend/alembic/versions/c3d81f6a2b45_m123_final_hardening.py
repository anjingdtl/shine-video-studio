"""M1/M2/M3 offline final hardening

Revision ID: c3d81f6a2b45
Revises: 9f2c4d7a10b3
Create Date: 2026-10-21 18:00:00.000000

Adds per-project quality policies, per-attempt request snapshots and repair
provenance, per-family ComfyUI media transfer declarations, and the named
quality gate ledger. Existing rows are preserved; every new column is
additive and nullable or has a server default.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c3d81f6a2b45"
down_revision = "9f2c4d7a10b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Direct ALTER TABLE is used deliberately: Alembic's SQLite "move and copy"
    # batch mode drops the old parent table, which triggers ON DELETE CASCADE
    # and can silently delete user rows. SQLite supports ADD COLUMN natively.
    op.add_column("projects", sa.Column("quality_policy", sa.JSON(), nullable=True))

    op.add_column("jobs", sa.Column("request_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("jobs", sa.Column("pending_repair_context", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("quality_verification", sa.String(length=24), nullable=True))

    op.add_column(
        "job_attempts",
        sa.Column("attempt_kind", sa.String(length=16), nullable=False, server_default="initial"),
    )
    op.add_column("job_attempts", sa.Column("parent_attempt_id", sa.String(length=36), nullable=True))
    op.add_column("job_attempts", sa.Column("request_snapshot", sa.JSON(), nullable=True))
    op.add_column("job_attempts", sa.Column("prompt_sha256", sa.String(length=64), nullable=True))
    op.add_column("job_attempts", sa.Column("parameter_sha256", sa.String(length=64), nullable=True))
    op.add_column("job_attempts", sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
    op.add_column("job_attempts", sa.Column("prompt_graph_sha256", sa.String(length=64), nullable=True))
    op.add_column("job_attempts", sa.Column("repair_reason", sa.Text(), nullable=True))
    op.add_column("job_attempts", sa.Column("repair_root_cause", sa.String(length=500), nullable=True))

    op.add_column("workflow_profiles", sa.Column("media_transfers", sa.JSON(), nullable=True))

    op.create_table(
        "quality_gates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("gate_name", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("coverage", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quality_gates_job_id"), "quality_gates", ["job_id"], unique=False)

    # Existing jobs were created before request revisions existed.
    op.execute("UPDATE jobs SET request_revision = 1 WHERE request_revision IS NULL OR request_revision < 1")
    # Historic attempts have no provenance snapshot; record that explicitly.
    op.execute("UPDATE job_attempts SET attempt_kind = 'initial' WHERE attempt_kind IS NULL")


def downgrade() -> None:
    op.drop_index(op.f("ix_quality_gates_job_id"), table_name="quality_gates")
    op.drop_table("quality_gates")

    op.drop_column("workflow_profiles", "media_transfers")

    for column in (
        "repair_root_cause",
        "repair_reason",
        "prompt_graph_sha256",
        "manifest_sha256",
        "parameter_sha256",
        "prompt_sha256",
        "request_snapshot",
        "parent_attempt_id",
        "attempt_kind",
    ):
        op.drop_column("job_attempts", column)

    op.drop_column("jobs", "quality_verification")
    op.drop_column("jobs", "pending_repair_context")
    op.drop_column("jobs", "request_revision")

    op.drop_column("projects", "quality_policy")

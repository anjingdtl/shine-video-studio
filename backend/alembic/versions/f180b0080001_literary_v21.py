"""The V2.1 literary ledger: physical calls, five-dimension memory, units.

Revision ID: f180b0080001
Revises: f180b0070001
Create Date: 2026-11-11

Nothing existing is rewritten: every table is additive, so a V1 database keeps
its rows and its unfinished tasks.  The five tables are the minimum the design
needs - a fact ledger cannot live in a trace JSON, and a project-wide question
about unknown calls cannot be answered by scanning every task trace.
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0080001"
down_revision = "f180b0070001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_call_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("unit_id", sa.String(160), nullable=False),
        sa.Column("role", sa.String(48), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="creative"),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="SENDING"),
        sa.Column("external_outcome", sa.String(16), nullable=False, server_default="NOT_SENT"),
        sa.Column("corrective", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("output_budget", sa.JSON(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("provider_code", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("first_event_ms", sa.Integer(), nullable=True),
        sa.Column("last_event_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_llm_call_attempts_project_id", "llm_call_attempts", ["project_id"])
    op.create_index("ix_llm_call_attempts_task_id", "llm_call_attempts", ["task_id"])
    op.create_index("ix_llm_call_attempt_project_unit", "llm_call_attempts",
                    ["project_id", "unit_id"])
    op.create_index("ix_llm_call_attempt_project_status", "llm_call_attempts",
                    ["project_id", "external_outcome"])

    op.create_table(
        "story_memory_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("subjects", sa.JSON(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False, server_default="ai_proposal"),
        sa.Column("authority", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("visibility", sa.JSON(), nullable=False),
        sa.Column("story_time", sa.String(64), nullable=True),
        sa.Column("narrative_order", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("unit_id", sa.String(160), nullable=True),
        sa.Column("source_task_id", sa.String(36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("superseded_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_story_memory_facts_project_id", "story_memory_facts", ["project_id"])
    op.create_index("ix_story_memory_fact_project_kind", "story_memory_facts",
                    ["project_id", "kind"])
    op.create_index("ix_story_memory_fact_project_authority", "story_memory_facts",
                    ["project_id", "authority"])

    op.create_table(
        "story_memory_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("fact_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("patch", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="local-user"),
        sa.Column("actor_kind", sa.String(16), nullable=False, server_default="system"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_story_memory_revisions_project_id", "story_memory_revisions",
                    ["project_id"])
    op.create_index("ix_story_memory_revisions_fact_id", "story_memory_revisions", ["fact_id"])

    op.create_table(
        "story_memory_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("as_of_story_time", sa.String(64), nullable=True),
        sa.Column("upstream", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_story_memory_snapshots_project_id", "story_memory_snapshots",
                    ["project_id"])

    op.create_table(
        "literary_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("source_start", sa.Integer(), nullable=True),
        sa.Column("source_end", sa.Integer(), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("dependency_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_literary_units_project_id", "literary_units", ["project_id"])
    op.create_index("ix_literary_unit_project_kind", "literary_units", ["project_id", "kind"])
    op.create_index("ix_literary_unit_parent", "literary_units", ["parent_id", "ordinal"])


def downgrade():
    for table in ("literary_units", "story_memory_snapshots", "story_memory_revisions",
                  "story_memory_facts", "llm_call_attempts"):
        op.drop_table(table)

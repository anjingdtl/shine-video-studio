"""The V2.1 literary ledger: physical calls, memory facts, snapshots, units.

Five tables, each with a job the existing ledger cannot do:

``llm_call_attempts``
    one row per physical provider request.  The task trace keeps the last few
    attempts for one unit; this table is how a project-wide question ("which
    calls are still unknown?") is answered without scanning every trace.
``story_memory_facts`` / ``story_memory_revisions`` / ``story_memory_snapshots``
    one fact ledger with five views.  A fact is written once and mapped to
    dimensions, never copied into five authoritative records.
``literary_units``
    the DAG node for a beat or a scene: parent, ordinal, dependency set and
    state.  The physical call still belongs to ``agent_task_records``.

Everything else (drafts, QA findings, stage results) stays an ``AgentArtifact``:
adding a table per concept is how a low-blocking design becomes a schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow


class LLMCallAttempt(Base):
    """One physical request, recorded before and after it leaves the machine."""

    __tablename__ = "llm_call_attempts"
    __table_args__ = (
        Index("ix_llm_call_attempt_project_unit", "project_id", "unit_id"),
        Index("ix_llm_call_attempt_project_status", "project_id", "external_outcome"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    unit_id: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(48), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="creative", nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="SENDING", nullable=False)
    external_outcome: Mapped[str] = mapped_column(String(16), default="NOT_SENT", nullable=False)
    corrective: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_budget: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_event_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StoryMemoryFact(Base):
    """One fact of the story world, written once and viewed five ways."""

    __tablename__ = "story_memory_facts"
    __table_args__ = (
        Index("ix_story_memory_fact_project_kind", "project_id", "kind"),
        Index("ix_story_memory_fact_project_authority", "project_id", "authority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    #: character | relationship | mainline | clue | foreshadow
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The dimensions this one fact serves; ``kind`` is its primary view.
    dimensions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    subjects: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: Parsed evidence: document sha256, span ids, offsets, quote hashes.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: source_fact | approved_adaptation | ai_proposal
    origin: Mapped[str] = mapped_column(String(24), default="ai_proposal", nullable=False)
    #: candidate | approved | authoritative
    authority: Mapped[str] = mapped_column(String(16), default="candidate", nullable=False)
    #: audience_known | characters_known | production_only, plus the holder ids.
    visibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    story_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    narrative_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: active | open | partially_paid | paid | closed | unknown
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    unit_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class StoryMemoryRevision(Base):
    """One atomic change to the fact ledger, with who asked for it."""

    __tablename__ = "story_memory_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    fact_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    patch: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="local-user", nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class StoryMemorySnapshot(Base):
    """An immutable hash of the authoritative facts a unit was written from."""

    __tablename__ = "story_memory_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    as_of_story_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upstream: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LiteraryUnit(Base):
    """A DAG node: volume -> episode -> scene -> beat.

    The node owns dependency and state; the provider call it may need is an
    ``agent_task_records`` row referenced by ``task_id``.
    """

    __tablename__ = "literary_units"
    __table_args__ = (
        Index("ix_literary_unit_project_kind", "project_id", "kind"),
        Index("ix_literary_unit_parent", "parent_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dependency_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow,
                                                 nullable=False)

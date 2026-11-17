"""Persistent metadata for the support agent.

These tables store a bounded conversation, the actions a person previewed and
approved, and the engineering reports that were written.  They intentionally do
not copy project production state: readiness, tasks, GPU leases and progress are
always read from their original ledgers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow


class SupportSession(Base):
    __tablename__ = "support_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    page_id: Mapped[str] = mapped_column(String(48), default="unknown", nullable=False)
    episode_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="local-user", nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("support_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="message", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SupportAction(Base):
    __tablename__ = "support_actions"
    __table_args__ = (UniqueConstraint("request_identity", name="uq_support_action_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PREVIEWED", nullable=False)
    #: Stable identity of the requested change (kind + params + actor + project).
    request_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    #: Monotonic attempt for that request key.  A terminal action never blocks a
    #: new legal attempt, while a live one is reused idempotently.
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_identity: Mapped[str] = mapped_column(String(96), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Action-scoped facts (installation, bindings, receipts, workflow graph,
    #: release targets).  ``None`` means the facts could not be read and the
    #: preview may never be executed.
    target_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="local-user", nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original_task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SupportDelegation(Base):
    """Persistent, idempotent authorization for one previewed multi-step plan.

    This is authorization metadata, not a second production system: every step
    is still executed by ``support.actions`` through the original executor.
    """

    __tablename__ = "support_delegations"
    __table_args__ = (
        UniqueConstraint("authorization_key", name="uq_support_delegation_authorization"),
        Index("ix_support_delegations_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Digest of session + plan identity + approved orders + budget.  A repeated
    #: authorization of the same delegation returns the same row.
    authorization_key: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class CopilotMemory(Base):
    """One long-lived, user-owned fact distilled from a conversation.

    This is *derived* data: the original exchange stays in ``SupportSession`` /
    ``SupportMessage``.  A memory never becomes a second production ledger and
    never decides a business verdict.  Deleted rows stay as tombstones (status
    ``DELETED``) with only the content hash, so a later automatic extraction
    cannot silently recreate exactly what the person removed.
    """

    __tablename__ = "copilot_memories"
    __table_args__ = (
        Index("ix_copilot_memories_scope_project", "scope", "project_id", "status"),
        Index("ix_copilot_memories_status_updated", "status", "updated_at"),
        Index("ix_copilot_memories_content_hash", "content_hash"),
        Index("ix_copilot_memories_source", "source", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), default="SUMMARY", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="MANUAL", nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    embedding_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    confirmed: Mapped[bool] = mapped_column(default=True, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)


class CopilotMemoryVector(Base):
    """Rebuildable local vector index for one memory revision.

    The row is not an authority: it carries the hash/revision it was built
    from, so a stale vector can never be returned for edited or deleted text.
    """

    __tablename__ = "copilot_memory_vectors"

    memory_id: Mapped[str] = mapped_column(
        ForeignKey("copilot_memories.id", ondelete="CASCADE"), primary_key=True
    )
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SupportReport(Base):
    __tablename__ = "support_reports"
    __table_args__ = (Index("ix_support_reports_fingerprint", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT_FOR_DEVELOPER", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

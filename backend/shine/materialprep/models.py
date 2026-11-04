"""Durable ledger rows for material-preparation batches, items and image attempts.

These reuse the one SQLite database, the one resident Worker and the one
``ResourceLease`` ledger.  A batch is a domain view over its items, not a second
scheduler: the Worker claims an ITEM, holds the exclusive GPU lease for that
item only, releases it, and lets the existing scheduler decide what runs next so
a long image batch cannot starve the H3 video queue.

Concurrency columns follow the ``Job``/``ProductionSeries`` pattern already in
this codebase: a conditional ``UPDATE`` on ``status`` + ``state_version`` +
``lease_owner`` is the only way a Worker writes, so an expired claim can be
re-claimed without ever authorising a second external submission.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow


class MaterialRun(Base):
    """One confirmed generation batch.

    ``idempotency_key`` is scoped per project: the same key with the same
    request hash returns the original batch, and the same key with a different
    payload is a 409 rather than a second GPU run.
    """

    __tablename__ = "material_runs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_material_runs_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(ForeignKey("preproduction_plans.id"), index=True, nullable=False)
    confirmation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MaterialItem(Base):
    """One requirement's generation slot inside a batch.

    ``(project_id, requirement_id, generation_revision)`` is unique, so two
    browsers using two different idempotency keys still cannot create two items
    for the same confirmed requirement revision.  An explicit "generate another
    version" bumps ``generation_revision`` and keeps the old row and its
    candidates; a resume of a failed item adds an attempt to THIS row.
    """

    __tablename__ = "material_items"
    __table_args__ = (
        UniqueConstraint("project_id", "requirement_id", "generation_revision", name="uq_material_items_revision"),
        Index("ix_material_items_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("material_runs.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("asset_requirements.id"), index=True, nullable=True
    )
    """NULL for setup validation samples, which prove a family can render but are
    not a user-confirmed material requirement."""
    generation_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    brief_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("agent_artifacts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MaterialImageAttempt(Base):
    """One external image execution attempt.

    The submit intent (final graph hash, seed, size, output node, runtime
    fingerprint) is persisted BEFORE the ``/prompt`` call and the external
    ``prompt_id`` immediately after it, so a crash in between is recoverable by
    reconciliation.  ``external_outcome=UNKNOWN`` is a permanent fact: the row
    is never rewritten into a second submission.

    ``active_intent_key`` holds ``"<item_id>:<attempt_no>"`` while the attempt
    is unfinished and is cleared when it reaches a terminal state.  Its UNIQUE
    constraint is what makes "at most one unfinished execution intent per item"
    enforceable atomically: a resume cannot create a second live attempt while
    the first one is still in flight or still unknown.
    """

    __tablename__ = "material_image_attempts"
    __table_args__ = (
        UniqueConstraint("item_id", "attempt_no", name="uq_material_image_attempts_no"),
        UniqueConstraint("active_intent_key", name="uq_material_image_attempts_active"),
        Index("ix_material_image_attempts_prompt", "prompt_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("material_runs.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    active_intent_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    graph_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submit_intent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reconciliation_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resubmitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_assets.id"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

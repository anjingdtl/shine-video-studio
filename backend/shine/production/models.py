"""Durable rows for a whole-film production run.

A run is the range/policy/aggregate authority for one confirmed director
package.  It never submits a render: it only decides which batch should become
claimable, and the existing ``ProductionSeries`` machinery still owns the GPU,
the fenced renderer and every job/attempt below it.  That is why these tables
carry no ComfyUI column at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow

# A run in one of these states still owns the project's long-form scope.  A
# second concurrent run would be a second production truth, so the partial
# unique index below admits only one.
ACTIVE_RUN_STATUSES = (
    "QUEUED",
    "RUNNING",
    "PAUSE_REQUESTED",
    "PAUSED",
    "RECONCILING",
    "CANCEL_REQUESTED",
)

TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


def _active_status_sql() -> str:
    return "status IN (" + ", ".join(f"'{status}'" for status in ACTIVE_RUN_STATUSES) + ")"


class ProductionRun(Base):
    """One whole-film run over one confirmed director package version."""

    __tablename__ = "production_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_production_run_idempotency"),
        Index(
            "uq_production_run_active_project",
            "project_id",
            unique=True,
            sqlite_where=text(_active_status_sql()),
        ),
        Index("ix_production_runs_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[str] = mapped_column(
        ForeignKey("director_package_versions.id", ondelete="RESTRICT"), index=True
    )
    package_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    counters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    control_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    blocker_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductionRunEpisode(Base):
    """One episode of the run: its ordered shot range and delivery pointer."""

    __tablename__ = "production_run_episodes"
    __table_args__ = (
        UniqueConstraint("run_id", "episode_id", name="uq_run_episode_identity"),
        UniqueConstraint("run_id", "position", name="uq_run_episode_position"),
        Index("ix_run_episodes_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("production_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    planned_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    shot_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PLANNED", nullable=False)
    blocker_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    post_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    """The confirmed post-production contract (cut points, subtitle revision).

    ``None`` is the default cut: every take in plan order, full length, no
    subtitles.  A revision creates a new delivery version; it never re-renders.
    """
    delivery_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delivered_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delivered_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ProductionRunBatch(Base):
    """One group of confirmed shots handed to exactly one production series.

    ``(run_id, batch_key, input_digest)`` is the batch idempotency identity: the
    same planned input can only ever own one series, so a crash between "create
    the series" and "remember its id" cannot double-queue the same shots.
    """

    __tablename__ = "production_run_batches"
    __table_args__ = (
        UniqueConstraint("run_id", "batch_key", "input_digest", name="uq_run_batch_input"),
        Index("uq_run_batch_series", "series_id", unique=True, sqlite_where=text("series_id IS NOT NULL")),
        Index("ix_run_batches_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("production_runs.id", ondelete="CASCADE"), index=True)
    episode_row_id: Mapped[str] = mapped_column(
        ForeignKey("production_run_episodes.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[str] = mapped_column(String(80), nullable=False)
    batch_key: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    shot_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PLANNED", nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ProductionRunEvent(Base):
    """A bounded, append-only progress journal for the run view."""

    __tablename__ = "production_run_events"
    __table_args__ = (Index("ix_run_events_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("production_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

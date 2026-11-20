"""Authoritative business ledger models for Manual4 (stored in manual4.sqlite)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shine.manual4.storage.db import M4Base


def m4_new_id() -> str:
    return str(uuid4())


_MAX_CLOCK_AHEAD = timedelta(seconds=1)
_last_m4_utcnow: datetime | None = None


def m4_utcnow() -> datetime:
    """Return strictly increasing naive UTC timestamps for deterministic ordering."""
    global _last_m4_utcnow
    now = datetime.now(UTC).replace(tzinfo=None)
    if _last_m4_utcnow is not None:
        if now <= _last_m4_utcnow and _last_m4_utcnow - now <= _MAX_CLOCK_AHEAD:
            now = _last_m4_utcnow + timedelta(microseconds=1)
    _last_m4_utcnow = now
    return now


class M4SchemaMeta(M4Base):
    __tablename__ = "m4_schema_meta"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4Project(M4Base):
    __tablename__ = "m4_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="Chinese", nullable=False)
    max_shot_seconds: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)

    documents: Mapped[list[M4SourceDocument]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    storyboard_revisions: Mapped[list[M4StoryboardRevision]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class M4SourceDocument(M4Base):
    __tablename__ = "m4_source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_format: Mapped[str] = mapped_column(String(24), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parsed_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)

    project: Mapped[M4Project] = relationship(back_populates="documents")


class M4StoryboardRevision(M4Base):
    __tablename__ = "m4_storyboard_revisions"
    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_m4_storyboard_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("m4_source_documents.id", ondelete="SET NULL"), nullable=True
    )
    source_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    summary_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)

    project: Mapped[M4Project] = relationship(back_populates="storyboard_revisions")


class M4EpisodePlan(M4Base):
    __tablename__ = "m4_episode_plans"
    __table_args__ = (
        UniqueConstraint("storyboard_revision_id", "episode_code", name="uq_m4_episode_rev_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    storyboard_revision_id: Mapped[str] = mapped_column(
        ForeignKey("m4_storyboard_revisions.id", ondelete="CASCADE"), index=True
    )
    episode_no: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    target_total_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    planned_total_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4Shot(M4Base):
    __tablename__ = "m4_shots"
    __table_args__ = (
        UniqueConstraint("storyboard_revision_id", "shot_code", name="uq_m4_shot_rev_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    storyboard_revision_id: Mapped[str] = mapped_column(
        ForeignKey("m4_storyboard_revisions.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[str] = mapped_column(ForeignKey("m4_episode_plans.id", ondelete="CASCADE"), index=True)
    shot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_code: Mapped[str] = mapped_column(String(80), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    target_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dialogue: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    requested_assets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    source_span: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", nullable=False)
    continuity_mode: Mapped[str] = mapped_column(String(32), default="cut", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4MaterialRequirement(M4Base):
    __tablename__ = "m4_material_requirements"
    __table_args__ = (
        UniqueConstraint(
            "storyboard_revision_id",
            "family",
            "canonical_key",
            name="uq_m4_req_rev_family_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    storyboard_revision_id: Mapped[str] = mapped_column(
        ForeignKey("m4_storyboard_revisions.id", ondelete="CASCADE"), index=True
    )
    family: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(120), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    shot_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="missing", nullable=False)
    bound_asset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("m4_asset_versions.id", ondelete="SET NULL"), nullable=True
    )
    user_declined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4AssetVersion(M4Base):
    __tablename__ = "m4_asset_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    family: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(120), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="candidate", nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    promoted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4ShotAssetBinding(M4Base):
    __tablename__ = "m4_shot_asset_bindings"
    __table_args__ = (UniqueConstraint("shot_id", "asset_version_id", name="uq_m4_shot_asset_binding"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("m4_shots.id", ondelete="CASCADE"), index=True)
    asset_version_id: Mapped[str] = mapped_column(
        ForeignKey("m4_asset_versions.id", ondelete="CASCADE"), index=True
    )
    use_as: Mapped[str] = mapped_column(String(24), nullable=False)
    role_label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4ManualPromptRevision(M4Base):
    __tablename__ = "m4_manual_prompt_revisions"
    __table_args__ = (UniqueConstraint("shot_id", "revision", name="uq_m4_prompt_shot_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("m4_shots.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    shot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    generation_mode: Mapped[str] = mapped_column(String(16), default="Ref2VA", nullable=False)
    reference_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    reference_manifest_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    frozen_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4ExecutionSnapshot(M4Base):
    __tablename__ = "m4_execution_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    storyboard_revision_id: Mapped[str] = mapped_column(
        ForeignKey("m4_storyboard_revisions.id", ondelete="CASCADE"), index=True
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="local-user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4ProductionRun(M4Base):
    __tablename__ = "m4_production_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    execution_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("m4_execution_snapshots.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_target_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider: Mapped[str] = mapped_column(String(24), default="comfyui", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4Job(M4Base):
    __tablename__ = "m4_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("m4_production_runs.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    shot_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    shot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    prompt_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    continuity_mode: Mapped[str] = mapped_column(String(32), default="cut", nullable=False)
    previous_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(24), default="comfyui", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    qc_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4JobAttempt(M4Base):
    __tablename__ = "m4_job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", name="uq_m4_job_attempt_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("m4_jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SUBMIT_INTENT", nullable=False)
    prompt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_graph_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submit_intent_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    submit_ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4Delivery(M4Base):
    __tablename__ = "m4_deliveries"
    __table_args__ = (UniqueConstraint("project_id", "episode_id", "version", name="uq_m4_delivery_ep_ver"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    episode_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    edl_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    shots_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    subtitle_srt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4DeliveryAcceptance(M4Base):
    __tablename__ = "m4_delivery_acceptances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("m4_projects.id", ondelete="CASCADE"), index=True)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("m4_deliveries.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    watched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    delivery_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="local-user", nullable=False)
    request_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4WorkerInstance(M4Base):
    __tablename__ = "m4_workers"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="online", nullable=False)
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_phase: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)


class M4RendererState(M4Base):
    __tablename__ = "m4_renderer_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="comfyui-m4")
    status: Mapped[str] = mapped_column(String(32), default="STOPPED", nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handoff_token_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, onupdate=m4_utcnow, nullable=False)


class M4AuditEvent(M4Base):
    __tablename__ = "m4_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=m4_new_id)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    shot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=m4_utcnow, nullable=False)

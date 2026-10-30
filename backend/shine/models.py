from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shine.db import Base


def new_id() -> str:
    return str(uuid4())


# The host wall clock does not advance inside one tick, so two rows written
# milliseconds apart can carry the same timestamp. "Newest row" selection would
# then fall back to a random identifier. The clock below is therefore strictly
# increasing, but never runs further ahead of the real clock than this, so a
# backwards step of the system time cannot pin the recorded time in the future.
_MAX_CLOCK_AHEAD = timedelta(seconds=1)
_last_utcnow: datetime | None = None


def utcnow() -> datetime:
    """Return the current UTC time as a strictly increasing naive timestamp."""
    global _last_utcnow
    now = datetime.now(UTC).replace(tzinfo=None)
    if _last_utcnow is not None:
        if now <= _last_utcnow and _last_utcnow - now <= _MAX_CLOCK_AHEAD:
            now = _last_utcnow + timedelta(microseconds=1)
    _last_utcnow = now
    return now


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="Chinese", nullable=False)
    style_draft: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    frozen_style_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    allow_remote_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_repair_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    assets: Mapped[list[Asset]] = relationship(back_populates="project", cascade="all, delete-orphan")
    style_versions: Mapped[list[StyleVersion]] = relationship(back_populates="project")
    shots: Mapped[list[Shot]] = relationship(back_populates="project", cascade="all, delete-orphan")
    documents: Mapped[list[ProjectDocument]] = relationship(back_populates="project", cascade="all, delete-orphan")
    production_series: Mapped[list[ProductionSeries]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    terminology: Mapped[list[ProjectTerminology]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectTerminology.revision"
    )
    subtitles: Mapped[list[SubtitleTrack]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="SubtitleTrack.revision"
    )


class ProjectDocument(Base):
    __tablename__ = "project_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="documents")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("project_id", "sha256", name="uq_asset_project_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Historical additive migration permits NULL; preserve that DB contract.
    tags: Mapped[list[str] | None] = mapped_column(JSON, default=list, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    derived_from_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="assets")
    bindings: Mapped[list[ShotAssetBinding]] = relationship(back_populates="asset")


class StyleVersion(Base):
    __tablename__ = "style_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_style_project_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="style_versions")


class ProjectTerminology(Base):
    """The script-specific terminology that the ASR model is told about.

    Every revision is kept, so a production that already ran against one
    table keeps the table it actually used.  A new revision never rewrites an
    earlier one.
    """

    __tablename__ = "project_terminology"
    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_project_terminology_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    terms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="human", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="terminology")


class SubtitleTrack(Base):
    """A subtitle layer of the project.

    The subtitles live outside the rendered shot, so a human can revise a line
    or a timing without rendering the shot again.  Every revision is kept and
    the delivery composites the one that was approved.
    """

    __tablename__ = "subtitle_tracks"
    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_subtitle_track_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    cues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    srt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ass: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="alignment", nullable=False)
    source_artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="subtitles")


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="shots")
    jobs: Mapped[list[Job]] = relationship(back_populates="shot")
    bindings: Mapped[list[ShotAssetBinding]] = relationship(
        back_populates="shot", cascade="all, delete-orphan", order_by="ShotAssetBinding.ordinal"
    )


class ShotAssetBinding(Base):
    __tablename__ = "shot_asset_bindings"
    __table_args__ = (UniqueConstraint("shot_id", "asset_id", name="uq_shot_asset_binding"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    use_as: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frame_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    shot: Mapped[Shot] = relationship(back_populates="bindings")
    asset: Mapped[Asset] = relationship(back_populates="bindings")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    shot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    style_version_id: Mapped[str] = mapped_column(ForeignKey("style_versions.id"), nullable=False)
    preproduction_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider: Mapped[str] = mapped_column(String(24), default="mock", nullable=False)
    mock_scenario: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    generation_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    workflow_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    compiled_prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_qc_result: Mapped[str | None] = mapped_column(String(24), nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    semantic_retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_semantic_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    last_repair_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    pending_repair_context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    quality_verification: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    shot: Mapped[Shot] = relationship(back_populates="jobs")
    attempts: Mapped[list[JobAttempt]] = relationship(back_populates="job", cascade="all, delete-orphan")
    dependencies: Mapped[list[JobDependency]] = relationship(
        foreign_keys="JobDependency.child_job_id",
        back_populates="child",
        cascade="all, delete-orphan",
    )


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (UniqueConstraint("parent_job_id", "child_job_id", name="uq_job_dependency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    child_job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="explicit", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    child: Mapped[Job] = relationship(foreign_keys=[child_job_id], back_populates="dependencies")
    parent: Mapped[Job] = relationship(foreign_keys=[parent_job_id])


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", name="uq_attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submit_intent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    submit_ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reconciliation_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_kind: Mapped[str] = mapped_column(String(16), default="initial", nullable=False)
    parent_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameter_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_graph_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repair_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_root_cause: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job: Mapped[Job] = relationship(back_populates="attempts")


class QualityGate(Base):
    """One named quality gate decision for a single job attempt.

    A gate records what was actually checked, not just an overall verdict, so
    a shot can never be marked final-passed while a required semantic or audio
    review was never executed.
    """

    __tablename__ = "quality_gates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gate_name: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage: Mapped[str] = mapped_column(String(32), default="not_covered", nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class QCReport(Base):
    __tablename__ = "qc_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("job_attempts.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(48), default="mock", nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MediaArtifact(Base):
    __tablename__ = "media_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RepairRecord(Base):
    __tablename__ = "repair_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    root_cause: Mapped[str] = mapped_column(String(500), nullable=False)
    changes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    cost_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ProviderProfile(Base):
    __tablename__ = "provider_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_provider_profile_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    api_key_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generation_options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timeout_s: Mapped[float] = mapped_column(Float, default=60.0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AgentRoute(Base):
    __tablename__ = "agent_routes"
    __table_args__ = (UniqueConstraint("role", name="uq_agent_route_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    role: Mapped[str] = mapped_column(String(48), nullable=False)
    provider_profile_id: Mapped[str] = mapped_column(ForeignKey("provider_profiles.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class WorkflowProfile(Base):
    __tablename__ = "workflow_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_workflow_profile_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parameter_map: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    media_transfers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    required_nodes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(160), default="MiniMax-H3-Base", nullable=False)
    graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_identity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validated_against: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ExternalJobReconciliation(Base):
    __tablename__ = "external_job_reconciliations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WorkerInstance(Base):
    __tablename__ = "worker_instances"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="starting", nullable=False)
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RulePack(Base):
    __tablename__ = "rule_packs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ProjectRuleLock(Base):
    __tablename__ = "project_rule_locks"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    rulepack_id: Mapped[str] = mapped_column(ForeignKey("rule_packs.id"), nullable=False)
    rulepack_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AgentTaskRecord(Base):
    __tablename__ = "agent_task_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role_id: Mapped[str] = mapped_column(String(48), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rulepack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    rulepack_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.1", nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.1", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_role: Mapped[str] = mapped_column(String(48), nullable=False)
    source_task_id: Mapped[str] = mapped_column(ForeignKey("agent_task_records.id"), nullable=False)
    input_revision_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

class ProductionSeries(Base):
    """One durable, ordered real-H3 production run for a project.

    A series is the unit the resident Worker claims.  It owns the renderer
    across every one of its shots, so the exclusive GPU lease is transferred
    between shots instead of being released and re-taken.  The row is also the
    ownership proof: only the worker that changed ``worker_id`` may advance or
    finalize the series.
    """

    __tablename__ = "production_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(24), default="comfyui", nullable=False)
    workflow_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    self_review_scope: Mapped[str] = mapped_column(String(16), default="full", nullable=False)
    basic_review_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    mock_scenario: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    shot_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalization_state: Mapped[str] = mapped_column(String(24), default="NONE", nullable=False)
    finalization_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    renderer_identity: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    edit_intent_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delivery_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="production_series")
    items: Mapped[list[ProductionSeriesItem]] = relationship(
        back_populates="series", cascade="all, delete-orphan", order_by="ProductionSeriesItem.position"
    )


class ProductionSeriesItem(Base):
    """One shot inside a production series, in production order."""

    __tablename__ = "production_series_items"
    __table_args__ = (UniqueConstraint("series_id", "position", name="uq_series_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    series_id: Mapped[str] = mapped_column(ForeignKey("production_series.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    continuity_mode: Mapped[str] = mapped_column(String(32), default="cut", nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    series: Mapped[ProductionSeries] = relationship(back_populates="items")
    shot: Mapped[Shot] = relationship()
    job: Mapped[Job | None] = relationship()


# V1.3 extends this same metadata/SQLite ledger.
from shine.comfyops import models as preparation_models  # noqa: E402,F401

# Auto delivery orders ride the same metadata ledger (V1.4).
from shine.media import delivery_models  # noqa: E402,F401

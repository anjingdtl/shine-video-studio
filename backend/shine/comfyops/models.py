from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow

PLAN_PURPOSE_PRODUCTION = "production"
PLAN_PURPOSE_ASSET_PREPARATION = "asset_preparation"
PLAN_PURPOSES = (PLAN_PURPOSE_PRODUCTION, PLAN_PURPOSE_ASSET_PREPARATION)


class RecordMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ComfyInstance(RecordMixin, Base):
    __tablename__ = "comfy_instances"


class EnvironmentSnapshot(RecordMixin, Base):
    __tablename__ = "environment_snapshots"


class ModelPackage(RecordMixin, Base):
    __tablename__ = "model_packages"


class NodePackage(RecordMixin, Base):
    __tablename__ = "node_packages"


class ProjectRecordMixin(RecordMixin):
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)


class InstallationPlan(ProjectRecordMixin, Base):
    __tablename__ = "installation_plans"


class InstallationAttempt(RecordMixin, Base):
    __tablename__ = "installation_attempts"
    plan_id: Mapped[str] = mapped_column(ForeignKey("installation_plans.id"), index=True)


class WorkflowRevision(RecordMixin, Base):
    __tablename__ = "workflow_revisions"


class RightsRecord(ProjectRecordMixin, Base):
    __tablename__ = "rights_records"


class CandidateAsset(ProjectRecordMixin, Base):
    __tablename__ = "candidate_assets"
    formal_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)


class VoiceProfile(ProjectRecordMixin, Base):
    __tablename__ = "voice_profiles"


class VoiceRender(ProjectRecordMixin, Base):
    __tablename__ = "voice_renders"
    voice_id: Mapped[str] = mapped_column(ForeignKey("voice_profiles.id"), index=True)


class PreProductionPlan(ProjectRecordMixin, Base):
    __tablename__ = "preproduction_plans"
    # A material-preparation plan is a director-side draft ledger, never a
    # production contract. Every production selector filters on this column so
    # a newer asset_preparation row cannot hijack the frozen H3 manifest.
    purpose: Mapped[str] = mapped_column(
        String(24), default=PLAN_PURPOSE_PRODUCTION, server_default=PLAN_PURPOSE_PRODUCTION, nullable=False
    )
    __table_args__ = (Index("ix_preproduction_plans_project_purpose", "project_id", "purpose"),)


class AssetRequirement(ProjectRecordMixin, Base):
    __tablename__ = "asset_requirements"
    plan_id: Mapped[str] = mapped_column(ForeignKey("preproduction_plans.id"), index=True)


class ApprovalGrant(Base):
    __tablename__ = "preparation_approval_grants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ResourceLease(Base):
    __tablename__ = "resource_leases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    gpu: Mapped[str] = mapped_column(String(64), default="gpu-0", index=True)
    budget_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    exclusive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

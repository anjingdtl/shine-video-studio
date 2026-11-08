from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow


class LibraryAsset(Base):
    __tablename__ = "library_assets"
    __table_args__ = (
        Index("ix_library_assets_kind_category_status", "kind", "category", "status"),
        Index("ix_library_assets_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    collection: Mapped[str | None] = mapped_column(String(120), nullable=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class LibraryRights(Base):
    __tablename__ = "library_rights"
    __table_args__ = (Index("ix_library_rights_status_expiry", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    license_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    allowed_project_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    permitted_uses: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    commercial_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DECLARED", nullable=False)
    recorded_actor: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryMedia(Base):
    __tablename__ = "library_media"
    __table_args__ = (
        Index("ix_library_media_sha256", "sha256"),
        Index("ix_library_media_purpose_kind", "purpose", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(nullable=True)
    rights_id: Mapped[str] = mapped_column(ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryVersion(Base):
    __tablename__ = "library_versions"
    __table_args__ = (
        UniqueConstraint("library_asset_id", "ordinal", name="uq_library_version_ordinal"),
        Index("ix_library_versions_sha256", "content_sha256"),
        Index("ix_library_versions_asset_status", "library_asset_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_asset_id: Mapped[str] = mapped_column(
        ForeignKey("library_assets.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(nullable=True)
    source_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_revision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    references_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    references_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rights_id: Mapped[str] = mapped_column(ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    qc: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE", nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibrarySourceReference(Base):
    __tablename__ = "library_source_references"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_library_source_reference_order"),
        Index("ix_library_source_media", "media_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("library_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_role: Mapped[str] = mapped_column(String(32), nullable=False)
    media_id: Mapped[str | None] = mapped_column(ForeignKey("library_media.id", ondelete="RESTRICT"), nullable=True)
    source_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mask_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rights_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class LibraryRenderTask(Base):
    __tablename__ = "library_render_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_library_task_idempotency"),
        Index("ix_library_tasks_status_created", "status", "created_at"),
        Index("ix_library_tasks_external_prompt", "external_prompt_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    workflow_revision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    submit_intent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    external_prompt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_outcome: Mapped[str] = mapped_column(String(24), default="NOT_SENT", nullable=False)
    result_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class LibraryTaskAttempt(Base):
    __tablename__ = "library_task_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_no", name="uq_library_task_attempt_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("library_render_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submit_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    external_prompt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_outcome: Mapped[str] = mapped_column(String(24), default="NOT_SENT", nullable=False)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryProjectLink(Base):
    __tablename__ = "library_project_links"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_library_project_link_idempotency"),
        UniqueConstraint(
            "project_id",
            "library_version_id",
            "role",
            "entity_key",
            "usage_scope",
            name="uq_library_project_semantic_assignment",
        ),
        Index("ix_library_links_project", "project_id"),
        Index("ix_library_links_asset_version", "library_asset_id", "library_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    library_asset_id: Mapped[str] = mapped_column(ForeignKey("library_assets.id", ondelete="RESTRICT"), nullable=False)
    library_version_id: Mapped[str] = mapped_column(
        ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=False
    )
    project_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    project_voice_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_key: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    usage_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryVoiceProfile(Base):
    __tablename__ = "library_voice_profiles"
    __table_args__ = (
        UniqueConstraint("library_asset_id", "version_id", name="uq_library_voice_version"),
        Index("ix_library_voice_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_asset_id: Mapped[str] = mapped_column(ForeignKey("library_assets.id", ondelete="RESTRICT"), nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("library_versions.id", ondelete="RESTRICT"), nullable=False)
    conditioning_media_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_media.id", ondelete="RESTRICT"), nullable=True
    )
    voice_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rights_id: Mapped[str] = mapped_column(ForeignKey("library_rights.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PREVIEW", nullable=False)
    frozen_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryApprovalGrant(Base):
    __tablename__ = "library_approval_grants"
    __table_args__ = (Index("ix_library_grant_target_action", "target_id", "action"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

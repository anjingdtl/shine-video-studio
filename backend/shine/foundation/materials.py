"""Platform-neutral Public Asset Center contract shared by director7 and manual4.

This facade exposes immutable asset versions, rights verification, single-writer
image generation task queuing (executed by ``asset_center.execution`` via the
resident worker), cross-project explicit authorization, and lossless historical
mapping from legacy ``M4AssetVersion`` rows into ``LibraryVersion``.

Business bindings (7-stage ``shot_asset_bindings`` vs 4-stage
``m4_shot_asset_bindings``) remain strictly independent in each line.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shine.asset_center import service as ac_service
from shine.asset_center.contracts import canonical_hash
from shine.asset_center.models import (
    LibraryAsset,
    LibraryMedia,
    LibraryRenderTask,
    LibraryRights,
    LibraryVersion,
)
from shine.foundation.environment import check_capability
from shine.media.artifacts import safe_asset_path
from shine.media.output_layout import ensure_storage_writable, output_root, store_library_asset
from shine.media.output_names import readable_base
from shine.models import AuditEvent, utcnow
from shine.quality.technical import _full_decode_check
from shine.security import probe_upload, verify_upload

FAMILY_TO_CATEGORY = {
    "character": "character",
    "scene": "scene",
    "prop": "prop",
    "voice": "voice",
    "reference_video": "other",
}


@dataclass(frozen=True)
class AssetVersionRef:
    library_asset_id: str
    library_version_id: str
    content_sha256: str
    media_kind: str
    category: str
    rights_id: str
    rights_status: str
    rights_snapshot_hash: str
    technical_qc: dict[str, Any] | None
    provenance: dict[str, Any]
    version_hash: str
    storage_path: str
    filename: str
    content_type: str
    width: int | None
    height: int | None
    duration_s: float | None
    status: str
    is_mock: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "library_asset_id": self.library_asset_id,
            "library_version_id": self.library_version_id,
            "content_sha256": self.content_sha256,
            "media_kind": self.media_kind,
            "category": self.category,
            "rights_id": self.rights_id,
            "rights_status": self.rights_status,
            "rights_snapshot_hash": self.rights_snapshot_hash,
            "technical_qc": self.technical_qc,
            "provenance": self.provenance,
            "version_hash": self.version_hash,
            "storage_path": self.storage_path,
            "filename": self.filename,
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "duration_s": self.duration_s,
            "status": self.status,
            "is_mock": self.is_mock,
        }


def _build_ref(db: Session, asset: LibraryAsset, version: LibraryVersion) -> AssetVersionRef:
    rights = db.get(LibraryRights, version.rights_id)
    snap = ac_service.rights_snapshot(rights)
    return AssetVersionRef(
        library_asset_id=asset.id,
        library_version_id=version.id,
        content_sha256=version.content_sha256,
        media_kind=version.kind,
        category=asset.category,
        rights_id=version.rights_id,
        rights_status=rights.status if rights else "REVOKED",
        rights_snapshot_hash=canonical_hash(snap),
        technical_qc=version.qc,
        provenance=dict(version.provenance or {}),
        version_hash=ac_service.version_snapshot(version, rights),
        storage_path=version.storage_path,
        filename=version.filename,
        content_type=version.content_type,
        width=version.width,
        height=version.height,
        duration_s=version.duration_s,
        status=version.status,
        is_mock=bool(version.is_mock),
    )


def register_uploaded_media_to_library(
    platform_db: Session,
    settings: Any,
    *,
    project_id: str,
    actor: str,
    content: bytes,
    filename: str,
    media_kind: str,
    family: str,
    title: str,
) -> AssetVersionRef:
    """Register user-uploaded image/audio into the public Asset Center as READY_FOR_REVIEW."""
    if media_kind not in {"image", "audio"}:
        raise ValueError(f"Library registration only supports image/audio, got {media_kind}")
    category = FAMILY_TO_CATEGORY.get(family, "other")
    purpose = "audio_import" if media_kind == "audio" else "reference"
    media = ac_service.create_media(
        platform_db,
        settings,
        content=content,
        filename=filename,
        kind=media_kind,
        purpose=purpose,
        actor=actor,
        source_type="user_owned",
        license_kind="USER_UPLOADED_SOURCE",
        scope="asset",
        statement="User uploaded reference material",
        subject=title,
        allowed_project_ids=[project_id],
        permitted_uses=["research", "personal", "non_commercial"],
        commercial_allowed=False,
        expires_at=None,
    )
    asset = LibraryAsset(
        title=title[:200],
        kind=media_kind,
        category=category,
        tags=[family, "manual4"],
        collection="manual4",
        created_by=actor,
    )
    platform_db.add(asset)
    platform_db.flush()
    version = ac_service.create_version_from_media(
        platform_db,
        settings,
        asset=asset,
        media=media,
        actor=actor,
        source_mode="UPLOAD",
        provenance={"requesting_line": "manual4", "requesting_project_id": project_id},
        is_mock=False,
    )
    version.qc = {
        "result": "passed",
        "tool": "upload_probe",
        "width": version.width,
        "height": version.height,
        "duration_s": version.duration_s,
    }
    version.status = "READY_FOR_REVIEW"
    platform_db.flush()
    return _build_ref(platform_db, asset, version)


def _run_version_qc(platform_db: Session, settings: Any, version: LibraryVersion) -> dict[str, Any]:
    root = output_root(platform_db, settings)
    content = ac_service.version_bytes(settings, version, root=root)
    verify_upload(version.kind, version.filename, content, settings.max_upload_bytes)
    probe = probe_upload(version.kind, content, version.content_type)
    path = safe_asset_path(root, version.storage_path)
    decoded = _full_decode_check(
        path,
        getattr(settings, "ffmpeg_path", "ffmpeg") or "ffmpeg",
    )
    decoded_dict = decoded.model_dump(mode="json") if hasattr(decoded, "model_dump") else dict(decoded)
    qc = {
        "result": "failed" if decoded_dict.get("status") == "fail" else "passed",
        "width": probe.width,
        "height": probe.height,
        "duration_s": probe.duration_s,
        "decoded": decoded_dict,
    }
    version.qc = qc
    if qc["result"] == "failed":
        version.status = "QC_FAILED"
        raise HTTPException(409, {"code": "LIBRARY_VERSION_QC_FAILED", "message": "素材技术质检未通过。", "qc": qc})
    if version.status == "CANDIDATE":
        version.status = "READY_FOR_REVIEW"
    platform_db.flush()
    return qc


def promote_library_version(
    platform_db: Session,
    settings: Any,
    version_id: str,
    *,
    actor: str,
    project_id: str,
) -> AssetVersionRef:
    """QC, approve rights if declared by local user, issue & consume one-use grant, and promote."""
    version = platform_db.get(LibraryVersion, version_id)
    if version is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "公共素材版本不存在。"})
    if version.is_mock:
        raise HTTPException(
            409,
            {"code": "LIBRARY_MOCK_PROMOTION_FORBIDDEN", "message": "Mock 样本不得晋升为公共正式素材。"},
        )

    asset = platform_db.get(LibraryAsset, version.library_asset_id)
    if asset is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "公共素材主记录不存在。"})

    rights = platform_db.get(LibraryRights, version.rights_id)
    if rights is None or rights.status == "REVOKED":
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_REVOKED", "message": "素材授权已撤销，不可晋升。"})

    # Ensure requesting project is authorized & approve DECLARED local-user rights
    allowed = list(rights.allowed_project_ids or [])
    if "*" not in allowed and project_id not in allowed:
        allowed.append(project_id)
        rights.allowed_project_ids = allowed
    if rights.status == "DECLARED":
        ac_service.enforce_rights_ceiling(
            platform_db,
            rights,
            allowed_project_ids=allowed,
            permitted_uses=list(rights.permitted_uses or ["research"]),
            commercial_allowed=bool(rights.commercial_allowed),
            expires_at=rights.expires_at,
        )
        rights.status = "APPROVED"
        rights.approved_by = actor
        platform_db.flush()

    if version.status == "PROMOTED":
        ac_service.require_version_rights(platform_db, version, project_id=project_id, usage_scope="local")
        return _build_ref(platform_db, asset, version)

    if version.status in {"CANDIDATE", "QC_FAILED"} or version.qc is None:
        _run_version_qc(platform_db, settings, version)

    ac_service.require_version_rights(platform_db, version, project_id=project_id, usage_scope="local")
    grant = ac_service.issue_grant(platform_db, actor, "library_promote", version)
    ac_service.consume_grant(
        platform_db,
        actor,
        "library_promote",
        version,
        grant["grant_id"],
        grant["snapshot"],
    )
    version.status = "PROMOTED"
    version.approved_by = actor
    version.approved_at = utcnow()
    asset.current_version_id = version.id
    asset.state_version += 1
    platform_db.flush()
    return _build_ref(platform_db, asset, version)


def search_library_assets(
    platform_db: Session,
    settings: Any,
    *,
    project_id: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    query: str = "",
    limit: int = 60,
    include_other_projects: bool = True,
) -> list[dict[str, Any]]:
    """Search active PROMOTED non-mock library versions cleanly across projects."""
    stmt = (
        select(LibraryAsset, LibraryVersion)
        .join(LibraryVersion, LibraryVersion.id == LibraryAsset.current_version_id)
        .where(
            LibraryAsset.status == "ACTIVE",
            LibraryVersion.status == "PROMOTED",
            LibraryVersion.is_mock.is_(False),
        )
        .order_by(LibraryAsset.updated_at.desc())
        .limit(max(1, min(200, limit)))
    )
    if kind:
        stmt = stmt.where(LibraryAsset.kind == kind)
    if category:
        stmt = stmt.where(LibraryAsset.category == category)

    q = (query or "").strip().casefold()
    results: list[dict[str, Any]] = []
    for asset, version in platform_db.execute(stmt).all():
        if q:
            haystack = " ".join([asset.title, asset.category, *(asset.tags or [])]).casefold()
            if q not in haystack:
                continue
        rights = platform_db.get(LibraryRights, version.rights_id)
        if rights is None or rights.status != "APPROVED":
            continue
        if rights.expires_at is not None and rights.expires_at <= utcnow():
            continue
        allowed = list(rights.allowed_project_ids or [])
        project_authorized = (project_id is None) or ("*" in allowed) or (project_id in allowed)
        if not include_other_projects and not project_authorized:
            continue
        ref = _build_ref(platform_db, asset, version)
        results.append(
            {
                **ref.to_dict(),
                "title": asset.title,
                "tags": list(asset.tags or []),
                "project_authorized": project_authorized,
                "media_url": f"/api/v1/asset-center/versions/{version.id}/media",
            }
        )
    return results


def authorize_library_version_for_project(
    platform_db: Session,
    settings: Any,
    version_id: str,
    *,
    project_id: str,
    actor: str,
) -> AssetVersionRef:
    """Verify PROMOTED + non-mock + file SHA + rights, explicitly granting project access."""
    version = platform_db.get(LibraryVersion, version_id)
    if version is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "公共素材版本不存在。"})
    if version.is_mock:
        raise HTTPException(409, {"code": "LIBRARY_MOCK_FORBIDDEN", "message": "不允许使用 Mock 素材。"})
    if version.status != "PROMOTED":
        raise HTTPException(
            409,
            {"code": "LIBRARY_VERSION_NOT_PROMOTED", "message": "仅允许引用已晋升为主素材的版本。"},
        )
    asset = platform_db.get(LibraryAsset, version.library_asset_id)
    if asset is None or asset.status != "ACTIVE":
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "公共素材不可用。"})

    root = output_root(platform_db, settings)
    ac_service.version_bytes(settings, version, root=root)

    rights = platform_db.get(LibraryRights, version.rights_id)
    if rights is None or rights.status != "APPROVED":
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_NOT_APPROVED", "message": "素材授权尚未批准或已撤销。"})
    if rights.expires_at is not None and rights.expires_at <= utcnow():
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_EXPIRED", "message": "素材授权已过期。"})
    allowed = list(rights.allowed_project_ids or [])
    if "*" not in allowed and project_id not in allowed:
        rights.allowed_project_ids = [*allowed, project_id]
        platform_db.add(
            AuditEvent(
                project_id=project_id,
                actor=actor,
                event_type="LIBRARY_PROJECT_ACCESS_GRANTED",
                entity_type="library_version",
                entity_id=version.id,
                payload={"library_asset_id": asset.id, "rights_id": rights.id},
            )
        )
        platform_db.flush()

    ac_service.require_version_rights(platform_db, version, project_id=project_id, usage_scope="local")
    return _build_ref(platform_db, asset, version)


def resolve_library_version_file(
    platform_db: Session,
    settings: Any,
    version_id: str,
    *,
    expected_sha256: str | None = None,
) -> tuple[Path, str, str]:
    """Verify disk bytes & SHA-256 and return ``(abs_path, content_type, sha256)``."""
    version = platform_db.get(LibraryVersion, version_id)
    if version is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "公共素材版本不存在。"})
    root = output_root(platform_db, settings)
    raw = ac_service.version_bytes(settings, version, root=root)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise HTTPException(409, {"code": "LIBRARY_MEDIA_SHA_MISMATCH", "message": "公共素材字节哈希不一致。"})
    path = safe_asset_path(root, version.storage_path)
    return path, version.content_type, digest


def enqueue_library_image_generation(
    platform_db: Session,
    settings: Any,
    *,
    project_id: str,
    actor: str,
    family: str,
    title: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    idempotency_key: str,
    negative_prompt: str = "",
) -> dict[str, Any]:
    """Queue ONE authoritative LibraryRenderTask executed by asset_center.execution."""
    local_enabled = (
        getattr(settings, "render_provider", "mock") == "comfyui"
        or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION", "").strip().lower() in {"1", "true", "yes"}
    )
    if not local_enabled:
        raise HTTPException(
            503,
            {
                "code": "M4_LOCAL_COMFY_DISABLED",
                "message": "未启用本地 ComfyUI 执行（SHINE_ENABLE_LOCAL_EXECUTION=0），请直接上传参考图。",
            },
        )
    cap = check_capability(
        platform_db,
        settings,
        recipe={"capability": "image"},
        source_line="manual4",
        project_id=project_id,
    )
    if cap["status"] != "ready":
        raise HTTPException(
            409,
            {
                "code": "M4_QWEN_IMAGE_NOT_READY",
                "message": (
                    "本机 Qwen 出图能力尚未准备就绪；你仍可直接上传图片完成 H3 制作，"
                    "或在「基础配置」补齐出图能力。"
                ),
                "capability": cap,
            },
        )

    category = FAMILY_TO_CATEGORY.get(family, "character")
    if category not in {"character", "costume", "scene", "prop"}:
        raise HTTPException(
            422,
            {"code": "M4_GENERATE_ONLY_SUPPORTS_IMAGE", "message": "仅人物/场景/道具支持图像生成。"},
        )

    payload: dict[str, Any] = {
        "mode": "T2I",
        "category": category,
        "title": title[:200],
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "prompt_template_version": "shine-manual4-user-prompt-v1",
        "output": {"width": int(width), "height": int(height)},
        "seed": int(seed),
        "candidate_index": 0,
        "requesting_line": "manual4",
        "requesting_project_id": project_id,
        "requested_by": actor,
        "capability": {"can_generate": True},
    }
    req_hash = canonical_hash({k: v for k, v in payload.items() if k != "capability"})
    existing = platform_db.scalar(
        select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.request_hash != req_hash:
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "同一生成幂等键被用于不同参数。"})
        return get_library_task_status(platform_db, settings, existing.id)

    task = LibraryRenderTask(
        idempotency_key=idempotency_key,
        request_hash=req_hash,
        kind="image",
        mode="T2I",
        payload=payload,
        status="QUEUED",
        external_outcome="NOT_SENT",
    )
    platform_db.add(task)
    try:
        platform_db.flush()
    except IntegrityError:
        platform_db.rollback()
        existing = platform_db.scalar(
            select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        return get_library_task_status(platform_db, settings, existing.id)

    platform_db.add(
        AuditEvent(
            project_id=project_id,
            actor=actor,
            event_type="M4_LIBRARY_IMAGE_TASK_QUEUED",
            entity_type="library_render_task",
            entity_id=task.id,
            payload={"category": category, "title": title, "seed": seed},
        )
    )
    platform_db.flush()
    return get_library_task_status(platform_db, settings, task.id)


def get_library_task_status(platform_db: Session, settings: Any, task_id: str) -> dict[str, Any]:
    task = platform_db.get(LibraryRenderTask, task_id)
    if task is None:
        raise HTTPException(404, {"code": "LIBRARY_TASK_NOT_FOUND", "message": "生成任务不存在。"})
    ref: AssetVersionRef | None = None
    if task.result_version_id:
        version = platform_db.get(LibraryVersion, task.result_version_id)
        if version is not None:
            asset = platform_db.get(LibraryAsset, version.library_asset_id)
            if asset is not None:
                ref = _build_ref(platform_db, asset, version)
    return {
        "task_id": task.id,
        "status": task.status,
        "external_outcome": task.external_outcome,
        "external_prompt_id": task.external_prompt_id,
        "error_code": task.error_code,
        "result_version_id": task.result_version_id,
        "version_ref": ref.to_dict() if ref else None,
        "updated_at": task.updated_at.isoformat() + "Z",
    }


def backfill_m4_asset_to_library(
    platform_db: Session,
    settings: Any,
    *,
    m4_asset_id: str,
    project_id: str,
    family: str,
    entity_name: str,
    filename: str,
    content_type: str,
    sha256: str,
    abs_file_path: Path,
    status: str,
    is_mock: bool,
    provenance: dict[str, Any] | None = None,
    existing_library_version_id: str | None = None,
    actor: str = "migration",
) -> AssetVersionRef:
    """Losslessly map a legacy M4AssetVersion into the Public Asset Center without modifying original bytes/SHAs.

    * Never promotes ``is_mock=True`` files to ``PROMOTED``.
    * Deduplicates idempotently by ``existing_library_version_id`` or provenance ``m4_asset_version_id``.
    """
    if existing_library_version_id:
        lv = platform_db.get(LibraryVersion, existing_library_version_id)
        if lv is not None:
            la = platform_db.get(LibraryAsset, lv.library_asset_id)
            if la is not None:
                return _build_ref(platform_db, la, lv)

    # Check if already backfilled earlier in provenance
    for lv in platform_db.scalars(
        select(LibraryVersion).where(LibraryVersion.content_sha256 == sha256)
    ).all():
        if (lv.provenance or {}).get("m4_asset_version_id") == m4_asset_id:
            la = platform_db.get(LibraryAsset, lv.library_asset_id)
            if la is not None:
                return _build_ref(platform_db, la, lv)

    if not abs_file_path.is_file():
        raise HTTPException(404, {"code": "M4_ASSET_FILE_MISSING", "message": f"历史素材文件缺失：{filename}"})
    content = abs_file_path.read_bytes()
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != sha256:
        raise HTTPException(409, {"code": "M4_ASSET_SHA_MISMATCH", "message": f"历史素材校验和不一致：{filename}"})

    media_kind = "audio" if (content_type.startswith("audio/") or family == "voice") else "image"
    category = FAMILY_TO_CATEGORY.get(family, "other")
    purpose = "audio_import" if media_kind == "audio" else "reference"

    ensure_storage_writable(platform_db)
    storage_path, _stored = store_library_asset(
        platform_db,
        settings,
        content,
        actual_sha,
        kind=media_kind,
        base=readable_base(filename, media_kind),
        extension=Path(filename).suffix or (".wav" if media_kind == "audio" else ".png"),
    )
    rights_status = "APPROVED" if (status == "promoted" and not is_mock) else "DECLARED"
    rights = LibraryRights(
        source_type="manual4_legacy_migration",
        license_kind="USER_UPLOADED_SOURCE",
        scope="asset",
        statement="Lossless migration from legacy M4AssetVersion",
        subject=entity_name,
        allowed_project_ids=[project_id],
        permitted_uses=["research", "personal", "non_commercial"],
        commercial_allowed=False,
        status=rights_status,
        recorded_actor=actor,
        approved_by=actor if rights_status == "APPROVED" else None,
    )
    platform_db.add(rights)
    platform_db.flush()

    probe = probe_upload(media_kind, content, content_type)
    media = LibraryMedia(
        kind=media_kind,
        purpose=purpose,
        filename=filename,
        content_type=content_type,
        sha256=actual_sha,
        storage_path=storage_path,
        byte_size=len(content),
        width=probe.width,
        height=probe.height,
        duration_s=probe.duration_s,
        rights_id=rights.id,
        created_by=actor,
    )
    platform_db.add(media)
    platform_db.flush()

    la = LibraryAsset(
        title=entity_name[:200],
        kind=media_kind,
        category=category,
        tags=[family, "manual4", "migrated"],
        collection="manual4-migrated",
        created_by=actor,
    )
    platform_db.add(la)
    platform_db.flush()

    target_status = "PROMOTED" if (status == "promoted" and not is_mock) else "CANDIDATE"
    lv = LibraryVersion(
        library_asset_id=la.id,
        ordinal=1,
        filename=filename,
        kind=media_kind,
        content_type=content_type,
        content_sha256=actual_sha,
        storage_path=storage_path,
        byte_size=len(content),
        width=probe.width,
        height=probe.height,
        duration_s=probe.duration_s,
        source_mode="UPLOAD",
        rights_id=rights.id,
        provenance={
            **(provenance or {}),
            "m4_asset_version_id": m4_asset_id,
            "m4_project_id": project_id,
            "migrated_from": "manual4_legacy",
        },
        qc={"result": "passed", "tool": "legacy_migration_sha_verified"},
        status=target_status,
        is_mock=bool(is_mock),
        approved_by=actor if target_status == "PROMOTED" else None,
        approved_at=utcnow() if target_status == "PROMOTED" else None,
        created_by=actor,
    )
    platform_db.add(lv)
    platform_db.flush()
    if target_status == "PROMOTED":
        la.current_version_id = lv.id
    platform_db.flush()
    return _build_ref(platform_db, la, lv)

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.asset_center.contracts import canonical_hash
from shine.asset_center.models import (
    LibraryApprovalGrant,
    LibraryAsset,
    LibraryMedia,
    LibraryProjectLink,
    LibraryRights,
    LibrarySourceReference,
    LibraryVersion,
    LibraryVoiceProfile,
)
from shine.comfy.workflow import graph_hash
from shine.comfyops.models import CandidateAsset, RightsRecord, VoiceProfile
from shine.media.artifacts import safe_asset_path
from shine.models import Asset, Project, utcnow
from shine.security import probe_upload, verify_upload
from shine.services import add_audit, store_asset_file

ASSET_CATEGORIES = {
    "character",
    "scene",
    "prop",
    "costume",
    "voice",
    "dialogue",
    "sfx",
    "music",
    "other",
}
ASSET_ROLES = {
    "character",
    "costume",
    "scene",
    "prop",
    "voice",
    "dialogue",
    "sfx",
    "music",
    "style_reference",
    "generic",
}


def media_bytes(settings, row: LibraryMedia) -> bytes:
    path = safe_asset_path(settings.data_dir, row.storage_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(409, {"code": "LIBRARY_MEDIA_MISSING", "message": "素材文件缺失，请重新导入"}) from exc
    if hashlib.sha256(content).hexdigest() != row.sha256:
        raise HTTPException(409, {"code": "LIBRARY_MEDIA_HASH_CHANGED", "message": "素材校验失败，请重新导入"})
    return content


def version_bytes(settings, row: LibraryVersion) -> bytes:
    path = safe_asset_path(settings.data_dir, row.storage_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            409, {"code": "LIBRARY_VERSION_MISSING", "message": "正式文件缺失，请检查本机存储"}
        ) from exc
    if hashlib.sha256(content).hexdigest() != row.content_sha256:
        raise HTTPException(409, {"code": "LIBRARY_VERSION_HASH_CHANGED", "message": "素材校验失败，已阻止使用"})
    return content


def rights_snapshot(rights: LibraryRights) -> dict[str, Any]:
    return {
        "id": rights.id,
        "source_type": rights.source_type,
        "license_kind": rights.license_kind,
        "scope": rights.scope,
        "statement": rights.statement,
        "subject": rights.subject,
        "allowed_project_ids": rights.allowed_project_ids or [],
        "permitted_uses": rights.permitted_uses or [],
        "commercial_allowed": rights.commercial_allowed,
        "status": rights.status,
        "expires_at": rights.expires_at.isoformat() if rights.expires_at else None,
        "revoked_at": rights.revoked_at.isoformat() if rights.revoked_at else None,
    }


def require_rights(rights: LibraryRights | None, *, project_id: str | None = None, usage_scope: str | None = None):
    now = utcnow()
    if rights is None or rights.status not in {"APPROVED", "DECLARED"} or rights.revoked_at is not None:
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_NOT_APPROVED", "message": "请先确认素材来源与授权范围"})
    if rights.expires_at is not None and rights.expires_at <= now:
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_EXPIRED", "message": "这项授权已过期，请更新授权后再使用"})
    if usage_scope == "local":
        return rights
    if (
        project_id is not None
        and "*" not in (rights.allowed_project_ids or [])
        and project_id not in (rights.allowed_project_ids or [])
    ):
        raise HTTPException(403, {"code": "LIBRARY_PROJECT_NOT_AUTHORIZED", "message": "这项素材没有授权给所选作品"})
    if usage_scope == "commercial" and (rights.status != "APPROVED" or not rights.commercial_allowed):
        raise HTTPException(409, {"code": "COMMERCIAL_LICENSE_REQUIRED", "message": "当前授权不包含商业用途"})
    if usage_scope and usage_scope not in (rights.permitted_uses or []) and "all" not in (rights.permitted_uses or []):
        raise HTTPException(409, {"code": "LIBRARY_USAGE_NOT_AUTHORIZED", "message": "授权范围不包含所选用途"})
    return rights


def require_version_rights(
    db: Session,
    version: LibraryVersion,
    *,
    project_id: str | None = None,
    usage_scope: str | None = None,
    _visited: set[str] | None = None,
) -> LibraryRights:
    """Recheck a version's own grant and every immutable source grant it depends on."""
    visited = _visited if _visited is not None else set()
    if version.id in visited:
        raise HTTPException(409, {"code": "LIBRARY_SOURCE_CYCLE", "message": "素材版本来源存在循环，已阻止使用"})
    visited.add(version.id)
    rights = require_rights(db.get(LibraryRights, version.rights_id), project_id=project_id, usage_scope=usage_scope)
    references = db.scalars(select(LibrarySourceReference).where(LibrarySourceReference.version_id == version.id)).all()
    for reference in references:
        if reference.media_id:
            source = db.get(LibraryMedia, reference.media_id)
            if source is None or source.sha256 != reference.sha256:
                raise HTTPException(409, {"code": "LIBRARY_SOURCE_CHANGED", "message": "来源素材已变化，请重新审核"})
            source_rights = require_rights(
                db.get(LibraryRights, source.rights_id), project_id=project_id, usage_scope=usage_scope
            )
        elif reference.source_version_id:
            source = db.get(LibraryVersion, reference.source_version_id)
            if source is None or source.content_sha256 != reference.sha256:
                raise HTTPException(409, {"code": "LIBRARY_SOURCE_CHANGED", "message": "来源版本已变化，请重新审核"})
            source_rights = require_version_rights(
                db, source, project_id=project_id, usage_scope=usage_scope, _visited=visited
            )
        else:
            raise HTTPException(409, {"code": "LIBRARY_SOURCE_MISSING", "message": "素材版本缺少来源记录"})
        source_snapshot = reference.rights_snapshot or {}
        if source_snapshot.get("id") != source_rights.id:
            raise HTTPException(
                409,
                {"code": "LIBRARY_SOURCE_RIGHTS_CHANGED", "message": "来源授权记录已替换，请重新生成或审核"},
            )
    visited.remove(version.id)
    return rights


def rights_ceiling_for_version(db: Session, version: LibraryVersion) -> dict[str, Any] | None:
    """Return the maximum rights an explicitly reviewed derivative may receive."""
    rights = db.get(LibraryRights, version.rights_id)
    if rights is None:
        return {"allowed_project_ids": [], "permitted_uses": [], "commercial_allowed": False}
    source_scope: set[str] | None = None
    source_uses: set[str] | None = None
    source_commercial = True
    source_expiry: datetime | None = None
    if rights.source_type == "project_asset":
        source_scope = {str((version.provenance or {}).get("source_project_id") or "")}
        source_scope.discard("")
        source_project_rights = db.get(RightsRecord, (version.provenance or {}).get("source_rights_id"))
        if source_project_rights is not None:
            source_uses = set(source_project_rights.payload.get("permitted_uses") or [])
            source_commercial = bool(source_project_rights.payload.get("commercial_allowed"))
            source_expiry = parse_expiry(source_project_rights.payload.get("expires_at"))
    else:
        source_rows = db.scalars(
            select(LibrarySourceReference).where(LibrarySourceReference.version_id == version.id)
        ).all()
        if not source_rows:
            if rights.source_type.startswith("qwen_") or rights.license_kind.upper().startswith("QWEN"):
                # A prompt-only model result may be assigned after a person
                # records an explicit project scope, but the model grants none.
                source_scope = {"*"}
                source_uses = {"research"}
                source_commercial = False
            else:
                return None
        else:
            scopes: list[set[str] | None] = []
            uses: list[set[str] | None] = []
            for reference in source_rows:
                if reference.media_id:
                    source = db.get(LibraryMedia, reference.media_id)
                    source_rights = db.get(LibraryRights, source.rights_id) if source else None
                else:
                    source = db.get(LibraryVersion, reference.source_version_id)
                    source_rights = db.get(LibraryRights, source.rights_id) if source else None
                if source_rights is None:
                    return {"allowed_project_ids": [], "permitted_uses": [], "commercial_allowed": False}
                allowed = set(source_rights.allowed_project_ids or [])
                scopes.append(None if "*" in allowed else allowed)
                permitted = set(source_rights.permitted_uses or [])
                uses.append(None if "all" in permitted else permitted)
                source_commercial = source_commercial and source_rights.commercial_allowed
                if source_rights.expires_at is not None and (
                    source_expiry is None or source_rights.expires_at < source_expiry
                ):
                    source_expiry = source_rights.expires_at
            finite_scopes = [scope for scope in scopes if scope is not None]
            finite_uses = [scope for scope in uses if scope is not None]
            source_scope = set.intersection(*finite_scopes) if finite_scopes else {"*"}
            source_uses = set.intersection(*finite_uses) if finite_uses else {"all"}
            if rights.source_type.startswith("qwen_") or rights.license_kind.upper().startswith("QWEN"):
                source_uses = source_uses & {"research"} if "all" not in source_uses else {"research"}
                source_commercial = False
    return {
        "allowed_project_ids": sorted(source_scope or []),
        "permitted_uses": sorted(source_uses or []),
        "commercial_allowed": source_commercial,
        "expires_at": source_expiry,
    }


def enforce_rights_ceiling(
    db: Session,
    rights: LibraryRights,
    *,
    allowed_project_ids: list[str],
    permitted_uses: list[str],
    commercial_allowed: bool,
    expires_at: datetime | None = None,
) -> None:
    versions = db.scalars(select(LibraryVersion).where(LibraryVersion.rights_id == rights.id)).all()
    ceilings = [rights_ceiling_for_version(db, version) for version in versions]
    ceilings = [row for row in ceilings if row is not None]
    if not ceilings:
        return
    project_scopes = [set(row["allowed_project_ids"]) for row in ceilings if "*" not in row["allowed_project_ids"]]
    maximum_projects = set.intersection(*project_scopes) if project_scopes else {"*"}
    use_scopes = [set(row["permitted_uses"]) for row in ceilings if "all" not in row["permitted_uses"]]
    maximum_uses = set.intersection(*use_scopes) if use_scopes else {"all"}
    requested_projects = set(allowed_project_ids)
    requested_uses = set(permitted_uses)
    if ("*" in requested_projects and "*" not in maximum_projects) or (
        "*" not in maximum_projects and not requested_projects.issubset(maximum_projects)
    ):
        raise HTTPException(409, {"code": "RIGHTS_SCOPE_EXCEEDS_SOURCE", "message": "授权项目范围不能超过原始来源许可"})
    if "all" not in maximum_uses and not requested_uses.issubset(maximum_uses):
        raise HTTPException(409, {"code": "RIGHTS_USE_EXCEEDS_SOURCE", "message": "授权用途不能超过原始来源许可"})
    if (commercial_allowed or "commercial" in requested_uses) and not all(
        row["commercial_allowed"] for row in ceilings
    ):
        raise HTTPException(409, {"code": "COMMERCIAL_LICENSE_REQUIRED", "message": "来源许可不包含商业用途"})
    maximum_expiries = [row["expires_at"] for row in ceilings if row.get("expires_at") is not None]
    if maximum_expiries and (expires_at is None or expires_at > min(maximum_expiries)):
        raise HTTPException(409, {"code": "RIGHTS_EXPIRY_EXCEEDS_SOURCE", "message": "授权有效期不能超过原始来源许可"})


def create_media(
    db: Session,
    settings,
    *,
    content: bytes,
    filename: str,
    kind: str,
    purpose: str,
    actor: str,
    source_type: str,
    license_kind: str,
    scope: str,
    statement: str,
    subject: str,
    allowed_project_ids: list[str],
    permitted_uses: list[str],
    commercial_allowed: bool,
    expires_at: datetime | None,
) -> LibraryMedia:
    content_type = verify_upload(kind, filename, content, settings.max_upload_bytes)
    info = probe_upload(kind, content, content_type)
    digest = hashlib.sha256(content).hexdigest()
    storage_path = store_asset_file(settings.data_dir, digest, content)
    rights = LibraryRights(
        source_type=source_type,
        license_kind=license_kind,
        scope=scope,
        statement=statement,
        subject=subject,
        allowed_project_ids=allowed_project_ids,
        permitted_uses=permitted_uses,
        commercial_allowed=commercial_allowed,
        status="DECLARED",
        recorded_actor=actor,
        expires_at=expires_at,
    )
    db.add(rights)
    db.flush()
    row = LibraryMedia(
        kind=kind,
        purpose=purpose,
        filename=filename,
        content_type=content_type,
        sha256=digest,
        storage_path=storage_path,
        byte_size=len(content),
        width=info.width,
        height=info.height,
        duration_s=info.duration_s,
        rights_id=rights.id,
        created_by=actor,
    )
    db.add(row)
    db.flush()
    add_audit(
        db,
        actor,
        "LIBRARY_MEDIA_IMPORTED",
        "library_media",
        row.id,
        {"kind": kind, "purpose": purpose, "sha256": digest, "rights_id": rights.id, "license_kind": license_kind},
    )
    return row


def create_version_from_media(
    db: Session,
    settings,
    *,
    asset: LibraryAsset,
    media: LibraryMedia,
    actor: str,
    source_mode: str = "UPLOAD",
    prompt: str = "",
    parent_version_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    references_manifest: list[dict[str, Any]] | None = None,
    workflow_revision_hash: str | None = None,
    is_mock: bool = False,
) -> LibraryVersion:
    if asset.kind != media.kind:
        raise HTTPException(409, {"code": "LIBRARY_KIND_MISMATCH", "message": "素材分类与导入文件类型不匹配"})
    if media.purpose == "mask":
        raise HTTPException(
            409, {"code": "MASK_CANNOT_BE_LIBRARY_VERSION", "message": "遮罩只能作为修订输入，不能当作正式素材"}
        )
    if parent_version_id:
        parent = db.get(LibraryVersion, parent_version_id)
        if parent is None or parent.library_asset_id != asset.id:
            raise HTTPException(409, {"code": "LIBRARY_LINEAGE_MISMATCH", "message": "新版本必须来自同一项全局素材"})
    media_bytes(settings, media)
    ordinal = (
        db.scalar(
            select(LibraryVersion.ordinal)
            .where(LibraryVersion.library_asset_id == asset.id)
            .order_by(LibraryVersion.ordinal.desc())
            .limit(1)
        )
        or 0
    ) + 1
    snapshot = rights_snapshot(db.get(LibraryRights, media.rights_id))
    refs = references_manifest or []
    row = LibraryVersion(
        library_asset_id=asset.id,
        ordinal=ordinal,
        parent_version_id=parent_version_id,
        filename=media.filename,
        kind=media.kind,
        content_type=media.content_type,
        content_sha256=media.sha256,
        storage_path=media.storage_path,
        byte_size=media.byte_size,
        width=media.width,
        height=media.height,
        duration_s=media.duration_s,
        source_mode=source_mode,
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
        workflow_revision_hash=workflow_revision_hash,
        references_manifest=refs,
        references_manifest_hash=canonical_hash(refs) if refs else None,
        rights_id=media.rights_id,
        provenance={
            **(provenance or {}),
            "source_media_id": media.id,
            "media_purpose": media.purpose,
            "rights_snapshot": snapshot,
        },
        status="CANDIDATE",
        is_mock=is_mock,
        created_by=actor,
    )
    db.add(row)
    db.flush()
    for entry in refs:
        source_media = db.get(LibraryMedia, entry.get("media_id")) if entry.get("media_id") else None
        source_version = db.get(LibraryVersion, entry.get("version_id")) if entry.get("version_id") else None
        source_sha = source_media.sha256 if source_media else source_version.content_sha256 if source_version else None
        if not source_sha:
            raise HTTPException(409, {"code": "LIBRARY_REFERENCE_MISSING", "message": "参考素材不存在"})
        source_rights = db.get(LibraryRights, source_media.rights_id if source_media else source_version.rights_id)
        db.add(
            LibrarySourceReference(
                version_id=row.id,
                ordinal=int(entry["order"]),
                slot_role=str(entry["role"]),
                media_id=source_media.id if source_media else None,
                source_version_id=source_version.id if source_version else None,
                sha256=source_sha,
                mask_sha256=entry.get("mask_sha256"),
                rights_snapshot=entry.get("rights_snapshot") or rights_snapshot(source_rights),
            )
        )
    add_audit(
        db,
        actor,
        "LIBRARY_VERSION_CANDIDATE_CREATED",
        "library_version",
        row.id,
        {"asset_id": asset.id, "ordinal": ordinal, "content_sha256": media.sha256, "source_mode": source_mode},
    )
    return row


def version_snapshot(row: LibraryVersion, rights: LibraryRights | None = None) -> str:
    return graph_hash(
        {
            "id": row.id,
            "asset_id": row.library_asset_id,
            "ordinal": row.ordinal,
            "parent_version_id": row.parent_version_id,
            "sha256": row.content_sha256,
            "source_mode": row.source_mode,
            "prompt_sha256": row.prompt_sha256,
            "workflow_revision_hash": row.workflow_revision_hash,
            "references_manifest_hash": row.references_manifest_hash,
            "rights_id": row.rights_id,
            "rights_snapshot_hash": canonical_hash(rights_snapshot(rights)) if rights else None,
            "status": row.status,
            "is_mock": row.is_mock,
        }
    )


def issue_grant(db: Session, actor: str, action: str, row: LibraryVersion):
    grant = LibraryApprovalGrant(
        id=secrets.token_hex(32),
        actor=actor,
        action=action,
        target_id=row.id,
        snapshot=version_snapshot(row, db.get(LibraryRights, row.rights_id)),
        expires_at=utcnow() + timedelta(minutes=5),
        used=False,
    )
    db.add(grant)
    add_audit(
        db,
        actor,
        "LIBRARY_REVIEW_GRANT_ISSUED",
        "library_version",
        row.id,
        {"action": action, "snapshot": grant.snapshot},
    )
    db.commit()
    return {"grant_id": grant.id, "snapshot": grant.snapshot, "expires_at": grant.expires_at}


def consume_grant(db: Session, actor: str, action: str, row: LibraryVersion, grant_id: str, expected: str):
    current = version_snapshot(row, db.get(LibraryRights, row.rights_id))
    if current != expected:
        raise HTTPException(409, {"code": "LIBRARY_VERSION_CHANGED", "message": "素材已变化，请重新检查"})
    result = db.execute(
        update(LibraryApprovalGrant)
        .where(
            LibraryApprovalGrant.id == grant_id,
            LibraryApprovalGrant.actor == actor,
            LibraryApprovalGrant.action == action,
            LibraryApprovalGrant.target_id == row.id,
            LibraryApprovalGrant.snapshot == expected,
            LibraryApprovalGrant.used.is_(False),
            LibraryApprovalGrant.expires_at > utcnow(),
        )
        .values(used=True)
    )
    if result.rowcount != 1:
        raise HTTPException(409, {"code": "LIBRARY_APPROVAL_REQUIRED_OR_REPLAYED", "message": "审核授权已过期或已使用"})


def asset_data(db: Session, row: LibraryAsset, *, include_versions: bool = False) -> dict[str, Any]:
    versions = db.scalars(
        select(LibraryVersion).where(LibraryVersion.library_asset_id == row.id).order_by(LibraryVersion.ordinal.desc())
    ).all()
    current = db.get(LibraryVersion, row.current_version_id) if row.current_version_id else None
    result = {
        "id": row.id,
        "title": row.title,
        "kind": row.kind,
        "category": row.category,
        "tags": row.tags or [],
        "collection": row.collection,
        "favorite": row.favorite,
        "status": row.status,
        "state_version": row.state_version,
        "created_at": row.created_at,
        "version_count": len(versions),
        "current_version": version_data(db, current) if current else None,
    }
    if include_versions:
        result["versions"] = [version_data(db, version) for version in versions]
    return result


def version_data(db: Session, row: LibraryVersion | None) -> dict[str, Any] | None:
    if row is None:
        return None
    rights = db.get(LibraryRights, row.rights_id)
    return {
        "id": row.id,
        "ordinal": row.ordinal,
        "parent_version_id": row.parent_version_id,
        "filename": row.filename,
        "kind": row.kind,
        "content_type": row.content_type,
        "content_sha256": row.content_sha256,
        "byte_size": row.byte_size,
        "width": row.width,
        "height": row.height,
        "duration_s": row.duration_s,
        "source_mode": row.source_mode,
        "prompt": row.prompt,
        "workflow_revision_hash": row.workflow_revision_hash,
        "references_manifest": row.references_manifest or [],
        "rights": rights_snapshot(rights) if rights else None,
        "rights_snapshot_hash": canonical_hash(rights_snapshot(rights)) if rights else None,
        "provenance": row.provenance or {},
        "qc": row.qc,
        "status": row.status,
        "is_mock": row.is_mock,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "created_at": row.created_at,
        "media_url": f"/api/v1/asset-center/versions/{row.id}/media",
    }


def assignment_snapshot(
    version: LibraryVersion, rights: LibraryRights, project_id: str, role: str, entity_id: str | None, usage_scope: str
):
    return canonical_hash(
        {
            "project_id": project_id,
            "version_id": version.id,
            "content_sha256": version.content_sha256,
            "rights": rights_snapshot(rights),
            "role": role,
            "entity_id": entity_id,
            "usage_scope": usage_scope,
        }
    )


def assign_to_project(
    db: Session,
    settings,
    *,
    project_id: str,
    version: LibraryVersion,
    role: str,
    entity_id: str | None,
    usage_scope: str,
    idempotency_key: str,
    actor: str,
):
    if role not in ASSET_ROLES:
        raise HTTPException(422, {"code": "ASSET_ROLE_INVALID", "message": "请选择有效的项目素材用途"})
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "找不到所选影视项目"})
    existing = db.scalar(select(LibraryProjectLink).where(LibraryProjectLink.idempotency_key == idempotency_key))
    if existing:
        if (
            existing.project_id != project_id
            or existing.library_version_id != version.id
            or existing.role != role
            or existing.entity_key != (entity_id or "")
            or existing.usage_scope != usage_scope
        ):
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "本次操作编号已用于其他素材"})
        return existing
    if version.status != "PROMOTED" or version.is_mock:
        raise HTTPException(
            409, {"code": "LIBRARY_VERSION_NOT_ASSIGNABLE", "message": "只有人工晋升的真实版本可以进入项目"}
        )
    rights = require_version_rights(db, version, project_id=project_id, usage_scope=usage_scope)
    content = version_bytes(settings, version)
    if version.kind not in {"image", "audio", "video"}:
        raise HTTPException(409, {"code": "PROJECT_ASSET_KIND_UNSUPPORTED", "message": "此文件类型不能分配到项目素材"})
    if role in {"voice", "dialogue", "sfx", "music"} and version.kind != "audio":
        raise HTTPException(409, {"code": "PROJECT_ASSET_ROLE_KIND_MISMATCH", "message": "声音用途只能分配音频素材"})
    if role in {"character", "costume", "scene", "prop", "style_reference"} and version.kind != "image":
        raise HTTPException(409, {"code": "PROJECT_ASSET_ROLE_KIND_MISMATCH", "message": "此用途只能分配图片素材"})
    digest = hashlib.sha256(content).hexdigest()
    if digest != version.content_sha256:
        raise HTTPException(409, {"code": "LIBRARY_VERSION_HASH_CHANGED", "message": "素材校验失败"})
    semantic_existing = db.scalar(
        select(LibraryProjectLink).where(
            LibraryProjectLink.project_id == project_id,
            LibraryProjectLink.library_version_id == version.id,
            LibraryProjectLink.role == role,
            LibraryProjectLink.entity_id.is_(None) if entity_id is None else LibraryProjectLink.entity_id == entity_id,
            LibraryProjectLink.usage_scope == usage_scope,
        )
    )
    if semantic_existing:
        return semantic_existing

    asset_rights_payload = {
        "scope": "asset",
        "license_id": rights.license_kind,
        "purpose": f"Shine Assets assignment ({usage_scope})",
        "expires_at": rights.expires_at.isoformat() if rights.expires_at else None,
        "commercial_allowed": rights.commercial_allowed,
        "permitted_uses": rights.permitted_uses or [],
        "source_rights_snapshot_hash": canonical_hash(rights_snapshot(rights)),
        "source_version_sha256": digest,
        "shine_assets_sources": [
            {
                "kind": "version",
                "version_id": version.id,
                "rights_snapshot_hash": canonical_hash(rights_snapshot(rights)),
                "usage_scope": usage_scope,
            }
        ],
    }
    project_asset_rights = RightsRecord(
        project_id=project.id,
        status="APPROVED",
        payload=asset_rights_payload,
        sha256=graph_hash(asset_rights_payload),
    )
    db.add(project_asset_rights)
    db.flush()

    global_voice = None
    clone_reference = None
    clone_rights = None
    if role == "voice":
        global_voice = db.scalar(select(LibraryVoiceProfile).where(LibraryVoiceProfile.version_id == version.id))
        if global_voice is None or global_voice.status != "FROZEN":
            raise HTTPException(409, {"code": "GLOBAL_VOICE_NOT_FROZEN", "message": "音色必须先完成全局人工试听与冻结"})
        global_voice_rights = require_rights(
            db.get(LibraryRights, global_voice.rights_id), project_id=project_id, usage_scope=usage_scope
        )
        voice_scope = "voice_clone" if global_voice.source == "AUTHORIZED_CLONE" else "voice_design"
        if voice_scope == "voice_clone":
            sample = (
                db.get(LibraryMedia, global_voice.conditioning_media_id) if global_voice.conditioning_media_id else None
            )
            if sample is None or sample.kind != "audio" or sample.purpose != "voice_sample":
                raise HTTPException(
                    409, {"code": "VOICE_CONDITIONING_SAMPLE_MISSING", "message": "授权克隆音色缺少原始授权录音"}
                )
            sample_rights = require_rights(
                db.get(LibraryRights, sample.rights_id), project_id=project_id, usage_scope=usage_scope
            )
            media_bytes(settings, sample)
            sample_rights_payload = {
                "scope": "asset",
                "license_id": sample_rights.license_kind,
                "purpose": "Authorized voice conditioning sample",
                "expires_at": sample_rights.expires_at.isoformat() if sample_rights.expires_at else None,
                "permitted_uses": sample_rights.permitted_uses or [],
                "source_rights_snapshot_hash": canonical_hash(rights_snapshot(sample_rights)),
                "source_sha256": sample.sha256,
                "shine_assets_sources": [
                    {
                        "kind": "media",
                        "media_id": sample.id,
                        "rights_id": sample_rights.id,
                        "rights_snapshot_hash": canonical_hash(rights_snapshot(sample_rights)),
                        "usage_scope": usage_scope,
                    }
                ],
            }
            sample_project_rights = RightsRecord(
                project_id=project.id,
                status="APPROVED",
                payload=sample_rights_payload,
                sha256=graph_hash(sample_rights_payload),
            )
            db.add(sample_project_rights)
            db.flush()
            sample_candidate_payload = {
                "family": "voice",
                "entity_id": entity_id or global_voice.description[:80] or "voice",
                "kind": "audio",
                "filename": sample.filename,
                "content_type": sample.content_type,
                "content_sha256": sample.sha256,
                "byte_size": sample.byte_size,
                "storage_path": sample.storage_path,
                "rights_id": sample_project_rights.id,
                "provenance": {
                    "source": "shine_assets_voice_conditioning",
                    "is_mock": False,
                    "rights_snapshot": rights_snapshot(sample_rights),
                },
                "parent_id": None,
                "media": {"width": sample.width, "height": sample.height, "duration_s": sample.duration_s},
                "qc": {"technical": "pass", "sha256": sample.sha256},
            }
            clone_reference = CandidateAsset(
                project_id=project.id,
                payload=sample_candidate_payload,
                sha256=graph_hash(sample_candidate_payload),
                status="PROMOTED",
            )
            db.add(clone_reference)
            db.flush()
            clone_rights_payload = {
                "scope": "voice_clone",
                "license_id": sample_rights.license_kind,
                "purpose": "Authorized clone reuse under the global voice approval",
                "expires_at": sample_rights.expires_at.isoformat() if sample_rights.expires_at else None,
                "reference_candidate_id": clone_reference.id,
                "permitted_uses": sample_rights.permitted_uses or [],
                "source_rights_snapshot_hash": canonical_hash(rights_snapshot(sample_rights)),
                "shine_assets_sources": [
                    {
                        "kind": "version",
                        "version_id": version.id,
                        "rights_snapshot_hash": canonical_hash(rights_snapshot(rights)),
                        "usage_scope": usage_scope,
                    },
                    {
                        "kind": "rights",
                        "rights_id": global_voice.rights_id,
                        "rights_snapshot_hash": canonical_hash(rights_snapshot(global_voice_rights)),
                        "usage_scope": usage_scope,
                    },
                    {
                        "kind": "media",
                        "media_id": sample.id,
                        "rights_id": sample_rights.id,
                        "rights_snapshot_hash": canonical_hash(rights_snapshot(sample_rights)),
                        "usage_scope": usage_scope,
                    },
                ],
            }
            clone_rights = RightsRecord(
                project_id=project.id,
                status="APPROVED",
                payload=clone_rights_payload,
                sha256=graph_hash(clone_rights_payload),
            )
            db.add(clone_rights)
            db.flush()

    project_asset = Asset(
        project_id=project.id,
        filename=version.filename,
        kind=version.kind,
        content_type=version.content_type,
        sha256=digest,
        storage_path=version.storage_path,
        byte_size=version.byte_size,
        role=role,
        entity_id=entity_id,
        tags=["shine-assets", role, *sorted(set(db.get(LibraryAsset, version.library_asset_id).tags or []))],
        duration_s=version.duration_s,
        width=version.width,
        height=version.height,
        approved=True,
        locked=True,
        approved_by=actor,
        approved_at=utcnow(),
        locked_at=utcnow(),
        provenance={
            "source": "shine_assets_library",
            "library_asset_id": version.library_asset_id,
            "library_version_id": version.id,
            "library_sha256": digest,
            "rights_id": project_asset_rights.id,
            "rights_snapshot": rights_snapshot(rights),
            "usage_scope": usage_scope,
            "is_mock": version.is_mock,
        },
    )
    db.add(project_asset)
    db.flush()
    project_voice = None
    if role == "voice" and global_voice is not None:
        candidate_payload = {
            "family": "voice",
            "entity_id": entity_id or db.get(LibraryAsset, version.library_asset_id).title,
            "kind": "audio",
            "filename": version.filename,
            "content_type": version.content_type,
            "content_sha256": digest,
            "byte_size": version.byte_size,
            "storage_path": version.storage_path,
            "rights_id": project_asset_rights.id,
            "provenance": {
                "source": "shine_assets_library",
                "library_version_id": version.id,
                "is_mock": False,
                "rights_snapshot": rights_snapshot(rights),
            },
            "parent_id": None,
            "media": {"width": version.width, "height": version.height, "duration_s": version.duration_s},
            "qc": version.qc or {"technical": "pass", "sha256": digest},
        }
        candidate = CandidateAsset(
            project_id=project.id,
            payload=candidate_payload,
            sha256=graph_hash(candidate_payload),
            status="PROMOTED",
            formal_asset_id=project_asset.id,
        )
        db.add(candidate)
        db.flush()
        project_asset.provenance = {**(project_asset.provenance or {}), "candidate_id": candidate.id}
        actor_profiles = db.scalars(select(VoiceProfile).where(VoiceProfile.project_id == project.id)).all()
        character = entity_id or db.get(LibraryAsset, version.library_asset_id).title
        prior_versions = [
            int((profile.payload or {}).get("version", 0))
            for profile in actor_profiles
            if (profile.payload or {}).get("character") == character
        ]
        voice_payload = {
            "character": character,
            "source": global_voice.source,
            "description": global_voice.description,
            "text": "",
            "rights_id": clone_rights.id if clone_rights else project_asset_rights.id,
            "asset_rights_id": project_asset_rights.id,
            "reference_id": clone_reference.id if clone_reference else None,
            "candidate_id": candidate.id,
            "conditioning_hash": digest,
            "is_mock": False,
            "language": "zh-CN",
            "version": max(prior_versions, default=0) + 1,
            "style_id": project.frozen_style_version_id,
            "shine_assets_library_version_id": version.id,
        }
        project_voice = VoiceProfile(
            project_id=project.id, payload=voice_payload, sha256=graph_hash(voice_payload), status="FROZEN"
        )
        db.add(project_voice)
        db.flush()
    link = LibraryProjectLink(
        project_id=project.id,
        library_asset_id=version.library_asset_id,
        library_version_id=version.id,
        project_asset_id=project_asset.id,
        project_voice_profile_id=project_voice.id if project_voice else None,
        role=role,
        entity_id=entity_id,
        entity_key=entity_id or "",
        usage_scope=usage_scope,
        rights_snapshot_hash=assignment_snapshot(version, rights, project_id, role, entity_id, usage_scope),
        idempotency_key=idempotency_key,
        created_by=actor,
    )
    db.add(link)
    db.flush()
    add_audit(
        db,
        actor,
        "LIBRARY_VERSION_ASSIGNED_TO_PROJECT",
        "library_project_link",
        link.id,
        {
            "library_asset_id": version.library_asset_id,
            "library_version_id": version.id,
            "project_asset_id": project_asset.id,
            "role": role,
            "entity_id": entity_id,
            "content_sha256": digest,
            "usage_scope": usage_scope,
        },
        project_id=project.id,
    )
    return link


def parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, {"code": "RIGHTS_EXPIRY_INVALID", "message": "授权到期时间格式不正确"}) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    if parsed <= utcnow():
        raise HTTPException(422, {"code": "RIGHTS_EXPIRY_IN_PAST", "message": "授权到期时间必须在未来"})
    return parsed


def safe_json_list(value: str | None, *, label: str, max_length: int = 30) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except ValueError as exc:
        raise HTTPException(422, {"code": f"{label}_INVALID", "message": "授权范围格式不正确"}) from exc
    if not isinstance(parsed, list) or len(parsed) > max_length or any(not isinstance(item, str) for item in parsed):
        raise HTTPException(422, {"code": f"{label}_INVALID", "message": "授权范围格式不正确"})
    cleaned = list(dict.fromkeys(item.strip() for item in parsed if item.strip()))
    return cleaned

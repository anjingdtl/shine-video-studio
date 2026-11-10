from __future__ import annotations

import hashlib
import json
from itertools import islice
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.asset_center import service
from shine.asset_center.contracts import (
    MAX_QWEN_EDIT_IMAGES,
    QWEN_EDIT_COMMIT,
    QWEN_EDIT_PATH,
    QWEN_EDIT_REPOSITORY,
    QWEN_EDIT_TEMPLATE_SHA256,
    build_qwen_edit_graph,
    map_qwen_reference_slots,
    qwen_edit_adapter_hash,
    validate_aligned_mask,
    validate_image_request,
    validate_rgba_output,
)
from shine.asset_center.models import (
    LibraryAsset,
    LibraryMedia,
    LibraryProjectLink,
    LibraryRenderTask,
    LibraryRights,
    LibraryTaskAttempt,
    LibraryVersion,
    LibraryVoiceProfile,
)
from shine.comfy.client import ComfyUIClient
from shine.comfy.workflow import validate_against_object_info, validate_graph
from shine.comfyops.assets import require_rights as require_project_rights
from shine.comfyops.models import RightsRecord
from shine.config import Settings
from shine.materialprep import runtime as image_runtime
from shine.media.artifacts import safe_asset_path
from shine.models import Asset, Project, new_id, utcnow
from shine.security import probe_upload, require_loopback_url, safe_filename, verify_upload
from shine.services import add_audit

router = APIRouter(prefix="/api/v1", tags=["shine-assets-center"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


def _validated_library_task(
    db, mode: str, fingerprint: str | None, *, category_family: str | None = None, require_alpha: bool = False
):
    if not fingerprint:
        return None
    from shine.asset_center.contracts import qwen_edit_adapter_hash

    modes = {mode}
    if mode == "RGBA":
        modes.add("T2I")
    rows = db.scalars(
        select(LibraryRenderTask)
        .where(
            LibraryRenderTask.kind == "image",
            LibraryRenderTask.mode.in_(modes),
            LibraryRenderTask.status == "CANDIDATE_READY",
            LibraryRenderTask.external_outcome == "SUCCEEDED",
            LibraryRenderTask.runtime_fingerprint == fingerprint,
        )
        .order_by(LibraryRenderTask.updated_at.desc(), LibraryRenderTask.id)
    ).all()
    for task in rows:
        payload = task.payload or {}
        if not payload.get("validation_only"):
            continue
        if category_family:
            category = str(payload.get("category") or "")
            family = "character" if category in {"character", "costume"} else category
            if family != category_family:
                continue
        if task.mode != "T2I" and payload.get("validation_adapter_hash") != qwen_edit_adapter_hash(mode):
            continue
        if require_alpha and not payload.get("transparent"):
            continue
        if mode == "INPAINT" and not (payload.get("mask") or {}).get("sha256"):
            continue
        if mode == "WARDROBE":
            roles = {str(row.get("role")) for row in payload.get("references") or []}
            if not {"subject", "garment"}.issubset(roles):
                continue
        if mode == "MULTIREF" and len(payload.get("references") or []) < 2:
            continue
        version = db.get(LibraryVersion, task.result_version_id) if task.result_version_id else None
        if version is None or version.is_mock or version.status not in {"READY_FOR_REVIEW", "PROMOTED"}:
            continue
        qc = version.qc or {}
        if qc.get("technical") != "pass" or qc.get("sha256") != version.content_sha256:
            continue
        if mode == "INPAINT" and (qc.get("mask_composite") or {}).get("applied_sha256") != (
            payload.get("mask") or {}
        ).get("sha256"):
            continue
        if require_alpha and not (qc.get("alpha") or {}).get("transparent_pixels", 0):
            continue
        attempt = db.scalar(
            select(LibraryTaskAttempt)
            .where(LibraryTaskAttempt.task_id == task.id, LibraryTaskAttempt.external_outcome == "SUCCEEDED")
            .order_by(LibraryTaskAttempt.attempt_no.desc())
            .limit(1)
        )
        if (
            attempt is not None
            and attempt.output_sha256 == version.content_sha256
            and (attempt.payload or {}).get("renderer_owned") is True
            and (mode == "T2I" or (attempt.payload or {}).get("adapter_hash") == qwen_edit_adapter_hash(mode))
            and (attempt.payload or {}).get("graph_sha256") == task.workflow_revision_hash
        ):
            return task
    return None


def _validated_2k_task(db, fingerprint: str | None):
    if not fingerprint:
        return None
    for row in db.scalars(
        select(LibraryRenderTask)
        .where(
            LibraryRenderTask.kind == "image",
            LibraryRenderTask.mode == "T2I",
            LibraryRenderTask.status == "CANDIDATE_READY",
            LibraryRenderTask.external_outcome == "SUCCEEDED",
            LibraryRenderTask.runtime_fingerprint == fingerprint,
        )
        .order_by(LibraryRenderTask.updated_at.desc())
    ).all():
        payload = row.payload or {}
        size = payload.get("output") or {}
        if not payload.get("validation_only") or max(int(size.get("width", 0)), int(size.get("height", 0))) < 2048:
            continue
        version = db.get(LibraryVersion, row.result_version_id) if row.result_version_id else None
        if (
            version
            and not version.is_mock
            and version.width == size.get("width")
            and version.height == size.get("height")
            and (version.qc or {}).get("technical") == "pass"
            and (version.qc or {}).get("sha256") == version.content_sha256
        ):
            attempt = db.scalar(
                select(LibraryTaskAttempt)
                .where(LibraryTaskAttempt.task_id == row.id, LibraryTaskAttempt.external_outcome == "SUCCEEDED")
                .order_by(LibraryTaskAttempt.attempt_no.desc())
                .limit(1)
            )
            if (
                attempt
                and attempt.output_sha256 == version.content_sha256
                and (attempt.payload or {}).get("renderer_owned") is True
            ):
                return row
    return None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAsset(Strict):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["image", "audio"]
    category: Literal["character", "scene", "prop", "costume", "voice", "dialogue", "sfx", "music", "other"]
    tags: list[str] = Field(default_factory=list, max_length=40)
    collection: str | None = Field(default=None, max_length=120)
    media_id: str | None = None
    source_mode: str = Field(default="UPLOAD", max_length=24)
    prompt: str = Field(default="", max_length=8000)


class PatchAsset(Strict):
    expected_state_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    tags: list[str] | None = Field(default=None, max_length=40)
    collection: str | None = Field(default=None, max_length=120)
    favorite: bool | None = None


class NewVersion(Strict):
    media_id: str
    parent_version_id: str | None = None
    source_mode: str = Field(default="UPLOAD", max_length=24)
    prompt: str = Field(default="", max_length=8000)
    references: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class RightsApproval(Strict):
    expected_snapshot: str
    evidence: str = Field(min_length=8, max_length=2000)
    allowed_project_ids: list[str] = Field(default_factory=list, max_length=200)
    permitted_uses: list[Literal["research", "personal", "non_commercial", "commercial"]] = Field(default_factory=list)
    commercial_allowed: bool = False


class RightsPatch(Strict):
    expected_snapshot: str
    evidence: str = Field(min_length=8, max_length=2000)
    allowed_project_ids: list[str] = Field(max_length=200)
    permitted_uses: list[Literal["research", "personal", "non_commercial", "commercial"]] = Field(max_length=4)
    commercial_allowed: bool = False
    expires_at: str | None = None
    license_kind: str | None = Field(default=None, max_length=80)


class PromoteVersion(Strict):
    grant_id: str
    snapshot: str
    evidence: str = Field(default="", max_length=2000)


class Assignment(Strict):
    project_id: str
    library_version_id: str
    role: Literal[
        "character", "costume", "scene", "prop", "voice", "dialogue", "sfx", "music", "style_reference", "generic"
    ]
    entity_id: str | None = Field(default=None, max_length=80)
    usage_scope: Literal["local", "research", "personal", "non_commercial", "commercial"] = "local"
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirmed: bool = True
    requirement_context: dict[str, str] | None = None


class SaveToLibrary(Strict):
    title: str = Field(min_length=1, max_length=200)
    category: Literal["character", "scene", "prop", "costume", "voice", "dialogue", "sfx", "music", "other"]
    tags: list[str] = Field(default_factory=list, max_length=40)
    confirmed_rights: bool = True


class ImageReference(Strict):
    media_id: str | None = None
    version_id: str | None = None
    order: int = Field(ge=0, le=9)
    role: str = Field(min_length=1, max_length=32)


class ImageJobRequest(Strict):
    idempotency_key: str = Field(min_length=8, max_length=80)
    title: str = Field(default="", max_length=200)
    mode: Literal["T2I", "I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"]
    prompt: str = Field(default="", max_length=8000)
    references: list[ImageReference] = Field(default_factory=list, max_length=10)
    mask_media_id: str | None = None
    output: dict[str, Any] = Field(default_factory=lambda: {"width": 1024, "height": 1024})
    seed: int | None = None
    candidate_count: int = Field(default=1, ge=1, le=4)
    category: str = "other"
    requirement_context: dict[str, Any] | None = None
    transparent: bool = False
    target_asset_id: str | None = None
    parent_version_id: str | None = None
    expected_asset_state_version: int | None = Field(default=None, ge=1)
    validation_only: bool = False


class VoiceJobRequest(Strict):
    idempotency_key: str = Field(min_length=8, max_length=80)
    mode: Literal["DESIGN", "AUTHORIZED_CLONE", "TTS"]
    title: str = Field(min_length=1, max_length=200)
    character: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    reference_text: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=1000)
    reference_media_id: str | None = None
    rights_id: str | None = None
    voice_profile_id: str | None = None
    emotion: str = Field(default="neutral", max_length=100)
    speed: float = Field(default=1, ge=0.5, le=2, allow_inf_nan=False)
    pause: float = Field(default=0, ge=0, le=3, allow_inf_nan=False)


class VoiceFreeze(Strict):
    grant_id: str
    snapshot: str
    listened: bool
    evidence: str = Field(min_length=4, max_length=1000)


class ReconcileRequest(Strict):
    evidence: str = Field(default="", max_length=1000)


class NewImageAttempt(Strict):
    confirmed: bool
    idempotency_key: str = Field(min_length=8, max_length=80)


def _capabilities(db, settings) -> dict[str, Any]:
    config = image_runtime.load_config(settings)
    object_info = None
    object_info_error = None
    if config.base_url and config.instance_id:
        try:
            base_url = require_loopback_url(
                config.base_url, allow_remote=bool(getattr(settings, "allow_remote_comfyui", False))
            )
            object_info = ComfyUIClient(base_url, max_retries=0).object_info()
        except Exception as exc:  # noqa: BLE001 - capability reads remain available while local Comfy is offline
            object_info_error = type(exc).__name__
    try:
        qwen = image_runtime.readiness(db, settings, object_info=object_info)
    except Exception as exc:  # noqa: BLE001 - report a blocker without making GET unavailable
        qwen = {"layer": "NOT_CONFIGURED", "families": {}, "blockers": [{"code": type(exc).__name__}]}
    fingerprint = qwen.get("fingerprint")
    families = qwen.get("families") or {}
    for family in ("character", "scene", "prop"):
        task = _validated_library_task(db, "T2I", fingerprint, category_family=family)
        if task:
            families.setdefault(family, {}).update(
                {"can_generate": True, "layer": "RENDER_VERIFIED", "evidence_task_id": task.id}
            )
    ready_families = [name for name, row in families.items() if row.get("can_generate")]
    ready = bool(ready_families)
    edit_report = None
    edit_can_validate = False
    if object_info:
        try:
            probe_graph = build_qwen_edit_graph(
                prompt="validation probe",
                uploaded_names=["shine-validation.png"],
                width=1024,
                height=1024,
                seed=0,
            )
            validate_graph(probe_graph, required_nodes=[node["class_type"] for node in probe_graph.values()])
            edit_report = validate_against_object_info(probe_graph, {}, object_info)
            edit_report["complete"] = not edit_report["missing_nodes"] and not edit_report["missing_inputs"]
            edit_can_validate = edit_report["complete"] and qwen.get("layer") in {
                image_runtime.LAYER_GRAPH_VERIFIED,
                image_runtime.LAYER_RENDER_VERIFIED,
            }
        except Exception as exc:  # noqa: BLE001 - an unknown graph is a blocker, never an implicit fallback
            edit_report = {"complete": False, "error_code": type(exc).__name__}
    runtime_can_validate = qwen.get("layer") in {
        image_runtime.LAYER_GRAPH_VERIFIED,
        image_runtime.LAYER_RENDER_VERIFIED,
    }
    edit_reason = (
        "官方编辑图和本机模型合同已匹配；运行一次本机验证样例后才能用于正式候选。"
        if edit_can_validate
        else "需要本机 ComfyUI、模型文件及官方编辑节点合同通过验证。"
    )
    if object_info_error:
        edit_reason = f"本机工作流检查暂不可用：{object_info_error}。"
    edit = {
        "state": "PENDING_LOCAL_VALIDATION",
        "workflow": f"{QWEN_EDIT_REPOSITORY}/{QWEN_EDIT_PATH}",
        "source_commit": QWEN_EDIT_COMMIT,
        "source_sha256": QWEN_EDIT_TEMPLATE_SHA256,
        "max_total_images": MAX_QWEN_EDIT_IMAGES,
        "reason": edit_reason,
        "can_validate": edit_can_validate,
        "graph_report": edit_report,
    }
    from shine.asset_center.voice_execution import can_queue_voice

    voices = {
        "VOICE_DESIGN": {
            "state": "PENDING_LOCAL_VALIDATION",
            "can_generate": False,
            "can_queue": can_queue_voice(db, settings, "DESIGN"),
            "reason": "本机声音模型服务尚未完成此工作流的真人工验收。",
        },
        "VOICE_CLONE": {
            "state": "PENDING_LOCAL_VALIDATION",
            "can_generate": False,
            "can_queue": can_queue_voice(db, settings, "AUTHORIZED_CLONE"),
            "reason": "声音克隆使用本机声音模型和 WAV 样本，生成后可试听候选。",
        },
        "TTS_MANDARIN": {
            "state": "PENDING_LOCAL_VALIDATION",
            "can_generate": False,
            "can_queue": can_queue_voice(db, settings, "TTS"),
            "reason": "普通话合成、ASR、时间对齐和试听质量需本机实测。",
        },
        "SFX_MUSIC_IMPORT": {
            "state": "READY",
            "can_generate": False,
            "can_queue": False,
            "reason": "支持本地导入、播放、分类和检索，不提供虚构生成。",
        },
    }
    return {
        "workspaces": ["director", "assets", "settings"],
        "image_capabilities": {
            "QWEN_IMAGE_T2I": {
                "state": "READY" if ready else "PENDING_LOCAL_VALIDATION",
                "can_generate": ready,
                "can_validate": runtime_can_validate,
                "reason": "Qwen-Image-2.1 T2I 需要本机模型、工作流和真实小样通过。",
                "verified_families": ready_families,
            },
            "QWEN_IMAGE_I2I": {
                **edit,
                "state": "READY" if _validated_library_task(db, "I2I", fingerprint) else edit["state"],
                "can_generate": bool(_validated_library_task(db, "I2I", fingerprint)),
            },
            "QWEN_IMAGE_INPAINT": {
                **edit,
                "state": "READY" if _validated_library_task(db, "INPAINT", fingerprint) else edit["state"],
                "can_generate": bool(_validated_library_task(db, "INPAINT", fingerprint)),
                "reason": "局部修订会把真实遮罩作为编辑图输入，并在结果落库前按像素精确合成。"
                if edit_can_validate
                else edit_reason,
            },
            "QWEN_IMAGE_MULTIREF": {
                **edit,
                "state": "READY" if _validated_library_task(db, "MULTIREF", fingerprint) else edit["state"],
                "can_generate": bool(_validated_library_task(db, "MULTIREF", fingerprint)),
            },
            "QWEN_IMAGE_WARDROBE": {
                **edit,
                "state": "READY" if _validated_library_task(db, "WARDROBE", fingerprint) else edit["state"],
                "can_generate": bool(_validated_library_task(db, "WARDROBE", fingerprint)),
                "inherits": "QWEN_IMAGE_MULTIREF",
            },
            "QWEN_IMAGE_RGBA": {
                "state": "READY"
                if _validated_library_task(db, "RGBA", fingerprint, require_alpha=True)
                else "PENDING_LOCAL_VALIDATION",
                "can_generate": bool(_validated_library_task(db, "RGBA", fingerprint, require_alpha=True)),
                "can_validate": edit_can_validate or qwen.get("layer") == image_runtime.LAYER_GRAPH_VERIFIED,
                "reason": "必须由真实样例证明输出含非零透明像素；只有 PNG 扩展名不构成证据。",
            },
            "QWEN_IMAGE_2K": {
                "state": "READY" if _validated_2k_task(db, fingerprint) else "PENDING_LOCAL_VALIDATION",
                "can_generate": bool(_validated_2k_task(db, fingerprint)),
                "can_validate": qwen.get("layer") == image_runtime.LAYER_GRAPH_VERIFIED,
                "reason": "只有本机真实输出达到请求尺寸后才开放 2K。",
            },
        },
        "voice_capabilities": voices,
        "t2i_runtime": {
            key: value
            for key, value in qwen.items()
            if key in {"layer", "fingerprint", "graph_sha256", "output_node_id", "blockers", "license", "upstream"}
        },
    }


def _media_public(row: LibraryMedia, rights: LibraryRights | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "purpose": row.purpose,
        "filename": row.filename,
        "content_type": row.content_type,
        "sha256": row.sha256,
        "byte_size": row.byte_size,
        "width": row.width,
        "height": row.height,
        "duration_s": row.duration_s,
        "rights_id": row.rights_id,
        "rights_snapshot_hash": service.canonical_hash(service.rights_snapshot(rights)) if rights else None,
        "media_url": f"/api/v1/asset-center/media/{row.id}/content",
        "created_at": row.created_at,
    }


@router.get("/asset-center/capabilities")
def capabilities(settings: SettingsDep, db: DBSession):
    return _capabilities(db, settings)


@router.post("/asset-center/uploads", status_code=201)
async def upload_media(
    file: Annotated[UploadFile, File()],
    asset_kind: Annotated[Literal["image", "audio"], Form()],
    purpose: Annotated[Literal["source", "reference", "mask", "audio_import", "voice_sample"], Form()],
    settings: SettingsDep,
    db: DBSession,
    source_type: Annotated[str, Form()] = "local_upload",
    license_kind: Annotated[str, Form()] = "UNSPECIFIED",
    rights_statement: Annotated[str, Form()] = "",
    subject: Annotated[str, Form()] = "",
    allowed_project_ids: Annotated[str, Form()] = "[]",
    permitted_uses: Annotated[str, Form()] = '["research"]',
    commercial_allowed: Annotated[bool, Form()] = False,
    expires_at: Annotated[str | None, Form()] = None,
):
    if purpose in {"mask", "reference", "source"} and asset_kind != "image":
        raise HTTPException(422, {"code": "UPLOAD_KIND_PURPOSE_MISMATCH", "message": "此入口需要图片文件"})
    if purpose in {"audio_import", "voice_sample"} and asset_kind != "audio":
        raise HTTPException(422, {"code": "UPLOAD_KIND_PURPOSE_MISMATCH", "message": "此入口需要音频文件"})
    filename = safe_filename(file.filename)
    content = await file.read(settings.max_upload_bytes + 1)
    if purpose == "mask" and Path(filename).suffix.lower() != ".png":
        raise HTTPException(415, {"code": "MASK_PNG_REQUIRED", "message": "遮罩画布只接受 PNG 文件"})
    projects = service.safe_json_list(allowed_project_ids, label="PROJECT_SCOPE")
    uses = service.safe_json_list(permitted_uses, label="PERMITTED_USES", max_length=4)
    expires = service.parse_expiry(expires_at)
    row = service.create_media(
        db,
        settings,
        content=content,
        filename=filename,
        kind=asset_kind,
        purpose=purpose,
        actor="local-user",
        source_type=source_type.strip()[:32],
        license_kind=license_kind.strip()[:80],
        scope="voice_clone" if purpose == "voice_sample" else "asset",
        statement=rights_statement.strip()[:2000],
        subject=subject.strip()[:160],
        allowed_project_ids=projects,
        permitted_uses=uses,
        commercial_allowed=commercial_allowed,
        expires_at=expires,
    )
    db.commit()
    return {**_media_public(row, db.get(LibraryRights, row.rights_id)), "rights_status": "DECLARED"}


@router.get("/asset-center/media/{media_id}/content")
def media_content(media_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(LibraryMedia, media_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_MEDIA_NOT_FOUND", "message": "素材不存在"})
    path = safe_asset_path(settings.data_dir, row.storage_path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256:
        raise HTTPException(409, {"code": "LIBRARY_MEDIA_HASH_CHANGED", "message": "素材校验失败"})
    return FileResponse(path, media_type=row.content_type, headers={"Cache-Control": "no-store"})


@router.get("/asset-center/assets")
def list_library_assets(
    db: DBSession,
    q: str = Query(default="", max_length=200),
    kind: str | None = None,
    category: str | None = None,
    favorite: bool | None = None,
    status: str = "ACTIVE",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(LibraryAsset).where(LibraryAsset.status == status)
    if kind:
        stmt = stmt.where(LibraryAsset.kind == kind)
    if category:
        stmt = stmt.where(LibraryAsset.category == category)
    if favorite is not None:
        stmt = stmt.where(LibraryAsset.favorite.is_(favorite))
    normalized = q.casefold().strip()
    stmt = stmt.order_by(LibraryAsset.updated_at.desc(), LibraryAsset.id)
    if normalized:
        matches = (
            row for row in db.scalars(stmt.execution_options(yield_per=100))
            if _matches_library_query(row, normalized)
        )
        rows = list(islice(matches, offset, offset + limit))
    else:
        rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    output = []
    for row in rows:
        result = service.asset_data(db, row)
        result["usage_count"] = (
            db.query(LibraryProjectLink).filter(LibraryProjectLink.library_asset_id == row.id).count()
        )
        output.append(result)
    return {"items": output, "limit": limit, "offset": offset, "query": q}


def _matches_library_query(asset: LibraryAsset, term: str) -> bool:
    return not term or term in asset.title.casefold() or any(term in tag.casefold() for tag in (asset.tags or []))


@router.post("/asset-center/assets", status_code=201)
def create_library_asset(body: CreateAsset, settings: SettingsDep, db: DBSession):
    title = body.title.strip()
    if not title:
        raise HTTPException(422, {"code": "ASSET_TITLE_REQUIRED", "message": "请填写素材名称"})
    if body.media_id is None:
        row = LibraryAsset(
            title=title,
            kind=body.kind,
            category=body.category,
            tags=body.tags,
            collection=body.collection,
            created_by="local-user",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return service.asset_data(db, row, include_versions=True)
    media = db.get(LibraryMedia, body.media_id)
    if media is None:
        raise HTTPException(404, {"code": "LIBRARY_MEDIA_NOT_FOUND", "message": "导入文件不存在"})
    row = LibraryAsset(
        title=title,
        kind=body.kind,
        category=body.category,
        tags=body.tags,
        collection=body.collection,
        created_by="local-user",
    )
    db.add(row)
    db.flush()
    service.create_version_from_media(
        db, settings, asset=row, media=media, actor="local-user", source_mode=body.source_mode, prompt=body.prompt
    )
    add_audit(
        db, "local-user", "LIBRARY_ASSET_CREATED", "library_asset", row.id, {"kind": row.kind, "category": row.category}
    )
    db.commit()
    db.refresh(row)
    return service.asset_data(db, row, include_versions=True)


@router.get("/asset-center/assets/{asset_id}")
def get_library_asset(asset_id: str, db: DBSession):
    row = db.get(LibraryAsset, asset_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "素材不存在"})
    result = service.asset_data(db, row, include_versions=True)
    result["usage"] = [
        {
            "project_id": link.project_id,
            "project_asset_id": link.project_asset_id,
            "version_id": link.library_version_id,
            "role": link.role,
            "entity_id": link.entity_id,
            "usage_scope": link.usage_scope,
            "created_at": link.created_at,
        }
        for link in db.scalars(
            select(LibraryProjectLink)
            .where(LibraryProjectLink.library_asset_id == row.id)
            .order_by(LibraryProjectLink.created_at.desc())
        ).all()
    ]
    return result


@router.patch("/asset-center/assets/{asset_id}")
def patch_library_asset(asset_id: str, body: PatchAsset, db: DBSession):
    row = db.get(LibraryAsset, asset_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "素材不存在"})
    if row.state_version != body.expected_state_version:
        raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材已经在别处更新，请刷新后再改"})
    updates = body.model_dump(exclude_unset=True, exclude={"expected_state_version"})
    if "category" in updates and updates["category"] not in service.ASSET_CATEGORIES:
        raise HTTPException(422, {"code": "LIBRARY_CATEGORY_INVALID", "message": "素材分类无效"})
    if "tags" in updates and updates["tags"] is not None:
        updates["tags"] = list(dict.fromkeys(tag.strip() for tag in updates["tags"] if tag.strip()))
    result = db.execute(
        update(LibraryAsset)
        .where(
            LibraryAsset.id == row.id,
            LibraryAsset.state_version == body.expected_state_version,
        )
        .values(**updates, state_version=body.expected_state_version + 1, updated_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材已经在别处更新，请刷新后再改"})
    add_audit(
        db,
        "local-user",
        "LIBRARY_ASSET_METADATA_UPDATED",
        "library_asset",
        row.id,
        {"fields": sorted(updates), "state_version": body.expected_state_version + 1},
    )
    db.commit()
    db.refresh(row)
    return service.asset_data(db, row, include_versions=True)


@router.post("/asset-center/assets/{asset_id}/archive")
def archive_library_asset(asset_id: str, db: DBSession):
    row = db.get(LibraryAsset, asset_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "素材不存在"})
    row.status = "ARCHIVED"
    row.state_version += 1
    add_audit(db, "local-user", "LIBRARY_ASSET_ARCHIVED", "library_asset", row.id)
    db.commit()
    return service.asset_data(db, row)


@router.post("/asset-center/assets/{asset_id}/versions", status_code=201)
def new_library_version(asset_id: str, body: NewVersion, settings: SettingsDep, db: DBSession):
    asset = db.get(LibraryAsset, asset_id)
    media = db.get(LibraryMedia, body.media_id)
    if asset is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "素材不存在"})
    if media is None:
        raise HTTPException(404, {"code": "LIBRARY_MEDIA_NOT_FOUND", "message": "导入文件不存在"})
    refs = [item.model_dump() for item in body.references]
    version = service.create_version_from_media(
        db,
        settings,
        asset=asset,
        media=media,
        actor="local-user",
        source_mode=body.source_mode,
        prompt=body.prompt,
        parent_version_id=body.parent_version_id,
        references_manifest=refs,
    )
    db.commit()
    return service.version_data(db, version)


@router.get("/asset-center/assets/{asset_id}/versions")
def list_library_versions(asset_id: str, db: DBSession):
    if db.get(LibraryAsset, asset_id) is None:
        raise HTTPException(404, {"code": "LIBRARY_ASSET_NOT_FOUND", "message": "素材不存在"})
    rows = db.scalars(
        select(LibraryVersion)
        .where(LibraryVersion.library_asset_id == asset_id)
        .order_by(LibraryVersion.ordinal.desc())
    ).all()
    return [service.version_data(db, row) for row in rows]


@router.get("/asset-center/versions/{version_id}/media")
def version_media(version_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(LibraryVersion, version_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    service.version_bytes(settings, row)
    return FileResponse(
        safe_asset_path(settings.data_dir, row.storage_path),
        media_type=row.content_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/asset-center/versions/{version_id}")
def get_library_version(version_id: str, db: DBSession):
    row = db.get(LibraryVersion, version_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    return service.version_data(db, row)


@router.post("/asset-center/rights/{rights_id}/approve")
def approve_library_rights(rights_id: str, body: RightsApproval, actor: Operator, db: DBSession):
    rights = db.get(LibraryRights, rights_id)
    if rights is None:
        raise HTTPException(404, {"code": "LIBRARY_RIGHTS_NOT_FOUND", "message": "授权记录不存在"})
    if rights.status == "REVOKED":
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_REVOKED", "message": "已撤销的授权不能恢复，请重新登记"})
    current_snapshot = service.canonical_hash(service.rights_snapshot(rights))
    if body.expected_snapshot != current_snapshot:
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_CHANGED", "message": "授权记录已变化，请刷新后重新确认"})
    if body.commercial_allowed and "commercial" not in body.permitted_uses:
        raise HTTPException(422, {"code": "COMMERCIAL_SCOPE_MISSING", "message": "商业授权需要同时勾选商业用途"})
    if rights.license_kind.upper().startswith("QWEN") and body.commercial_allowed:
        raise HTTPException(409, {"code": "MODEL_LICENSE_NONCOMMERCIAL", "message": "Qwen 研究许可不包含商业用途"})
    service.enforce_rights_ceiling(
        db,
        rights,
        allowed_project_ids=body.allowed_project_ids,
        permitted_uses=body.permitted_uses,
        commercial_allowed=body.commercial_allowed,
        expires_at=rights.expires_at,
    )
    rights.allowed_project_ids = body.allowed_project_ids
    rights.permitted_uses = body.permitted_uses
    rights.commercial_allowed = body.commercial_allowed
    rights.status = "APPROVED"
    rights.approved_by = actor
    add_audit(
        db,
        actor,
        "LIBRARY_RIGHTS_APPROVED",
        "library_rights",
        rights.id,
        {
            "evidence": body.evidence,
            "scope": rights.scope,
            "license_kind": rights.license_kind,
            "allowed_project_count": len(body.allowed_project_ids),
            "commercial_allowed": body.commercial_allowed,
        },
    )
    db.commit()
    return {**service.rights_snapshot(rights), "snapshot_hash": service.canonical_hash(service.rights_snapshot(rights))}


@router.patch("/asset-center/rights/{rights_id}")
def update_library_rights(rights_id: str, body: RightsPatch, actor: Operator, db: DBSession):
    rights = db.get(LibraryRights, rights_id)
    if rights is None:
        raise HTTPException(404, {"code": "LIBRARY_RIGHTS_NOT_FOUND", "message": "授权记录不存在"})
    if rights.status == "REVOKED" or rights.revoked_at is not None:
        raise HTTPException(
            409, {"code": "LIBRARY_RIGHTS_REVOKED", "message": "已撤销的授权不能恢复，请重新导入并登记"}
        )
    if service.canonical_hash(service.rights_snapshot(rights)) != body.expected_snapshot:
        raise HTTPException(409, {"code": "LIBRARY_RIGHTS_CHANGED", "message": "授权已变化，请刷新后重试"})
    if body.commercial_allowed and ("commercial" not in body.permitted_uses or not body.license_kind):
        raise HTTPException(
            422, {"code": "COMMERCIAL_LICENSE_EVIDENCE_REQUIRED", "message": "商业用途需要明确许可类型与授权范围"}
        )
    if (body.license_kind or rights.license_kind).upper().startswith("QWEN") and body.commercial_allowed:
        raise HTTPException(409, {"code": "MODEL_LICENSE_NONCOMMERCIAL", "message": "Qwen 研究许可不包含商业用途"})
    expires_at = service.parse_expiry(body.expires_at)
    service.enforce_rights_ceiling(
        db,
        rights,
        allowed_project_ids=body.allowed_project_ids,
        permitted_uses=body.permitted_uses,
        commercial_allowed=body.commercial_allowed,
        expires_at=expires_at,
    )
    rights.allowed_project_ids = body.allowed_project_ids
    rights.permitted_uses = body.permitted_uses
    rights.commercial_allowed = body.commercial_allowed
    rights.expires_at = expires_at
    if body.license_kind:
        rights.license_kind = body.license_kind
    rights.status = "APPROVED"
    rights.approved_by = actor
    add_audit(
        db,
        actor,
        "LIBRARY_RIGHTS_SCOPE_UPDATED",
        "library_rights",
        rights.id,
        {"evidence": body.evidence, "commercial_allowed": rights.commercial_allowed},
    )
    db.commit()
    return {**service.rights_snapshot(rights), "snapshot_hash": service.canonical_hash(service.rights_snapshot(rights))}


@router.post("/asset-center/rights/{rights_id}/revoke")
def revoke_library_rights(
    rights_id: str, evidence: Annotated[str, Query(min_length=4, max_length=1000)], actor: Operator, db: DBSession
):
    rights = db.get(LibraryRights, rights_id)
    if rights is None:
        raise HTTPException(404, {"code": "LIBRARY_RIGHTS_NOT_FOUND", "message": "授权记录不存在"})
    rights.status = "REVOKED"
    rights.revoked_at = utcnow()
    add_audit(db, actor, "LIBRARY_RIGHTS_REVOKED", "library_rights", rights.id, {"evidence": evidence})
    db.commit()
    return service.rights_snapshot(rights)


@router.post("/asset-center/versions/{version_id}/qc")
def qc_library_version(version_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(LibraryVersion, version_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    if row.status == "PROMOTED":
        raise HTTPException(409, {"code": "LIBRARY_VERSION_IMMUTABLE", "message": "正式版本不可覆盖审核结果"})
    data = service.version_bytes(settings, row)
    verify_upload(row.kind, row.filename, data, settings.max_upload_bytes)
    media = probe_upload(row.kind, data, row.content_type)
    from shine.quality.technical import _full_decode_check

    decoded = _full_decode_check(safe_asset_path(settings.data_dir, row.storage_path), settings.ffmpeg_path)
    alpha: Any = "NOT_REQUESTED"
    if row.content_type == "image/png" and (row.provenance or {}).get("transparent"):
        alpha = validate_rgba_output(data)
    prior_qc = row.qc or {}
    dialogue_qc = prior_qc.get("dialogue") or (row.provenance or {}).get("dialogue_qc")
    hard_dialogue_errors = (dialogue_qc or {}).get("hard_errors") or []
    qc = {
        "technical": decoded.status,
        "decode": decoded.model_dump(),
        "sha256": row.content_sha256,
        "dimensions": {"width": media.width, "height": media.height},
        "alpha": alpha,
        "semantic": "PENDING_HUMAN_REVIEW",
        "is_mock": row.is_mock,
        "dialogue": dialogue_qc,
    }
    row.qc = qc
    row.status = "READY_FOR_REVIEW" if decoded.status == "pass" and not hard_dialogue_errors else "QC_FAILED"
    add_audit(db, "local-user", "LIBRARY_VERSION_TECHNICAL_QC", "library_version", row.id, qc)
    db.commit()
    return service.version_data(db, row)


@router.post("/asset-center/versions/{version_id}/grant")
def grant_version_promotion(version_id: str, actor: Operator, db: DBSession):
    row = db.get(LibraryVersion, version_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    if row.status != "READY_FOR_REVIEW" or row.is_mock:
        raise HTTPException(
            409, {"code": "LIBRARY_VERSION_NOT_REVIEWABLE", "message": "技术审核未通过或结果来自模拟执行"}
        )
    service.require_version_rights(db, row)
    return service.issue_grant(db, actor, "library_promote", row)


@router.post("/asset-center/versions/{version_id}/promote")
def promote_version(version_id: str, body: PromoteVersion, actor: Operator, settings: SettingsDep, db: DBSession):
    row = db.get(LibraryVersion, version_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    service.consume_grant(db, actor, "library_promote", row, body.grant_id, body.snapshot)
    if row.status != "READY_FOR_REVIEW" or row.is_mock:
        raise HTTPException(409, {"code": "LIBRARY_VERSION_NOT_REVIEWABLE", "message": "候选版本还不能晋升"})
    service.require_version_rights(db, row)
    service.version_bytes(settings, row)
    row.status = "PROMOTED"
    row.approved_by = actor
    row.approved_at = utcnow()
    asset = db.get(LibraryAsset, row.library_asset_id)
    if asset is None:
        raise HTTPException(409, {"code": "LIBRARY_ASSET_MISSING", "message": "素材记录不存在"})
    expected_asset_state_version = asset.state_version
    result = db.execute(
        update(LibraryAsset)
        .where(
            LibraryAsset.id == asset.id,
            LibraryAsset.state_version == expected_asset_state_version,
        )
        .values(
            current_version_id=row.id,
            state_version=expected_asset_state_version + 1,
            updated_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材状态已更新，请刷新"})
    if asset.category == "voice":
        profile = db.scalar(select(LibraryVoiceProfile).where(LibraryVoiceProfile.version_id == row.id))
        if profile is None:
            profile = LibraryVoiceProfile(
                library_asset_id=asset.id,
                version_id=row.id,
                conditioning_media_id=(row.provenance or {}).get("reference_media_id")
                or (
                    (row.provenance or {}).get("source_media_id")
                    if (row.provenance or {}).get("media_purpose") == "voice_sample"
                    else None
                ),
                voice_hash=row.content_sha256,
                source=(row.provenance or {}).get("voice_source")
                or (
                    "AUTHORIZED_CLONE"
                    if (row.provenance or {}).get("media_purpose") == "voice_sample"
                    else "ORIGINAL_DESIGN"
                ),
                description=row.prompt,
                rights_id=row.rights_id,
                status="PREVIEW",
                created_by=actor,
            )
            db.add(profile)
    add_audit(
        db,
        actor,
        "LIBRARY_VERSION_PROMOTED",
        "library_version",
        row.id,
        {"asset_id": asset.id, "content_sha256": row.content_sha256, "evidence": body.evidence},
    )
    db.commit()
    return service.version_data(db, row)


@router.post("/asset-center/assets/{asset_id}/select-version")
def select_library_version(
    asset_id: str,
    version_id: Annotated[str, Query()],
    expected_state_version: Annotated[int, Query(ge=1)],
    db: DBSession,
):
    asset = db.get(LibraryAsset, asset_id)
    row = db.get(LibraryVersion, version_id)
    if asset is None or row is None or row.library_asset_id != asset_id:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "版本不存在"})
    if asset.state_version != expected_state_version:
        raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材状态已更新，请刷新"})
    if row.status != "PROMOTED":
        raise HTTPException(409, {"code": "LIBRARY_VERSION_NOT_PROMOTED", "message": "只能选择已晋升版本"})
    result = db.execute(
        update(LibraryAsset)
        .where(
            LibraryAsset.id == asset.id,
            LibraryAsset.state_version == expected_state_version,
        )
        .values(
            current_version_id=row.id,
            state_version=expected_state_version + 1,
            updated_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材状态已更新，请刷新"})
    add_audit(
        db,
        "local-user",
        "LIBRARY_CURRENT_VERSION_CHANGED",
        "library_asset",
        asset.id,
        {"version_id": row.id, "sha256": row.content_sha256},
    )
    db.commit()
    db.refresh(asset)
    return service.asset_data(db, asset, include_versions=True)


@router.post("/asset-center/image-jobs", status_code=202)
def create_image_jobs(body: ImageJobRequest, settings: SettingsDep, db: DBSession):
    payload = body.model_dump(mode="json")
    if body.category not in service.ASSET_CATEGORIES:
        raise HTTPException(422, {"code": "LIBRARY_CATEGORY_INVALID", "message": "素材分类无效"})
    if body.requirement_context:
        from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan

        project_id = body.requirement_context.get("project_id")
        requirement_id = body.requirement_context.get("requirement_id")
        expected_hash = body.requirement_context.get("requirement_hash")
        requirement = db.get(AssetRequirement, requirement_id) if requirement_id else None
        plan = db.get(PreProductionPlan, requirement.plan_id) if requirement else None
        if (
            requirement is None
            or requirement.project_id != project_id
            or requirement.sha256 != expected_hash
            or plan is None
            or plan.project_id != project_id
            or plan.purpose != PLAN_PURPOSE_ASSET_PREPARATION
        ):
            raise HTTPException(
                409, {"code": "MATERIAL_REQUIREMENT_STALE", "message": "素材需求已经变化，请刷新后重新生成"}
            )
        payload["requirement_context"] = {
            "project_id": project_id,
            "requirement_id": requirement_id,
            "requirement_hash": expected_hash,
            "plan_id": plan.id,
        }
    if body.target_asset_id:
        target_asset = db.get(LibraryAsset, body.target_asset_id)
        if target_asset is None or target_asset.kind != "image" or target_asset.status != "ACTIVE":
            raise HTTPException(404, {"code": "IMAGE_TARGET_ASSET_NOT_FOUND", "message": "找不到可继续创作的图片素材"})
        if body.category != target_asset.category:
            raise HTTPException(
                409, {"code": "IMAGE_TARGET_CATEGORY_MISMATCH", "message": "新版本会沿用原素材分类，请先统一分类"}
            )
        if body.expected_asset_state_version != target_asset.state_version:
            raise HTTPException(409, {"code": "LIBRARY_ASSET_CHANGED", "message": "素材版本已变化，请刷新后重新选择"})
        parent_id = body.parent_version_id or target_asset.current_version_id
        parent = db.get(LibraryVersion, parent_id) if parent_id else None
        if parent is None or parent.library_asset_id != target_asset.id:
            raise HTTPException(
                409, {"code": "IMAGE_PARENT_VERSION_INVALID", "message": "请选择此素材中已晋升的来源版本"}
            )
        if parent.status != "PROMOTED" or parent.is_mock:
            raise HTTPException(
                409, {"code": "IMAGE_PARENT_VERSION_NOT_PROMOTED", "message": "只能从已晋升的真实版本继续创作"}
            )
        parent_rights = service.require_version_rights(db, parent, usage_scope="local")
        service.version_bytes(settings, parent)
        payload["target_asset_id"] = target_asset.id
        payload["parent_version_id"] = parent.id
        payload["expected_asset_state_version"] = target_asset.state_version
        payload["target_asset_title"] = target_asset.title
        payload["parent_version_snapshot"] = {
            "version_id": parent.id,
            "sha256": parent.content_sha256,
            "rights_id": parent_rights.id,
            "rights_snapshot": service.rights_snapshot(parent_rights),
        }
    elif body.parent_version_id or body.expected_asset_state_version is not None:
        raise HTTPException(422, {"code": "IMAGE_TARGET_ASSET_REQUIRED", "message": "选择来源版本时需要同时选择素材"})
    refs = validate_image_request(payload)
    payload["output"] = {
        "width": (body.output or {}).get("width", 1024),
        "height": (body.output or {}).get("height", 1024),
    }
    reference_rows = []
    for ref in refs:
        if ref.get("media_id"):
            media = db.get(LibraryMedia, ref["media_id"])
            if media is None or media.kind != "image" or media.purpose == "mask":
                raise HTTPException(
                    404, {"code": "IMAGE_REFERENCE_NOT_FOUND", "message": "有一张参考图不存在或用途不正确"}
                )
            rights = service.require_rights(db.get(LibraryRights, media.rights_id), usage_scope="local")
            service.media_bytes(settings, media)
            reference_rows.append(
                {
                    **ref,
                    "sha256": media.sha256,
                    "rights_id": rights.id,
                    "rights_snapshot": service.rights_snapshot(rights),
                    "width": media.width,
                    "height": media.height,
                }
            )
        else:
            version = db.get(LibraryVersion, ref.get("version_id"))
            if version is None or version.kind != "image" or version.status != "PROMOTED" or version.is_mock:
                raise HTTPException(
                    404, {"code": "IMAGE_REFERENCE_NOT_FOUND", "message": "全局参考版本不存在或尚未晋升"}
                )
            rights = service.require_version_rights(db, version, usage_scope="local")
            service.version_bytes(settings, version)
            reference_rows.append(
                {
                    **ref,
                    "sha256": version.content_sha256,
                    "rights_id": rights.id,
                    "rights_snapshot": service.rights_snapshot(rights),
                    "width": version.width,
                    "height": version.height,
                }
            )
    mask_info = None
    if body.mask_media_id:
        mask = db.get(LibraryMedia, body.mask_media_id)
        if mask is None or mask.purpose != "mask" or mask.kind != "image" or not reference_rows:
            raise HTTPException(409, {"code": "MASK_MEDIA_NOT_FOUND", "message": "遮罩文件或原图不存在"})
        first_reference = reference_rows[0]
        source = (
            db.get(LibraryMedia, first_reference["media_id"])
            if first_reference.get("media_id")
            else db.get(LibraryVersion, first_reference.get("version_id"))
        )
        mask_rights = service.require_rights(db.get(LibraryRights, mask.rights_id), usage_scope="local")
        mask_info = validate_aligned_mask(
            (source.width or 0),
            (source.height or 0),
            service.media_bytes(settings, mask),
        )
        mask_info["media_id"] = mask.id
        mask_info["rights_snapshot"] = service.rights_snapshot(mask_rights)
    if body.mode == "INPAINT":
        source = (
            db.get(LibraryMedia, reference_rows[0]["media_id"])
            if reference_rows[0].get("media_id")
            else db.get(LibraryVersion, reference_rows[0].get("version_id"))
        )
        if source is None or source.content_type != "image/png":
            raise HTTPException(
                415,
                {
                    "code": "INPAINT_PNG_SOURCE_REQUIRED",
                    "message": "局部修订目前要求使用 PNG 底图，以保证遮罩像素逐点对齐",
                },
            )
        if payload["output"]["width"] != source.width or payload["output"]["height"] != source.height:
            raise HTTPException(
                409, {"code": "INPAINT_OUTPUT_SIZE_MISMATCH", "message": "局部修订输出尺寸必须与底图和遮罩完全一致"}
            )
    manual_cutout = body.mode == "RGBA" and mask_info is not None
    if manual_cutout:
        if len(reference_rows) != 1:
            raise HTTPException(
                422, {"code": "RGBA_CUTOUT_SINGLE_SOURCE_REQUIRED", "message": "手动画布去背景需要且只需要一张底图"}
            )
        source_ref = reference_rows[0]
        source = (
            db.get(LibraryMedia, source_ref["media_id"])
            if source_ref.get("media_id")
            else db.get(LibraryVersion, source_ref["version_id"])
        )
        if source is None or source.content_type != "image/png":
            raise HTTPException(415, {"code": "RGBA_CUTOUT_PNG_REQUIRED", "message": "手动画布去背景目前需要 PNG 底图"})
        if payload["output"]["width"] != source.width or payload["output"]["height"] != source.height:
            raise HTTPException(409, {"code": "RGBA_CUTOUT_SIZE_MISMATCH", "message": "输出尺寸必须与底图和遮罩一致"})
        if body.candidate_count != 1:
            raise HTTPException(422, {"code": "RGBA_CUTOUT_CANDIDATE_COUNT", "message": "一次去背景只建立一个候选版本"})
        mask_info["semantics"] = "white=keep_subject,black=remove_background"
        payload["alpha_operation"] = "MASK_CUTOUT_V1"
    payload["references"] = reference_rows
    payload["mask"] = mask_info
    payload["transparent"] = bool(body.transparent or body.mode == "RGBA")
    payload["validation_only"] = body.validation_only
    graph_references = list(reference_rows)
    if body.mode == "INPAINT" and mask_info is not None:
        graph_references.append(
            {
                "order": len(graph_references),
                "role": "mask_annotation",
                "sha256": mask_info["sha256"],
                "media_id": mask_info["media_id"],
                "width": mask_info["width"],
                "height": mask_info["height"],
            }
        )
    payload["graph_references"] = graph_references
    payload["reference_slot_map"] = map_qwen_reference_slots(graph_references) if graph_references else {}
    cap_key = {
        "T2I": "QWEN_IMAGE_T2I",
        "I2I": "QWEN_IMAGE_I2I",
        "INPAINT": "QWEN_IMAGE_INPAINT",
        "MULTIREF": "QWEN_IMAGE_MULTIREF",
        "WARDROBE": "QWEN_IMAGE_WARDROBE",
        "RGBA": "QWEN_IMAGE_RGBA",
    }[body.mode]
    if body.transparent:
        cap_key = "QWEN_IMAGE_RGBA"
    capability = _capabilities(db, settings)["image_capabilities"][cap_key]
    can_queue = capability.get("can_generate", False)
    if manual_cutout:
        capability = {
            "state": "READY",
            "can_generate": True,
            "reason": "按用户实际绘制的白色主体遮罩生成透明 PNG；此为确定性本机像素操作，不调用图像模型。",
            "operation": "MASK_CUTOUT_V1",
        }
        can_queue = True
    if body.validation_only:
        if body.candidate_count != 1:
            raise HTTPException(
                422, {"code": "VALIDATION_CANDIDATE_COUNT_INVALID", "message": "本机验证一次只排队 1 个样例"}
            )
        can_queue = bool(capability.get("can_validate"))
        if body.mode in {"I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"}:
            payload["validation_adapter_hash"] = qwen_edit_adapter_hash(body.mode)
    if body.mode == "T2I":
        if body.category not in {"character", "costume", "scene", "prop"}:
            can_queue = False
        elif not body.validation_only and not body.transparent:
            family = "scene" if body.category == "scene" else "prop" if body.category == "prop" else "character"
            can_queue = can_queue and family in capability.get("verified_families", [])
    if not manual_cutout and (payload["output"]["width"] > 1024 or payload["output"]["height"] > 1024):
        capability_2k = _capabilities(db, settings)["image_capabilities"]["QWEN_IMAGE_2K"]
        supports_2k = body.mode in {"T2I", "RGBA"} and not reference_rows
        can_queue = supports_2k and (
            bool(capability_2k.get("can_generate"))
            or (body.validation_only and bool(capability_2k.get("can_validate")))
        )
        capability = capability_2k
    elif not can_queue and body.mode == "T2I":
        capability = {**capability, "can_generate": False, "reason": "所选素材类型的 T2I 工作流或本机小样尚未验证。"}
    created = []
    for index in range(body.candidate_count):
        candidate_seed = (body.seed + index) if body.seed is not None else None
        if candidate_seed is not None and candidate_seed > 0xFFFFFFFFFFFFFFFF:
            raise HTTPException(422, {"code": "IMAGE_SEED_OUT_OF_RANGE", "message": "候选图片的随机种子超出范围"})
        item_payload = {**payload, "candidate_index": index, "seed": candidate_seed, "capability": capability}
        item_key = f"{body.idempotency_key}:{index + 1}"
        request_hash = service.canonical_hash(
            {key: value for key, value in item_payload.items() if key != "capability"}
        )
        prior = db.scalar(select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == item_key))
        if prior:
            if prior.request_hash != request_hash:
                raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "此生成编号已用于不同参数"})
            created.append(task_data(db, prior))
            continue
        task = LibraryRenderTask(
            idempotency_key=item_key,
            request_hash=request_hash,
            kind="image",
            mode=body.mode,
            payload=item_payload,
            workflow_revision_hash=(
                "MASK_CUTOUT_V1" if manual_cutout else QWEN_EDIT_TEMPLATE_SHA256 if body.mode != "T2I" else None
            ),
            runtime_fingerprint=None,
            status="QUEUED" if can_queue else "BLOCKED_CAPABILITY",
            external_outcome="NOT_SENT",
        )
        db.add(task)
        created.append(task)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rows = [
            db.scalar(
                select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == f"{body.idempotency_key}:{i + 1}")
            )
            for i in range(body.candidate_count)
        ]
        if all(row is None for row in rows):
            raise HTTPException(
                409,
                {"code": "IMAGE_TASK_PERSISTENCE_CONFLICT", "message": "生成任务未能安全保存，请刷新后重试"},
            ) from exc
        if any(row is None for row in rows) or any(
            row.request_hash
            != service.canonical_hash(
                {**payload, "candidate_index": i, "seed": (body.seed + i) if body.seed is not None else None}
            )
            for i, row in enumerate(rows)
        ):
            raise HTTPException(
                409, {"code": "IMAGE_JOB_IDEMPOTENCY_CONFLICT", "message": "生成请求发生冲突，请刷新后重试"}
            ) from exc
        created = [task_data(db, row) for row in rows]
    created = [task_data(db, row) if isinstance(row, LibraryRenderTask) else row for row in created]
    return {
        "items": created,
        "capability": capability,
        "notice": "生成任务已保存；只有本机能力完成验证后才会进入显卡队列。"
        if not can_queue
        else "任务已排入本机生成队列。",
    }


def task_data(db, row: LibraryRenderTask):
    attempts = db.scalars(
        select(LibraryTaskAttempt).where(LibraryTaskAttempt.task_id == row.id).order_by(LibraryTaskAttempt.attempt_no)
    ).all()
    return {
        "id": row.id,
        "kind": row.kind,
        "mode": row.mode,
        "status": row.status,
        "state_version": row.state_version,
        "error_code": row.error_code,
        "external_outcome": row.external_outcome,
        "external_prompt_id": row.external_prompt_id,
        "result_version_id": row.result_version_id,
        "capability": (row.payload or {}).get("capability"),
        "candidate_index": (row.payload or {}).get("candidate_index"),
        "can_new_attempt": bool(
            row.kind == "image"
            and row.status == "FAILED"
            and row.external_outcome == "NOT_SENT"
            and row.submit_intent_at is None
            and row.external_prompt_id is None
        ),
        "attempts": [
            {
                "id": attempt.id,
                "attempt_no": attempt.attempt_no,
                "status": attempt.status,
                "external_prompt_id": attempt.external_prompt_id,
                "external_outcome": attempt.external_outcome,
                "error_code": attempt.error_code,
                "output_sha256": attempt.output_sha256,
            }
            for attempt in attempts
        ],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/asset-center/image-jobs/{task_id}")
@router.get("/asset-center/voice-jobs/{task_id}")
def get_library_task(task_id: str, db: DBSession):
    row = db.get(LibraryRenderTask, task_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_TASK_NOT_FOUND", "message": "任务不存在"})
    return task_data(db, row)


@router.post("/asset-center/image-jobs/{task_id}/cancel")
@router.post("/asset-center/voice-jobs/{task_id}/cancel")
def cancel_library_task(task_id: str, db: DBSession):
    row = db.get(LibraryRenderTask, task_id)
    if row is None:
        raise HTTPException(404, {"code": "LIBRARY_TASK_NOT_FOUND", "message": "任务不存在"})
    if row.status in {"SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN", "RECONCILE_REQUIRED"}:
        raise HTTPException(409, {"code": "LIBRARY_TASK_TERMINAL", "message": "任务已结束或需要对账"})
    if row.submit_intent_at is not None or row.external_prompt_id is not None or row.external_outcome != "NOT_SENT":
        raise HTTPException(
            409,
            {
                "code": "LIBRARY_TASK_ALREADY_SUBMITTED",
                "message": "任务已经进入显卡提交阶段，不能安全中断；可继续核对结果",
            },
        )
    if row.status == "RUNNING":
        row.cancel_requested = True
        row.state_version += 1
        row.status = "CANCELLED"
    else:
        row.status = "CANCELLED"
        row.state_version += 1
    add_audit(
        db,
        "local-user",
        "LIBRARY_TASK_CANCEL_REQUESTED",
        "library_render_task",
        row.id,
        {"status": row.status, "external_call": False},
    )
    db.commit()
    return task_data(db, row)


@router.post("/asset-center/image-jobs/{task_id}/reconcile")
def reconcile_library_image_task(
    task_id: str, body: ReconcileRequest, request: Request, settings: SettingsDep, db: DBSession
):
    row = db.get(LibraryRenderTask, task_id)
    if row is None or row.kind != "image":
        raise HTTPException(404, {"code": "LIBRARY_TASK_NOT_FOUND", "message": "图片任务不存在"})
    if row.status not in {"UNKNOWN", "RECONCILE_REQUIRED"}:
        if row.status != "RECONCILING":
            raise HTTPException(409, {"code": "LIBRARY_TASK_NOT_UNKNOWN", "message": "当前任务不需要对账"})
    if not row.external_prompt_id:
        return {
            **task_data(db, row),
            "reconciliation": "NO_EXTERNAL_PROMPT_ID",
            "next_action": "保留记录，检查本机 ComfyUI 队列后由操作人决定是否创建新任务。",
        }
    config = image_runtime.load_config(settings)
    if not config.base_url:
        raise HTTPException(409, {"code": "IMAGE_RUNTIME_NOT_BOUND", "message": "本机图片服务尚未绑定"})
    from shine.comfy.client import ComfyUIClient

    client = ComfyUIClient(config.base_url, max_retries=0)
    try:
        history = client.history(row.external_prompt_id).get(row.external_prompt_id)
    except Exception as exc:  # noqa: BLE001 - read-only reconciliation only
        raise HTTPException(503, {"code": "RECONCILIATION_READ_FAILED", "message": "暂时无法读取本机任务记录"}) from exc
    if not history:
        return {
            **task_data(db, row),
            "reconciliation": "PROMPT_NOT_FOUND",
            "next_action": "不会自动重提；请检查 ComfyUI 历史记录。",
        }
    prompt_id = row.external_prompt_id
    db.rollback()
    try:
        from shine.asset_center.execution import reconcile_completed_history

        result = reconcile_completed_history(
            request.app.state.session_factory,
            settings,
            task_id,
            prompt_id,
            history,
            client,
            body.evidence,
        )
    except RuntimeError as exc:
        code = str(exc)
        if code == "LIBRARY_RECONCILIATION_RACE":
            raise HTTPException(409, {"code": code, "message": "另一项对账正在处理，请刷新任务状态"}) from exc
        raise HTTPException(
            503, {"code": code, "message": "无法安全收获此结果，请保留任务并检查本机 ComfyUI 记录"}
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a failed read/adoption never starts a new render
        raise HTTPException(
            503, {"code": "RECONCILIATION_ADOPTION_FAILED", "message": "无法安全收获此结果，请稍后检查任务"}
        ) from exc
    refreshed = db.get(LibraryRenderTask, task_id)
    return {**task_data(db, refreshed), **result, "next_action": "候选已保留在素材库中，请完成技术检查和人工审核。"}


@router.post("/asset-center/image-jobs/{task_id}/new-attempt", status_code=202)
def new_image_attempt(task_id: str, body: NewImageAttempt, settings: SettingsDep, db: DBSession):
    previous = db.get(LibraryRenderTask, task_id)
    if previous is None or previous.kind != "image":
        raise HTTPException(404, {"code": "LIBRARY_TASK_NOT_FOUND", "message": "图片任务不存在"})
    if not body.confirmed:
        raise HTTPException(
            409, {"code": "NEW_ATTEMPT_CONFIRMATION_REQUIRED", "message": "请明确确认创建新的物理执行任务"}
        )
    if (
        previous.status != "FAILED"
        or previous.external_outcome != "NOT_SENT"
        or previous.submit_intent_at is not None
        or previous.external_prompt_id is not None
    ):
        raise HTTPException(
            409,
            {
                "code": "NEW_ATTEMPT_NOT_SAFE",
                "message": "只有已确认未提交到 ComfyUI 的失败任务才能复制；UNKNOWN 结果必须先对账。",
            },
        )
    prior_payload = dict(previous.payload or {})
    if prior_payload.get("requirement_context"):
        raise HTTPException(
            409,
            {
                "code": "MATERIAL_REQUIREMENT_REQUEUE_REQUIRES_REFRESH",
                "message": "请回到当前项目素材需求并刷新后重新提交。",
            },
        )
    new_key = body.idempotency_key
    ref_rows = [
        {key: value for key, value in row.items() if key in {"media_id", "version_id", "order", "role"}}
        for row in (prior_payload.get("references") or [])
    ]
    request = ImageJobRequest(
        idempotency_key=new_key,
        title=str(prior_payload.get("title") or ""),
        mode=previous.mode,
        prompt=str(prior_payload.get("prompt") or ""),
        references=ref_rows,
        mask_media_id=(prior_payload.get("mask") or {}).get("media_id"),
        output=prior_payload.get("output") or {"width": 1024, "height": 1024},
        seed=prior_payload.get("seed"),
        candidate_count=1,
        category=str(prior_payload.get("category") or "other"),
        transparent=bool(prior_payload.get("transparent")),
        target_asset_id=prior_payload.get("target_asset_id"),
        parent_version_id=prior_payload.get("parent_version_id"),
        expected_asset_state_version=prior_payload.get("expected_asset_state_version"),
        validation_only=bool(prior_payload.get("validation_only")),
    )
    result = create_image_jobs(request, settings, db)
    created = result["items"][0]
    add_audit(
        db,
        "local-user",
        "LIBRARY_IMAGE_NEW_ATTEMPT_CREATED",
        "library_render_task",
        created["id"],
        {"previous_task_id": previous.id, "idempotency_key": new_key, "confirmed": True},
    )
    db.commit()
    return {**task_data(db, db.get(LibraryRenderTask, created["id"])), "notice": result["notice"]}


@router.post("/asset-center/assignments", status_code=201)
def assign_library_version(body: Assignment, actor: Operator, settings: SettingsDep, db: DBSession):
    version = db.get(LibraryVersion, body.library_version_id)
    if version is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "素材版本不存在"})
    if body.requirement_context:
        from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan
        from shine.comfyops.preproduction import current_material_plan

        project_id = body.requirement_context.get("project_id")
        requirement_id = body.requirement_context.get("requirement_id")
        expected_hash = body.requirement_context.get("requirement_hash")
        requirement = db.get(AssetRequirement, requirement_id) if requirement_id else None
        plan = db.get(PreProductionPlan, requirement.plan_id) if requirement else None
        active_plan = current_material_plan(db, body.project_id)
        item = (requirement.payload or {}) if requirement else {}
        if (
            requirement is None
            or requirement.project_id != body.project_id
            or project_id != body.project_id
            or requirement.sha256 != expected_hash
            or plan is None
            or plan.project_id != body.project_id
            or plan.purpose != PLAN_PURPOSE_ASSET_PREPARATION
            or active_plan is None
            or active_plan.id != plan.id
            or item.get("family") != body.role
            or item.get("entity_id") != (body.entity_id or "")
        ):
            raise HTTPException(
                409, {"code": "MATERIAL_REQUIREMENT_STALE", "message": "素材需求已经变化，请刷新后重新分配"}
            )
    elif body.role in {"character", "scene", "prop", "costume"} and body.entity_id:
        # Manual project assignments remain available outside a script demand;
        # the requirement path above is bound to the exact family and identity.
        pass
    link = service.assign_to_project(
        db,
        settings,
        project_id=body.project_id,
        version=version,
        role=body.role,
        entity_id=body.entity_id.strip() if body.entity_id else None,
        usage_scope=body.usage_scope,
        idempotency_key=body.idempotency_key,
        actor=actor,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(LibraryProjectLink).where(
                LibraryProjectLink.project_id == body.project_id,
                LibraryProjectLink.library_version_id == body.library_version_id,
                LibraryProjectLink.role == body.role,
                LibraryProjectLink.entity_id.is_(None)
                if not body.entity_id
                else LibraryProjectLink.entity_id == body.entity_id.strip(),
                LibraryProjectLink.usage_scope == body.usage_scope,
            )
        )
        if existing:
            return {
                "id": existing.id,
                "project_id": existing.project_id,
                "library_asset_id": existing.library_asset_id,
                "library_version_id": existing.library_version_id,
                "project_asset_id": existing.project_asset_id,
                "project_voice_profile_id": existing.project_voice_profile_id,
                "role": existing.role,
                "entity_id": existing.entity_id,
                "usage_scope": existing.usage_scope,
                "rights_snapshot_hash": existing.rights_snapshot_hash,
            }
        raise HTTPException(
            409,
            {"code": "PROJECT_ASSET_ALREADY_REGISTERED", "message": "此项目已登记相同文件内容，请复用原项目素材身份"},
        ) from exc
    db.refresh(link)
    return {
        "id": link.id,
        "project_id": link.project_id,
        "library_asset_id": link.library_asset_id,
        "library_version_id": link.library_version_id,
        "project_asset_id": link.project_asset_id,
        "project_voice_profile_id": link.project_voice_profile_id,
        "role": link.role,
        "entity_id": link.entity_id,
        "usage_scope": link.usage_scope,
        "rights_snapshot_hash": link.rights_snapshot_hash,
    }


@router.get("/asset-center/assignments")
def list_assignments(db: DBSession, project_id: str | None = None, asset_id: str | None = None):
    stmt = select(LibraryProjectLink)
    if project_id:
        stmt = stmt.where(LibraryProjectLink.project_id == project_id)
    if asset_id:
        stmt = stmt.where(LibraryProjectLink.library_asset_id == asset_id)
    links = db.scalars(stmt.order_by(LibraryProjectLink.created_at.desc()).limit(500)).all()
    rows = []
    for link in links:
        project = db.get(Project, link.project_id)
        asset = db.get(LibraryAsset, link.library_asset_id)
        version = db.get(LibraryVersion, link.library_version_id)
        rows.append(
            {
                "id": link.id,
                "project_id": link.project_id,
                "project_title": project.title if project else "",
                "library_asset_id": link.library_asset_id,
                "title": asset.title if asset else "",
                "library_version_id": link.library_version_id,
                "version": version.ordinal if version else None,
                "content_sha256": version.content_sha256 if version else None,
                "project_asset_id": link.project_asset_id,
                "project_voice_profile_id": link.project_voice_profile_id,
                "role": link.role,
                "entity_id": link.entity_id,
                "usage_scope": link.usage_scope,
                "created_at": link.created_at,
            }
        )
    return {"items": rows}


@router.get("/projects/{project_id}/asset-center/search")
def search_library_for_project(
    project_id: str, db: DBSession, q: str = Query(default="", max_length=200),
    category: str | None = None, scope: str = Query(default="project", max_length=16),
):
    """Search the library from inside one project's material step.

    The default scope is this project. The library is shared on purpose - a good
    plate or a coat can serve several productions - but a person preparing
    materials for one film must not be shown another film's generated stills as
    if they were candidates for this one. Anything this project never linked is
    excluded unless the caller explicitly asks for the whole library.
    """
    if db.get(Project, project_id) is None:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "找不到所选影视项目"})
    project_versions: set[str] = set()
    if scope != "library":
        project_versions = {
            version_id
            for version_id in db.scalars(
                select(LibraryProjectLink.library_version_id).where(
                    LibraryProjectLink.project_id == project_id,
                    LibraryProjectLink.library_version_id.is_not(None),
                )
            )
            if version_id
        }
        if not project_versions:
            return {"items": [], "scope": "project",
                    "note": "这个项目还没有用过素材库里的版本；如需跨项目挑选，请显式选择搜索全部素材库。"}
    stmt = select(LibraryAsset).where(LibraryAsset.status == "ACTIVE")
    if category:
        stmt = stmt.where(LibraryAsset.category == category)
    rows = db.scalars(stmt.order_by(LibraryAsset.updated_at.desc(), LibraryAsset.id).execution_options(yield_per=100))
    term = q.casefold().strip()
    output = []
    for asset in rows:
        if not _matches_library_query(asset, term):
            continue
        version = db.get(LibraryVersion, asset.current_version_id) if asset.current_version_id else None
        if version is None or version.status != "PROMOTED" or version.is_mock:
            continue
        if project_versions and version.id not in project_versions:
            # Another production's still. It is not a candidate for this one.
            continue
        try:
            service.require_version_rights(db, version, project_id=project_id, usage_scope="local")
        except HTTPException:
            continue
        output.append({"asset": service.asset_data(db, asset), "version": service.version_data(db, version)})
        if len(output) == 200:
            break
    return {"items": output}


@router.post("/projects/{project_id}/assets/{asset_id}/save-to-library", status_code=201)
def save_project_asset_to_library(
    project_id: str,
    asset_id: str,
    body: SaveToLibrary,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    project = db.get(Project, project_id)
    asset = db.get(Asset, asset_id)
    if project is None or asset is None or asset.project_id != project_id:
        raise HTTPException(404, {"code": "PROJECT_ASSET_NOT_FOUND", "message": "项目素材不存在"})
    if not asset.approved or not asset.locked or (asset.provenance or {}).get("offline_only"):
        raise HTTPException(
            409, {"code": "PROJECT_ASSET_NOT_EXPORTABLE", "message": "只有已批准、锁定且非离线模拟的项目素材可以收藏"}
        )
    project_rights_id = (asset.provenance or {}).get("rights_id")
    project_rights = db.get(RightsRecord, project_rights_id) if project_rights_id else None
    if project_rights_id:
        require_project_rights(db, project_id, project_rights_id)
    source_rights = project_rights.payload if project_rights else {}
    path = safe_asset_path(settings.data_dir, asset.storage_path)
    content = path.read_bytes() if path.is_file() else b""
    if not content or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise HTTPException(409, {"code": "PROJECT_ASSET_HASH_CHANGED", "message": "项目素材文件校验失败"})
    global_rights = LibraryRights(
        source_type="project_asset",
        license_kind=str(source_rights.get("license_kind") or source_rights.get("license_id", "UNSPECIFIED")),
        scope="asset",
        statement=f"从作品 {project.title} 收藏。",
        subject=str(source_rights.get("subject", "")),
        allowed_project_ids=[project_id],
        permitted_uses=["research"],
        commercial_allowed=False,
        status="APPROVED",
        recorded_actor=actor,
        approved_by=actor,
        expires_at=service.parse_expiry(source_rights.get("expires_at")),
    )
    db.add(global_rights)
    db.flush()
    global_media = LibraryMedia(
        kind=asset.kind,
        purpose="source",
        filename=asset.filename,
        content_type=asset.content_type,
        sha256=asset.sha256,
        storage_path=asset.storage_path,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        duration_s=asset.duration_s,
        rights_id=global_rights.id,
        created_by=actor,
    )
    db.add(global_media)
    db.flush()
    library_asset = LibraryAsset(
        title=body.title.strip(), kind=asset.kind, category=body.category, tags=body.tags, created_by=actor
    )
    db.add(library_asset)
    db.flush()
    version = service.create_version_from_media(
        db,
        settings,
        asset=library_asset,
        media=global_media,
        actor=actor,
        source_mode="PROJECT_ASSET",
        provenance={
            "source_project_id": project.id,
            "source_project_asset_id": asset.id,
            "source_rights_id": project_rights_id,
        },
    )
    version.status = "READY_FOR_REVIEW"
    version.qc = {
        "technical": "pass",
        "decode": {"status": "pass"},
        "semantic": "PENDING_HUMAN_REVIEW",
        "source_locked_project_asset": asset.id,
    }
    add_audit(
        db,
        actor,
        "PROJECT_ASSET_SAVED_TO_LIBRARY",
        "library_version",
        version.id,
        {"project_asset_id": asset.id, "sha256": asset.sha256, "cross_project_scope": "NOT_GRANTED"},
        project_id=project.id,
    )
    db.commit()
    return service.asset_data(db, library_asset, include_versions=True)


@router.post("/asset-center/voice-jobs", status_code=202)
def create_voice_job(body: VoiceJobRequest, settings: SettingsDep, db: DBSession):
    payload = body.model_dump(mode="json")
    if body.mode == "DESIGN" and (not body.description.strip() or not body.text.strip()):
        raise HTTPException(422, {"code": "VOICE_DESIGN_INPUT_REQUIRED", "message": "音色设计需要声音描述和试听文本"})
    if body.mode == "TTS" and not body.text.strip():
        raise HTTPException(422, {"code": "VOICE_DIALOGUE_TEXT_REQUIRED", "message": "普通话对白不能为空"})
    if body.mode == "AUTHORIZED_CLONE":
        media = db.get(LibraryMedia, body.reference_media_id) if body.reference_media_id else None
        rights = db.get(LibraryRights, media.rights_id) if media else None
        if (
            media is None
            or media.kind != "audio"
            or media.purpose != "voice_sample"
            or media.content_type != "audio/wav"
            or rights is None
            or rights.scope != "voice_clone"
        ):
            raise HTTPException(
                409, {"code": "VOICE_SAMPLE_REQUIRED", "message": "声音克隆需要导入一段 WAV 录音作为声音样本"}
            )
        if body.rights_id and body.rights_id != rights.id:
            raise HTTPException(409, {"code": "VOICE_CONSENT_SOURCE_MISMATCH", "message": "授权记录与来源录音不匹配"})
        if not body.reference_text.strip():
            raise HTTPException(
                422, {"code": "VOICE_REFERENCE_TRANSCRIPT_REQUIRED", "message": "授权克隆需要填写录音中的实际对白"}
            )
        service.require_rights(rights, usage_scope="local")
        payload["rights_id"] = rights.id
        payload["reference_media_sha256"] = media.sha256
        payload["rights_snapshot"] = service.rights_snapshot(rights)
    elif body.mode == "TTS":
        profile = db.get(LibraryVoiceProfile, body.voice_profile_id) if body.voice_profile_id else None
        if profile is None or profile.status != "FROZEN":
            raise HTTPException(409, {"code": "VOICE_PROFILE_NOT_FROZEN", "message": "对白生成需要已试听冻结的音色"})
        voice_version = db.get(LibraryVersion, profile.version_id)
        if voice_version is None:
            raise HTTPException(409, {"code": "VOICE_PROFILE_VERSION_MISSING", "message": "冻结音色缺少对应版本"})
        service.require_version_rights(db, voice_version, usage_scope="local")
        service.require_rights(db.get(LibraryRights, profile.rights_id), usage_scope="local")
        payload["voice_hash"] = profile.voice_hash
        payload["rights_snapshot"] = service.rights_snapshot(db.get(LibraryRights, profile.rights_id))
        payload["voice_version_id"] = profile.version_id
    from shine.asset_center.voice_execution import can_queue_voice

    can_queue = can_queue_voice(db, settings, body.mode)
    capability = {
        "state": "PENDING_LOCAL_VALIDATION",
        "can_generate": False,
        "can_queue": can_queue,
        "reason": "任务仅在已配置的本机声音 Provider 中排队；推理结果仍需真人试听和人工审核。",
    }
    request_hash = service.canonical_hash(payload)
    key = body.idempotency_key + ":1"
    prior = db.scalar(select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == key))
    if prior:
        if prior.request_hash != request_hash:
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "此声音任务编号已用于不同参数"})
        return {
            "item": task_data(db, prior),
            "capability": capability,
            "notice": "已恢复此前保存的同一声音任务，没有重复创建。",
        }
    payload["capability"] = capability
    row = LibraryRenderTask(
        id=new_id(),
        idempotency_key=key,
        request_hash=request_hash,
        kind="voice",
        mode=body.mode,
        payload=payload,
        status="QUEUED" if can_queue else "BLOCKED_CAPABILITY",
        external_outcome="NOT_SENT",
    )
    db.add(row)
    add_audit(
        db,
        "local-user",
        "LIBRARY_VOICE_JOB_REQUESTED",
        "library_render_task",
        row.id,
        {"mode": row.mode, "status": row.status, "is_mock": False, "can_queue": can_queue},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        prior = db.scalar(select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == key))
        if prior is None:
            raise HTTPException(
                409, {"code": "VOICE_TASK_PERSISTENCE_CONFLICT", "message": "声音任务未能安全保存，请刷新后重试"}
            ) from exc
        if prior.request_hash != request_hash:
            raise HTTPException(
                409, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "此声音任务编号已用于不同参数"}
            ) from exc
        return {"item": task_data(db, prior), "capability": capability}
    return {
        "item": task_data(db, row),
        "capability": capability,
        "notice": "声音任务已保存；如果本机 Provider 尚未配置，任务会安全停留在待验证状态。",
    }


@router.post("/asset-center/voice-profiles/{profile_id}/grant")
def grant_voice_freeze(profile_id: str, actor: Operator, db: DBSession):
    profile = db.get(LibraryVoiceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, {"code": "LIBRARY_VOICE_NOT_FOUND", "message": "全局音色不存在"})
    version = db.get(LibraryVersion, profile.version_id)
    if version is None or version.status != "PROMOTED" or version.is_mock:
        raise HTTPException(409, {"code": "VOICE_VERSION_NOT_REVIEWABLE", "message": "请先完成真实音频审核和晋升"})
    service.require_version_rights(db, version, usage_scope="local")
    service.require_rights(db.get(LibraryRights, profile.rights_id), usage_scope="local")
    return service.issue_grant(db, actor, "library_voice_freeze", version)


@router.post("/asset-center/voice-profiles/{profile_id}/freeze")
def freeze_library_voice(profile_id: str, body: VoiceFreeze, actor: Operator, db: DBSession):
    profile = db.get(LibraryVoiceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, {"code": "LIBRARY_VOICE_NOT_FOUND", "message": "全局音色不存在"})
    if not body.listened:
        raise HTTPException(409, {"code": "VOICE_LISTENING_CONFIRMATION_REQUIRED", "message": "请先实际试听候选音频"})
    version = db.get(LibraryVersion, profile.version_id)
    if version is None:
        raise HTTPException(404, {"code": "LIBRARY_VERSION_NOT_FOUND", "message": "音色版本不存在"})
    service.consume_grant(db, actor, "library_voice_freeze", version, body.grant_id, body.snapshot)
    service.require_version_rights(db, version, usage_scope="local")
    service.require_rights(db.get(LibraryRights, profile.rights_id), usage_scope="local")
    if version.status != "PROMOTED" or version.is_mock:
        raise HTTPException(409, {"code": "VOICE_VERSION_NOT_REVIEWABLE", "message": "模拟或未晋升音频不能冻结"})
    profile.status = "FROZEN"
    profile.frozen_by = actor
    profile.frozen_at = utcnow()
    add_audit(
        db,
        actor,
        "LIBRARY_VOICE_FROZEN",
        "library_voice_profile",
        profile.id,
        {"version_id": version.id, "voice_hash": profile.voice_hash, "listened": True, "evidence": body.evidence},
    )
    db.commit()
    return {
        "id": profile.id,
        "status": profile.status,
        "voice_hash": profile.voice_hash,
        "frozen_by": profile.frozen_by,
        "frozen_at": profile.frozen_at,
    }


@router.get("/asset-center/voice-profiles")
def list_library_voices(db: DBSession):
    rows = db.scalars(select(LibraryVoiceProfile).order_by(LibraryVoiceProfile.created_at.desc()).limit(200)).all()
    result = []
    for row in rows:
        asset = db.get(LibraryAsset, row.library_asset_id)
        version = db.get(LibraryVersion, row.version_id)
        result.append(
            {
                "id": row.id,
                "title": asset.title if asset else "",
                "version_id": row.version_id,
                "voice_hash": row.voice_hash,
                "source": row.source,
                "status": row.status,
                "rights": service.rights_snapshot(db.get(LibraryRights, row.rights_id)),
                "media_url": f"/api/v1/asset-center/versions/{version.id}/media" if version else None,
                "created_at": row.created_at,
            }
        )
    return {"items": result}


@router.get("/asset-center/local-validation-plan")
def local_validation_plan():
    return {
        "workflow": {
            "repository": QWEN_EDIT_REPOSITORY,
            "path": QWEN_EDIT_PATH,
            "commit": QWEN_EDIT_COMMIT,
            "sha256": QWEN_EDIT_TEMPLATE_SHA256,
        },
        "model": {"repository": "Comfy-Org/Qwen-Image-2.1", "revision": "aacf31a6d48e707f352ecba714cee0680da3479b"},
        "image_slots": {"total": 10, "image_1": "edit_target", "image_2_to_10": "ordered_references"},
        "mask": {
            "status": "PENDING_LOCAL_VALIDATION",
            "current_workflow_has_mask_input": False,
            "transport": (
                "actual PNG bytes are appended as the final images.image_N annotation slot, "
                "then exact-pixel compositing is applied"
            ),
            "required_semantics": "white=edit,black=preserve",
            "must_match_source_pixels": True,
        },
        "output": {"node_id": "461", "format": "PNG", "must_check_hash_and_decode": True},
        "required_evidence": [
            "ComfyUI object_info",
            "model file hashes",
            "API graph hash",
            "prompt_id",
            "declared output node",
            "output SHA-256",
            "decoded dimensions",
            "alpha pixels",
            "mask dimensions and semantics",
            "GPU peak and release",
            "human review",
        ],
        "safety": "Do not run against a live H3 job or user's production library; use an isolated local test dataset.",
    }


# ---------------------------------------------------------------------------
# Assign -> adopt: the project master becomes a reference on exact shots
# ---------------------------------------------------------------------------


class AdoptionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    library_version_id: str | None = None
    project_asset_id: str
    shot_ids: list[str] | None = Field(default=None, max_length=200)
    role: str | None = None
    entity_id: str | None = None


class AdoptionRequest(AdoptionPreviewRequest):
    expected_shot_revisions: dict[str, int] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=120)
    confirmed: bool = False


def _adoption_asset(db, body: AdoptionPreviewRequest):
    from shine.models import Asset

    asset = db.get(Asset, body.project_asset_id)
    if asset is None or asset.project_id != body.project_id:
        raise HTTPException(404, {"code": "PROJECT_ASSET_NOT_FOUND", "message": "项目素材不存在"})
    provenance = asset.provenance or {}
    if body.library_version_id is not None and provenance.get("library_version_id") != body.library_version_id:
        raise HTTPException(409, {
            "code": "LIBRARY_ASSIGNMENT_REQUIRED",
            "message": "先把全局版本分配到本项目，再采用到镜头",
            "library_version_id": body.library_version_id,
        })
    if body.role is not None and body.role != asset.role:
        raise HTTPException(409, {"code": "ASSET_ROLE_MISMATCH", "message": "素材用途与作品中的用途不一致"})
    if body.entity_id is not None and body.entity_id != asset.entity_id:
        raise HTTPException(409, {"code": "ASSET_ENTITY_MISMATCH", "message": "素材身份与作品中的身份不一致"})
    if not (asset.approved and asset.locked):
        raise HTTPException(409, {"code": "ASSET_NOT_MASTER", "message": "项目素材尚未确认并锁定"})
    return asset


def _confirmed_package_for_shot(db, project_id: str, shot_id: str) -> str | None:
    """The *current* confirmed package that still contains this shot.

    A confirmed package from an earlier revision is history, not a lock: once a
    newer package revision exists, the person has already started the path that
    replaces it.  Blocking on the oldest confirmed row forever made an explicit
    material replacement impossible to finish (J07).
    """
    from shine.director.models import DirectorPackageEpisode, DirectorPackageVersion

    latest = db.scalars(
        select(DirectorPackageVersion)
        .where(DirectorPackageVersion.project_id == project_id)
        .order_by(DirectorPackageVersion.revision.desc())
        .limit(1)
    ).first()
    if latest is None or latest.status != "CONFIRMED" or latest.stale:
        return None
    packages = [latest]
    for package in packages:
        episodes = db.scalars(
            select(DirectorPackageEpisode).where(DirectorPackageEpisode.package_id == package.id)
        ).all()
        if any(
            shot.get("shot_id") == shot_id
            for episode in episodes
            for shot in (episode.manifest or {}).get("shots", [])
        ):
            return package.id
    return None


def _adoption_plan(db, body: AdoptionPreviewRequest) -> tuple[Any, list[dict[str, Any]]]:
    from shine.models import Asset, Job, Shot
    from shine.schemas import ShotSpec
    from shine.services import UNKNOWN_SUBMISSION_CODES

    asset = _adoption_asset(db, body)
    from shine.comfyops.assets import rights_status

    # A revoked licence is a fact about the material, not about the shot:
    # the preview names it and the apply refuses it, so no revision is
    # written for an asset nobody may use any more.
    rights = rights_status(db, body.project_id, (asset.provenance or {}).get("rights_id"))
    shots = list(db.scalars(select(Shot).where(Shot.project_id == body.project_id)).all())
    if body.shot_ids is not None:
        by_id = {shot.id: shot for shot in shots}
        missing = [shot_id for shot_id in body.shot_ids if shot_id not in by_id]
        if missing:
            raise HTTPException(404, {"code": "SHOT_NOT_FOUND", "missing": missing})
        scoped = [by_id[shot_id] for shot_id in dict.fromkeys(body.shot_ids)]
    else:
        # No explicit list: the shots whose storyboard asks for this identity.
        wanted_entity = (asset.entity_id or "").strip()
        scoped = []
        for shot in shots:
            try:
                spec = ShotSpec.model_validate(shot.spec)
            except Exception:
                continue
            if asset.role == "character" and wanted_entity in spec.characters:
                scoped.append(shot)
            elif wanted_entity and any(
                (required := db.get(Asset, asset_id)) is not None
                and required.project_id == body.project_id
                and required.role == asset.role
                and required.entity_id == wanted_entity
                for asset_id in spec.required_asset_ids
            ):
                scoped.append(shot)
    use_as = "subject" if (body.role == "character" or asset.kind == "image" and asset.role == "character") else (
        {"image": "picture", "video": "video", "audio": "audio"}.get(asset.kind, "picture")
    )
    entries = []
    for shot in scoped:
        already = [row for row in shot.bindings if row.asset_id == asset.id]
        package_id = _confirmed_package_for_shot(db, body.project_id, shot.id)
        in_flight = db.scalars(
            select(Job).where(
                Job.project_id == body.project_id,
                Job.shot_id == shot.id,
                Job.stale.is_(False),
                or_(
                    Job.status.in_(["PLANNED", "QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING"]),
                    and_(Job.status == "NEEDS_REVIEW", Job.failure_code.in_(sorted(UNKNOWN_SUBMISSION_CODES))),
                ),
            ).order_by(Job.created_at.desc())
        ).first()
        entries.append({
            "shot_id": shot.id,
            "shot_name": shot.name,
            "revision": shot.revision,
            "already_bound": bool(already),
            "current_binding_count": len(list(shot.bindings)),
            "use_as": already[0].use_as if already else use_as,
            "will_bump_revision": not already,
            "confirmed_package_id": package_id,
            "production_job_id": getattr(in_flight, "id", None),
            "production_status": getattr(in_flight, "status", None),
            "rights": {"approved": rights["approved"], "code": rights["code"],
                       "message": rights["message"]},
            "conflict": (
                "ASSET_RIGHTS_NOT_APPROVED" if not rights["approved"]
                else None if already
                else ("SHOT_PRODUCTION_IN_FLIGHT" if in_flight
                      else "SHOT_IN_CONFIRMED_PACKAGE" if package_id else None)
            ),
            "action": (
                "REPLACE_WITH_AUTHORIZED_VERSION" if not rights["approved"]
                else None if already
                else ("RECONCILE_OR_FINISH_CURRENT_TAKE" if in_flight
                      else "REGENERATE_PACKAGE_AFTER_ADOPTION" if package_id else None)
            ),
        })
    return asset, entries


@router.post("/asset-center/adoptions/preview")
def preview_adoption(body: AdoptionPreviewRequest, db: DBSession):
    """What adopting this version onto these shots would change, before consent.

    The preview names the exact shot revisions and the conflicts (an already
    confirmed package).  It never writes a binding.
    """
    asset, entries = _adoption_plan(db, body)
    return {
        "project_id": body.project_id,
        "project_asset_id": asset.id,
        "library_version_id": body.library_version_id,
        "asset_sha256": asset.sha256,
        "shot_count": len(entries),
        "shots": entries,
        "blocked": [row for row in entries if row["conflict"]],
    }


@router.post("/asset-center/adoptions", status_code=201)
def adopt_into_shots(body: AdoptionRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    """Apply the project master to exactly the shots the person agreed to.

    Each shot that really changes becomes a new shot revision; the previous
    take is invalidated, never overwritten.  A shot inside an already confirmed
    package is a named conflict: the person must re-freeze the package instead
    of silently producing a different film than the one confirmed.
    """
    from shine.models import AppSetting, Project, Shot
    from shine.schemas import ShotBindingInput, ShotSpec
    from shine.services import revise_shot_record

    project = db.get(Project, body.project_id)
    if project is None:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND"})

    if not body.confirmed:
        raise HTTPException(409, {"code": "ADOPTION_CONFIRMATION_REQUIRED",
                                  "message": "请先确认采用范围与镜头版本"})
    # The canonical request is part of the receipt: the same key with a
    # different payload is a named conflict, never a replay of the old answer
    # (J07's second counterexample).
    request_digest = hashlib.sha256(json.dumps({
        "project_id": body.project_id,
        "library_version_id": body.library_version_id,
        "project_asset_id": body.project_asset_id,
        "shot_ids": sorted(set(body.shot_ids)) if body.shot_ids is not None else None,
        "role": body.role,
        "entity_id": body.entity_id,
        "expected_shot_revisions": {key: body.expected_shot_revisions[key]
                                    for key in sorted(body.expected_shot_revisions)},
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    # AppSetting.key is String(80); the digest keeps the receipt id bounded
    # while scoping the caller's key to this project.
    receipt_key = "asset-adoption:" + hashlib.sha256(
        f"{body.project_id}:{body.idempotency_key}".encode()
    ).hexdigest()
    previous = db.get(AppSetting, receipt_key)
    if previous is not None:
        if (previous.value or {}).get("request_digest") != request_digest:
            raise HTTPException(409, {
                "code": "ADOPTION_IDEMPOTENCY_CONFLICT",
                "message": "同一个请求编号被用于了不同的采用内容；请用新的编号重新确认",
                "adoption_id": receipt_key,
            })
        return {**previous.value, "replayed": True}
    asset, entries = _adoption_plan(db, body)
    blocked = [row for row in entries if row["conflict"]]
    if blocked:
        in_flight = [row for row in blocked if row["conflict"] == "SHOT_PRODUCTION_IN_FLIGHT"]
        raise HTTPException(409, {
            "code": "ADOPTION_BLOCKED",
            "message": (
                "这些镜头正在拍摄或结果未知：先按原尝试收束，再继续采用"
                if in_flight else
                "这些镜头属于当前已确认的导演包；先生成并确认新的导演包，不能静默替换已确认的内容"
            ),
            "blocked": blocked,
        })
    if not entries:
        raise HTTPException(422, {"code": "ADOPTION_SHOTS_REQUIRED", "message": "请选择需要采用素材的镜头"})
    path = safe_asset_path(settings.data_dir, asset.storage_path)
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise HTTPException(409, {"code": "PROJECT_ASSET_FILE_MISSING", "message": "项目素材文件不可读取"}) from exc
    if digest != asset.sha256:
        raise HTTPException(409, {"code": "PROJECT_ASSET_HASH_CHANGED", "message": "项目素材文件校验失败"})
    results = []
    for entry in entries:
        shot = db.get(Shot, entry["shot_id"])
        expected = body.expected_shot_revisions.get(shot.id)
        if expected is None or expected != shot.revision:
            raise HTTPException(409, {
                "code": "SHOT_REVISION_CHANGED",
                "message": "镜头在你确认之后被修改过，请重新预览",
                "shot_id": shot.id,
                "expected": expected,
                "current": shot.revision,
            })
        current = [
            ShotBindingInput(
                asset_id=row.asset_id, use_as=row.use_as, role=row.role,
                frame_role=row.frame_role, ordinal=row.ordinal,
            )
            for row in sorted(shot.bindings, key=lambda link: link.ordinal)
        ]
        if any(row.asset_id == asset.id for row in current):
            results.append({"shot_id": shot.id, "status": "ALREADY_BOUND", "revision": shot.revision})
            continue
        if len(current) >= 12:
            raise HTTPException(409, {"code": "SHOT_REFERENCE_LIMIT", "shot_id": shot.id})
        current.append(ShotBindingInput(
            asset_id=asset.id,
            use_as=entry["use_as"],
            role=body.role or asset.role,
            ordinal=len(current),
        ))
        revise_shot_record(
            db, project, shot,
            spec=ShotSpec.model_validate(shot.spec), bindings=current, actor=actor,
        )
        results.append({
            "shot_id": shot.id,
            "status": "ADOPTED",
            "from_revision": entry["revision"],
            "to_revision": shot.revision,
            "use_as": entry["use_as"],
        })
    receipt = {
        "adoption_id": receipt_key,
        "request_digest": request_digest,
        "project_id": body.project_id,
        "library_version_id": body.library_version_id,
        "project_asset_id": asset.id,
        "asset_sha256": asset.sha256,
        "shots": results,
        "adopted_count": sum(1 for row in results if row["status"] == "ADOPTED"),
        "reused_count": sum(1 for row in results if row["status"] == "ALREADY_BOUND"),
    }
    db.add(AppSetting(key=receipt_key, value=receipt))
    db.commit()
    return {**receipt, "replayed": False}

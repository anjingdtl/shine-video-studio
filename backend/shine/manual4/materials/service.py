"""Manual4 Stage 2 material requirements, generation, upload, promotion and shot bindings."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.manual4.comfy.image_workflow import (
    DEFAULT_CFG,
    DEFAULT_RESOLUTION,
    DEFAULT_STEPS,
    MODEL_FAMILY,
    OUTPUT_NODE,
    REQUIRED_NODES,
    build_qwen_t2i_graph,
)
from shine.manual4.comfy.managed_instance import managed_m4_comfyui
from shine.manual4.comfy.workflow import graph_hash, validate_against_object_info, validate_graph
from shine.manual4.materials.probe import probe_file
from shine.manual4.status.readiness import evaluate_m4_readiness
from shine.manual4.storage.files import (
    ALLOWED_M4_EXTENSIONS,
    safe_m4_filename,
    safe_m4_path,
    store_m4_bytes,
    verify_m4_media_bytes,
)
from shine.manual4.storage.models import (
    M4AssetVersion,
    M4AuditEvent,
    M4ManualPromptRevision,
    M4MaterialRequirement,
    M4Project,
    M4Shot,
    M4ShotAssetBinding,
    M4StoryboardRevision,
    m4_utcnow,
)

VALID_FAMILIES = {"character", "scene", "prop", "voice", "reference_video"}
MAX_IMAGE_SLOTS = 6
MAX_VIDEO_SLOTS = 2
MAX_AUDIO_SLOTS = 2
DEFAULT_IMAGE_EXT = ".png"


def _canonical_key(entity_name: str, variant_name: str = "") -> str:
    base = re.sub(r"\s+", " ", (entity_name or "").strip().lower())
    var = re.sub(r"\s+", " ", (variant_name or "").strip().lower())
    return f"{base}::{var}" if var else base


def _media_kind_for(family: str, filename: str, requested_kind: str | None = None) -> str:
    if requested_kind:
        kind = requested_kind.strip().lower()
        if kind not in ALLOWED_M4_EXTENSIONS:
            raise HTTPException(422, {"code": "M4_MEDIA_KIND_INVALID", "message": f"不支持的素材类型：{kind}"})
        return kind
    suffix = PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()
    for kind, exts in ALLOWED_M4_EXTENSIONS.items():
        if suffix in exts:
            return kind
    if family == "voice":
        return "audio"
    if family == "reference_video":
        return "video"
    return "image"


def _use_as_for_asset(asset: M4AssetVersion) -> str:
    if asset.content_type.startswith("audio/") or asset.family == "voice":
        return "audio"
    if asset.content_type.startswith("video/") or asset.family == "reference_video":
        return "video"
    return "image"


def _serialize_asset(asset: M4AssetVersion) -> dict[str, Any]:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "requirement_id": asset.requirement_id,
        "family": asset.family,
        "entity_name": asset.entity_name,
        "variant_name": asset.variant_name,
        "source_kind": asset.source_kind,
        "filename": asset.filename,
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "duration_s": asset.duration_s,
        "status": asset.status,
        "is_mock": bool(asset.is_mock),
        "provenance": asset.provenance,
        "promoted_by": asset.promoted_by,
        "promoted_at": asset.promoted_at,
        "created_at": asset.created_at,
        "media_url": f"/api/v1/manual4/projects/{asset.project_id}/assets/{asset.id}/media",
    }


def _invalidate_frozen_prompts_for_shots(m4_db: Session, shot_ids: list[str], reason: str) -> list[str]:
    if not shot_ids:
        return []
    invalidated: list[str] = []
    rows = m4_db.scalars(
        select(M4ManualPromptRevision).where(
            M4ManualPromptRevision.shot_id.in_(shot_ids),
            M4ManualPromptRevision.status == "FROZEN",
        )
    ).all()
    now = m4_utcnow()
    for row in rows:
        row.status = "INVALIDATED"
        row.invalidated_reason = reason
        row.updated_at = now
        invalidated.append(row.shot_id)
    return invalidated


def _count_slot_usage(links: list[M4ShotAssetBinding]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "audio": 0}
    for link in links:
        key = link.use_as if link.use_as in counts else "image"
        counts[key] += 1
    return counts


def _enforce_slot_caps(counts: dict[str, int]) -> None:
    if counts["image"] > MAX_IMAGE_SLOTS:
        raise HTTPException(
            422,
            {
                "code": "M4_REF2VA_SLOT_LIMIT_EXCEEDED",
                "message": f"单镜图片参考槽位超过 H3 上限（{counts['image']}/{MAX_IMAGE_SLOTS}）。",
            },
        )
    if counts["video"] > MAX_VIDEO_SLOTS:
        raise HTTPException(
            422,
            {
                "code": "M4_REF2VA_SLOT_LIMIT_EXCEEDED",
                "message": f"单镜视频参考槽位超过 H3 上限（{counts['video']}/{MAX_VIDEO_SLOTS}）。",
            },
        )
    if counts["audio"] > MAX_AUDIO_SLOTS:
        raise HTTPException(
            422,
            {
                "code": "M4_REF2VA_SLOT_LIMIT_EXCEEDED",
                "message": f"单镜音频参考槽位超过 H3 上限（{counts['audio']}/{MAX_AUDIO_SLOTS}）。",
            },
        )


def _bind_asset_to_shots(
    m4_db: Session,
    project: M4Project,
    asset: M4AssetVersion,
    shot_ids: list[str],
    *,
    role_label: str,
) -> list[str]:
    if not shot_ids:
        return []
    use_as = _use_as_for_asset(asset)
    changed_shots: list[str] = []
    valid_shots = {
        s.id: s
        for s in m4_db.scalars(select(M4Shot).where(M4Shot.project_id == project.id, M4Shot.id.in_(shot_ids))).all()
    }
    for shot_id in shot_ids:
        if shot_id not in valid_shots:
            continue
        existing = m4_db.scalars(
            select(M4ShotAssetBinding)
            .where(M4ShotAssetBinding.shot_id == shot_id)
            .order_by(M4ShotAssetBinding.ordinal)
        ).all()
        if any(link.asset_version_id == asset.id for link in existing):
            continue
        counts = _count_slot_usage(existing)
        counts[use_as] = counts.get(use_as, 0) + 1
        _enforce_slot_caps(counts)
        next_ord = (max((link.ordinal for link in existing), default=-1) + 1)
        m4_db.add(
            M4ShotAssetBinding(
                project_id=project.id,
                shot_id=shot_id,
                asset_version_id=asset.id,
                use_as=use_as,
                role_label=role_label,
                ordinal=next_ord,
            )
        )
        changed_shots.append(shot_id)
    if changed_shots:
        _invalidate_frozen_prompts_for_shots(m4_db, changed_shots, "M4_REFERENCE_MANIFEST_CHANGED")
    return changed_shots


def get_materials_state(m4_db: Session, project: M4Project) -> dict[str, Any]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    assets = m4_db.scalars(
        select(M4AssetVersion)
        .where(M4AssetVersion.project_id == project.id)
        .order_by(M4AssetVersion.created_at.desc())
    ).all()
    asset_map = {a.id: _serialize_asset(a) for a in assets}
    promoted_assets = [asset_map[a.id] for a in assets if a.status == "promoted"]

    requirements_data: list[dict[str, Any]] = []
    shots_data: list[dict[str, Any]] = []
    if confirmed is not None:
        reqs = m4_db.scalars(
            select(M4MaterialRequirement)
            .where(M4MaterialRequirement.storyboard_revision_id == confirmed.id)
            .order_by(M4MaterialRequirement.family, M4MaterialRequirement.entity_name)
        ).all()
        for req in reqs:
            candidates = [
                asset_map[a.id]
                for a in assets
                if a.requirement_id == req.id
                or (a.family == req.family and _canonical_key(a.entity_name, a.variant_name) == req.canonical_key)
            ]
            reusable = [a for a in promoted_assets if a["family"] == req.family]
            requirements_data.append(
                {
                    "id": req.id,
                    "project_id": req.project_id,
                    "storyboard_revision_id": req.storyboard_revision_id,
                    "family": req.family,
                    "entity_name": req.entity_name,
                    "variant_name": req.variant_name,
                    "canonical_key": req.canonical_key,
                    "description": req.description,
                    "shot_ids": req.shot_ids,
                    "status": req.status,
                    "bound_asset_version_id": req.bound_asset_version_id,
                    "user_declined": bool(req.user_declined),
                    "bound_asset": asset_map.get(req.bound_asset_version_id) if req.bound_asset_version_id else None,
                    "candidates": candidates,
                    "reusable_assets": reusable,
                }
            )

        shots = m4_db.scalars(
            select(M4Shot)
            .where(M4Shot.storyboard_revision_id == confirmed.id)
            .order_by(M4Shot.episode_id, M4Shot.shot_no)
        ).all()
        shot_ids = [s.id for s in shots]
        links_by_shot: dict[str, list[M4ShotAssetBinding]] = {}
        if shot_ids:
            for link in m4_db.scalars(
                select(M4ShotAssetBinding)
                .where(M4ShotAssetBinding.shot_id.in_(shot_ids))
                .order_by(M4ShotAssetBinding.ordinal)
            ).all():
                links_by_shot.setdefault(link.shot_id, []).append(link)
        for sh in shots:
            sh_links = links_by_shot.get(sh.id, [])
            serialized_links = [
                {
                    "id": link.id,
                    "asset_version_id": link.asset_version_id,
                    "use_as": link.use_as,
                    "role_label": link.role_label,
                    "ordinal": link.ordinal,
                    "asset": asset_map.get(link.asset_version_id),
                }
                for link in sh_links
                if link.asset_version_id in asset_map
            ]
            shots_data.append(
                {
                    "id": sh.id,
                    "episode_id": sh.episode_id,
                    "shot_no": sh.shot_no,
                    "shot_code": sh.shot_code,
                    "revision": sh.revision,
                    "target_seconds": sh.target_seconds,
                    "summary": sh.summary,
                    "requested_assets": sh.requested_assets,
                    "bindings": serialized_links,
                    "ref2va_ready": len(serialized_links) > 0,
                }
            )

    verdict = evaluate_m4_readiness(m4_db, project)
    return {
        "confirmed_revision": {
            "id": confirmed.id,
            "revision": confirmed.revision,
        }
        if confirmed
        else None,
        "requirements": requirements_data,
        "assets": [asset_map[a.id] for a in assets],
        "shots": shots_data,
        "readiness": verdict.model_dump(),
    }


def upload_m4_asset(
    m4_db: Session,
    project: M4Project,
    m4_root: Path,
    *,
    actor: str,
    family: str,
    entity_name: str,
    variant_name: str = "",
    requirement_id: str | None = None,
    filename: str,
    content: bytes,
    kind: str | None = None,
) -> dict[str, Any]:
    fam = (family or "").strip().lower()
    if fam not in VALID_FAMILIES:
        raise HTTPException(422, {"code": "M4_ASSET_FAMILY_INVALID", "message": f"不支持的素材分类：{fam}"})
    clean_name = (entity_name or "").strip()
    if not clean_name:
        raise HTTPException(422, {"code": "M4_ASSET_ENTITY_NAME_REQUIRED", "message": "素材实体名称不能为空。"})

    req: M4MaterialRequirement | None = None
    if requirement_id:
        req = m4_db.get(M4MaterialRequirement, requirement_id)
        if req is None or req.project_id != project.id:
            raise HTTPException(404, {"code": "M4_REQUIREMENT_NOT_FOUND", "message": "目标素材需求不存在。"})

    safe_name = safe_m4_filename(filename)
    media_kind = _media_kind_for(fam, safe_name, kind)
    content_type = verify_m4_media_bytes(media_kind, safe_name, content)
    rel_path, digest, target = store_m4_bytes(m4_db_project_root := m4_root, project.id, "assets", safe_name, content)
    _ = m4_db_project_root
    info = probe_file(target, content_type=content_type)

    asset = M4AssetVersion(
        project_id=project.id,
        requirement_id=req.id if req else None,
        family=fam,
        entity_name=clean_name,
        variant_name=(variant_name or "").strip(),
        source_kind="upload",
        filename=safe_name,
        content_type=content_type,
        sha256=digest,
        byte_size=len(content),
        storage_path=rel_path,
        width=info.width,
        height=info.height,
        duration_s=info.duration_s,
        status="candidate",
        is_mock=False,
        provenance={
            "upload_filename": safe_name,
            "media_kind": media_kind,
            "probe_tool": info.tool,
            "decoded": info.decoded,
        },
    )
    m4_db.add(asset)
    m4_db.flush()

    if req is not None and not req.user_declined and req.status == "missing":
        req.status = "candidate"
        req.updated_at = m4_utcnow()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="materials",
            event_type="M4_ASSET_UPLOADED",
            entity_type="m4_asset_version",
            entity_id=asset.id,
            payload={
                "family": fam,
                "entity_name": clean_name,
                "sha256": digest,
                "requirement_id": req.id if req else None,
            },
        )
    )
    return _serialize_asset(asset)


def select_or_generate_material(
    m4_db: Session,
    platform_factory: Any,
    m4_factory: Any,
    settings: Any,
    m4_root: Path,
    project: M4Project,
    req_id: str,
    *,
    actor: str,
    mode: str = "generate",
    asset_version_id: str | None = None,
    prompt: str = "",
    width: int = DEFAULT_RESOLUTION,
    height: int = DEFAULT_RESOLUTION,
    seed: int = 1,
    steps: int = DEFAULT_STEPS,
    image_provider: Any = None,
) -> dict[str, Any]:
    req = m4_db.get(M4MaterialRequirement, req_id)
    if req is None or req.project_id != project.id:
        raise HTTPException(404, {"code": "M4_REQUIREMENT_NOT_FOUND", "message": "素材需求不存在。"})

    op_mode = (mode or "generate").strip().lower()
    if op_mode == "reuse" or asset_version_id:
        if not asset_version_id:
            raise HTTPException(422, {"code": "M4_REUSE_ASSET_REQUIRED", "message": "复用模式必须指定已有已晋升素材。"})
        asset = m4_db.get(M4AssetVersion, asset_version_id)
        if asset is None or asset.project_id != project.id:
            raise HTTPException(404, {"code": "M4_ASSET_NOT_FOUND", "message": "要复用的素材不存在。"})
        if asset.status != "promoted":
            raise HTTPException(
                409,
                {"code": "M4_REUSE_REQUIRES_PROMOTED", "message": "仅允许复用已晋升为主素材的版本。"},
            )

        old_bound = req.bound_asset_version_id
        req.bound_asset_version_id = asset.id
        req.status = "bound"
        req.user_declined = False
        req.updated_at = m4_utcnow()

        if old_bound and old_bound != asset.id and req.shot_ids:
            for link in m4_db.scalars(
                select(M4ShotAssetBinding).where(
                    M4ShotAssetBinding.shot_id.in_(req.shot_ids),
                    M4ShotAssetBinding.asset_version_id == old_bound,
                )
            ).all():
                m4_db.delete(link)
            m4_db.flush()

        role_label = f"{req.entity_name}" + (f"（{req.variant_name}）" if req.variant_name else "")
        _bind_asset_to_shots(m4_db, project, asset, list(req.shot_ids or []), role_label=role_label)
        m4_db.add(
            M4AuditEvent(
                project_id=project.id,
                actor=actor,
                stage="materials",
                event_type="M4_MATERIAL_REUSED",
                entity_type="m4_material_requirement",
                entity_id=req.id,
                payload={"asset_version_id": asset.id, "sha256": asset.sha256},
            )
        )
        return {"mode": "reuse", "requirement_id": req.id, "asset": _serialize_asset(asset)}

    if op_mode != "generate":
        raise HTTPException(422, {"code": "M4_MATERIAL_MODE_INVALID", "message": f"不支持的素材操作模式：{op_mode}"})
    if req.family in {"voice", "reference_video"}:
        raise HTTPException(
            422,
            {
                "code": "M4_GENERATE_ONLY_SUPPORTS_IMAGE",
                "message": "音频/视频参考素材暂不支持 Qwen 图像生成，请直接上传或复用。",
            },
        )

    effective_prompt = (prompt or "").strip() or (
        f"High quality reference portrait and clean neutral framing of {req.family} '{req.entity_name}'"
        + (f", variant {req.variant_name}" if req.variant_name else "")
        + (f", {req.description}" if req.description else "")
    )
    is_mock = False
    provenance: dict[str, Any] = {
        "model_family": MODEL_FAMILY,
        "prompt": effective_prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "steps": steps,
    }
    filename = f"{req.family}_{req.id[:8]}_s{seed}{DEFAULT_IMAGE_EXT}"

    if image_provider is not None:
        raw_out = image_provider(
            prompt=effective_prompt,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            requirement=req,
        )
        content_bytes = raw_out["bytes"] if isinstance(raw_out, dict) else bytes(raw_out)
        is_mock = bool(raw_out.get("is_mock", True)) if isinstance(raw_out, dict) else True
        if isinstance(raw_out, dict) and raw_out.get("filename"):
            filename = safe_m4_filename(str(raw_out["filename"]))
        provenance["provider"] = (
            str(raw_out.get("provider", "test-provider")) if isinstance(raw_out, dict) else "test-provider"
        )
    else:
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
        graph = build_qwen_t2i_graph(
            prompt=effective_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=DEFAULT_CFG,
        )
        validate_graph(graph, required_nodes=REQUIRED_NODES)
        g_hash = graph_hash(graph)
        with managed_m4_comfyui(
            platform_factory,
            m4_factory,
            settings,
            m4_root,
            owner=f"m4-t2i-{project.id}",
        ) as (client, _spec):
            obj_info = client.object_info()
            v_res = validate_against_object_info(graph, {}, obj_info)
            if v_res["missing_nodes"] or v_res["missing_inputs"]:
                raise HTTPException(
                    422,
                    {"code": "M4_T2I_NODES_UNAVAILABLE", "message": f"Qwen T2I 节点不满足：{v_res}"},
                )
            queued = client.queue_prompt(graph, extra_data={"shine_line": "manual4", "project_id": project.id})
            prompt_id = str(queued.get("prompt_id") or "")
            if not prompt_id:
                raise HTTPException(502, {"code": "M4_COMFY_NO_PROMPT_ID", "message": "ComfyUI 未返回 prompt_id。"})
            history = client.poll_until_done(prompt_id, timeout_s=300.0)
            outputs = (history.get("outputs") or {}).get(OUTPUT_NODE) or {}
            images = outputs.get("images") or []
            if not images:
                raise HTTPException(502, {"code": "M4_T2I_NO_IMAGE_OUTPUT", "message": "Qwen T2I 未输出图片文件。"})
            img_ref = images[0]
            out_name = safe_m4_filename(str(img_ref.get("filename") or filename))
            filename = out_name
            content_bytes = client.view(
                out_name,
                subfolder=str(img_ref.get("subfolder") or ""),
                file_type=str(img_ref.get("type") or "output"),
            )
            provenance["provider"] = "comfyui-qwen-t2i"
            provenance["prompt_id"] = prompt_id
            provenance["graph_sha256"] = g_hash

    content_type = verify_m4_media_bytes("image", filename, content_bytes)
    rel_path, digest, target = store_m4_bytes(m4_root, project.id, "assets", filename, content_bytes)
    info = probe_file(target, content_type=content_type)

    asset = M4AssetVersion(
        project_id=project.id,
        requirement_id=req.id,
        family=req.family,
        entity_name=req.entity_name,
        variant_name=req.variant_name,
        source_kind="mock_t2i" if is_mock else "qwen_t2i",
        filename=filename,
        content_type=content_type,
        sha256=digest,
        byte_size=len(content_bytes),
        storage_path=rel_path,
        width=info.width,
        height=info.height,
        duration_s=info.duration_s,
        status="candidate",
        is_mock=is_mock,
        provenance=provenance,
    )
    m4_db.add(asset)
    m4_db.flush()

    if not req.user_declined and req.status == "missing":
        req.status = "candidate"
        req.updated_at = m4_utcnow()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="materials",
            event_type="M4_ASSET_GENERATED",
            entity_type="m4_asset_version",
            entity_id=asset.id,
            payload={"requirement_id": req.id, "sha256": digest, "is_mock": is_mock},
        )
    )
    return {"mode": "generate", "requirement_id": req.id, "asset": _serialize_asset(asset)}


def promote_m4_asset(
    m4_db: Session,
    project: M4Project,
    asset_id: str,
    *,
    actor: str,
    requirement_id: str | None = None,
) -> dict[str, Any]:
    asset = m4_db.get(M4AssetVersion, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(404, {"code": "M4_ASSET_NOT_FOUND", "message": "待晋升素材不存在。"})

    asset.status = "promoted"
    asset.promoted_by = actor
    asset.promoted_at = m4_utcnow()

    target_req_id = requirement_id or asset.requirement_id
    bound_req: M4MaterialRequirement | None = None
    bound_shots: list[str] = []
    if target_req_id:
        req = m4_db.get(M4MaterialRequirement, target_req_id)
        confirmed = m4_db.scalar(
            select(M4StoryboardRevision)
            .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
            .order_by(M4StoryboardRevision.revision.desc())
            .limit(1)
        )
        if req is not None and req.project_id == project.id:
            old_bound = req.bound_asset_version_id
            req.bound_asset_version_id = asset.id
            req.status = "bound"
            req.user_declined = False
            req.updated_at = m4_utcnow()
            bound_req = req
            if confirmed is not None and req.storyboard_revision_id == confirmed.id:
                if old_bound and old_bound != asset.id and req.shot_ids:
                    for link in m4_db.scalars(
                        select(M4ShotAssetBinding).where(
                            M4ShotAssetBinding.shot_id.in_(req.shot_ids),
                            M4ShotAssetBinding.asset_version_id == old_bound,
                        )
                    ).all():
                        m4_db.delete(link)
                    m4_db.flush()
                role_label = f"{req.entity_name}" + (f"（{req.variant_name}）" if req.variant_name else "")
                bound_shots = _bind_asset_to_shots(
                    m4_db, project, asset, list(req.shot_ids or []), role_label=role_label
                )

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="materials",
            event_type="M4_ASSET_PROMOTED",
            entity_type="m4_asset_version",
            entity_id=asset.id,
            payload={
                "requirement_id": bound_req.id if bound_req else None,
                "bound_shots": bound_shots,
                "sha256": asset.sha256,
            },
        )
    )
    return {
        "asset": _serialize_asset(asset),
        "requirement_id": bound_req.id if bound_req else None,
        "bound_shots": bound_shots,
    }


def decline_m4_requirement(
    m4_db: Session,
    project: M4Project,
    req_id: str,
    *,
    actor: str,
    reason: str = "",
) -> dict[str, Any]:
    req = m4_db.get(M4MaterialRequirement, req_id)
    if req is None or req.project_id != project.id:
        raise HTTPException(404, {"code": "M4_REQUIREMENT_NOT_FOUND", "message": "素材需求不存在。"})

    old_bound = req.bound_asset_version_id
    removed_shots: list[str] = []
    if old_bound and req.shot_ids:
        for link in m4_db.scalars(
            select(M4ShotAssetBinding).where(
                M4ShotAssetBinding.shot_id.in_(req.shot_ids),
                M4ShotAssetBinding.asset_version_id == old_bound,
            )
        ).all():
            removed_shots.append(link.shot_id)
            m4_db.delete(link)
        m4_db.flush()
        if removed_shots:
            _invalidate_frozen_prompts_for_shots(m4_db, removed_shots, "M4_REFERENCE_MANIFEST_CHANGED")

    req.bound_asset_version_id = None
    req.user_declined = True
    req.status = "declined"
    req.updated_at = m4_utcnow()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="materials",
            event_type="M4_MATERIAL_DECLINED",
            entity_type="m4_material_requirement",
            entity_id=req.id,
            payload={"reason": reason, "removed_shots": removed_shots},
        )
    )
    return {"requirement_id": req.id, "status": req.status, "user_declined": True}


def put_shot_bindings(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    shot = m4_db.get(M4Shot, shot_id)
    if (
        shot is None
        or shot.project_id != project.id
        or confirmed is None
        or shot.storyboard_revision_id != confirmed.id
    ):
        raise HTTPException(404, {"code": "M4_SHOT_NOT_FOUND", "message": "当前已确认分镜中未找到该镜头。"})

    seen_assets: set[str] = set()
    normalized: list[tuple[M4AssetVersion, str, str]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    for item in bindings:
        asset_id = str(item.get("asset_version_id") or "").strip()
        if not asset_id:
            raise HTTPException(422, {"code": "M4_BINDING_ASSET_REQUIRED", "message": "绑定项缺少 asset_version_id。"})
        if asset_id in seen_assets:
            raise HTTPException(422, {"code": "M4_DUPLICATE_SHOT_BINDING", "message": "同一镜头不可重复绑定同一素材。"})
        seen_assets.add(asset_id)

        asset = m4_db.get(M4AssetVersion, asset_id)
        if asset is None or asset.project_id != project.id:
            raise HTTPException(404, {"code": "M4_ASSET_NOT_FOUND", "message": f"素材不存在：{asset_id}"})
        if asset.status != "promoted":
            raise HTTPException(
                409,
                {
                    "code": "M4_BINDING_REQUIRES_PROMOTED_ASSET",
                    "message": f"素材「{asset.entity_name}」尚未晋升为主素材，不能绑定到镜头。",
                },
            )
        use_as = str(item.get("use_as") or "").strip().lower() or _use_as_for_asset(asset)
        if use_as not in counts:
            raise HTTPException(422, {"code": "M4_BINDING_USE_AS_INVALID", "message": f"不支持的参考角色：{use_as}"})
        counts[use_as] += 1
        role_label = str(item.get("role_label") or asset.entity_name).strip()
        normalized.append((asset, use_as, role_label))

    _enforce_slot_caps(counts)

    prior_links = m4_db.scalars(
        select(M4ShotAssetBinding)
        .where(M4ShotAssetBinding.shot_id == shot.id)
        .order_by(M4ShotAssetBinding.ordinal)
    ).all()
    prior_sig = [(link.asset_version_id, link.use_as, link.role_label) for link in prior_links]
    next_sig = [(a.id, u, r) for a, u, r in normalized]

    for link in prior_links:
        m4_db.delete(link)
    m4_db.flush()

    created: list[dict[str, Any]] = []
    for idx, (asset, use_as, role_label) in enumerate(normalized):
        row = M4ShotAssetBinding(
            project_id=project.id,
            shot_id=shot.id,
            asset_version_id=asset.id,
            use_as=use_as,
            role_label=role_label,
            ordinal=idx,
        )
        m4_db.add(row)
        created.append(
            {
                "asset_version_id": asset.id,
                "use_as": use_as,
                "role_label": role_label,
                "ordinal": idx,
            }
        )

    if prior_sig != next_sig:
        _invalidate_frozen_prompts_for_shots(m4_db, [shot.id], "M4_REFERENCE_MANIFEST_CHANGED")

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="materials",
            event_type="M4_SHOT_BINDINGS_UPDATED",
            entity_type="m4_shot",
            entity_id=shot.id,
            payload={"shot_code": shot.shot_code, "count": len(created)},
        )
    )
    return {"shot_id": shot.id, "shot_code": shot.shot_code, "bindings": created}


def resolve_m4_asset_file(m4_db: Session, m4_root: Path, project: M4Project, asset_id: str) -> tuple[Path, str]:
    asset = m4_db.get(M4AssetVersion, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(404, {"code": "M4_ASSET_NOT_FOUND", "message": "素材不存在。"})
    path = safe_m4_path(m4_root, asset.storage_path)
    if not path.is_file():
        raise HTTPException(404, {"code": "M4_ASSET_FILE_MISSING", "message": "素材文件缺失。"})
    return path, asset.content_type

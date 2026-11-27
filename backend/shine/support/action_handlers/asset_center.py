"""Domain 4: Asset center image jobs, character sheets, promotions, project assignments, and Style LoRA management."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from shine.models import utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionHandler,
    ActionInvalid,
    blocked_from_executor,
    register_handler,
)


def _image_job_readback(db, settings, row) -> None:
    if row.status != "DELEGATED" or not row.original_task_id:
        return
    from shine.asset_center.models import LibraryRenderTask

    task_ids = [item for item in str(row.original_task_id).split(",") if item]
    tasks = [db.get(LibraryRenderTask, tid) for tid in task_ids]
    if not tasks or any(t is None for t in tasks):
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到素材中心生图任务记录，无法确认结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    statuses = [str(t.status or "") for t in tasks if t is not None]
    row.verification = {**(row.verification or {}), "task_statuses": statuses, "checked_at": utcnow().isoformat()}
    if all(s == "SUCCEEDED" for s in statuses):
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
    elif any(s in {"FAILED", "CANCELLED"} for s in statuses):
        row.status = "FAILED"
        row.last_error = f"生图任务结束，状态：{', '.join(statuses)}"
        row.verification["outcome"] = "FAILED"
    elif any(s == "NEEDS_REVIEW" for s in statuses):
        row.status = "NEEDS_REVIEW"
        row.last_error = "生图任务存在未知结果，不能自动重提。"
        row.verification["outcome"] = "UNKNOWN"
    db.commit()


# ---------------------------------------------------------------------------
# asset_center_image_generate & asset_center_character_sheet_generate
# ---------------------------------------------------------------------------


def _ac_image_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    return {
        "project_id": project_id or params.get("project_id"),
        "mode": params.get("mode") or ("I2I" if params.get("character_sheet") else "T2I"),
        "category": params.get("category") or "other",
        "prompt": str(params.get("prompt") or params.get("character_sheet_note") or "")[:200],
        "references": params.get("references") or [],
        "target_asset_id": params.get("target_asset_id"),
    }, None


def _ac_image_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    is_sheet = str(common.get("kind")) == "asset_center_character_sheet_generate" or bool(params.get("character_sheet"))
    return {
        **common,
        "summary": (
            "提交 16:9 五视图定妆照生图任务到素材中心队列（自动绑定多视角 LoRA）。"
            if is_sheet
            else f"提交素材中心生图任务（{params.get('mode')}，分类 {params.get('category', 'other')}）。"
        ),
        "mode": "I2I" if is_sheet else params.get("mode"),
        "category": params.get("category") or ("character" if is_sheet else "other"),
        "candidate_count": int(params.get("candidate_count") or 1),
        "will_start_gpu": True,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过既有版权/LoRA/提示词合成门禁；生成的版本仍需人工审看再晋升。",
    }


def _ac_image_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import queue_asset_center_character_sheet, queue_asset_center_image_jobs

    try:
        if row.kind == "asset_center_character_sheet_generate":
            res = queue_asset_center_character_sheet(
                db, settings,
                idempotency_key=f"copilot-sheet:{row.id}",
                references=list(params.get("references") or []),
                character_sheet_note=str(params.get("character_sheet_note") or ""),
                category=str(params.get("category") or "character"),
                project_id=row.project_id or params.get("project_id"),
                title=str(params.get("title") or ""),
                seed=params.get("seed"),
                actor=actor,
            )
        else:
            res = queue_asset_center_image_jobs(
                db, settings,
                idempotency_key=f"copilot-acimg:{row.id}",
                mode=str(params.get("mode") or "T2I"),
                prompt=str(params.get("prompt") or ""),
                negative_prompt=str(params.get("negative_prompt") or ""),
                category=str(params.get("category") or "other"),
                project_id=row.project_id or params.get("project_id"),
                title=str(params.get("title") or ""),
                character_sheet=bool(params.get("character_sheet")),
                character_sheet_note=str(params.get("character_sheet_note") or ""),
                style_preset=params.get("style_preset"),
                style_loras=params.get("style_loras"),
                candidate_count=int(params.get("candidate_count") or 1),
                seed=params.get("seed"),
                references=params.get("references"),
                target_asset_id=params.get("target_asset_id"),
                actor=actor,
            )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    items = res.get("items") or []
    task_ids = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
    row.original_task_id = ",".join(task_ids) if task_ids else None
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/asset-center/image-jobs/{task_id}",
        "task_ids": task_ids,
        "notice": res.get("notice"),
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# asset_center_version_promote
# ---------------------------------------------------------------------------


def _ac_promote_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.asset_center.models import LibraryVersion

    row = db.get(LibraryVersion, str(params.get("version_id") or ""))
    if row is None:
        return {}, "LIBRARY_VERSION_NOT_FOUND"
    return {"version_id": row.id, "status": row.status, "sha256": row.content_sha256, "is_mock": bool(row.is_mock)}, None


def _ac_promote_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.asset_center.models import LibraryVersion

    row = db.get(LibraryVersion, str(params.get("version_id") or ""))
    if row is None:
        raise ActionInvalid("素材版本不存在。")
    blocked = row.status != "READY_FOR_REVIEW" or bool(row.is_mock)
    return {
        **common,
        "summary": f"晋升素材中心版本「{row.id}」为已选用版本。",
        "version_id": row.id,
        "version_status": row.status,
        "blocked": bool(common["occupancy"]["busy"] or blocked),
        "blocked_code": common["occupancy"]["code"] if common["occupancy"]["busy"] else ("LIBRARY_VERSION_NOT_REVIEWABLE" if blocked else None),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过单次签发、非模拟校验、版权核验与字节哈希验证。",
    }


def _ac_promote_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import promote_asset_center_version

    vid = str(params.get("version_id") or "")
    try:
        res = promote_asset_center_version(
            db, settings, version_id=vid, evidence=str(params.get("evidence") or "copilot_confirmed"), actor=actor
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = vid
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/asset-center/assets",
        "version_id": vid,
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# asset_center_assign_to_project
# ---------------------------------------------------------------------------


def _ac_assign_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.asset_center.models import LibraryVersion
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    if db.get(Project, pid) is None:
        return {}, "PROJECT_NOT_FOUND"
    ver = db.get(LibraryVersion, str(params.get("library_version_id") or ""))
    if ver is None:
        return {}, "LIBRARY_VERSION_NOT_FOUND"
    return {
        "project_id": pid,
        "library_version_id": ver.id,
        "role": params.get("role"),
        "entity_id": params.get("entity_id"),
    }, None


def _ac_assign_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"将素材版本「{params.get('library_version_id')}」分配到项目（角色 {params.get('role')}）。",
        "library_version_id": params.get("library_version_id"),
        "role": params.get("role"),
        "entity_id": params.get("entity_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "复用既有素材中心分配与版权登记逻辑。",
    }


def _ac_assign_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import assign_asset_center_version

    pid = str(row.project_id or "")
    try:
        res = assign_asset_center_version(
            db, settings,
            project_id=pid,
            library_version_id=str(params.get("library_version_id") or ""),
            role=str(params.get("role") or "generic"),
            idempotency_key=f"copilot-assign:{row.id}",
            entity_id=params.get("entity_id"),
            usage_scope=str(params.get("usage_scope") or "local"),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("id") or "") or None
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/asset-center/assignments",
        "assignment": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# lora_import_local, lora_update, lora_delete
# ---------------------------------------------------------------------------


def _lora_import_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    return {
        "local_path": str(params.get("local_path") or "").strip(),
        "base_model": params.get("base_model") or "minimax_h3",
    }, None


def _lora_import_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"从本地导入风格 LoRA：{params.get('local_path')}（基底 {params.get('base_model', 'minimax_h3')}）。",
        "local_path": params.get("local_path"),
        "base_model": params.get("base_model") or "minimax_h3",
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过既有 .safetensors 格式、哈希去重与触发词校验；写入受管 shine_user LoRA 目录。",
    }


def _lora_import_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.comfyops.loras import H3_LORA_BASE, StyleLoraError, import_style_lora

    try:
        asset, created = import_style_lora(
            db, settings,
            local_path=str(params.get("local_path") or ""),
            display_name=params.get("display_name"),
            default_strength=float(params.get("default_strength", 0.65)),
            trigger_word=str(params.get("trigger_word") or ""),
            base_model=str(params.get("base_model") or H3_LORA_BASE),
            negative_trigger_word=str(params.get("negative_trigger_word") or ""),
            trigger_position=str(params.get("trigger_position") or "suffix"),
            actor=actor,
        )
    except StyleLoraError as exc:
        raise ActionBlocked(exc.code, exc.message, details=exc.details) from exc
    row.original_task_id = asset.id
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/loras",
        "lora_id": asset.id,
        "created": created,
        "base_model": asset.base_model,
        "checked_at": utcnow().isoformat(),
    }


def _lora_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.loras import StyleLoraAsset

    asset = db.get(StyleLoraAsset, str(params.get("lora_id") or ""))
    if asset is None:
        return {}, "LORA_FILE_MISSING"
    return {
        "lora_id": asset.id,
        "display_name": asset.display_name,
        "default_strength": asset.default_strength,
        "trigger_word": asset.trigger_word,
        "base_model": asset.base_model,
    }, None


def _lora_update_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"更新风格 LoRA「{params.get('lora_id')}」的展示名、默认权重或触发词。",
        "lora_id": params.get("lora_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过触发词与强度上下限校验，不改动磁盘权重文件。",
    }


def _lora_update_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.comfyops.loras import StyleLoraError, update_style_lora

    lid = str(params.get("lora_id") or "")
    try:
        res = update_style_lora(
            db, settings, lid,
            display_name=params.get("display_name"),
            default_strength=params.get("default_strength"),
            trigger_word=params.get("trigger_word"),
            negative_trigger_word=params.get("negative_trigger_word"),
            trigger_position=params.get("trigger_position"),
            actor=actor,
        )
    except StyleLoraError as exc:
        raise ActionBlocked(exc.code, exc.message, details=exc.details) from exc
    row.original_task_id = lid
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/loras",
        "lora": res,
        "checked_at": utcnow().isoformat(),
    }


def _lora_delete_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"删除未在项目冻结配方或生产队列中使用的风格 LoRA「{params.get('lora_id')}」。",
        "lora_id": params.get("lora_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过 _ensure_lora_not_in_use 引用检查，被项目绑定的 LoRA 会直接拒绝删除。",
    }


def _lora_delete_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.comfyops.loras import StyleLoraError, delete_style_lora

    lid = str(params.get("lora_id") or "")
    try:
        res = delete_style_lora(db, settings, lid, actor=actor)
    except StyleLoraError as exc:
        raise ActionBlocked(exc.code, exc.message, details=exc.details) from exc
    row.original_task_id = lid
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/loras",
        "deleted": res,
        "checked_at": utcnow().isoformat(),
    }


register_handler("asset_center_image_generate", ActionHandler(target_facts=_ac_image_target_facts, preflight=_ac_image_preflight, execute=_ac_image_execute, readback=_image_job_readback))
register_handler("asset_center_character_sheet_generate", ActionHandler(target_facts=_ac_image_target_facts, preflight=_ac_image_preflight, execute=_ac_image_execute, readback=_image_job_readback))
register_handler("asset_center_version_promote", ActionHandler(target_facts=_ac_promote_target_facts, preflight=_ac_promote_preflight, execute=_ac_promote_execute))
register_handler("asset_center_assign_to_project", ActionHandler(target_facts=_ac_assign_target_facts, preflight=_ac_assign_preflight, execute=_ac_assign_execute))
register_handler("lora_import_local", ActionHandler(target_facts=_lora_import_target_facts, preflight=_lora_import_preflight, execute=_lora_import_execute))
register_handler("lora_update", ActionHandler(target_facts=_lora_target_facts, preflight=_lora_update_preflight, execute=_lora_update_execute))
register_handler("lora_delete", ActionHandler(target_facts=_lora_target_facts, preflight=_lora_delete_preflight, execute=_lora_delete_execute))

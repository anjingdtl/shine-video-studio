"""Domain 3: Project material derivation, preferences, generation, promotion, and D12 shot asset swap."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.models import utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionHandler,
    ActionInvalid,
    blocked_from_executor,
    json_sha256,
    register_handler,
)


def _material_run_readback(db, settings, row) -> None:
    if row.status != "DELEGATED" or not row.original_task_id:
        return
    from shine.materialprep.models import MaterialRun

    run = db.get(MaterialRun, str(row.original_task_id))
    if run is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到素材生成批次记录，无法确认结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    status = str(run.status or "")
    row.verification = {**(row.verification or {}), "run_id": run.id, "run_status": status, "checked_at": utcnow().isoformat()}
    if status == "COMPLETED":
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
    elif status in {"FAILED", "CANCELLED"}:
        row.status = "FAILED"
        row.last_error = f"素材生成批次状态为 {status}。"
        row.verification["outcome"] = "FAILED"
    elif status == "NEEDS_REVIEW":
        row.status = "NEEDS_REVIEW"
        row.last_error = "素材生成存在待核对项，不能自动重试。"
        row.verification["outcome"] = "UNKNOWN"
    else:
        row.verification["outcome"] = "PENDING"
    db.commit()


# ---------------------------------------------------------------------------
# material_derive
# ---------------------------------------------------------------------------


def _derive_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.models import DirectorEpisodePlan
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    if db.get(Project, pid) is None:
        return {}, "PROJECT_NOT_FOUND"
    latest_plan = db.scalar(
        select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == pid).order_by(DirectorEpisodePlan.version.desc()).limit(1)
    )
    return {
        "project_id": pid,
        "episode_ids": sorted(params.get("episode_ids") or []),
        "latest_plan_version": latest_plan.version if latest_plan else None,
        "latest_plan_status": latest_plan.status if latest_plan else None,
    }, None


def _derive_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": "从已确认镜头推导本项目素材需求清单，不覆盖已绑定或已锁定主资产。",
        "episode_ids": list(params.get("episode_ids") or []),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后调用 derive_shot_materials 更新项目素材清单。",
    }


def _derive_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.streamline import derive_shot_materials

    pid = str(row.project_id or "")
    try:
        res = derive_shot_materials(db, pid, episode_ids=params.get("episode_ids") or None, actor=actor)
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("plan_revision_id") or "") or None
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/material-preparation",
        "plan_revision_id": row.original_task_id,
        "requirement_count": len(res.get("requirements") or []),
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# material_preferences_update
# ---------------------------------------------------------------------------


def _prefs_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.materialprep.service import get_material_preferences
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    if db.get(Project, pid) is None:
        return {}, "PROJECT_NOT_FOUND"
    current = get_material_preferences(db, pid, settings=settings)
    return {
        "project_id": pid,
        "current_character_sheet_mode": bool(current.get("character_sheet_mode")),
        "requested_character_sheet_mode": bool(params.get("character_sheet_mode")),
    }, None


def _prefs_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.materialprep.service import get_material_preferences

    pid = str(params.get("project_id") or "")
    current = get_material_preferences(db, pid, settings=settings)
    next_mode = bool(params.get("character_sheet_mode"))
    return {
        **common,
        "summary": f"切换项目素材生成模式：character_sheet_mode -> {'开启（16:9 五视图定妆照）' if next_mode else '关闭（标准角色生成）'}。",
        "parameter_diff": [
            {"key": "character_sheet_mode", "before": bool(current.get("character_sheet_mode")), "after": next_mode}
        ],
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "只修改当前项目素材偏好配置；开启五视图仍受本地多角度 LoRA 门禁约束。",
    }


def _prefs_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.materialprep.service import set_material_preferences

    pid = str(row.project_id or "")
    try:
        res = set_material_preferences(
            db, pid, character_sheet_mode=bool(params.get("character_sheet_mode")), actor=actor, settings=settings
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/material-preparation",
        "preferences": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# material_generate
# ---------------------------------------------------------------------------


def _generate_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.materialprep.service import plan_progress
    from shine.media.output_layout import output_root
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    if db.get(Project, pid) is None:
        return {}, "PROJECT_NOT_FOUND"
    state = plan_progress(db, pid, data_dir=output_root(db, settings), settings=settings)
    conf = state.get("confirmation") or {}
    return {
        "project_id": pid,
        "plan_revision_id": state.get("plan_revision_id"),
        "expected_snapshot": state.get("expected_snapshot"),
        "confirmation_id": conf.get("confirmation_id"),
        "confirmation_hash": conf.get("confirmation_hash"),
        "requirement_ids": sorted(params.get("requirement_ids") or []),
        "character_sheet_mode": params.get("character_sheet_mode"),
    }, None


def _generate_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.materialprep.service import plan_progress
    from shine.media.output_layout import output_root

    pid = str(params.get("project_id") or "")
    state = plan_progress(db, pid, data_dir=output_root(db, settings), settings=settings)
    conf = state.get("confirmation") or {}
    if not conf.get("confirmation_id"):
        if not state.get("plan_revision_id"):
            raise ActionBlocked("MATERIAL_PLAN_NOT_DERIVED", "请先从已确认的分镜推导素材清单。")
        _material_commit_body(state, params)
    return {
        **common,
        "summary": f"为选中的 {len(params.get('requirement_ids') or [])} 项素材需求排队生图批次。",
        "requirement_ids": list(params.get("requirement_ids") or []),
        "style_preset": params.get("style_preset"),
        "character_sheet_mode": bool(params.get("character_sheet_mode")),
        "will_start_gpu": True,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后提交到统一素材队列并占用 GPU 依次生图，只产出 CandidateAsset 候选，不自动覆盖主资产。",
    }


def _generate_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.materialprep.service import plan_progress
    from shine.media.output_layout import output_root

    pid = str(row.project_id or "")
    state = plan_progress(db, pid, data_dir=output_root(db, settings), settings=settings)
    conf = state.get("confirmation") or {}
    cid = str(params.get("confirmation_id") or conf.get("confirmation_id") or "")
    chash = str(params.get("confirmation_hash") or conf.get("confirmation_hash") or "")
    if not conf.get("confirmation_id"):
        from shine.api_streamline import commit_director_materials

        try:
            committed = commit_director_materials(pid, _material_commit_body(state, params), actor, db, settings)
        except HTTPException as exc:
            raise blocked_from_executor(exc) from exc
        res = committed.get("batch")
        if not res:
            raise ActionBlocked("MATERIAL_NOTHING_TO_GENERATE", "所选素材已进入原生成队列，请先回读进度。")
    else:
        res = _queue_confirmed_batch(db, settings, row, params, cid, chash, state)
    row.original_task_id = str(res.get("run_id") or "") or None
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/material-preparation/generation-progress",
        "run_id": row.original_task_id,
        "run_status": res.get("status"),
        "item_count": len(res.get("items") or []),
        "checked_at": utcnow().isoformat(),
    }


def _material_commit_body(state, params):
    from shine.api_streamline import MaterialCommitBody, MaterialDecisionBody

    if any(params.get(key) for key in ("confirmation_id", "confirmation_hash", "rights_id", "seeds")):
        raise ActionBlocked("MATERIAL_PLAN_NOT_CONFIRMED", "清单尚未确认，不能使用指定的确认或种子参数。")
    selected = list(dict.fromkeys(params.get("requirement_ids") or []))
    known = {item["requirement_id"]: item for item in state.get("requirements") or []}
    if not selected or any(requirement_id not in known for requirement_id in selected):
        raise ActionBlocked("MATERIAL_REQUIREMENT_NOT_FOUND", "所选需求需来自当前素材清单。")
    sheet = params.get("character_sheet_mode")
    if sheet is None:
        sheet = (state.get("preferences") or {}).get("character_sheet_mode")
    return MaterialCommitBody(
        plan_revision_id=state["plan_revision_id"], expected_snapshot=state["expected_snapshot"],
        decisions=[MaterialDecisionBody(requirement_id=requirement_id, strategy="generate",
                                        entity_id=known[requirement_id]["entity_id"]) for requirement_id in selected],
        character_sheet_mode=bool(sheet),
        style_preset=params.get("style_preset"), style_loras=params.get("style_loras") or [],
    )


def _queue_confirmed_batch(db, settings, row, params, cid, chash, state):
    from shine.comfyops.loras import StyleLoraError, freeze_image_lora_style
    from shine.materialprep.service import create_batch

    image_style: dict[str, Any] = {}
    if params.get("style_preset") or params.get("style_loras"):
        try:
            image_style = freeze_image_lora_style(db, settings, params.get("style_loras") or [], params.get("style_preset"))
        except StyleLoraError as exc:
            raise ActionBlocked(exc.code, exc.message) from exc
    csm = params.get("character_sheet_mode")
    if csm is None:
        csm = bool((state.get("preferences") or {}).get("character_sheet_mode"))
    try:
        return create_batch(
            db, row.project_id,
            confirmation_id=cid,
            confirmation_hash=chash,
            requirement_ids=list(params.get("requirement_ids") or []),
            idempotency_key=f"copilot-matgen:{row.id}",
            rights_id=params.get("rights_id"),
            seeds=params.get("seeds"),
            image_style=image_style or None,
            character_sheet_mode=bool(csm),
            settings=settings,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc


# ---------------------------------------------------------------------------
# material_regenerate
# ---------------------------------------------------------------------------


def _regen_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.models import AssetRequirement

    pid = str(project_id or params.get("project_id") or "")
    req = db.get(AssetRequirement, str(params.get("requirement_id") or ""))
    if req is None or req.project_id != pid:
        return {}, "MATERIAL_REQUIREMENT_NOT_FOUND"
    return {"project_id": pid, "requirement_id": req.id, "sha256": req.sha256, "status": req.status}, None


def _regen_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfyops.models import AssetRequirement

    req = db.get(AssetRequirement, str(params.get("requirement_id") or ""))
    if req is None:
        raise ActionInvalid("找不到对应的素材需求。")
    return {
        **common,
        "summary": f"为素材需求「{(req.payload or {}).get('entity_id') or req.id}」排队新版本生图，保留旧候选图以供对比。",
        "requirement_id": req.id,
        "seed": params.get("seed", 0),
        "character_sheet_mode": params.get("character_sheet_mode"),
        "will_start_gpu": True,
        "will_modify_user_comfyui": False,
        "authorization_summary": "调用既有 regenerate_material_requirement；产出的仍是 CandidateAsset 候选，不直接晋升为主资产。",
    }


def _regen_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.materialprep.service import regenerate_material_requirement

    pid = str(row.project_id or "")
    try:
        res = regenerate_material_requirement(
            db, settings,
            project_id=pid,
            requirement_id=str(params.get("requirement_id") or ""),
            idempotency_key=f"copilot-matregen:{row.id}",
            actor=actor,
            seed=int(params.get("seed") or 0),
            style_preset=params.get("style_preset"),
            style_loras=params.get("style_loras"),
            character_sheet_mode=params.get("character_sheet_mode"),
            rights_id=params.get("rights_id"),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("run_id") or "") or None
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/material-preparation",
        "run_id": row.original_task_id,
        "run_status": res.get("status"),
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# material_promote
# ---------------------------------------------------------------------------


def _promote_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.models import CandidateAsset

    pid = str(project_id or params.get("project_id") or "")
    cand = db.get(CandidateAsset, str(params.get("candidate_id") or ""))
    if cand is None or cand.project_id != pid:
        return {}, "CANDIDATE_NOT_FOUND"
    return {"project_id": pid, "candidate_id": cand.id, "status": cand.status, "sha256": cand.sha256}, None


def _promote_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfyops.models import CandidateAsset

    cand = db.get(CandidateAsset, str(params.get("candidate_id") or ""))
    if cand is None:
        raise ActionInvalid("找不到对应的候选素材。")
    not_ready = cand.status != "READY_FOR_REVIEW"
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": f"将已过技术质检的候选素材「{cand.id}」晋升并锁定为项目主资产。",
        "candidate_id": cand.id,
        "candidate_status": cand.status,
        "offline": bool(params.get("offline")),
        "blocked": bool(occupancy["busy"] or not_ready),
        "blocked_code": occupancy["code"] if occupancy["busy"] else ("CANDIDATE_NOT_APPROVED" if not_ready else None),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过单次确认签发后调用原 promote 执行者，完整校验哈希、版权与非模拟标志。",
    }


def _promote_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.materialprep.service import promote_material_candidate

    pid = str(row.project_id or "")
    cid = str(params.get("candidate_id") or "")
    try:
        res = promote_material_candidate(
            db,
            settings,
            project_id=pid,
            candidate_id=cid,
            actor=actor,
            offline=bool(params.get("offline")),
            evidence=str(params.get("evidence") or "copilot_confirmed"),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = cid
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/assets",
        "candidate_id": cid,
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# shot_swap_material_binding (D12 swap / adopt)
# ---------------------------------------------------------------------------


def _swap_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Asset, Shot

    pid = str(project_id or params.get("project_id") or "")
    shot_ids = list(params.get("shot_ids") or [])
    if not shot_ids:
        return {}, "ADOPTION_SHOTS_REQUIRED"
    asset_id = params.get("project_asset_id")
    if asset_id:
        asset = db.get(Asset, str(asset_id))
        if asset is None or asset.project_id != pid:
            return {}, "PROJECT_ASSET_NOT_FOUND"
    revs: list[tuple[str, int]] = []
    for sid in sorted(shot_ids):
        shot = db.get(Shot, sid)
        if shot is None or shot.project_id != pid:
            return {}, "SHOT_NOT_FOUND"
        revs.append((shot.id, int(shot.revision)))
    return {
        "project_id": pid,
        "project_asset_id": asset_id,
        "library_version_id": params.get("library_version_id"),
        "shot_revisions": revs,
        "role": params.get("role"),
        "entity_id": params.get("entity_id"),
    }, None


def _swap_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.api_asset_center import AdoptionPreviewRequest, _adoption_plan

    pid = str(params.get("project_id") or "")
    body = AdoptionPreviewRequest(
        project_id=pid,
        library_version_id=params.get("library_version_id"),
        project_asset_id=params.get("project_asset_id"),
        shot_ids=list(params.get("shot_ids") or []),
        role=params.get("role"),
        entity_id=params.get("entity_id"),
        allow_revise_frozen=bool(params.get("allow_revise_frozen", True)),
    )
    try:
        asset, entries = _adoption_plan(db, body)
    except HTTPException as exc:
        raise ActionInvalid(str(getattr(exc, "detail", exc))[:200]) from exc
    blocked = [entry for entry in entries if entry.get("conflict")]
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": f"将主资产「{asset.id}」显式替换为所选 {len(entries)} 镜的绑定素材（递增镜头版本并作废旧片段）。",
        "project_asset_id": asset.id,
        "shots": entries,
        "blocked": bool(occupancy["busy"] or blocked),
        "blocked_code": occupancy["code"] if occupancy["busy"] else (blocked[0].get("conflict") if blocked else None),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "复用既有 D12 adopt_into_shots 换图合同；拍摄中镜头受写保护，旧镜头修订不会丢失。",
    }


def _swap_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import adopt_project_asset_to_shots

    pid = str(row.project_id or "")
    try:
        res = adopt_project_asset_to_shots(
            db, settings,
            project_id=pid,
            idempotency_key=f"copilot-swap:{row.id}",
            shot_ids=list(params.get("shot_ids") or []),
            project_asset_id=params.get("project_asset_id"),
            library_version_id=params.get("library_version_id"),
            role=params.get("role"),
            entity_id=params.get("entity_id"),
            allow_revise_frozen=bool(params.get("allow_revise_frozen", True)),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("adoption_id") or "") or None
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/shots",
        "adoption_id": row.original_task_id,
        "adopted_count": res.get("adopted_count"),
        "shots": res.get("shots") or [],
        "facts_hash": json_sha256(res.get("shots") or []),
        "checked_at": utcnow().isoformat(),
    }


register_handler("material_derive", ActionHandler(target_facts=_derive_target_facts, preflight=_derive_preflight, execute=_derive_execute))
register_handler("material_preferences_update", ActionHandler(target_facts=_prefs_target_facts, preflight=_prefs_preflight, execute=_prefs_execute))
register_handler("material_generate", ActionHandler(target_facts=_generate_target_facts, preflight=_generate_preflight, execute=_generate_execute, readback=_material_run_readback))
register_handler("material_regenerate", ActionHandler(target_facts=_regen_target_facts, preflight=_regen_preflight, execute=_regen_execute, readback=_material_run_readback))
register_handler("material_promote", ActionHandler(target_facts=_promote_target_facts, preflight=_promote_preflight, execute=_promote_execute))
register_handler("shot_swap_material_binding", ActionHandler(target_facts=_swap_target_facts, preflight=_swap_preflight, execute=_swap_execute))

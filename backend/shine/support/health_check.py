"""Deterministic project health check (V4).

One read-only, side-effect-free answer to "what is blocking this project
right now", shared by the no-LLM copilot card and by the LLM copilot's
summary badge.  Every dimension reads an existing authoritative reader:

* environment  — ``comfyops.setup_summary.summary`` (the Setup verdict)
* storyboard   — ``director.overview.build_overview`` + ``director_storyboard_state``
* materials    — ``materialprep.service.evaluate_materials`` + the D12 binding projection
* tasks        — ``Job`` / ``LibraryRenderTask`` / ``MaterialItem`` ledgers
* gpu          — ``support.occupancy.resource_occupancy`` + managed processes

Nothing here is a second state table, a second readiness verdict or a second
scheduler: an unreadable layer is reported as unreadable, never as "fine".
"""
from __future__ import annotations

from typing import Any

from shine.models import utcnow

#: Hash routes of the original pages; the card's buttons jump there.
JUMP_SETUP = "#d/setup"
JUMP_ADVANCED = "#d/advanced"
JUMP_STORYBOARD = "#d/produce"
JUMP_MATERIALS = "#d/materials"
JUMP_RUN = "#d/run"
JUMP_DELIVER = "#d/deliver"
JUMP_PROJECTS = "#d/projects"

GROUP_LABELS: dict[str, str] = {
    "environment": "制作环境与可选能力",
    "storyboard": "项目阶段与分镜底盘",
    "materials": "素材缺件与 D12 回绑",
    "tasks": "镜头失败与待核对任务",
    "gpu": "GPU 租约与渲染器",
}

_BLOCKING_VERDICT_MODES = {"BLOCKED", "SETUP_REQUIRED", "VALIDATION_REQUIRED"}
_READY_STATES = {"READY", "VERIFIED_RENDER"}
#: Episode states that mean "a person must act before this episode can shoot".
_EPISODE_BLOCKING_STATES = {"STORYBOARD_BLOCKED", "MATERIAL_MISSING", "SHOTS_TO_CONFIRM"}
_FAILED_JOB_STATUSES = ("FAILED",)
_UNRESOLVED = ("submission_unknown", "unknown", "reconcile_required", "uncertain")


def _item(
    code: str,
    label: str,
    *,
    status: str,
    detail: str = "",
    jump: str | None = None,
    error_code: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "detail": detail,
        "jump": jump,
        "error_code": error_code,
        "entity_type": entity_type,
        "entity_id": entity_id,
        **extra,
    }


def _group(key: str, *, status: str, items: list[dict[str, Any]], jump: str | None,
           summary: str = "") -> dict[str, Any]:
    return {
        "key": key,
        "label": GROUP_LABELS[key],
        "status": status,
        "summary": summary,
        "jump": jump,
        "items": items,
    }


def _worst(items: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("status")) for row in items}
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "WARNING" in statuses:
        return "WARNING"
    return "OK"


def _environment(db, settings, *, project_id: str | None, llm_ready: bool | None) -> dict[str, Any]:
    from shine.comfyops.setup_summary import summary as setup_summary

    items: list[dict[str, Any]] = []
    try:
        verdict_doc = setup_summary(db, settings, project_id=project_id, discovered=[])
    except Exception as exc:  # noqa: BLE001 - an unreadable verdict is a named fact
        return _group(
            "environment", status="BLOCKED", jump=JUMP_SETUP,
            summary="无法读取本机制作环境裁决。",
            items=[_item("ENVIRONMENT_UNREADABLE", "环境台账读取失败", status="BLOCKED",
                         detail=type(exc).__name__, jump=JUMP_SETUP)],
        )
    verdict = verdict_doc.get("verdict") or {}
    mode = str(verdict.get("mode") or "UNKNOWN")
    verdict_code = verdict.get("code")
    if mode in _BLOCKING_VERDICT_MODES:
        items.append(_item(
            str(verdict_code or mode), str(verdict.get("label") or "制作环境未就绪"),
            status="BLOCKED", detail=str(verdict.get("detail") or "")[:240],
            jump=JUMP_SETUP, error_code=str(verdict_code) if verdict_code else None,
        ))
    else:
        items.append(_item(
            "VERDICT", str(verdict.get("label") or "制作环境就绪"), status="OK",
            detail=str(verdict.get("detail") or "")[:240], jump=JUMP_SETUP,
            mode=mode,
        ))
    capabilities = verdict_doc.get("capabilities") or {}
    for key in sorted(capabilities):
        row = capabilities.get(key) or {}
        if not isinstance(row, dict):
            continue
        missing = int(row.get("missing_count") or 0)
        optional = bool(row.get("optional"))
        state = str(row.get("state") or "UNKNOWN")
        label = str(row.get("label") or key)
        if missing:
            items.append(_item(
                f"CAPABILITY_MISSING_{key}", label,
                status="WARNING" if optional else "BLOCKED",
                detail=f"缺 {missing} 个文件" + ("（可选增强，不影响出片）" if optional else ""),
                jump=JUMP_SETUP, state=state, missing_count=missing, optional=optional,
            ))
        elif state not in _READY_STATES:
            items.append(_item(
                f"CAPABILITY_NOT_READY_{key}", label,
                status="WARNING" if optional else "BLOCKED",
                detail=f"状态 {state}" + ("（可选增强）" if optional else ""),
                jump=JUMP_SETUP, state=state, optional=optional,
            ))
        else:
            items.append(_item(
                f"CAPABILITY_{key}", label, status="OK", detail=f"状态 {state}",
                jump=JUMP_SETUP, state=state, optional=optional,
            ))
    if llm_ready is False:
        items.append(_item(
            "COPILOT_LLM_REQUIRED", "AI 管家文本模型未配置", status="WARNING",
            detail="配置文本模型后开启 AI 管家对话与自主项目操作。", jump=JUMP_ADVANCED,
        ))
    return _group(
        "environment", status=_worst(items), items=items, jump=JUMP_SETUP,
        summary=str(verdict.get("label") or mode),
    )


def _storyboard(db, *, project: Any) -> dict[str, Any]:
    from shine.director.overview import build_overview
    from shine.director.streamline import director_storyboard_state

    items: list[dict[str, Any]] = []
    try:
        overview = build_overview(db, project)
    except Exception as exc:  # noqa: BLE001
        return _group(
            "storyboard", status="BLOCKED", jump=JUMP_STORYBOARD,
            summary="无法读取导演台账。",
            items=[_item("OVERVIEW_UNREADABLE", "导演台账读取失败", status="BLOCKED",
                         detail=type(exc).__name__, jump=JUMP_STORYBOARD)],
        )
    plan = overview.get("plan")
    style = overview.get("style")
    if project.current_document_id is None:
        items.append(_item("SCRIPT_MISSING", "尚未导入原文", status="BLOCKED",
                           jump=JUMP_PROJECTS))
    elif plan is None:
        items.append(_item("BOOK_MISSING", "分镜书尚未生成", status="BLOCKED",
                           jump=JUMP_STORYBOARD))
    elif str(plan.get("status")) != "CONFIRMED":
        items.append(_item("BOOK_UNCONFIRMED", "分镜书尚未确认", status="WARNING",
                           detail=f"方案状态 {plan.get('status')}", jump=JUMP_STORYBOARD))
    else:
        items.append(_item("BOOK_CONFIRMED", "分镜书已确认", status="OK",
                           detail=f"版本 {plan.get('version')} · {plan.get('episode_count')} 集",
                           jump=JUMP_STORYBOARD))
    if style is None:
        items.append(_item("STYLE_NOT_FROZEN", "导演风格未冻结", status="BLOCKED",
                           jump=JUMP_STORYBOARD))
    else:
        items.append(_item("STYLE_FROZEN", "导演风格已冻结", status="OK",
                           detail=f"版本 {style.get('version')}", jump=JUMP_STORYBOARD))
    try:
        ledger = director_storyboard_state(db, project.id)
    except Exception as exc:  # noqa: BLE001
        ledger = {"error": type(exc).__name__}
    if ledger.get("stale_for_basis"):
        items.append(_item(
            "STORYBOARD_STALE_FOR_BASIS", "分镜底盘已过期", status="WARNING",
            detail=f"风格或分集方案在分镜生成后变更（{ledger.get('basis_changed_reason')}）",
            jump=JUMP_STORYBOARD,
        ))
    counts = overview.get("counts") or {}
    episodes = overview.get("episodes") or []
    blocked_episodes = [row for row in episodes if row.get("state") in _EPISODE_BLOCKING_STATES]
    for row in blocked_episodes[:12]:
        action = row.get("next_action") or {}
        materials = row.get("materials") or {}
        detail = str(row.get("state_label") or row.get("state"))
        if int(materials.get("missing_count") or 0):
            detail += f" · 缺 {materials['missing_count']} 项素材"
        items.append(_item(
            f"EPISODE_{row.get('state')}", f"第 {row.get('order')} 集 {row.get('title') or ''}".strip(),
            status="BLOCKED", detail=detail,
            jump=JUMP_MATERIALS if row.get("state") == "MATERIAL_MISSING" else JUMP_STORYBOARD,
            entity_type="episode", entity_id=row.get("episode_id"),
            next_action_kind=action.get("kind"), next_action_label=action.get("label"),
        ))
    if episodes and not blocked_episodes:
        items.append(_item(
            "EPISODES_ON_TRACK", f"{len(episodes)} 集均可推进", status="OK",
            detail=f"已确认 {counts.get('confirmed_episode_count', 0)} 集 · 已落镜 {counts.get('shot_count', 0)} 镜",
            jump=JUMP_STORYBOARD,
        ))
    run = overview.get("run") or {}
    if run.get("active"):
        items.append(_item(
            "RUN_ACTIVE", "生产运行进行中", status="OK", detail=str(run.get("status")),
            jump=JUMP_RUN, entity_type="run", entity_id=run.get("run_id"),
        ))
    next_action = overview.get("next_action") or {}
    return _group(
        "storyboard", status=_worst(items), items=items, jump=JUMP_STORYBOARD,
        summary=str(next_action.get("label") or "无待办"),
    )


def _materials(db, *, project: Any) -> dict[str, Any]:
    from shine.materialprep.service import evaluate_materials

    items: list[dict[str, Any]] = []
    try:
        judged = evaluate_materials(db, project.id)
    except Exception as exc:  # noqa: BLE001
        return _group(
            "materials", status="WARNING", jump=JUMP_MATERIALS,
            summary="无法读取素材台账。",
            items=[_item("MATERIALS_UNREADABLE", "素材台账读取失败", status="WARNING",
                         detail=type(exc).__name__, jump=JUMP_MATERIALS)],
        )
    if judged.get("materials_ready"):
        items.append(_item("MATERIALS_READY", "素材已齐备", status="OK",
                           detail=f"{len(judged.get('requirements') or [])} 项需求已满足",
                           jump=JUMP_MATERIALS))
        return _group("materials", status="OK", items=items, jump=JUMP_MATERIALS,
                      summary="素材已齐备")
    if judged.get("reason") == "MATERIAL_PLAN_NOT_CONFIRMED":
        items.append(_item("MATERIAL_PLAN_NOT_CONFIRMED", "素材计划尚未确认", status="WARNING",
                           jump=JUMP_MATERIALS))
        return _group("materials", status="WARNING", items=items, jump=JUMP_MATERIALS,
                      summary="素材计划尚未确认")
    for row in (judged.get("missing") or [])[:12]:
        items.append(_item(
            str(row.get("reason") or "ASSET_OR_VOICE_MISSING"),
            f"{row.get('family')} / {row.get('entity_id')}", status="BLOCKED",
            detail="缺主素材", jump=JUMP_MATERIALS,
            entity_type="requirement", entity_id=row.get("requirement_id"),
        ))
    detached = _d12_detached(db, project.id, judged)
    for row in detached[:12]:
        rebound = f"{row.get('bound_to_current_count')}/{row.get('target_shot_count')}"
        items.append(_item(
            "D12_BINDING_DETACHED", f"{row.get('family')} / {row.get('entity_id')}",
            status="WARNING",
            detail=f"新主素材已晋升，但冻结镜仍用旧图（{rebound} 回绑）",
            jump=JUMP_MATERIALS, entity_type="requirement", entity_id=row.get("requirement_id"),
        ))
    missing_count = len(judged.get("missing") or [])
    return _group(
        "materials", status=_worst(items), items=items, jump=JUMP_MATERIALS,
        summary=f"缺 {missing_count} 项素材" + (f" · {len(detached)} 项 D12 脱节" if detached else ""),
    )


def _d12_detached(db, project_id: str, judged: dict[str, Any]) -> list[dict[str, Any]]:
    """Requirements whose master was promoted after the shots were frozen.

    The projection is the existing ``project_requirement_shot_bindings`` truth:
    a requirement points at a current master, but its frozen shots still bind
    another asset (0/N rebound).  Only requirements that are satisfied (so a
    master exists) and that name shots can be detached.
    """
    from shine.comfyops.models import AssetRequirement
    from shine.materialprep.service import project_requirement_shot_bindings

    detached: list[dict[str, Any]] = []
    for row in judged.get("requirements") or []:
        if row.get("status") != "SATISFIED" or not row.get("asset_id"):
            continue
        requirement = db.get(AssetRequirement, str(row.get("requirement_id") or ""))
        if requirement is None:
            continue
        shot_ids = [str(item) for item in ((requirement.payload or {}).get("shot_ids") or []) if item]
        if not shot_ids:
            continue
        try:
            projection = project_requirement_shot_bindings(
                db, project_id, family=row.get("family"), entity_id=row.get("entity_id"),
                current_asset_id=row.get("asset_id"), shot_ids=shot_ids,
            )
        except Exception:  # noqa: BLE001 - an unreadable projection is not "bound"
            continue
        if projection.get("status") in {"LIBRARY_ONLY", "UNBOUND"} and projection.get("other_asset_shots"):
            detached.append({**row, **projection})
    return detached


def _tasks(db, *, project_id: str | None) -> dict[str, Any]:
    from sqlalchemy import func, select

    from shine.asset_center.models import LibraryRenderTask
    from shine.materialprep.models import MaterialItem
    from shine.models import Job, JobAttempt

    items: list[dict[str, Any]] = []
    job_query = select(Job).where(Job.status.in_(_FAILED_JOB_STATUSES))
    unresolved_query = (
        select(JobAttempt.job_id, JobAttempt.reconciliation_state, JobAttempt.failure_code)
        .where(func.lower(JobAttempt.reconciliation_state).in_(_UNRESOLVED))
    )
    if project_id:
        job_query = job_query.where(Job.project_id == project_id)
        unresolved_query = unresolved_query.where(
            JobAttempt.job_id.in_(select(Job.id).where(Job.project_id == project_id))
        )
    try:
        failed_jobs = list(db.scalars(job_query.order_by(Job.updated_at.desc()).limit(20)).all())
        unresolved = list(db.execute(unresolved_query.limit(20)).all())
    except Exception as exc:  # noqa: BLE001
        return _group(
            "tasks", status="WARNING", jump=JUMP_RUN, summary="无法读取任务台账。",
            items=[_item("TASKS_UNREADABLE", "任务台账读取失败", status="WARNING",
                         detail=type(exc).__name__, jump=JUMP_RUN)],
        )
    for job in failed_jobs:
        items.append(_item(
            str(job.failure_code or "JOB_FAILED"), f"镜头任务 {job.shot_id[:8]}",
            status="BLOCKED", detail=f"状态 {job.status}", jump=JUMP_RUN,
            error_code=job.failure_code, entity_type="job", entity_id=job.id,
            shot_id=job.shot_id,
        ))
    for job_id, state, failure_code in unresolved:
        items.append(_item(
            "RECONCILE_REQUIRED", f"镜头任务 {str(job_id)[:8]}", status="BLOCKED",
            detail=f"外部结果待核对（{state}）；确认前禁止重提", jump=JUMP_RUN,
            error_code=failure_code, entity_type="job", entity_id=job_id,
        ))
    try:
        render_query = select(LibraryRenderTask).where(LibraryRenderTask.status == "FAILED")
        material_query = select(MaterialItem).where(MaterialItem.status == "FAILED")
        render_failed = list(db.scalars(render_query.order_by(LibraryRenderTask.created_at.desc()).limit(10)).all())
        material_failed = list(db.scalars(material_query.order_by(MaterialItem.created_at.desc()).limit(10)).all())
    except Exception:  # noqa: BLE001 - the job ledger above is still authoritative
        render_failed, material_failed = [], []
    for task in render_failed:
        items.append(_item(
            str(getattr(task, "error_code", None) or "IMAGE_TASK_FAILED"),
            f"素材中心生图任务 {task.id[:8]}", status="BLOCKED", detail="状态 FAILED",
            jump="#a/library", entity_type="library_render_task", entity_id=task.id,
        ))
    for item_row in material_failed:
        items.append(_item(
            str(getattr(item_row, "error_code", None) or "MATERIAL_ITEM_FAILED"),
            f"项目素材生成 {item_row.id[:8]}", status="BLOCKED", detail="状态 FAILED",
            jump=JUMP_MATERIALS, entity_type="material_item", entity_id=item_row.id,
        ))
    if not items:
        items.append(_item("NO_FAILED_TASKS", "没有失败或待核对的任务", status="OK", jump=JUMP_RUN))
    return _group(
        "tasks", status=_worst(items), items=items, jump=JUMP_RUN,
        summary=f"{len(failed_jobs)} 个失败 · {len(unresolved)} 个待核对",
    )


def _gpu(db) -> dict[str, Any]:
    from shine.support.occupancy import resource_occupancy

    items: list[dict[str, Any]] = []
    try:
        occupancy = resource_occupancy(db)
    except Exception as exc:  # noqa: BLE001
        return _group(
            "gpu", status="WARNING", jump=JUMP_SETUP, summary="无法读取资源占用。",
            items=[_item("OCCUPANCY_UNREADABLE", "资源占用读取失败", status="WARNING",
                         detail=type(exc).__name__, jump=JUMP_SETUP)],
        )
    code = occupancy.get("code")
    if code == "UNRESOLVED_EXTERNAL_OUTCOME":
        items.append(_item(
            "UNRESOLVED_EXTERNAL_OUTCOME", "有任务外部结果待核对", status="BLOCKED",
            detail=occupancy.get("label"), jump=JUMP_RUN,
        ))
    elif code == "GPU_LEASE_ACTIVE":
        items.append(_item("GPU_LEASE_ACTIVE", "GPU 租约占用中", status="WARNING",
                           detail=occupancy.get("label"), jump=JUMP_SETUP,
                           leases=occupancy.get("leases")))
    elif code in {"PRODUCTION_ACTIVE", "MATERIAL_ACTIVE"}:
        items.append(_item(str(code), "生产/素材任务进行中", status="OK",
                           detail=occupancy.get("label"), jump=JUMP_RUN))
    elif code == "OCCUPANCY_UNKNOWN":
        items.append(_item("OCCUPANCY_UNKNOWN", "资源占用无法完整读取", status="WARNING",
                           detail=occupancy.get("label"), jump=JUMP_SETUP))
    else:
        items.append(_item("GPU_IDLE", "GPU 空闲", status="OK", detail=occupancy.get("label"),
                           jump=JUMP_SETUP))
    try:
        from shine.comfyops.renderer_release import describe_managed_processes

        # The guarded executor's own vocabulary: a recorded renderer that is
        # provably gone, or one whose ownership cannot be proven, needs a person.
        for row in describe_managed_processes(db)[:6]:
            if not isinstance(row, dict):
                continue
            state = str(row.get("state") or "UNKNOWN")
            needs_attention = state in {"GONE", "UNVERIFIED"}
            items.append(_item(
                f"RENDERER_{state}", f"{row.get('kind') or '渲染器'} {row.get('stage') or ''}".strip(),
                status="WARNING" if needs_attention else "OK",
                detail={"GONE": "已登记的渲染器已退出，记录尚未收尾",
                        "UNVERIFIED": "无法证明该渲染器归属，需人工核对",
                        "RUNNING": "运行中", "STOPPED": "已停止"}.get(state, state),
                jump=JUMP_SETUP, entity_type="managed_process", entity_id=row.get("id"),
            ))
    except Exception:  # noqa: BLE001 - the lease ledger above is the authority
        pass
    try:
        from sqlalchemy import select

        from shine.models import WorkerInstance

        workers = list(db.scalars(select(WorkerInstance)).all())
    except Exception:  # noqa: BLE001
        workers = []
    live_workers = [row for row in workers if str(row.status) in {"running", "idle", "starting"}]
    if workers and not live_workers:
        items.append(_item("WORKER_OFFLINE", "后台 Worker 未运行", status="BLOCKED",
                           detail="制作任务不会继续推进", jump=JUMP_SETUP))
    return _group(
        "gpu", status=_worst(items), items=items, jump=JUMP_SETUP,
        summary=str(occupancy.get("label") or ""),
    )


def build_health_check(
    db,
    settings,
    *,
    project_id: str | None = None,
    llm_ready: bool | None = None,
) -> dict[str, Any]:
    """The one deterministic health answer.  Read-only; never starts a renderer."""
    from shine.models import Project

    project = db.get(Project, project_id) if project_id else None
    groups = [_environment(db, settings, project_id=project_id, llm_ready=llm_ready)]
    groups.append(_storyboard(db, project=project) if project is not None else _group(
        "storyboard", status="OK", items=[_item("NO_PROJECT", "未选定项目", status="OK",
                                                 jump=JUMP_PROJECTS)],
        jump=JUMP_PROJECTS, summary="未选定项目",
    ))
    groups.append(_materials(db, project=project) if project is not None else _group(
        "materials", status="OK", items=[_item("NO_PROJECT", "未选定项目", status="OK",
                                                 jump=JUMP_PROJECTS)],
        jump=JUMP_MATERIALS, summary="未选定项目",
    ))
    groups.append(_tasks(db, project_id=project_id))
    groups.append(_gpu(db))
    blockers = [
        {**row, "group": group["key"], "group_label": group["label"]}
        for group in groups for row in group["items"] if row["status"] == "BLOCKED"
    ]
    warnings = sum(1 for group in groups for row in group["items"] if row["status"] == "WARNING")
    return {
        "generated_at": utcnow().replace(microsecond=0).isoformat(),
        "project_id": project.id if project is not None else None,
        "project_title": project.title if project is not None else None,
        "summary": {
            "blocking": len(blockers),
            "warnings": warnings,
            "status": "BLOCKED" if blockers else ("WARNING" if warnings else "OK"),
        },
        "groups": groups,
        "blockers": blockers[:24],
        "read_only": True,
        "authority": "comfyops.setup_summary.summary / director.overview.build_overview / "
                     "materialprep.service.evaluate_materials / support.occupancy.resource_occupancy",
    }

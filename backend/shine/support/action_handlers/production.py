"""Domain 5: Workflows, director package, production start/control, shot rerender, delivery, and recovery."""
from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from shine.copilot.params import ParamError, model_payload, routed_kwargs
from shine.models import utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionHandler,
    ActionInvalid,
    blocked_from_executor,
    json_sha256,
    register_handler,
)

# ---------------------------------------------------------------------------
# workflow_user_copy & workflow_graph_register
# ---------------------------------------------------------------------------


def _workflow_copy_resolve_project(db, params: dict[str, Any], canonical: str | None) -> str | None:
    from shine.models import WorkflowProfile

    source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
    if source is None:
        raise ActionInvalid("找不到要复制的工作流合同。")
    if source.project_id and canonical and canonical != source.project_id:
        raise ActionInvalid("不能把其它项目的私有工作流复制到当前项目。")
    return source.project_id or canonical


def _workflow_copy_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfy.workflow import graph_hash
    from shine.models import WorkflowProfile

    source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
    if source is None:
        return {}, "SOURCE_PROFILE_MISSING"
    return {
        "source": {
            "id": source.id,
            "project_id": source.project_id,
            "name": source.name,
            "mode": source.mode,
            "graph_sha256": graph_hash(source.graph or {}),
            "stored_graph_sha256": source.graph_sha256,
            "parameter_map": source.parameter_map or {},
            "media_transfers": source.media_transfers or {},
            "required_nodes": sorted(source.required_nodes or []),
            "checkpoint_id": source.checkpoint_id,
            "model_identity": source.model_identity or {},
            "validated_against": source.validated_against,
        },
        "requested": {
            "name": params.get("name"),
            "overrides": params.get("parameter_overrides") or {},
            "project_id": params.get("project_id") or source.project_id,
        },
    }, None


def _workflow_copy_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import WorkflowProfile

    source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
    if source is None:
        raise ActionInvalid("找不到要复制的工作流合同。")
    overrides = dict(params.get("parameter_overrides") or {})
    unknown = sorted(set(overrides) - set(source.parameter_map or {}))
    if unknown:
        raise ActionInvalid(f"只能覆盖该工作流已映射的参数：{', '.join(unknown)}")
    name = str(params.get("name") or "").strip()
    if not name or name == source.name:
        raise ActionInvalid("新副本必须使用新的工作流名称。")
    before = {key: (source.parameter_map or {}).get(key) for key in overrides}
    return {
        **common,
        "summary": "复制一条新的用户级工作流合同，内置模板与源合同保持不变。",
        "missing_components": [],
        "download_bytes": 0,
        "source": {
            "profile_id": source.id,
            "name": source.name,
            "scope": "GLOBAL_BUILTIN" if source.project_id is None else "PROJECT",
            "mode": source.mode,
            "validated_against": source.validated_against,
        },
        "target": {"name": name, "project_id": params.get("project_id") or source.project_id},
        "parameter_diff": [{"key": key, "before": before.get(key), "after": overrides[key]} for key in overrides],
        "will_modify_builtin_template": False,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "只新增副本并调用既有工作流校验；源合同和内置模板不会被修改，可在执行后回滚删除该副本。",
    }


def _workflow_copy_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.api_comfy import create_workflow_profile
    from shine.models import WorkflowProfile
    from shine.schemas import GenerationMode, WorkflowProfileCreate

    source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
    if source is None:
        raise ActionBlocked("SOURCE_PROFILE_MISSING", "源工作流合同已不存在，请重新预览。")
    target_project = params.get("project_id") or source.project_id
    if source.project_id is not None and target_project != source.project_id:
        raise ActionBlocked("CROSS_PROJECT_COPY", "不能把其它项目的私有工作流复制到当前项目。")
    parameter_map = dict(source.parameter_map or {})
    parameter_map.update(dict(params.get("parameter_overrides") or {}))
    body = WorkflowProfileCreate.model_validate({
        **model_payload(declaration, params),
        "mode": GenerationMode(source.mode),
        "graph": copy.deepcopy(source.graph),
        "parameter_map": parameter_map,
        "media_transfers": copy.deepcopy(source.media_transfers),
        "required_nodes": list(source.required_nodes or []),
        "checkpoint_id": source.checkpoint_id,
        "project_id": target_project,
    })
    created = create_workflow_profile(body, settings=settings, db=db)
    row.status = "EXECUTED"
    row.verification = {
        "outcome": "VERIFIED" if created.validated_against else "STATIC_VALIDATED_LIVE_PENDING",
        "executor": spec.executor,
        "authority": "workflow_profiles.validated_against",
        "created_profile_id": created.id,
        "created_profile_name": created.name,
        "source_profile_id": source.id,
        "source_unchanged": True,
        "checked_at": utcnow().isoformat(),
    }


def _workflow_copy_rollback(db, settings, row, actor: str) -> None:
    from shine.models import Job, ProductionSeries, WorkflowProfile
    from shine.services import add_audit

    if row.status == "ROLLED_BACK":
        return
    created_id = (row.verification or {}).get("created_profile_id")
    if not created_id:
        raise ActionBlocked("ROLLBACK_UNAVAILABLE", "没有可回滚的用户副本。")
    profile = db.get(WorkflowProfile, str(created_id))
    if profile is not None and row.project_id and profile.project_id != row.project_id:
        raise ActionBlocked("CROSS_PROJECT_ROLLBACK", "该副本不属于本次授权动作的项目，不能删除。")
    jobs = db.scalar(select(func.count(Job.id)).where(Job.workflow_profile_id == created_id))
    series = db.scalar(select(func.count(ProductionSeries.id)).where(ProductionSeries.workflow_profile_id == created_id))
    if jobs or series:
        raise ActionBlocked(
            "PROFILE_IN_USE",
            "该副本已被生产任务引用，不能删除；请保留现场并由开发者处理。",
            details={"jobs": int(jobs or 0), "series": int(series or 0)},
        )
    if profile is not None:
        db.delete(profile)
    row.status = "ROLLED_BACK"
    row.verification = {
        **(row.verification or {}),
        "outcome": "ROLLED_BACK",
        "rolled_back_at": utcnow().isoformat(),
        "rolled_back_profile_id": created_id,
    }
    add_audit(
        db, actor, "SUPPORT_WORKFLOW_COPY_ROLLED_BACK", "support_action", row.id,
        {"created_profile_id": created_id, "deleted": profile is not None},
        project_id=row.project_id,
    )
    db.commit()


def _workflow_graph_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfy.workflow import graph_hash
    from shine.models import WorkflowProfile

    graph = params.get("graph")
    if not isinstance(graph, dict) or not graph:
        return {}, "WORKFLOW_GRAPH_MISSING"
    name = str(params.get("name") or "")
    if not name:
        return {}, "WORKFLOW_NAME_MISSING"
    scoped = select(WorkflowProfile).where(WorkflowProfile.name == name)
    scoped = scoped.where(WorkflowProfile.project_id == project_id) if project_id else scoped.where(WorkflowProfile.project_id.is_(None))
    existing = db.scalar(scoped)
    return {
        "graph_sha256": graph_hash(graph),
        "name": name,
        "project_id": project_id,
        "existing_profile_id": existing.id if existing is not None else None,
        "existing_graph_sha256": existing.graph_sha256 if existing is not None else None,
        "mode": params.get("mode"),
        "required_nodes": sorted(params.get("required_nodes") or []),
        "parameter_map": params.get("parameter_map") or {},
        "media_transfers": params.get("media_transfers") or {},
    }, None


def _workflow_graph_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfy.workflow import graph_hash, validate_graph
    from shine.models import WorkflowProfile

    graph = params.get("graph")
    if not isinstance(graph, dict) or not graph:
        raise ActionInvalid("缺少待注册的工作流图。")
    try:
        validate_graph(graph, required_nodes=list(params.get("required_nodes") or []))
    except Exception as exc:  # noqa: BLE001
        raise ActionInvalid(f"工作流图未通过既有静态校验：{str(exc)[:160]}") from exc
    parameter_map = dict(params.get("parameter_map") or {})
    for key in ("positive_prompt", "duration"):
        if key not in parameter_map:
            raise ActionInvalid(f"parameter_map 必须映射 {key} 才能在真实生产中选择。")
    target_project = params.get("project_id")
    name = str(params.get("name") or "")
    scoped = select(WorkflowProfile).where(WorkflowProfile.name == name)
    scoped = scoped.where(WorkflowProfile.project_id == target_project) if target_project else scoped.where(WorkflowProfile.project_id.is_(None))
    existing = db.scalar(scoped)
    same_graph = bool(existing is not None and existing.graph_sha256 == graph_hash(graph))
    return {
        **common,
        "summary": "把这份图注册成用户级工作流合同，并调用原有节点校验。",
        "missing_components": [],
        "download_bytes": 0,
        "graph_sha256": graph_hash(graph),
        "node_count": len(graph),
        "node_classes": sorted({str((node or {}).get("class_type")) for node in graph.values()})[:24],
        "parameter_map": sorted(parameter_map),
        "media_transfers": sorted(params.get("media_transfers") or {}),
        "target": {"name": params.get("name"), "project_id": params.get("project_id")},
        "reuse_existing_profile_id": existing.id if same_graph else None,
        "name_conflict_profile_id": existing.id if existing is not None and not same_graph else None,
        "will_modify_builtin_template": False,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "只新增一条用户级工作流合同并调用原注册与节点校验；内置模板与节点源码不变。登记成功不等于可生产，仍需原 validate 与小样任务。",
    }


def _workflow_graph_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.api_comfy import create_workflow_profile
    from shine.schemas import WorkflowProfileCreate

    body = WorkflowProfileCreate.model_validate(model_payload(declaration, params))
    created = create_workflow_profile(body, settings=settings, db=db)
    row.status = "EXECUTED"
    row.verification = {
        "outcome": "VERIFIED" if created.validated_against else "REGISTERED_NOT_EXECUTABLE",
        "executor": spec.executor,
        "authority": "workflow_profiles.validated_against",
        "created_profile_id": created.id,
        "created_profile_name": created.name,
        "graph_sha256": created.graph_sha256,
        "validated_against": created.validated_against,
        "production_ready": False,
        "checked_at": utcnow().isoformat(),
    }


def _workflow_graph_readback(db, settings, row) -> None:
    if row.status != "EXECUTED":
        return
    from shine.models import WorkflowProfile

    profile_id = (row.verification or {}).get("created_profile_id")
    profile = db.get(WorkflowProfile, str(profile_id or "")) if profile_id else None
    if profile is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "注册出来的工作流合同已不存在，无法确认这一步的结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "created_profile_id": profile_id, "checked_at": utcnow().isoformat()}
        db.commit()
        return
    validated = profile.validated_against or None
    missing_nodes = list((validated or {}).get("missing_nodes") or [])
    missing_inputs = list((validated or {}).get("missing_inputs") or [])
    executable = bool(validated) and not missing_nodes and not missing_inputs
    row.verification = {
        **(row.verification or {}),
        "created_profile_id": profile.id,
        "graph_sha256": profile.graph_sha256,
        "validated_against": validated,
        "outcome": "VERIFIED" if executable else "REGISTERED_NOT_EXECUTABLE",
        "node_contract_executable": executable,
        "missing_nodes": missing_nodes,
        "missing_input_count": len(missing_inputs),
        "production_ready": False,
        "checked_at": utcnow().isoformat(),
    }
    db.commit()


# ---------------------------------------------------------------------------
# director_package_create (NEW)
# ---------------------------------------------------------------------------


def _package_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    return {
        "project_id": pid,
        "frozen_style_version_id": project.frozen_style_version_id,
        "episode_ids": sorted(params.get("episode_ids") or []),
        "workflow_profile_id": params.get("workflow_profile_id"),
    }, None


def _package_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": "按当前已确认分镜、主资产绑定与工作流合同生成一版新的导演包快照。",
        "episode_ids": list(params.get("episode_ids") or []),
        "workflow_profile_id": params.get("workflow_profile_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "调用既有 build_package，完整保留 Ref2VA 与风格/资产绑定校验。",
    }


def _package_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.package import build_package, package_episodes, package_public
    from shine.models import Project
    from shine.services import add_audit

    pid = str(row.project_id or "")
    project = db.get(Project, pid)
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "项目不存在。")
    try:
        package = build_package(
            db, project, settings=settings,
            workflow_profile_id=params.get("workflow_profile_id"),
            generation_mode=str(params.get("generation_mode") or "REF2VA"),
            output=params.get("output"),
            policy=params.get("policy"),
            episode_ids=params.get("episode_ids"),
            created_by=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    add_audit(
        db, actor, "DIRECTOR_PACKAGE_CREATED", "director_package", package.id,
        {"revision": package.revision, "scope_digest": package.scope_digest, "episodes": package.episode_count, "shots": package.shot_count},
        project_id=pid,
    )
    row.original_task_id = package.id
    row.status = "SUCCEEDED"
    pub = package_public(package, episodes=package_episodes(db, package.id))
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/director-packages/{package_id}",
        "package_id": package.id,
        "revision": package.revision,
        "readiness": pub.get("readiness"),
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# production_start & production_control
# ---------------------------------------------------------------------------


def _production_start_validate_scope(db, params: dict[str, Any], target_project: str | None) -> None:
    from shine.director.overview import build_overview
    from shine.models import Project, WorkflowProfile

    if not target_project:
        raise ActionInvalid("开始拍摄必须指定项目。")
    project = db.get(Project, str(target_project))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    overview = build_overview(db, project)
    known = {row["episode_id"] for row in (overview.get("episodes") or [])}
    unknown = [item for item in (params.get("episode_ids") or []) if item not in known]
    if unknown:
        raise ActionInvalid(f"这些集不在当前分集方案里：{', '.join(unknown)}")
    profile_id = params.get("workflow_profile_id")
    if profile_id:
        profile = db.get(WorkflowProfile, str(profile_id))
        if profile is None:
            raise ActionInvalid("选择的生成方案不存在。")
        if profile.project_id is not None and profile.project_id != target_project:
            raise ActionInvalid("选择的生成方案不属于这个作品。")
    if params.get("workflow_profile_id") and params.get("strategy"):
        raise ActionInvalid("生产策略与静态生成方案不能同时指定。")


def _production_start_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.overview import build_overview
    from shine.models import Project, WorkflowProfile
    from shine.strategy import GenerationStrategy

    pid = str(params.get("project_id") or project_id or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    overview = build_overview(db, project)
    selected = [row for row in (overview.get("episodes") or []) if row.get("episode_id") in set(params.get("episode_ids") or [])]
    if not selected:
        return {}, "EPISODE_NOT_IN_PLAN"
    strategy_sha = GenerationStrategy.model_validate(params["strategy"]).strategy_sha256() if params.get("strategy") else None
    profile_facts = None
    profile_id = params.get("workflow_profile_id")
    if profile_id:
        profile = db.get(WorkflowProfile, str(profile_id))
        if profile is None:
            return {}, "WORKFLOW_PROFILE_NOT_FOUND"
        profile_facts = {"id": profile.id, "graph_sha256": profile.graph_sha256, "validated_against": profile.validated_against, "checkpoint_id": profile.checkpoint_id}
    return {
        "episodes": sorted(
            (
                {
                    "id": row.get("episode_id"),
                    "state": row.get("state"),
                    "shots_confirmed": bool((row.get("shots") or {}).get("confirmed")),
                    "shots_landed": int((row.get("shots") or {}).get("landed_count") or 0),
                    "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
                    "package": {
                        "id": (row.get("package") or {}).get("package_id"),
                        "revision": (row.get("package") or {}).get("revision"),
                        "startable": (row.get("package") or {}).get("startable"),
                    },
                }
                for row in selected
            ),
            key=lambda item: str(item["id"]),
        ),
        "strategy_sha256": strategy_sha,
        "workflow_profile": profile_facts,
        "frozen_style_version_id": project.frozen_style_version_id,
    }, None


def _production_start_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.director.overview import build_overview
    from shine.models import Project

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    overview = build_overview(db, project)
    selected = [row for row in (overview.get("episodes") or []) if row.get("episode_id") in set(params.get("episode_ids") or [])]
    blocked_episodes = [
        {
            "episode_id": row["episode_id"],
            "state": row.get("state"),
            "state_label": row.get("state_label"),
            "next_action": (row.get("next_action") or {}).get("label"),
            "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
        }
        for row in selected
        if row.get("state") not in {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING", "REVIEW_READY", "DELIVERED"}
    ]
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": "把选中的集交给既有导演包与 Run 引擎开始真实拍摄；相同选择只会得到同一次运行。",
        "missing_components": [],
        "download_bytes": 0,
        "episodes": [
            {
                "episode_id": row["episode_id"],
                "title": row.get("title"),
                "state": row.get("state"),
                "state_label": row.get("state_label"),
                "shots_confirmed": bool((row.get("shots") or {}).get("confirmed")),
                "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
            }
            for row in selected
        ],
        "blocked": bool(occupancy["busy"] or blocked_episodes),
        "blocked_code": occupancy["code"] if occupancy["busy"] else ("EPISODE_NOT_SHOOTABLE" if blocked_episodes else None),
        "blocked_episodes": blocked_episodes,
        "strategy": params.get("strategy"),
        "workflow_profile_id": params.get("workflow_profile_id"),
        "will_start_gpu": True,
        "will_modify_user_comfyui": True,
        "paid": False,
        "authorization_summary": "确认后按原门禁真实开拍（占用 GPU 并生成视频）；不替用户确认风格、主资产或审片。",
    }


def _production_start_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.overview import build_overview
    from shine.director.streamline import start_director_production
    from shine.models import Project
    from shine.services import add_audit

    project = db.get(Project, str(row.project_id or ""))
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
    overview = build_overview(db, project)
    selected = [row_view for row_view in (overview.get("episodes") or []) if row_view.get("episode_id") in set(params.get("episode_ids") or [])]
    not_shootable = [item for item in selected if item.get("state") not in {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING", "REVIEW_READY", "DELIVERED"}]
    if not selected:
        raise ActionBlocked("EPISODE_NOT_IN_PLAN", "选择的集不在当前分集方案里，请重新预览。")
    if not_shootable:
        detail = "；".join(f"{item.get('title') or item['episode_id']}：{item.get('state_label') or item.get('state')}" for item in not_shootable[:4])
        raise ActionBlocked("EPISODE_NOT_SHOOTABLE", f"选中的集还没准备好（{detail}）；先处理这些集，其它集不受影响。")
    try:
        value = start_director_production(
            db, project, settings=settings, actor=actor,
            **routed_kwargs(declaration, params, start_director_production),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    run_id = str(value.get("run_id") or "") or None
    row.original_task_id = run_id
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/production-runs/{run_id}",
        "run_id": run_id,
        "run_status": value.get("run_status"),
        "package_id": value.get("package_id"),
        "package_revision": value.get("package_revision"),
        "package_built": value.get("package_built"),
        "created": value.get("created"),
        "episode_ids": value.get("episode_ids"),
        "bound_at": utcnow().isoformat(),
        "checked_at": utcnow().isoformat(),
    }
    if run_id:
        add_audit(db, actor, "SUPPORT_ACTION_TASK_BOUND", "support_action", row.id,
                  {"kind": row.kind, "task_id": run_id, "run_status": value.get("run_status")}, project_id=row.project_id)


def _production_start_readback(db, settings, row) -> None:
    if row.status != "DELEGATED":
        return
    from shine.production.models import TERMINAL_RUN_STATUSES, ProductionRun, ProductionRunEpisode

    run = db.get(ProductionRun, str(row.original_task_id or ""))
    if run is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到这次生产运行的记录，无法确认结果；请核对后再操作。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "attributed": False, "authority": "GET /api/v1/production-runs/{run_id}", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    episodes = list(db.scalars(select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run.id)).all())
    episode_status = sorted({str(item.status) for item in episodes})
    delivered = all(item.status == "DELIVERED" for item in episodes) if episodes else False
    row.verification = {
        **(row.verification or {}),
        "run_id": run.id,
        "run_status": run.status,
        "episode_count": len(episodes),
        "episode_statuses": episode_status,
        "delivered": delivered,
        "checked_at": utcnow().isoformat(),
    }
    if run.status in TERMINAL_RUN_STATUSES:
        if run.status == "COMPLETED" and delivered:
            row.status = "SUCCEEDED"
            row.verification["outcome"] = "VERIFIED"
        elif run.status == "COMPLETED":
            row.status = "NEEDS_REVIEW"
            row.last_error = "拍摄已结束，但仍有集未完成审片/交付；这部分必须由你确认。"
            row.verification["outcome"] = "PENDING_REVIEW"
        else:
            row.status = "FAILED"
            row.last_error = f"原生产运行状态 {run.status}。"
            row.verification["outcome"] = "FAILED"
    else:
        row.verification["outcome"] = "PENDING"
    db.commit()


def _production_control_resolve_project(db, params: dict[str, Any], canonical: str | None) -> str | None:
    from shine.production.models import ProductionRun

    run = db.get(ProductionRun, str(params.get("run_id") or ""))
    if run is None:
        raise ActionInvalid("找不到这次生产任务。")
    if canonical and canonical != run.project_id:
        raise ActionInvalid("生产任务不属于当前项目；已拒绝跨项目控制。")
    return run.project_id


def _production_control_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.production.models import ProductionRun

    run = db.get(ProductionRun, str(params.get("run_id") or ""))
    if run is None:
        return {}, "PRODUCTION_RUN_NOT_FOUND"
    return {"run_id": run.id, "run_status": run.status, "state_version": run.state_version}, None


def _production_control_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.production.models import ProductionRun

    run = db.get(ProductionRun, str(params.get("run_id") or ""))
    if run is None:
        raise ActionInvalid("找不到这次生产任务。")
    return {
        **common,
        "summary": "暂停、继续或取消这次真实生产任务；只操作这一个已存在的 run。",
        "run": {"id": run.id, "status": run.status, "state_version": run.state_version, "episode_ids": list(run.episode_ids or [])},
        "operation": params.get("operation"),
        "will_start_gpu": True,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后由既有 Run 引擎执行所选控制操作；不会改动其它任务。",
    }


def _production_control_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director import run_engine
    from shine.production.models import ProductionRun
    from shine.support.models import SupportAction

    run_id = str(params.get("run_id") or "")
    if db.get(ProductionRun, run_id) is None:
        raise ActionBlocked("PRODUCTION_RUN_NOT_FOUND", "找不到这次生产任务。")
    operation = str(params.get("operation") or "")
    action_id = row.id
    db.rollback()
    if operation == "pause":
        status = run_engine.pause_run(session_factory, run_id, worker_id=actor)
    elif operation == "resume":
        status = run_engine.resume_run(session_factory, run_id, actor=actor)
    elif operation == "cancel":
        status = run_engine.cancel_run(session_factory, run_id, actor=actor)
    else:  # pragma: no cover
        raise ActionBlocked("INVALID_OPERATION", "只支持暂停、继续或取消。")
    db.expire_all()
    row_ref = db.get(SupportAction, action_id)
    fresh = db.get(ProductionRun, run_id)
    if row_ref is not None:
        row_ref.original_task_id = run_id
        row_ref.status = "SUCCEEDED"
        row_ref.verification = {
            "outcome": "VERIFIED",
            "executor": spec.executor,
            "authority": "GET /api/v1/production-runs/{run_id}",
            "operation": operation,
            "run_status": status,
            "state_version": fresh.state_version if fresh else None,
            "checked_at": utcnow().isoformat(),
        }


# ---------------------------------------------------------------------------
# shot_rerender, episode_delivery_assemble, task_reconcile, stale_run_recover (NEW)
# ---------------------------------------------------------------------------


def _rerender_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Job, Shot

    pid = str(project_id or params.get("project_id") or "")
    shot = db.get(Shot, str(params.get("shot_id") or ""))
    source = db.get(Job, str(params.get("source_job_id") or ""))
    if shot is None or shot.project_id != pid:
        return {}, "SHOT_NOT_FOUND"
    if source is None or source.project_id != pid or source.shot_id != shot.id:
        return {}, "GENERATION_SOURCE_INVALID"
    return {
        "project_id": pid,
        "shot_id": shot.id,
        "shot_revision": shot.revision,
        "source_job_id": source.id,
        "source_status": source.status,
    }, None


def _rerender_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"按镜重拍镜头「{params.get('shot_id')}」（源作业 {params.get('source_job_id')}）。",
        "shot_id": params.get("shot_id"),
        "source_job_id": params.get("source_job_id"),
        "will_start_gpu": True,
        "will_modify_user_comfyui": True,
        "authorization_summary": "经过原 generation-segments/regenerate 校验与非重提交对账，保留旧已过版本，不覆盖既有产物。",
    }


def _rerender_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import rerender_shot_segment
    from shine.support.models import SupportAction

    pid = str(row.project_id or "")
    action_id = row.id
    try:
        res = rerender_shot_segment(
            db, settings,
            project_id=pid,
            shot_id=str(params.get("shot_id") or ""),
            source_job_id=str(params.get("source_job_id") or ""),
            expected_shot_revision=params.get("expected_shot_revision"),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    db.expire_all()
    fresh_row = db.get(SupportAction, action_id) or row
    job_id = res.get("job_id")
    series_id = res.get("series_id")
    fresh_row.original_task_id = str(job_id or series_id or "") or None
    fresh_row.status = "DELEGATED"
    fresh_row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/shots",
        "job_id": job_id,
        "series_id": series_id,
        "shot_revision": res.get("shot_revision"),
        "checked_at": utcnow().isoformat(),
    }


def _rerender_readback(db, settings, row) -> None:
    if row.status != "DELEGATED":
        return
    from shine.models import Job

    job_id = (row.verification or {}).get("job_id")
    if not job_id:
        return
    job = db.get(Job, str(job_id))
    if job is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到重拍作业记录，无法确认结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    status = str(job.status or "")
    row.verification = {**(row.verification or {}), "job_status": status, "checked_at": utcnow().isoformat()}
    if status == "PASSED":
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
    elif status in {"FAILED", "CANCELLED"}:
        row.status = "FAILED"
        row.last_error = f"重拍作业结束，状态：{status}"
        row.verification["outcome"] = "FAILED"
    elif status == "NEEDS_REVIEW":
        row.status = "NEEDS_REVIEW"
        row.last_error = "重拍作业存在待核对状态。"
        row.verification["outcome"] = "UNKNOWN"
    db.commit()


def _delivery_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import ProductionSeries

    pid = str(project_id or params.get("project_id") or "")
    series = db.get(ProductionSeries, str(params.get("series_id") or ""))
    if series is None or series.project_id != pid:
        return {}, "PRODUCTION_SERIES_NOT_FOUND"
    return {"project_id": pid, "series_id": series.id, "series_status": series.status}, None


def _delivery_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import ProductionSeries

    series = db.get(ProductionSeries, str(params.get("series_id") or ""))
    if series is None:
        raise ActionInvalid("找不到指定生产序列。")
    not_done = series.status != "COMPLETED"
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": f"将已拍完的生产序列「{series.id}」装配为成片交付单（仅合成交付，不代做人工审片签收）。",
        "series_id": series.id,
        "series_status": series.status,
        "blocked": bool(occupancy["busy"] or not_done),
        "blocked_code": occupancy["code"] if occupancy["busy"] else ("PRODUCTION_SERIES_NOT_COMPLETED" if not_done else None),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "调用既有 assemble_completed_series，acceptance_status 始终保持待人工签收。",
    }


def _delivery_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import assemble_series_delivery

    pid = str(row.project_id or "")
    sid = str(params.get("series_id") or "")
    try:
        res = assemble_series_delivery(db, project_id=pid, series_id=sid, actor=actor)
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("id") or sid)
    row.status = "DELEGATED" if res.get("status") in {"QUEUED", "ASSEMBLING"} else "SUCCEEDED"
    row.verification = {
        "outcome": "PENDING" if row.status == "DELEGATED" else "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/deliveries",
        "delivery_order": res,
        "checked_at": utcnow().isoformat(),
    }


def _delivery_readback(db, settings, row) -> None:
    if row.status != "DELEGATED" or not row.original_task_id:
        return
    from shine.media.delivery_models import DeliveryOrder

    order = db.get(DeliveryOrder, str(row.original_task_id))
    if order is None:
        row.status = "NEEDS_REVIEW"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    row.verification = {**(row.verification or {}), "order_status": order.status, "checked_at": utcnow().isoformat()}
    if order.status in {"DELIVERED", "COMPLETED", "READY"}:
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
    elif order.status == "FAILED":
        row.status = "FAILED"
        row.last_error = f"成片装配失败：{order.failure_code or order.failure_note or 'FAILED'}"
        row.verification["outcome"] = "FAILED"
    db.commit()


def _reconcile_resolve_project(db, params: dict[str, Any], canonical: str | None) -> str | None:
    if params.get("run_id"):
        from shine.production.models import ProductionRun

        run = db.get(ProductionRun, str(params["run_id"]))
        if run is None:
            raise ActionInvalid("找不到要对账的生产运行。")
        if canonical and canonical != run.project_id:
            raise ActionInvalid("生产运行不属于当前项目。")
        return run.project_id
    if params.get("material_run_id"):
        from shine.materialprep.models import MaterialRun

        mrun = db.get(MaterialRun, str(params["material_run_id"]))
        if mrun is None:
            raise ActionInvalid("找不到要对账的素材生成批次。")
        if canonical and canonical != mrun.project_id:
            raise ActionInvalid("素材批次不属于当前项目。")
        return mrun.project_id
    return canonical


def _reconcile_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    if params.get("run_id"):
        from shine.production.models import ProductionRun

        run = db.get(ProductionRun, str(params["run_id"]))
        if run is None:
            return {}, "PRODUCTION_RUN_NOT_FOUND"
        return {"run_id": run.id, "status": run.status, "state_version": run.state_version}, None
    if params.get("material_run_id"):
        from shine.materialprep.models import MaterialRun

        mrun = db.get(MaterialRun, str(params["material_run_id"]))
        if mrun is None:
            return {}, "MATERIAL_RUN_NOT_FOUND"
        return {"material_run_id": mrun.id, "status": mrun.status, "state_version": mrun.state_version}, None
    return {}, "RECONCILE_TARGET_REQUIRED"


def _reconcile_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": "只读取并核对运行或素材批次的实际结果（绝不盲目重新提交 UNKNOWN 外部作业）。",
        "run_id": params.get("run_id"),
        "material_run_id": params.get("material_run_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "安全对账：只读核验提交凭证与实际完成状态。",
    }


def _reconcile_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.production.runner import reconcile_production_task
    from shine.support.models import SupportAction

    action_id = row.id
    pid = row.project_id
    db.rollback()
    try:
        res = reconcile_production_task(
            session_factory, db,
            project_id=pid,
            run_id=params.get("run_id"),
            material_run_id=params.get("material_run_id"),
            expected_state_version=params.get("expected_state_version"),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    db.expire_all()
    fresh = db.get(SupportAction, action_id) or row
    fresh.original_task_id = str(res.get("run_id") or res.get("material_run_id") or "") or None
    fresh.status = "SUCCEEDED"
    fresh.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/production-runs/{run_id}",
        "result": res,
        "facts_hash": json_sha256(res),
        "checked_at": utcnow().isoformat(),
    }


def _stale_recover_resolve_project(db, params: dict[str, Any], canonical: str | None) -> str | None:
    from shine.production.models import ProductionRun

    run = db.get(ProductionRun, str(params.get("run_id") or ""))
    if run is None:
        raise ActionInvalid("找不到要恢复的生产运行。")
    if canonical and canonical != run.project_id:
        raise ActionInvalid("生产运行不属于当前项目。")
    return run.project_id


def _stale_recover_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.production.models import ProductionRun

    run = db.get(ProductionRun, str(params.get("run_id") or ""))
    if run is None:
        return {}, "PRODUCTION_RUN_NOT_FOUND"
    return {"run_id": run.id, "status": run.status, "state_version": run.state_version}, None


def _stale_recover_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"对卡死/中断的生产运行「{params.get('run_id')}」执行安全对账并恢复已确认未提交的队列。",
        "run_id": params.get("run_id"),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "先对账再按原 resume_run 恢复；任何 UNKNOWN 外部请求保持隔离，不重提。",
    }


def _stale_recover_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.production.runner import recover_stale_run
    from shine.support.models import SupportAction

    pid = str(row.project_id or "")
    rid = str(params.get("run_id") or "")
    action_id = row.id
    db.rollback()
    try:
        res = recover_stale_run(session_factory, db, project_id=pid, run_id=rid, actor=actor)
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    db.expire_all()
    fresh = db.get(SupportAction, action_id) or row
    fresh.original_task_id = rid
    fresh.status = "SUCCEEDED"
    fresh.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/production-runs/{run_id}",
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


register_handler(
    "workflow_user_copy",
    ActionHandler(
        target_facts=_workflow_copy_target_facts,
        preflight=_workflow_copy_preflight,
        execute=_workflow_copy_execute,
        rollback=_workflow_copy_rollback,
        resolve_project=_workflow_copy_resolve_project,
    ),
)
register_handler(
    "workflow_graph_register",
    ActionHandler(
        target_facts=_workflow_graph_target_facts,
        preflight=_workflow_graph_preflight,
        execute=_workflow_graph_execute,
        readback=_workflow_graph_readback,
    ),
)
register_handler("director_package_create", ActionHandler(target_facts=_package_target_facts, preflight=_package_preflight, execute=_package_execute))
register_handler(
    "production_start",
    ActionHandler(
        target_facts=_production_start_target_facts,
        preflight=_production_start_preflight,
        execute=_production_start_execute,
        readback=_production_start_readback,
        validate_scope=_production_start_validate_scope,
    ),
)
register_handler(
    "production_control",
    ActionHandler(
        target_facts=_production_control_target_facts,
        preflight=_production_control_preflight,
        execute=_production_control_execute,
        resolve_project=_production_control_resolve_project,
    ),
)
register_handler("shot_rerender", ActionHandler(target_facts=_rerender_target_facts, preflight=_rerender_preflight, execute=_rerender_execute, readback=_rerender_readback))
register_handler("episode_delivery_assemble", ActionHandler(target_facts=_delivery_target_facts, preflight=_delivery_preflight, execute=_delivery_execute, readback=_delivery_readback))
register_handler("task_reconcile", ActionHandler(target_facts=_reconcile_target_facts, preflight=_reconcile_preflight, execute=_reconcile_execute, resolve_project=_reconcile_resolve_project))
register_handler("stale_run_recover", ActionHandler(target_facts=_stale_recover_target_facts, preflight=_stale_recover_preflight, execute=_stale_recover_execute, resolve_project=_stale_recover_resolve_project))

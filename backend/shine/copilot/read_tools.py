"""Read-only investigation tools (V3.1 closeout → V4 full-chain diagnostics).

The autonomous loop and the daily chat may *look* before they write or answer.
These tools add no new authority: every handler calls an existing reader
(setup verdict/summary, resource discovery, the live renderer's object_info,
the director overview, the material ledger, the asset centre, the H3 request
archive, the production run ledger, the audit trail, the original
SupportAction readback).  A read never creates a SupportAction, never starts a
renderer, never downloads, and never mutates anything.

Inside a task grant a read is still counted against the grant's tool-call
budget and refused when it names a project outside the frozen scope.  In the
daily chat (no grant) the session's project is the scope.

The menu is derived from this module alone, and the write menu stays derived
from ``copilot.declarations`` — the two can never disagree about what exists.
"""
from __future__ import annotations

import time
from typing import Any

from shine.copilot import task_grant as grant_contract
from shine.llm.tool_calls import ToolSpec
from shine.models import utcnow

#: Bounds keep one tool result inside the conversation's context budget.
MAX_ROWS = 40
MAX_TEXT = 600

#: name -> (description, JSON schema). Handlers live in :func:`execute`.
READ_TOOL_DEFINITIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "inspect_environment": (
        "只读读取本机 Setup verdict、各能力就绪事实（缺件数/层级）与本机显存档位建议；"
        "环境是否就绪只以这份裁决为准。",
        {"type": "object", "additionalProperties": False,
         "properties": {"project_id": {"type": "string", "maxLength": 36}}},
    ),
    "read_resource_inventory": (
        "只读读取资源检索台账与已发现的 ComfyUI 安装清单（含受管/收编状态）。",
        {"type": "object", "additionalProperties": False, "properties": {}},
    ),
    "read_node_inventory": (
        "只读读取当前绑定渲染器的真实 object_info 节点清单；渲染器未运行时如实报告不可用，不会为读取启动渲染器。",
        {"type": "object", "additionalProperties": False,
         "properties": {"node_filter": {"type": "string", "maxLength": 80}}},
    ),
    "inspect_project_overview": (
        "只读读取项目当前阶段：分集方案/风格冻结状态、每集状态与下一步、活跃生产运行、"
        "分镜底盘是否因风格或方案变更而过期（stale_for_basis）。回答「项目走到哪一步/下一步做什么」时用。",
        {"type": "object", "additionalProperties": False,
         "properties": {"project_id": {"type": "string", "maxLength": 36}}},
    ),
    "inspect_literary_and_story": (
        "只读读取项目文学产物：故事大纲、故事圣经、实体目录（人物/场景/道具档案）、分镜书的存在与版本。"
        "核查剧本角色档案、世界观设定、时代与道具定义时用。",
        {"type": "object", "additionalProperties": False,
         "properties": {
             "project_id": {"type": "string", "maxLength": 36},
             "kind": {"type": "string", "maxLength": 64,
                      "description": "可选：只看某一种产物，例如 outline / bible / entity_catalog / storyboard_book"},
         }},
    ),
    "inspect_shots_and_bindings": (
        "逐镜只读查看：镜号、时长、台词、连续性模式（last_frame/CUT）、绑定的参考资产、"
        "最新渲染 Job 状态与 failure_code。可按集或单镜过滤。",
        {"type": "object", "additionalProperties": False,
         "properties": {
             "project_id": {"type": "string", "maxLength": 36},
             "episode_id": {"type": "string", "maxLength": 36},
             "shot_id": {"type": "string", "maxLength": 36},
             "include_failed_only": {"type": "boolean"},
         }},
    ),
    "inspect_shot_prompt_and_evidence": (
        "读取指定镜头最新渲染请求里真实归档的 H3 编译提示词、编译器版本、参考图槽位（label/asset/use_as）"
        "与各 attempt 的结果证据。排查串角色/串道具/接拍冲突时用。",
        {"type": "object", "additionalProperties": False,
         "properties": {
             "project_id": {"type": "string", "maxLength": 36},
             "shot_id": {"type": "string", "maxLength": 36},
             "job_id": {"type": "string", "maxLength": 36},
         },
         "required": ["shot_id"]},
    ),
    "inspect_materials_and_loras": (
        "只读读取项目素材需求清单（每项的族/实体/绑定主素材/缺失原因）、D12 冻结镜回绑投影、"
        "本机已安装的 H3/Qwen LoRA 台账与画风配方、角色五视图定妆照就绪状态。",
        {"type": "object", "additionalProperties": False,
         "properties": {"project_id": {"type": "string", "maxLength": 36}}},
    ),
    "inspect_asset_center": (
        "只读读取素材中心资产库、衍生版本（含 16:9 定妆照子图）与近期生图任务状态/错误码。"
        "可按关键词检索资产。",
        {"type": "object", "additionalProperties": False,
         "properties": {
             "project_id": {"type": "string", "maxLength": 36},
             "query": {"type": "string", "maxLength": 80},
             "asset_id": {"type": "string", "maxLength": 36},
         }},
    ),
    "read_production_run": (
        "只读读取一次生产运行的状态、逐集/逐镜通过数、分集交付与成片交付单。",
        {"type": "object", "additionalProperties": False,
         "properties": {"run_id": {"type": "string", "maxLength": 36}},
         "required": ["run_id"]},
    ),
    "inspect_deliveries_and_library": (
        "只读读取成片交付历史（v1/v2 交付单、解码 QC 状态、人工签收 acceptance_status）与成片库条目。",
        {"type": "object", "additionalProperties": False,
         "properties": {"project_id": {"type": "string", "maxLength": 36}}},
    ),
    "inspect_leases_and_workers": (
        "只读读取 GPU 租约台账（ACTIVE/RECONCILE_REQUIRED）、后台 Worker 实例心跳、"
        "受管渲染器进程状态（RUNNING/STOPPED/GONE/UNVERIFIED）与资源占用裁决。"
        "诊断卡死租约/离线 Worker/僵尸渲染器时用。",
        {"type": "object", "additionalProperties": False, "properties": {}},
    ),
    "read_recent_audit_events": (
        "回溯最近的关键业务审计事件（按项目/实体类型/事件类型过滤），含失败上下文。最多 30 条。",
        {"type": "object", "additionalProperties": False,
         "properties": {
             "project_id": {"type": "string", "maxLength": 36},
             "entity_type": {"type": "string", "maxLength": 32},
             "entity_id": {"type": "string", "maxLength": 36},
             "event_type": {"type": "string", "maxLength": 64},
             "failures_only": {"type": "boolean"},
         }},
    ),
    "read_original_task": (
        "回读一个已绑定原任务的最新权威结果（绝不重新提交）。",
        {"type": "object", "additionalProperties": False,
         "properties": {"action_id": {"type": "string", "maxLength": 36}},
         "required": ["action_id"]},
    ),
}

#: The conversation-level menu is the full chain; ``read_original_task`` only
#: makes sense once a write happened, so the chat offers the other 13 by
#: default and the tool loop adds it when a pending action exists.
CHAT_READ_TOOLS: tuple[str, ...] = tuple(READ_TOOL_DEFINITIONS)


def read_tool_specs(names: tuple[str, ...] | None = None) -> list[ToolSpec]:
    return [
        ToolSpec(name=name, description=description, parameters=schema)
        for name, (description, schema) in READ_TOOL_DEFINITIONS.items()
        if names is None or name in names
    ]


def is_read_tool(name: str) -> bool:
    return name in READ_TOOL_DEFINITIONS


def _bounded(value: Any, limit: int = MAX_TEXT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _bounded_receipt(name: str, facts: dict[str, Any], *, authority: str) -> dict[str, Any]:
    return {
        "status": "OK",
        "capability_id": name,
        "tool_call_id": None,
        "grant_id": None,
        "action_id": None,
        "original_task_id": None,
        "observed_facts": facts,
        "authority": authority,
        "resource_usage": {},
        "next_available_actions": [],
        "error_code": None,
        "message": "",
        "read_only": True,
        "checked_at": utcnow().isoformat(),
    }


def _scope_project(arguments: dict[str, Any], *, grant: dict[str, Any] | None,
                   scope_project_id: str | None) -> str | None:
    """Resolve and scope-check the project a read names.

    Inside a grant the project must be inside the frozen scope.  In chat the
    session's project is the scope: a read may not look at another project.
    """
    project_id = str(arguments.get("project_id") or "") or None
    if grant is not None:
        scoped = (grant.get("scope") or {}).get("project_ids") or []
        if not project_id and len(scoped) == 1:
            project_id = scoped[0]
        if project_id:
            grant_contract.assert_project_scope(grant, project_id=project_id, capability_id="read")
        return project_id
    if project_id and scope_project_id and project_id != scope_project_id:
        raise grant_contract.GrantError(
            "SCOPE_EXCEEDED", "本次对话只绑定当前项目，不能读取其它项目。",
            details={"project_id": project_id, "scope_project_id": scope_project_id},
        )
    return project_id or scope_project_id


def _require_project(db, project_id: str | None):
    from shine.models import Project

    if not project_id:
        raise grant_contract.GrantError("PROJECT_REQUIRED", "这个只读工具需要指定项目。")
    project = db.get(Project, project_id)
    if project is None:
        raise grant_contract.GrantError("PROJECT_NOT_FOUND", "找不到这个项目。",
                                        details={"project_id": project_id})
    return project


def execute(
    db,
    settings,
    *,
    name: str,
    arguments: dict[str, Any],
    grant: dict[str, Any] | None = None,
    scope_project_id: str | None = None,
) -> dict[str, Any]:
    """Run one read-only tool inside the grant's (or the chat session's) scope.

    Refusals are named.  The returned receipt carries ``elapsed_ms`` so the
    UI can show the real cost of each investigation step.
    """
    started = time.perf_counter()
    if not is_read_tool(name):
        raise grant_contract.GrantError(
            "TOOL_NOT_DECLARED", "这个只读工具不存在。", details={"tool": name}
        )
    arguments = dict(arguments or {})
    if grant is not None:
        project_id = str(arguments.get("project_id") or "") or None
        if project_id:
            grant_contract.assert_project_scope(grant, project_id=project_id, capability_id=name)

    handler = _HANDLERS[name]
    receipt = handler(db, settings, arguments, grant=grant, scope_project_id=scope_project_id)
    receipt["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return receipt


# ---------------------------------------------------------------------------
# Handlers.  Each one reads an existing authority and nothing else.
# ---------------------------------------------------------------------------


def _inspect_environment(db, settings, arguments, *, grant, scope_project_id):
    from shine.comfyops.capability import image_vram_advice_for_host
    from shine.comfyops.setup_summary import summary as setup_summary

    project_id = str(arguments.get("project_id") or "") or None
    verdict = setup_summary(db, settings, project_id=project_id, discovered=[])
    capabilities = {
        key: {
            "label": row.get("label"),
            "state": row.get("state"),
            "optional": bool(row.get("optional")),
            "missing_count": row.get("missing_count"),
            "facts": row.get("facts"),
        }
        for key, row in (verdict.get("capabilities") or {}).items()
        if isinstance(row, dict)
    }
    try:
        vram = image_vram_advice_for_host()
    except Exception as exc:  # noqa: BLE001 - advice never blocks; unreadable is a named fact
        vram = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    return _bounded_receipt(
        "inspect_environment",
        {
            "verdict": verdict.get("verdict"),
            "preparation": verdict.get("preparation"),
            "capabilities": capabilities,
            "installation": (verdict.get("installation") or {}).get("selected"),
            "vram_advice": vram,
        },
        authority="GET /api/v1/setup/verdict",
    )


def _read_resource_inventory(db, settings, arguments, *, grant, scope_project_id):
    from shine.comfyops.resource_discovery import read_state
    from shine.comfyops.setup_summary import resolve_installation

    state = read_state(db)
    resolved = resolve_installation(db, settings, path=None, discovered=[])
    candidates = [
        {
            key: row.get(key)
            for key in ("instance_id", "root", "port", "managed", "adopted", "status", "source")
        }
        for row in (resolved.get("candidates") or [])[:12]
    ]
    return _bounded_receipt(
        "read_resource_inventory",
        {
            "scan": state.get("scan"),
            "verification_candidates": len(state.get("verification_candidates") or []),
            "installations": candidates,
            "choice_required": bool(resolved.get("choice_required")),
            "selected": resolved.get("selected"),
        },
        authority="shine.comfyops.resource_discovery.read_state",
    )


def _read_node_inventory(db, settings, arguments, *, grant, scope_project_id):
    from shine.comfy.client import ComfyError, ComfyUIClient
    from shine.comfyops.runtime_configuration import resolve_runtime

    try:
        effective = resolve_runtime(db, settings)
    except Exception as exc:  # noqa: BLE001 - a blocked runtime is a named fact
        return _bounded_receipt(
            "read_node_inventory",
            {"available": False, "reason": "RUNTIME_UNAVAILABLE",
             "detail": str(getattr(exc, "detail", exc))[:200]},
            authority="live /object_info (renderer not started for a read)",
        )
    client = ComfyUIClient(effective.comfyui_base_url, timeout_s=5, max_retries=0)
    try:
        info = client.object_info()
    except ComfyError as exc:
        return _bounded_receipt(
            "read_node_inventory",
            {"available": False, "reason": "OBJECT_INFO_UNAVAILABLE",
             "detail": str(exc)[:200]},
            authority="live /object_info (renderer not started for a read)",
        )
    node_filter = str(arguments.get("node_filter") or "").casefold()
    names = sorted(info)
    if node_filter:
        names = [item for item in names if node_filter in item.casefold()]
    return _bounded_receipt(
        "read_node_inventory",
        {
            "available": True,
            "node_count": len(info),
            "nodes": names[:200],
            "h3_nodes": [item for item in sorted(info)
                         if "h3" in item.casefold() or "minimax" in item.casefold()][:40],
        },
        authority="live /object_info",
    )


def _inspect_project_overview(db, settings, arguments, *, grant, scope_project_id):
    from shine.director.overview import build_overview
    from shine.director.streamline import director_storyboard_state

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    project = _require_project(db, project_id)
    overview = build_overview(db, project)
    ledger = director_storyboard_state(db, project.id)
    episodes = [
        {
            "episode_id": row.get("episode_id"),
            "order": row.get("order"),
            "title": row.get("title"),
            "state": row.get("state"),
            "state_label": row.get("state_label"),
            "confirmed_shots": (row.get("shots") or {}).get("confirmed_count"),
            "landed_shots": (row.get("shots") or {}).get("landed_count"),
            "materials_missing": (row.get("materials") or {}).get("missing_count"),
            "package": row.get("package"),
            "production": row.get("production"),
            "next_action": row.get("next_action"),
        }
        for row in (overview.get("episodes") or [])[:MAX_ROWS]
    ]
    return _bounded_receipt(
        "inspect_project_overview",
        {
            "project_id": project.id,
            "title": project.title,
            "plan": overview.get("plan"),
            "style": overview.get("style"),
            "counts": overview.get("counts"),
            "next_action": overview.get("next_action"),
            "run": overview.get("run"),
            "package": overview.get("package"),
            "episodes": episodes,
            "storyboard_ledger": {
                "task_id": ledger.get("task_id"),
                "status": ledger.get("status"),
                "error_code": ledger.get("error_code"),
                "stale_for_basis": ledger.get("stale_for_basis"),
                "basis_changed_reason": ledger.get("basis_changed_reason"),
            },
        },
        authority="GET /api/v1/projects/{project_id}/director/overview",
    )


def _inspect_literary_and_story(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.models import AgentArtifact, ProjectDocument

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    project = _require_project(db, project_id)
    wanted = str(arguments.get("kind") or "").strip() or None
    query = select(AgentArtifact).where(AgentArtifact.project_id == project.id)
    if wanted:
        query = query.where(AgentArtifact.kind == wanted)
    artifacts = list(db.scalars(query.order_by(AgentArtifact.created_at.desc()).limit(60)).all())
    latest_by_kind: dict[str, dict[str, Any]] = {}
    for row in artifacts:
        latest_by_kind.setdefault(str(row.kind), {
            "id": row.id,
            "kind": row.kind,
            "sha256": row.sha256,
            "stale": row.stale,
            "approved": row.approved_at is not None,
            "created_at": row.created_at.isoformat(),
            "summary": _artifact_summary(row),
        })
    documents = list(db.scalars(
        select(ProjectDocument).where(ProjectDocument.project_id == project.id)
        .order_by(ProjectDocument.created_at.desc()).limit(10)
    ).all())
    return _bounded_receipt(
        "inspect_literary_and_story",
        {
            "project_id": project.id,
            "current_document_id": project.current_document_id,
            "documents": [
                {"id": row.id, "filename": _bounded(row.filename, 120), "sha256": row.sha256,
                 "is_basis": row.id == project.current_document_id,
                 "text_length": len(row.text or "")}
                for row in documents
            ],
            "artifacts": list(latest_by_kind.values())[:12],
        },
        authority="agent_artifacts / project_documents ledgers",
    )


def _artifact_summary(row) -> dict[str, Any]:
    """A bounded, structural view of one artifact; never its full payload."""
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    summary: dict[str, Any] = {"keys": sorted(payload)[:24]}
    for key in ("characters", "entities", "locations", "props", "production_sections",
                "episodes", "volumes", "sections", "locked_facts", "world", "settings"):
        value = payload.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
            if value and isinstance(value[0], dict):
                summary[f"{key}_sample"] = [
                    {k: _bounded(v, 80) for k, v in item.items() if k in {
                        "id", "name", "entity_id", "title", "order", "family", "episode_id",
                        "production_section_id", "start_offset", "end_offset"}}
                    for item in value[:6] if isinstance(item, dict)
                ]
    return summary


def _inspect_shots_and_bindings(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.models import Asset, Job, JobAttempt, Shot, ShotAssetBinding

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    project = _require_project(db, project_id)
    episode_id = str(arguments.get("episode_id") or "") or None
    shot_id = str(arguments.get("shot_id") or "") or None
    failed_only = bool(arguments.get("include_failed_only"))
    query = select(Shot).where(Shot.project_id == project.id).order_by(Shot.created_at, Shot.id)
    if shot_id:
        query = query.where(Shot.id == shot_id)
    shots = list(db.scalars(query.limit(MAX_ROWS * 2)).all())
    if episode_id and not shot_id:
        # The confirmed plan names the shots of an episode; that membership is
        # the director package's own fact, never inferred from ordering.
        from shine.director.package import latest_confirmed_plan

        plan = latest_confirmed_plan(db, project.id)
        episodes = ((plan.payload or {}).get("episodes") or []) if plan is not None else []
        member_ids = {
            str(sid) for ep in episodes if ep.get("episode_id") == episode_id
            for sid in (ep.get("shot_ids") or [])
        }
        shots = [shot for shot in shots if shot.id in member_ids]
    shot_ids = [shot.id for shot in shots]
    bindings = list(db.scalars(
        select(ShotAssetBinding).where(ShotAssetBinding.shot_id.in_(shot_ids))
        .order_by(ShotAssetBinding.ordinal)
    ).all()) if shot_ids else []
    asset_ids = {link.asset_id for link in bindings}
    assets = {
        row.id: row for row in db.scalars(select(Asset).where(Asset.id.in_(list(asset_ids)))).all()
    } if asset_ids else {}
    jobs = list(db.scalars(
        select(Job).where(Job.shot_id.in_(shot_ids)).order_by(Job.created_at.desc())
    ).all()) if shot_ids else []
    latest_job: dict[str, Job] = {}
    for job in jobs:
        latest_job.setdefault(job.shot_id, job)
    attempts = list(db.scalars(
        select(JobAttempt).where(JobAttempt.job_id.in_([job.id for job in latest_job.values()]))
        .order_by(JobAttempt.attempt_no.desc())
    ).all()) if latest_job else []
    latest_attempt: dict[str, JobAttempt] = {}
    for attempt in attempts:
        latest_attempt.setdefault(attempt.job_id, attempt)
    rows = []
    for index, shot in enumerate(shots, start=1):
        job = latest_job.get(shot.id)
        attempt = latest_attempt.get(job.id) if job is not None else None
        if failed_only and not (job is not None and (job.status == "FAILED" or job.failure_code)):
            continue
        spec = shot.spec if isinstance(shot.spec, dict) else {}
        rows.append({
            "shot_id": shot.id,
            "position": index,
            "name": _bounded(shot.name, 120),
            "revision": shot.revision,
            "duration_target_s": spec.get("duration_target_s"),
            "dialogue": [
                _bounded(line.get("text") if isinstance(line, dict) else str(line), 160)
                for line in (spec.get("dialogue") or [])[:4]
            ],
            "continuity": spec.get("continuity"),
            "bindings": [
                {
                    "asset_id": link.asset_id,
                    "use_as": link.use_as,
                    "role": link.role,
                    "frame_role": link.frame_role,
                    "asset_name": _bounded(assets[link.asset_id].filename, 120)
                    if link.asset_id in assets else None,
                }
                for link in bindings if link.shot_id == shot.id
            ][:12],
            "latest_job": ({
                "job_id": job.id,
                "status": job.status,
                "failure_code": job.failure_code,
                "stale": job.stale,
                "generation_mode": job.generation_mode,
                "workflow_profile_id": job.workflow_profile_id,
                "quality_verification": job.quality_verification,
            } if job is not None else None),
            "latest_attempt": ({
                "attempt_id": attempt.id,
                "attempt_no": attempt.attempt_no,
                "status": attempt.status,
                "failure_code": attempt.failure_code,
                "reconciliation_state": attempt.reconciliation_state,
                "repair_root_cause": attempt.repair_root_cause,
                "progress": attempt.progress,
            } if attempt is not None else None),
        })
    return _bounded_receipt(
        "inspect_shots_and_bindings",
        {
            "project_id": project.id,
            "episode_id": episode_id,
            "shot_count": len(rows),
            "shots": rows[:MAX_ROWS],
        },
        authority="shots / shot_asset_bindings / jobs / job_attempts ledgers",
    )


def _inspect_shot_prompt_and_evidence(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.models import Job, JobAttempt

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    shot_id = str(arguments.get("shot_id") or "")
    job_id = str(arguments.get("job_id") or "") or None
    if not shot_id:
        raise grant_contract.GrantError("SHOT_REQUIRED", "需要指定镜头（shot_id）。")
    query = select(Job).where(Job.shot_id == shot_id)
    if project_id:
        query = query.where(Job.project_id == project_id)
    if job_id:
        query = query.where(Job.id == job_id)
    job = db.scalars(query.order_by(Job.created_at.desc()).limit(1)).first()
    if job is None:
        raise grant_contract.GrantError(
            "JOB_NOT_FOUND", "这个镜头还没有渲染请求。",
            details={"shot_id": shot_id},
        )
    request = job.request if isinstance(job.request, dict) else {}
    manifest = request.get("reference_manifest") or []
    spec = request.get("shot_spec") or {}
    attempts = list(db.scalars(
        select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no.desc()).limit(6)
    ).all())
    return _bounded_receipt(
        "inspect_shot_prompt_and_evidence",
        {
            "job_id": job.id,
            "shot_id": job.shot_id,
            "shot_revision": job.shot_revision,
            "job_status": job.status,
            "failure_code": job.failure_code,
            "generation_mode": request.get("generation_mode") or job.generation_mode,
            "compiler_version": request.get("compiler_version"),
            "rule_version": request.get("rule_version"),
            "prompt_sha256": request.get("prompt_sha256") or job.compiled_prompt_sha256,
            "manual_prompt": bool(request.get("manual_prompt")),
            "compiled_prompt": _bounded(request.get("compiled_prompt") or "", 2400),
            "compiled_prompt_sections": {
                key: _bounded(value, 400)
                for key, value in (request.get("compiled_prompt_sections") or {}).items()
            },
            "shot_spec": {
                "duration_target_s": spec.get("duration_target_s"),
                "continuity": spec.get("continuity"),
            },
            "reference_slots": [
                {
                    "label": item.get("label"),
                    "asset_id": item.get("asset_id"),
                    "use_as": item.get("use_as"),
                    "role": item.get("role"),
                    "frame_role": item.get("frame_role"),
                    "entity_id": item.get("entity_id"),
                    "ordinal": item.get("ordinal"),
                }
                for item in manifest if isinstance(item, dict)
            ][:12],
            "strategy_style_loras": ((request.get("strategy") or {}).get("style_loras")),
            "attempts": [
                {
                    "attempt_id": row.id,
                    "attempt_no": row.attempt_no,
                    "status": row.status,
                    "failure_code": row.failure_code,
                    "reconciliation_state": row.reconciliation_state,
                    "repair_root_cause": row.repair_root_cause,
                    "output_sha256": row.output_sha256,
                    "prompt_sha256": row.prompt_sha256,
                    "attempt_kind": row.attempt_kind,
                }
                for row in attempts
            ],
        },
        authority="jobs.request (archived H3 request) / job_attempts",
    )


def _inspect_materials_and_loras(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.asset_center.character_sheet import project_character_sheet_lora_from_listed
    from shine.comfyops.loras import image_lora_presets, project_style_lora_asset
    from shine.comfyops.models import AssetRequirement, StyleLoraAsset
    from shine.materialprep.service import (
        evaluate_materials,
        generation_progress,
        get_material_preferences,
        project_requirement_shot_bindings,
    )

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    project = _require_project(db, project_id)
    judged = evaluate_materials(db, project.id)
    try:
        preferences = get_material_preferences(db, project.id)
    except Exception as exc:  # noqa: BLE001
        preferences = {"error": type(exc).__name__}
    requirements = list(db.scalars(
        select(AssetRequirement).where(AssetRequirement.project_id == project.id)
    ).all())
    plan_ids = {row.plan_id for row in requirements}
    candidates = [
        {"candidate_id": candidate["candidate_id"], "requirement_id": item["requirement_id"],
         "family": item["family"], "entity_id": item["entity_id"], "generation_revision": item["generation_revision"]}
        for run in generation_progress(db, project.id)["runs"]
        for item in run["items"] for candidate in item["candidates"]
        if candidate["status"] == "READY_FOR_REVIEW"
    ]
    bindings = []
    for row in (judged.get("requirements") or [])[:MAX_ROWS]:
        requirement = next((item for item in requirements if item.id == row.get("requirement_id")), None)
        shot_ids = [str(sid) for sid in ((requirement.payload or {}).get("shot_ids") or [])] if requirement else []
        projection = None
        if shot_ids:
            try:
                projection = project_requirement_shot_bindings(
                    db, project.id, family=row.get("family"), entity_id=row.get("entity_id"),
                    current_asset_id=row.get("asset_id"), shot_ids=shot_ids,
                )
            except Exception as exc:  # noqa: BLE001
                projection = {"status": "UNREADABLE", "error": type(exc).__name__}
        bindings.append({
            **{key: row.get(key) for key in ("requirement_id", "family", "entity_id", "asset_id", "status", "reason")},
            "shot_count": len(shot_ids),
            "binding_status": (projection or {}).get("status"),
            "bound_to_current_count": (projection or {}).get("bound_to_current_count"),
            "other_asset_shot_count": len((projection or {}).get("other_asset_shots") or []),
            "unbound_shot_ids": ((projection or {}).get("unbound_shot_ids") or [])[:8],
        })
    lora_rows = list(db.scalars(select(StyleLoraAsset)).all())
    # Projected straight from the ledger with no live probe: the read never
    # contacts (let alone starts) a renderer and never syncs disk → ledger.
    loras = [
        {key: item.get(key) for key in (
            "id", "display_name", "base_model", "origin", "status", "default_strength", "trigger_word")}
        for item in (
            project_style_lora_asset(row, available_names=None, comfy_reachable=False, lora_roots=[])
            for row in lora_rows
        )
    ]
    try:
        sheet = project_character_sheet_lora_from_listed({"items": loras, "comfy_reachable": False})
    except Exception as exc:  # noqa: BLE001
        sheet = {"error": type(exc).__name__}
    return _bounded_receipt(
        "inspect_materials_and_loras",
        {
            "project_id": project.id,
            "candidate_images": candidates[:MAX_ROWS],
            "materials_ready": judged.get("materials_ready"),
            "reason": judged.get("reason"),
            "missing_count": len(judged.get("missing") or []),
            "requirements": bindings,
            "plan_ids": sorted(str(item) for item in plan_ids),
            "preferences": preferences,
            "loras": loras[:MAX_ROWS],
            "lora_bases": sorted({str(row.base_model) for row in lora_rows}),
            "image_presets": [
                {"id": preset.get("id"), "name": preset.get("name"),
                 "steps": preset.get("steps"), "cfg": preset.get("cfg")}
                for preset in (image_lora_presets().get("presets") or [])
            ],
            "character_sheet_lora": sheet,
        },
        authority="materialprep.service.evaluate_materials / style_lora_assets ledger",
    )


def _inspect_asset_center(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import or_, select

    from shine.asset_center.models import LibraryAsset, LibraryProjectLink, LibraryRenderTask, LibraryVersion
    from shine.asset_center.service import asset_data

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    query_text = str(arguments.get("query") or "").strip().casefold()
    asset_id = str(arguments.get("asset_id") or "") or None
    if asset_id:
        asset = db.get(LibraryAsset, asset_id)
        if asset is None:
            raise grant_contract.GrantError("ASSET_NOT_FOUND", "找不到这个素材中心资产。",
                                            details={"asset_id": asset_id})
        versions = list(db.scalars(
            select(LibraryVersion).where(LibraryVersion.library_asset_id == asset.id)
            .order_by(LibraryVersion.ordinal.desc()).limit(20)
        ).all())
        return _bounded_receipt(
            "inspect_asset_center",
            {
                "asset": asset_data(db, asset, include_versions=True),
                "versions": [
                    {"id": row.id, "ordinal": row.ordinal, "kind": row.kind, "status": row.status,
                     "source_mode": row.source_mode, "width": row.width, "height": row.height,
                     "created_at": row.created_at.isoformat(),
                     "approved": row.approved_at is not None,
                     "is_mock": bool(row.is_mock)}
                    for row in versions
                ],
            },
            authority="GET /api/v1/asset-center/assets/{asset_id}",
        )
    query = select(LibraryAsset).where(LibraryAsset.status == "ACTIVE")
    if project_id:
        # Project-linked assets come from the assignment ledger; an unassigned
        # library asset has no project and is visible everywhere.
        linked_versions = select(LibraryProjectLink.library_version_id).where(
            LibraryProjectLink.project_id == project_id
        )
        linked_assets = select(LibraryVersion.library_asset_id).where(
            LibraryVersion.id.in_(linked_versions)
        )
        unlinked = ~LibraryAsset.id.in_(select(LibraryVersion.library_asset_id))
        query = query.where(or_(LibraryAsset.id.in_(linked_assets), unlinked))
    if query_text:
        query = query.where(or_(LibraryAsset.title.ilike(f"%{query_text}%"),
                                LibraryAsset.category.ilike(f"%{query_text}%")))
    assets = list(db.scalars(query.order_by(LibraryAsset.updated_at.desc()).limit(MAX_ROWS)).all())
    task_query = select(LibraryRenderTask).order_by(LibraryRenderTask.created_at.desc())
    tasks = list(db.scalars(task_query.limit(20)).all())
    return _bounded_receipt(
        "inspect_asset_center",
        {
            "assets": [
                {
                    "id": row.id,
                    "title": _bounded(row.title, 120),
                    "kind": row.kind,
                    "category": row.category,
                    "current_version_id": row.current_version_id,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in assets
            ],
            "recent_image_tasks": [
                {"task_id": task.id, "kind": task.kind, "mode": task.mode, "status": task.status,
                 "error_code": task.error_code, "external_outcome": task.external_outcome,
                 "result_version_id": task.result_version_id,
                 "created_at": task.created_at.isoformat()}
                for task in tasks
            ],
        },
        authority="GET /api/v1/asset-center/assets; GET /api/v1/asset-center/image-jobs",
    )


def _read_production_run(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.production.models import ProductionRun, ProductionRunEpisode

    run_id = str(arguments.get("run_id") or "")
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise grant_contract.GrantError(
            "TARGET_FACTS_UNAVAILABLE", "找不到这次生产运行。", details={"run_id": run_id}
        )
    if grant is not None:
        grant_contract.assert_project_scope(grant, project_id=run.project_id, capability_id="read")
    elif scope_project_id and run.project_id != scope_project_id:
        raise grant_contract.GrantError(
            "SCOPE_EXCEEDED", "这次生产运行不属于当前对话绑定的项目。",
            details={"run_id": run_id},
        )
    episodes = list(db.scalars(
        select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run.id)
    ).all())
    return _bounded_receipt(
        "read_production_run",
        {
            "run_id": run.id,
            "run_status": run.status,
            "project_id": run.project_id,
            "package_id": getattr(run, "package_id", None),
            "state_version": run.state_version,
            "episode_ids": list(getattr(run, "episode_ids", None) or []),
            "episodes": [
                {"episode_id": item.episode_id, "status": item.status,
                 "shot_count": len(item.shot_ids or []),
                 "delivery_order_id": item.delivery_order_id}
                for item in episodes
            ],
        },
        authority="GET /api/v1/production-runs/{run_id}",
    )


def _inspect_deliveries_and_library(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.models import MediaArtifact

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    project = _require_project(db, project_id)
    from shine.api_delivery import list_deliveries, list_delivery_orders
    from shine.api_library import VIDEO_KINDS

    deliveries = list_deliveries(project.id, db)
    orders = list_delivery_orders(project.id, db)
    # The library's own ledger rows (the same MediaArtifact table the
    # /video-library route projects), bounded; no URL/asset-root is needed.
    videos = list(db.scalars(
        select(MediaArtifact).where(
            MediaArtifact.project_id == project.id,
            MediaArtifact.artifact_kind.in_(VIDEO_KINDS),
            MediaArtifact.deleted_at.is_(None),
        ).order_by(MediaArtifact.created_at.desc()).limit(MAX_ROWS)
    ).all())
    return _bounded_receipt(
        "inspect_deliveries_and_library",
        {
            "project_id": project.id,
            "deliveries": deliveries[:MAX_ROWS],
            "delivery_orders": orders[:MAX_ROWS],
            "video_library": [
                {"artifact_id": row.id, "kind": row.artifact_kind, "job_id": row.job_id,
                 "created_at": row.created_at.isoformat()}
                for row in videos
            ],
        },
        authority="GET /api/v1/projects/{project_id}/deliveries; GET /api/v1/projects/{project_id}/video-library",
    )


def _inspect_leases_and_workers(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_release import _record_key, describe_managed_processes
    from shine.models import WorkerInstance
    from shine.support.occupancy import occupancy_facts, resource_occupancy

    occupancy = resource_occupancy(db)
    leases = list(db.scalars(
        select(ResourceLease).where(ResourceLease.status.in_(("ACTIVE", "RECONCILE_REQUIRED")))
    ).all())
    workers = list(db.scalars(select(WorkerInstance)).all())
    return _bounded_receipt(
        "inspect_leases_and_workers",
        {
            "occupancy": occupancy,
            "facts": occupancy_facts(db),
            "leases": [
                {"id": row.id, "owner": str(row.owner)[:120], "status": row.status,
                 "budget_mb": row.budget_mb,
                 "expires_at": row.expires_at.isoformat() if row.expires_at else None}
                for row in leases[:20]
            ],
            "workers": [
                {"worker_id": row.worker_id, "status": row.status, "pid": row.pid,
                 "current_phase": row.current_phase, "last_error": _bounded(row.last_error, 200),
                 "last_heartbeat_at": row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None}
                for row in workers[:10]
            ],
            "managed_processes": [
                {"key": _record_key(row), **{k: row.get(k) for k in (
                    "kind", "id", "stage", "state", "port", "pid", "lease_status", "in_flight",
                    "job_id", "job_status", "ownership_evidence")}}
                for row in describe_managed_processes(db)[:8] if isinstance(row, dict)
            ],
        },
        authority="resource_leases / worker_instances / GET /api/v1/setup/managed-processes",
    )


def _read_recent_audit_events(db, settings, arguments, *, grant, scope_project_id):
    from sqlalchemy import select

    from shine.models import AuditEvent

    project_id = _scope_project(arguments, grant=grant, scope_project_id=scope_project_id)
    entity_type = str(arguments.get("entity_type") or "") or None
    entity_id = str(arguments.get("entity_id") or "") or None
    event_type = str(arguments.get("event_type") or "") or None
    failures_only = bool(arguments.get("failures_only"))
    query = select(AuditEvent)
    if project_id:
        query = query.where(AuditEvent.project_id == project_id)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    if failures_only:
        query = query.where(AuditEvent.event_type.ilike("%FAIL%") | AuditEvent.event_type.ilike("%REFUSED%")
                           | AuditEvent.event_type.ilike("%BLOCKED%"))
    rows = list(db.scalars(query.order_by(AuditEvent.created_at.desc()).limit(30)).all())
    return _bounded_receipt(
        "read_recent_audit_events",
        {
            "count": len(rows),
            "events": [
                {
                    "event_type": row.event_type,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "actor": row.actor,
                    "created_at": row.created_at.isoformat(),
                    "payload": {key: _bounded(value, 200) for key, value in (row.payload or {}).items()
                                if key in {"code", "error", "message", "reason", "outcome", "status",
                                           "stop_reason", "kind", "failure_code", "detail"}},
                }
                for row in rows
            ],
        },
        authority="audit_events ledger",
    )


def _read_original_task(db, settings, arguments, *, grant, scope_project_id):
    from shine.support.action_handlers import ActionBlocked, ActionUnknown
    from shine.support.actions import read_under_grant

    action_id = str(arguments.get("action_id") or "")
    if not action_id:
        raise grant_contract.GrantError(
            "INTENT_PARAMS_MALFORMED", "read_original_task 需要 action_id。"
        )
    try:
        result = read_under_grant(
            db, settings, action_id=action_id,
            grant_id=str((grant or {}).get("grant_id") or ""),
        )
    except (ActionUnknown, ActionBlocked) as exc:
        raise grant_contract.GrantError(exc.code, exc.message, details=exc.details) from exc
    return {
        **_bounded_receipt("read_original_task", result.get("observed_facts") or {},
                           authority=str(result.get("authority") or "")),
        "status": str(result.get("status") or ""),
        "action_id": action_id,
        "original_task_id": result.get("original_task_id"),
        "error_code": result.get("error_code"),
        "message": str(result.get("message") or ""),
    }


_HANDLERS: dict[str, Any] = {
    "inspect_environment": _inspect_environment,
    "read_resource_inventory": _read_resource_inventory,
    "read_node_inventory": _read_node_inventory,
    "inspect_project_overview": _inspect_project_overview,
    "inspect_literary_and_story": _inspect_literary_and_story,
    "inspect_shots_and_bindings": _inspect_shots_and_bindings,
    "inspect_shot_prompt_and_evidence": _inspect_shot_prompt_and_evidence,
    "inspect_materials_and_loras": _inspect_materials_and_loras,
    "inspect_asset_center": _inspect_asset_center,
    "read_production_run": _read_production_run,
    "inspect_deliveries_and_library": _inspect_deliveries_and_library,
    "inspect_leases_and_workers": _inspect_leases_and_workers,
    "read_recent_audit_events": _read_recent_audit_events,
    "read_original_task": _read_original_task,
}

__all__ = [
    "CHAT_READ_TOOLS",
    "READ_TOOL_DEFINITIONS",
    "execute",
    "is_read_tool",
    "read_tool_specs",
]

"""AI Copilot (Shine AI 管家) API.

Read endpoints project existing facts.  Mutating endpoints require the loopback
operator header used by the rest of the personal-desktop app, and every action
still needs a preview plus an explicit approval before an existing executor is
called.  There is no endpoint that writes source code, runs a shell, creates a
Git patch, or submits an Issue/PR.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from shine.api_common import DBSession, app_settings, llm_gateway
from shine.api_operator import Operator
from shine.config import Settings
from shine.models import utcnow
from shine.support import actions as action_service
from shine.support import agent as agent_service
from shine.support import capability_catalog, official_docs, project_knowledge
from shine.support import contracts as contract_registry
from shine.support import delegation as delegation_service
from shine.support import parameters as parameter_service
from shine.support import planner as planner_service
from shine.support import reports as report_service
from shine.support.context import build_evidence_snapshot
from shine.support.diagnostics import diagnose_snapshot, suggested_questions
from shine.support.knowledge import local_version_facts, search_local, search_official_web
from shine.support.memory import profile as memory_profile
from shine.support.models import SupportAction, SupportReport
from shine.support.tools import ToolDenied, read_tool_names

router = APIRouter(prefix="/api/v1/support", tags=["ai-copilot"])
SettingsDep = Annotated[Settings, Depends(app_settings)]
GatewayDep = Annotated[object, Depends(llm_gateway)]


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(default="unknown", max_length=48)
    project_id: str | None = Field(default=None, max_length=36)
    episode_id: str | None = Field(default=None, max_length=80)
    job_id: str | None = Field(default=None, max_length=36)
    attempt_id: str | None = Field(default=None, max_length=36)
    task_id: str | None = Field(default=None, max_length=80)
    error_code: str | None = Field(default=None, max_length=80)
    question: str = Field(default="", max_length=4000)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    #: Optional rebind to a NEW fault context. A session created for one
    #: failure must not swallow the next one: when the page reports a new
    #: job/attempt/error, the bindings travel with the message and the next
    #: snapshot reads the new facts. An explicit null clears that binding.
    page_id: str | None = Field(default=None, max_length=48)
    job_id: str | None = Field(default=None, max_length=36)
    attempt_id: str | None = Field(default=None, max_length=36)
    task_id: str | None = Field(default=None, max_length=80)
    error_code: str | None = Field(default=None, max_length=80)


class ActionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Validated against the packaged capability contracts, never a second
    #: hand-kept literal list (V2.1).
    kind: str = Field(min_length=1, max_length=48)
    params: dict = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)


class PlanPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=4000)
    project_id: str | None = Field(default=None, max_length=36)
    episode_ids: list[str] = Field(default_factory=list, max_length=32)
    session_id: str | None = Field(default=None, max_length=36)


class ParameterPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=120)
    values: dict = Field(default_factory=dict)
    project_id: str | None = Field(default=None, max_length=36)
    name: str | None = Field(default=None, max_length=120)
    episode_ids: list[str] = Field(default_factory=list, max_length=32)
    session_id: str | None = Field(default=None, max_length=36)


class ParameterConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=36)
    preview_hash: str = Field(min_length=16, max_length=64)
    confirm: bool = False


class ActionApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_hash: str = Field(min_length=16, max_length=64)
    confirm: bool = False


class PlanDelegateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    #: The exact plan the person previewed.  A button rendered for an older
    #: plan must never authorize the newest one.
    plan_id: str | None = Field(default=None, max_length=48)
    plan_hash: str | None = Field(default=None, max_length=64)
    approved_orders: list[int] = Field(min_length=1, max_length=8)
    budget: dict = Field(default_factory=dict)
    confirm: bool = False


class PlanDelegateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, max_length=36)
    kind: Literal["BLOCKER", "BUG", "REQUIREMENT"] = "BLOCKER"
    title: str | None = Field(default=None, max_length=160)
    notes: str = Field(default="", max_length=2000)


class PreferenceUpdate(BaseModel):
    """V2.2 unified profile; the original ``web_search`` field stays compatible."""

    model_config = ConfigDict(extra="forbid")
    web_search: bool | None = None
    assistant_nickname: str | None = Field(default=None, max_length=20)
    user_salutation: str | None = Field(default=None, max_length=20)
    startup_memory: str | None = Field(default=None, max_length=4000)
    memory_enabled: bool | None = None
    auto_memory_enabled: bool | None = None
    remote_memory_processing_consent: bool | None = None
    remote_context_consent: bool | None = None


def _http(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _identify_plan(plan: dict, episode_ids: list[str]) -> dict:
    return planner_service.identify_plan(plan, episode_ids)


def _require_session(db, session_id: str):
    row = agent_service.get_session(db, session_id)
    if row is None:
        raise _http("SESSION_NOT_FOUND", "AI 管家会话不存在或已被清理。", 404)
    return row


def _schedule_memory_extraction(
    background: BackgroundTasks,
    request: Request,
    settings: Settings,
    db,
    session,
    gateway,
) -> None:
    """Queue a batched distillation after the answer, never before it."""
    from shine.support import memory as memory_service

    try:
        due = memory_service.auto_extract_due(db, session, gateway=gateway)
    except Exception:  # noqa: BLE001 - memory must never break a chat turn
        return
    if due.get("due"):
        background.add_task(
            memory_service.extract_background,
            request.app.state.session_factory,
            settings,
            session.id,
            gateway,
        )


@router.get("/capabilities")
def support_capabilities():
    """The callable tool list, for auditability in the UI and tests."""
    from shine.copilot import task_grant as grant_contract
    from shine.copilot import tool_registry

    return {
        "tools": read_tool_names(),
        "max_steps": 8,
        "permissions": {"read": "允许", "confirm": "需要预览并明确授权", "forbidden": "不存在"},
        "forbidden": [
            "shell",
            "git",
            "pip_install",
            "write_source",
            "write_node_source",
            "delete_user_data",
            "bypass_gate",
        ],
        # V3.1: one task-level authorization, then autonomous calls inside it.
        "task_grant": {
            "schema": grant_contract.SCHEMA,
            "capability_groups": list(grant_contract.GROUP_PERMISSIONS),
            "autonomous_capabilities": list(tool_registry.AUTONOMOUS_CAPABILITIES),
            "default_limits": dict(grant_contract.DEFAULT_LIMITS),
            "never_granted": sorted(grant_contract.PERMANENTLY_FORBIDDEN),
            "rule": "一次点击冻结范围；每个子动作仍走原 preview/approve/executor/回读。",
        },
    }


@router.get("/knowledge/project")
def support_project_knowledge(
    q: str = "",
    limit: int = 5,
    build: str | None = None,
):
    """Versioned project knowledge with citations and an explicit freshness state."""
    limit = max(1, min(int(limit), 10))
    result = project_knowledge.search(q, limit=limit, build_commit=build)
    return {
        **result,
        "answer": project_knowledge.answer(q, limit=limit, build_commit=build),
    }


@router.get("/knowledge/sources")
def support_knowledge_sources():
    """Every knowledge source, its version binding and its licence policy."""
    return {
        "project": {
            "metadata": project_knowledge.index_metadata(),
            "coverage": project_knowledge.coverage(),
        },
        "official": official_docs.sources_payload(),
        "h3_boundary": official_docs.h3_boundary(),
        "evidence_levels": list(project_knowledge.EVIDENCE_LEVELS),
        "notice": "项目百科只描述当前构建；官方资料仅作参考，不代表本机已支持。",
    }


@router.get("/capabilities/actions")
def support_capability_actions():
    return capability_catalog.catalog_payload()


@router.get("/build")
def support_build(request: Request):
    """One build identity: knowledge index, capability contracts and routes.

    This is the endpoint a person/UI checks to know whether the copilot's
    knowledge and contracts belong to this installed version.  It never
    executes anything and never scans the worktree.
    """
    identity = contract_registry.build_identity(openapi=request.app.openapi())
    live = contract_registry.live_api_digest(request.app)
    api_matches = bool(identity.get("api_schema_digest")) and identity["api_schema_digest"] == live
    state = identity["state"]
    if state == "FRESH" and not api_matches:
        state = "STALE"
        identity["reasons"].append("运行中的 API 请求合同与打包合同不一致，已按过期合同处理。")
    identity["state"] = state
    identity["api_matches"] = api_matches
    identity["live_api_digest"] = live
    identity["action_states"] = {
        row.action_id: contract_registry.execution_state(row.action_id)["state"]
        for row in capability_catalog.CAPABILITIES
    }
    identity["notice"] = (
        "AI 管家知识与能力合同已匹配当前版本。"
        if state == "FRESH"
        else "；".join(identity["reasons"]) or "无法证明知识与能力合同属于当前版本。"
    )
    return identity


@router.post("/plans/preview")
def support_plan_preview(
    body: PlanPreviewRequest,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    """Bounded read-only plan; no executor is called here."""
    session = _require_session(db, body.session_id) if body.session_id else None
    project_id = body.project_id or (session.project_id if session is not None else None)
    try:
        plan = planner_service.build_plan(
            db,
            settings,
            request=body.request,
            project_id=project_id,
            episode_ids=body.episode_ids or None,
        )
    except ValueError as exc:
        raise _http(str(exc), "计划缺少项目或分集范围。", 400) from exc
    if plan is None:
        raise _http(
            "NO_PLAN",
            "这句话没有匹配到可执行计划；可以直接提问，管家会先诊断或检索项目百科。",
            409,
        )
    identified = _identify_plan(plan, list(body.episode_ids or []))
    if session is not None:
        # Only the read-only plan, its immutable identity and the person's
        # explicit scope are stored; delegation walks this list later and still
        # previews every step.
        session.context_json = {
            **(session.context_json or {}),
            "last_plan": {**identified, "stored_at": utcnow().isoformat()},
        }
        db.commit()
    return {
        "plan": identified,
        "capabilities": {
            "version": capability_catalog.CATALOG_VERSION,
            "coverage": capability_catalog.catalog_payload()["matrix_totals"],
        },
        "notice": "计划是只读建议；每个可执行步骤仍要预览并明确确认。",
    }


@router.post("/plans/delegate")
def support_plan_delegate(
    body: PlanDelegateRequest,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    if not body.confirm:
        raise _http("CONFIRMATION_REQUIRED", "必须明确确认授权范围后才会推进计划。", 400)
    session = _require_session(db, body.session_id)
    try:
        payload = delegation_service.create(
            db,
            settings,
            request.app.state.session_factory,
            session=session,
            plan_id=body.plan_id,
            plan_hash=body.plan_hash,
            approved_orders=list(body.approved_orders),
            budget=body.budget,
            actor=actor,
            gateway=gateway,
            app=request.app,
        )
    except delegation_service.DelegationError as exc:
        raise _http(exc.code, exc.message, 409) from exc
    db.commit()
    return {"delegation": payload}


@router.get("/sessions/{session_id}/delegation")
def support_delegation_get(session_id: str, db: DBSession):
    session = _require_session(db, session_id)
    payload = delegation_service.get(db, session)
    if payload is None:
        raise _http("DELEGATION_NOT_FOUND", "当前会话还没有委托。", 404)
    return {"delegation": payload}


@router.post("/sessions/{session_id}/delegation/refresh")
def support_delegation_refresh(
    session_id: str,
    body: PlanDelegateAction,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    session = _require_session(db, session_id)
    payload = delegation_service.refresh(
        db,
        settings,
        request.app.state.session_factory,
        session=session,
        actor=actor,
        gateway=gateway,
        app=request.app,
    )
    db.commit()
    return {"delegation": payload}


@router.post("/sessions/{session_id}/delegation/cancel")
def support_delegation_cancel(session_id: str, db: DBSession):
    session = _require_session(db, session_id)
    try:
        payload = delegation_service.cancel(db=db, session=session)
    except delegation_service.DelegationError as exc:
        raise _http(exc.code, exc.message, 409) from exc
    db.commit()
    return {"delegation": payload}


@router.get("/parameters/contracts")
def support_parameter_contracts(db: DBSession, project_id: str | None = None):
    try:
        return parameter_service.contracts(db, project_id)
    except parameter_service.ParameterError as exc:
        raise _http(exc.code, exc.message, 422) from exc


@router.post("/parameters/preview")
def support_parameter_preview(
    body: ParameterPreviewRequest,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    """Validate a tuning request and create the real action preview (no write)."""
    project_id = body.project_id
    if body.session_id:
        session = _require_session(db, body.session_id)
        project_id = project_id or session.project_id
    try:
        result = parameter_service.preview_change(
            db,
            settings,
            target=body.target,
            values=body.values,
            project_id=project_id,
            name=body.name,
            episode_ids=body.episode_ids or None,
        )
    except parameter_service.ParameterError as exc:
        raise _http(exc.code, exc.message, 422) from exc
    proposed = result["preview"].get("proposed_action")
    action = None
    reused = False
    if proposed:
        try:
            action, reused = action_service.preview_action(
                db,
                settings,
                kind=proposed["kind"],
                params=proposed["params"],
                actor=actor,
                session_id=body.session_id,
                project_id=project_id,
                app=request.app,
            )
        except action_service.ActionInvalid as exc:
            raise _http(exc.code, exc.message, 400) from exc
        except action_service.ActionBlocked as exc:
            raise _http(exc.code, exc.message, 423) from exc
        except ToolDenied as exc:
            raise _http("ACTION_NOT_PERMITTED", str(exc), 403) from exc
    return {
        "parameter": result,
        "action": action,
        "reused": reused,
        "notice": result["notice"],
    }


@router.post("/parameters/confirm")
def support_parameter_confirm(
    body: ParameterConfirmRequest,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    if not body.confirm:
        raise _http("CONFIRMATION_REQUIRED", "必须明确确认后才会执行参数调整。", 400)
    try:
        action, reused = action_service.approve_action(
            db,
            settings,
            request.app.state.session_factory,
            action_id=body.action_id,
            preview_hash=body.preview_hash,
            actor=actor,
            gateway=gateway,
            app=request.app,
        )
    except action_service.ActionUnknown as exc:
        raise _http(exc.code, exc.message, 404) from exc
    except action_service.ActionStale as exc:
        raise _http(exc.code, exc.message, 409) from exc
    except action_service.ActionBlocked as exc:
        raise _http(exc.code, exc.message, 423) from exc
    return {"action": action, "reused": reused}


@router.get("/context")
def support_context(
    settings: SettingsDep,
    db: DBSession,
    page_id: str = "unknown",
    project_id: str | None = None,
    episode_id: str | None = None,
    job_id: str | None = None,
    attempt_id: str | None = None,
    task_id: str | None = None,
    error_code: str | None = None,
):
    snapshot = build_evidence_snapshot(
        db,
        settings,
        page_id=page_id,
        project_id=project_id,
        episode_id=episode_id,
        job_id=job_id,
        attempt_id=attempt_id,
        task_id=task_id,
        error_code=error_code,
    )
    return {
        "snapshot": snapshot,
        "suggested_questions": suggested_questions(snapshot["page_context"]["screen"]),
        "web_search_enabled": agent_service.web_search_enabled(db),
    }


@router.get("/knowledge")
def support_knowledge(
    settings: SettingsDep,
    db: DBSession,
    q: str = "",
    limit: int = 5,
    web: bool = False,
    code: str | None = None,
):
    limit = max(1, min(int(limit), 10))
    local = search_local(q, limit=limit)
    web_result = {"enabled": False, "results": []}
    if web:
        enabled = agent_service.web_search_enabled(db)
        if not enabled:
            raise _http("WEB_SEARCH_DISABLED", "联网检索已关闭；请先在 AI 管家中开启。", 409)
        web_result = search_official_web(
            q,
            enabled=True,
            error_code=code,
            versions=local_version_facts(db, settings),
        )
    return {"version": local[0]["knowledge_version"] if local else None, "local": local, "web": web_result}


@router.get("/preferences")
def support_preferences(db: DBSession):
    """One profile: identity, startup pre-read, privacy and the legacy switch."""
    return memory_profile.preferences_payload(db)


@router.post("/preferences")
def support_preferences_update(body: PreferenceUpdate, actor: Operator, db: DBSession):
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return memory_profile.update_preferences(db, updates=updates, actor=actor)
    except memory_profile.ProfileInvalid as exc:
        raise _http(exc.code, exc.message, 400) from exc


@router.post("/sessions", status_code=201)
def support_session_create(
    body: SessionCreate,
    request: Request,
    background: BackgroundTasks,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    session = agent_service.create_session(
        db,
        settings,
        page_id=body.page_id,
        project_id=body.project_id,
        episode_id=body.episode_id,
        job_id=body.job_id,
        attempt_id=body.attempt_id,
        task_id=body.task_id,
        error_code=body.error_code,
        actor=actor,
    )
    payload = agent_service.session_payload(db, session)
    result = None
    if body.question.strip():
        result = agent_service.handle_message(
            db, settings, session, body.question.strip(), actor=actor, gateway=gateway
        )
        payload = result["session"]
        _schedule_memory_extraction(background, request, settings, db, session, gateway)
    return {
        "session": payload,
        "diagnosis": (result or {}).get("diagnosis"),
        "suggested_questions": suggested_questions(session.page_id),
        "web_search_enabled": agent_service.web_search_enabled(db),
        "capabilities": {"max_steps": 8, "tools": read_tool_names()},
    }


@router.get("/sessions/{session_id}")
def support_session_get(session_id: str, db: DBSession):
    session = _require_session(db, session_id)
    return agent_service.session_payload(db, session)


@router.post("/sessions/{session_id}/messages")
def support_session_message(
    session_id: str,
    body: MessageCreate,
    request: Request,
    background: BackgroundTasks,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    session = _require_session(db, session_id)
    if any(
        value is not None
        for value in (body.page_id, body.job_id, body.attempt_id, body.task_id, body.error_code)
    ):
        from shine.support.context import normalise_page_id

        if body.page_id is not None:
            session.page_id = normalise_page_id(body.page_id)
        if body.job_id is not None:
            session.job_id = body.job_id or None
        if body.attempt_id is not None:
            session.attempt_id = body.attempt_id or None
        if body.task_id is not None:
            session.task_id = (body.task_id or None) and body.task_id[:80]
        if body.error_code is not None:
            session.error_code = (body.error_code or None) and body.error_code[:80]
        session.updated_at = utcnow()
    result = agent_service.handle_message(
        db, settings, session, body.content, actor=actor, gateway=gateway
    )
    _schedule_memory_extraction(background, request, settings, db, session, gateway)
    return result


@router.get("/sessions/{session_id}/events")
def support_session_events(session_id: str, db: DBSession, since: str | None = None):
    from datetime import datetime

    session = _require_session(db, session_id)
    payload = agent_service.session_payload(db, session)
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError as exc:
            raise _http("INVALID_SINCE", "since 必须是 ISO 时间。", 400) from exc
        payload["messages"] = [
            row for row in payload["messages"] if datetime.fromisoformat(row["created_at"]) > cutoff
        ]
    return payload


@router.post("/actions/preview")
def support_action_preview(
    body: ActionPreviewRequest,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    try:
        preview, reused = action_service.preview_action(
            db,
            settings,
            kind=body.kind,
            params=body.params,
            actor=actor,
            session_id=body.session_id,
            project_id=body.project_id,
            app=request.app,
        )
    except action_service.ActionInvalid as exc:
        raise _http(exc.code, exc.message, 400) from exc
    except action_service.ActionBlocked as exc:
        raise _http(exc.code, exc.message, 423) from exc
    except ToolDenied as exc:
        raise _http("ACTION_NOT_PERMITTED", str(exc), 403) from exc
    return {"action": preview, "reused": reused}


@router.post("/actions/{action_id}/approve")
def support_action_approve(
    action_id: str,
    body: ActionApproveRequest,
    request: Request,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    if not body.confirm:
        raise _http("CONFIRMATION_REQUIRED", "必须明确确认后才会执行配置动作。", 400)
    try:
        action, reused = action_service.approve_action(
            db,
            settings,
            request.app.state.session_factory,
            action_id=action_id,
            preview_hash=body.preview_hash,
            actor=actor,
            gateway=gateway,
            app=request.app,
        )
    except action_service.ActionUnknown as exc:
        raise _http(exc.code, exc.message, 404) from exc
    except action_service.ActionStale as exc:
        raise _http(exc.code, exc.message, 409) from exc
    except action_service.ActionBlocked as exc:
        raise _http(exc.code, exc.message, 423) from exc
    except ToolDenied as exc:
        raise _http("ACTION_NOT_PERMITTED", str(exc), 403) from exc
    return {"action": action, "reused": reused}


@router.get("/actions/{action_id}")
def support_action_get(action_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(SupportAction, action_id)
    if row is None:
        raise _http("ACTION_NOT_FOUND", "授权动作不存在。", 404)
    return {"action": action_service.refresh_action(db, settings, row)}


@router.post("/actions/{action_id}/rollback")
def support_action_rollback(
    action_id: str,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    try:
        action = action_service.rollback_action(db, settings, action_id=action_id, actor=actor)
    except action_service.ActionUnknown as exc:
        raise _http(exc.code, exc.message, 404) from exc
    except action_service.ActionBlocked as exc:
        raise _http(exc.code, exc.message, 409) from exc
    return {"action": action}


@router.post("/reports", status_code=201)
def support_report_create(
    body: ReportCreate,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
):
    snapshot = None
    diagnosis = None
    session = None
    attempted: list[dict] = []
    investigation: dict = {}
    references: list[dict] = []
    if body.session_id:
        session = _require_session(db, body.session_id)
        snapshot = agent_service.latest_snapshot(session)
        diagnosis = agent_service.latest_diagnosis(session)
        attempted = agent_service.attempt_actions(db, session.id)
        investigation = agent_service.latest_investigation(session)
        references = agent_service.latest_references(session)
    if not snapshot:
        snapshot = build_evidence_snapshot(db, settings, page_id="unknown")
    if not diagnosis:
        diagnosis = diagnose_snapshot(snapshot, db=db)
    title = body.title or f"{diagnosis.get('category_label')}：{diagnosis.get('symptom')}"
    try:
        row, deduplicated = report_service.generate_report(
            db,
            settings,
            kind=body.kind,
            title=title,
            diagnosis=diagnosis,
            snapshot=snapshot,
            session_id=session.id if session else None,
            project_id=(snapshot.get("page_context") or {}).get("project_id"),
            attempted_actions=attempted,
            notes=body.notes,
            investigation=investigation,
            references=references,
        )
    except ValueError as exc:
        raise _http("REPORT_REJECTED", str(exc), 400) from exc
    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "SUPPORT_REPORT_WRITTEN",
        "support_report",
        row.id,
        {"kind": row.kind, "filename": row.filename, "deduplicated": deduplicated},
        project_id=row.project_id,
    )
    db.commit()
    return {
        "report": {
            "id": row.id,
            "kind": row.kind,
            "title": row.title,
            "filename": row.filename,
            "display_path": f"{report_service.REPORT_DIRNAME}/{row.filename}",
            "sha256": row.sha256,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        },
        "deduplicated": deduplicated,
        "notice": "报告已写入本机受控目录，不会自动修改代码或提交 Issue/PR。",
    }


@router.get("/reports")
def support_reports_list(db: DBSession, limit: int = 20):
    limit = max(1, min(int(limit), 50))
    rows = db.scalars(
        select(SupportReport).order_by(SupportReport.created_at.desc()).limit(limit)
    ).all()
    return {
        "reports": [
            {
                "id": row.id,
                "kind": row.kind,
                "title": row.title,
                "filename": row.filename,
                "display_path": f"{report_service.REPORT_DIRNAME}/{row.filename}",
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.get("/reports/{report_id}")
def support_report_get(report_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(SupportReport, report_id)
    if row is None:
        raise _http("REPORT_NOT_FOUND", "报告不存在。", 404)
    try:
        return {"report": report_service.read_report(settings, row)}
    except ValueError as exc:
        raise _http("REPORT_REJECTED", str(exc), 400) from exc


@router.get("/reports/{report_id}/download", response_class=PlainTextResponse)
def support_report_download(report_id: str, settings: SettingsDep, db: DBSession):
    row = db.get(SupportReport, report_id)
    if row is None:
        raise _http("REPORT_NOT_FOUND", "报告不存在。", 404)
    try:
        data = report_service.read_report(settings, row)
    except ValueError as exc:
        raise _http("REPORT_REJECTED", str(exc), 400) from exc
    return PlainTextResponse(
        data["content"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{data["filename"]}"'},
    )


# ---------------------------------------------------------------------------
# V3.1 task-level grant: one preview, one click, autonomous execution
# ---------------------------------------------------------------------------


class TaskGrantPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    objective: str = Field(min_length=2, max_length=2000)
    project_id: str | None = Field(default=None, max_length=36)
    installation_id: str | None = Field(default=None, max_length=96)
    capability_groups: list[str] = Field(default_factory=list, max_length=8)
    limits: dict = Field(default_factory=dict)
    validity_hours: int = Field(default=24, ge=1, le=168)


class TaskGrantAuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    preview_id: str = Field(min_length=1, max_length=64)
    grant_digest: str = Field(min_length=16, max_length=64)
    confirm: bool = False
    #: When true the copilot starts working immediately after the click.  The
    #: execution itself is still bounded by the loop budgets.
    run: bool = True


class TaskGrantRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    max_steps: int = Field(default=12, ge=1, le=40)


def _grant_draft(session) -> dict:
    context = session.context_json if isinstance(session.context_json, dict) else {}
    draft = context.get("task_grant_preview")
    return dict(draft) if isinstance(draft, dict) else {}


def _store_grant_draft(db, session, draft: dict) -> None:
    context = dict(session.context_json or {})
    context["task_grant_preview"] = draft
    session.context_json = context
    session.updated_at = utcnow()
    db.commit()


def _resolve_installation(db, settings, installation_id: str | None) -> dict:
    """The original installation resolution; never a guess from a port.

    When several installations exist the candidates travel with the preview so
    the person selects ONE before the grant freezes its target — the choice is
    made once, at preview time, not silently by the model mid-task.
    """
    from shine.comfyops.setup_summary import resolve_installation

    try:
        resolved = resolve_installation(db, settings, path=None, discovered=[])
    except Exception:  # noqa: BLE001 - an unreadable registry is reported, not fatal
        return {"selected": None, "choice_required": False, "candidates": [],
                "error": "INSTALLATION_UNREADABLE"}
    selected = resolved.get("selected") or {}
    candidates = [
        {
            key: row.get(key)
            for key in ("instance_id", "root", "port", "managed", "adopted", "status", "source")
        }
        for row in (resolved.get("candidates") or [])[:12]
    ]
    if installation_id:
        for row in candidates:
            if str(row.get("instance_id") or "") == installation_id:
                return {"selected": row, "choice_required": False, "pinned": True,
                        "candidates": candidates}
        if str(selected.get("instance_id") or "") == installation_id:
            return {"selected": selected, "choice_required": False, "pinned": True,
                    "candidates": candidates}
        return {
            "selected": None,
            "choice_required": False,
            "candidates": candidates,
            "error": "INSTALLATION_NOT_FOUND",
            "requested": installation_id,
        }
    return {
        "selected": selected or None,
        "choice_required": bool(resolved.get("choice_required")),
        "candidates": candidates,
    }


def _risk_list(groups: list[str]) -> list[str]:
    risks: list[str] = []
    if "resources" in groups:
        risks.append("DISK_WRITE")
        risks.append("LARGE_DOWNLOAD")
    if "nodes" in groups:
        # A reviewed, pinned node package still brings executable code.
        risks.append("THIRD_PARTY_CODE")
    if "workflows" in groups:
        risks.append("USER_DATA_WRITE")
    if "test" in groups:
        risks.append("GPU_TEST")
    if "runtime" in groups:
        risks.append("MANAGED_PROCESS_CONTROL")
    return risks


@router.post("/task-grants/preview")
def support_task_grant_preview(
    body: TaskGrantPreviewRequest,
    settings: SettingsDep,
    gateway: GatewayDep,
    db: DBSession,
):
    """Read-only: describe the one authorization the person is about to give."""
    from shine.copilot import task_grant as grant_contract

    session = _require_session(db, body.session_id)
    project_id = body.project_id or session.project_id
    if project_id:
        from shine.services import require_project

        require_project(db, project_id)
    try:
        groups = grant_contract.normalize_groups(
            body.capability_groups or list(grant_contract.DEFAULT_GROUPS)
        )
        limits = grant_contract.normalize_limits({**body.limits} or None)
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message) from exc

    installation = _resolve_installation(db, settings, body.installation_id)
    capabilities = grant_contract.capabilities_for_groups(groups)
    blocked: list[dict] = []
    for capability_id in capabilities:
        state = contract_registry.execution_state(capability_id)
        if not state["executable"]:
            blocked.append(
                {
                    "capability_id": capability_id,
                    "state": state["state"],
                    "notice": state.get("notice"),
                }
            )

    model_info: dict = {"inherited": False}
    try:
        profile, source_role = gateway.profile_for_role_or_inherit(db, "support_agent")
        model_info = {
            "inherited": source_role != "support_agent",
            "source_role": source_role,
            "profile_name": profile.name,
            "model": profile.model,
            "kind": profile.kind,
        }
    except Exception as exc:  # noqa: BLE001 - no model is a named blocker, not a crash
        model_info = {"inherited": False, "error": type(exc).__name__}

    now = utcnow()
    from datetime import timedelta as _timedelta

    from shine.models import new_id as _new_id

    draft_id = "grantpreview-" + _new_id().replace("-", "")[:20]
    try:
        proposed = grant_contract.build_proposed_grant(
            grant_id="preview",
            session_id=session.id,
            actor_id=session.actor,
            objective=body.objective,
            project_ids=[project_id] if project_id else [],
            installation_ids=(
                [body.installation_id] if body.installation_id
                else ([str((installation.get("selected") or {}).get("instance_id"))]
                      if (installation.get("selected") or {}).get("instance_id") else [])
            ),
            allowed_resource_roots=[
                str((installation.get("selected") or {}).get("root") or "")
            ] if (installation.get("selected") or {}).get("root") else [],
            groups=groups,
            limits=limits,
            created_at=now,
            validity=_timedelta(hours=body.validity_hours),
        )
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message) from exc

    draft = {
        "preview_id": draft_id,
        "grant_digest": proposed["grant_digest"],
        "expires_at": proposed["validity"]["expires_at"],
        "objective": proposed["objective"],
        "objective_digest": proposed["objective_digest"],
        "groups": groups,
        "limits": proposed["limits"],
        "project_ids": proposed["scope"]["project_ids"],
        "installation_ids": proposed["scope"]["installation_ids"],
        "allowed_resource_roots": proposed["scope"]["allowed_resource_roots"],
        "installation_choice_required": bool(
            installation.get("choice_required") and not installation.get("selected")
        ),
        "created_at": now.replace(microsecond=0).isoformat(),
    }
    _store_grant_draft(db, session, draft)

    selection_pending = bool(draft["installation_choice_required"])
    return {
        "preview_id": draft_id,
        "schema": grant_contract.SCHEMA,
        "grant_digest": proposed["grant_digest"],
        "purpose": proposed["objective"],
        "can_authorize": bool(not blocked and not model_info.get("error") and not selection_pending),
        "selection_required": (
            ["INSTALLATION_SELECTION_REQUIRED"] if selection_pending else []
        ),
        "summary": "将在本次授权范围内自动检查资源、冻结/执行安装计划、注册并验证用户级工作流。",
        "capability_groups": groups,
        "capabilities": capabilities,
        "blocked_capabilities": blocked,
        "permissions": proposed["permissions"],
        "limits": proposed["limits"],
        "risks": _risk_list(groups),
        "installation": installation,
        "model": model_info,
        "expires_at": proposed["validity"]["expires_at"],
        "preview_expires_at": (now + grant_contract.PREVIEW_VALIDITY)
        .replace(microsecond=0)
        .isoformat(),
        "not_allowed": [
            "修改 Shine 或第三方源码",
            "执行任意 Shell / pip / git / 安装脚本",
            "删除用户素材、模型或成片",
            "跳过生产门禁或替用户宣布环境就绪",
        ],
    }


@router.post("/task-grants/authorize")
def support_task_grant_authorize(
    body: TaskGrantAuthorizeRequest,
    settings: SettingsDep,
    gateway: GatewayDep,
    db: DBSession,
):
    """The single click.  Repeating it returns the same grant, never a new one."""
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service

    session = _require_session(db, body.session_id)
    if not body.confirm:
        raise _http("CONFIRM_REQUIRED", "请确认后再授权。", status=400)
    draft = _grant_draft(session)
    if not draft:
        raise _http("GRANT_PREVIEW_REQUIRED", "请先生成授权预览，再确认本次授权。", status=409)
    if draft.get("preview_id") != body.preview_id:
        raise _http("GRANT_PREVIEW_CHANGED", "授权预览已更新，请重新确认。", status=409)
    if draft.get("installation_choice_required") and not draft.get("installation_ids"):
        raise _http(
            "INSTALLATION_SELECTION_REQUIRED",
            "检索发现多套 ComfyUI 安装；请先在预览里选定一套，再授权本次任务。",
            status=409,
        )
    try:
        preview_deadline = grant_contract.parse_iso(draft["preview_expires_at"])
    except (KeyError, ValueError):
        preview_deadline = grant_contract.parse_iso(draft["created_at"]) + grant_contract.PREVIEW_VALIDITY
    if utcnow() > preview_deadline:
        raise _http("GRANT_PREVIEW_EXPIRED", "授权预览已过期，请重新预览。", status=409)

    try:
        loaded, created = grant_service.create(
            db,
            session=session,
            actor=session.actor,
            objective=str(draft["objective"]),
            groups=draft["groups"],
            limits=draft["limits"],
            project_ids=list(draft["project_ids"]),
            installation_ids=list(draft["installation_ids"]),
            allowed_resource_roots=list(draft["allowed_resource_roots"]),
            expires_at=grant_contract.parse_iso(draft["expires_at"]),
            expected_digest=body.grant_digest,
        )
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message, status=409) from exc

    # The click ends here.  What follows is server policy, not a second consent.
    result: dict = {
        "grant_id": loaded.id,
        "delegation_id": loaded.row.id,
        "status": grant_contract.grant_status(loaded.grant, now=utcnow()),
        "status_label": grant_contract.STATUS_LABELS.get(
            grant_contract.grant_status(loaded.grant, now=utcnow()), ""
        ),
        "schema": grant_contract.SCHEMA,
        "grant_digest": loaded.grant.get("grant_digest"),
        "created": bool(created),
        "objective": loaded.grant.get("objective"),
        "limits": loaded.grant.get("limits"),
        "execution": None,
    }
    from shine.services import add_audit

    add_audit(
        db,
        session.actor,
        "SUPPORT_TASK_GRANT_AUTHORIZED",
        "support_delegation",
        loaded.row.id,
        {
            "grant_id": loaded.id,
            "grant_digest": loaded.grant.get("grant_digest"),
            "groups": draft["groups"],
            "created": bool(created),
            "objective_digest": loaded.grant.get("objective_digest"),
        },
        project_id=session.project_id,
    )
    db.commit()

    if body.run:
        result["execution"] = _run_grant(
            db, settings, gateway, session=session, grant_id=loaded.id, max_steps=12
        )
    return result


@router.get("/task-grants/{grant_id}")
def support_task_grant_get(grant_id: str, db: DBSession):
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service

    try:
        loaded = grant_service.load(db, grant_id)
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message, status=404) from exc
    return {
        **grant_contract.grant_public(loaded.grant, now=utcnow()),
        "sub_actions": grant_service.sub_action_public(loaded.grant),
        "recent_tool_calls": (loaded.grant.get("tool_calls") or [])[-12:],
        "objective_verification": (loaded.row.payload or {}).get("objective_verification"),
        "not_allowed": [
            "修改源码",
            "执行任意命令",
            "删除用户素材",
        ],
    }


@router.post("/task-grants/{grant_id}/revoke")
def support_task_grant_revoke(grant_id: str, db: DBSession):
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service

    try:
        loaded = grant_service.revoke(
            db, grant_id, actor="local-user", reason="用户撤销后续 AI 操作"
        )
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message, status=409) from exc
    from shine.services import add_audit

    add_audit(
        db,
        "local-user",
        "SUPPORT_TASK_GRANT_REVOKED",
        "support_delegation",
        loaded.row.id,
        {"grant_id": grant_id},
        project_id=None,
    )
    db.commit()
    return {
        "grant_id": grant_id,
        "status": grant_contract.grant_status(loaded.grant, now=utcnow()),
        "status_label": grant_contract.STATUS_LABELS.get(
            grant_contract.grant_status(loaded.grant, now=utcnow()), ""
        ),
        # An already submitted executor task is not cancelled by this:
        # it follows its own safe-stop rules.
        "note": "已停止后续 AI 操作；已提交给原执行器的任务不会被强制取消。",
    }


@router.post("/task-grants/{grant_id}/run")
def support_task_grant_run(
    grant_id: str,
    body: TaskGrantRunRequest,
    settings: SettingsDep,
    gateway: GatewayDep,
    db: DBSession,
):
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service

    session = _require_session(db, body.session_id)
    try:
        grant_service.load(db, grant_id)
    except grant_contract.GrantError as exc:
        raise _http(exc.code, exc.message, status=404) from exc
    return _run_grant(
        db, settings, gateway, session=session, grant_id=grant_id, max_steps=body.max_steps
    )


@router.get("/sessions/{session_id}/task-grant")
def support_session_task_grant(session_id: str, db: DBSession):
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service

    session = _require_session(db, session_id)
    loaded = grant_service.latest_for_session(db, session.id)
    if loaded is None:
        return {"grant": None, "preview": _grant_draft(session) or None}
    return {
        "grant": {
            **grant_contract.grant_public(loaded.grant, now=utcnow()),
            "sub_actions": grant_service.sub_action_public(loaded.grant),
            "recent_tool_calls": (loaded.grant.get("tool_calls") or [])[-12:],
            "objective_verification": (loaded.row.payload or {}).get("objective_verification"),
        },
        "preview": None,
    }


def _run_grant(db, settings, gateway, *, session, grant_id: str, max_steps: int) -> dict:
    """Advance the durable loop and translate its outcome into grant state.

    The driver lease makes a repeated click reconnect to the SAME execution:
    while the Worker (or another request) holds it, this call reports the
    current state instead of starting a second one.  A step-budget yield or a
    delegated original task is NOT a pause — the persistent chain continues.
    """
    import os

    from shine.copilot import task_grant as grant_contract
    from shine.copilot import tool_loop, tool_registry
    from shine.support import grants as grant_service

    loaded = grant_service.load(db, grant_id)
    live = grant_contract.grant_status(loaded.grant, now=utcnow())
    if live == "PAUSED_USER_DECISION":
        # An explicit click on "继续推进" IS the user's decision the pause asked
        # for. Only this pause is resumable by a click; scope/contract/unknown/
        # budget pauses need their own reconciliation and stay refused.
        grant_service.set_status(db, grant_id, "RUNNING", reason="用户点击继续推进")
        from shine.services import add_audit

        add_audit(
            db, session.actor, "SUPPORT_TASK_GRANT_RESUMED", "support_delegation",
            loaded.row.id, {"grant_id": grant_id, "from": "PAUSED_USER_DECISION"},
            project_id=session.project_id,
        )
        db.commit()
        loaded = grant_service.load(db, grant_id)
    tools = tool_registry.grant_tool_specs(loaded.grant)
    try:
        outcome = tool_loop.advance(
            db,
            settings,
            grant_id=grant_id,
            objective=str(loaded.grant.get("objective") or ""),
            tools=tools,
            actor=session.actor,
            session_id=session.id,
            gateway=gateway,
            max_steps=max_steps,
            owner=f"api:{os.getpid()}",
        )
    except grant_contract.GrantError as exc:
        return {"status": "PAUSED_USER_DECISION", "stop_reason": exc.code,
                "final": exc.message, "steps": []}

    if outcome.stop_reason in {"STEP_BUDGET_EXHAUSTED", "WALL_CLOCK_EXHAUSTED",
                               "LOOP_ALREADY_RUNNING"}:
        # The task is still live and the persistent driver continues it; a
        # bounded burst ending is not a user-facing pause.
        pass
    elif outcome.status == "WAITING_EXECUTOR":
        try:
            grant_service.set_status(db, grant_id, "WAITING_EXECUTOR")
        except grant_contract.GrantError:
            pass
    else:
        try:
            grant_service.set_status(
                db, grant_id, outcome.status, reason=outcome.stop_reason
            )
        except grant_contract.GrantError:
            # A concurrent revoke/expiry wins over this write; report the real state.
            pass
    from shine.services import add_audit

    add_audit(
        db,
        session.actor,
        "SUPPORT_TASK_GRANT_RUN_FINISHED",
        "support_delegation",
        loaded.row.id,
        {
            "grant_id": grant_id,
            "status": outcome.status,
            "stop_reason": outcome.stop_reason,
            "steps": len(outcome.steps),
            "satisfied": sum(1 for step in outcome.steps if step.get("satisfied")),
        },
        project_id=session.project_id,
    )
    db.commit()
    return outcome.public()

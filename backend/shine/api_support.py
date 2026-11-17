"""AI Copilot (Shine AI 管家) API.

Read endpoints project existing facts.  Mutating endpoints require the loopback
operator header used by the rest of the personal-desktop app, and every action
still needs a preview plus an explicit approval before an existing executor is
called.  There is no endpoint that writes source code, runs a shell, creates a
Git patch, or submits an Issue/PR.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
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
    model_config = ConfigDict(extra="forbid")
    web_search: bool


def _http(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _require_session(db, session_id: str):
    row = agent_service.get_session(db, session_id)
    if row is None:
        raise _http("SESSION_NOT_FOUND", "AI 管家会话不存在或已被清理。", 404)
    return row


@router.get("/capabilities")
def support_capabilities():
    """The callable tool list, for auditability in the UI and tests."""
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
    routes = [
        (method, getattr(route, "path", ""))
        for route in request.app.routes
        for method in (getattr(route, "methods", None) or set())
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    ]
    identity = contract_registry.build_identity()
    live = contract_registry.live_api_digest(routes)
    api_matches = bool(identity.get("api_schema_digest")) and identity["api_schema_digest"] == live
    state = identity["state"]
    if state == "FRESH" and not api_matches:
        state = "STALE"
        identity["reasons"].append("运行中的 API 路由与打包合同不一致，已按过期合同处理。")
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
    if session is not None:
        # Only the read-only plan and the person's explicit scope are stored;
        # delegation walks this list later and still previews every step.
        session.context_json = {
            **(session.context_json or {}),
            "last_plan": {
                **plan,
                "episode_ids": list(body.episode_ids or []),
                "stored_at": utcnow().isoformat(),
            },
        }
        db.commit()
    return {
        "plan": plan,
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
            approved_orders=list(body.approved_orders),
            budget=body.budget,
            actor=actor,
            gateway=gateway,
        )
    except delegation_service.DelegationError as exc:
        raise _http(exc.code, exc.message, 409) from exc
    db.commit()
    return {"delegation": payload}


@router.get("/sessions/{session_id}/delegation")
def support_delegation_get(session_id: str, db: DBSession):
    session = _require_session(db, session_id)
    payload = delegation_service.get(session)
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
    )
    db.commit()
    return {"delegation": payload}


@router.post("/sessions/{session_id}/delegation/cancel")
def support_delegation_cancel(session_id: str, db: DBSession):
    session = _require_session(db, session_id)
    try:
        payload = delegation_service.cancel(session=session)
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
            )
        except action_service.ActionInvalid as exc:
            raise _http(exc.code, exc.message, 400) from exc
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
    return {"web_search": agent_service.web_search_enabled(db)}


@router.post("/preferences")
def support_preferences_update(body: PreferenceUpdate, actor: Operator, db: DBSession):
    return agent_service.set_web_search(db, enabled=body.web_search, actor=actor)


@router.post("/sessions", status_code=201)
def support_session_create(
    body: SessionCreate,
    request: Request,
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
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    session = _require_session(db, session_id)
    result = agent_service.handle_message(
        db, settings, session, body.content, actor=actor, gateway=gateway
    )
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
        )
    except action_service.ActionInvalid as exc:
        raise _http(exc.code, exc.message, 400) from exc
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

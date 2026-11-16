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
from shine.support import actions as action_service
from shine.support import agent as agent_service
from shine.support import reports as report_service
from shine.support.context import build_evidence_snapshot
from shine.support.diagnostics import diagnose_snapshot, suggested_questions
from shine.support.knowledge import search_local, search_official_web
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
    kind: Literal[
        "setup_repair", "setup_reconcile", "managed_release", "workflow_user_copy"
    ]
    params: dict = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)


class ActionApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_hash: str = Field(min_length=16, max_length=64)
    confirm: bool = False


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
    db: DBSession,
    q: str = "",
    limit: int = 5,
    web: bool = False,
):
    limit = max(1, min(int(limit), 10))
    local = search_local(q, limit=limit)
    web_result = {"enabled": False, "results": []}
    if web:
        enabled = agent_service.web_search_enabled(db)
        if not enabled:
            raise _http("WEB_SEARCH_DISABLED", "联网检索已关闭；请先在 AI 管家中开启。", 409)
        web_result = search_official_web(q, enabled=True)
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
    if body.session_id:
        session = _require_session(db, body.session_id)
        snapshot = agent_service.latest_snapshot(session)
        diagnosis = agent_service.latest_diagnosis(session)
        attempted = agent_service.attempt_actions(db, session.id)
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

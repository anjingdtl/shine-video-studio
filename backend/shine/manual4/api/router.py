"""Manual4 HTTP API router (`/api/v1/manual4/*`).

All endpoints enforce:
1. Platform workflow guard: only projects registered as ``manual4`` may enter.
2. Separate database session (``manual4.sqlite``), never touching seven-stage ORM.
3. Loopback + same-origin CSRF guard on mutating requests.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.manual4.status.readiness import build_m4_overview, evaluate_m4_readiness
from shine.manual4.storage.migrations import read_m4_schema_meta
from shine.manual4.storage.models import M4AuditEvent, M4Project
from shine.platform_gpu import inspect_gpu_status
from shine.platform_projects import require_manual4_project

router = APIRouter(prefix="/api/v1/manual4", tags=["manual4"])


def _m4_db(request: Request):
    factory = getattr(request.app.state, "m4_session_factory", None)
    if factory is None:
        raise HTTPException(503, "MANUAL4_DB_UNINITIALIZED")
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def _platform_db(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


M4DB = Annotated[Session, Depends(_m4_db)]
PlatformDB = Annotated[Session, Depends(_platform_db)]


def m4_operator(request: Request, authorization: Annotated[str, Header()] = "") -> str:
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Manual4 API is loopback-only")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin Manual4 action forbidden")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site Manual4 action forbidden")
    if request.headers.get("x-shine-local-action") == "1" or not authorization:
        return "local-user"
    return "local-user"


M4Operator = Annotated[str, Depends(m4_operator)]


def _require_m4_project(project_id: str, platform_db: Session, m4_db: Session) -> M4Project:
    require_manual4_project(platform_db, project_id)
    row = m4_db.get(M4Project, project_id)
    if row is None:
        raise HTTPException(404, "M4_PROJECT_RECORD_MISSING")
    return row


@router.get("/system/status")
def m4_system_status(request: Request, m4_db: M4DB) -> dict[str, Any]:
    settings = request.app.state.settings
    meta = read_m4_schema_meta(m4_db)
    gpu = inspect_gpu_status(request.app.state.session_factory, settings.data_dir)
    return {
        "line": "manual4",
        "schema_version": meta.get("manual4_schema_version", ""),
        "runtime_version": meta.get("manual4_runtime_version", ""),
        "m4_root": str(request.app.state.m4_root),
        "gpu": gpu,
    }


@router.get("/projects/{project_id}/overview")
def m4_project_overview(
    project_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _require_m4_project(project_id, platform_db, m4_db)
    settings = request.app.state.settings
    gpu = inspect_gpu_status(request.app.state.session_factory, settings.data_dir)
    return build_m4_overview(m4_db, project, gpu_status=gpu)


@router.get("/projects/{project_id}/readiness")
def m4_project_readiness(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _require_m4_project(project_id, platform_db, m4_db)
    return evaluate_m4_readiness(m4_db, project).model_dump(mode="json")


@router.get("/projects/{project_id}/audit")
def m4_project_audit(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _require_m4_project(project_id, platform_db, m4_db)
    safe_limit = max(1, min(200, limit))
    safe_offset = max(0, offset)
    rows = m4_db.scalars(
        select(M4AuditEvent)
        .where(M4AuditEvent.project_id == project_id)
        .order_by(M4AuditEvent.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    ).all()
    return {
        "project_id": project_id,
        "items": [
            {
                "id": row.id,
                "actor": row.actor,
                "stage": row.stage,
                "episode_id": row.episode_id,
                "shot_id": row.shot_id,
                "run_id": row.run_id,
                "job_id": row.job_id,
                "event_type": row.event_type,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() + "Z",
            }
            for row in rows
        ],
    }

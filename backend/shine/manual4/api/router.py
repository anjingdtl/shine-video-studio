"""Manual4 HTTP API router (`/api/v1/manual4/*`).

All endpoints enforce:
1. Platform workflow guard: only projects registered as ``manual4`` may enter.
2. Separate database session (``manual4.sqlite``), never touching seven-stage ORM.
3. Loopback + same-origin CSRF guard on mutating requests.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from shine.foundation.environment import environment_snapshot
from shine.manual4.api.deps import (
    M4DB,
    M4Operator,
    PlatformDB,
    m4_operator,
    require_m4_project,
)
from shine.manual4.status.readiness import build_m4_overview, evaluate_m4_readiness
from shine.manual4.storage.migrations import read_m4_schema_meta
from shine.manual4.storage.models import M4AuditEvent
from shine.platform_gpu import inspect_gpu_status

router = APIRouter(prefix="/api/v1/manual4", tags=["manual4"])

from shine.manual4.api.delivery_routes import delivery_router  # noqa: E402
from shine.manual4.api.materials_routes import materials_router  # noqa: E402
from shine.manual4.api.production_routes import production_router  # noqa: E402
from shine.manual4.api.storyboard_routes import storyboard_router  # noqa: E402

router.include_router(storyboard_router)
router.include_router(materials_router)
router.include_router(production_router)
router.include_router(delivery_router)

__all__ = ["M4DB", "M4Operator", "PlatformDB", "m4_operator", "router"]


@router.get("/system/status")
def m4_system_status(request: Request, platform_db: PlatformDB, m4_db: M4DB) -> dict[str, Any]:
    settings = request.app.state.settings
    meta = read_m4_schema_meta(m4_db)
    gpu = inspect_gpu_status(request.app.state.session_factory, settings.data_dir)
    env = environment_snapshot(platform_db, settings)
    return {
        "line": "manual4",
        "schema_version": meta.get("manual4_schema_version", ""),
        "runtime_version": meta.get("manual4_runtime_version", ""),
        "m4_root": str(request.app.state.m4_root),
        "gpu": gpu,
        "environment": env,
    }


@router.get("/projects/{project_id}/overview")
def m4_project_overview(
    project_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = require_m4_project(project_id, platform_db, m4_db)
    settings = request.app.state.settings
    gpu = inspect_gpu_status(request.app.state.session_factory, settings.data_dir)
    return build_m4_overview(m4_db, project, gpu_status=gpu)


@router.get("/projects/{project_id}/readiness")
def m4_project_readiness(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = require_m4_project(project_id, platform_db, m4_db)
    return evaluate_m4_readiness(m4_db, project).model_dump(mode="json")


@router.get("/projects/{project_id}/audit")
def m4_project_audit(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    require_m4_project(project_id, platform_db, m4_db)
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

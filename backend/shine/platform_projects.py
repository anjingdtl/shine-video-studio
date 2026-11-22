"""Platform-level project production-line registry and read-only LLM connection view.

This module is part of the minimal platform shell:
* Historical projects without a ``project_workflows`` row always resolve to
  ``director7``.
* Creating a ``manual4`` project is atomic across ``shine.db`` (platform identity
  + ``project_workflows``) and ``manual4.sqlite`` (``m4_projects``).
* Switching an existing project between ``director7`` and ``manual4`` is
  explicitly refused.
"""

from __future__ import annotations

import logging
import shutil
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.config import Settings
from shine.llm.openai_compatible import chat_completions_url
from shine.llm.secrets import SecretStore
from shine.models import Project, ProjectWorkflow, ProviderProfile, utcnow
from shine.security import is_loopback_url

logger = logging.getLogger("shine.platform")

WORKFLOW_DIRECTOR7 = "director7"
WORKFLOW_MANUAL4 = "manual4"
WORKFLOW_KINDS = (WORKFLOW_DIRECTOR7, WORKFLOW_MANUAL4)

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


def _platform_db(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


PlatformDB = Annotated[Session, Depends(_platform_db)]


def _require_loopback_operator(
    request: Request,
    authorization: Annotated[str, Header()] = "",
) -> str:
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Platform write operations are loopback-only")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin platform action forbidden")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site local action forbidden")
    token = authorization.strip()
    if request.headers.get("x-shine-local-action") == "1" and not token:
        return "local-user"
    if token.lower().startswith("bearer ") and len(token) > 7:
        return "local-user"
    raise HTTPException(403, "Platform write action requires x-shine-local-action header")


def get_project_workflow_record(db: Session, project_id: str) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    row = db.get(ProjectWorkflow, project_id)
    if row is None:
        return {
            "project_id": project.id,
            "title": project.title,
            "workflow_kind": WORKFLOW_DIRECTOR7,
            "selection_revision": 1,
            "implicit": True,
            "created_at": project.created_at,
        }
    return {
        "project_id": project.id,
        "title": project.title,
        "workflow_kind": row.workflow_kind,
        "selection_revision": row.selection_revision,
        "implicit": False,
        "created_at": row.created_at,
    }


def get_workflow_kind_if_known(db: Session, project_id: str) -> str | None:
    row = db.get(ProjectWorkflow, project_id)
    if row is not None:
        return row.workflow_kind
    project = db.get(Project, project_id)
    if project is not None:
        return WORKFLOW_DIRECTOR7
    return None


def require_manual4_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="M4_PROJECT_NOT_FOUND")
    row = db.get(ProjectWorkflow, project_id)
    kind = row.workflow_kind if row is not None else WORKFLOW_DIRECTOR7
    if kind != WORKFLOW_MANUAL4:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKFLOW_LINE_MISMATCH",
                "message": "该项目属于七阶 AI 导演线，禁止通过四阶接口读写。",
                "project_id": project_id,
                "workflow_kind": kind,
            },
        )
    return project


def list_platform_llm_profiles(db: Session, settings: Settings) -> list[dict[str, Any]]:
    """Read-only view of configured LLM provider connections (keys never returned)."""
    secrets = SecretStore(settings.secret_store_dir or (settings.data_dir / "secrets"))
    rows = db.scalars(
        select(ProviderProfile)
        .where(ProviderProfile.enabled.is_(True))
        .order_by(ProviderProfile.created_at)
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "model": row.model,
            "base_url": row.base_url,
            "timeout_s": row.timeout_s,
            "is_local": is_loopback_url(row.base_url),
            "secret_status": secrets.status(row.api_key_ref),
        }
        for row in rows
    ]


def resolve_platform_llm_connection(db: Session, settings: Settings, profile_id: str) -> dict[str, Any]:
    """Load one enabled provider connection + decrypted secret for server-side calls only."""
    row = db.get(ProviderProfile, profile_id)
    if row is None or not row.enabled:
        raise HTTPException(404, "LLM_PROFILE_NOT_FOUND")
    secrets = SecretStore(settings.secret_store_dir or (settings.data_dir / "secrets"))
    api_key = secrets.load(row.api_key_ref) if row.api_key_ref else None
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "model": row.model,
        "base_url": row.base_url,
        "chat_url": chat_completions_url(row.base_url),
        "timeout_s": row.timeout_s,
        "api_key": api_key,
        "generation_options": dict(row.generation_options or {}),
    }


class CreateManual4ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    language: str = Field(default="Chinese", min_length=1, max_length=32)
    max_shot_seconds: float = Field(default=15.0, ge=4.0, le=15.0)


class SwitchWorkflowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_kind: Literal["director7", "manual4"]


@router.get("/projects")
def list_platform_projects(db: PlatformDB) -> list[dict[str, Any]]:
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    workflows = {
        row.project_id: row
        for row in db.scalars(select(ProjectWorkflow)).all()
    }
    items = []
    for project in projects:
        wf = workflows.get(project.id)
        items.append(
            {
                "id": project.id,
                "title": project.title,
                "description": project.description,
                "language": project.language,
                "workflow_kind": wf.workflow_kind if wf is not None else WORKFLOW_DIRECTOR7,
                "selection_revision": wf.selection_revision if wf is not None else 1,
                "created_at": project.created_at.isoformat() + "Z",
            }
        )
    return items


@router.get("/projects/{project_id}/workflow")
def read_project_workflow(project_id: str, db: PlatformDB) -> dict[str, Any]:
    record = get_project_workflow_record(db, project_id)
    return {
        "project_id": record["project_id"],
        "title": record["title"],
        "workflow_kind": record["workflow_kind"],
        "selection_revision": record["selection_revision"],
        "implicit": record["implicit"],
        "created_at": record["created_at"].isoformat() + "Z",
    }


@router.post("/projects/{project_id}/workflow")
def refuse_workflow_switch(project_id: str, body: SwitchWorkflowBody, db: PlatformDB) -> dict[str, Any]:
    current = get_project_workflow_record(db, project_id)
    if current["workflow_kind"] != body.workflow_kind:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKFLOW_SELECTION_IMMUTABLE",
                "message": "作品生产线在创建时已锁定，禁止在七阶与四阶之间切换；请另建新作品。",
                "project_id": project_id,
                "current_workflow_kind": current["workflow_kind"],
                "requested_workflow_kind": body.workflow_kind,
            },
        )
    return {
        "project_id": current["project_id"],
        "workflow_kind": current["workflow_kind"],
        "selection_revision": current["selection_revision"],
    }


@router.post("/manual4-projects", status_code=201)
def create_manual4_project(
    body: CreateManual4ProjectBody,
    request: Request,
    db: PlatformDB,
    actor: Annotated[str, Depends(_require_loopback_operator)],
) -> dict[str, Any]:
    """Atomically register a Manual4 project in shine.db and manual4.sqlite."""
    from shine.manual4.storage.models import M4AuditEvent, M4Project

    m4_factory = getattr(request.app.state, "m4_session_factory", None)
    m4_root = getattr(request.app.state, "m4_root", None)
    if m4_factory is None or m4_root is None:
        raise HTTPException(503, "MANUAL4_SUBSYSTEM_UNAVAILABLE")

    now = utcnow()
    project = Project(
        title=body.title.strip(),
        description=body.description.strip(),
        language=body.language.strip() or "Chinese",
        created_at=now,
    )
    db.add(project)
    db.flush()
    wf = ProjectWorkflow(
        project_id=project.id,
        workflow_kind=WORKFLOW_MANUAL4,
        selection_revision=1,
        created_at=now,
    )
    db.add(wf)
    db.commit()

    project_dir = m4_root / "projects" / project.id
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        with m4_factory.begin() as m4_db:
            m4_db.add(
                M4Project(
                    id=project.id,
                    title=project.title,
                    description=project.description,
                    language=project.language,
                    max_shot_seconds=float(body.max_shot_seconds),
                    created_at=now,
                    updated_at=now,
                )
            )
            m4_db.add(
                M4AuditEvent(
                    project_id=project.id,
                    actor=actor,
                    stage="platform",
                    event_type="M4_PROJECT_CREATED",
                    entity_type="m4_project",
                    entity_id=project.id,
                    payload={"workflow_kind": WORKFLOW_MANUAL4, "selection_revision": 1},
                )
            )
    except Exception as exc:  # noqa: BLE001 - compensate platform registry on m4 failure
        try:
            shutil.rmtree(project_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            row = db.get(Project, project.id)
            if row is not None:
                db.delete(row)
                db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(500, f"M4_PROJECT_CREATION_FAILED: {type(exc).__name__}") from exc

    logger.info("manual4_project_created id=%s", project.id)
    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "language": project.language,
        "workflow_kind": WORKFLOW_MANUAL4,
        "selection_revision": 1,
        "max_shot_seconds": float(body.max_shot_seconds),
        "created_at": project.created_at.isoformat() + "Z",
    }


class CapabilityCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(default="h3", max_length=32)
    variant_id: str = Field(default="h3-ref2va-int8-convrot", max_length=80)
    accelerator_id: str = Field(default="pdd8", max_length=32)
    second_pass: dict[str, Any] = Field(default_factory=dict)
    upscale: dict[str, Any] = Field(default_factory=dict)
    source_line: Literal["director7", "manual4"] = "manual4"
    project_id: str | None = None


class PlatformPrepareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[str] = Field(default_factory=lambda: ["h3"])
    variant_id: str | None = None
    project_id: str | None = None
    deep_scan: bool = False
    reconcile_only: bool = False


@router.get("/environment")
def read_platform_environment(request: Request, db: PlatformDB) -> dict[str, Any]:
    """Single authoritative environment snapshot shared by director7 and manual4."""
    from shine.foundation.environment import environment_snapshot

    settings = request.app.state.settings
    snap = environment_snapshot(db, settings)
    m4_status = getattr(request.app.state, "m4_status", {"available": False, "error_code": "MANUAL4_DB_UNINITIALIZED"})
    snap["subsystems"] = {
        "director7": {"available": True},
        "manual4": m4_status,
    }
    return snap


@router.post("/capabilities/check")
def check_platform_capability(
    body: CapabilityCheckBody,
    request: Request,
    db: PlatformDB,
) -> dict[str, Any]:
    from shine.foundation.environment import check_capability

    settings = request.app.state.settings
    return check_capability(
        db,
        settings,
        recipe={
            "capability": body.capability,
            "variant_id": body.variant_id,
            "accelerator_id": body.accelerator_id,
            "second_pass": body.second_pass,
            "upscale": body.upscale,
        },
        source_line=body.source_line,
        project_id=body.project_id,
    )


@router.post("/setup/prepare")
def trigger_platform_setup_prepare(
    body: PlatformPrepareBody,
    request: Request,
    db: PlatformDB,
    actor: Annotated[str, Depends(_require_loopback_operator)],
) -> dict[str, Any]:
    from shine.foundation.environment import prepare_missing

    settings = request.app.state.settings
    return prepare_missing(
        db,
        settings,
        capabilities=body.capabilities,
        project_id=body.project_id,
        variant_id=body.variant_id,
        actor=actor,
        deep_scan=body.deep_scan,
        reconcile_only=body.reconcile_only,
    )

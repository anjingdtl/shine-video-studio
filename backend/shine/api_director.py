"""Director journey endpoints: the six stages, the package and the whole-film run.

These routes only persist intent.  Nothing here calls ComfyUI, FFmpeg or a model
provider: the resident Worker performs every real action, so an HTTP call can
never become the task authority and a closed browser can never stop production.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.config import Settings
from shine.director.contracts import (
    DEFAULT_TARGET_SECONDS,
    DEFAULT_TOLERANCE_SECONDS,
    EpisodePlanPayload,
    derive_episode_plan,
    digest_of,
    validate_episode_coverage,
)
from shine.director.journey import compute_journey
from shine.director.models import DirectorEpisodePlan, DirectorPackageEpisode, DirectorPackageVersion
from shine.director.package import (
    build_package,
    confirm_package,
    package_episodes,
    package_public,
    package_readiness,
)
from shine.models import Shot, utcnow
from shine.production import runner as run_engine
from shine.production.models import ProductionRun
from shine.schemas import APIModel
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["director journey"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


def _plan_public(row: DirectorEpisodePlan) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "version": row.version,
        "status": row.status,
        "target_seconds": row.target_seconds,
        "tolerance_seconds": row.tolerance_seconds,
        "sha256": row.sha256,
        "source": row.source,
        "payload": row.payload,
        "created_at": row.created_at.isoformat() + "Z",
        "confirmed_at": row.confirmed_at.isoformat() + "Z" if row.confirmed_at else None,
    }


@router.get("/projects/{project_id}/journey")
def project_journey(project_id: str, db: DBSession) -> dict[str, Any]:
    """The six-stage completion state, derived from the ledger only."""
    project = require_project(db, project_id)
    return compute_journey(db, project)


# ---------------------------------------------------------------------------
# Episode plan
# ---------------------------------------------------------------------------


class EpisodePlanBody(APIModel):
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=8, le=3600)
    tolerance_seconds: int = Field(default=DEFAULT_TOLERANCE_SECONDS, ge=0, le=900)
    episodes: list[dict[str, Any]] | None = None
    """An explicit plan to store; omitted means "derive one from the shots"."""


@router.get("/projects/{project_id}/episode-plans")
def list_episode_plans(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    require_project(db, project_id)
    rows = db.scalars(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == project_id)
        .order_by(DirectorEpisodePlan.version.desc())
    ).all()
    return [_plan_public(row) for row in rows]


@router.post("/projects/{project_id}/episode-plans", status_code=201)
def create_episode_plan(project_id: str, body: EpisodePlanBody, actor: Operator, db: DBSession) -> dict[str, Any]:
    """Store a new episode-plan revision (derived or explicitly supplied)."""
    require_project(db, project_id)
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    if not shots:
        raise HTTPException(422, "EPISODE_PLAN_REQUIRES_SHOTS")
    if body.episodes:
        payload = EpisodePlanPayload.model_validate({
            "target_seconds": body.target_seconds,
            "tolerance_seconds": body.tolerance_seconds,
            "episodes": body.episodes,
        })
    else:
        payload = derive_episode_plan(
            shots, target_seconds=body.target_seconds, tolerance_seconds=body.tolerance_seconds
        )
    problems = validate_episode_coverage(payload, [shot.id for shot in shots])
    if problems:
        raise HTTPException(422, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})

    previous = db.scalars(
        select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == project_id)
    ).all()
    version = max((row.version for row in previous), default=0) + 1
    row = DirectorEpisodePlan(
        project_id=project_id,
        version=version,
        status="DRAFT",
        target_seconds=payload.target_seconds,
        tolerance_seconds=payload.tolerance_seconds,
        payload=payload.model_dump(mode="json"),
        sha256=digest_of(payload.model_dump(mode="json")),
        source="derived" if not body.episodes else "supplied",
        created_by=actor,
    )
    db.add(row)
    db.flush()
    add_audit(db, actor, "EPISODE_PLAN_CREATED", "director_episode_plan", row.id,
              {"version": version, "episodes": len(payload.episodes),
               "target_seconds": payload.target_seconds, "source": row.source},
              project_id=project_id)
    db.commit()
    db.refresh(row)
    return _plan_public(row)


@router.post("/projects/{project_id}/episode-plans/{version}/confirm")
def confirm_episode_plan(project_id: str, version: int, actor: Operator, db: DBSession,
                         body: dict[str, Any] | None = None) -> dict[str, Any]:
    require_project(db, project_id)
    row = db.scalars(
        select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project_id, DirectorEpisodePlan.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "EPISODE_PLAN_NOT_FOUND")
    if body and body.get("expected_sha256") and body["expected_sha256"] != row.sha256:
        raise HTTPException(409, "EPISODE_PLAN_CHANGED")
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    problems = validate_episode_coverage(EpisodePlanPayload.model_validate(row.payload), [shot.id for shot in shots])
    if problems:
        raise HTTPException(409, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})
    superseded = 0
    for other in db.scalars(
        select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project_id,
            DirectorEpisodePlan.status == "CONFIRMED",
            DirectorEpisodePlan.id != row.id,
        )
    ).all():
        other.status = "SUPERSEDED"
        superseded += 1
    row.status = "CONFIRMED"
    row.confirmed_by = actor
    row.confirmed_at = utcnow()
    add_audit(db, actor, "EPISODE_PLAN_CONFIRMED", "director_episode_plan", row.id,
              {"version": row.version, "sha256": row.sha256, "superseded": superseded},
              project_id=project_id)
    db.commit()
    db.refresh(row)
    return _plan_public(row)


# ---------------------------------------------------------------------------
# Director package
# ---------------------------------------------------------------------------


class PackageBody(APIModel):
    workflow_profile_id: str = Field(min_length=1, max_length=36)
    generation_mode: Literal["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"] | None = None
    output: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None


@router.get("/projects/{project_id}/director-packages")
def list_packages(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    require_project(db, project_id)
    rows = db.scalars(
        select(DirectorPackageVersion)
        .where(DirectorPackageVersion.project_id == project_id)
        .order_by(DirectorPackageVersion.revision.desc())
    ).all()
    return [package_public(row) for row in rows]


@router.post("/projects/{project_id}/director-packages", status_code=201)
def create_package(project_id: str, body: PackageBody, actor: Operator, db: DBSession) -> dict[str, Any]:
    """Snapshot the current confirmed state into a new package revision."""
    project = require_project(db, project_id)
    package = build_package(
        db,
        project,
        workflow_profile_id=body.workflow_profile_id,
        generation_mode=body.generation_mode,
        output=body.output,
        policy=body.policy,
        created_by=actor,
    )
    add_audit(db, actor, "DIRECTOR_PACKAGE_CREATED", "director_package", package.id,
              {"revision": package.revision, "scope_digest": package.scope_digest,
               "episodes": package.episode_count, "shots": package.shot_count,
               "workflow_profile_id": body.workflow_profile_id},
              project_id=project_id)
    db.commit()
    db.refresh(package)
    return package_public(package, episodes=package_episodes(db, package.id))


@router.get("/director-packages/{package_id}")
def get_package(package_id: str, db: DBSession) -> dict[str, Any]:
    package = db.get(DirectorPackageVersion, package_id)
    if package is None:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    return package_public(package, episodes=package_episodes(db, package.id))


@router.get("/director-packages/{package_id}/episodes")
def list_package_episodes(package_id: str, db: DBSession) -> list[dict[str, Any]]:
    package = db.get(DirectorPackageVersion, package_id)
    if package is None:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    rows = db.scalars(
        select(DirectorPackageEpisode)
        .where(DirectorPackageEpisode.package_id == package_id)
        .order_by(DirectorPackageEpisode.position)
    ).all()
    return [
        {
            "episode_id": row.episode_id,
            "position": row.position,
            "title": row.title,
            "shot_count": row.shot_count,
            "planned_seconds": row.planned_seconds,
            "manifest_sha256": row.manifest_sha256,
            "shots": [
                {"shot_id": shot["shot_id"], "name": shot["name"], "revision": shot["revision"],
                 "duration_target_s": shot["duration_target_s"],
                 "continuity_mode": shot.get("continuity_mode")}
                for shot in row.manifest.get("shots", [])
            ],
        }
        for row in rows
    ]


@router.get("/director-packages/{package_id}/readiness")
def package_readiness_endpoint(package_id: str, db: DBSession) -> dict[str, Any]:
    package = db.get(DirectorPackageVersion, package_id)
    if package is None:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    return package_readiness(db, package)


@router.post("/director-packages/{package_id}/confirm")
def confirm_package_endpoint(package_id: str, actor: Operator, db: DBSession,
                             body: dict[str, Any] | None = None) -> dict[str, Any]:
    package = db.get(DirectorPackageVersion, package_id)
    if package is None:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    if body and body.get("expected_manifest_sha256") and \
            body["expected_manifest_sha256"] != package.manifest_sha256:
        raise HTTPException(409, "DIRECTOR_PACKAGE_CHANGED")
    confirm_package(db, package, actor=actor)
    add_audit(db, actor, "DIRECTOR_PACKAGE_CONFIRMED", "director_package", package.id,
              {"revision": package.revision, "manifest_sha256": package.manifest_sha256,
               "scope_digest": package.scope_digest},
              project_id=package.project_id)
    db.commit()
    db.refresh(package)
    return package_public(package, episodes=package_episodes(db, package.id))


# ---------------------------------------------------------------------------
# Whole-film run
# ---------------------------------------------------------------------------


class RunBody(APIModel):
    package_id: str = Field(min_length=1, max_length=36)
    episode_ids: list[str] | None = Field(default=None, max_length=200)
    request_key: str | None = Field(default=None, max_length=120)


@router.get("/projects/{project_id}/production-runs")
def list_runs(project_id: str, db: DBSession, limit: int = 20) -> list[dict[str, Any]]:
    require_project(db, project_id)
    rows = db.scalars(
        select(ProductionRun)
        .where(ProductionRun.project_id == project_id)
        .order_by(ProductionRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [run_engine.run_public(db, row, include_episodes=False) for row in rows]


@router.post("/projects/{project_id}/production-runs", status_code=201)
def create_run_endpoint(project_id: str, body: RunBody, request: Request, actor: Operator,
                        db: DBSession, settings: SettingsDep) -> dict[str, Any]:
    """Persist one whole-film run. The resident Worker advances it from here.

    Repeating the same request — a double click, a refresh, a retried POST —
    returns the same run instead of starting a second production.
    """
    require_project(db, project_id)
    package = db.get(DirectorPackageVersion, body.package_id)
    if package is None or package.project_id != project_id:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    idempotency_key = body.request_key or (
        "auto:" + digest_of({"project": project_id, "package": package.manifest_sha256,
                             "episodes": body.episode_ids or "all"})
    )
    run, created = run_engine.create_run(
        db, package, episode_ids=body.episode_ids, idempotency_key=idempotency_key, actor=actor
    )
    db.commit()
    db.refresh(run)
    payload = run_engine.run_public(db, run)
    payload["created"] = created
    return payload


@router.get("/production-runs/{run_id}")
def get_run(run_id: str, db: DBSession) -> dict[str, Any]:
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    return run_engine.run_public(db, run)


@router.get("/production-runs/{run_id}/episodes")
def get_run_episodes(run_id: str, db: DBSession, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """One page of a run's episodes, read from the ledger.

    Paging happens in SQL, not after materialising every episode: a 200-episode
    run must not cost the same as a 2-episode one just to render page 3.
    """
    from sqlalchemy import func

    from shine.production.models import ProductionRunEpisode

    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    total = db.scalar(
        select(func.count()).select_from(ProductionRunEpisode)
        .where(ProductionRunEpisode.run_id == run_id)
    ) or 0
    rows = db.scalars(
        select(ProductionRunEpisode)
        .where(ProductionRunEpisode.run_id == run_id)
        .order_by(ProductionRunEpisode.position)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
    ).all()
    return {"total": total, "offset": max(0, offset), "items": run_engine.episode_public(db, run, rows)}


@router.get("/production-runs/{run_id}/events")
def get_run_events(run_id: str, db: DBSession, after_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    return run_engine.run_events(db, run_id, limit=limit, after_id=after_id)


@router.get("/production-runs/{run_id}/blockers")
def get_run_blockers(run_id: str, db: DBSession) -> dict[str, Any]:
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    payload = run_engine.run_public(db, run)
    blockers = [
        {"episode_id": episode["episode_id"], "title": episode["title"],
         "code": episode["blocker_code"], "status": episode["status"],
         "batches": [batch for batch in episode["batches"] if batch["failure_code"]]}
        for episode in payload.get("episodes", [])
        if episode["status"] == "BLOCKED" or episode["blocker_code"]
    ]
    return {"run_id": run_id, "status": run.status, "count": len(blockers), "blockers": blockers}


def _control_route(request: Request, run_id: str, action: str, actor: str, db: DBSession) -> dict[str, Any]:
    """Control operations open their own transaction on the app's factory.

    They are conditional UPDATEs with their own commit, so riding the request's
    read transaction would either hold a writer lock or silently roll back.
    """
    factory = request.app.state.session_factory
    if db.get(ProductionRun, run_id) is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    db.rollback()
    if action == "pause":
        status = run_engine.pause_run(factory, run_id, worker_id=actor)
    elif action == "resume":
        status = run_engine.resume_run(factory, run_id)
    elif action == "cancel":
        status = run_engine.cancel_run(factory, run_id, actor=actor)
    else:
        result = run_engine.reconcile_run(factory, run_id, worker_id=actor)
        db.expire_all()
        row = db.get(ProductionRun, run_id)
        return {"id": run_id, "status": row.status if row else "GONE", "reconcile": result}
    db.expire_all()
    row = db.get(ProductionRun, run_id)
    return {"id": run_id, "status": status, "state_version": row.state_version if row else None}


@router.post("/production-runs/{run_id}/pause", status_code=202)
def pause_run_endpoint(run_id: str, request: Request, actor: Operator, db: DBSession) -> dict[str, Any]:
    return _control_route(request, run_id, "pause", actor, db)


@router.post("/production-runs/{run_id}/resume", status_code=202)
def resume_run_endpoint(run_id: str, request: Request, actor: Operator, db: DBSession) -> dict[str, Any]:
    return _control_route(request, run_id, "resume", actor, db)


@router.post("/production-runs/{run_id}/cancel", status_code=202)
def cancel_run_endpoint(run_id: str, request: Request, actor: Operator, db: DBSession) -> dict[str, Any]:
    return _control_route(request, run_id, "cancel", actor, db)


@router.post("/production-runs/{run_id}/reconcile", status_code=202)
def reconcile_run_endpoint(run_id: str, request: Request, actor: Operator, db: DBSession) -> dict[str, Any]:
    return _control_route(request, run_id, "reconcile", actor, db)



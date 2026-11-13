"""Director journey endpoints: the seven stages, the package and the whole-film run.

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
def project_journey(project_id: str, db: DBSession, package_id: str | None = None,
                    run_id: str | None = None) -> dict[str, Any]:
    """The seven-stage completion state, derived from the ledger only.

    ``package_id`` (and optionally ``run_id``) names the exact scope every
    production, playback and delivery view must project.  Without it the newest
    package is the scope; an unknown id is a clear 404 with the package ids the
    project really has, never a silent fallback to a different revision.
    """
    project = require_project(db, project_id)
    return compute_journey(db, project, package_id=package_id, run_id=run_id)


# ---------------------------------------------------------------------------
# Entry A: idea -> creative brief -> script (Entry B is the document upload)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/creative-briefs")
def list_creative_briefs(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    from shine.director import creative

    require_project(db, project_id)
    return [creative.brief_public(db, row) for row in creative.brief_rows(db, project_id)]


@router.post("/projects/{project_id}/creative-briefs", status_code=201)
def save_creative_brief(project_id: str, body: dict[str, Any], actor: Operator, db: DBSession) -> dict[str, Any]:
    """Save one new, editable brief version from the person's idea."""
    from pydantic import ValidationError

    from shine.director import creative

    project = require_project(db, project_id)
    try:
        payload = creative.CreativeBriefPayload.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, {
            "code": "CREATIVE_BRIEF_INVALID",
            "problems": [".".join(str(p) for p in error.get("loc", ())) + ": " + error.get("msg", "")
                         for error in exc.errors()][:20],
        }) from exc
    artifact = creative.save_brief(db, project, payload, actor=actor)
    db.commit()
    db.refresh(artifact)
    return creative.brief_public(db, artifact)


@router.post("/projects/{project_id}/creative-briefs/{artifact_id}/confirm")
def confirm_creative_brief(project_id: str, artifact_id: str, actor: Operator, db: DBSession,
                           body: dict[str, Any] | None = None) -> dict[str, Any]:
    from shine.director import creative
    from shine.models import AgentArtifact

    require_project(db, project_id)
    artifact = db.get(AgentArtifact, artifact_id)
    if artifact is None or artifact.project_id != project_id:
        raise HTTPException(404, "CREATIVE_BRIEF_NOT_FOUND")
    creative.confirm_brief(db, artifact, actor=actor,
                           expected_sha256=(body or {}).get("expected_sha256"))
    db.commit()
    db.refresh(artifact)
    return creative.brief_public(db, artifact)


@router.post("/projects/{project_id}/creative-briefs/{artifact_id}/generate-script", status_code=202)
def generate_script_from_brief(project_id: str, artifact_id: str, actor: Operator, db: DBSession,
                               settings: SettingsDep, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Queue the bounded brief→script task; the resident Worker performs it.

    The written script becomes an ordinary project document, so it continues
    through exactly the same outline, confirmation and storyboard pipeline as an
    imported script.
    """
    from shine.director import creative
    from shine.models import AgentArtifact

    project = require_project(db, project_id)
    artifact = db.get(AgentArtifact, artifact_id)
    if artifact is None or artifact.project_id != project_id:
        raise HTTPException(404, "CREATIVE_BRIEF_NOT_FOUND")
    task = creative.queue_script_generation(
        db, project, artifact, settings=settings, actor=actor,
        remote_text_consent=bool((body or {}).get("remote_text_consent")),
    )
    db.commit()
    db.refresh(artifact)
    return creative.brief_public(db, artifact) | {"task_id": task.id}


# ---------------------------------------------------------------------------
# Episode plan
# ---------------------------------------------------------------------------


class EpisodePlanBody(APIModel):
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=8, le=3600)
    tolerance_seconds: int = Field(default=DEFAULT_TOLERANCE_SECONDS, ge=0, le=900)
    episodes: list[dict[str, Any]] | None = None
    """An explicit plan to store; omitted means "derive one"."""
    mode: Literal["shots", "sections"] | None = None
    """``sections`` plans from the confirmed script before any shot exists;
    ``shots`` groups an existing shot list (the compatible entry).  Omitted:
    a project that already has shots keeps the shot entry, otherwise the plan
    is derived from the confirmed script."""


def _confirmed_outline(db, project_id: str):
    """The confirmed outline of the *current* script basis.

    A project can hold several imported scripts.  The plan is derived from the
    outline that belongs to the basis the person selected, never from whichever
    outline happens to be newest.
    """
    from shine.director.package import _outline_for_document, current_script_basis
    from shine.models import AgentArtifact, Project

    project = db.get(Project, project_id)
    if project is None:
        return None
    basis = current_script_basis(db, project)
    if basis["document"] is None:
        # A project without any imported document (an idea written straight
        # into the storyboard, or a package that predates provenance) keeps
        # its original outline contract: there is no document to be wrong about.
        return db.scalars(
            select(AgentArtifact)
            .where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == "StoryOutline",
                AgentArtifact.stale.is_(False),
                AgentArtifact.approved_at.isnot(None),
            )
            .order_by(AgentArtifact.created_at.desc())
        ).first()
    outline = _outline_for_document(db, project_id, basis["document"])
    return outline if outline is not None and outline.approved_at else None


def _outline_sections(outline) -> list[dict[str, Any]]:
    return list((outline.payload_json or {}).get("production_sections") or [])


def _section_plan_problems(db, project_id: str, payload: EpisodePlanPayload) -> list[str]:
    """Coverage of a section plan against the *current* confirmed script."""
    from shine.director.contracts import validate_section_coverage

    outline = _confirmed_outline(db, project_id)
    if outline is None:
        return ["SCRIPT_OUTLINE_NOT_CONFIRMED"]
    source = payload.source or {}
    if source.get("outline_artifact_id") and source.get("outline_artifact_id") != outline.id:
        return ["SCRIPT_OUTLINE_CHANGED"]
    if source.get("outline_sha256") and source.get("outline_sha256") != outline.sha256:
        return ["SCRIPT_OUTLINE_CHANGED"]
    problems = validate_section_coverage(
        payload, [str(row["production_section_id"]) for row in _outline_sections(outline)]
    )
    if payload.materialized():
        known = set(db.scalars(select(Shot.id).where(Shot.project_id == project_id)).all())
        unknown = [shot_id for shot_id in payload.shot_order() if shot_id not in known]
        if unknown:
            problems.append("PLAN_REFERENCES_UNKNOWN_SHOTS:" + ",".join(unknown)[:400])
    return problems


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
    """Store a new episode-plan revision (derived or explicitly supplied).

    Script first is the primary entry: with a confirmed script outline the plan
    is derived from its sections before a single shot exists.  Grouping an
    already-built shot list stays available as the compatible entry.
    """
    from shine.director.contracts import derive_episode_plan_from_sections

    project = require_project(db, project_id)
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    mode = body.mode or ("shots" if shots else "sections")
    if mode == "sections":
        outline = _confirmed_outline(db, project_id)
        if outline is None:
            raise HTTPException(422, {"code": "SCRIPT_OUTLINE_NOT_CONFIRMED",
                                      "message": "先确认剧本大纲，再按剧本分集"})
        sections = _outline_sections(outline)
        if not sections:
            raise HTTPException(422, "SCRIPT_OUTLINE_HAS_NO_SECTIONS")
        try:
            if body.episodes:
                payload = EpisodePlanPayload.model_validate({
                    "mode": "sections",
                    "target_seconds": body.target_seconds,
                    "tolerance_seconds": body.tolerance_seconds,
                    "episodes": body.episodes,
                    "source": {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256},
                })
            else:
                payload = derive_episode_plan_from_sections(
                    sections, target_seconds=body.target_seconds,
                    tolerance_seconds=body.tolerance_seconds,
                    source={"outline_artifact_id": outline.id, "outline_sha256": outline.sha256},
                )
        except ValueError as exc:
            raise HTTPException(422, {"code": "EPISODE_PLAN_INVALID", "problems": [str(exc)[:400]]}) from exc
        problems = _section_plan_problems(db, project_id, payload)
    else:
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
        # The compatible entry groups the shots the project already has.  It
        # records the outline those shots really came from, so a re-grouping
        # of an old script's shots under a new script is visible instead of
        # silent (R01).  The recorded basis is the *actual* one, never the
        # outline that happens to be confirmed now.
        from shine.director.basis import shots_plan_source

        payload = payload.model_copy(update={
            "source": shots_plan_source(db, project, payload.shot_order())["source"],
        })
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
        source=("script_sections" if mode == "sections" else "derived") if not body.episodes else "supplied",
        created_by=actor,
    )
    db.add(row)
    db.flush()
    add_audit(db, actor, "EPISODE_PLAN_CREATED", "director_episode_plan", row.id,
              {"version": version, "episodes": len(payload.episodes), "mode": mode,
               "target_seconds": payload.target_seconds, "source": row.source},
              project_id=project_id)
    db.commit()
    db.refresh(row)
    return _plan_public(row)


@router.post("/projects/{project_id}/episode-plans/{version}/materialize", status_code=201)
def materialize_episode_plan(project_id: str, version: int, actor: Operator, db: DBSession) -> dict[str, Any]:
    """Bind a script-first plan to the shots its sections really produced.

    Each section resolves to the shots of its newest *applied* storyboard; a
    section that was not storyboarded yet is named, never skipped.  The result
    is a new plan version the person confirms — the earlier one stays history.
    """
    from shine.materialprep.story import section_storyboard_shot_ids

    require_project(db, project_id)
    row = db.scalars(
        select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project_id, DirectorEpisodePlan.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "EPISODE_PLAN_NOT_FOUND")
    payload = EpisodePlanPayload.model_validate(row.payload)
    if payload.mode != "sections":
        raise HTTPException(409, "EPISODE_PLAN_NOT_SCRIPT_FIRST")
    pending: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for episode in payload.episodes:
        shot_ids: list[str] = []
        for section_id in episode.section_ids:
            resolved = section_storyboard_shot_ids(db, project_id, section_id)
            if resolved["missing"] or not resolved["shot_ids"]:
                pending.append({"episode_id": episode.episode_id, "section_id": section_id,
                                "missing": resolved["missing"] or [{"reason": "SECTION_HAS_NO_SHOTS"}]})
            shot_ids.extend(resolved["shot_ids"])
        episodes.append({**episode.model_dump(mode="json"), "shot_ids": shot_ids})
    if pending:
        raise HTTPException(409, {"code": "EPISODE_SECTION_NOT_STORYBOARDED",
                                  "message": "还有场次没有拆成镜头", "pending": pending[:50]})
    try:
        materialized = EpisodePlanPayload.model_validate({
            **payload.model_dump(mode="json"), "episodes": episodes,
        })
    except ValueError as exc:
        raise HTTPException(409, {"code": "EPISODE_PLAN_INVALID", "problems": [str(exc)[:400]]}) from exc
    problems = _section_plan_problems(db, project_id, materialized)
    if problems:
        raise HTTPException(409, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})
    previous = db.scalars(
        select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == project_id)
    ).all()
    new_row = DirectorEpisodePlan(
        project_id=project_id,
        version=max((plan.version for plan in previous), default=0) + 1,
        status="DRAFT",
        target_seconds=materialized.target_seconds,
        tolerance_seconds=materialized.tolerance_seconds,
        payload=materialized.model_dump(mode="json"),
        sha256=digest_of(materialized.model_dump(mode="json")),
        source="script_sections_materialized",
        created_by=actor,
    )
    db.add(new_row)
    db.flush()
    add_audit(db, actor, "EPISODE_PLAN_MATERIALIZED", "director_episode_plan", new_row.id,
              {"from_version": row.version, "version": new_row.version,
               "shots": len(materialized.shot_order())}, project_id=project_id)
    db.commit()
    db.refresh(new_row)
    return _plan_public(new_row)


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
    payload = EpisodePlanPayload.model_validate(row.payload)
    if payload.mode == "sections":
        problems = _section_plan_problems(db, project_id, payload)
    else:
        shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
        problems = validate_episode_coverage(payload, [shot.id for shot in shots])
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
    fill_missing_character_bindings: bool = False
    # A whole-plan build stays the default; a streamline build may freeze only
    # the episodes that are really ready (plan §9) while another episode is
    # still being fixed.
    episode_ids: list[str] | None = Field(default=None, max_length=200)


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
def create_package(project_id: str, body: PackageBody, request: Request,
                   actor: Operator, db: DBSession) -> dict[str, Any]:
    """Snapshot the current confirmed state into a new package revision."""
    project = require_project(db, project_id)
    package = build_package(
        db,
        project,
        settings=request.app.state.settings,
        workflow_profile_id=body.workflow_profile_id,
        generation_mode=body.generation_mode,
        output=body.output,
        policy=body.policy,
        fill_missing_character_bindings=body.fill_missing_character_bindings,
        episode_ids=body.episode_ids,
        created_by=actor,
    )
    add_audit(db, actor, "DIRECTOR_PACKAGE_CREATED", "director_package", package.id,
              {"revision": package.revision, "scope_digest": package.scope_digest,
               "episodes": package.episode_count, "shots": package.shot_count,
               "workflow_profile_id": body.workflow_profile_id,
               **({"selected_episode_ids": body.episode_ids} if body.episode_ids is not None else {})},
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


@router.put("/production-runs/{run_id}/episodes/{episode_id}/post-contract", status_code=202)
def set_episode_post_contract_endpoint(run_id: str, episode_id: str, request: Request, actor: Operator,
                                       db: DBSession, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Revise an episode's cut points or subtitle layer: a new delivery version, no re-render."""
    if db.get(ProductionRun, run_id) is None:
        raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    db.rollback()
    return run_engine.set_episode_post_contract(
        request.app.state.session_factory, run_id, episode_id, body, actor=actor
    )


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
        status = run_engine.resume_run(factory, run_id, actor=actor)
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



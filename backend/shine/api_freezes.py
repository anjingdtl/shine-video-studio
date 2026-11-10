"""User-triggered unfreeze and re-freeze controls.

Every frozen decision keeps its history; these routes never rewrite an old
version.  They either supersede it with a new frozen version or mark it stale,
and they record who did it and why.  Active production is never interrupted:
an unlock waits until the shots it would invalidate are no longer being made.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.comfyops.ownership import LIVE_SERIES_STATUSES
from shine.director.models import DirectorEpisodePlan, DirectorPackageVersion
from shine.models import (
    Asset,
    Job,
    ProductionSeries,
    ProductionSeriesItem,
    Shot,
    ShotAssetBinding,
    StyleVersion,
)
from shine.production.models import ACTIVE_RUN_STATUSES, ProductionRun
from shine.schemas import APIModel, ShotSpec, StyleBible
from shine.services import add_audit, freeze_style, require_project, revise_shot_record

router = APIRouter(prefix="/api/v1", tags=["freeze controls"])

ACTIVE_JOB_STATUSES = ("SUBMITTING", "RUNNING", "QC_PENDING")


class UnlockBody(APIModel):
    reason: str = Field(default="", max_length=500)


class StyleRefreshBody(APIModel):
    expected_style_version_id: str = Field(min_length=36, max_length=36)
    approval_note: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=500)


def _active_shot_ids(db, project_id: str) -> set[str]:
    active = set(
        db.scalars(
            select(Job.shot_id).where(
                Job.project_id == project_id,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        ).all()
    )
    active.update(
        db.scalars(
            select(ProductionSeriesItem.shot_id)
            .join(ProductionSeries, ProductionSeries.id == ProductionSeriesItem.series_id)
            .where(
                ProductionSeries.project_id == project_id,
                ProductionSeries.status.in_(LIVE_SERIES_STATUSES),
            )
        ).all()
    )
    return active


def _refuse_active(active_shot_ids: set[str], message: str) -> None:
    if active_shot_ids:
        raise HTTPException(
            409,
            {
                "code": "FREEZE_UNLOCK_PRODUCTION_ACTIVE",
                "message": message,
                "shot_ids": sorted(active_shot_ids),
            },
        )


def _revision_payload(shot: Shot, style_id: str) -> ShotSpec:
    spec = ShotSpec.model_validate(shot.spec)
    # A per-shot English style belongs to the style it was written for.  A new
    # frozen Style Bible must be compiled from its own bytes, so the stale
    # projection is cleared rather than silently carrying the old wording.
    return spec.model_copy(update={"style_version_id": style_id, "prompt_style": None})


@router.post("/projects/{project_id}/freeze/style/refresh")
def refresh_frozen_style(
    project_id: str,
    body: StyleRefreshBody,
    actor: Operator,
    db: DBSession,
) -> dict:
    """Supersede the frozen style, then apply the new freeze to every old shot."""
    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(409, "FREEZE_STYLE_NOT_FROZEN")
    if project.frozen_style_version_id != body.expected_style_version_id:
        raise HTTPException(409, "FREEZE_STYLE_CHANGED")
    _refuse_active(_active_shot_ids(db, project_id), "有镜头正在制作，先等它收尾再解锁风格")
    if not project.style_draft:
        raise HTTPException(409, "FREEZE_STYLE_DRAFT_MISSING")

    previous_id = project.frozen_style_version_id
    current = db.get(StyleVersion, previous_id)
    draft_payload = StyleBible.model_validate(project.style_draft).model_dump(mode="json")
    if current is not None and current.snapshot == draft_payload:
        target = current
    else:
        target, _impact = freeze_style(
            db, project, approval_note=body.approval_note, actor=actor
        )

    revised: list[str] = []
    for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)).all():
        spec = ShotSpec.model_validate(shot.spec)
        if spec.style_version_id == target.id:
            continue
        revise_shot_record(
            db, project, shot, spec=_revision_payload(shot, target.id), actor=actor
        )
        revised.append(shot.id)

    add_audit(
        db,
        actor,
        "STYLE_UNLOCKED_AND_REFROZEN",
        "style_version",
        target.id,
        {
            "previous_style_version_id": previous_id,
            "new_style_version_id": target.id,
            "revised_shot_ids": sorted(revised),
            "reason": body.reason,
        },
        project_id=project_id,
    )
    db.commit()
    return {
        "style": {
            "id": target.id,
            "version": target.version,
            "sha256": target.sha256,
            "snapshot": target.snapshot,
        },
        "revised_shot_ids": sorted(revised),
    }


@router.post("/projects/{project_id}/freeze/shots/style")
def apply_current_style_to_shots(
    project_id: str,
    body: UnlockBody,
    actor: Operator,
    db: DBSession,
) -> dict:
    """Revise every shot that still points at an older frozen style."""
    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(409, "FREEZE_STYLE_NOT_FROZEN")
    _refuse_active(_active_shot_ids(db, project_id), "有镜头正在制作，先等它收尾再应用新风格")
    target = db.get(StyleVersion, project.frozen_style_version_id)
    if target is None:
        raise HTTPException(409, "FREEZE_STYLE_NOT_FROZEN")

    revised: list[str] = []
    for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)).all():
        spec = ShotSpec.model_validate(shot.spec)
        if spec.style_version_id == target.id:
            continue
        revise_shot_record(
            db, project, shot, spec=_revision_payload(shot, target.id), actor=actor
        )
        revised.append(shot.id)
    add_audit(
        db,
        actor,
        "STYLE_APPLIED_TO_SHOTS",
        "style_version",
        target.id,
        {"revised_shot_ids": sorted(revised), "reason": body.reason},
        project_id=project_id,
    )
    db.commit()
    return {"style_version_id": target.id, "revised_shot_ids": sorted(revised)}


@router.post("/projects/{project_id}/freeze/assets/{asset_id}/unlock")
def unlock_asset(
    project_id: str,
    asset_id: str,
    body: UnlockBody,
    actor: Operator,
    db: DBSession,
) -> dict:
    """Unlock one approved master so the person can edit and re-confirm it."""
    require_project(db, project_id)
    asset = db.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id:
        raise HTTPException(404, "ASSET_NOT_FOUND")
    bound = set(
        db.scalars(
            select(ShotAssetBinding.shot_id).where(ShotAssetBinding.asset_id == asset.id)
        ).all()
    )
    _refuse_active(
        _active_shot_ids(db, project_id) & bound,
        "这个素材正被在拍镜头使用，先等镜头收尾再解锁",
    )
    previous = {"approved": asset.approved, "locked": asset.locked}
    asset.approved = False
    asset.locked = False
    asset.approved_at = None
    asset.locked_at = None
    add_audit(
        db,
        actor,
        "ASSET_UNFROZEN",
        "asset",
        asset.id,
        {
            "filename": asset.filename,
            "previous": previous,
            "bound_shot_ids": sorted(bound),
            "reason": body.reason,
        },
        project_id=project_id,
    )
    db.commit()
    return {
        "id": asset.id,
        "filename": asset.filename,
        "approved": asset.approved,
        "locked": asset.locked,
    }


@router.post("/projects/{project_id}/freeze/episode-plans/{version}/unlock")
def unlock_episode_plan(
    project_id: str,
    version: int,
    body: UnlockBody,
    actor: Operator,
    db: DBSession,
) -> dict:
    """Return a confirmed episode plan to DRAFT and stale its confirmed packages."""
    require_project(db, project_id)
    plan = db.scalars(
        select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project_id,
            DirectorEpisodePlan.version == version,
        )
    ).first()
    if plan is None:
        raise HTTPException(404, "EPISODE_PLAN_NOT_FOUND")
    if plan.status != "CONFIRMED":
        raise HTTPException(409, "EPISODE_PLAN_NOT_CONFIRMED")
    packages = list(
        db.scalars(
            select(DirectorPackageVersion).where(
                DirectorPackageVersion.project_id == project_id,
                DirectorPackageVersion.episode_plan_id == plan.id,
            )
        ).all()
    )
    package_ids = [row.id for row in packages]
    if package_ids:
        active_run = db.scalars(
            select(ProductionRun).where(
                ProductionRun.project_id == project_id,
                ProductionRun.package_id.in_(package_ids),
                ProductionRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        ).first()
        if active_run is not None:
            raise HTTPException(
                409,
                {
                    "code": "FREEZE_UNLOCK_RUN_ACTIVE",
                    "message": "这部作品还在按这份分集方案运行，先暂停或取消再解锁",
                    "run_id": active_run.id,
                },
            )
    stale_packages: list[str] = []
    for package in packages:
        if package.status == "CONFIRMED":
            package.stale = True
            package.stale_reason = "EPISODE_PLAN_UNFROZEN"
            stale_packages.append(package.id)
    plan.status = "DRAFT"
    plan.confirmed_by = None
    plan.confirmed_at = None
    add_audit(
        db,
        actor,
        "EPISODE_PLAN_UNFROZEN",
        "director_episode_plan",
        plan.id,
        {"version": plan.version, "stale_package_ids": sorted(stale_packages), "reason": body.reason},
        project_id=project_id,
    )
    db.commit()
    return {
        "id": plan.id,
        "version": plan.version,
        "status": plan.status,
        "stale_package_ids": sorted(stale_packages),
    }


@router.post("/projects/{project_id}/freeze/director-packages/{package_id}/unlock")
def unlock_director_package(
    project_id: str,
    package_id: str,
    body: UnlockBody,
    actor: Operator,
    db: DBSession,
) -> dict:
    """Mark a confirmed package stale without rewriting its confirmed snapshot."""
    require_project(db, project_id)
    package = db.get(DirectorPackageVersion, package_id)
    if package is None or package.project_id != project_id:
        raise HTTPException(404, "DIRECTOR_PACKAGE_NOT_FOUND")
    if package.status != "CONFIRMED":
        raise HTTPException(409, "DIRECTOR_PACKAGE_NOT_CONFIRMED")
    active_run = db.scalars(
        select(ProductionRun).where(
            ProductionRun.project_id == project_id,
            ProductionRun.package_id == package.id,
            ProductionRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    ).first()
    if active_run is not None:
        raise HTTPException(
            409,
            {
                "code": "FREEZE_UNLOCK_RUN_ACTIVE",
                "message": "这部作品还在按这个导演包运行，先暂停或取消再解锁",
                "run_id": active_run.id,
            },
        )
    package.stale = True
    package.stale_reason = "MANUAL_UNLOCK"
    add_audit(
        db,
        actor,
        "DIRECTOR_PACKAGE_UNFROZEN",
        "director_package",
        package.id,
        {"revision": package.revision, "reason": body.reason},
        project_id=project_id,
    )
    db.commit()
    return {
        "id": package.id,
        "revision": package.revision,
        "status": package.status,
        "stale": package.stale,
        "stale_reason": package.stale_reason,
    }

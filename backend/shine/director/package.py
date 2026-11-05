"""Build, inspect, confirm and invalidate the immutable director package.

The package is a *snapshot of identities*, never a copy of the assets: it stores
ids, revisions and hashes of objects that already exist under their own
governance.  It cannot approve an asset, freeze a style or apply a professional
proposal — those are user actions and stay outside this module.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director.contracts import (
    EpisodePlanPayload,
    continuity_mode,
    digest_of,
    planned_seconds,
    scope_digest,
    shot_order,
    validate_episode_coverage,
)
from shine.director.models import (
    DirectorEpisodePlan,
    DirectorPackageEpisode,
    DirectorPackageVersion,
)
from shine.models import (
    AgentArtifact,
    Asset,
    Project,
    Shot,
    StyleVersion,
    WorkflowProfile,
    utcnow,
)
from shine.schemas import ShotSpec

PACKAGE_SCHEMA_VERSION = "1.0"
DEFAULT_POLICY: dict[str, Any] = {
    "auto_repair": True,
    "max_repairs_per_shot": 2,
    "batch_shot_limit": 6,
    "stop_on_blocker": False,
    "deliver_partial_episode": False,
}
"""The default execution policy a package freezes.

Values are recorded, not guessed at run time: a run executes the policy it
started with even if the project default changes later.
"""


def _style_snapshot(db: Session, project: Project) -> dict[str, Any] | None:
    if not project.frozen_style_version_id:
        return None
    style = db.get(StyleVersion, project.frozen_style_version_id)
    if style is None or style.project_id != project.id:
        return None
    return {"style_version_id": style.id, "version": style.version, "sha256": style.sha256}


def _script_ref(db: Session, project_id: str) -> dict[str, Any]:
    """The confirmed script identity, when the project has one.

    A project may legitimately have no imported document (ideas entered directly
    in the storyboard).  The package records ``None`` rather than inventing a
    script version.
    """
    from shine.models import ProjectDocument

    document = db.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.created_at.desc())
    ).first()
    outline = db.scalars(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "StoryOutline",
            AgentArtifact.stale.is_(False),
        )
        .order_by(AgentArtifact.created_at.desc())
    ).first()
    return {
        "document_id": document.id if document else None,
        "document_sha256": document.sha256 if document else None,
        "outline_artifact_id": outline.id if outline else None,
        "outline_sha256": outline.sha256 if outline else None,
        "outline_confirmed": bool(outline and outline.approved_at),
    }


def _binding_ref(db: Session, project_id: str) -> dict[str, Any] | None:
    from shine.comfyops.preproduction import current_plan

    plan = current_plan(db, project_id)
    if plan is None:
        return None
    return {"plan_id": plan.id, "status": plan.status, "sha256": plan.sha256}


def _shot_manifest(db: Session, shot: Shot) -> dict[str, Any]:
    spec = ShotSpec.model_validate(shot.spec)
    bindings = []
    for link in sorted(shot.bindings, key=lambda row: row.ordinal):
        asset = db.get(Asset, link.asset_id)
        bindings.append(
            {
                "asset_id": link.asset_id,
                "use_as": link.use_as,
                "role": link.role,
                "frame_role": link.frame_role,
                "ordinal": link.ordinal,
                "sha256": asset.sha256 if asset else None,
                "approved": bool(asset and asset.approved),
                "locked": bool(asset and asset.locked),
                "is_master": bool(asset and asset.approved and asset.locked),
            }
        )
    return {
        "shot_id": shot.id,
        "revision": shot.revision,
        "name": shot.name,
        "spec_sha256": digest_of(shot.spec),
        "duration_target_s": spec.duration_target_s,
        "style_version_id": spec.style_version_id,
        "continuity_mode": continuity_mode(shot),
        "characters": list(spec.characters),
        "dialogue_line_count": len(spec.dialogue),
        "bindings": bindings,
    }


def latest_confirmed_plan(db: Session, project_id: str) -> DirectorEpisodePlan | None:
    return db.scalars(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == project_id, DirectorEpisodePlan.status == "CONFIRMED")
        .order_by(DirectorEpisodePlan.version.desc())
    ).first()


def latest_package(db: Session, project_id: str) -> DirectorPackageVersion | None:
    return db.scalars(
        select(DirectorPackageVersion)
        .where(DirectorPackageVersion.project_id == project_id)
        .order_by(DirectorPackageVersion.revision.desc())
    ).first()


def build_package(
    db: Session,
    project: Project,
    *,
    workflow_profile_id: str,
    generation_mode: str | None = None,
    output: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    created_by: str = "user",
) -> DirectorPackageVersion:
    """Snapshot the current confirmed project state into a new package revision.

    Building never mutates the underlying objects and never assumes that a state
    is executable: an incomplete package is still built (so the person can see
    exactly what is missing) and stays ``DRAFT``.
    """
    plan = latest_confirmed_plan(db, project.id)
    if plan is None:
        raise HTTPException(409, "DIRECTOR_EPISODE_PLAN_NOT_CONFIRMED")

    shots = list(db.scalars(select(Shot).where(Shot.project_id == project.id)))
    if not shots:
        raise HTTPException(422, "DIRECTOR_PACKAGE_REQUIRES_SHOTS")
    by_id = {shot.id: shot for shot in shots}
    plan_payload = EpisodePlanPayload.model_validate(plan.payload)
    problems = validate_episode_coverage(plan_payload, by_id.keys())
    if problems:
        raise HTTPException(409, {"code": "DIRECTOR_EPISODE_PLAN_STALE", "problems": problems})

    profile = db.get(WorkflowProfile, workflow_profile_id)
    if profile is None or (profile.project_id not in (None, project.id)):
        raise HTTPException(404, "WORKFLOW_PROFILE_NOT_FOUND")

    style = _style_snapshot(db, project)
    binding = {
        "workflow_profile_id": profile.id,
        "mode": profile.mode,
        "graph_sha256": profile.graph_sha256,
        "checkpoint_id": profile.checkpoint_id,
        "model_identity": profile.model_identity or {},
        "parameter_map": profile.parameter_map or {},
        "media_transfers": profile.media_transfers or {},
        "generation_mode": generation_mode,
        "output": output or {},
        "validated_against": profile.validated_against or {},
    }
    resolved_policy = {**DEFAULT_POLICY, **(policy or {})}

    revision = (db.scalars(
        select(DirectorPackageVersion.revision)
        .where(DirectorPackageVersion.project_id == project.id)
        .order_by(DirectorPackageVersion.revision.desc())
        .limit(1)
    ).first() or 0) + 1

    episodes: list[dict[str, Any]] = []
    for planned in plan_payload.episodes:
        manifest_shots = [_shot_manifest(db, by_id[shot_id]) for shot_id in planned.shot_ids]
        episodes.append(
            {
                "episode_id": planned.episode_id,
                "position": planned.order,
                "title": planned.title,
                "planned_seconds": planned.planned_seconds,
                "boundary_note": planned.boundary_note,
                "shots": manifest_shots,
            }
        )

    root = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "project_id": project.id,
        "revision": revision,
        "episode_plan": {
            "plan_id": plan.id,
            "version": plan.version,
            "sha256": plan.sha256,
            "target_seconds": plan_payload.target_seconds,
            "tolerance_seconds": plan_payload.tolerance_seconds,
        },
        "script": _script_ref(db, project.id),
        "style": style,
        "binding": binding,
        "preproduction": _binding_ref(db, project.id),
        "policy": resolved_policy,
        "episodes": [
            {
                "episode_id": episode["episode_id"],
                "position": episode["position"],
                "title": episode["title"],
                "planned_seconds": episode["planned_seconds"],
                "shot_count": len(episode["shots"]),
                "manifest_sha256": digest_of(episode),
            }
            for episode in episodes
        ],
    }
    root["scope_digest"] = scope_digest(
        project.id, episodes, style=style, binding=binding, script=root["script"]
    )

    package = DirectorPackageVersion(
        project_id=project.id,
        revision=revision,
        status="DRAFT",
        manifest=root,
        scope_digest=root["scope_digest"],
        manifest_sha256=digest_of(root),
        policy_hash=digest_of(resolved_policy),
        episode_count=len(episodes),
        shot_count=sum(len(episode["shots"]) for episode in episodes),
        episode_plan_id=plan.id,
        created_by=created_by,
    )
    db.add(package)
    db.flush()
    for episode in episodes:
        db.add(
            DirectorPackageEpisode(
                package_id=package.id,
                project_id=project.id,
                episode_id=episode["episode_id"],
                position=episode["position"],
                title=episode["title"],
                manifest=episode,
                manifest_sha256=digest_of(episode),
                shot_count=len(episode["shots"]),
                planned_seconds=episode["planned_seconds"],
            )
        )
    db.flush()
    return package


def _blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def package_readiness(db: Session, package: DirectorPackageVersion) -> dict[str, Any]:
    """Everything that must be true before this package may really be started.

    The checks are read-only and repeatable.  A missing dynamic last frame is a
    *declared dependency*, not a readiness failure: the final request is built
    from the real passed predecessor at submit time by the one compiler.
    """
    project = db.get(Project, package.project_id)
    blockers: list[dict[str, Any]] = []
    manifest = package.manifest or {}

    if project is None:
        blockers.append(_blocker("PROJECT_MISSING", "作品已不存在"))
    if package.stale:
        blockers.append(_blocker("PACKAGE_STALE", package.stale_reason or "导演包已失效"))
    if package.status == "DRAFT":
        blockers.append(_blocker("PACKAGE_NOT_CONFIRMED", "导演包尚未确认"))

    style = manifest.get("style")
    if not style:
        blockers.append(_blocker("STYLE_NOT_FROZEN", "尚未冻结作品风格，无法开拍"))
    elif project is not None:
        pinned_style_id = style.get("style_version_id") or ""
        current = db.get(StyleVersion, pinned_style_id)
        if (project.frozen_style_version_id != pinned_style_id
                or current is None or current.sha256 != style.get("sha256")):
            blockers.append(_blocker("STYLE_SUPERSEDED", "冻结风格已变更，请重新生成导演包"))

    profile_row = db.get(WorkflowProfile, (manifest.get("binding") or {}).get("workflow_profile_id") or "")
    if profile_row is None:
        blockers.append(_blocker("WORKFLOW_PROFILE_MISSING", "生成方案已不存在"))
    elif profile_row.graph_sha256 != (manifest.get("binding") or {}).get("graph_sha256"):
        blockers.append(_blocker("WORKFLOW_PROFILE_CHANGED", "生成方案图已变更，请重新生成导演包"))
    elif not (profile_row.parameter_map or {}):
        blockers.append(_blocker("WORKFLOW_PARAMETER_MAP_MISSING", "生成方案未声明参数映射"))

    from shine.comfyops.preproduction import current_plan

    plan = current_plan(db, package.project_id)
    pinned = manifest.get("preproduction") or {}
    if plan is not None and plan.status != "FROZEN":
        # The same rule ``comfyops.preproduction.enforce`` applies to every real
        # job: an analysed plan must be frozen before it may govern a render.
        # A project without a plan is a legacy project and keeps that contract;
        # the package does not invent a stricter gate than the gate itself.
        blockers.append(_blocker("PREPRODUCTION_NOT_FROZEN", "制片准备计划尚未冻结"))
    elif plan is not None and pinned.get("sha256") and plan.sha256 != pinned.get("sha256"):
        blockers.append(_blocker("PREPRODUCTION_CHANGED", "制片准备计划已变更，请重新生成导演包"))

    episodes = db.scalars(
        select(DirectorPackageEpisode)
        .where(DirectorPackageEpisode.package_id == package.id)
        .order_by(DirectorPackageEpisode.position)
    ).all()
    if not episodes:
        blockers.append(_blocker("PACKAGE_HAS_NO_EPISODES", "导演包没有分集"))
    for episode in episodes:
        if digest_of(episode.manifest) != episode.manifest_sha256:
            blockers.append(
                _blocker("EPISODE_MANIFEST_TAMPERED", f"{episode.title or episode.episode_id} 的分集清单被改动",
                         episode_id=episode.episode_id)
            )
        for shot in episode.manifest.get("shots", []):
            row = db.get(Shot, shot.get("shot_id") or "")
            if row is None:
                blockers.append(_blocker("SHOT_MISSING", f"镜头「{shot.get('name')}」已不存在",
                                         shot_id=shot.get("shot_id")))
                continue
            if row.revision != shot.get("revision"):
                blockers.append(_blocker("SHOT_REVISION_CHANGED", f"镜头「{row.name}」已修改",
                                         shot_id=row.id))
            if style and shot.get("style_version_id") != style.get("style_version_id"):
                blockers.append(_blocker("SHOT_STYLE_MISMATCH", f"镜头「{row.name}」不执行当前冻结风格",
                                         shot_id=row.id))
            for link in shot.get("bindings", []):
                asset = db.get(Asset, link.get("asset_id") or "")
                if asset is None:
                    blockers.append(_blocker("ASSET_MISSING", "参考素材已不存在",
                                             shot_id=shot.get("shot_id"), asset_id=link.get("asset_id")))
                elif not (asset.approved and asset.locked):
                    blockers.append(_blocker("ASSET_NOT_MASTER", f"素材「{asset.filename}」尚未确认并锁定",
                                             shot_id=shot.get("shot_id"), asset_id=asset.id))

    fingerprint = digest_of(
        {
            "graph": (manifest.get("binding") or {}).get("graph_sha256"),
            "style": (manifest.get("style") or {}).get("sha256"),
            "preproduction": pinned.get("sha256"),
            "policy": package.policy_hash,
        }
    )
    # ``ready`` answers "is the content satisfiable right now" — everything a
    # confirmation would have to fix.  ``startable`` additionally requires the
    # user's confirmation, because a confirmed package is a separate decision
    # from a satisfiable one.
    hard = [row for row in blockers if row["code"] != "PACKAGE_NOT_CONFIRMED"]
    return {
        "package_id": package.id,
        "revision": package.revision,
        "status": package.status,
        "stale": package.stale,
        "scope_digest": package.scope_digest,
        "manifest_sha256": package.manifest_sha256,
        "config_fingerprint": fingerprint,
        "ready": not hard,
        "startable": not blockers and not package.stale,
        "blockers": blockers,
        "episode_count": len(episodes),
        "shot_count": package.shot_count,
        "checked_at": utcnow().isoformat() + "Z",
    }


def confirm_package(db: Session, package: DirectorPackageVersion, *, actor: str) -> DirectorPackageVersion:
    if package.status == "CONFIRMED":
        return package
    if package.stale:
        raise HTTPException(409, "DIRECTOR_PACKAGE_STALE")
    readiness = package_readiness(db, package)
    hard = [row for row in readiness["blockers"] if row["code"] not in {"PACKAGE_NOT_CONFIRMED"}]
    if hard:
        raise HTTPException(409, {"code": "DIRECTOR_PACKAGE_NOT_READY", "blockers": hard})
    package.status = "CONFIRMED"
    package.confirmed_by = actor
    package.confirmed_at = utcnow()
    db.flush()
    return package


def mark_package_stale(db: Session, package: DirectorPackageVersion, reason: str) -> bool:
    """Invalidate a package without rewriting what was confirmed."""
    if package.stale and package.stale_reason == reason:
        return False
    package.stale = True
    package.stale_reason = reason[:200]
    return True


def package_public(
    package: DirectorPackageVersion, *, episodes: list[DirectorPackageEpisode] | None = None
) -> dict[str, Any]:
    manifest = package.manifest or {}
    return {
        "id": package.id,
        "project_id": package.project_id,
        "revision": package.revision,
        "status": package.status,
        "stale": package.stale,
        "stale_reason": package.stale_reason,
        "scope_digest": package.scope_digest,
        "manifest_sha256": package.manifest_sha256,
        "policy_hash": package.policy_hash,
        "episode_count": package.episode_count,
        "shot_count": package.shot_count,
        "episode_plan": manifest.get("episode_plan"),
        "script": manifest.get("script"),
        "style": manifest.get("style"),
        "binding": manifest.get("binding"),
        "policy": manifest.get("policy"),
        "episodes": [
            {
                "episode_id": row.episode_id,
                "position": row.position,
                "title": row.title,
                "shot_count": row.shot_count,
                "planned_seconds": row.planned_seconds,
                "manifest_sha256": row.manifest_sha256,
            }
            for row in (episodes or [])
        ],
        "created_at": package.created_at.isoformat() + "Z",
        "confirmed_at": package.confirmed_at.isoformat() + "Z" if package.confirmed_at else None,
    }


def package_episodes(db: Session, package_id: str) -> list[DirectorPackageEpisode]:
    return list(db.scalars(
        select(DirectorPackageEpisode)
        .where(DirectorPackageEpisode.package_id == package_id)
        .order_by(DirectorPackageEpisode.position)
    ).all())


def episode_manifest(package_episode: DirectorPackageEpisode) -> dict[str, Any]:
    """The referenced sub-manifest, re-verified against the root's pinned hash."""
    if digest_of(package_episode.manifest) != package_episode.manifest_sha256:
        raise HTTPException(409, "DIRECTOR_EPISODE_MANIFEST_TAMPERED")
    return package_episode.manifest


def ordered_shot_ids(shots: list[Shot]) -> list[str]:
    return [shot.id for shot in shot_order(shots)]


def planned_total_seconds(shots: list[Shot]) -> float:
    return round(sum(planned_seconds(shot) for shot in shots), 3)


def hash_file_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

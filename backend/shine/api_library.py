"""The project's local video library: every produced byte, one row per file.

The page is a management view over the existing ledger, not a second truth:
single-shot renders come from ``media_artifacts`` of kind ``video`` and reels
from kind ``delivery``.  A delivery artifact is listed once even when several
delivery receipts name it; thumbnails and continuity derivatives are never
listed as films.  Deleting a history video removes the bytes and leaves a
tombstone, so the job, the delivery receipt and the audit trail still point at
the same artifact instead of being rewritten into another version.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.media.artifacts import safe_asset_path
from shine.media.video_naming import episode_for_job, project_shot_order, shot_context
from shine.models import (
    Job,
    MediaArtifact,
    Project,
    QualityGate,
    Shot,
)
from shine.production.models import ProductionRunEpisode
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["video-library"])

VIDEO_KINDS = ("video", "delivery")
ACTIVE_JOB_STATUSES = {
    "PLANNED", "QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING", "NEEDS_REVIEW",
}


class DeleteVideosRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=200)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _final_gate(db, job_id: str) -> QualityGate | None:
    return db.scalar(
        select(QualityGate)
        .where(QualityGate.job_id == job_id, QualityGate.gate_name == "final")
        .order_by(QualityGate.created_at.desc())
        .limit(1)
    )


def _technical_evidence(db, job_id: str) -> dict[str, Any]:
    gate = db.scalar(
        select(QualityGate)
        .where(QualityGate.job_id == job_id, QualityGate.gate_name == "technical")
        .order_by(QualityGate.created_at.desc())
        .limit(1)
    )
    evidence = gate.evidence if gate and isinstance(gate.evidence, dict) else {}
    return {
        "duration_s": evidence.get("duration_s"),
        "width": evidence.get("width"),
        "height": evidence.get("height"),
        "fps": evidence.get("fps"),
    }


def _file_state(asset_root, artifact: MediaArtifact) -> str:
    try:
        path = safe_asset_path(asset_root, artifact.storage_path)
    except HTTPException:
        return "path_invalid"
    if not path.is_file():
        return "missing"
    if path.stat().st_size != artifact.byte_size:
        return "changed"
    return "ok"


def _delivery_references(db, project_id: str) -> dict[str, set[str]]:
    """What valid reels and continuity relations still depend on.

    Any non-deleted reel names its source artifacts in its own provenance;
    assigning an episode is not required for a reel to count.  A deleted reel
    no longer protects anything, because its bytes are already gone.
    """
    videos: set[str] = set()
    continuity: set[str] = set()
    rows = db.scalars(
        select(MediaArtifact).where(
            MediaArtifact.project_id == project_id,
            MediaArtifact.artifact_kind.in_(("delivery", "continuity")),
            MediaArtifact.deleted_at.is_(None),
        )
    ).all()
    for row in rows:
        lineage = row.provenance if isinstance(row.provenance, dict) else {}
        if row.artifact_kind == "delivery":
            for clip in lineage.get("clips") or []:
                if isinstance(clip, dict) and clip.get("artifact_id"):
                    videos.add(str(clip["artifact_id"]))
        elif row.artifact_kind == "continuity":
            source = lineage.get("source") if isinstance(lineage.get("source"), dict) else {}
            if source.get("artifact_id"):
                continuity.add(str(source["artifact_id"]))
    return {"delivery": videos, "continuity": continuity}


def _current_shot_artifacts(db, project_id: str) -> set[str]:
    """The adopted take per shot: selection first, else the normal series.

    A retake that merely passed is not 'current' until the person selects it;
    the generation-segments selection and the normal series are the two
    authorities, in that order.
    """
    from shine.media.video_layout import current_job_ids

    artifacts = set()
    for job_id in current_job_ids(db, project_id).values():
        job = db.get(Job, job_id)
        if job is not None and job.artifact_id:
            artifacts.add(job.artifact_id)
    return artifacts


def _current_delivery_artifacts(db, project_id: str) -> set[str]:
    """One current reel per delivery scope, plus every episode's delivered reel."""
    from shine.media.delivery_models import DeliveryOrder

    latest: dict[tuple[str, str], tuple[int, str]] = {}
    orders = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.project_id == project_id,
            DeliveryOrder.artifact_id.is_not(None),
        )
    ).all()
    for order in orders:
        scope = (order.scope_kind, order.scope_id or order.series_id or "")
        prior = latest.get(scope)
        if prior is None or order.version >= prior[0]:
            latest[scope] = (order.version, order.artifact_id)
    current = {artifact_id for _version, artifact_id in latest.values()}
    episodes = db.scalars(
        select(ProductionRunEpisode).where(
            ProductionRunEpisode.project_id == project_id,
            ProductionRunEpisode.delivered_artifact_id.is_not(None),
        )
    ).all()
    current.update(episode.delivered_artifact_id for episode in episodes if episode.delivered_artifact_id)
    return current


def _take_numbers(db, project_id: str) -> dict[str, int]:
    """The 1-based take number of each video artifact within its shot."""
    rows = db.scalars(
        select(MediaArtifact)
        .join(Job, Job.id == MediaArtifact.job_id)
        .where(
            MediaArtifact.project_id == project_id,
            MediaArtifact.artifact_kind == "video",
        )
        .order_by(MediaArtifact.created_at)
    ).all()
    seen: dict[str, int] = {}
    numbers: dict[str, int] = {}
    for row in rows:
        job = db.get(Job, row.job_id)
        if job is None:
            continue
        seen[job.shot_id] = seen.get(job.shot_id, 0) + 1
        numbers[row.id] = seen[job.shot_id]
    return numbers


def _episode_for_delivery(db, artifact: MediaArtifact, source_jobs: list[Job]) -> tuple[int | None, str]:
    from shine.media.delivery_models import DeliveryOrder

    orders = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.project_id == artifact.project_id,
            DeliveryOrder.artifact_id == artifact.id,
            DeliveryOrder.scope_kind == "episode",
        )
    ).all()
    for order in orders:
        episode = db.get(ProductionRunEpisode, order.scope_id) if order.scope_id else None
        if episode is not None:
            return episode.position, f"{episode.position:02d}集"
    episodes = db.scalars(
        select(ProductionRunEpisode).where(
            ProductionRunEpisode.project_id == artifact.project_id,
            ProductionRunEpisode.delivered_artifact_id == artifact.id,
        )
    ).all()
    if episodes:
        return episodes[0].position, f"{episodes[0].position:02d}集"
    positions = set()
    for job in source_jobs:
        position, _label = episode_for_job(db, job)
        if position is not None:
            positions.add(position)
    if len(positions) == 1:
        position = next(iter(positions))
        return position, f"{position:02d}集"
    return None, "未分集"


def _entry_base(artifact: MediaArtifact, project: Project, asset_root) -> dict[str, Any]:
    file_state = _file_state(asset_root, artifact)
    parts = artifact.storage_path.replace("\\", "/").split("/")
    folder = "current" if "current" in parts else ("history" if "history" in parts else "legacy")
    return {
        "artifact_id": artifact.id,
        "storage_path": artifact.storage_path,
        "folder": folder,
        "file_name": artifact.storage_path.replace("\\", "/").rsplit("/", 1)[-1],
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "content_type": artifact.content_type,
        "generated_at": _iso(artifact.created_at),
        "is_mock": bool(artifact.is_mock),
        "deleted": artifact.deleted_at is not None,
        "deleted_at": _iso(artifact.deleted_at),
        "file_state": file_state,
        "project_id": project.id,
        "project_title": project.title,
    }


def _shot_entry(db, artifact, project, asset_root, *, current_ids, takes) -> dict[str, Any]:
    job = db.get(Job, artifact.job_id) if artifact.job_id else None
    shot = db.get(Shot, job.shot_id) if job else None
    order = project_shot_order(db, project.id)
    number, episode = shot_context(db, job, project_order=order) if job else (None, "未分集")
    technical = _technical_evidence(db, job.id) if job else {}
    final = _final_gate(db, job.id) if job else None
    if artifact.is_mock or (job and job.provider == "mock"):
        review_state = "mock"
    elif job and job.status == "PASSED" and final is not None and final.status == "passed":
        review_state = "passed"
    elif job and job.status == "PASSED":
        review_state = "pending_review"
    elif job and job.status in ACTIVE_JOB_STATUSES:
        review_state = "running" if job.status != "NEEDS_REVIEW" else "needs_attention"
    else:
        review_state = "needs_attention"
    entry = _entry_base(artifact, project, asset_root)
    entry.update({
        "kind": "shot",
        "shot_id": shot.id if shot else None,
        "shot_name": shot.name if shot else None,
        "job_id": job.id if job else None,
        "episode_position": int(episode[:2]) if episode != "未分集" else None,
        "episode_label": episode,
        "shot_numbers": [number] if number is not None else [],
        "shot_range_label": f"{number:03d}" if number is not None else "镜号未登记",
        "shot_names": [shot.name] if shot else [],
        "take": takes.get(artifact.id, 1),
        "version_label": f"第{takes.get(artifact.id, 1)}次拍摄",
        "review_state": review_state,
        "duration_s": technical.get("duration_s"),
        "width": technical.get("width"),
        "height": technical.get("height"),
        "fps": technical.get("fps"),
        "adopted": artifact.id in current_ids,
        # The query names the poster contract.  It also busts an older cached
        # first-frame cover that was stored as immutable under the bare URL.
        "preview_url": f"/api/v1/jobs/{job.id}/thumbnail?poster=1s" if job and entry["file_state"] == "ok" else None,
        "play_url": f"/api/v1/jobs/{job.id}/media" if job and entry["file_state"] == "ok" else None,
        "delete_blockers": [],
        "deletable": True,
        "shared_file": _shares_file(db, artifact),
    })
    return entry


def _delivery_entry(db, artifact, project, asset_root, *, current_ids) -> dict[str, Any]:
    lineage = artifact.provenance if isinstance(artifact.provenance, dict) else {}
    source_jobs: list[Job] = []
    for clip in lineage.get("clips") or []:
        job = db.get(Job, str(clip.get("job_id"))) if isinstance(clip, dict) and clip.get("job_id") else None
        if job is not None and job.project_id == project.id:
            source_jobs.append(job)
    order = project_shot_order(db, project.id)
    numbers: list[int] = []
    names: list[str] = []
    for job in source_jobs:
        number, _episode = shot_context(db, job, project_order=order)
        if number is not None:
            numbers.append(number)
        shot = db.get(Shot, job.shot_id)
        if shot is not None:
            names.append(shot.name)
    if numbers:
        low, high = min(numbers), max(numbers)
        range_label = f"{low:03d}" if low == high else f"{low:03d}-{high:03d}"
    else:
        range_label = "镜号未登记"
    position, episode = _episode_for_delivery(db, artifact, source_jobs)
    version = _delivery_version(db, artifact)
    review_state = "mock" if artifact.is_mock else (
        "passed" if lineage.get("review_state") == "all_passed" else "pending_review"
    )
    entry = _entry_base(artifact, project, asset_root)
    entry.update({
        "kind": "delivery",
        "shot_id": None,
        "shot_name": None,
        "job_id": None,
        "episode_position": position,
        "episode_label": episode,
        "shot_numbers": sorted(set(numbers)),
        "shot_range_label": range_label,
        "shot_names": names,
        "take": version,
        "version_label": f"v{version}",
        "review_state": review_state,
        "duration_s": lineage.get("duration_s"),
        "width": lineage.get("width"),
        "height": lineage.get("height"),
        "fps": lineage.get("fps"),
        "adopted": artifact.id in current_ids,
        "preview_url": f"/api/v1/deliveries/{artifact.id}/thumbnail?poster=1s" if entry["file_state"] == "ok" else None,
        "play_url": f"/api/v1/deliveries/{artifact.id}/media" if entry["file_state"] == "ok" else None,
        "delete_blockers": [],
        "deletable": True,
        "shared_file": _shares_file(db, artifact),
    })
    return entry


def _delivery_version(db, artifact: MediaArtifact) -> int:
    from shine.media.delivery_models import DeliveryOrder

    orders = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.project_id == artifact.project_id,
            DeliveryOrder.artifact_id == artifact.id,
        )
    ).all()
    return max((order.version for order in orders), default=1)


def _shares_file(db, artifact: MediaArtifact) -> bool:
    """A second live artifact row naming the same bytes on disk."""
    return bool(db.scalar(
        select(MediaArtifact.id).where(
            MediaArtifact.storage_path == artifact.storage_path,
            MediaArtifact.id != artifact.id,
            MediaArtifact.deleted_at.is_(None),
        ).limit(1)
    ))


def _common_blockers(artifact, file_state: str, current_ids, references) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if file_state == "path_invalid":
        blockers.append({"code": "PATH_OUTSIDE_DATA_DIR", "message": "文件路径不在本机数据目录内"})
    if file_state == "changed":
        blockers.append({"code": "ARTIFACT_HASH_MISMATCH", "message": "磁盘文件与登记哈希不一致，拒绝删除"})
    if artifact.id in current_ids:
        blockers.append({"code": "CURRENT_VERSION", "message": "这是当前采用的版本"})
    if artifact.id in references["delivery"]:
        blockers.append({"code": "USED_BY_DELIVERY", "message": "仍被一份有效成片引用"})
    if artifact.id in references["continuity"]:
        blockers.append({"code": "USED_BY_CONTINUITY", "message": "仍被连续性关系引用"})
    return blockers


def _blockers(db, artifact, job, current_ids, references, asset_root) -> list[dict[str, str]]:
    blockers = _common_blockers(artifact, _file_state(asset_root, artifact), current_ids, references)
    if artifact.artifact_kind == "video" and job is not None and job.status in ACTIVE_JOB_STATUSES:
        blockers.append({"code": "ACTIVE_JOB", "message": "这个镜头的任务仍在进行或待核对"})
    return blockers


@router.get("/projects/{project_id}/video-library")
def video_library(project_id: str, request: Request, db: DBSession,
                  include_deleted: bool = False) -> dict[str, Any]:
    project = require_project(db, project_id)
    asset_root = request.app.state.asset_root
    query = select(MediaArtifact).where(
        MediaArtifact.project_id == project_id,
        MediaArtifact.artifact_kind.in_(VIDEO_KINDS),
    )
    if not include_deleted:
        query = query.where(MediaArtifact.deleted_at.is_(None))
    artifacts = db.scalars(query.order_by(MediaArtifact.created_at.desc())).all()
    current_shot_ids = _current_shot_artifacts(db, project_id)
    current_delivery_ids = _current_delivery_artifacts(db, project_id)
    current_ids = current_shot_ids | current_delivery_ids
    references = _delivery_references(db, project_id)
    takes = _take_numbers(db, project_id)
    entries = []
    for artifact in artifacts:
        job = db.get(Job, artifact.job_id) if artifact.job_id else None
        blockers = _blockers(db, artifact, job, current_ids, references, asset_root)
        if artifact.artifact_kind == "video":
            entry = _shot_entry(db, artifact, project, asset_root, current_ids=current_ids, takes=takes)
        else:
            entry = _delivery_entry(db, artifact, project, asset_root, current_ids=current_ids)
        entry["deletable"] = not blockers
        entry["delete_blockers"] = blockers
        entries.append(entry)
    summary = {
        "total": len(entries),
        "current": sum(1 for entry in entries if entry["adopted"]),
        "history": sum(1 for entry in entries if not entry["adopted"]),
        "pending_review": sum(
            1 for entry in entries
            if entry["review_state"] in {"pending_review", "running", "needs_attention"}
        ),
        "missing": sum(1 for entry in entries if entry["file_state"] in {"missing", "changed", "path_invalid"}),
        "deletable": sum(1 for entry in entries if entry["deletable"]),
    }
    return {"project_id": project.id, "project_title": project.title, "entries": entries, "summary": summary}


def _blockers_now(db, artifact, project_id, asset_root) -> list[dict[str, str]]:
    current_ids = _current_shot_artifacts(db, project_id) | _current_delivery_artifacts(db, project_id)
    references = _delivery_references(db, project_id)
    job = db.get(Job, artifact.job_id) if artifact.job_id else None
    return _blockers(db, artifact, job, current_ids, references, asset_root)


@router.post("/projects/{project_id}/video-library/delete")
def delete_videos(project_id: str, body: DeleteVideosRequest, request: Request,
                  actor: Operator, db: DBSession) -> dict[str, Any]:
    project = require_project(db, project_id)
    asset_root = request.app.state.asset_root
    unique_ids = list(dict.fromkeys(body.artifact_ids))
    rows = [db.get(MediaArtifact, artifact_id) for artifact_id in unique_ids]
    if any(row is None or row.project_id != project.id or row.artifact_kind not in VIDEO_KINDS
           for row in rows):
        # The server only ever resolves this project's own video artifacts.
        raise HTTPException(422, "VIDEO_LIBRARY_ARTIFACT_NOT_IN_PROJECT")

    result: dict[str, Any] = {
        "project_id": project.id,
        "project_title": project.title,
        "deleted": [],
        "already_deleted": [],
        "missing": [],
        "blocked": [],
        "failed": [],
        "files_deleted": 0,
        "files_kept_shared": 0,
    }
    for artifact in rows:
        artifact_id = artifact.id
        if artifact.deleted_at is not None:
            result["already_deleted"].append(artifact_id)
            continue
        blockers = _blockers_now(db, artifact, project_id, asset_root)
        if blockers:
            result["blocked"].append({"artifact_id": artifact_id, "blockers": blockers})
            db.rollback()
            continue
        try:
            path = safe_asset_path(asset_root, artifact.storage_path)
        except HTTPException:
            result["blocked"].append({"artifact_id": artifact_id, "blockers": [
                {"code": "PATH_OUTSIDE_DATA_DIR", "message": "文件路径不在本机数据目录内"}]})
            db.rollback()
            continue
        file_missing = not path.is_file()
        if not file_missing:
            digest = _digest(path)
            if digest != artifact.sha256 or path.stat().st_size != artifact.byte_size:
                result["blocked"].append({"artifact_id": artifact_id, "blockers": [
                    {"code": "ARTIFACT_HASH_MISMATCH", "message": "磁盘文件与登记哈希不一致，拒绝删除"}]})
                db.rollback()
                continue
        shared = _shares_file(db, artifact)
        reason = "FILE_MISSING" if file_missing else ("SHARED_FILE" if shared else "USER_DELETED")
        try:
            if not file_missing and not shared:
                path.unlink()
                result["files_deleted"] += 1
            elif shared:
                result["files_kept_shared"] += 1
            artifact.deleted_at = _now()
            artifact.deleted_by = actor[:64]
            artifact.deleted_reason = reason
            add_audit(
                db, actor, "VIDEO_ARTIFACT_DELETED", "artifact", artifact.id,
                {
                    "sha256": artifact.sha256,
                    "byte_size": artifact.byte_size,
                    "storage_path": artifact.storage_path,
                    "job_id": artifact.job_id,
                    "reason": reason,
                    "shared_file_kept": shared,
                    "file_missing": file_missing,
                },
                project_id=project_id,
            )
            db.commit()
        except OSError as exc:
            db.rollback()
            result["failed"].append({"artifact_id": artifact_id, "code": "FILE_DELETE_FAILED",
                                     "message": f"删除文件失败：{exc}"})
            continue
        if file_missing:
            result["missing"].append(artifact_id)
        else:
            result["deleted"].append(artifact_id)
    return result


@router.post("/projects/{project_id}/video-library/organize-current")
def organize_current(project_id: str, request: Request, actor: Operator, db: DBSession) -> dict[str, Any]:
    """Move adopted videos into the adopted folder, unadopted ones to history.

    This is an explicit user action: it never touches bytes other than moving
    the file, keeps the SHA identity, and does not delete anything.  It is the
    only way existing files are reorganised; ordinary production and library
    loads never move a file on their own.
    """
    from shine.media.video_layout import (
        CURRENT,
        DELIVERY_CURRENT,
        DELIVERY_HISTORY,
        HISTORY,
        relocate_artifact,
        revert_file_moves,
    )

    project = require_project(db, project_id)
    asset_root = request.app.state.asset_root
    current_shot = _current_shot_artifacts(db, project_id)
    current_delivery = _current_delivery_artifacts(db, project_id)
    artifacts = db.scalars(
        select(MediaArtifact).where(
            MediaArtifact.project_id == project_id,
            MediaArtifact.artifact_kind.in_(VIDEO_KINDS),
            MediaArtifact.deleted_at.is_(None),
        )
    ).all()
    moves: list[tuple[str, str]] = []
    moved: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for artifact in artifacts:
        if artifact.artifact_kind == "video":
            target = CURRENT if artifact.id in current_shot else HISTORY
        else:
            target = DELIVERY_CURRENT if artifact.id in current_delivery else DELIVERY_HISTORY
        try:
            result = relocate_artifact(db, asset_root, artifact, target)
        except (OSError, ValueError) as exc:
            errors.append({"artifact_id": artifact.id, "message": str(exc)[:200]})
            continue
        if result:
            moves.append(result)
            moved.append({"artifact_id": artifact.id, "storage_path": result[1], "target": target})
    add_audit(
        db, actor, "VIDEO_LIBRARY_ORGANIZED", "project", project.id,
        {"moved": moved, "errors": errors}, project_id=project_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        revert_file_moves(asset_root, moves)
        raise
    return {"project_id": project.id, "moved": moved, "errors": errors,
            "files_moved": len(moved)}


def _digest(path) -> str:
    with path.open("rb") as media:
        return hashlib.file_digest(media, "sha256").hexdigest()


def _now():
    from shine.models import utcnow

    return utcnow()

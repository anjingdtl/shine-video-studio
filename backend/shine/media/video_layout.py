"""On-disk layout: adopted videos live apart from unadopted takes.

The ledger keeps the identity (SHA-256); this module only decides which folder
a video is written to and moves a file when a retake is explicitly adopted.
Existing files are never reorganised on their own: history that was produced
before the split stays exactly where it is, and only a user selection moves a
replacement into the adopted folder.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.media.artifacts import safe_asset_path

CURRENT = "current"
HISTORY = "history"
DELIVERY_CURRENT = "delivery/current"
DELIVERY_HISTORY = "delivery/history"
_RETAKE_ROLES = ("segment_regenerate", "segment_assembly")


def retake_series_ids(db: Session, project_id: str) -> set[str]:
    from shine.models import AgentTaskRecord

    rows = db.scalars(
        select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id.in_(_RETAKE_ROLES),
        )
    ).all()
    ids = set()
    for row in rows:
        trace = row.trace if isinstance(row.trace, dict) else {}
        for key in ("series_id", "root_series_id"):
            if trace.get(key):
                ids.add(str(trace[key]))
    return ids


def _job_series(db: Session, job) -> str | None:
    from shine.models import ProductionSeriesItem

    return db.scalar(
        select(ProductionSeriesItem.series_id).where(ProductionSeriesItem.job_id == job.id)
    )


def selected_job_ids(db: Session, project_id: str) -> dict[str, str]:
    """Explicit assembly selections for this project, by shot."""
    from shine.models import AppSetting, Job

    selected: dict[str, str] = {}
    rows = db.scalars(select(AppSetting).where(AppSetting.key.like("assembly-selection:%"))).all()
    for row in rows:
        if not isinstance(row.value, dict):
            continue
        for shot_id, job_id in row.value.items():
            job = db.get(Job, str(job_id))
            if (
                job is not None and job.project_id == project_id and job.shot_id == shot_id
                and job.status == "PASSED" and not job.stale and job.artifact_id
                and job.provider != "mock"
            ):
                selected[shot_id] = job.id
    return selected


def current_job_ids(db: Session, project_id: str) -> dict[str, str]:
    """The adopted job per shot.

    An explicit assembly selection wins.  Otherwise the newest passed normal
    production is current; a retake series is not the adopted timeline until
    the person selects its replacement, so its passed take stays history.
    """
    from shine.models import Job, MediaArtifact

    retakes = retake_series_ids(db, project_id)
    current = selected_job_ids(db, project_id)
    passed = db.scalars(
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.status == "PASSED",
            Job.stale.is_(False),
            Job.provider != "mock",
            Job.artifact_id.is_not(None),
        )
        .order_by(Job.created_at.desc())
    ).all()
    for job in passed:
        if _job_series(db, job) in retakes:
            continue
        current.setdefault(job.shot_id, job.id)
    for shot_id, job_id in list(current.items()):
        job = db.get(Job, job_id)
        artifact = db.get(MediaArtifact, job.artifact_id) if job and job.artifact_id else None
        if artifact is None or artifact.deleted_at is not None:
            current.pop(shot_id, None)
    return current


def shot_output_subdir(db: Session, job) -> str:
    """Adopted first take goes to the adopted folder; a retake waits in history."""
    retakes = retake_series_ids(db, job.project_id)
    if _job_series(db, job) in retakes and selected_job_ids(db, job.project_id).get(job.shot_id) != job.id:
        return HISTORY
    selected = selected_job_ids(db, job.project_id)
    if selected.get(job.shot_id) not in (None, job.id):
        return HISTORY
    return CURRENT


def _digest(path: Path) -> str:
    with path.open("rb") as media:
        return hashlib.file_digest(media, "sha256").hexdigest()


def _unique_target(folder: Path, name: str) -> Path:
    target = folder / name
    index = 2
    stem, suffix = Path(name).stem, Path(name).suffix
    while target.exists():
        target = folder / f"{stem}_v{index}{suffix}"
        index += 1
    return target


def relocate_artifact(db: Session, root: Path, artifact, subdir: str) -> tuple[str, str] | None:
    """Move one video file into ``subdir`` and keep every ledger path in step.

    Returns ``(old_relative, new_relative)`` when a move happened, else None.
    Raises OSError/ValueError on an unverifiable file so a caller can refuse
    before recording anything.
    """
    from shine.models import JobAttempt, MediaArtifact

    old_rel = str(artifact.storage_path)
    source = safe_asset_path(root, old_rel)
    folder = root / "artifacts" / artifact.project_id / subdir
    target = folder / Path(old_rel).name
    if target.resolve() == source.resolve():
        return None
    if not source.is_file() or _digest(source) != artifact.sha256:
        raise ValueError("VIDEO_FILE_NOT_VERIFIABLE")
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == artifact.byte_size and _digest(target) == artifact.sha256:
            source.unlink()
        else:
            target = _unique_target(folder, Path(old_rel).name)
            os.replace(source, target)
    else:
        os.replace(source, target)
    new_rel = target.relative_to(root).as_posix()
    artifact.storage_path = new_rel
    db.execute(
        update(JobAttempt).where(JobAttempt.output_path == old_rel).values(output_path=new_rel)
    )
    # A continuity derivative records the source path in its lineage.  The
    # bytes and identity did not change, so rewrite only that path.
    rows = db.scalars(
        select(MediaArtifact).where(
            MediaArtifact.project_id == artifact.project_id,
            MediaArtifact.artifact_kind == "continuity",
        )
    ).all()
    for row in rows:
        lineage = row.provenance if isinstance(row.provenance, dict) else None
        source_info = lineage.get("source") if lineage and isinstance(lineage.get("source"), dict) else None
        if not source_info or source_info.get("artifact_id") != artifact.id:
            continue
        updated = {**lineage, "source": {**source_info, "storage_path": new_rel}}
        row.provenance = updated
    return old_rel, new_rel


def revert_file_moves(root: Path, moves: list[tuple[str, str]]) -> None:
    """Undo file moves after a failed ledger commit (best effort)."""
    for old_rel, new_rel in reversed(moves):
        try:
            old_path = safe_asset_path(root, old_rel)
            new_path = safe_asset_path(root, new_rel)
            if new_path.is_file() and not old_path.exists():
                old_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(new_path, old_path)
        except (OSError, ValueError):
            continue


def move_adoption(db: Session, root: Path, *, shot_id: str, replacement_job_id: str) -> list[tuple[str, str]]:
    """Put the selected replacement in the adopted folder and the old take in history."""
    from shine.models import Job, MediaArtifact

    moves: list[tuple[str, str]] = []
    try:
        replacement = db.get(Job, replacement_job_id)
        if replacement is None or not replacement.artifact_id:
            return moves
        new_artifact = db.get(MediaArtifact, replacement.artifact_id)
        current = current_job_ids(db, replacement.project_id)
        previous = db.get(Job, current.get(shot_id)) if current.get(shot_id) else None
        if new_artifact is not None and new_artifact.deleted_at is None:
            moved = relocate_artifact(db, root, new_artifact, CURRENT)
            if moved:
                moves.append(moved)
        if previous is not None and previous.id != replacement.id and previous.artifact_id:
            old_artifact = db.get(MediaArtifact, previous.artifact_id)
            if old_artifact is not None and old_artifact.deleted_at is None:
                moved = relocate_artifact(db, root, old_artifact, HISTORY)
                if moved:
                    moves.append(moved)
    except Exception:
        # A half-done pair would leave one take in the wrong folder; restore
        # the first move before the caller records the refusal.
        revert_file_moves(root, moves)
        raise
    return moves

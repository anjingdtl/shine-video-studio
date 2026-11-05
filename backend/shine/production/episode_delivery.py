"""Assemble one episode whose clips come from several production series.

A series order publishes one reel per series.  An episode of a whole-film run is
different: its clips came from more than one series, and publishing each series
as its own reel would be a duplicate release of "the episode".  This module owns
that second scope.  It builds the ordered EDL from the run's plan (not from
creation time), verifies every source identity and byte hash before assembly,
and refuses to publish when a required shot is missing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.media.assembly import DeliveryAssembler, DeliveryClip, DeliveryError
from shine.media.delivery_engine import (
    ASSEMBLY_LEASE_SECONDS,
    DeliveryBlock,
    DeliveryClaim,
    _write_back,
)
from shine.media.delivery_models import DeliveryOrder
from shine.models import Job, MediaArtifact, SubtitleTrack, utcnow
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from shine.security import sha256_bytes

EPISODE_RULES_VERSION = "run-episode-cuts-v1"
EPISODE_PAGE_SIZE = 100


def episode_input_digest(
    db: Session,
    run: ProductionRun,
    episode: ProductionRunEpisode,
    batches: list[ProductionRunBatch],
) -> str:
    """The content identity of one episode version.

    It is built from the plan order plus the real passed artifacts and their
    hashes, so a re-take, a re-cut or a re-ordered plan each produce a different
    version and never silently overwrite a published reel.
    """
    passed = _passed_jobs(db, episode.shot_ids)
    payload = {
        "run_id": run.id,
        "episode_id": episode.episode_id,
        "manifest_sha256": episode.manifest_sha256,
        "batches": [
            [batch.batch_key, batch.input_digest, batch.series_id]
            for batch in sorted(batches, key=lambda row: row.batch_key)
        ],
        "clips": [
            [
                shot_id,
                (job.id if job else ""),
                (job.artifact_id if job else ""),
            ]
            for shot_id, job in ((shot_id, passed.get(shot_id)) for shot_id in episode.shot_ids)
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _passed_jobs(db: Session, shot_ids: list[str]) -> dict[str, Job]:
    from shine.models import Shot

    if not shot_ids:
        return {}
    shots = {shot.id: shot for shot in db.scalars(select(Shot).where(Shot.id.in_(shot_ids))).all()}
    jobs = db.scalars(
        select(Job)
        .where(Job.shot_id.in_(shot_ids), Job.status == "PASSED", Job.stale.is_(False))
        .order_by(Job.created_at)
    ).all()
    passed: dict[str, Job] = {}
    for job in jobs:
        shot = shots.get(job.shot_id)
        if shot is None or shot.revision != job.shot_revision or not job.artifact_id:
            continue
        passed[job.shot_id] = job
    return passed


def build_episode_edl(
    db: Session,
    order: DeliveryOrder,
    *,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
    asset_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """The ordered edit decision list of one episode, from real passed takes."""
    from shine.media.artifacts import safe_asset_path
    from shine.media.probe import probe_file

    episode = db.get(ProductionRunEpisode, order.scope_id or "")
    if episode is None or episode.project_id != order.project_id:
        raise DeliveryBlock("EPISODE_SCOPE_MISSING")
    run = db.get(ProductionRun, episode.run_id)
    if run is None:
        raise DeliveryBlock("EPISODE_RUN_MISSING")

    passed = _passed_jobs(db, episode.shot_ids)
    missing = [shot_id for shot_id in episode.shot_ids if shot_id not in passed]
    if missing:
        raise DeliveryBlock("EPISODE_SHOT_NOT_PASSED")

    clips: list[dict[str, Any]] = []
    for position, shot_id in enumerate(episode.shot_ids):
        job = passed[shot_id]
        attempts = list(job.attempts)
        if not attempts:
            raise DeliveryBlock("EPISODE_SHOT_ATTEMPT_MISSING")
        attempt = max(attempts, key=lambda row: row.attempt_no)
        artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        if artifact is None or artifact.is_mock:
            raise DeliveryBlock("EPISODE_SOURCE_NOT_REAL_VIDEO")
        if artifact.artifact_kind != "video" or artifact.attempt_id != attempt.id:
            raise DeliveryBlock("EPISODE_SOURCE_IDENTITY_MISMATCH")
        if artifact.sha256 != attempt.output_sha256:
            raise DeliveryBlock("EPISODE_SOURCE_IDENTITY_MISMATCH")
        path = safe_asset_path(asset_root, artifact.storage_path)
        if not path.is_file():
            raise DeliveryBlock("EPISODE_SOURCE_FILE_MISSING")
        if sha256_bytes(path.read_bytes()) != artifact.sha256:
            raise DeliveryBlock("EPISODE_SOURCE_FILE_HASH_MISMATCH")
        info = probe_file(path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
        if info.duration_s is None:
            raise DeliveryBlock("EPISODE_SOURCE_DURATION_UNKNOWN")
        clips.append(
            {
                "job_id": job.id,
                "attempt_id": attempt.id,
                "artifact_id": artifact.id,
                "shot_id": shot_id,
                "in_s": 0.0,
                "out_s": round(info.duration_s, 3),
                "transition": "CUT",
                "reason": f"{episode.episode_id} 第 {position + 1} 镜：当前通过的真实产物，整条使用",
            }
        )
    if not clips:
        raise DeliveryBlock("EPISODE_EMPTY_EDIT")
    rules = {
        "rules_version": EPISODE_RULES_VERSION,
        "order": "production_run_episodes.shot_ids",
        "transition": "CUT",
        "in_s": 0.0,
        "out_s": "full_take",
        "episode_id": episode.episode_id,
        "planned_seconds": episode.planned_seconds,
    }
    return {"schema_version": "1.1", "clips": clips}, "run_episode_plan", rules


def _claim_order(db: Session, order: DeliveryOrder, worker_id: str | None) -> bool:
    """The same conditional claim as a series order, scoped to this row."""
    now = utcnow()
    read_version = order.state_version
    fresh = db.execute(
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order.id,
            DeliveryOrder.status == "PENDING",
            DeliveryOrder.state_version == read_version,
        )
        .values(
            status="ASSEMBLING",
            worker_id=worker_id,
            state_version=DeliveryOrder.state_version + 1,
            attempt_count=DeliveryOrder.attempt_count + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if fresh.rowcount == 1:
        return True
    cutoff = now - timedelta(seconds=ASSEMBLY_LEASE_SECONDS)
    reclaimed = db.execute(
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order.id,
            DeliveryOrder.status == "ASSEMBLING",
            DeliveryOrder.state_version == read_version,
            DeliveryOrder.updated_at <= cutoff,
            DeliveryOrder.attempt_count < DeliveryOrder.max_attempts,
        )
        .values(
            status="ASSEMBLING",
            worker_id=worker_id,
            state_version=DeliveryOrder.state_version + 1,
            attempt_count=DeliveryOrder.attempt_count + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return reclaimed.rowcount == 1


def _order_status_now(db: Session, order_id: str) -> str:
    row = db.get(DeliveryOrder, order_id)
    return row.status if row else "GONE"


def run_episode_delivery(
    session_factory,
    order_id: str,
    *,
    settings,
    asset_root: Path,
    worker_id: str | None = None,
) -> str:
    """Execute one episode order: plan EDL -> FFmpeg -> registered episode reel."""
    with session_factory.begin() as db:
        order = db.get(DeliveryOrder, order_id)
        if order is None:
            return "GONE"
        if order.scope_kind != "episode":
            return order.status
        read_version = order.state_version
        if not _claim_order(db, order, worker_id):
            db.refresh(order)
            return order.status
        claim = DeliveryClaim(owner=worker_id, version=read_version + 1)
        project_id = order.project_id
        episode_scope_id = order.scope_id
        attempt = order.attempt_count + 1
        max_attempts = order.max_attempts

    assembler = DeliveryAssembler(
        asset_root=asset_root,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    try:
        with session_factory() as db:
            order = db.get(DeliveryOrder, order_id)
            edl, edl_source, rules = build_episode_edl(
                db,
                order,
                ffprobe_path=settings.ffprobe_path,
                ffmpeg_path=settings.ffmpeg_path,
                asset_root=asset_root,
            )
            subtitle_ass = None
            subtitle_sha256 = None
            if order.subtitle_revision:
                track = db.scalar(
                    select(SubtitleTrack).where(
                        SubtitleTrack.project_id == project_id,
                        SubtitleTrack.revision == order.subtitle_revision,
                    )
                )
                if track is None or track.status != "APPROVED":
                    raise DeliveryBlock("EPISODE_SUBTITLE_NOT_APPROVED")
                subtitle_ass = track.ass
                subtitle_sha256 = track.sha256
            clips = [
                DeliveryClip(
                    job_id=clip["job_id"],
                    attempt_id=clip["attempt_id"],
                    artifact_id=clip["artifact_id"],
                    in_s=clip["in_s"],
                    out_s=clip["out_s"],
                )
                for clip in edl["clips"]
            ]
        with session_factory() as work_db:
            result = assembler.assemble(
                work_db,
                project_id,
                clips,
                fps=24.0,
                width=None,
                height=None,
                crf=18,
                subtitle_ass=subtitle_ass,
                subtitle_sha256=subtitle_sha256,
                reuse_existing=True,
                page_size=EPISODE_PAGE_SIZE,
            )
            work_db.commit()
        with session_factory.begin() as db:
            if not _write_back(db, order_id, claim, {
                "status": "DELIVERED",
                "artifact_id": result.artifact.id,
                "delivered_at": utcnow(),
                "edl_payload": edl,
                "edl_source": edl_source,
                "edit_rules": rules,
                "failure_code": None,
                "failure_note": None,
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            episode = db.get(ProductionRunEpisode, episode_scope_id or "")
            if episode is not None:
                episode.delivery_order_id = order_id
                episode.delivered_artifact_id = result.artifact.id
                episode.delivered_sha256 = result.artifact.sha256
                episode.actual_seconds = float(result.duration_s or 0.0)
                episode.delivered_at = utcnow()
                episode.status = "DELIVERED"
                episode.blocker_code = None
            from shine.services import add_audit

            add_audit(
                db,
                "worker",
                "EPISODE_DELIVERED",
                "delivery_order",
                order_id,
                {
                    "episode_id": episode.episode_id if episode else None,
                    "artifact_id": result.artifact.id,
                    "sha256": result.artifact.sha256,
                    "duration_s": result.duration_s,
                    "review_state": result.review_state,
                    "clip_count": len(result.clips),
                    "edl_source": edl_source,
                },
                project_id=project_id,
            )
    except DeliveryBlock as exc:
        with session_factory.begin() as db:
            if not _write_back(db, order_id, claim, {
                "status": "BLOCKED",
                "failure_code": exc.code,
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            episode = db.get(ProductionRunEpisode, episode_scope_id or "")
            if episode is not None:
                episode.status = "BLOCKED"
                episode.blocker_code = exc.code
            from shine.services import add_audit

            add_audit(db, "worker", "EPISODE_DELIVERY_BLOCKED", "delivery_order", order_id,
                      {"code": exc.code}, project_id=project_id)
    except DeliveryError as exc:
        retryable = exc.status_code >= 500
        with session_factory.begin() as db:
            retry_scheduled = retryable and attempt < max_attempts
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": str(exc.detail)[:400],
                "failure_note": "bounded retry scheduled" if retry_scheduled else "episode assembly refused",
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
    except Exception as exc:  # noqa: BLE001 - the order must record every failure mode
        with session_factory.begin() as db:
            retry_scheduled = attempt < max_attempts and isinstance(exc, OSError | subprocess.SubprocessError)
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": f"EPISODE_DELIVERY_ERROR:{type(exc).__name__}"[:400],
                "failure_note": ("bounded retry scheduled" if retry_scheduled else str(exc)[:400]),
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
    with session_factory() as db:
        return _order_status_now(db, order_id)



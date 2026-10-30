"""Turn a finished production series into a playable reel without a script.

The engine owns the whole automatic path: it enqueues one durable delivery
order per finished series version, generates the EditDecisionList from the
confirmed shot order and the frozen edit intent (or the confirmed default cut
rules), drives FFmpeg through ``DeliveryAssembler``, and registers the result
on the series.  A crash at any point leaves a durable order behind, and the
resident Worker picks pending orders up again; failures retry a bounded
number of times and then report themselves.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.media.assembly import DeliveryAssembler, DeliveryClip, DeliveryError
from shine.media.delivery_models import DeliveryOrder
from shine.models import (
    AgentArtifact,
    Job,
    MediaArtifact,
    ProductionSeries,
    SubtitleTrack,
    utcnow,
)
from shine.security import sha256_bytes

logger = logging.getLogger(__name__)

DELIVERY_VERSION = "auto-delivery-v1"
DEFAULT_CUT_RULES_VERSION = "default-cut-rules-v1"
ASSEMBLY_LEASE_SECONDS = 2400


class DeliveryBlock(Exception):
    """A definite business refusal to assemble; never retried."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _shots_digest(items: list, jobs: dict[str, Job]) -> str:
    """The content identity of one series version: order, shots, jobs, artifacts."""
    payload = []
    for item in sorted(items, key=lambda row: row.position):
        job = jobs.get(item.job_id or "")
        payload.append(
            [
                item.position,
                item.shot_id,
                item.job_id or "",
                job.artifact_id or "" if job is not None else "",
            ]
        )
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def enqueue_auto_delivery(db: Session, series: ProductionSeries) -> DeliveryOrder | None:
    """Create the durable delivery order for a finished series, idempotently.

    Called inside the transaction that completes the series, so the assembly
    work exists as soon as the completion itself exists.  An identical series
    version that was already delivered (or is already queued) never produces a
    second order; a different version (rework, subtitle change) does.
    """
    from shine.media.delivery_models import DeliveryOrder

    items = list(series.items)
    if not items:
        raise DeliveryBlock("DELIVERY_SERIES_HAS_NO_ITEMS")
    job_ids = [item.job_id for item in items if item.job_id]
    jobs = {
        job.id: job
        for job in db.scalars(select(Job).where(Job.id.in_(job_ids))).all()
    }
    unpassed = [
        item.position
        for item in items
        if item.status != "PASSED"
        or not item.job_id
        or item.job_id not in jobs
        or jobs[item.job_id].status != "PASSED"
        or jobs[item.job_id].stale
    ]
    if unpassed:
        raise DeliveryBlock("DELIVERY_SHOT_NOT_PASSED")
    digest = _shots_digest(items, jobs)
    orders = db.scalars(
        select(DeliveryOrder).where(DeliveryOrder.series_id == series.id)
    ).all()
    for order in orders:
        if order.status in {"PENDING", "ASSEMBLING"}:
            return order
        if order.status == "DELIVERED" and order.shots_digest == digest:
            return order
    subtitle = _latest_approved_subtitle(db, series.project_id)
    order = DeliveryOrder(
        project_id=series.project_id,
        series_id=series.id,
        origin="series_auto",
        version=max((row.version for row in orders), default=0) + 1,
        status="PENDING",
        shots_digest=digest,
        subtitle_revision=subtitle[0] if subtitle else None,
        subtitle_sha256=subtitle[1] if subtitle else None,
    )
    db.add(order)
    db.flush()
    return order


def _latest_approved_subtitle(db: Session, project_id: str) -> tuple[int, str] | None:
    row = db.scalars(
        select(SubtitleTrack)
        .where(SubtitleTrack.project_id == project_id, SubtitleTrack.status == "APPROVED")
        .order_by(SubtitleTrack.revision.desc())
    ).first()
    return (row.revision, row.sha256) if row else None


def _edit_intent(db: Session, series: ProductionSeries) -> tuple[dict | None, dict | None]:
    """The frozen edit intent of the series, or the confirmed default rules."""
    if not series.edit_intent_artifact_id:
        return None, {
            "rules_version": DEFAULT_CUT_RULES_VERSION,
            "transition": "CUT",
            "in_s": 0.0,
            "out_s": "full_take",
            "order": "production_series_items.position",
        }
    artifact = db.get(AgentArtifact, series.edit_intent_artifact_id)
    if (
        not artifact
        or artifact.project_id != series.project_id
        or artifact.kind != "EditIntentPlan"
        or artifact.stale
    ):
        return None, {
            "rules_version": DEFAULT_CUT_RULES_VERSION,
            "transition": "CUT",
            "in_s": 0.0,
            "out_s": "full_take",
            "order": "production_series_items.position",
            "superseded_intent_artifact_id": series.edit_intent_artifact_id,
        }
    plan = artifact.payload_json
    return plan, {
        "rules_version": DEFAULT_CUT_RULES_VERSION,
        "transition": plan.get("clips", [{}])[0].get("transition", "CUT") if plan.get("clips") else "CUT",
        "in_s": 0.0,
        "frozen_edit_intent": {
            "artifact_id": artifact.id,
            "sha256": artifact.sha256,
            "rhythm": plan.get("rhythm"),
            "dialogue_pace": plan.get("dialogue_pace"),
        },
    }


def build_auto_edl(
    db: Session,
    series: ProductionSeries,
    *,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
    asset_root: Path,
) -> tuple[dict, str, dict]:
    """Generate the structured EditDecisionList for a finished series.

    The clip order is the confirmed series order (``position``), never the
    creation time.  Every clip must reference the current passed attempt and
    a real artifact whose bytes still match its registered hash; the out
    point is the real media duration measured with FFprobe.
    """
    from shine.media.artifacts import safe_asset_path
    from shine.media.probe import probe_file

    intent, rules = _edit_intent(db, series)
    intent_source = "frozen_edit_intent" if intent else "default_cut_rules"
    clips = []
    items = sorted(list(series.items), key=lambda row: row.position)
    for item in items:
        job = db.get(Job, item.job_id) if item.job_id else None
        if item.status != "PASSED" or job is None or job.status != "PASSED" or job.stale:
            raise DeliveryBlock("DELIVERY_SHOT_NOT_PASSED")
        attempts = list(job.attempts)
        if not attempts:
            raise DeliveryBlock("DELIVERY_SHOT_ATTEMPT_MISSING")
        attempt = max(attempts, key=lambda row: row.attempt_no)
        artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        if artifact is None or artifact.is_mock:
            raise DeliveryBlock("DELIVERY_SOURCE_NOT_REAL_VIDEO")
        if artifact.artifact_kind != "video" or artifact.attempt_id != attempt.id:
            raise DeliveryBlock("DELIVERY_SOURCE_IDENTITY_MISMATCH")
        if artifact.sha256 != attempt.output_sha256:
            raise DeliveryBlock("DELIVERY_SOURCE_IDENTITY_MISMATCH")
        path = safe_asset_path(asset_root, artifact.storage_path)
        if not path.is_file():
            raise DeliveryBlock("DELIVERY_SOURCE_FILE_MISSING")
        if sha256_bytes(path.read_bytes()) != artifact.sha256:
            raise DeliveryBlock("DELIVERY_SOURCE_FILE_HASH_MISMATCH")
        info = probe_file(path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
        if info.duration_s is None:
            raise DeliveryBlock("DELIVERY_SOURCE_DURATION_UNKNOWN")
        clips.append(
            {
                "job_id": job.id,
                "attempt_id": attempt.id,
                "artifact_id": artifact.id,
                "in_s": 0.0,
                "out_s": round(info.duration_s, 3),
                "transition": "CUT",
                "reason": f"Series position {item.position}: passed real take, full take",
            }
        )
    if not clips:
        raise DeliveryBlock("DELIVERY_EMPTY_EDIT")
    if intent and intent.get("clips") and len(intent["clips"]) == len(clips):
        planned = sorted(intent["clips"], key=lambda row: row.get("order", 0))
        for clip, plan in zip(clips, planned, strict=False):
            clip["reason"] = (
                f"Series position {plan.get('order', '?')}: frozen edit intent"
                f" (planned {plan.get('planned_duration_s')}s, {plan.get('transition')});"
                " real passed take"
            )
    return {"schema_version": "1.1", "clips": clips}, intent_source, rules


def _order_public(order: DeliveryOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "project_id": order.project_id,
        "series_id": order.series_id,
        "origin": order.origin,
        "version": order.version,
        "status": order.status,
        "shots_digest": order.shots_digest,
        "edl_source": order.edl_source,
        "edl_payload": order.edl_payload,
        "edit_rules": order.edit_rules,
        "subtitle_revision": order.subtitle_revision,
        "subtitle_sha256": order.subtitle_sha256,
        "attempt_count": order.attempt_count,
        "max_attempts": order.max_attempts,
        "artifact_id": order.artifact_id,
        "failure_code": _short(order.failure_code),
        "failure_note": _short(order.failure_note),
        "worker_id": order.worker_id,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
    }


def _short(value: str | None) -> str | None:
    """Keep refusal codes readable without echoing provider or path noise."""
    if value is None:
        return None
    return value[:400]


def _claim_order(db: Session, order: DeliveryOrder, worker_id: str | None) -> bool:
    """Move one PENDING order into ASSEMBLING, or recover a stale ASSEMBLING row."""
    now = utcnow()
    if order.status == "ASSEMBLING":
        from datetime import timedelta

        stale_after = (order.updated_at or now) + timedelta(seconds=ASSEMBLY_LEASE_SECONDS)
        if stale_after > now:
            return False
        order.attempt_count += 1
        if order.attempt_count > order.max_attempts:
            order.status = "FAILED"
            order.failure_code = "DELIVERY_ASSEMBLY_LEASE_LOST"
            return False
        order.status = "PENDING"
        order.state_version += 1
        order.worker_id = None
        return True
    if order.status != "PENDING":
        return False
    order.status = "ASSEMBLING"
    order.worker_id = worker_id
    order.state_version += 1
    order.attempt_count += 1
    return True


def run_delivery_order(
    session_factory,
    order_id: str,
    *,
    settings,
    asset_root: Path,
    worker_id: str | None = None,
) -> str:
    """Execute one delivery order: EDL -> FFmpeg -> registered reel.

    Returns the order status.  FFmpeg/infrastructure failures retry a bounded
    number of times; deterministic refusals (missing source, hash mismatch,
    gate not passed) terminate immediately with their stable code.
    """
    with session_factory.begin() as db:
        order = db.get(DeliveryOrder, order_id)
        if order is None or not _claim_order(db, order, worker_id):
            return order.status if order else "GONE"
        project_id = order.project_id
    assembler = DeliveryAssembler(
        asset_root=asset_root,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    try:
        with session_factory.begin() as db:
            order = db.get(DeliveryOrder, order_id)
            series = db.get(ProductionSeries, order.series_id)
            edl, edl_source, edit_rules = build_auto_edl(
                db, series,
                ffprobe_path=settings.ffprobe_path,
                ffmpeg_path=settings.ffmpeg_path,
                asset_root=asset_root,
            )
            order.edl_payload = edl
            order.edl_source = edl_source
            order.edit_rules = edit_rules
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
                    raise DeliveryBlock("DELIVERY_SUBTITLE_NOT_APPROVED")
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
                )
                work_db.commit()
            with session_factory.begin() as db:
                order = db.get(DeliveryOrder, order_id)
                series = db.get(ProductionSeries, order.series_id)
                order.status = "DELIVERED"
                order.artifact_id = result.artifact.id
                order.delivered_at = utcnow()
                order.failure_code = None
                order.failure_note = None
                series.delivery_result = {
                    "order_id": order.id,
                    "version": order.version,
                    "artifact_id": result.artifact.id,
                    "sha256": result.artifact.sha256,
                    "byte_size": result.artifact.byte_size,
                    "duration_s": result.duration_s,
                    "width": result.width,
                    "height": result.height,
                    "fps": result.fps,
                    "review_state": result.review_state,
                    "edl_source": order.edl_source,
                }
                from shine.services import add_audit

                add_audit(
                    db,
                    "worker",
                    "AUTO_DELIVERY_ASSEMBLED",
                    "delivery_order",
                    order.id,
                    {
                        "series_id": order.series_id,
                        "version": order.version,
                        "artifact_id": result.artifact.id,
                        "sha256": result.artifact.sha256,
                        "review_state": result.review_state,
                        "edl_source": order.edl_source,
                        "clip_count": len(result.clips),
                    },
                    project_id=order.project_id,
                )
                order.state_version += 1
    except DeliveryBlock as exc:
            with session_factory.begin() as db:
                order = db.get(DeliveryOrder, order_id)
                order.status = "BLOCKED"
                order.failure_code = exc.code
                order.state_version += 1
                from shine.services import add_audit
                add_audit(db, "worker", "AUTO_DELIVERY_BLOCKED", "delivery_order", order.id,
                          {"series_id": order.series_id, "code": exc.code},
                          project_id=order.project_id)
    except DeliveryError as exc:
            retryable = exc.status_code >= 500
            with session_factory.begin() as db:
                order = db.get(DeliveryOrder, order_id)
                from shine.services import add_audit

                if retryable and order.attempt_count < order.max_attempts:
                    order.status = "PENDING"
                    order.failure_code = _short(exc.detail)
                    order.failure_note = "bounded retry scheduled"
                else:
                    order.status = "FAILED"
                    order.failure_code = _short(exc.detail)
                    order.failure_note = ("retries exhausted" if retryable
                                          else "definite assembly refusal")
                order.state_version += 1
                add_audit(db, "worker", "AUTO_DELIVERY_FAILED", "delivery_order", order.id,
                          {"series_id": order.series_id, "code": _short(exc.detail),
                           "retryable": retryable, "attempt": order.attempt_count},
                          project_id=order.project_id)
    except Exception as exc:  # noqa: BLE001 - the order must record every failure mode
            with session_factory.begin() as db:
                order = db.get(DeliveryOrder, order_id)
                from shine.services import add_audit

                retryable = order.attempt_count < order.max_attempts and isinstance(
                    exc, OSError | subprocess.SubprocessError
                )
                if retryable:
                    order.status = "PENDING"
                    order.failure_code = f"DELIVERY_WORKER_ERROR:{type(exc).__name__}"
                    order.failure_note = "bounded retry scheduled"
                else:
                    order.status = "FAILED"
                    order.failure_code = f"DELIVERY_WORKER_ERROR:{type(exc).__name__}"
                    order.failure_note = _short(str(exc))
                order.state_version += 1
                add_audit(db, "worker", "AUTO_DELIVERY_FAILED", "delivery_order", order.id,
                          {"series_id": order.series_id, "code": f"DELIVERY_WORKER_ERROR:{type(exc).__name__}",
                           "retryable": retryable, "attempt": order.attempt_count},
                          project_id=order.project_id)
    return order.status


def run_pending_deliveries(session_factory, *, settings, asset_root: Path, worker_id: str | None = None) -> int:
    """Drive every runnable auto-delivery order; return how many were processed.

    This is the recovery path: a worker that crashed between the last shot
    and the assembly leaves a durable PENDING or stale ASSEMBLING order, and
    any worker loop tick resumes it here.
    """
    processed = 0
    while True:
        with session_factory() as db:
            row = db.scalars(
                select(DeliveryOrder)
                .where(DeliveryOrder.status.in_(["PENDING", "ASSEMBLING"]))
                .order_by(DeliveryOrder.updated_at)
                .limit(1)
            ).first()
        if row is None:
            break
        run_delivery_order(
            session_factory, row.id, settings=settings, asset_root=asset_root, worker_id=worker_id
        )
        processed += 1
    return processed


def cancel_orders_for_series(db: Session, series_id: str) -> int:
    """Stop the pending auto deliveries of a cancelled series."""
    changed = 0
    orders = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.series_id == series_id,
            DeliveryOrder.status.in_(["PENDING", "ASSEMBLING"]),
        )
    ).all()
    for order in orders:
        if order.status == "ASSEMBLING":
            # FFmpeg may still be running; reconciliation, not fiat, decides.
            continue
        order.status = "CANCELLED"
        order.failure_code = "SERIES_CANCELLED"
        order.state_version += 1
        changed += 1
    return changed

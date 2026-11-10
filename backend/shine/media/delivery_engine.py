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
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from shine.media.assembly import DeliveryAssembler, DeliveryClip, DeliveryError
from shine.media.delivery_models import DeliveryOrder
from shine.models import (
    AgentArtifact,
    Job,
    MediaArtifact,
    ProductionSeries,
    Shot,
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


def _delivery_jobs(db: Session, series: ProductionSeries) -> list[Job]:
    """Follow the first shot's pinned continuity takes back to the opening cut."""
    items = sorted(series.items, key=lambda row: row.position)
    if not items:
        raise DeliveryBlock("DELIVERY_SERIES_HAS_NO_ITEMS")
    jobs = []
    for item in items:
        job = db.get(Job, item.job_id) if item.job_id else None
        if (item.status != "PASSED" or job is None or job.status != "PASSED" or job.stale
                or job.project_id != series.project_id or job.shot_id != item.shot_id):
            raise DeliveryBlock("DELIVERY_SHOT_NOT_PASSED")
        jobs.append(job)

    seen_shots = {job.shot_id for job in jobs}
    prefix = []
    first = jobs[0]
    while True:
        continuity = ((first.request or {}).get("shot_spec") or {}).get("continuity") or {}
        if continuity.get("mode") in {None, "cut"}:
            break
        previous_id = continuity.get("previous_job_id")
        previous = db.get(Job, previous_id) if previous_id else None
        if (previous is None or previous.project_id != series.project_id
                or previous.shot_id != continuity.get("previous_shot_id")
                or previous.shot_id in seen_shots or previous.status != "PASSED"
                or previous.stale or previous.provider != series.provider):
            raise DeliveryBlock("DELIVERY_CONTINUITY_SOURCE_NOT_CURRENT")
        shot = db.get(Shot, previous.shot_id)
        if shot is None or shot.revision != previous.shot_revision:
            raise DeliveryBlock("DELIVERY_CONTINUITY_SOURCE_NOT_CURRENT")
        seen_shots.add(previous.shot_id)
        prefix.append(previous)
        first = previous
    return [*reversed(prefix), *jobs]


def _shots_digest(jobs: list[Job]) -> str:
    """Pin the order and exact take for each shot in the delivery."""
    payload = [[position, job.shot_id, job.id, job.artifact_id or ""] for position, job in enumerate(jobs)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def enqueue_auto_delivery(db: Session, series: ProductionSeries) -> DeliveryOrder | None:
    """Create the durable delivery order for a finished series, idempotently.

    Called inside the transaction that completes the series, so the assembly
    work exists as soon as the completion itself exists.  An identical series
    version that was already delivered (or is already queued) never produces a
    second order; a different version (rework, subtitle change) does.
    """
    from shine.media.delivery_models import DeliveryOrder
    from shine.models import AgentTaskRecord
    from shine.production.models import ProductionRunBatch

    # A series that belongs to a whole-film run is one *batch*, not the
    # episode: publishing its reel as "the film" would release the same episode
    # several times.  The run's episode order owns that delivery instead.
    if db.scalars(
        select(ProductionRunBatch.id).where(ProductionRunBatch.series_id == series.id)
    ).first() is not None:
        return None

    # A re-take is evidence awaiting an explicit assembly selection. Do not
    # silently publish a one-segment film when its isolated series completes.
    retakes = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == series.project_id, AgentTaskRecord.role_id == "segment_regenerate"))
    if any(row.trace.get("series_id") == series.id for row in retakes):
        return None

    digest = _shots_digest(_delivery_jobs(db, series))
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
            "director_style_id": plan.get("director_style_id"),
            "director_style_resolution_sha256": plan.get("director_style_resolution_sha256"),
            "editing_direction": plan.get("editing_direction"),
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

    Pinned continuity predecessors come first, followed by the confirmed
    series order. Every clip must reference the current passed attempt and
    a real artifact whose bytes still match its registered hash; the out
    point is the real media duration measured with FFprobe.
    """
    from shine.media.artifacts import safe_asset_path
    from shine.media.probe import probe_file

    intent, rules = _edit_intent(db, series)
    intent_source = "frozen_edit_intent" if intent else "default_cut_rules"
    clips = []
    jobs = _delivery_jobs(db, series)
    prefix_count = len(jobs) - len(series.items)
    if prefix_count:
        rules = {**rules, "order": "pinned_continuity_predecessors_then_series_position"}
    for position, job in enumerate(jobs):
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
                "reason": (
                    "Pinned continuity predecessor: passed real take, full take"
                    if position < prefix_count else
                    f"Series position {position - prefix_count}: passed real take, full take"
                ),
            }
        )
    if not clips:
        raise DeliveryBlock("DELIVERY_EMPTY_EDIT")
    if intent and intent.get("clips") and len(intent["clips"]) == len(clips) - prefix_count:
        planned = sorted(intent["clips"], key=lambda row: row.get("order", 0))
        for clip, plan in zip(clips[prefix_count:], planned, strict=True):
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
        "scope_kind": order.scope_kind,
        "scope_id": order.scope_id,
        "origin": order.origin,
        "version": order.version,
        "status": order.status,
        "shots_digest": order.shots_digest,
        "input_digest": order.input_digest,
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
    """Atomically move one PENDING order into ASSEMBLING, or reclaim an expired lease.

    The claim is a conditional UPDATE judged by its affected-row count, so two
    workers that read the same PENDING row cannot both win: the loser's UPDATE
    matches zero rows because the winner already moved the row.  The condition
    carries the row identity we read (status and state_version); an expired
    ASSEMBLING lease is only recoverable when its ``updated_at`` is past the
    lease window, and the attempt budget is consumed atomically with the
    reclaim, so a stale reader can never resurrect an exhausted budget.
    """
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
    closed = db.execute(
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order.id,
            DeliveryOrder.status == "ASSEMBLING",
            DeliveryOrder.state_version == read_version,
            DeliveryOrder.updated_at <= cutoff,
            DeliveryOrder.attempt_count >= DeliveryOrder.max_attempts,
        )
        .values(
            status="FAILED",
            failure_code="DELIVERY_ASSEMBLY_LEASE_LOST",
            state_version=DeliveryOrder.state_version + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if closed.rowcount == 1:
        return False

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


class DeliveryClaim(NamedTuple):
    """The identity a claimer must present on every write-back.

    A claim establishes (owner, state_version); every completion, failure and
    retry write-back re-asserts this pair as a conditional UPDATE, so a worker
    that lost its lease can never overwrite the new owner or a final state.
    """

    owner: str | None
    version: int


def _write_back(db: Session, order_id: str, claim: DeliveryClaim, values: dict[str, Any]) -> bool:
    """Apply one order write-back only while this worker still owns the claim.

    The guard is the claim token (owner + state_version); the affected-row
    count decides.  When the guard matches zero rows the caller must leave the
    row alone: the new owner or a final state is never overwritten, and the
    caller reports the authoritative status instead of its own claim.
    """
    statement = (
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order_id,
            DeliveryOrder.worker_id == claim.owner,
            DeliveryOrder.state_version == claim.version,
        )
        .values(**values)
    )
    result = db.execute(statement)
    return result.rowcount == 1


ASSEMBLY_HEARTBEAT_SECONDS = 60


def renew_assembly_claim(session_factory, order_id: str, claim: DeliveryClaim) -> bool | None:
    """Refresh a running assembly's lease without changing its claim token.

    Only the owning claim may renew it, and only while the order is still
    ASSEMBLING.  ``state_version`` is deliberately left alone: the claim token
    stays valid for the final write-back.  Returns ``None`` when SQLite was busy
    — that defers to the next beat, it is not a lost claim.
    """
    from sqlalchemy.exc import OperationalError

    try:
        with session_factory.begin() as db:
            result = db.execute(
                update(DeliveryOrder)
                .where(
                    DeliveryOrder.id == order_id,
                    DeliveryOrder.worker_id == claim.owner,
                    DeliveryOrder.state_version == claim.version,
                    DeliveryOrder.status == "ASSEMBLING",
                )
                .values(updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            return result.rowcount == 1
    except OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return None
        raise


class AssemblyHeartbeat:
    """Keep a long assembly's claim alive while FFmpeg is still working.

    The reclaim rule hands an ASSEMBLING order to another worker once its
    ``updated_at`` is older than the lease.  A multi-hour episode is not a dead
    worker: the heartbeat proves life, so a second worker never starts a
    parallel FFmpeg run on the same order.  A worker that really died stops
    beating and the order becomes reclaimable again.
    """

    def __init__(self, session_factory, order_id: str, claim: DeliveryClaim, *,
                 interval: float = ASSEMBLY_HEARTBEAT_SECONDS):
        from threading import Event

        self.session_factory, self.order_id, self.claim = session_factory, order_id, claim
        self.interval = interval
        self.lost = False
        self._stop = Event()
        self._thread = None

    def beat(self) -> bool | None:
        return renew_assembly_claim(self.session_factory, self.order_id, self.claim)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                outcome = self.beat()
            except Exception:  # noqa: BLE001 - the beat must never kill the assembly
                logger.warning("assembly_heartbeat_failed order_id=%s", self.order_id)
                continue
            if outcome is False:
                # The claim moved on: the final write-back will be refused by
                # the claim token, so this worker only stops renewing.
                self.lost = True
                return

    def __enter__(self) -> AssemblyHeartbeat:
        from threading import Thread

        self._thread = Thread(target=self._run, name=f"assembly-heartbeat-{self.order_id[:8]}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _order_status_now(db: Session, order_id: str) -> str:
    """The authoritative status after a lost write-back, for honest reporting."""
    row = db.get(DeliveryOrder, order_id)
    return row.status if row else "GONE"


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
        if order is None:
            return "GONE"
        read_version = order.state_version
        if not _claim_order(db, order, worker_id):
            db.refresh(order)
            return order.status
        claim = DeliveryClaim(owner=worker_id, version=read_version + 1)
        project_id = order.project_id
        series_id = order.series_id
        version = order.version
        attempt = order.attempt_count + 1
        max_attempts = order.max_attempts
    assembler = DeliveryAssembler(
        asset_root=asset_root,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    try:
        # Build local values only. No dirty order may survive across assembly:
        # a worker can lose its claim while FFmpeg is running.
        with session_factory() as db:
            order = db.get(DeliveryOrder, order_id)
            series = db.get(ProductionSeries, order.series_id)
            edl, edl_source, edit_rules = build_auto_edl(
                db, series,
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
        with session_factory() as work_db, AssemblyHeartbeat(session_factory, order_id, claim):
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
                output_subdir="delivery/current",
            )
            work_db.commit()
        with session_factory.begin() as db:
            if not _write_back(db, order_id, claim, {
                "status": "DELIVERED",
                "artifact_id": result.artifact.id,
                "delivered_at": utcnow(),
                "edl_payload": edl,
                "edl_source": edl_source,
                "edit_rules": edit_rules,
                "failure_code": None,
                "failure_note": None,
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            series = db.get(ProductionSeries, series_id)
            series.delivery_result = {
                "order_id": order_id,
                "version": version,
                "artifact_id": result.artifact.id,
                "sha256": result.artifact.sha256,
                "byte_size": result.artifact.byte_size,
                "duration_s": result.duration_s,
                "width": result.width,
                "height": result.height,
                "fps": result.fps,
                "review_state": result.review_state,
                "edl_source": edl_source,
            }
            from shine.services import add_audit

            add_audit(
                db,
                "worker",
                "AUTO_DELIVERY_ASSEMBLED",
                "delivery_order",
                order_id,
                {
                    "series_id": series_id,
                    "version": version,
                    "artifact_id": result.artifact.id,
                    "sha256": result.artifact.sha256,
                    "review_state": result.review_state,
                    "edl_source": edl_source,
                    "clip_count": len(result.clips),
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
            from shine.services import add_audit
            add_audit(db, "worker", "AUTO_DELIVERY_BLOCKED", "delivery_order", order_id,
                      {"series_id": series_id, "code": exc.code},
                      project_id=project_id)
    except DeliveryError as exc:
        retryable = exc.status_code >= 500
        with session_factory.begin() as db:
            retry_scheduled = retryable and attempt < max_attempts
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": _short(exc.detail),
                "failure_note": ("bounded retry scheduled" if retry_scheduled
                                 else ("retries exhausted" if retryable
                                       else "definite assembly refusal")),
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            from shine.services import add_audit
            add_audit(db, "worker", "AUTO_DELIVERY_FAILED", "delivery_order", order_id,
                      {"series_id": series_id, "code": _short(exc.detail),
                       "retryable": retryable, "attempt": attempt},
                      project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - the order must record every failure mode
        with session_factory.begin() as db:
            retry_scheduled = attempt < max_attempts and isinstance(exc, OSError | subprocess.SubprocessError)
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": f"DELIVERY_WORKER_ERROR:{type(exc).__name__}",
                "failure_note": ("bounded retry scheduled" if retry_scheduled else _short(str(exc))),
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            from shine.services import add_audit
            add_audit(db, "worker", "AUTO_DELIVERY_FAILED", "delivery_order", order_id,
                      {"series_id": series_id, "code": f"DELIVERY_WORKER_ERROR:{type(exc).__name__}",
                       "retryable": retry_scheduled, "attempt": attempt},
                      project_id=project_id)
    with session_factory() as db:
        return _order_status_now(db, order_id)


def run_pending_deliveries(session_factory, *, settings, asset_root: Path, worker_id: str | None = None) -> int:
    """Drive the runnable auto-delivery orders in one bounded scheduling round.

    One call is one round with a definite, finite scope: it snapshots the
    PENDING orders and expired ASSEMBLING leases once and attempts each order
    at most once. Occupied leases are excluded before the batch limit so they
    cannot hide runnable orders. The Worker then returns to its other duties
    (series, ordinary jobs, heartbeat). An order that goes back to PENDING for
    a bounded retry is picked up again on the next round, not inside this one.
    """
    processed = 0
    cutoff = utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS)
    with session_factory() as db:
        runnable = db.execute(
            select(DeliveryOrder.id, DeliveryOrder.state_version, DeliveryOrder.scope_kind)
            .where(or_(
                DeliveryOrder.status == "PENDING",
                and_(DeliveryOrder.status == "ASSEMBLING", DeliveryOrder.updated_at <= cutoff),
            ))
            .order_by(DeliveryOrder.updated_at, DeliveryOrder.id)
            .limit(64)
        ).all()
    for order_id, seen_version, scope_kind in runnable:
        if scope_kind == "episode":
            from shine.production.episode_delivery import run_episode_delivery

            run_episode_delivery(
                session_factory, order_id, settings=settings, asset_root=asset_root, worker_id=worker_id
            )
        else:
            run_delivery_order(
                session_factory, order_id, settings=settings, asset_root=asset_root, worker_id=worker_id
            )
        with session_factory() as db:
            current = db.get(DeliveryOrder, order_id)
        if current is not None and current.state_version != seen_version:
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

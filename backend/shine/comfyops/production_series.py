"""The resident Worker's production scheduling entry for real H3 renders.

A *production series* is one durable, ordered real-H3 run for a project.  The
resident Worker claims a series, keeps one managed renderer alive across every
shot of it, and releases the renderer and the exclusive GPU lease exactly once
at the end.  Two serial model stages therefore never overlap, and a second
Worker can never take over a run that is still alive.

The invariants this module exists to protect:

* the shot order of a series is the order the series recorded, not the order
  the global job queue happens to have;
* a dependent shot is only created after its source job has really passed, so
  a continuity source is always a real, passed, non-stale job;
* the lease release and the series state transition commit in ONE transaction,
  so a released lease can never be hidden behind a series that still claims to
  hold it;
* an external submission whose outcome is unknown is never resubmitted; it is
  recorded as needing review.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.media.delivery_engine import DeliveryBlock, cancel_orders_for_series, enqueue_auto_delivery
from shine.models import (
    Job,
    ProductionSeries,
    ProductionSeriesItem,
    Project,
    Shot,
    utcnow,
)
from shine.schemas import (
    BasicReviewResolution,
    CreateJob,
    GenerationMode,
    MockScenario,
    RenderProvider,
    ShotSpec,
)

logger = logging.getLogger("shine")

SERIES_LEASE_SECONDS = 90
SERIES_HEARTBEAT_SECONDS = 10


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _items(db, series_id: str) -> list[ProductionSeriesItem]:
    return list(
        db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series_id)
            .order_by(ProductionSeriesItem.position)
        ).all()
    )


def _item_public(item: ProductionSeriesItem, job: Job | None) -> dict[str, Any]:
    return {
        "id": item.id,
        "shot_id": item.shot_id,
        "position": item.position,
        "status": item.status,
        "job_id": item.job_id,
        "continuity_mode": item.continuity_mode,
        "failure_code": item.failure_code,
        "job_status": job.status if job is not None else None,
        "job_artifact_id": job.artifact_id if job is not None else None,
        "job_failure_code": job.failure_code if job is not None else None,
    }


def series_public(db, series: ProductionSeries) -> dict[str, Any]:
    items = _items(db, series.id)
    jobs = {
        job.id: job
        for job in db.scalars(select(Job).where(Job.id.in_([i.job_id for i in items if i.job_id]))).all()
    } if any(i.job_id for i in items) else {}
    return {
        "id": series.id,
        "project_id": series.project_id,
        "status": series.status,
        "provider": series.provider,
        "workflow_profile_id": series.workflow_profile_id,
        "generation_mode": series.generation_mode,
        "self_review_scope": series.self_review_scope,
        "shot_count": series.shot_count,
        "completed_count": series.completed_count,
        "worker_id": series.worker_id,
        "lease_id": series.lease_id,
        "current_job_id": series.current_job_id,
        "state_version": series.state_version,
        "failure_code": series.failure_code,
        "finalization_state": series.finalization_state,
        "finalization_note": series.finalization_note,
        "edit_intent_artifact_id": series.edit_intent_artifact_id,
        "delivery_result": series.delivery_result,
        "created_at": series.created_at.isoformat() if series.created_at else None,
        "started_at": series.started_at.isoformat() if series.started_at else None,
        "finished_at": series.finished_at.isoformat() if series.finished_at else None,
        "items": [_item_public(item, jobs.get(item.job_id or "")) for item in items],
    }


def _continuity_mode(shot: Shot) -> str:
    try:
        return str(ShotSpec.model_validate(shot.spec).continuity.mode.value)
    except Exception:
        return "cut"


def _effective_spec(shot: Shot, *, previous_job_id: str | None, previous_shot_id: str | None) -> ShotSpec:
    """The shot spec with its continuity source bound to a real job.

    The stored shot spec carries the *intent* of a continuity relation.  The
    series is what knows which job really produced the previous shot, so the
    source is bound here, at job creation time, and never guessed.
    """
    spec = ShotSpec.model_validate(shot.spec)
    if spec.continuity.mode.value == "cut":
        return spec
    continuity = spec.continuity.model_copy(update={
        "previous_job_id": previous_job_id,
        "previous_shot_id": previous_shot_id,
    })
    return spec.model_copy(update={"continuity": continuity})


def _create_body(series: ProductionSeries, shot: Shot) -> CreateJob:
    resolution = None
    if series.basic_review_resolution:
        resolution = BasicReviewResolution.model_validate(series.basic_review_resolution)
    return CreateJob(
        shot_id=shot.id,
        mock_scenario=MockScenario(series.mock_scenario),
        provider=RenderProvider(series.provider),
        generation_mode=GenerationMode(series.generation_mode) if series.generation_mode else None,
        workflow_profile_id=series.workflow_profile_id,
        self_review_scope=series.self_review_scope,
        basic_review_resolution=resolution,
    )


# ---------------------------------------------------------------------------
# Creation (Web workbench)
# ---------------------------------------------------------------------------


def create_series(
    db,
    project: Project,
    shots: list[Shot],
    *,
    body: CreateJob,
    settings,
    offline: bool = False,
) -> tuple[ProductionSeries, Job]:
    """Create one production series and queue its first shot.

    The series records the whole shot order up front, so the resident Worker
    knows what the run is even if the process that created it disappears.  Only
    the first shot is queued here: a dependent shot may only be created after
    its source job really passed, which is the Worker's job, not the API's.
    """
    from shine.services import create_render_job

    if not shots:
        raise HTTPException(422, "PRODUCTION_SERIES_REQUIRES_SHOTS")
    if len({shot.id for shot in shots}) != len(shots):
        raise HTTPException(422, "PRODUCTION_SERIES_REQUIRES_DISTINCT_SHOTS")
    if body.provider != RenderProvider.COMFYUI:
        raise HTTPException(422, "PRODUCTION_SERIES_REQUIRES_COMFYUI")

    # Every real H3 render belongs to exactly one series.  A shot that is
    # already being produced must not be produced a second time, but unrelated
    # shots of the same project may form their own series.
    live_shot_ids = set(db.scalars(
        select(ProductionSeriesItem.shot_id)
        .join(ProductionSeries, ProductionSeries.id == ProductionSeriesItem.series_id)
        .where(
            ProductionSeries.status.in_(["QUEUED", "RUNNING", "PAUSED", "PAUSE_REQUESTED"]),
            ProductionSeriesItem.shot_id.in_([shot.id for shot in shots]),
        )
    ).all())
    live_jobs = db.scalars(
        select(Job.id).where(
            Job.shot_id.in_([shot.id for shot in shots]),
            Job.stale.is_(False),
            Job.status.in_(["PLANNED", "QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING"]),
        )
    ).all()
    if live_shot_ids or live_jobs:
        raise HTTPException(409, "PRODUCTION_SERIES_SHOT_ALREADY_QUEUED")

    series = ProductionSeries(
        project_id=project.id,
        status="QUEUED",
        provider=body.provider.value,
        workflow_profile_id=body.workflow_profile_id,
        generation_mode=body.generation_mode.value if body.generation_mode else None,
        self_review_scope=body.self_review_scope,
        basic_review_resolution=(
            body.basic_review_resolution.model_dump(mode="json") if body.basic_review_resolution else None
        ),
        mock_scenario=body.mock_scenario.value,
        shot_count=len(shots),
        completed_count=0,
        finalization_state="NONE",
    )
    db.add(series)
    db.flush()

    first_item: ProductionSeriesItem | None = None
    for position, shot in enumerate(shots):
        item = ProductionSeriesItem(
            series_id=series.id,
            shot_id=shot.id,
            position=position,
            status="PENDING",
            continuity_mode=_continuity_mode(shot),
        )
        db.add(item)
        db.flush()
        if first_item is None:
            first_item = item

    first_shot = shots[0]
    first_spec = ShotSpec.model_validate(first_shot.spec)
    job, _meta = create_render_job(
        db, project, first_shot, body, settings=settings, offline=offline, spec=first_spec,
    )
    first_item.job_id = job.id
    first_item.status = "QUEUED"
    series.current_job_id = job.id
    series.current_item_id = first_item.id
    return series, job


# ---------------------------------------------------------------------------
# Claiming and ownership
# ---------------------------------------------------------------------------


def _series_is_stale(series: ProductionSeries, now) -> bool:
    if series.status == "QUEUED":
        return True
    if series.status != "RUNNING":
        return False
    return series.lease_expires_at is None or series.lease_expires_at <= now


def _series_lease_is_alive(db, series: ProductionSeries, now) -> bool:
    """Whether the exclusive GPU lease of the series is still active.

    The series lease proves that the owner is alive; the GPU lease proves that
    the renderer is alive.  A series whose renderer is still running must not be
    handed to another worker, even when its owner stopped renewing.
    """
    if not series.lease_id:
        return False
    from shine.comfyops.models import ResourceLease

    lease = db.get(ResourceLease, series.lease_id)
    if lease is None:
        return False
    return lease.status == "ACTIVE" and lease.expires_at is not None and lease.expires_at > now


def claim_series(session_factory, worker_id: str, *, lease_seconds: int = SERIES_LEASE_SECONDS):
    """Atomically take ownership of one claimable series.

    The claim is a conditional UPDATE, so exactly one Worker can win.  A
    series whose previous owner stopped renewing is claimable again and will
    be resumed at its first unfinished shot.
    """
    from sqlalchemy import and_, or_, update

    now = utcnow()
    stale_clause = or_(
        ProductionSeries.status == "QUEUED",
        and_(
            ProductionSeries.status == "RUNNING",
            or_(
                ProductionSeries.lease_expires_at.is_(None),
                ProductionSeries.lease_expires_at <= now,
            ),
        ),
    )
    # A cheap read first: an idle worker must never take the SQLite writer.
    with session_factory() as db:
        rows = db.scalars(
            select(ProductionSeries)
            .where(ProductionSeries.provider == "comfyui",
                   ProductionSeries.status.in_(["QUEUED", "RUNNING"]))
            .order_by(ProductionSeries.created_at, ProductionSeries.id)
        ).all()
        target = next(
            (row for row in rows
             if _series_is_stale(row, now) and not _series_lease_is_alive(db, row, now)),
            None,
        )
        if target is None:
            return None
        target_id = target.id
    with session_factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        # The same guard as the cheap read: a series whose renderer still holds
        # an active GPU lease is never handed to another worker.
        live = db.get(ProductionSeries, target_id)
        if live is None or _series_lease_is_alive(db, live, now):
            return None
        result = db.execute(
            update(ProductionSeries)
            .where(ProductionSeries.id == target_id, stale_clause)
            .values(
                worker_id=worker_id,
                status="RUNNING",
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=target.started_at or now,
                state_version=target.state_version + 1,
            )
        )
        if result.rowcount != 1:
            return None
        db.flush()
        return db.get(ProductionSeries, target_id)


def renew_series(session_factory, series_id: str, worker_id: str, *, lease_seconds: int = SERIES_LEASE_SECONDS) -> bool:
    """Extend the ownership proof of a running series.

    A paused series still belongs to its owner: the pause is a request that the
    owner observes between shots, not a handover.  Stopping the heartbeat here
    would let a second worker claim a series whose renderer is still running.
    """
    now = utcnow()
    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        if series is None or series.worker_id != worker_id:
            return False
        if series.status not in {"RUNNING", "PAUSE_REQUESTED"}:
            return False
        series.heartbeat_at = now
        series.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return True


def record_series_lease(session_factory, series_id: str, worker_id: str, lease_id: str) -> None:
    """Persist the exclusive GPU lease that the series currently holds.

    The lease is the proof of life of a running series.  When the owning worker
    dies, a reconciliation must be able to tell "the renderer is still alive"
    from "nothing is running any more" by reading this column, so it is written
    when the lease is admitted and cleared only when the lease is released.
    """
    with session_factory.begin() as db:
        series = _owned_series(db, series_id, worker_id)
        if series is None:
            return
        series.lease_id = lease_id
        series.state_version += 1


class SeriesHeartbeat:
    """Keep the ownership proof of a running series alive.

    The series is the unit two Workers compete for, so the proof of life must
    not depend on a shot finishing.  A worker that dies stops renewing, and
    the series becomes claimable again instead of staying locked forever.
    """

    def __init__(self, factory, series_id, worker_id, *, interval=SERIES_HEARTBEAT_SECONDS,
                 lease_seconds=SERIES_LEASE_SECONDS):
        from threading import Event

        self.factory, self.series_id, self.worker_id = factory, series_id, worker_id
        self.interval, self.lease_seconds = interval, lease_seconds
        self._stop = Event()
        self._thread = None
        self.error = False

    def _pulse(self) -> bool:
        return renew_series(self.factory, self.series_id, self.worker_id, lease_seconds=self.lease_seconds)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                if not self._pulse():
                    self.error = True
                    self._stop.set()
                    return
            except Exception:
                self.error = True
                self._stop.set()
                return

    def __enter__(self):
        from threading import Thread

        if not self._pulse():
            raise HTTPException(409, "PRODUCTION_SERIES_CLAIM_LOST")
        self._thread = Thread(target=self._run, name="shine-production-series-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def _owned_series(db, series_id: str, worker_id: str) -> ProductionSeries | None:
    series = db.get(ProductionSeries, series_id)
    if series is None or series.worker_id != worker_id:
        return None
    return series


def _set_series_state(db, series_id: str, worker_id: str, *, status: str, failure_code: str | None = None,
                       note: str | None = None, finalization_state: str | None = None) -> None:
    series = _owned_series(db, series_id, worker_id)
    if series is None:
        return
    if series.status in {"COMPLETED", "FAILED", "CANCELLED"} and series.status != status:
        # A terminal series keeps its outcome.  A late worker transition must
        # not rewrite a cancellation or a finished run.
        return
    series.status = status
    series.state_version += 1
    series.failure_code = failure_code
    if finalization_state is not None:
        series.finalization_state = finalization_state
    if note is not None:
        series.finalization_note = note[:2000]
    if status in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
        series.finished_at = utcnow()


# ---------------------------------------------------------------------------
# Item progression
# ---------------------------------------------------------------------------


def _item_is_done(item: ProductionSeriesItem, job: Job | None) -> bool:
    if item.status in {"PASSED", "CANCELLED"}:
        return True
    if job is None:
        return False
    return job.status == "PASSED" and not job.stale


def _next_runnable_item(db, series_id: str) -> tuple[ProductionSeriesItem | None, Job | None]:
    """The first item of the series that can still run.

    A shot that needs review blocks only the shots that depend on it.  An
    independent shot of the same run continues, because a low-confidence
    judgement about one shot must not stop the whole production.
    """
    items = _items(db, series_id)
    by_position = {item.position: item for item in items}
    for item in items:
        job = db.get(Job, item.job_id) if item.job_id else None
        if _item_is_done(item, job):
            continue
        if job is not None and job.status in {"FAILED", "NEEDS_REVIEW"}:
            item.status = "BLOCKED"
            item.failure_code = job.failure_code or "SERIES_SHOT_NEEDS_REVIEW"
            continue
        if item.continuity_mode != "cut":
            previous = by_position.get(item.position - 1)
            if previous is not None:
                source = db.get(Job, previous.job_id) if previous.job_id else None
                if source is None or source.status != "PASSED" or source.stale:
                    item.status = "BLOCKED"
                    item.failure_code = "SERIES_SOURCE_NOT_PASSED"
                    continue
        return item, job
    return None, None


def _sync_items(db, series: ProductionSeries) -> None:
    """Refresh the item states from the real job states."""
    completed = 0
    for item in _items(db, series.id):
        job = db.get(Job, item.job_id) if item.job_id else None
        if job is None:
            continue
        if job.status == "PASSED" and not job.stale:
            item.status = "PASSED"
            completed += 1
        elif job.status in {"FAILED", "NEEDS_REVIEW"}:
            item.status = "BLOCKED"
            item.failure_code = job.failure_code
        elif job.status in {"QUEUED", "PLANNED", "READY"}:
            item.status = "QUEUED"
        else:
            item.status = "RUNNING"
    series.completed_count = completed


def _create_item_job(session_factory, series_id: str, item_id: str, *, settings, offline: bool = False):
    """Create the job of one series item, binding its continuity source.

    The source job must already have passed; the dependency edge and the
    compiled prompt then reference that real job.
    """
    from shine.services import create_render_job

    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        if series is None or item is None:
            raise HTTPException(409, "PRODUCTION_SERIES_ITEM_MISSING")
        if item.job_id:
            return item.job_id
        project = db.get(Project, series.project_id)
        shot = db.get(Shot, item.shot_id)
        if project is None or shot is None:
            raise HTTPException(409, "PRODUCTION_SERIES_ITEM_MISSING")
        items = _items(db, series.id)
        position = item.position
        previous_item = next((row for row in items if row.position == position - 1), None)
        previous_job_id = None
        previous_shot_id = None
        if previous_item is not None and item.continuity_mode != "cut":
            # A dependent shot may only consume a source job that really passed.
            if not previous_item.job_id:
                raise HTTPException(409, "PRODUCTION_SERIES_ORDER_VIOLATION")
            previous_job = db.get(Job, previous_item.job_id)
            if previous_job is None or previous_job.status != "PASSED" or previous_job.stale:
                raise HTTPException(409, "PRODUCTION_SERIES_SOURCE_NOT_PASSED")
            previous_job_id = previous_job.id
            previous_shot_id = previous_item.shot_id
        spec = _effective_spec(shot, previous_job_id=previous_job_id, previous_shot_id=previous_shot_id)
        body = _create_body(series, shot)
        job, _meta = create_render_job(
            db, project, shot, body, settings=settings, offline=offline, spec=spec,
        )
        item.job_id = job.id
        item.status = "QUEUED"
        series.current_job_id = job.id
        series.current_item_id = item.id
        series.state_version += 1
        return job.id


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


def record_finalization_intent(session_factory, series_id: str, worker_id: str, lease_id: str | None,
                                coordinator) -> None:
    """Persist the intent to release before any external action happens.

    After this row is written, a crash leaves a durable trace of what was
    being released, so a later reconciliation can tell "never started" from
    "started and interrupted".
    """
    identity = None
    supervisor = getattr(coordinator, "replacement", None)
    if supervisor is not None:
        identity = {
            "instance_id": getattr(supervisor, "instance_id", None),
            "url": getattr(supervisor, "_url", None),
        }
    with session_factory.begin() as db:
        series = _owned_series(db, series_id, worker_id)
        if series is None:
            return
        series.finalization_state = "FINALIZING"
        series.lease_id = lease_id or series.lease_id
        series.renderer_identity = identity
        series.state_version += 1


def mark_series_released(db, series_id: str, worker_id: str) -> None:
    """Record that the renderer and its exclusive lease are gone.

    The series status itself is decided by the caller from the real shot
    outcomes, so this only records the physical release.  Only the recorded
    owner may mark it, because clearing another worker's lease would destroy
    the proof that the renderer is still alive.
    """
    series = db.get(ProductionSeries, series_id)
    if series is None:
        return
    if series.worker_id and series.worker_id != worker_id:
        return
    series.finalization_state = "RELEASED"
    series.lease_id = None
    series.state_version += 1


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_production_series(
    session_factory,
    worker_id: str,
    series_id: str,
    *,
    settings,
    recipe,
    review_configs,
    max_shots: int = 16,
    comfy_service=None,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
    offline: bool = False,
) -> dict[str, Any]:
    """Drive one production series behind a single managed renderer.

    The first unfinished shot owns a fresh managed startup.  Every following
    shot continues the same fenced renderer through the idle keeper's lease
    transfer, so the exclusive GPU lease is never released between two shots
    of the same run.
    """
    from shine.worker import _run_managed_loop

    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        if series is None or series.worker_id != worker_id:
            return {"status": "NOT_OWNED", "shots": []}
        items = _items(db, series.id)
        if not items:
            return {"status": "EMPTY", "shots": []}

    shots: list[str] = []
    current_job_id: str | None = None

    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        _sync_items(db, series)
        item, job = _next_runnable_item(db, series_id)
        if item is None:
            _set_series_state(db, series_id, worker_id, status="COMPLETED")
            return {"status": "COMPLETED", "shots": shots}
        if job is None:
            # The shot still needs its job; its source must have passed first.
            pass
        else:
            current_job_id = job.id

    if current_job_id is None:
        # Create the job of the first unfinished item, then run the series.
        with session_factory.begin() as db:
            series = db.get(ProductionSeries, series_id)
            _sync_items(db, series)
            item, job = _next_runnable_item(db, series_id)
            item_id = item.id
        try:
            current_job_id = _create_item_job(session_factory, series_id, item_id, settings=settings, offline=offline)
        except HTTPException as exc:
            with session_factory.begin() as db:
                _set_series_state(db, series_id, worker_id, status="NEEDS_REVIEW",
                                  failure_code=f"SERIES_ITEM_JOB_BLOCKED:{exc.detail}")
            return {"status": "NEEDS_REVIEW", "shots": shots, "reason": str(exc.detail)}

    def resolve_next(previous_job_id: str) -> str | None:
        """Create and name the next shot of the series, or end the run."""
        with session_factory.begin() as db:
            series = db.get(ProductionSeries, series_id)
            if series is None or series.status != "RUNNING":
                return None
            _sync_items(db, series)
            item, job = _next_runnable_item(db, series_id)
            if item is None:
                return None
            if job is None:
                item_id = item.id
            else:
                item_id = None
        if item_id is None:
            return job.id
        try:
            return _create_item_job(session_factory, series_id, item_id, settings=settings, offline=offline)
        except HTTPException as exc:
            logger.error("production_series_item_blocked series_id=%s reason=%s", series_id, exc.detail)
            with session_factory.begin() as db:
                _set_series_state(db, series_id, worker_id, status="NEEDS_REVIEW",
                                  failure_code=f"SERIES_ITEM_JOB_BLOCKED:{exc.detail}")
            return None

    try:
        heartbeat = SeriesHeartbeat(session_factory, series_id, worker_id)
        with heartbeat:
            shots = _run_managed_loop(
                session_factory, worker_id,
                settings=settings, recipe=recipe, review_configs=review_configs, max_shots=max_shots,
                comfy_service=comfy_service, comfy_client=comfy_client,
                semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
                first_job_id=current_job_id,
                next_job_id=resolve_next,
                on_shot_complete=lambda job_id, coordinator_: _after_shot(
                    session_factory, series_id, worker_id, job_id,
                ),
                series_id=series_id,
            )
        if heartbeat.error:
            # The ownership proof was lost while the run was going on.  The
            # shots that were produced stay, but this worker must not decide
            # the outcome of a series it no longer owns.
            with session_factory.begin() as db:
                _set_series_state(db, series_id, worker_id, status="NEEDS_REVIEW",
                                  failure_code="SERIES_OWNERSHIP_LOST")
            return {"status": "NEEDS_REVIEW", "shots": shots, "reason": "SERIES_OWNERSHIP_LOST"}
    except Exception as exc:
        logger.error("production_series_failed series_id=%s error_type=%s", series_id, type(exc).__name__)
        with session_factory.begin() as db:
            _set_series_state(db, series_id, worker_id, status="NEEDS_REVIEW",
                              failure_code=f"SERIES_EXECUTION_FAILED:{type(exc).__name__}")
        return {"status": "NEEDS_REVIEW", "shots": shots, "reason": type(exc).__name__}

    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        _sync_items(db, series)
        if series is not None and series.status == "PAUSE_REQUESTED":
            # The user asked for a pause.  The finished shots stay, the rest of
            # the series stays claimable, and the renderer and the lease are
            # released by the managed loop.
            _set_series_state(db, series_id, worker_id, status="PAUSED", failure_code=None)
            return {"status": "PAUSED", "shots": shots}
        item, job = _next_runnable_item(db, series_id)
        blocked = [row for row in _items(db, series_id) if row.status == "BLOCKED"]
        if item is None and not blocked:
            status = "COMPLETED"
            failure_code = None
        elif item is None:
            status = "NEEDS_REVIEW"
            failure_code = blocked[0].failure_code or "SERIES_SHOT_NEEDS_REVIEW"
        else:
            status = "NEEDS_REVIEW"
            failure_code = (job.failure_code if job is not None else None) or "SERIES_INCOMPLETE"
        _set_series_state(db, series_id, worker_id, status=status, failure_code=failure_code)
        if status == "COMPLETED":
            try:
                enqueue_auto_delivery(db, series)
            except DeliveryBlock as exc:
                logger.error(
                    "auto_delivery_enqueue_blocked series_id=%s code=%s", series_id, exc.code
                )
    return {"status": status, "shots": shots}


def _after_shot(session_factory, series_id: str, worker_id: str, job_id: str) -> None:
    """Record the outcome of one finished shot on its series."""
    with session_factory.begin() as db:
        series = _owned_series(db, series_id, worker_id)
        if series is None:
            return
        _sync_items(db, series)
        series.current_job_id = job_id
        series.state_version += 1


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def reconcile_series(session_factory, worker_id: str) -> int:
    """Bring series rows that a dead worker left behind back into a known state.

    A series whose owner stopped renewing is either resumable (its unfinished
    shot can simply continue) or blocked.  It is never silently forgotten, and
    an uncertain external submission is never resubmitted by this path.
    """
    now = utcnow()
    changed = 0
    with session_factory.begin() as db:
        rows = db.scalars(
            select(ProductionSeries).where(ProductionSeries.status.in_(["RUNNING", "QUEUED"]))
        ).all()
        for series in rows:
            if series.status == "RUNNING" and not _series_is_stale(series, now):
                continue
            _sync_items(db, series)
            item, job = _next_runnable_item(db, series.id)
            if item is None and not [row for row in _items(db, series.id) if row.status == "BLOCKED"]:
                series.status = "COMPLETED"
                series.finished_at = now
                series.lease_id = None
                series.finalization_state = "RELEASED"
                series.state_version += 1
                changed += 1
                try:
                    enqueue_auto_delivery(db, series)
                except DeliveryBlock as exc:
                    logger.error(
                        "auto_delivery_enqueue_blocked series_id=%s code=%s", series.id, exc.code
                    )
                continue
            if series.status == "RUNNING":
                # The owner is gone.  The GPU lease is the real proof of life.
                if series.lease_id:
                    from shine.comfyops.models import ResourceLease

                    lease = db.get(ResourceLease, series.lease_id)
                    if _series_lease_is_alive(db, series, now):
                        # The renderer is still alive and fenced; leave it alone
                        # and let the lease keeper finish or reconcile it.
                        continue
                    if lease is not None and lease.status == "ACTIVE":
                        lease.status = "RECONCILE_REQUIRED"
                series.worker_id = None
                series.status = "QUEUED"
                series.state_version += 1
                changed += 1
        # A release that was interrupted between the intent and the release
        # left the series in FINALIZING.  The lease is reconciled above; the
        # series must not stay in a state that nobody reads again.
        interrupted = db.scalars(
            select(ProductionSeries).where(
                ProductionSeries.finalization_state == "FINALIZING",
                ProductionSeries.status.in_(["RUNNING", "QUEUED", "NEEDS_REVIEW"]),
            )
        ).all()
        for series in interrupted:
            if _series_lease_is_alive(db, series, now):
                continue
            series.finalization_state = "RECONCILE_REQUIRED"
            series.state_version += 1
            changed += 1
    return changed


def cancel_series_in_session(db, series_id: str, *, reason: str = "SERIES_CANCELLED") -> str:
    """Cancel a series inside an open session.

    A series that already renders cannot be cancelled by fiat: its external
    submission may still be running, and that outcome must be reconciled
    rather than guessed.  Only the shots that were never submitted are
    cancelled here.
    """
    series = db.get(ProductionSeries, series_id)
    if series is None:
        raise HTTPException(404, "Production series not found")
    if series.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return series.status
    series.status = "CANCELLED"
    series.failure_code = reason
    series.finished_at = utcnow()
    series.state_version += 1
    for item in _items(db, series.id):
        if item.status in {"PENDING", "QUEUED"}:
            item.status = "CANCELLED"
            item.failure_code = reason
            # A cancelled item's queued job must not stay claimable: the series
            # owns it, so the ordinary queue would otherwise render it alone.
            job = db.get(Job, item.job_id) if item.job_id else None
            if job is not None and job.status == "QUEUED":
                job.stale = True
    cancel_orders_for_series(db, series.id)
    return series.status


def cancel_series(session_factory, series_id: str, *, reason: str = "SERIES_CANCELLED") -> str:
    """Cancel a series that has not started rendering yet."""
    with session_factory.begin() as db:
        return cancel_series_in_session(db, series_id, reason=reason)


def pause_series(session_factory, series_id: str) -> str:
    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        if series is None:
            raise HTTPException(404, "Production series not found")
        if series.status == "RUNNING":
            # A running series owns a live renderer; pausing it must not
            # pretend the hardware was released.
            series.status = "PAUSE_REQUESTED"
        elif series.status == "QUEUED":
            series.status = "PAUSED"
        series.state_version += 1
        return series.status


def resume_series(session_factory, series_id: str) -> str:
    """Resume a paused series without taking it away from a living owner."""
    with session_factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        if series is None:
            raise HTTPException(404, "Production series not found")
        if series.status in {"PAUSED", "PAUSE_REQUESTED"}:
            owner_is_alive = bool(
                series.worker_id
                and series.lease_expires_at is not None
                and series.lease_expires_at > utcnow()
            )
            if owner_is_alive:
                series.status = "RUNNING"
            else:
                series.status = "QUEUED"
                series.worker_id = None
                series.lease_expires_at = None
            series.state_version += 1
        return series.status

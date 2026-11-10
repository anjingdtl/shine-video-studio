"""The whole-film run: planning, dispatch, progress and honest completion.

One resident Worker advances one run in a short transaction.  The run never
submits a render itself: it decides which *batch* may become claimable, creates
exactly one ``ProductionSeries`` for it, and then reads back what that series
really produced.  Every step is idempotent, so a crash between "series created"
and "batch remembered" cannot queue the same shots twice.

Ownership rules that this module enforces:

* An episode may only be assembled from jobs that *this run's own series items*
  produced.  A newer passed job elsewhere in the project is someone else's
  product and never fills a gap in this run.
* A run only completes when every required batch of every selected episode was
  planned, dispatched, passed and delivered — a partially dispatched plan is
  unfinished work, not a completed film.
* Every state write is fenced: a stale in-memory run object can never revive a
  run that another session already cancelled, paused or completed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shine.comfyops.models import ResourceLease
from shine.director.contracts import digest_of
from shine.director.models import DirectorPackageEpisode, DirectorPackageVersion
from shine.director.package import (
    episode_manifest,
    package_episodes,
    package_readiness,
)
from shine.models import (
    AppSetting,
    AuditEvent,
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    Shot,
    WorkflowProfile,
    utcnow,
)
from shine.production.models import (
    ACTIVE_RUN_STATUSES,
    ProductionRun,
    ProductionRunBatch,
    ProductionRunEpisode,
    ProductionRunEvent,
)

logger = logging.getLogger(__name__)

RUN_LEASE_SECONDS = 120
DEFAULT_BATCH_SHOT_LIMIT = 6
MAX_DISPATCHES_PER_ADVANCE = 1
"""One advance creates at most one series.

The resident Worker claims a single series, so creating more than one is pure
speculation and would only widen the window in which two series compete for the
same GPU.  The next beat creates the next batch.
"""

# The control reasons a *run* may pause its own child series.  Resume only
# undoes these: a series paused directly by its own control path stays paused
# until that path releases it.
RUN_PAUSE_REASONS = {"USER_PAUSED", "BUDGET_EXHAUSTED", "STORAGE_LOW", "TIME_WINDOW"}

# Job statuses whose external outcome may still be unknown or in flight.
INFLIGHT_JOB_STATUSES = ("SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING")

ADVANCEABLE_RUN_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")


class RunBlocked(Exception):
    """A definite refusal to dispatch a batch; recorded, never blindly retried."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code or detail)


# ---------------------------------------------------------------------------
# Identity and creation
# ---------------------------------------------------------------------------


def _batch_plan(
    episode: DirectorPackageEpisode, *, batch_shot_limit: int, split_continuity: bool = False,
) -> list[dict[str, Any]]:
    """Split one episode's ordered shots into dispatchable batches.

    Legacy runs retain their original chain-preserving grouping.  The opt-in
    split is for runs that can bind an earlier series' verified source media;
    persisting that choice on the run prevents a restart from changing its plan.
    """
    manifest = episode_manifest(episode)
    shots = manifest.get("shots", [])
    limit = max(1, int(batch_shot_limit))
    batches: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def close() -> None:
        nonlocal current
        if not current:
            return
        index = len(batches) + 1
        batches.append(
            {
                "batch_key": f"{episode.episode_id}-b{index:02d}",
                "position": index,
                "shot_ids": [shot["shot_id"] for shot in current],
                "input_digest": digest_of(
                    {
                        "episode_id": episode.episode_id,
                        "position": index,
                        "shots": [
                            [shot["shot_id"], shot["revision"], shot["spec_sha256"],
                             [binding["asset_id"] for binding in shot.get("bindings", [])]]
                            for shot in current
                        ],
                    }
                ),
            }
        )
        current = []

    for index, shot in enumerate(shots):
        current.append(shot)
        last = index == len(shots) - 1
        if last:
            break
        next_shot = shots[index + 1]
        if not split_continuity and next_shot.get("continuity_mode") != "cut":
            continue
        if len(current) >= limit:
            close()
    close()
    return batches


def create_run(
    db: Session,
    package: DirectorPackageVersion,
    *,
    episode_ids: list[str] | None = None,
    idempotency_key: str,
    actor: str = "user",
) -> tuple[ProductionRun, bool]:
    """Persist one whole-film run for a confirmed package, idempotently."""
    if package.status != "CONFIRMED":
        raise HTTPException(409, "DIRECTOR_PACKAGE_NOT_CONFIRMED")
    if package.stale:
        raise HTTPException(409, "DIRECTOR_PACKAGE_STALE")

    episodes = package_episodes(db, package.id)
    if not episodes:
        raise HTTPException(409, "DIRECTOR_PACKAGE_HAS_NO_EPISODES")
    by_id = {episode.episode_id: episode for episode in episodes}
    selected = [by_id[eid] for eid in (episode_ids or []) if eid in by_id]
    if episode_ids and len(selected) != len(episode_ids):
        raise HTTPException(422, "PRODUCTION_RUN_UNKNOWN_EPISODE")
    chosen = selected or episodes
    scope = {
        "episode_ids": [episode.episode_id for episode in chosen],
        "all_episodes": not episode_ids,
        "shot_count": sum(episode.shot_count for episode in chosen),
    }
    request_hash = digest_of(
        {
            "package": package.manifest_sha256,
            "scope": scope,
            "policy": package.policy_hash,
        }
    )

    existing = db.scalars(
        select(ProductionRun).where(ProductionRun.idempotency_key == idempotency_key)
    ).first()
    if existing is not None:
        if existing.project_id != package.project_id or existing.request_hash != request_hash:
            raise HTTPException(409, "PRODUCTION_RUN_IDEMPOTENCY_CONFLICT")
        return existing, False

    active = db.scalars(
        select(ProductionRun).where(
            ProductionRun.project_id == package.project_id,
            ProductionRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    ).first()
    if active is not None:
        if active.package_id == package.id and active.request_hash == request_hash:
            return active, False
        raise HTTPException(409, {
            "code": "PRODUCTION_RUN_ALREADY_ACTIVE",
            "run_id": active.id,
            "status": active.status,
        })

    readiness = package_readiness(db, package)
    if not readiness["startable"]:
        raise HTTPException(409, {"code": "DIRECTOR_PACKAGE_NOT_STARTABLE",
                                 "blockers": readiness["blockers"]})

    run = ProductionRun(
        project_id=package.project_id,
        package_id=package.id,
        package_revision=package.revision,
        manifest_sha256=package.manifest_sha256,
        scope_digest=package.scope_digest,
        policy_hash=package.policy_hash,
        scope=scope,
        budget={
            "max_wall_seconds": int((package.manifest.get("policy") or {}).get("max_wall_seconds", 0) or 0),
            "max_repairs": int((package.manifest.get("policy") or {}).get("max_repairs_per_shot", 2) or 0),
            "min_free_gb": float((package.manifest.get("policy") or {}).get("min_free_gb", 0) or 0),
        },
        counters={},
        status="QUEUED",
        claim_owner=None,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        started_at=None,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:  # the partial unique index won the race
        db.rollback()
        raise HTTPException(409, "PRODUCTION_RUN_ALREADY_ACTIVE") from exc

    for episode in chosen:
        db.add(
            ProductionRunEpisode(
                run_id=run.id,
                project_id=package.project_id,
                episode_id=episode.episode_id,
                position=episode.position,
                title=episode.title,
                manifest_sha256=episode.manifest_sha256,
                target_seconds=float((package.manifest.get("episode_plan") or {}).get("target_seconds", 0) or 0),
                planned_seconds=episode.planned_seconds,
                shot_ids=_episode_shot_ids(episode),
                status="PLANNED",
            )
        )
    _event(db, run, "RUN_CREATED", f"启动整部作品：{len(chosen)} 集 / {scope['shot_count']} 镜",
           {"package_revision": package.revision})
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "PRODUCTION_RUN_CREATED", "production_run", run.id,
              {"package_id": package.id, "revision": package.revision,
               "episodes": scope["episode_ids"], "manifest_sha256": package.manifest_sha256},
              project_id=package.project_id)
    return run, True


def _episode_shot_ids(episode: DirectorPackageEpisode) -> list[str]:
    """The ordered shot ids of one package episode, re-verified against its hash."""
    return [shot["shot_id"] for shot in episode_manifest(episode).get("shots", [])]


def _event(db: Session, run: ProductionRun, kind: str, message: str, payload: dict | None = None) -> None:
    db.add(
        ProductionRunEvent(
            run_id=run.id,
            project_id=run.project_id,
            kind=kind,
            message=message[:2000],
            payload=payload or {},
        )
    )


# ---------------------------------------------------------------------------
# Batch bookkeeping
# ---------------------------------------------------------------------------


def _batch_rows(db: Session, run_id: str) -> list[ProductionRunBatch]:
    return list(db.scalars(
        select(ProductionRunBatch)
        .where(ProductionRunBatch.run_id == run_id)
        .order_by(ProductionRunBatch.position)
    ).all())


def _episode_rows(db: Session, run_id: str) -> list[ProductionRunEpisode]:
    return list(db.scalars(
        select(ProductionRunEpisode)
        .where(ProductionRunEpisode.run_id == run_id)
        .order_by(ProductionRunEpisode.position)
    ).all())


def _live_series(db: Session, run_id: str) -> ProductionSeries | None:
    batch = db.scalars(
        select(ProductionRunBatch)
        .join(ProductionSeries, ProductionSeries.id == ProductionRunBatch.series_id)
        .where(
            ProductionRunBatch.run_id == run_id,
            ProductionSeries.status.in_([
                "QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "CANCEL_REQUESTED",
            ]),
        )
    ).first()
    return db.get(ProductionSeries, batch.series_id) if batch and batch.series_id else None


def _sync_batch_from_series(db: Session, batch: ProductionRunBatch) -> bool:
    """Read one batch's real state back from its series. Never guesses."""
    if not batch.series_id:
        return False
    series = db.get(ProductionSeries, batch.series_id)
    if series is None:
        return False
    changed = False
    if series.status == "COMPLETED":
        if batch.status != "PASSED":
            batch.status = "PASSED"
            batch.completed_at = series.finished_at or utcnow()
            batch.failure_code = None
            changed = True
    elif series.status in {"FAILED", "CANCELLED", "NEEDS_REVIEW"}:
        # A series that needs a human decision blocks its batch: the run must
        # surface that blocker instead of spinning on a batch that will never
        # advance by itself.
        if batch.status != "BLOCKED":
            batch.status = "BLOCKED"
            batch.failure_code = series.failure_code or f"SERIES_{series.status}"
            changed = True
    elif series.status == "QUEUED":
        # A recovered or abandoned series has no worker claim yet. Keep its
        # batch in the visible queue instead of leaving a stale RUNNING label
        # from its previous owner. The run and episode may remain active while
        # this batch waits for the single series worker.
        if batch.status != "QUEUED":
            batch.status = "QUEUED"
            batch.failure_code = None
            batch.completed_at = None
            changed = True
    elif series.status in {"RUNNING", "PAUSE_REQUESTED"}:
        if batch.status != "RUNNING":
            batch.status = "RUNNING"
            changed = True
    # ``PAUSED`` and ``CANCEL_REQUESTED`` are control states, not progress: the
    # batch keeps whatever it had, so a paused run does not look like work.
    return changed


def run_owned_passed_jobs(
    db: Session,
    run_id: str,
    episodes: list[ProductionRunEpisode] | None = None,
) -> dict[str, Job]:
    """Map ``shot_id`` to the passed job *this run's own series items* produced.

    The only jobs that may fill an episode of this run are the ones reachable
    from ``episode -> batch -> series -> item -> job``.  A newer passed job that
    no series item of this run owns (an unrelated take, a manual render, a later
    project job) is somebody else's product and is never selected here.
    """
    if episodes is None:
        episodes = _episode_rows(db, run_id)
    if not episodes:
        return {}
    episode_ids = [episode.episode_id for episode in episodes]
    rows = db.execute(
        select(ProductionRunBatch, ProductionSeriesItem)
        .join(ProductionSeriesItem,
              ProductionSeriesItem.series_id == ProductionRunBatch.series_id)
        .where(
            ProductionRunBatch.run_id == run_id,
            ProductionRunBatch.episode_id.in_(episode_ids),
            ProductionRunBatch.series_id.isnot(None),
        )
        .order_by(ProductionRunBatch.position, ProductionSeriesItem.position)
    ).all()
    shot_revisions = {
        shot.id: shot.revision
        for shot in db.scalars(
            select(Shot).where(
                Shot.id.in_({item.shot_id for _batch, item in rows} or {"-"})
            )
        ).all()
    }
    passed: dict[str, Job] = {}
    for _batch, item in rows:
        if not item.job_id or item.status != "PASSED":
            continue
        job = db.get(Job, item.job_id)
        if job is None or job.status != "PASSED" or job.stale or not job.artifact_id:
            continue
        if shot_revisions.get(item.shot_id) != job.shot_revision:
            continue
        # First item wins: with the ordered query it is the earliest batch that
        # produced this shot inside this run.
        passed.setdefault(item.shot_id, job)
    return passed


def _series_has_unreconciled_submission(db: Session, series: ProductionSeries) -> bool:
    """Whether an unfinished item still has a possible external H3 request."""
    items = db.scalars(select(ProductionSeriesItem).where(
        ProductionSeriesItem.series_id == series.id,
    )).all()
    for item in items:
        if item.status in {"PASSED", "CANCELLED"}:
            continue
        job = db.get(Job, item.job_id) if item.job_id else None
        if job is None:
            continue
        if (job.status in INFLIGHT_JOB_STATUSES or job.lease_owner or job.lease_expires_at
                or job.provider_job_id):
            return True
        if db.scalar(select(JobAttempt.id).where(JobAttempt.job_id == job.id).limit(1)):
            return True
        if db.scalar(select(AuditEvent.id).where(
            AuditEvent.project_id == series.project_id,
            AuditEvent.entity_type == "job",
            AuditEvent.entity_id == job.id,
            AuditEvent.event_type == "COMFY_SUBMIT_INTENT",
        ).limit(1)):
            return True
    return False


def _series_has_inflight_work(db: Session, series: ProductionSeries | None) -> bool:
    """Whether external work may still be in flight for this series.

    A terminal series is settled.  A running / pause-finishing / reconciling
    series owns live work by definition.  A cancel-requested series whose owner
    is still alive waits for that owner; once the owner is gone it is settled
    exactly when no submitted job remains. A NEEDS_REVIEW series with a live
    renderer or unreconciled submit stays blocked. If it never acquired a
    renderer and only has queued, attempt-free jobs, it is settled and its
    WAITING reservations can be retired as NOT_SENT_PROVEN on cancellation.
    """
    if series is None or series.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return False
    if series.status in {"RUNNING", "PAUSE_REQUESTED", "RECONCILING"}:
        return True
    if series.lease_id:
        lease = db.get(ResourceLease, series.lease_id)
        if lease is None or lease.status != "RELEASED":
            return True
    if series.renderer_identity and series.finalization_state != "RELEASED":
        return True
    if series.status == "CANCEL_REQUESTED":
        owner_alive = bool(
            series.worker_id
            and series.lease_expires_at is not None
            and series.lease_expires_at > utcnow()
        )
        if owner_alive:
            return True
        return _series_has_unreconciled_submission(db, series)
    if series.status == "NEEDS_REVIEW":
        if series.finalization_state not in {"NONE", "RELEASED"}:
            return True
        return _series_has_unreconciled_submission(db, series)
    return _series_has_unreconciled_submission(db, series)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _create_series_for_batch(
    db: Session,
    run: ProductionRun,
    batch: ProductionRunBatch,
    *,
    settings,
) -> ProductionSeries:
    from shine.comfyops.production_series import create_series
    from shine.models import Project
    from shine.schemas import CreateJob, GenerationMode, OutputSpec, RenderProvider, ShotSpec

    package = db.get(DirectorPackageVersion, run.package_id)
    manifest = package.manifest if package else {}
    binding = manifest.get("binding") or {}
    policy = manifest.get("policy") or {}
    profile = db.get(WorkflowProfile, binding.get("workflow_profile_id") or "")
    if profile is None:
        raise RunBlocked("WORKFLOW_PROFILE_MISSING")
    project = db.get(Project, run.project_id)
    if project is None:
        raise RunBlocked("PROJECT_MISSING")

    shots = {shot.id: shot for shot in db.scalars(select(Shot).where(Shot.id.in_(batch.shot_ids))).all()}
    missing = [shot_id for shot_id in batch.shot_ids if shot_id not in shots]
    if missing:
        raise RunBlocked("BATCH_SHOT_MISSING", ",".join(missing)[:200])
    in_order = [shots[shot_id] for shot_id in batch.shot_ids]

    try:
        output = OutputSpec.model_validate(binding.get("output") or {}) if binding.get("output") else None
    except Exception as exc:
        raise RunBlocked("BATCH_OUTPUT_SPEC_INVALID", str(exc)[:200]) from None
    mode = None
    if binding.get("generation_mode"):
        try:
            mode = GenerationMode(binding["generation_mode"])
        except ValueError as exc:
            raise RunBlocked("BATCH_GENERATION_MODE_INVALID", str(binding["generation_mode"])) from exc

    body = CreateJob(
        shot_id=in_order[0].id,
        provider=RenderProvider.COMFYUI,
        workflow_profile_id=profile.id,
        generation_mode=mode,
        output=output,
        self_review_scope="full",
        # The frozen package policy reaches every job of this series — the
        # project default must not quietly widen a run's repair budget.
        max_semantic_retries=_policy_retries(policy),
        auto_repair=bool(policy.get("auto_repair", True)),
    )
    # A batch that opens with a continuation cannot create its first job
    # here: the source media belongs to an earlier series or episode, and the
    # Worker - which owns the lease and the renderer - resolves it and
    # derives the reference outside the write transaction.
    defer_first = ShotSpec.model_validate(in_order[0].spec).continuity.mode.value != "cut"
    try:
        series, _job = create_series(db, project, in_order, body=body, settings=settings, offline=False,
                                     defer_first=defer_first)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("code", "")
        raise RunBlocked("BATCH_DISPATCH_REFUSED", str(detail)[:200]) from None
    return series


def _policy_retries(policy: dict[str, Any]) -> int:
    """The semantic-retry budget a frozen policy grants, normalised at freeze time."""
    try:
        return max(0, min(20, int(policy.get("max_repairs_per_shot", 2))))
    except (TypeError, ValueError):
        return 2


def _dispatch_next_batch(db: Session, run: ProductionRun, *, settings) -> ProductionRunBatch | None:
    """Create at most one series for the run's next dispatchable batch."""
    if _live_series(db, run.id) is not None:
        return None
    episodes = _episode_rows(db, run.id)
    batches = _batch_rows(db, run.id)
    by_episode: dict[str, list[ProductionRunBatch]] = {}
    for batch in batches:
        by_episode.setdefault(batch.episode_id, []).append(batch)

    package = db.get(DirectorPackageVersion, run.package_id)
    if package is None:
        raise RunBlocked("PACKAGE_MISSING")
    # Package confirmation is not a permanent start licence: every dispatch
    # re-verifies the frozen identities, so a workflow/style/preproduction
    # change under a confirmed package blocks the next batch with its reason.
    readiness = package_readiness(db, package)
    if not readiness["startable"]:
        blocker = readiness["blockers"][0] if readiness["blockers"] else {"code": "PACKAGE_NOT_STARTABLE"}
        for episode in episodes:
            if episode.status not in {"DELIVERED", "BLOCKED"}:
                episode.status = "BLOCKED"
                episode.blocker_code = blocker["code"]
        _event(db, run, "RUN_PACKAGE_NOT_STARTABLE",
               f"导演包不再可启动：{blocker['code']}，需重新生成并确认导演包",
               {"code": blocker["code"]})
        db.flush()
        return None
    policy = package.manifest.get("policy") or {}
    limit = int(policy.get("batch_shot_limit", DEFAULT_BATCH_SHOT_LIMIT) or DEFAULT_BATCH_SHOT_LIMIT)
    # The production strategy is frozen with the package: a restart may not
    # regroup the shots of a run that is already under way.
    split_continuity = bool(policy.get("split_continuity", False))
    package_episode_rows = {row.episode_id: row for row in package_episodes(db, package.id)}

    for episode in episodes:
        if episode.status in {"DELIVERED", "BLOCKED"}:
            continue
        existing = by_episode.get(episode.episode_id, [])
        if any(batch.status == "BLOCKED" for batch in existing):
            # A blocked batch stops this episode; other episodes may continue.
            continue
        if any(batch.status in {"QUEUED", "RUNNING"} for batch in existing):
            return None
        source_episode = package_episode_rows.get(episode.episode_id)
        if source_episode is None:
            raise RunBlocked("EPISODE_NOT_IN_PACKAGE", episode.episode_id)
        planned = _batch_plan(source_episode, batch_shot_limit=limit, split_continuity=split_continuity)
        done_keys = {batch.batch_key for batch in existing}
        remaining = [row for row in planned if row["batch_key"] not in done_keys]
        if not remaining:
            continue
        plan = remaining[0]
        batch = ProductionRunBatch(
            run_id=run.id,
            episode_row_id=episode.id,
            episode_id=episode.episode_id,
            batch_key=plan["batch_key"],
            position=plan["position"],
            input_digest=plan["input_digest"],
            shot_ids=plan["shot_ids"],
            status="PLANNED",
        )
        db.add(batch)
        db.flush()
        try:
            series = _create_series_for_batch(db, run, batch, settings=settings)
        except RunBlocked as exc:
            batch.status = "BLOCKED"
            batch.failure_code = exc.code
            episode.status = "BLOCKED"
            episode.blocker_code = exc.code
            _event(db, run, "BATCH_BLOCKED", f"{batch.batch_key} 无法开拍：{exc.code}",
                   {"batch_key": batch.batch_key, "detail": exc.detail})
            db.flush()
            return batch
        batch.series_id = series.id
        batch.status = "QUEUED"
        batch.dispatched_at = utcnow()
        episode.status = "PRODUCING"
        run.status = "RUNNING"
        run.started_at = run.started_at or utcnow()
        _event(db, run, "BATCH_DISPATCHED", f"{batch.batch_key} 已交给制作序列",
               {"batch_key": batch.batch_key, "series_id": series.id, "shots": len(batch.shot_ids)})
        db.flush()
        return batch
    return None


# ---------------------------------------------------------------------------
# Episode delivery hand-off
# ---------------------------------------------------------------------------


def _ensure_episode_delivery(
    db: Session,
    run: ProductionRun,
    episode: ProductionRunEpisode,
) -> tuple[str | None, bool]:
    """Create the per-episode delivery order once its *whole plan* really passed.

    The second element says whether this call *created* the order.  Finding an
    order that already exists is a check, not new work: an order that is
    already PENDING, ASSEMBLING or DELIVERED is reported back to the caller so
    the run keeps pointing at it, but it must never be counted as progress.
    Re-reporting the same pending order every beat is what made the assembly
    executor look permanently starved.
    """
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.episode_delivery import episode_input_digest

    package = db.get(DirectorPackageVersion, run.package_id)
    if package is None:
        return None, False
    batches = [b for b in _batch_rows(db, run.id) if b.episode_id == episode.episode_id]

    # 1. The complete planned batch set must exist and have passed.  Batches
    #    that were never dispatched are unfinished work, not history.
    source = next((row for row in package_episodes(db, package.id)
                   if row.episode_id == episode.episode_id), None)
    if source is None:
        return None, False
    policy = package.manifest.get("policy") or {}
    limit = int(policy.get("batch_shot_limit", DEFAULT_BATCH_SHOT_LIMIT) or DEFAULT_BATCH_SHOT_LIMIT)
    split_continuity = bool(policy.get("split_continuity", False))
    planned_keys = {
        row["batch_key"]
        for row in _batch_plan(source, batch_shot_limit=limit, split_continuity=split_continuity)
    }
    existing_keys = {batch.batch_key for batch in batches}
    if (planned_keys - existing_keys) or any(batch.status != "PASSED" for batch in batches):
        return None, False

    # 2. Every shot must have a passed job that this run's own series items
    #    produced.  An unrelated passed job never completes this run.
    passed = run_owned_passed_jobs(db, run.id, [episode])
    missing = [shot_id for shot_id in episode.shot_ids if shot_id not in passed]
    if missing:
        return None, False
    digest = episode_input_digest(db, run, episode, batches)
    existing = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.scope_kind == "episode",
            DeliveryOrder.scope_id == episode.id,
        )
    ).all()
    for order in existing:
        if order.status == "ASSEMBLING":
            # A running assembly is never cancelled mid-file; a revision waits
            # for it and becomes the next version afterwards.
            episode.delivery_order_id = order.id
            return order.id, False
        if order.status == "PENDING":
            if order.input_digest == digest:
                episode.delivery_order_id = order.id
                return order.id, False
            # Queued for an earlier contract and not started: supersede it.
            order.status = "CANCELLED"
            order.failure_code = "POST_CONTRACT_REVISED"
            order.state_version += 1
            continue
        if order.status == "DELIVERED" and order.input_digest == digest:
            episode.delivery_order_id = order.id
            return order.id, False
    contract = episode.post_contract or {}
    order = DeliveryOrder(
        project_id=run.project_id,
        series_id=None,
        scope_kind="episode",
        scope_id=episode.id,
        origin="run_episode",
        version=max((row.version for row in existing), default=0) + 1,
        status="PENDING",
        input_digest=digest,
        shots_digest=digest,
        subtitle_revision=contract.get("subtitle_revision"),
        subtitle_sha256=contract.get("subtitle_sha256"),
    )
    db.add(order)
    db.flush()
    episode.delivery_order_id = order.id
    episode.status = "DELIVERING"
    _event(db, run, "EPISODE_DELIVERY_QUEUED", f"{episode.title or episode.episode_id} 开始逐集装配",
           {"episode_id": episode.episode_id, "order_id": order.id})
    return order.id, True


def _sync_episode_delivery(db: Session, run: ProductionRun, episode: ProductionRunEpisode) -> bool:
    from shine.media.delivery_models import DeliveryOrder

    if not episode.delivery_order_id:
        return False
    order = db.get(DeliveryOrder, episode.delivery_order_id)
    if order is None:
        return False
    changed = False
    if order.status == "DELIVERED" and order.artifact_id:
        artifact = db.get(MediaArtifact, order.artifact_id)
        if episode.status != "DELIVERED":
            episode.status = "DELIVERED"
            episode.delivered_artifact_id = order.artifact_id
            episode.delivered_sha256 = artifact.sha256 if artifact else None
            episode.actual_seconds = float((artifact.provenance or {}).get("duration_s") or 0.0) if artifact else None
            episode.delivered_at = order.delivered_at or utcnow()
            episode.blocker_code = None
            changed = True
    elif order.status in {"FAILED", "BLOCKED"} and episode.status != "BLOCKED":
        episode.status = "BLOCKED"
        episode.blocker_code = order.failure_code or "EPISODE_DELIVERY_FAILED"
        changed = True
    return changed


def _unblock_resolved_episodes(db: Session, run: ProductionRun) -> int:
    """Re-open episodes whose batches no longer block them.

    A blocker that a reconciliation resolved (a series that later completed, an
    operator decision) must not leave its episode blocked forever; the normal
    advance flow re-derives delivery from the real rows.
    """
    batches = _batch_rows(db, run.id)
    changed = 0
    for episode in _episode_rows(db, run.id):
        if episode.status != "BLOCKED":
            continue
        episode_batches = [batch for batch in batches if batch.episode_id == episode.episode_id]
        if not episode_batches:
            continue
        if any(batch.status == "BLOCKED" for batch in episode_batches):
            continue
        episode.status = "PRODUCING"
        episode.blocker_code = None
        changed += 1
    return changed


# ---------------------------------------------------------------------------
# Advance
# ---------------------------------------------------------------------------


def _claim_run(db: Session, run: ProductionRun, worker_id: str) -> bool:
    """Take or renew the run's advance ownership with a conditional UPDATE.

    The run claim only grants the right to *plan* work.  It never substitutes
    for the renderer lease: two claims may never produce two submissions because
    series creation itself is guarded by the existing live-shot checks.
    """
    now = utcnow()
    result = db.execute(
        update(ProductionRun)
        .where(
            ProductionRun.id == run.id,
            ProductionRun.state_version == run.state_version,
            or_(
                ProductionRun.claim_owner.is_(None),
                ProductionRun.claim_owner == worker_id,
                ProductionRun.claim_expires_at.is_(None),
                ProductionRun.claim_expires_at <= now,
            ),
        )
        .values(
            claim_owner=worker_id,
            claim_expires_at=now + timedelta(seconds=RUN_LEASE_SECONDS),
            heartbeat_at=now,
            state_version=ProductionRun.state_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _fenced_pause(db: Session, run: ProductionRun, reason: str) -> bool:
    """Pause the run through a conditional UPDATE; a stale object can never win.

    The budget and storage guards run *before* the claim, on whatever run object
    the caller handed in — which may be arbitrarily old.  Only a conditional
    update against the row's current version may change the state, so a session
    that still remembers a live run cannot revive one that was cancelled or
    completed meanwhile.
    """
    result = db.execute(
        update(ProductionRun)
        .where(
            ProductionRun.id == run.id,
            ProductionRun.state_version == run.state_version,
            ProductionRun.status.in_(["QUEUED", "RUNNING"]),
        )
        .values(
            status="PAUSED",
            control_reason=reason,
            claim_owner=None,
            claim_expires_at=None,
            state_version=ProductionRun.state_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        # Somebody else already decided this run.  Discard every pending
        # change the stale object may carry and leave the decision alone.
        db.rollback()
        return False
    _pause_child_series(db, run.id, reason)
    _event(db, run, "RUN_PAUSED", _pause_message(reason))
    db.flush()
    return True


def _pause_message(reason: str) -> str:
    if reason == "BUDGET_EXHAUSTED":
        return "生产预算已用尽，等待人工确认是否继续"
    if reason == "STORAGE_LOW":
        return "磁盘空间低于预留，暂停新提交"
    return "运行暂停：不再安排新的片段，当前片段等待安全落盘"


def _pause_child_series(db: Session, run_id: str, reason: str) -> bool:
    """Propagate a run-level pause into the child series.

    A queued series has nothing in flight and pauses immediately.  A running
    series owns external work and only *requests* the pause; its worker moves it
    to PAUSED once the current shot reached a safe boundary.  Returns whether
    any child still has work in flight.
    """
    live = False
    for batch in _batch_rows(db, run_id):
        if not batch.series_id:
            continue
        series = db.get(ProductionSeries, batch.series_id)
        if series is None or series.status in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
            continue
        if series.status in {"RUNNING", "PAUSE_REQUESTED"}:
            if series.status != "PAUSE_REQUESTED":
                series.state_version += 1
            series.status = "PAUSE_REQUESTED"
            series.control_reason = series.control_reason or reason
            live = True
        elif series.status == "QUEUED":
            series.status = "PAUSED"
            series.control_reason = reason
            series.state_version += 1
        elif series.status == "PAUSED":
            series.control_reason = series.control_reason or reason
    return live


def _finalize_cancel(db: Session, run: ProductionRun, *, actor: str = "user") -> None:
    """Complete a cancellation whose external work has settled."""
    from shine.comfyops.production_series import cancel_series_in_session
    from shine.media.delivery_models import DeliveryOrder

    for batch in _batch_rows(db, run.id):
        if not batch.series_id:
            continue
        series = db.get(ProductionSeries, batch.series_id)
        if series is None:
            continue
        if _series_has_inflight_work(db, series):
            return  # not settled yet; a later beat finalises
        if series.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            cancel_series_in_session(db, series.id, reason="RUN_CANCELLED", actor=actor)
    for episode in _episode_rows(db, run.id):
        if not episode.delivery_order_id:
            continue
        order = db.get(DeliveryOrder, episode.delivery_order_id)
        if order is not None and order.status == "PENDING":
            order.status = "CANCELLED"
            order.failure_code = "RUN_CANCELLED"
            order.state_version += 1
    run.status = "CANCELLED"
    run.control_reason = run.control_reason or "USER_CANCELLED"
    run.finished_at = utcnow()
    run.claim_owner = None
    run.claim_expires_at = None
    _event(db, run, "RUN_CANCELLED", "取消收尾完成：已产出数据全部保留")
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "PRODUCTION_RUN_CANCELLED", "production_run", run.id,
              {"delivered_episodes": (run.counters or {}).get("delivered_episode_count", 0)},
              project_id=run.project_id)


def advance_run(
    db: Session,
    run: ProductionRun,
    *,
    worker_id: str,
    settings,
) -> dict[str, Any]:
    """One bounded advance step.  Safe to call repeatedly and concurrently."""
    if run.status not in ADVANCEABLE_RUN_STATUSES:
        return {"run_id": run.id, "status": run.status, "action": "skipped"}

    if run.status == "CANCEL_REQUESTED":
        # The cancellation is waiting for its external work to settle.  This
        # beat only reconciles; it never starts anything new.
        if not _claim_run(db, run, worker_id):
            return {"run_id": run.id, "status": run.status, "action": "claimed_elsewhere"}
        for batch in _batch_rows(db, run.id):
            _sync_batch_from_series(db, batch)
        inflight = any(
            _series_has_inflight_work(db, db.get(ProductionSeries, batch.series_id))
            for batch in _batch_rows(db, run.id) if batch.series_id
        )
        if not inflight:
            _finalize_cancel(db, run, actor=worker_id)
            return {"run_id": run.id, "status": run.status, "action": "cancelled"}
        db.flush()
        return {"run_id": run.id, "status": run.status, "action": "cancel_reconciling"}

    budget = run.budget or {}
    if run.started_at and budget.get("max_wall_seconds"):
        elapsed = (utcnow() - run.started_at).total_seconds()
        if elapsed > float(budget["max_wall_seconds"]):
            if not _fenced_pause(db, run, "BUDGET_EXHAUSTED"):
                return {"run_id": run.id, "status": db.get(ProductionRun, run.id).status
                        if db.get(ProductionRun, run.id) else "GONE", "action": "skipped_stale"}
            return {"run_id": run.id, "status": "PAUSED", "action": "paused"}

    min_free = float(budget.get("min_free_gb") or 0)
    if min_free:
        from shine.comfyops.storage import disk_info
        from shine.media.output_layout import output_root

        try:
            free_gb = float(disk_info(output_root(db, settings)).get("free_gb") or 0)
        except OSError:
            free_gb = None
        if free_gb is not None and free_gb < min_free:
            if not _fenced_pause(db, run, "STORAGE_LOW"):
                return {"run_id": run.id, "status": db.get(ProductionRun, run.id).status
                        if db.get(ProductionRun, run.id) else "GONE", "action": "skipped_stale"}
            return {"run_id": run.id, "status": "PAUSED", "action": "paused"}

    if not _claim_run(db, run, worker_id):
        return {"run_id": run.id, "status": run.status, "action": "claimed_elsewhere"}
    # The claim bumped state_version in SQL; re-read it so the ORM writes below
    # extend the claimed version instead of clobbering a newer one.
    db.refresh(run)

    status_before = run.status
    actions: list[str] = []
    observations: list[str] = []
    for batch in _batch_rows(db, run.id):
        if _sync_batch_from_series(db, batch):
            actions.append("batch_sync")

    # A batch that its own series could not finish blocks *its* episode, and
    # only that episode: every other episode keeps running.  A blocked batch is
    # never retried automatically — the operator decides.
    batches = _batch_rows(db, run.id)
    for batch in batches:
        if batch.status != "BLOCKED":
            continue
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        if episode is not None and episode.status not in {"DELIVERED", "BLOCKED"}:
            episode.status = "BLOCKED"
            episode.blocker_code = batch.failure_code or "BATCH_BLOCKED"
            actions.append("episode_blocked")
    if _unblock_resolved_episodes(db, run):
        actions.append("episode_unblocked")

    episodes = _episode_rows(db, run.id)
    for episode in episodes:
        if _sync_episode_delivery(db, run, episode):
            actions.append("episode_delivery")

    for episode in episodes:
        if episode.status in {"PLANNED", "PRODUCING", "DELIVERING"}:
            order_id, created = _ensure_episode_delivery(db, run, episode)
            if created:
                actions.append("episode_queued")
            elif order_id:
                # An order that already exists is a *check*, not new work.  The
                # assembly executor still owns it, so re-reporting it here would
                # make every beat look like progress and starve that executor.
                observations.append("episode_delivery_pending")

    dispatched = _dispatch_next_batch(db, run, settings=settings)
    if dispatched is not None:
        actions.append("batch_dispatched")

    episodes = _episode_rows(db, run.id)
    batches = _batch_rows(db, run.id)
    delivered = [episode for episode in episodes if episode.status == "DELIVERED"]
    blocked_episodes = [episode for episode in episodes if episode.status == "BLOCKED"]
    blocked_batches = [batch for batch in batches if batch.status == "BLOCKED"]
    run.blocker_count = len(blocked_batches)
    run.counters = {
        **dict(run.counters or {}),
        "required_episode_count": len(episodes),
        "delivered_episode_count": len(delivered),
        "batch_count": len(batches),
        "passed_batch_count": len([batch for batch in batches if batch.status == "PASSED"]),
        "delivered_seconds": round(sum(episode.actual_seconds or 0.0 for episode in delivered), 3),
    }

    unfinished = [episode for episode in episodes if episode.status not in {"DELIVERED", "BLOCKED"}]
    if not unfinished and not blocked_episodes:
        run.status = "COMPLETED"
        run.finished_at = utcnow()
        run.claim_owner = None
        run.claim_expires_at = None
        _event(db, run, "RUN_COMPLETED",
               f"全部 {len(episodes)} 集已交付；艺术验收状态另计")
    elif not unfinished and blocked_episodes:
        run.status = "NEEDS_REVIEW"
        run.control_reason = blocked_episodes[0].blocker_code or "EPISODE_BLOCKED"
        _event(db, run, "RUN_NEEDS_REVIEW",
               f"{len(blocked_episodes)} 集有未解决阻塞，其余可运行部分已推进完毕")
    else:
        run.status = "RUNNING"
    # Real progress only: a beat that changed nothing must not move the
    # progress clock, or waiting would look like work.
    progressed = bool(actions) or run.status != status_before
    if progressed:
        run.last_progress_at = utcnow()
    run.state_version += 1
    db.flush()
    # "checked" is not "advanced".  A beat that only inspected the run and
    # found nothing to do must hand the turn to the Worker's other
    # executors, or the run's own series and its episode assemblies never
    # get a chance to run.
    return {
        "run_id": run.id,
        "status": run.status,
        "action": "advanced" if progressed else "waiting",
        "changes": sorted(set(actions)),
        "observed": sorted(set(observations)),
    }


class RunAdvanceSummary:
    """What one scheduler beat did with the whole-film run layer.

    ``checked`` counts the runs that were inspected; ``progressed`` counts only
    the ones whose durable state really changed.  The Worker decides whether
    the run layer has earned the tick from the second number: a run that only
    looked at its own state must not keep the series, the episode assembly or
    the ordinary queue from running.
    """

    __slots__ = ("checked", "progressed", "dispatched", "queued")

    def __init__(self, checked: int = 0, progressed: int = 0,
                 dispatched: int = 0, queued: int = 0) -> None:
        self.checked = checked
        self.progressed = progressed
        self.dispatched = dispatched
        self.queued = queued

    def __bool__(self) -> bool:
        return bool(self.progressed)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"RunAdvanceSummary(checked={self.checked}, progressed={self.progressed}, "
                f"dispatched={self.dispatched}, queued={self.queued})")


def advance_runs(session_factory, *, worker_id: str, settings, limit: int = 1) -> RunAdvanceSummary:
    """Drive at most ``limit`` runnable runs in one scheduler beat."""
    with session_factory() as db:
        candidates = db.execute(
            select(ProductionRun.id)
            .where(ProductionRun.status.in_(ADVANCEABLE_RUN_STATUSES))
            # Fairness: the run that was advanced longest ago goes first, so a
            # run with a long queue cannot starve the other runs.  ``updated_at``
            # moves on every advance because the run's own state version
            # changes, which makes this a true rotation rather than a
            # first-come-first-served queue that always picks the same row.
            .order_by(ProductionRun.updated_at.asc(), ProductionRun.created_at.asc())
            .limit(max(1, limit))
        ).all()
    checked = 0
    progressed = 0
    dispatched = 0
    queued = 0
    for (run_id,) in candidates:
        try:
            with session_factory.begin() as db:
                run = db.get(ProductionRun, run_id)
                if run is None:
                    continue
                result = advance_run(db, run, worker_id=worker_id, settings=settings)
            checked += 1
            changes = set(result.get("changes") or ())
            dispatched += 1 if "batch_dispatched" in changes else 0
            queued += 1 if "episode_queued" in changes else 0
            if result.get("action") in {"advanced", "paused", "cancelled", "cancel_reconciling"}:
                progressed += 1
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the loop
            logger.warning("production_run_advance_failed run_id=%s error=%s", run_id, type(exc).__name__)
    return RunAdvanceSummary(checked=checked, progressed=progressed,
                              dispatched=dispatched, queued=queued)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


def _control(run: ProductionRun, status: str, reason: str | None) -> None:
    run.status = status
    run.control_reason = reason
    run.state_version += 1


def pause_run(session_factory, run_id: str, *, worker_id: str = "user", expected_version: int | None = None) -> str:
    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return "GONE"
        if expected_version is not None and run.state_version != expected_version:
            return "STATE_VERSION_CONFLICT"
        if run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
            return run.status
        if run.status in {"PAUSED", "PAUSE_REQUESTED"}:
            return run.status
        live = _pause_child_series(db, run.id, "USER_PAUSED")
        status = "PAUSE_REQUESTED" if live else "PAUSED"
        _control(run, status, "USER_PAUSED")
        _event(db, run, "RUN_PAUSED",
               "用户暂停：不再安排新的片段，当前片段等待安全落盘" if status == "PAUSE_REQUESTED"
               else "用户暂停：已到安全边界")
        db.flush()
        return run.status


def _resume_safe_pre_submit_series(db: Session, run: ProductionRun, *, actor: str) -> int:
    """Reopen only run series whose failed item is proven not to have reached H3.

    This is intentionally narrower than retrying a failed render. A workflow
    slot rejection before graph preparation can be replaced only after the
    current shot bindings now fit the run's frozen profile. A series that
    could not obtain the shared GPU lease can continue only when its first
    queued job has no attempt. Any graph hash, provider id, acknowledgement,
    artifact, active lease, or unknown failure keeps the blocker intact.
    """
    from shine.comfy.client import ComfyError
    from shine.comfy.provider import collect_unbound_transfer_families, validate_reference_slots
    from shine.comfyops.models import ResourceLease
    from shine.models import Asset
    from shine.services import manifest_from_shot_bindings

    recovered = 0
    package = db.get(DirectorPackageVersion, run.package_id)
    # This path repairs a submission that was *proven never sent*.  It must not
    # be gated by the package's new-run readiness: a corrected binding is
    # exactly the drift that makes a fresh run refuse to start, while the
    # recovery only replaces an unsubmitted request.  The scope and the frozen
    # execution identity are still enforced below by ``current_bindings_are_safe``
    # and by the package's own confirmation.
    if package is None or package.stale or package.status != "CONFIRMED":
        return 0
    pinned_shots = {
        shot.get("shot_id"): shot
        for episode in package_episodes(db, package.id)
        for shot in episode_manifest(episode).get("shots", [])
    }
    binding_pin = (package.manifest or {}).get("binding") or {}

    # A superseded request was explicitly proven unsent when its immutable
    # snapshot was preserved. Its old scheduler reservation must not retain a
    # place ahead of the replacement forever; WAITING means it never owned the
    # renderer, so reconciling this reservation has no external side effect.
    superseded_events = db.scalars(select(AuditEvent).where(
        AuditEvent.project_id == run.project_id,
        AuditEvent.event_type == "RUN_JOB_SUPERSEDED_PRE_SUBMIT",
    )).all()
    for event in superseded_events:
        proof = event.payload if isinstance(event.payload, dict) else {}
        if proof.get("external_submission") != "NOT_SENT_PROVEN" or proof.get("attempt_count") != 0:
            continue
        job = db.get(Job, event.entity_id)
        if (job is None or job.project_id != run.project_id or not job.stale
                or job.status != "NEEDS_REVIEW" or job.failure_code != "RUN_BINDINGS_REVISED_BEFORE_SUBMIT"
                or job.provider_job_id or job.artifact_id
                or db.scalar(select(JobAttempt.id).where(JobAttempt.job_id == job.id).limit(1))):
            continue
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job.id))
        if lease is None or lease.status != "WAITING":
            continue
        lease.status = "RELEASED"
        lease.expires_at = utcnow()
        from shine.services import add_audit

        add_audit(
            db, actor, "RESOURCE_LEASE_RELEASED_PRE_SUBMIT", "resource_lease", lease.id,
            {"owner_job_id": job.id, "external_submission": "NOT_SENT_PROVEN",
             "basis_event_id": event.id},
            project_id=run.project_id,
        )

    def current_bindings_are_safe(shot: Shot, profile: WorkflowProfile, manifest) -> bool:
        """Allow slot recategorization only for the same pinned, locked masters."""
        if (profile.mode != binding_pin.get("mode")
                or (profile.parameter_map or {}) != (binding_pin.get("parameter_map") or {})
                or (profile.media_transfers or {}) != (binding_pin.get("media_transfers") or {})):
            return False
        pinned_shot = pinned_shots.get(shot.id)
        pinned_bindings = {
            str(row.get("asset_id")): row for row in (pinned_shot or {}).get("bindings", [])
        }
        current_assets = {row.asset_id: db.get(Asset, row.asset_id) for row in manifest}
        if not pinned_bindings or set(pinned_bindings) != set(current_assets) or any(
            asset is None or not asset.approved or not asset.locked
            or asset.sha256 != pinned_bindings[asset_id].get("sha256")
            for asset_id, asset in current_assets.items()
        ):
            return False
        try:
            validate_reference_slots(profile.parameter_map, manifest)
        except (ComfyError, HTTPException, ValueError):
            return False
        # An unbound declared transfer family is a warning now, not a refusal:
        # the unused loader is pruned and the shot still renders.
        collect_unbound_transfer_families(profile.media_transfers or {}, manifest, current_assets)
        return True

    for batch in _batch_rows(db, run.id):
        if batch.status != "BLOCKED" or not batch.series_id:
            continue
        series = db.get(ProductionSeries, batch.series_id)
        if series is None or series.status != "NEEDS_REVIEW":
            continue
        if series.worker_id and series.lease_expires_at and series.lease_expires_at > utcnow():
            continue
        if series.lease_id:
            held = db.get(ResourceLease, series.lease_id)
            if held is None or held.status != "RELEASED":
                continue
        if db.scalar(select(ResourceLease.id).where(
            ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]),
        ).limit(1)):
            continue

        items = list(db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all())
        if not items:
            continue

        recovery_kind: str | None = None
        old_job_id: str | None = None
        current_item: ProductionSeriesItem | None = None
        current_job: Job | None = None

        if series.failure_code == "SERIES_EXECUTION_FAILED:HTTPException":
            blocked = next((item for item in items if item.status == "BLOCKED"), None)
            job = db.get(Job, blocked.job_id) if blocked and blocked.job_id else None
            if not blocked or not job or job.provider != "comfyui" or job.status != "NEEDS_REVIEW" \
                    or job.failure_code != "PENDING_WORKFLOW_MAPPING" or job.artifact_id or job.provider_job_id:
                continue
            attempts = list(db.scalars(
                select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no)
            ).all())
            if not attempts or any(
                attempt.status != "FAILED" or attempt.failure_code != "PENDING_WORKFLOW_MAPPING"
                or attempt.provider_job_id or attempt.submit_ack_at or attempt.prompt_graph_sha256
                or attempt.output_sha256 or attempt.output_bytes
                or db.get(AppSetting, "renderer-activation:" + attempt.id) is not None
                for attempt in attempts
            ):
                continue
            has_pre_submit_proof = db.scalar(select(AuditEvent.id).where(
                AuditEvent.project_id == run.project_id,
                AuditEvent.entity_type == "job",
                AuditEvent.entity_id == job.id,
                AuditEvent.event_type == "COMFY_WORKFLOW_MAPPING_PENDING",
            ).limit(1))
            submitted = db.scalar(select(AuditEvent.id).where(
                AuditEvent.project_id == run.project_id,
                AuditEvent.entity_type == "job",
                AuditEvent.entity_id == job.id,
                AuditEvent.event_type == "COMFY_SUBMIT_INTENT",
            ).limit(1))
            if not has_pre_submit_proof or submitted or db.scalar(
                select(MediaArtifact.id).where(MediaArtifact.job_id == job.id).limit(1)
            ):
                continue

            shot = db.get(Shot, blocked.shot_id)
            profile = db.get(WorkflowProfile, series.workflow_profile_id or "")
            if shot is None or profile is None or job.shot_revision != shot.revision:
                continue
            manifest = manifest_from_shot_bindings(db, shot)
            if not current_bindings_are_safe(shot, profile, manifest):
                continue
            previous_manifest = list((job.request or {}).get("reference_manifest") or [])
            current_manifest = [row.model_dump(mode="json") for row in manifest]
            if not previous_manifest or digest_of(previous_manifest) == digest_of(current_manifest):
                continue
            recovery_kind = "PENDING_WORKFLOW_MAPPING_PROVEN_BEFORE_SUBMIT"
            old_job_id = job.id
            current_item = blocked
            current_job = None

        elif series.failure_code == "SERIES_INCOMPLETE":
            current_item = next((item for item in items if item.status not in {"PASSED", "CANCELLED"}), None)
            current_job = db.get(Job, current_item.job_id) if current_item and current_item.job_id else None
            if not current_item or not current_job or current_job.status != "QUEUED" \
                    or current_job.provider != "comfyui" or current_job.paused or current_job.stale \
                    or current_job.artifact_id or current_job.provider_job_id \
                    or db.scalar(select(JobAttempt.id).where(JobAttempt.job_id == current_job.id).limit(1)):
                continue
            waiting = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + current_job.id))
            if waiting is not None and waiting.status not in {"WAITING", "RELEASED"}:
                continue
            shot = db.get(Shot, current_item.shot_id)
            profile = db.get(WorkflowProfile, series.workflow_profile_id or "")
            if shot is None or profile is None or current_job.shot_revision != shot.revision:
                continue
            manifest = manifest_from_shot_bindings(db, shot)
            if not current_bindings_are_safe(shot, profile, manifest):
                continue
            previous_manifest = list((current_job.request or {}).get("reference_manifest") or [])
            current_manifest = [row.model_dump(mode="json") for row in manifest]
            if not previous_manifest:
                continue
            if digest_of(previous_manifest) == digest_of(current_manifest):
                recovery_kind = "QUEUED_JOB_WITHOUT_ATTEMPT"
            else:
                # A queued job is an immutable request snapshot. Preserve it as
                # stale history and let the Worker create a new job from the
                # current bindings; no attempt means no external submission.
                current_job.stale = True
                current_job.failure_code = "RUN_BINDINGS_REVISED_BEFORE_SUBMIT"
                current_job.status = "NEEDS_REVIEW"
                current_job.state_version += 1
                recovery_kind = "QUEUED_JOB_REPLACED_PRE_SUBMIT_BINDINGS_CHANGED"
            old_job_id = current_job.id
        else:
            continue

        if recovery_kind in {
            "PENDING_WORKFLOW_MAPPING_PROVEN_BEFORE_SUBMIT",
            "QUEUED_JOB_REPLACED_PRE_SUBMIT_BINDINGS_CHANGED",
        }:
            # The old failed job and attempt stay as immutable history. The
            # series creates a new job from the now-correct shot bindings.
            old_lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + old_job_id))
            if old_lease is not None and old_lease.status == "WAITING":
                old_lease.status = "RELEASED"
                old_lease.expires_at = utcnow()
                from shine.services import add_audit

                add_audit(
                    db, actor, "RESOURCE_LEASE_RELEASED_PRE_SUBMIT", "resource_lease", old_lease.id,
                    {"owner_job_id": old_job_id, "external_submission": "NOT_SENT_PROVEN",
                     "recovery_kind": recovery_kind},
                    project_id=run.project_id,
                )
            current_item.job_id = None
            current_item.status = "PENDING"
            current_item.failure_code = None
            series.current_item_id = None
            series.current_job_id = None
        else:
            current_item.status = "QUEUED"
            current_item.failure_code = None
            series.current_item_id = current_item.id
            series.current_job_id = current_job.id

        # Safe continuation only reopens durable queue state. The resident
        # Worker claims one series at a time and alone promotes it to RUNNING
        # after it owns the series lease.
        series.status = "QUEUED"
        series.worker_id = None
        series.lease_expires_at = None
        series.heartbeat_at = None
        series.lease_id = None
        series.failure_code = None
        series.finished_at = None
        series.control_reason = None
        series.state_version += 1
        batch.status = "QUEUED"
        batch.failure_code = None
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        if episode is not None:
            episode.status = "PRODUCING"
            episode.blocker_code = None
        from shine.services import add_audit

        add_audit(
            db, actor, "PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT", "production_series", series.id,
            {"run_id": run.id, "batch_key": batch.batch_key, "recovery_kind": recovery_kind,
             "previous_job_id": old_job_id, "external_submission": "NOT_SENT_PROVEN",
             "old_job_preserved": True},
            project_id=run.project_id,
        )
        if recovery_kind == "QUEUED_JOB_REPLACED_PRE_SUBMIT_BINDINGS_CHANGED":
            add_audit(
                db, actor, "RUN_JOB_SUPERSEDED_PRE_SUBMIT", "job", old_job_id,
                {"series_id": series.id, "replacement_manifest_sha256": digest_of(current_manifest),
                 "external_submission": "NOT_SENT_PROVEN", "attempt_count": 0,
                 "job_preserved": True},
                project_id=run.project_id,
            )
        recovered += 1
    return recovered


def resume_run(session_factory, run_id: str, *, actor: str = "user",
               expected_version: int | None = None) -> str:
    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return "GONE"
        if expected_version is not None and run.state_version != expected_version:
            return "STATE_VERSION_CONFLICT"
        if run.status not in {"PAUSED", "PAUSE_REQUESTED", "NEEDS_REVIEW"}:
            return run.status

        recovered = 0
        if run.status == "NEEDS_REVIEW":
            recovered = _resume_safe_pre_submit_series(db, run, actor=actor)

        # Re-read what really happened while the run stood still: a blocker
        # that a reconciliation resolved may make the run runnable again.
        for batch in _batch_rows(db, run.id):
            _sync_batch_from_series(db, batch)
        _unblock_resolved_episodes(db, run)
        for episode in _episode_rows(db, run.id):
            _sync_episode_delivery(db, run, episode)

        if run.status == "NEEDS_REVIEW":
            episodes = _episode_rows(db, run.id)
            batches = _batch_rows(db, run.id)
            still_blocked = (any(episode.status == "BLOCKED" for episode in episodes)
                             or any(batch.status == "BLOCKED" for batch in batches))
            if still_blocked and not recovered:
                return "NEEDS_REVIEW"
            _control(run, "RUNNING", None)
            message = (f"已核验并恢复 {recovered} 个未提交的制作序列；其他阻塞保持原状"
                       if still_blocked else "阻塞已解除，恢复生产")
            _event(db, run, "RUN_RESUMED", message, {"recovered_pre_submit_series": recovered})
            db.flush()
            return run.status

        # Resume only the children this run paused: a series paused by its own
        # control path keeps its pause until that path releases it.
        from shine.models import ProductionSeries as _Series

        for batch in _batch_rows(db, run.id):
            if not batch.series_id:
                continue
            series = db.get(_Series, batch.series_id)
            if series is None or series.control_reason not in RUN_PAUSE_REASONS:
                continue
            owner_alive = bool(
                series.worker_id
                and series.lease_expires_at is not None
                and series.lease_expires_at > utcnow()
            )
            if series.status == "PAUSED":
                series.status = "RUNNING" if owner_alive else "QUEUED"
                if not owner_alive:
                    series.worker_id = None
                    series.lease_expires_at = None
            elif series.status == "PAUSE_REQUESTED":
                series.status = "RUNNING"
            else:
                continue
            series.control_reason = None
            series.state_version += 1
        _control(run, "RUNNING", None)
        _event(db, run, "RUN_RESUMED", "用户恢复生产")
        db.flush()
        return run.status


def cancel_run(session_factory, run_id: str, *, actor: str = "user", expected_version: int | None = None) -> str:
    """Cancel without deleting anything and without touching foreign resources.

    Cancellation is a two-step protocol, not a fiat: series that still own
    external work are asked to stop and the run stays ``CANCEL_REQUESTED``
    until that work settled and was reconciled.  Only then does the run reach
    its terminal ``CANCELLED`` state.
    """
    from shine.comfyops.production_series import cancel_series_in_session
    from shine.media.delivery_models import DeliveryOrder

    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return "GONE"
        if expected_version is not None and run.state_version != expected_version:
            return "STATE_VERSION_CONFLICT"
        if run.status in {"CANCELLED", "COMPLETED", "FAILED"}:
            return run.status
        inflight = False
        for batch in _batch_rows(db, run.id):
            if not batch.series_id:
                continue
            series = db.get(ProductionSeries, batch.series_id)
            if series is None:
                continue
            if _series_has_inflight_work(db, series):
                inflight = True
                if series.status in {"RUNNING", "PAUSE_REQUESTED", "QUEUED", "PAUSED"}:
                    # The owner observes this between shots: it finishes the
                    # current submission, releases the renderer and then the
                    # series reaches CANCELLED — never a blind stop.
                    if series.status != "CANCEL_REQUESTED":
                        series.status = "CANCEL_REQUESTED"
                        series.state_version += 1
            elif series.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                cancel_series_in_session(db, series.id, reason="RUN_CANCELLED", actor=actor)
        for episode in _episode_rows(db, run.id):
            if not episode.delivery_order_id:
                continue
            order = db.get(DeliveryOrder, episode.delivery_order_id)
            if order is not None and order.status == "PENDING":
                order.status = "CANCELLED"
                order.failure_code = "RUN_CANCELLED"
                order.state_version += 1
        if inflight:
            _control(run, "CANCEL_REQUESTED", "USER_CANCELLED")
            _event(db, run, "RUN_CANCEL_REQUESTED",
                   "用户取消：等待已提交片段与受管资源收尾，产物全部保留")
            db.flush()
            return run.status
        _control(run, "CANCELLED", "USER_CANCELLED")
        run.finished_at = utcnow()
        run.claim_owner = None
        run.claim_expires_at = None
        _event(db, run, "RUN_CANCELLED", "用户取消：已产出数据全部保留")
        db.flush()
        from shine.services import add_audit

        add_audit(db, actor, "PRODUCTION_RUN_CANCELLED", "production_run", run.id,
                  {"delivered_episodes": (run.counters or {}).get("delivered_episode_count", 0)},
                  project_id=run.project_id)
        return run.status


def set_episode_post_contract(
    session_factory,
    run_id: str,
    episode_id: str,
    body: dict[str, Any] | None,
    *,
    actor: str = "local-user",
) -> dict[str, Any]:
    """Revise one episode's cut/subtitle contract; re-assemble, never re-render.

    The contract is validated against the episode (its own shots, an approved
    subtitle revision) and pinned.  When the episode's takes are complete a new
    delivery version is queued from the *same* run-owned takes; an assembly
    already running is not interrupted.
    """
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.episode_delivery import normalized_post_contract

    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
        episode = db.scalars(
            select(ProductionRunEpisode).where(
                ProductionRunEpisode.run_id == run_id,
                ProductionRunEpisode.episode_id == episode_id,
            )
        ).first()
        if episode is None:
            raise HTTPException(404, "PRODUCTION_RUN_EPISODE_NOT_FOUND")
        if run.status in {"CANCELLED", "CANCEL_REQUESTED", "FAILED"}:
            raise HTTPException(409, f"PRODUCTION_RUN_{run.status}")
        assembling = db.scalars(
            select(DeliveryOrder).where(
                DeliveryOrder.scope_kind == "episode",
                DeliveryOrder.scope_id == episode.id,
                DeliveryOrder.status == "ASSEMBLING",
            )
        ).first()
        if assembling is not None:
            raise HTTPException(409, "EPISODE_ASSEMBLY_IN_PROGRESS")
        contract = normalized_post_contract(db, episode, body)
        previous = episode.post_contract
        episode.post_contract = contract
        db.flush()
        order_id = None
        if episode.status in {"DELIVERED", "DELIVERING", "PRODUCING", "PLANNED"}:
            order_id, _created = _ensure_episode_delivery(db, run, episode)
        _event(db, run, "EPISODE_POST_CONTRACT_REVISED",
               f"{episode.title or episode.episode_id} 更新剪辑/字幕合同；只重装配，不重拍",
               {"episode_id": episode.episode_id, "order_id": order_id})
        from shine.services import add_audit

        add_audit(db, actor, "EPISODE_POST_CONTRACT_REVISED", "production_run_episode", episode.id,
                  {"previous": previous, "contract": contract, "order_id": order_id},
                  project_id=run.project_id)
        return {"episode_id": episode.episode_id, "post_contract": contract,
                "delivery_order_id": order_id, "status": episode.status}


def _undelivered(episode: ProductionRunEpisode) -> bool:
    """An episode that never published a reel and is not a recorded blocker.

    A revision of an already-delivered reel that is being re-assembled still
    has its published version, so it does not reopen a completed run.
    """
    return episode.status not in {"DELIVERED", "BLOCKED"} and not episode.delivered_artifact_id


def reconcile_run(session_factory, run_id: str, *, worker_id: str = "user") -> dict[str, Any]:
    """Re-derive the run from its series/orders without creating new work."""
    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return {"status": "GONE"}
        synced = 0
        for batch in _batch_rows(db, run.id):
            if _sync_batch_from_series(db, batch):
                synced += 1
        for episode in _episode_rows(db, run.id):
            if episode.status == "BLOCKED":
                pass  # handled by _unblock_resolved_episodes below
        synced += _unblock_resolved_episodes(db, run)
        for episode in _episode_rows(db, run.id):
            if _sync_episode_delivery(db, run, episode):
                synced += 1

        episodes = _episode_rows(db, run.id)
        batches = _batch_rows(db, run.id)
        if run.status == "NEEDS_REVIEW" and not any(
                episode.status == "BLOCKED" for episode in episodes) and not any(
                batch.status == "BLOCKED" for batch in batches):
            _control(run, "QUEUED", None)
            _event(db, run, "RUN_UNBLOCKED", "阻塞已解除，运行回到可推进状态")
        elif run.status == "COMPLETED" and any(_undelivered(episode) for episode in episodes):
            # A completion merged before its episodes were decided is wrong, not
            # history: correct it explicitly and keep the event trail.
            _control(run, "NEEDS_REVIEW", "COMPLETION_CORRECTED")
            _event(db, run, "RUN_COMPLETION_CORRECTED",
                   "存在未交付的必需集，此前的完成结论被纠正为待复核")
            from shine.services import add_audit

            add_audit(db, worker_id, "PRODUCTION_RUN_COMPLETION_CORRECTED", "production_run", run.id,
                      {"undelivered_episodes": [
                          episode.episode_id for episode in episodes if _undelivered(episode)]},
                      project_id=run.project_id)
        run.counters = {
            **dict(run.counters or {}),
            "reconciled_at": utcnow().isoformat() + "Z",
        }
        run.state_version += 1
        _event(db, run, "RUN_RECONCILED", f"核对完成，更新 {synced} 项", {"changed": synced})
        db.flush()
        return {"status": run.status, "changed": synced}


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def _progress(run: ProductionRun, episodes: list[ProductionRunEpisode], batches: list[ProductionRunBatch],
              passed_shots: int) -> dict[str, Any]:
    required = len(episodes)
    delivered = len([episode for episode in episodes if episode.status == "DELIVERED"])
    return {
        "required_episode_count": required,
        "delivered_episode_count": delivered,
        "producing_episode_count": len([e for e in episodes if e.status in {"PRODUCING", "DELIVERING"}]),
        "blocked_episode_count": len([e for e in episodes if e.status == "BLOCKED"]),
        "batch_count": len(batches),
        "passed_batch_count": len([b for b in batches if b.status == "PASSED"]),
        "delivered_seconds": round(sum(e.actual_seconds or 0.0 for e in episodes if e.status == "DELIVERED"), 3),
        "passed_shot_count": passed_shots,
    }


def episode_public(
    db: Session, run: ProductionRun, episodes: list[ProductionRunEpisode]
) -> list[dict[str, Any]]:
    """Public shape of a bounded set of episodes, with their batches."""
    batches = _batch_rows(db, run.id) if episodes else []
    passed = run_owned_passed_jobs(db, run.id, episodes)
    return [
        {
            "id": episode.id,
            "episode_id": episode.episode_id,
            "position": episode.position,
            "title": episode.title,
            "status": episode.status,
            "shot_count": len(episode.shot_ids),
            "shot_ids": list(episode.shot_ids),
            "post_contract": episode.post_contract,
            "passed_shot_count": len([shot_id for shot_id in episode.shot_ids if shot_id in passed]),
            "planned_seconds": episode.planned_seconds,
            "actual_seconds": episode.actual_seconds,
            "delivery_order_id": episode.delivery_order_id,
            "delivered_artifact_id": episode.delivered_artifact_id,
            "delivered_sha256": episode.delivered_sha256,
            "blocker_code": episode.blocker_code,
            "delivered_at": episode.delivered_at.isoformat() + "Z" if episode.delivered_at else None,
            "batches": [
                {
                    "batch_key": batch.batch_key,
                    "status": _batch_public_status(db, batch),
                    "series_id": batch.series_id,
                    "shot_count": len(batch.shot_ids),
                    "failure_code": batch.failure_code,
                    "dispatched_at": batch.dispatched_at.isoformat() + "Z" if batch.dispatched_at else None,
                }
                for batch in batches if batch.episode_id == episode.episode_id
            ],
        }
        for episode in episodes
    ]


def _batch_public_status(db: Session, batch: ProductionRunBatch) -> str:
    """Expose the current series state even before a reconciliation beat.

    A requeued series may still have a historical RUNNING batch row until the
    next worker reconciliation. The read model must not present queued work as
    concurrently shooting while the single resident worker is busy elsewhere.
    """
    if not batch.series_id:
        return batch.status
    series = db.get(ProductionSeries, batch.series_id)
    if series is None:
        return batch.status
    if series.status == "QUEUED":
        return "QUEUED"
    if series.status in {"RUNNING", "PAUSE_REQUESTED"}:
        return "RUNNING"
    if series.status == "COMPLETED":
        return "PASSED"
    if series.status in {"FAILED", "CANCELLED", "NEEDS_REVIEW"}:
        return "BLOCKED"
    return batch.status


def run_public(db: Session, run: ProductionRun, *, include_episodes: bool = True) -> dict[str, Any]:
    episodes = _episode_rows(db, run.id)
    batches = _batch_rows(db, run.id)
    passed = run_owned_passed_jobs(db, run.id, episodes)
    payload: dict[str, Any] = {
        "id": run.id,
        "project_id": run.project_id,
        "package_id": run.package_id,
        "package_revision": run.package_revision,
        "manifest_sha256": run.manifest_sha256,
        "scope_digest": run.scope_digest,
        "status": run.status,
        "state_version": run.state_version,
        "control_reason": run.control_reason,
        "blocker_count": run.blocker_count,
        "scope": run.scope,
        "counters": run.counters,
        "progress": _progress(run, episodes, batches, len(passed)),
        "created_at": run.created_at.isoformat() + "Z",
        "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
        "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
        "last_progress_at": run.last_progress_at.isoformat() + "Z" if run.last_progress_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() + "Z" if run.heartbeat_at else None,
    }
    if include_episodes:
        payload["episodes"] = episode_public(db, run, episodes)
    return payload


def run_summary(db: Session, project_id: str, *, run_id: str | None = None) -> dict[str, Any] | None:
    """One run of the project, in the small shape the journey needs.

    A journey that names its scope passes the run id it resolved; without one
    the newest run is used for a project that has no package at all.
    """
    if run_id:
        run = db.get(ProductionRun, run_id)
        if run is None or run.project_id != project_id:
            return None
    else:
        run = db.scalars(
            select(ProductionRun)
            .where(ProductionRun.project_id == project_id)
            .order_by(ProductionRun.created_at.desc())
        ).first()
    if run is None:
        return None
    episodes = _episode_rows(db, run.id)
    delivered = [episode for episode in episodes if episode.status == "DELIVERED"]
    blocked = [
        {"code": episode.blocker_code or "EPISODE_BLOCKED",
         "message": f"{episode.title or episode.episode_id} 未交付"}
        for episode in episodes if episode.status == "BLOCKED"
    ]
    return {
        "id": run.id,
        "status": run.status,
        "required_episode_count": len(episodes),
        "delivered_episode_count": len(delivered),
        "blockers": blocked,
        "control_reason": run.control_reason,
        "last_progress_at": run.last_progress_at.isoformat() + "Z" if run.last_progress_at else None,
    }


def run_events(db: Session, run_id: str, *, limit: int = 50, after_id: int = 0) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ProductionRunEvent)
        .where(ProductionRunEvent.run_id == run_id, ProductionRunEvent.id > after_id)
        .order_by(ProductionRunEvent.id.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "message": row.message,
            "payload": row.payload,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in reversed(rows)
    ]

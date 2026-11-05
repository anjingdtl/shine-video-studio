"""The whole-film run: planning, dispatch, progress and honest completion.

One resident Worker advances one run in a short transaction.  The run never
submits a render itself: it decides which *batch* may become claimable, creates
exactly one ``ProductionSeries`` for it, and then reads back what that series
really produced.  Every step is idempotent, so a crash between "series created"
and "batch remembered" cannot queue the same shots twice.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shine.director.contracts import digest_of
from shine.director.models import DirectorPackageEpisode, DirectorPackageVersion
from shine.director.package import (
    episode_manifest,
    package_episodes,
    package_readiness,
)
from shine.models import (
    Job,
    MediaArtifact,
    ProductionSeries,
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


class RunBlocked(Exception):
    """A definite refusal to dispatch a batch; recorded, never blindly retried."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code or detail)


# ---------------------------------------------------------------------------
# Identity and creation
# ---------------------------------------------------------------------------


def _batch_plan(episode: DirectorPackageEpisode, *, batch_shot_limit: int) -> list[dict[str, Any]]:
    """Split one episode's ordered shots into dispatchable batches.

    A batch boundary may only fall before a shot that does not continue the
    previous one: a dependent shot has to live in the same series as its source,
    otherwise the series would have to re-derive a continuity it cannot see.
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
        if next_shot.get("continuity_mode") != "cut":
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
            ProductionSeries.status.in_(["QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED"]),
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
    elif series.status in {"FAILED", "CANCELLED"}:
        if batch.status != "BLOCKED":
            batch.status = "BLOCKED"
            batch.failure_code = series.failure_code or f"SERIES_{series.status}"
            changed = True
    elif series.status in {"RUNNING", "PAUSE_REQUESTED", "PAUSED"}:
        if batch.status != "RUNNING":
            batch.status = "RUNNING"
            changed = True
    return changed


def _current_passed_jobs(db: Session, shot_ids: list[str]) -> dict[str, Job]:
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
    from shine.schemas import CreateJob, GenerationMode, OutputSpec, RenderProvider

    package = db.get(DirectorPackageVersion, run.package_id)
    manifest = package.manifest if package else {}
    binding = manifest.get("binding") or {}
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
    )
    try:
        series, _job = create_series(db, project, in_order, body=body, settings=settings, offline=False)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("code", "")
        raise RunBlocked("BATCH_DISPATCH_REFUSED", str(detail)[:200]) from None
    return series


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
    limit = int((package.manifest.get("policy") or {}).get("batch_shot_limit", DEFAULT_BATCH_SHOT_LIMIT) or
                DEFAULT_BATCH_SHOT_LIMIT)
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
        planned = _batch_plan(source_episode, batch_shot_limit=limit)
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


def _ensure_episode_delivery(db: Session, run: ProductionRun, episode: ProductionRunEpisode) -> str | None:
    """Create the per-episode delivery order once all its batches really passed."""
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.episode_delivery import episode_input_digest

    batches = [b for b in _batch_rows(db, run.id) if b.episode_id == episode.episode_id]
    if not batches or any(batch.status != "PASSED" for batch in batches):
        return None
    passed = _current_passed_jobs(db, episode.shot_ids)
    missing = [shot_id for shot_id in episode.shot_ids if shot_id not in passed]
    if missing:
        return None
    digest = episode_input_digest(db, run, episode, batches)
    existing = db.scalars(
        select(DeliveryOrder).where(
            DeliveryOrder.scope_kind == "episode",
            DeliveryOrder.scope_id == episode.id,
        )
    ).all()
    for order in existing:
        if order.status in {"PENDING", "ASSEMBLING"}:
            episode.delivery_order_id = order.id
            return order.id
        if order.status == "DELIVERED" and order.input_digest == digest:
            episode.delivery_order_id = order.id
            return order.id
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
    )
    db.add(order)
    db.flush()
    episode.delivery_order_id = order.id
    episode.status = "DELIVERING"
    _event(db, run, "EPISODE_DELIVERY_QUEUED", f"{episode.title or episode.episode_id} 开始逐集装配",
           {"episode_id": episode.episode_id, "order_id": order.id})
    return order.id


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


def advance_run(
    db: Session,
    run: ProductionRun,
    *,
    worker_id: str,
    settings,
) -> dict[str, Any]:
    """One bounded advance step.  Safe to call repeatedly and concurrently."""
    if run.status not in {"QUEUED", "RUNNING"}:
        return {"run_id": run.id, "status": run.status, "action": "skipped"}

    budget = run.budget or {}
    if run.started_at and budget.get("max_wall_seconds"):
        elapsed = (utcnow() - run.started_at).total_seconds()
        if elapsed > float(budget["max_wall_seconds"]):
            _control(run, "PAUSED", "BUDGET_EXHAUSTED")
            _event(db, run, "RUN_PAUSED", "生产预算已用尽，等待人工确认是否继续")
            db.flush()
            return {"run_id": run.id, "status": run.status, "action": "paused"}

    min_free = float(budget.get("min_free_gb") or 0)
    if min_free:
        from shine.comfyops.storage import disk_info

        try:
            free_gb = float(disk_info(settings.data_dir).get("free_gb") or 0)
        except OSError:
            free_gb = None
        if free_gb is not None and free_gb < min_free:
            _control(run, "PAUSED", "STORAGE_LOW")
            _event(db, run, "RUN_PAUSED", f"磁盘剩余 {free_gb:.1f} GB 低于预留，暂停新提交")
            db.flush()
            return {"run_id": run.id, "status": run.status, "action": "paused"}

    if not _claim_run(db, run, worker_id):
        return {"run_id": run.id, "status": run.status, "action": "claimed_elsewhere"}

    actions: list[str] = []
    for batch in _batch_rows(db, run.id):
        if _sync_batch_from_series(db, batch):
            actions.append("batch_sync")

    # A batch that its own series could not finish blocks *its* episode, and
    # only that episode: every other episode keeps running.  A blocked batch is
    # never retried automatically — the operator decides.
    for batch in _batch_rows(db, run.id):
        if batch.status != "BLOCKED":
            continue
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        if episode is not None and episode.status not in {"DELIVERED", "BLOCKED"}:
            episode.status = "BLOCKED"
            episode.blocker_code = batch.failure_code or "BATCH_BLOCKED"
            actions.append("episode_blocked")

    episodes = _episode_rows(db, run.id)
    for episode in episodes:
        if _sync_episode_delivery(db, run, episode):
            actions.append("episode_delivery")

    for episode in episodes:
        if episode.status in {"PLANNED", "PRODUCING", "DELIVERING"}:
            if _ensure_episode_delivery(db, run, episode):
                actions.append("episode_queued")

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
    run.last_progress_at = utcnow()

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
    run.state_version += 1
    db.flush()
    return {"run_id": run.id, "status": run.status, "action": "advanced", "changes": sorted(set(actions))}


def advance_runs(session_factory, *, worker_id: str, settings, limit: int = 1) -> int:
    """Drive at most ``limit`` runnable runs in one scheduler beat."""
    with session_factory() as db:
        candidates = db.execute(
            select(ProductionRun.id)
            .where(ProductionRun.status.in_(["QUEUED", "RUNNING"]))
            .order_by(ProductionRun.created_at)
            .limit(max(1, limit))
        ).all()
    advanced = 0
    for (run_id,) in candidates:
        try:
            with session_factory.begin() as db:
                run = db.get(ProductionRun, run_id)
                if run is None:
                    continue
                result = advance_run(db, run, worker_id=worker_id, settings=settings)
            if result.get("action") in {"advanced", "paused"}:
                advanced += 1
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the loop
            logger.warning("production_run_advance_failed run_id=%s error=%s", run_id, type(exc).__name__)
    return advanced


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
        if run.status == "PAUSED":
            return run.status
        _control(run, "PAUSED", "USER_PAUSED")
        _event(db, run, "RUN_PAUSED", "用户暂停：不再安排新的片段，当前片段等待安全落盘")
        db.flush()
        return run.status


def resume_run(session_factory, run_id: str, *, expected_version: int | None = None) -> str:
    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return "GONE"
        if expected_version is not None and run.state_version != expected_version:
            return "STATE_VERSION_CONFLICT"
        if run.status != "PAUSED":
            return run.status
        _control(run, "RUNNING", None)
        _event(db, run, "RUN_RESUMED", "用户恢复生产")
        db.flush()
        return run.status


def cancel_run(session_factory, run_id: str, *, actor: str = "user", expected_version: int | None = None) -> str:
    """Cancel without deleting anything and without touching foreign resources."""
    from shine.comfyops.production_series import cancel_series_in_session
    from shine.media.delivery_models import DeliveryOrder

    with session_factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        if run is None:
            return "GONE"
        if expected_version is not None and run.state_version != expected_version:
            return "STATE_VERSION_CONFLICT"
        if run.status in {"CANCELLED", "COMPLETED"}:
            return run.status
        for batch in _batch_rows(db, run.id):
            if batch.series_id:
                cancel_series_in_session(db, batch.series_id, reason="RUN_CANCELLED")
        for episode in _episode_rows(db, run.id):
            if not episode.delivery_order_id:
                continue
            order = db.get(DeliveryOrder, episode.delivery_order_id)
            if order is not None and order.status == "PENDING":
                order.status = "CANCELLED"
                order.failure_code = "RUN_CANCELLED"
                order.state_version += 1
        _control(run, "CANCELLED", "USER_CANCELLED")
        run.finished_at = utcnow()
        _event(db, run, "RUN_CANCELLED", "用户取消：已产出数据全部保留")
        db.flush()
        from shine.services import add_audit

        add_audit(db, actor, "PRODUCTION_RUN_CANCELLED", "production_run", run.id,
                  {"delivered_episodes": run.counters.get("delivered_episode_count", 0)},
                  project_id=run.project_id)
        return run.status


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
            if _sync_episode_delivery(db, run, episode):
                synced += 1
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
    shot_ids = [shot_id for episode in episodes for shot_id in episode.shot_ids]
    passed = _current_passed_jobs(db, shot_ids)
    return [
        {
            "id": episode.id,
            "episode_id": episode.episode_id,
            "position": episode.position,
            "title": episode.title,
            "status": episode.status,
            "shot_count": len(episode.shot_ids),
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
                    "status": batch.status,
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


def run_public(db: Session, run: ProductionRun, *, include_episodes: bool = True) -> dict[str, Any]:
    episodes = _episode_rows(db, run.id)
    batches = _batch_rows(db, run.id)
    all_shots = [shot_id for episode in episodes for shot_id in episode.shot_ids]
    passed = _current_passed_jobs(db, all_shots)
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


def run_summary(db: Session, project_id: str) -> dict[str, Any] | None:
    """The newest run of the project, in the small shape the journey needs."""
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

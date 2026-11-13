"""The Worker's side of a persistent installation attempt.

The resident Worker claims a queued installation attempt, installs it behind
the same pinned digests as before, writes its stage and its confirmed bytes,
and stops at safe points when the operator pauses or cancels.

The Worker never invents an outcome: a network failure is retryable, and an
uncertain filesystem result is reconciled by a human decision instead of being
replayed.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installation import (
    CANCELLED,
    DOWNLOADING,
    FAILED_RETRYABLE,
    FILES_VERIFIED_PENDING_RUNTIME,
    MOCK_VERIFIED,
    NEEDS_REVIEW,
    PAUSED,
    QUEUED,
    ProgressRecorder,
    _update,
    claim_next_attempt,
    local_execution_enabled,
    recover_expired_attempts,
)
from shine.comfyops.models import InstallationAttempt, InstallationPlan, ModelPackage, NodePackage
from shine.services import add_audit

logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 30.0


def _read_control(factory, attempt_id: str) -> dict:
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        if attempt is None:
            return {"cancel_requested": False, "pause_requested": False}
        return {
            "cancel_requested": bool(attempt.cancel_requested),
            "pause_requested": bool(attempt.pause_requested),
        }


def _finish(factory, attempt_id: str, worker_id: str, *, stage, status, error_code=None,
            error_message=None, extra=None) -> None:
    fields = {
        "stage": stage,
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    if extra:
        fields.update(extra)
    _update(factory, attempt_id, worker_id, **fields)


def _build_installer(settings, *, progress, should_stop):
    from shine.comfyops.installer import (
        TRUSTED_DOWNLOAD_HOSTS,
        ControlledDownloader,
        HTTPTransport,
        LocalInstaller,
    )

    downloader = ControlledDownloader(TRUSTED_DOWNLOAD_HOSTS, HTTPTransport())
    return LocalInstaller(
        settings.comfyops_dir / "isolated-packages",
        downloader,
        progress=progress,
        should_stop=should_stop,
    )


def run_pending_installation(factory, worker_id: str, *, settings) -> str | None:
    """One Worker tick for setup-type installation attempts.

    It returns the stage it reached, or None when nothing was claimable.
    """
    recover_expired_attempts(factory, worker_id)
    attempt_id = claim_next_attempt(factory, worker_id)
    if attempt_id is None:
        return None
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        executor = (attempt.payload or {}).get("executor") if attempt else None
    if executor == "LOCAL_ISOLATED" and settings.offline_simulation:
        # An offline simulation never downloads real bytes.  The attempt stays
        # queued and keeps its confirmed bytes.
        _update(
            factory, attempt_id, worker_id,
            stage=QUEUED, status=QUEUED, lease_owner=None, lease_expires_at=None,
        )
        return None
    logger.info("installation_attempt_claimed attempt_id=%s worker_id=%s", attempt_id, worker_id)
    return run_attempt(factory, worker_id, attempt_id, settings=settings)


def run_attempt(factory, worker_id: str, attempt_id: str, *, settings, installer=None) -> str:
    """Keep the existing attempt lease alive while slow file operations run."""
    from shine.comfyops.installation import renew_lease

    stop_heartbeat = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                if not renew_lease(factory, attempt_id, worker_id):
                    lost.set()
                    return
            except Exception:  # noqa: BLE001 - transient database locks must not invent lease loss
                logger.warning("installation heartbeat deferred attempt_id=%s", attempt_id)

    thread = threading.Thread(target=heartbeat, name="shine-install-heartbeat", daemon=True)
    thread.start()
    try:
        return _run_attempt(factory, worker_id, attempt_id, settings=settings, installer=installer,
                            ownership_lost=lost)
    finally:
        stop_heartbeat.set()
        thread.join(timeout=5)


def _run_attempt(factory, worker_id: str, attempt_id: str, *, settings, installer=None,
                 ownership_lost: threading.Event) -> str:
    """Run one claimed attempt to a definite stage."""
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        if attempt is None:
            return "missing"
        if attempt.lease_owner != worker_id:
            return NEEDS_REVIEW
        plan = db.get(InstallationPlan, attempt.plan_id)
        payload = dict(attempt.payload or {})
    if plan is None:
        _finish(
            factory, attempt_id, worker_id,
            stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
            error_code="INSTALL_PLAN_MISSING",
            error_message="The approved plan is gone; nothing may be installed from it.",
        )
        return NEEDS_REVIEW
    plan_hash = payload.get("plan_hash")
    if not plan_hash or plan.sha256 != plan_hash or graph_hash(plan.payload) != plan_hash:
        # The approval hash changed: the old attempt may not execute new content.
        _finish(
            factory, attempt_id, worker_id,
            stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
            error_code="PLAN_HASH_CHANGED",
            error_message="The approved plan changed after the attempt was queued.",
        )
        return NEEDS_REVIEW
    executor = payload.get("executor") or plan.payload.get("executor")
    if executor == "LOCAL_ISOLATED":
        from shine.comfyops.resource_resolver import reject_unbound_candidate_checkpoint, validate_resource_plan

        try:
            reject_unbound_candidate_checkpoint(plan.payload)
            if plan.payload.get("resource_context") is not None:
                with factory() as db:
                    validate_resource_plan(db, settings, plan.payload)
        except Exception as exc:  # noqa: BLE001 - fail closed on unreadable or changed evidence
            logger.warning("resource plan revalidation failed: %s", type(exc).__name__)
            code = "RESOURCE_DOWNLOAD_CHANGED" if str(exc) == "RESOURCE_DOWNLOAD_CHANGED" else (
                "RESOURCE_PLAN_REVALIDATION_FAILED")
            _finish(factory, attempt_id, worker_id, stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
                    error_code=code,
                    error_message="Resource identity could not be revalidated; no download was started.")
            return NEEDS_REVIEW
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        current_attempt = db.get(InstallationAttempt, attempt_id)
        if not current_attempt or current_attempt.lease_owner != worker_id or ownership_lost.is_set():
            return NEEDS_REVIEW
        plan_row = db.get(InstallationPlan, plan.id)
        if plan_row is not None:
            plan_row.status = "INSTALLING"
    if executor == "FAKE_ONLY":
        return _run_mock_attempt(factory, worker_id, attempt_id, plan)
    if executor != "LOCAL_ISOLATED":
        _finish(
            factory, attempt_id, worker_id,
            stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
            error_code="LOCAL_PLAN_REQUIRED",
            error_message="This plan is not an approved local installation plan.",
        )
        return NEEDS_REVIEW
    if not local_execution_enabled():
        _finish(
            factory, attempt_id, worker_id,
            stage=FAILED_RETRYABLE, status=FAILED_RETRYABLE,
            error_code="LOCAL_EXECUTOR_UNAVAILABLE",
            error_message="Local execution is disabled on this host; enable it and resume the attempt.",
        )
        return FAILED_RETRYABLE
    if plan.payload.get("resource_context"):
        return _run_resource_attempt(factory, worker_id, attempt_id, plan, settings=settings, installer=installer,
                                     ownership_lost=ownership_lost)
    return _run_local_attempt(factory, worker_id, attempt_id, plan, settings=settings, installer=installer,
                              ownership_lost=ownership_lost)


def _run_resource_attempt(factory, worker_id: str, attempt_id: str, plan: InstallationPlan, *, settings,
                          installer=None, ownership_lost: threading.Event | None = None) -> str:
    """Keep the physical installation idle while packages are swapped."""
    from sqlalchemy import select

    from shine.comfyops.models import ComfyInstance
    from shine.comfyops.renderer_supervisor import windows_port_listeners
    from shine.comfyops.resource_resolver import validate_resource_plan
    from shine.models import Job
    from shine.runtime_orchestrator.guard import (
        LifecycleGuardBusy,
        LifecycleGuardUnavailable,
        renderer_lifecycle_guard,
    )
    from shine.runtime_orchestrator.identity import installation_activity
    from shine.services import UNKNOWN_SUBMISSION_CODES

    instance_id = plan.payload["resource_context"]["instance_id"]
    started = False
    try:
        with renderer_lifecycle_guard(instance_id):
            with factory() as db:
                validate_resource_plan(db, settings, plan.payload)
                activity = installation_activity(db, instance_id)
                instance = db.get(ComfyInstance, instance_id)
                port = (instance.payload or {}).get("port") if instance else None
                if not isinstance(port, int) or windows_port_listeners(port):
                    raise LifecycleGuardBusy("EXTERNAL_INSTANCE_ON_PORT")
                unknown = db.scalar(select(Job.id).where(
                    Job.status == "NEEDS_REVIEW", Job.failure_code.in_(sorted(UNKNOWN_SUBMISSION_CODES)),
                ).limit(1))
                if (unknown or activity["renderer_state"] != "NONE" or activity["gpu_leases"]
                        or activity["in_flight_h3_jobs"] or activity["in_flight_image_work"]):
                    raise LifecycleGuardBusy("RESOURCE_INSTALLATION_BUSY")
            if ownership_lost and ownership_lost.is_set():
                return NEEDS_REVIEW
            started = True
            return _run_local_attempt(factory, worker_id, attempt_id, plan, settings=settings, installer=installer,
                                      ownership_lost=ownership_lost)
    except (LifecycleGuardBusy, LifecycleGuardUnavailable) as exc:
        _finish(factory, attempt_id, worker_id, stage=FAILED_RETRYABLE, status=FAILED_RETRYABLE,
                error_code=("EXTERNAL_INSTANCE_ON_PORT" if str(exc) == "EXTERNAL_INSTANCE_ON_PORT"
                            else "RESOURCE_INSTALLATION_BUSY"), error_message=str(exc))
        return FAILED_RETRYABLE
    except Exception as exc:  # noqa: BLE001 - unknown ownership is never a maintenance window
        logger.warning("resource installation safety check failed: %s", type(exc).__name__)
        _finish(factory, attempt_id, worker_id, stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
                error_code="RESOURCE_PLAN_REVALIDATION_FAILED",
                error_message=("Installation was interrupted; reconcile its recorded state."
                               if started else "Resource or installation evidence changed; no download was started."))
        return NEEDS_REVIEW


def _run_mock_attempt(factory, worker_id: str, attempt_id: str, plan: InstallationPlan) -> str:
    """The mock drill changes nothing on disk and claims nothing real."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        attempt = db.get(InstallationAttempt, attempt_id)
        if not attempt or attempt.lease_owner != worker_id:
            return NEEDS_REVIEW
        for package in plan.payload.get("packages", []):
            cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
            row = db.get(cls, package["id"])
            if row is None or row.sha256 != package["hash"]:
                raise ValueError("PLAN_HASH_CHANGED")
            row.status = "MOCK_AVAILABLE"
        plan_row = db.get(InstallationPlan, plan.id)
        plan_row.status = "MOCK_AVAILABLE"
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.payload = {**(attempt.payload or {}), "is_mock": True, "system_changes": False}
        attempt.sha256 = graph_hash(attempt.payload)
        add_audit(
            db, worker_id, "INSTALL_FAKE_EXECUTED", "installation_plan", plan.id,
            {"attempt_id": attempt_id, "is_mock": True},
            project_id=plan.project_id,
        )
    _finish(
        factory, attempt_id, worker_id,
        stage=MOCK_VERIFIED, status=MOCK_VERIFIED,
        error_code=None, error_message=None,
    )
    return MOCK_VERIFIED


def _resource_transfer_authorizer(context: dict):
    """Permit only files explicitly frozen as missing in the approved plan."""
    authorized = {(item["sha256"], item["size"]) for item in context["authorized_download_files"]}

    def authorize(file):
        if (file.sha256, file.size) not in authorized:
            raise ValueError("RESOURCE_DOWNLOAD_CHANGED")

    return authorize


def _run_local_attempt(factory, worker_id: str, attempt_id: str, plan: InstallationPlan, *, settings,
                       installer=None, ownership_lost: threading.Event | None = None) -> str:
    """Install every pinned package of the plan behind the pinned digests."""
    from shine.comfyops.installer import InstallationInterrupted

    packages = list(plan.payload.get("packages", []))
    recorder = ProgressRecorder(factory, attempt_id, worker_id)
    progress_files: dict[tuple[str, str], int] = {}
    stop = {"reason": None}

    def should_stop():
        if ownership_lost and ownership_lost.is_set():
            return "INSTALL_LEASE_LOST"
        if stop["reason"]:
            return stop["reason"]
        control = _read_control(factory, attempt_id)
        if control["cancel_requested"]:
            stop["reason"] = CANCELLED
        elif control["pause_requested"]:
            stop["reason"] = PAUSED
        return stop["reason"]

    def progress(component, relative_path, confirmed, total):
        key = (component, relative_path)
        if key in progress_files:
            progress_files[key] = min(int(confirmed or 0), int(total or 0))
        recorder.report(component=component, file=relative_path, confirmed=sum(progress_files.values()),
                        total=plan.payload.get("download_bytes"))

    if installer is None:
        installer = _build_installer(settings, progress=progress, should_stop=should_stop)
        context = plan.payload.get("resource_context")
        if context:
            installer.downloader.source_preference = context["source_preference"]
            installer.downloader.probe_sources = context["source_preference"] == "AUTO"
            installer.downloader.before_transfer = _resource_transfer_authorizer(context)
    _update(factory, attempt_id, worker_id, stage=DOWNLOADING, status=DOWNLOADING,
            current_component=None, current_file=None)
    recorder.report(component=None, confirmed=0, total=plan.payload.get("download_bytes"), force=True)
    try:
        for package in packages:
            from shine.comfyops.installer import PackageSpec

            spec = PackageSpec.model_validate(package["spec"])
            reason = should_stop()
            if reason:
                raise InstallationInterrupted(reason)
            from shine.comfyops.verification_receipts import cache_candidates

            cache = settings.comfyops_dir / "isolated-packages" / ".cache"
            entries = [item.model_dump() for item in spec.files]
            checked = cache_candidates(cache, {"files": entries})["files"]
            missing = [item for item, evidence in zip(spec.files, checked, strict=True)
                       if not evidence.get("verified")]
            for item in missing:
                key = (spec.name, item.relative_path)
                partial = cache / (item.sha256 + ".part")
                try:
                    partial_size = partial.stat().st_size
                    progress_files[key] = partial_size if partial_size < item.size else 0
                except OSError:
                    progress_files[key] = 0
            _update(factory, attempt_id, worker_id, stage=DOWNLOADING, status=DOWNLOADING,
                    current_component=spec.name)
            recorder.report(component=spec.name, confirmed=sum(progress_files.values()),
                            total=plan.payload.get("download_bytes"), force=True)
            result = installer.install(spec)
            for item in missing:
                progress_files[(spec.name, item.relative_path)] = item.size
            recorder.report(component=spec.name, confirmed=sum(progress_files.values()),
                            total=plan.payload.get("download_bytes"), force=True)
            logger.info("installation_package_installed attempt_id=%s package=%s status=%s",
                        attempt_id, spec.name, result.get("status"))
    except InstallationInterrupted as exc:
        reason = str(exc)
        if reason == "INSTALL_LEASE_LOST":
            return NEEDS_REVIEW  # a stale owner never writes a new terminal state
        if reason == CANCELLED:
            _finish(
                factory, attempt_id, worker_id,
                stage=CANCELLED, status=CANCELLED,
                error_code="INSTALL_CANCELLED_BY_OPERATOR",
                error_message="Cancelled at a safe point; reusable partial files and verified models are kept.",
            )
            return CANCELLED
        _finish(
            factory, attempt_id, worker_id,
            stage=PAUSED, status=PAUSED,
            error_code="INSTALL_PAUSED_BY_OPERATOR",
            error_message="Paused at a safe point; the confirmed bytes stay reusable.",
        )
        return PAUSED
    except ValueError as exc:
        message = str(exc)
        code = message.split(":", 1)[0] if message else "INSTALL_FAILED"
        retryable = code in {
            "DOWNLOAD_CONNECTION_FAILED",
            "DOWNLOAD_STREAM_FAILED",
            "DOWNLOAD_HTTP_REJECTED",
            "DOWNLOAD_RESUME_RANGE",
            "INVALID_RESUME_RANGE",
            "DOWNLOAD_SEGMENT_SIZE_MISMATCH",
            "FILE_CHECKSUM_FAILED",
        }
        with factory.begin() as db:
            attempt = db.get(InstallationAttempt, attempt_id)
            if not attempt or attempt.lease_owner != worker_id:
                return NEEDS_REVIEW
            attempt.retry_count = int(attempt.retry_count or 0) + 1
        if retryable:
            _finish(
                factory, attempt_id, worker_id,
                stage=FAILED_RETRYABLE, status=FAILED_RETRYABLE,
                error_code=code, error_message=message[:500],
            )
            return FAILED_RETRYABLE
        _finish(
            factory, attempt_id, worker_id,
            stage=NEEDS_REVIEW, status="FAILED_RECONCILE_REQUIRED",
            error_code=code, error_message=message[:500],
        )
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            current_attempt = db.get(InstallationAttempt, attempt_id)
            plan_row = db.get(InstallationPlan, plan.id)
            if (plan_row is not None and current_attempt is not None
                    and current_attempt.stage == NEEDS_REVIEW and current_attempt.error_code == code
                    and current_attempt.lease_owner is None):
                # The existing reconciliation path only accepts a plan that is
                # installing or already needs review.
                plan_row.status = "NEEDS_REVIEW"
        return NEEDS_REVIEW
    # Every file of every package is on disk and hash-verified.  That is a
    # proven fact about the files, not a runtime claim.
    with factory.begin() as db:
        from shine.comfyops.verification_receipts import save_receipt

        db.execute(text("BEGIN IMMEDIATE"))
        attempt = db.get(InstallationAttempt, attempt_id)
        if not attempt or attempt.lease_owner != worker_id or (ownership_lost and ownership_lost.is_set()):
            return NEEDS_REVIEW
        plan_row = db.get(InstallationPlan, plan.id)
        variant = (plan.payload or {}).get("variant") or {}
        deployment = (plan.payload or {}).get("deployment") or {}
        install_root = settings.comfyops_dir / "isolated-packages"
        for package in packages:
            spec = package["spec"]
            cls = ModelPackage if spec["kind"] == "model" else NodePackage
            row = db.get(cls, package["id"])
            if row is not None:
                row.status = "FILES_VERIFIED_PENDING_RUNTIME"
            # The status is a summary; the receipt is the evidence.  It binds
            # the exact revision, every file length/SHA and the directory that
            # really holds them, so a later readiness check never turns green
            # from an old row or a same-sized wrong byte (J04).
            save_receipt(
                db,
                variant_id=variant.get("variant_id"),
                catalog_id=deployment.get("catalog_id"),
                catalog_version=deployment.get("catalog_version"),
                component={
                    "component_id": spec["name"],
                    "revision": spec.get("revision"),
                    "files": spec.get("files") or [],
                },
                root=install_root,
                directory=install_root / spec["name"],
            )
        plan_row = db.get(InstallationPlan, plan.id)
        plan_row.status = "FILES_VERIFIED_PENDING_RUNTIME"
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.payload = {**(attempt.payload or {}), "runtime_verified": False}
        attempt.sha256 = graph_hash(attempt.payload)
        add_audit(
            db, worker_id, "LOCAL_PACKAGE_FILES_VERIFIED", "installation_plan", plan.id,
            {"attempt_id": attempt_id, "runtime_verified": False},
            project_id=plan.project_id,
        )
    _finish(
        factory, attempt_id, worker_id,
        stage=FILES_VERIFIED_PENDING_RUNTIME, status=FILES_VERIFIED_PENDING_RUNTIME,
        error_code=None, error_message=None,
    )
    return FILES_VERIFIED_PENDING_RUNTIME

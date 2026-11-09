"""A persistent installation attempt: an asynchronous, re-attachable install task.

The repair plan (docs/NEW_USER_JOURNEY_REPAIR_PLAN_20261107.md, phase B) takes
the installation out of one long HTTP request:

* the API only *queues* an attempt and answers 202 with its id;
* the resident Worker claims the attempt, writes its stage, its confirmed
  bytes and its heartbeat, and stops at safe points for pause and cancel;
* the browser re-attaches by attempt id after a refresh, a restart or a
  reinstall, and never has to keep one HTTP request open.

Three rules never bend:

* confirmed bytes are written from the ".part" file that is really on disk,
  never from a client-side estimate;
* only an explicit cancel asks the server to stop; closing the browser stops
  the observation, not the installation;
* an uncertain outcome (a directory swap, a subprocess, an uncommitted result)
  is reconciled, never replayed.  Only a provable download stage may resume
  automatically, and only when plan id, plan hash and attempt id still agree.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import InstallationAttempt, InstallationPlan
from shine.models import utcnow
from shine.services import add_audit

logger = logging.getLogger(__name__)

# --- the honest stage vocabulary -------------------------------------------
QUEUED = "QUEUED"
DOWNLOADING = "DOWNLOADING"
RETRY_WAIT = "RETRY_WAIT"
VERIFYING = "VERIFYING"
INSTALLING = "INSTALLING"
FILES_VERIFIED_PENDING_RUNTIME = "FILES_VERIFIED_PENDING_RUNTIME"
VALIDATING_RUNTIME_CONTRACT = "VALIDATING_RUNTIME_CONTRACT"
RUNTIME_CONTRACT_READY = "RUNTIME_CONTRACT_READY"
PAUSED = "PAUSED"
CANCELLED = "CANCELLED"
FAILED_RETRYABLE = "FAILED_RETRYABLE"
NEEDS_REVIEW = "NEEDS_REVIEW"
#: The mock drill never changes a byte on disk and never claims a real install.
MOCK_VERIFIED = "MOCK_VERIFIED"

STAGES = (
    QUEUED,
    DOWNLOADING,
    RETRY_WAIT,
    VERIFYING,
    INSTALLING,
    FILES_VERIFIED_PENDING_RUNTIME,
    VALIDATING_RUNTIME_CONTRACT,
    RUNTIME_CONTRACT_READY,
    PAUSED,
    CANCELLED,
    FAILED_RETRYABLE,
    NEEDS_REVIEW,
    MOCK_VERIFIED,
)

#: A stage that only moves bytes on disk.  After a dead Worker the attempt may
#: continue from the confirmed bytes; the pinned digest still decides whether
#: the finished file is acceptable.
RESUMABLE_STAGES = frozenset({QUEUED, DOWNLOADING, RETRY_WAIT, VERIFYING})
#: A stage whose external outcome may be unknown.  It is reconciled by a human
#: decision, never replayed automatically.
UNCERTAIN_STAGES = frozenset({INSTALLING, VALIDATING_RUNTIME_CONTRACT})
TERMINAL_STAGES = frozenset(
    {
        CANCELLED,
        NEEDS_REVIEW,
        FILES_VERIFIED_PENDING_RUNTIME,
        RUNTIME_CONTRACT_READY,
        MOCK_VERIFIED,
    }
)
ACTIVE_STAGES = frozenset(
    {
        QUEUED,
        DOWNLOADING,
        RETRY_WAIT,
        VERIFYING,
        INSTALLING,
        VALIDATING_RUNTIME_CONTRACT,
        PAUSED,
        FAILED_RETRYABLE,
    }
)

#: How long a Worker claim stays valid without a renewal.
LEASE_SECONDS = 300


def normalized_storage_root(value: str | None) -> str:
    """One physical directory has exactly one identity on this host."""
    import os as _os

    if not value:
        return ""
    return _os.path.normcase(_os.path.realpath(_os.path.abspath(value)))


def plan_target_key(plan) -> str:
    """The physical work a plan owns: the directory it writes and the bytes.

    Two plans in two browser tabs, or in two projects, that would write the
    same files into the same root share this key.  The key is what makes the
    second one a re-attach or a conflict instead of a second download.
    """
    payload = plan.payload or {}
    pinned = payload.get("target_key")
    if pinned:
        return pinned
    from shine.comfyops.deployment import target_identity

    return target_identity(
        payload.get("storage_root"), payload.get("executor"), payload.get("packages", [])
    )


def active_attempts(db):
    """Every live attempt, with the plan it belongs to."""
    from shine.comfyops.models import InstallationPlan

    attempts = db.scalars(
        select(InstallationAttempt).where(
            or_(
                InstallationAttempt.stage.in_(sorted(ACTIVE_STAGES)),
                and_(
                    InstallationAttempt.stage.is_(None),
                    InstallationAttempt.status == "SUBMIT_INTENT",
                ),
            )
        )
    ).all()
    rows = []
    for attempt in attempts:
        plan = db.get(InstallationPlan, attempt.plan_id)
        if plan is not None:
            rows.append((attempt, plan))
    return rows


def _iso(value):
    return value.isoformat() if value else None


def local_execution_enabled() -> bool:
    """The local executor stays opt-in on the authorized Windows host."""
    return os.name == "nt" and os.getenv("SHINE_ENABLE_LOCAL_EXECUTION") == "1"


def attempt_view(attempt: InstallationAttempt) -> dict:
    """The re-attachable truth about one installation attempt."""
    payload = attempt.payload or {}
    stage = attempt.stage or attempt.status
    return {
        "attempt_id": attempt.id,
        "plan_id": attempt.plan_id,
        "plan_hash": payload.get("plan_hash"),
        "executor": payload.get("executor"),
        "stage": stage,
        "status": attempt.status,
        "current_component": attempt.current_component,
        "current_file": attempt.current_file,
        "confirmed_bytes": int(attempt.confirmed_bytes or 0),
        "total_bytes": int(attempt.total_bytes or 0),
        "speed_bps": int(attempt.speed_bps or 0),
        "retry_count": int(attempt.retry_count or 0),
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "heartbeat_at": _iso(attempt.heartbeat_at),
        "lease_owner": attempt.lease_owner,
        "lease_expires_at": _iso(attempt.lease_expires_at),
        "cancel_requested": bool(attempt.cancel_requested),
        "pause_requested": bool(attempt.pause_requested),
        "terminal": stage in TERMINAL_STAGES,
        "resumable": stage in RESUMABLE_STAGES,
        "created_at": _iso(attempt.created_at),
    }


def active_attempt_query(plan_id: str):
    """The query that finds an attempt that still owns its plan."""
    return select(InstallationAttempt.id).where(
        InstallationAttempt.plan_id == plan_id,
        or_(
            InstallationAttempt.stage.in_(sorted(ACTIVE_STAGES)),
            and_(
                InstallationAttempt.stage.is_(None),
                InstallationAttempt.status == "SUBMIT_INTENT",
            ),
        ),
    )


def queue_attempt(db, plan: InstallationPlan, plan_hash: str, actor: str,
                  extra=None) -> tuple[InstallationAttempt, bool]:
    """Create the persistent attempt.  Nothing is downloaded here.

    The exclusive right belongs to the *physical target*, not to one plan row:
    a second tab, a second project or a fresh browser session that asks for the
    same bytes in the same directory either re-attaches to the one live attempt
    (same plan hash) or is refused with a conflicting identity.  Two attempts
    can never write the same files at the same time.
    """
    existing_id = db.scalar(active_attempt_query(plan.id).limit(1))
    if existing_id:
        # The same plan asked again (a lost receipt, a refreshed tab) gets the
        # one attempt it already owns.  Queueing is the re-attachable receipt,
        # not a second piece of physical work.
        return db.get(InstallationAttempt, existing_id), True
    target_key = plan_target_key(plan)
    target_root = normalized_storage_root((plan.payload or {}).get("storage_root"))
    for existing, existing_plan in active_attempts(db):
        if plan_target_key(existing_plan) == target_key:
            add_audit(
                db,
                actor,
                "LOCAL_INSTALL_REATTACHED",
                "installation_attempt",
                existing.id,
                {"plan_id": plan.id, "owner_plan_id": existing_plan.id,
                 "target_key": target_key},
                project_id=plan.project_id,
            )
            return existing, True
        # The same physical directory cannot receive two different component
        # sets at once: two writers would interleave into the same files.
        if target_root and normalized_storage_root(
            (existing_plan.payload or {}).get("storage_root")
        ) == target_root:
            raise ValueError("INSTALL_TARGET_CONFLICT")
    payload = {
        "plan_hash": plan_hash,
        "target_key": target_key,
        "queued_at": str(utcnow()),
        "actor": actor,
        "executor": plan.payload.get("executor"),
    }
    if extra:
        payload.update(extra)
    attempt = InstallationAttempt(
        plan_id=plan.id,
        payload=payload,
        sha256=plan_hash,
        status=QUEUED,
        stage=QUEUED,
        total_bytes=int(plan.payload.get("download_bytes") or 0),
    )
    db.add(attempt)
    db.flush()
    add_audit(
        db,
        actor,
        "LOCAL_INSTALL_QUEUED",
        "installation_attempt",
        attempt.id,
        {"plan_id": plan.id, "plan_hash": plan_hash},
        project_id=plan.project_id,
    )
    return attempt, False


def attempts_for_target(db, plan: InstallationPlan) -> list[InstallationAttempt]:
    """Every attempt that ever wrote this plan's physical target.

    The receipt of a lost response belongs to the *directory*, not to the plan
    row that happened to request it first.  A second project or a fresh browser
    that asks for the same bytes must find that history; otherwise an attempt
    that is really queued or really finished looks like it never existed (J02).
    """
    key = plan_target_key(plan)
    root = normalized_storage_root((plan.payload or {}).get("storage_root"))
    matches = []
    rows = db.execute(
        select(InstallationAttempt, InstallationPlan).join(
            InstallationPlan, InstallationPlan.id == InstallationAttempt.plan_id
        )
    ).all()
    for attempt, owner in rows:
        if plan_target_key(owner) == key:
            matches.append(attempt)
            continue
        if root and normalized_storage_root((owner.payload or {}).get("storage_root")) == root:
            matches.append(attempt)
    return sorted(matches, key=lambda row: (row.created_at, row.id))


def request_pause(db, attempt: InstallationAttempt) -> InstallationAttempt:
    """Pause at the next safe point.  The bytes on disk stay reusable."""
    if (attempt.stage or attempt.status) in TERMINAL_STAGES:
        raise ValueError("INSTALL_ATTEMPT_FINISHED")
    attempt.pause_requested = True
    return attempt


def request_resume(db, attempt: InstallationAttempt) -> InstallationAttempt:
    """Continue a paused or retryable attempt from its confirmed bytes."""
    stage = attempt.stage or attempt.status
    if stage in TERMINAL_STAGES:
        raise ValueError("INSTALL_ATTEMPT_FINISHED")
    attempt.pause_requested = False
    attempt.cancel_requested = False
    if stage in {PAUSED, FAILED_RETRYABLE}:
        attempt.stage = QUEUED
        attempt.status = QUEUED
        attempt.error_code = None
        attempt.error_message = None
        attempt.payload = {**(attempt.payload or {}), "resumed_at": str(utcnow())}
        attempt.sha256 = graph_hash(attempt.payload)
    return attempt


def request_cancel(db, attempt: InstallationAttempt) -> InstallationAttempt:
    """Cancel.  Only this explicit request asks the server to stop."""
    stage = attempt.stage or attempt.status
    if stage in TERMINAL_STAGES:
        raise ValueError("INSTALL_ATTEMPT_FINISHED")
    attempt.cancel_requested = True
    attempt.pause_requested = False
    if attempt.lease_owner is None and stage in {QUEUED, PAUSED, FAILED_RETRYABLE}:
        # Nothing is running: the cancellation is already effective.  The
        # reusable .part files and the verified models are kept.
        attempt.stage = CANCELLED
        attempt.status = CANCELLED
        attempt.payload = {**(attempt.payload or {}), "cancelled_at": str(utcnow())}
        attempt.sha256 = graph_hash(attempt.payload)
    return attempt


def _plan_hash_matches(db, attempt: InstallationAttempt) -> bool:
    plan = db.get(InstallationPlan, attempt.plan_id)
    plan_hash = (attempt.payload or {}).get("plan_hash")
    if not plan or not plan_hash:
        return False
    return plan.sha256 == plan_hash and graph_hash(plan.payload) == plan_hash


def claim_next_attempt(factory, worker_id: str, *, lease_seconds: int = LEASE_SECONDS) -> str | None:
    """Claim one queued attempt.  The claim is a lease, not a permanent lock."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        now = utcnow()
        attempt = db.scalar(
            select(InstallationAttempt)
            .where(
                InstallationAttempt.stage == QUEUED,
                or_(
                    InstallationAttempt.lease_owner.is_(None),
                    InstallationAttempt.lease_expires_at <= now,
                ),
            )
            .order_by(InstallationAttempt.created_at, InstallationAttempt.id)
            .limit(1)
        )
        if attempt is None:
            return None
        attempt.lease_owner = worker_id
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.heartbeat_at = now
        attempt_id = attempt.id
    return attempt_id


def renew_lease(factory, attempt_id: str, worker_id: str, *, lease_seconds: int = LEASE_SECONDS) -> bool:
    """Renew the claim.  A worker that stops renewing leaves a claimable attempt."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        attempt = db.get(InstallationAttempt, attempt_id)
        if attempt is None or attempt.lease_owner != worker_id:
            return False
        now = utcnow()
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.heartbeat_at = now
        return True


def recover_expired_attempts(factory, worker_id: str) -> list[str]:
    """A dead Worker leaves a claimable attempt, not a locked one.

    A provable download stage may continue from the confirmed bytes when the
    plan id, the plan hash and the attempt id still agree.  Every uncertain
    stage is reconciled instead: the outcome of a directory swap, a subprocess
    or an uncommitted result is not known, so it is never replayed.
    """
    recovered: list[str] = []
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        now = utcnow()
        attempts = db.scalars(
            select(InstallationAttempt).where(
                InstallationAttempt.lease_owner.is_not(None),
                InstallationAttempt.lease_expires_at.is_not(None),
                InstallationAttempt.lease_expires_at <= now,
                InstallationAttempt.stage.in_(sorted(RESUMABLE_STAGES | UNCERTAIN_STAGES)),
            )
        ).all()
        for attempt in attempts:
            stage = attempt.stage
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            if stage in RESUMABLE_STAGES and _plan_hash_matches(db, attempt):
                attempt.stage = QUEUED
                attempt.status = QUEUED
                attempt.error_code = "INSTALL_WORKER_LOST_RESUMABLE"
                attempt.error_message = "Worker stopped renewing; confirmed bytes are kept and the download continues."
                attempt.payload = {**(attempt.payload or {}), "resumed_from_stage": stage}
                attempt.sha256 = graph_hash(attempt.payload)
                recovered.append(attempt.id)
                add_audit(
                    db,
                    worker_id,
                    "INSTALL_ATTEMPT_RESUMED",
                    "installation_attempt",
                    attempt.id,
                    {"stage": stage, "plan_hash": (attempt.payload or {}).get("plan_hash")},
                )
            else:
                attempt.stage = NEEDS_REVIEW
                attempt.status = "FAILED_RECONCILE_REQUIRED"
                attempt.error_code = "INSTALL_OUTCOME_UNCERTAIN"
                attempt.error_message = "The previous run stopped in an uncertain stage; reconcile it, do not resubmit."
                attempt.payload = {**(attempt.payload or {}), "uncertain_stage": stage}
                attempt.sha256 = graph_hash(attempt.payload)
                add_audit(
                    db,
                    worker_id,
                    "INSTALL_ATTEMPT_NEEDS_REVIEW",
                    "installation_attempt",
                    attempt.id,
                    {"stage": stage, "resubmitted": False},
                )
    return recovered


def _update(factory, attempt_id: str, worker_id: str, **fields) -> None:
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        attempt = db.get(InstallationAttempt, attempt_id)
        if attempt is None:
            return
        now = utcnow()
        for key, value in fields.items():
            setattr(attempt, key, value)
        attempt.heartbeat_at = now
        if attempt.lease_owner == worker_id:
            attempt.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)


class ProgressRecorder:
    """Throttled, durable progress writes.

    The byte count is the length of the ".part" file that is really on disk.
    The database is written at most once per interval, so a slow download does
    not turn into a write storm.
    """

    def __init__(self, factory, attempt_id: str, worker_id: str, *, interval: float = 1.0):
        self.factory = factory
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.interval = interval
        self._last_write = 0.0
        self._last_bytes = 0
        self._started = time.monotonic()

    def report(self, *, component=None, file=None, confirmed=None, total=None, force=False) -> None:
        now = time.monotonic()
        confirmed = int(confirmed or 0)
        if not force and (now - self._last_write) < self.interval:
            return
        elapsed = max(now - self._started, 1e-6)
        speed = int(confirmed / elapsed) if confirmed else 0
        fields = {
            "confirmed_bytes": confirmed,
            "speed_bps": speed,
            "current_component": component,
            "current_file": file,
        }
        if total is not None:
            fields["total_bytes"] = int(total)
        self._last_write = now
        self._last_bytes = confirmed
        _update(self.factory, self.attempt_id, self.worker_id, **fields)

    @property
    def last_bytes(self) -> int:
        return self._last_bytes

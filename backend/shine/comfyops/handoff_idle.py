"""Keep a completed managed handoff exclusive while its renderer is fenced."""

import logging
import os
from datetime import timedelta
from threading import Event, RLock, Thread

from fastapi import HTTPException
from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_journal import fail_handoff
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.resource_epoch import read_epoch
from shine.models import AppSetting, utcnow

logger = logging.getLogger(__name__)


class IdleHandoffKeeper:
    """Maintain an owned, drained renderer's existing lease; never release it on timeout."""

    def __init__(self, factory, attempt_id, supervisor, *, lease_seconds=60, interval=10):
        from shine.comfyops.renderer_supervisor import RendererSupervisor

        if (not isinstance(supervisor, RendererSupervisor) or not isinstance(attempt_id, str)
                or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300
                or not 0 < interval < lease_seconds):
            raise ValueError("HANDOFF_IDLE_KEEPER_INVALID")
        self.factory, self.attempt_id, self.supervisor = factory, attempt_id, supervisor
        self.lease_seconds, self.interval = lease_seconds, interval
        self._lock, self._stop = RLock(), Event()
        self._thread, self._error, self._transferred = None, None, False
        self._released = False

    def _binding(self, db):
        row = db.get(AppSetting, "handoff:" + self.attempt_id)
        value = row.value if row else {}
        lease = db.get(ResourceLease, value.get("resource_id")) if value.get("resource_id") else None
        instance = db.get(ComfyInstance, value.get("instance_id")) if value.get("instance_id") else None
        payload = instance.payload if instance else {}
        try:
            same_worker = process_identity(os.getpid()) == value.get("worker_birth")
        except (OSError, ValueError):
            same_worker = False
        now = utcnow()
        owner = "h3:" + str(value.get("job_id") or "")
        checks = {
            "stage": value.get("stage") == "RESTARTED",
            "attempt": value.get("attempt_id") == self.attempt_id,
            "worker_pid": value.get("worker_pid") == os.getpid(),
            "worker_birth": bool(value.get("worker_birth")) and same_worker,
            "lease_owner": lease is not None and lease.owner == owner,
            "lease_active": (lease is not None and lease.status == "ACTIVE"
                             and bool(lease.exclusive) and lease.expires_at > now),
            "instance": instance is not None and instance.sha256 == value.get("restarted_instance_sha256"),
            "payload_sha": instance is not None and graph_hash(payload) == instance.sha256,
            "payload_flags": payload.get("managed") is True and payload.get("is_mock") is False,
            "epoch": read_epoch(db) == value.get("resource_epoch"),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            # The reason code alone hid which fence failed; operators reconcile
            # from this line, so name the exact checks without leaking payloads.
            logger.error(
                "handoff_idle_binding_failed attempt_id=%s checks=%s lease_status=%s exclusive=%s "
                "expires_at=%s now=%s", self.attempt_id, failed, getattr(lease, "status", None),
                getattr(lease, "exclusive", None), getattr(lease, "expires_at", None), now,
            )
            raise HTTPException(409, "HANDOFF_IDLE_CLAIM_LOST")
        return value, dict(payload)

    def _pulse(self):
        with self.factory() as db:
            value, payload = self._binding(db)
        self.supervisor.verify_drained_binding(payload.get("url", ""), payload.get("process"))
        with self.factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            current, current_payload = self._binding(db)
            if current != value or current_payload != payload:
                logger.error("handoff_idle_recheck_changed attempt_id=%s journal_changed=%s instance_changed=%s",
                             self.attempt_id, current != value, current_payload != payload)
                raise HTTPException(409, "HANDOFF_IDLE_CLAIM_LOST")
            lease = db.get(ResourceLease, current["resource_id"])
            now = utcnow()
            lease.heartbeat_at, lease.expires_at = now, now + timedelta(seconds=self.lease_seconds)

    def _fail(self, error: BaseException | None = None):
        self._error = "HANDOFF_IDLE_RECONCILIATION_REQUIRED"
        self._stop.set()
        if error is not None:
            # The journal only keeps a fixed reason code; the operator needs the
            # real cause to reconcile without guessing.
            logger.error("handoff_idle_failed attempt_id=%s error=%s: %s",
                         self.attempt_id, type(error).__name__, error)
        fail_handoff(self.factory, self.attempt_id, self._error)

    def check(self):
        with self._lock:
            if self._error or self._stop.is_set() or self._transferred:
                raise HTTPException(409, "HANDOFF_IDLE_RECONCILIATION_REQUIRED")
            try:
                self._pulse()
            except Exception as error:
                self._fail(error)
                raise

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.check()
            except Exception:
                return

    def start(self):
        if self._thread is not None:
            raise ValueError("HANDOFF_IDLE_KEEPER_ALREADY_STARTED")
        self.check()
        self._thread = Thread(target=self._run, name="shine-handoff-idle-heartbeat", daemon=True)
        try:
            self._thread.start()
        except Exception as error:
            self._fail(error)
            raise
        return self

    def _handover(self, operation):
        with self._lock:
            self.check()
            try:
                result = operation()
            except Exception as error:
                self._fail(error)
                raise
            self._transferred = True
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._fail()
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")
        return result

    def stop_renewal(self):
        """Stop the heartbeat before an explicitly proven release.

        The lease stays held and is never released here; the caller must still
        stop the owned renderer and prove the physical release afterwards. If
        that proof fails the unrenewed lease expires instead of being revived.
        """
        with self._lock:
            if self._error:
                raise HTTPException(409, "HANDOFF_IDLE_RECONCILIATION_REQUIRED")
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._fail()
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")
        return {"renewal_stopped": True}

    def finalize(self):
        """End the idle hold after an explicit verified release of the renderer.

        The caller must already have stopped the owned renderer and proved the
        physical release; this only stops the lease renewal so a released
        lease is never resurrected by the next heartbeat.
        """
        with self._lock:
            if self._error:
                raise HTTPException(409, "HANDOFF_IDLE_RECONCILIATION_REQUIRED")
            self._released = True
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._fail()
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")
        return {"released": True}

    def transfer(self, settings, boundary, job_id, attempt_id, instance_id, worker_id, state_version):
        from shine.comfyops.handoff_transfer import transfer_handoff_lease

        return self._handover(lambda: transfer_handoff_lease(
            self.factory, settings, boundary, job_id, attempt_id, instance_id, worker_id, state_version,
        ))

    def reserve(self, settings, boundary, job_id, instance_id):
        from shine.comfyops.handoff_transfer import reserve_next_handoff_job

        return self._handover(lambda: reserve_next_handoff_job(self.factory, settings, boundary, job_id, instance_id))

    def close(self):
        """Stop renewal conservatively; a non-transferred lease needs reconciliation."""
        self._stop.set()
        with self._lock:
            if not self._transferred and not self._released and not self._error:
                self._fail()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")

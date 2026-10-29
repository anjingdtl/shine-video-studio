"""Keep a completed managed handoff exclusive while its renderer is fenced."""

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

    def _binding(self, db):
        row = db.get(AppSetting, "handoff:" + self.attempt_id)
        value = row.value if row else {}
        lease = db.get(ResourceLease, value.get("resource_id")) if value.get("resource_id") else None
        instance = db.get(ComfyInstance, value.get("instance_id")) if value.get("instance_id") else None
        payload = instance.payload if instance else {}
        if (value.get("stage") != "RESTARTED" or value.get("attempt_id") != self.attempt_id
                or value.get("worker_pid") != os.getpid() or not value.get("worker_birth")
                or process_identity(os.getpid()) != value.get("worker_birth")
                or not lease or lease.owner != "h3:" + value.get("job_id", "")
                or lease.status != "ACTIVE" or not lease.exclusive or lease.expires_at <= utcnow()
                or not instance or instance.sha256 != value.get("restarted_instance_sha256")
                or graph_hash(payload) != instance.sha256 or payload.get("managed") is not True
                or payload.get("is_mock") is not False or read_epoch(db) != value.get("resource_epoch")):
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
                raise HTTPException(409, "HANDOFF_IDLE_CLAIM_LOST")
            lease = db.get(ResourceLease, current["resource_id"])
            now = utcnow()
            lease.heartbeat_at, lease.expires_at = now, now + timedelta(seconds=self.lease_seconds)

    def _fail(self):
        self._error = "HANDOFF_IDLE_RECONCILIATION_REQUIRED"
        self._stop.set()
        fail_handoff(self.factory, self.attempt_id, self._error)

    def check(self):
        with self._lock:
            if self._error or self._stop.is_set() or self._transferred:
                raise HTTPException(409, "HANDOFF_IDLE_RECONCILIATION_REQUIRED")
            try:
                self._pulse()
            except Exception:
                self._fail()
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
        except Exception:
            self._fail()
            raise
        return self

    def _handover(self, operation):
        with self._lock:
            self.check()
            try:
                result = operation()
            except Exception:
                self._fail()
                raise
            self._transferred = True
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._fail()
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")
        return result

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
            if not self._transferred and not self._error:
                self._fail()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise HTTPException(409, "HANDOFF_IDLE_KEEPER_EXIT_UNCONFIRMED")

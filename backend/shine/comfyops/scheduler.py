"""Single SQLite GPU admission ledger. Expiry requires reconciliation, never blind reuse."""

import gc
import time
from contextvars import ContextVar
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfyops.models import ResourceLease
from shine.models import utcnow

CURRENT_RESOURCE = ContextVar("shine_resource_lease", default=None)


class ResourceScheduler:
    def __init__(self, capacity_mb=16384):
        self.capacity_mb = capacity_mb  # ESTIMATED until a local probe supplies measured capacity.

    def enqueue(self, db, owner, budget_mb, exclusive=False, priority=10):
        if not 0 < budget_mb <= self.capacity_mb:
            raise HTTPException(409, "VRAM_BUDGET_EXCEEDED")
        existing = db.scalar(select(ResourceLease).where(ResourceLease.owner == owner))
        if existing:
            return existing
        lease = ResourceLease(
            owner=owner,
            budget_mb=budget_mb,
            exclusive=True,
            priority=priority,
            status="WAITING",
            expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.flush()
        return lease

    def admit(self, db, lease_id):
        # Caller starts a fresh transaction; reserve the SQLite writer before reading budgets.
        db.execute(text("BEGIN IMMEDIATE"))
        now = utcnow()
        rows = db.scalars(select(ResourceLease).order_by(ResourceLease.created_at, ResourceLease.id)).all()
        for row in rows:
            if row.status == "ACTIVE" and row.expires_at <= now:
                row.status = "RECONCILE_REQUIRED"
        active = [x for x in rows if x.status in {"ACTIVE", "RECONCILE_REQUIRED"}]
        waiting = [x for x in rows if x.status == "WAITING"]
        # Aging prevents starvation while H3 starts with higher priority.
        waiting.sort(key=lambda x: (-x.priority - int((now - x.created_at).total_seconds() / 30), x.created_at, x.id))
        candidate = next((x for x in rows if x.id == lease_id), None)
        if not candidate or candidate.status != "WAITING" or not waiting or waiting[0].id != lease_id:
            db.commit()
            return False
        # Model stages are serial even when their estimated budgets would fit.
        # This also protects old non-exclusive leases created before this policy.
        if active:
            db.commit()
            return False
        candidate.status = "ACTIVE"
        candidate.expires_at = now + timedelta(seconds=60)
        candidate.heartbeat_at = now
        db.commit()
        return True

    def heartbeat(self, db, lease_id, owner):
        lease = db.get(ResourceLease, lease_id)
        if not lease or lease.owner != owner or lease.status != "ACTIVE" or lease.expires_at <= utcnow():
            raise HTTPException(409, "RESOURCE_LEASE_LOST")
        lease.expires_at = utcnow() + timedelta(seconds=60)
        lease.heartbeat_at = utcnow()

    def release(self, db, lease_id, owner, *, reconciled=False):
        lease = db.get(ResourceLease, lease_id)
        if not lease or lease.owner != owner:
            raise HTTPException(403, "RESOURCE_OWNER_MISMATCH")
        if lease.status == "RECONCILE_REQUIRED" and not reconciled:
            raise HTTPException(409, "MODEL_RELEASE_RECONCILIATION_REQUIRED")
        lease.status = "RELEASED"


def release_completed(factory, lease_id, owner, *, reconciled=False):
    with factory.begin() as db:
        ResourceScheduler().release(db, lease_id, owner, reconciled=reconciled)


# One resource ledger is shared by HTTP preparation tools and the H3 Worker.
from contextlib import contextmanager  # noqa: E402
from uuid import uuid4  # noqa: E402


@contextmanager
def resource_slot(factory, kind, budget_mb, *, release_models=None, wait_seconds=0, on_acquired=None):
    owner = kind.lower() + ":" + uuid4().hex
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        row = scheduler.enqueue(db, owner, budget_mb, True, 10)
        identity = row.id
    with factory() as db:
        admitted = scheduler.admit(db, identity)
    deadline = time.monotonic() + wait_seconds
    while not admitted and time.monotonic() < deadline:
        with factory() as db:
            if db.scalar(select(ResourceLease.id).where(ResourceLease.status == "RECONCILE_REQUIRED").limit(1)):
                break  # Unknown release needs reconciliation, not a timed blind retry.
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        with factory() as db:
            admitted = scheduler.admit(db, identity)
    if not admitted:
        # Synchronous API callers may retry a fresh intent; do not leave an orphaned waiting entry.
        with factory.begin() as db:
            scheduler.release(db, identity, owner)
        raise HTTPException(409, "GPU_BUSY_RETRY_OR_QUEUE")
    from threading import Event, Thread

    stopped = Event()
    heartbeat_errors = []

    def pulse():
        while not stopped.wait(15):
            try:
                with factory.begin() as db:
                    scheduler.heartbeat(db, identity, owner)
            except Exception as exc:
                heartbeat_errors.append(type(exc).__name__)
                stopped.set()

    thread = Thread(target=pulse, name="shine-resource-heartbeat", daemon=True)
    thread.start()
    resource_token = CURRENT_RESOURCE.set(identity)
    try:
        if on_acquired:
            on_acquired(identity)
        if release_models is not None:
            release_models()
        gc.collect()
        yield identity
        if heartbeat_errors:
            raise HTTPException(409, "RESOURCE_LEASE_LOST_RECONCILE_REQUIRED")
    finally:
        try:
            if release_models is not None:
                release_models()
            gc.collect()
        except Exception:
            with factory.begin() as db:
                db.get(ResourceLease, identity).status = "RECONCILE_REQUIRED"
            raise
        else:
            with factory.begin() as db:
                lease = db.get(ResourceLease, identity)
                if lease.status == "RECONCILE_REQUIRED":
                    raise HTTPException(409, "MODEL_RELEASE_RECONCILIATION_REQUIRED")
                scheduler.release(db, identity, owner)
        finally:
            CURRENT_RESOURCE.reset(resource_token)
            stopped.set()
            thread.join(timeout=2)

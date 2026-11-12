"""Durable control-intent records for lifecycle actions.

One flat table, one row per control operation.  The API inserts intents and
polls them; the resident Worker claims and executes them.  Idempotency: the
same ``idempotency_key`` with the same semantics always returns the same
operation; the same key with different semantics is a 409.  Claiming and
completion are compare-and-set, so a stale executor can never overwrite a
newer outcome.

Concurrency: two lifecycle actions that change processes on the *same*
installation never execute at the same time.  The claim step refuses a
conflicting action while a live owner is running one, and a single claim batch
never hands out two conflicting actions for one installation.  ``probe`` and
``reconcile`` are read-only evidence gathers and are never excluded.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from shine.models import RuntimeControlOperation, utcnow
from shine.runtime_orchestrator.contracts import (
    BUSY_ACTIONS,
    KNOWN_ACTIONS,
    TERMINAL_STATUSES,
    OperationError,
)

OPERATION_TTL_SECONDS = 3600
OWNER_LIVENESS_SECONDS = 30


def _owner_alive(db, row: RuntimeControlOperation, now) -> bool:
    """Whether the executor that claimed this row is still heartbeating."""
    if not row.owner_worker_id:
        return False
    from shine.models import WorkerInstance

    worker = db.get(WorkerInstance, row.owner_worker_id)
    return bool(
        worker
        and worker.last_heartbeat_at
        and worker.last_heartbeat_at > now - timedelta(seconds=OWNER_LIVENESS_SECONDS)
    )


def conflicting_running_operations(
    db, *, installation_id: str, action: str, now=None
) -> list[RuntimeControlOperation]:
    """Lifecycle actions that must finish before ``action`` may start here."""
    if action not in BUSY_ACTIONS:
        return []
    now = now or utcnow()
    rows = db.scalars(
        select(RuntimeControlOperation).where(
            RuntimeControlOperation.installation_id == installation_id,
            RuntimeControlOperation.status.in_(("PENDING", "RUNNING")),
        )
    ).all()
    blocking = []
    for row in rows:
        if row.action not in BUSY_ACTIONS:
            continue
        if row.status == "PENDING":
            # A queued lifecycle action on this installation owns the next
            # slot; executing a conflicting one first would invert the user's
            # order (a stop that lands before an earlier ensure).
            blocking.append(row)
            continue
        if _owner_alive(db, row, now):
            blocking.append(row)
    return blocking


def state_version_for(db, installation_id: str) -> int:
    """The CAS version a control request must quote.

    Derived from the terminal operations of the installation, exactly like the
    snapshot's ``state_version``: an old page that quotes an older number is
    holding a stale view of the engine.
    """
    from sqlalchemy import func

    counts = dict(
        db.execute(
            select(RuntimeControlOperation.status, func.count())
            .where(RuntimeControlOperation.installation_id == installation_id)
            .group_by(RuntimeControlOperation.status)
        ).all()
    )
    return sum(int(counts.get(status, 0)) for status in ("SUCCEEDED", "REFUSED", "FAILED"))


def any_worker_online(db) -> bool:
    """Whether a worker heartbeat is fresh, for the intent wait short-circuit.

    Without a live executor a control intent can never leave PENDING; callers
    must learn that immediately instead of blocking out their wait budget.
    """
    from datetime import timedelta

    from sqlalchemy import select as _select

    from shine.models import WorkerInstance, utcnow

    rows = db.scalars(
        _select(WorkerInstance).where(WorkerInstance.last_heartbeat_at.is_not(None))
    ).all()
    fresh = utcnow() - timedelta(seconds=OWNER_LIVENESS_SECONDS)
    return any(row.last_heartbeat_at > fresh for row in rows)



def create_or_get_operation(
    db,
    *,
    action: str,
    installation_id: str,
    idempotency_key: str,
    request: dict[str, Any],
    expected_state_version: int | None = None,
) -> RuntimeControlOperation:
    if action not in KNOWN_ACTIONS:
        raise OperationError(422, "UNKNOWN_CONTROL_ACTION", action)
    if not installation_id or len(installation_id) > 96:
        raise OperationError(422, "INSTALLATION_IDENTITY_INVALID", "")
    if not idempotency_key or len(idempotency_key) > 160:
        raise OperationError(422, "IDEMPOTENCY_KEY_REQUIRED", "")
    existing = db.scalar(
        select(RuntimeControlOperation).where(
            RuntimeControlOperation.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        same = (
            existing.action == action
            and existing.installation_id == installation_id
            and (existing.request or {}).get("params") == (request or {}).get("params")
        )
        if not same:
            raise OperationError(409, "IDEMPOTENCY_KEY_CONFLICT", "same key, different intent")
        return existing
    if expected_state_version is not None:
        current = state_version_for(db, installation_id)
        if int(expected_state_version) != current:
            # The page is looking at an older engine state than the backend has
            # (another tab acted, or this one is minutes old).  Refusing is the
            # only safe answer: the command it would send was formed against a
            # world that no longer exists.
            raise OperationError(
                409,
                "RUNTIME_STATE_VERSION_STALE",
                f"expected {expected_state_version}, current {current}",
            )
    operation = RuntimeControlOperation(
        action=action,
        installation_id=installation_id,
        idempotency_key=idempotency_key,
        request=request,
        status="PENDING",
    )
    db.add(operation)
    db.flush()
    return operation


def claim_pending_operations(db, *, worker_id: str, worker_birth: str, limit: int = 4):
    """Claim PENDING operations whose previous owner provably died.

    RUNNING rows whose owner heartbeat is gone are re-claimed (the executor is
    idempotent by construction: ensure/stop/release re-derive every fact from
    durable records before acting).  Terminal rows are never touched.

    A conflicting lifecycle action on the same installation is never claimed
    while another one is queued or running there, and one batch never hands out
    two conflicting actions for one installation.  Two web pages that click
    start and stop at the same moment therefore serialize instead of fighting
    over the same renderer.
    """
    rows = db.scalars(
        select(RuntimeControlOperation)
        .where(RuntimeControlOperation.status.in_(("PENDING", "RUNNING")))
        .order_by(RuntimeControlOperation.created_at)
        .limit(limit * 4)
    ).all()
    claimed: list[RuntimeControlOperation] = []

    now = utcnow()
    busy_by_installation: dict[str, str] = {}
    for row in rows:
        if len(claimed) >= limit:
            break
        if row.status == "RUNNING":
            if row.owner_worker_id == worker_id and row.owner_birth == worker_birth:
                continue  # already ours (crash restart of this very worker)
            if _owner_alive(db, row, now):
                if row.action in BUSY_ACTIONS:
                    busy_by_installation.setdefault(row.installation_id, row.id)
                continue
        if row.action in BUSY_ACTIONS:
            holder = busy_by_installation.get(row.installation_id)
            if holder is not None:
                continue  # the same installation already has a live claim
            blocking = [
                other for other in conflicting_running_operations(
                    db, installation_id=row.installation_id, action=row.action, now=now,
                )
                if other.id != row.id and other.status == "RUNNING"
            ]
            if blocking:
                continue
            busy_by_installation[row.installation_id] = row.id
        row.status = "RUNNING"
        row.owner_worker_id = worker_id
        row.owner_birth = worker_birth
        row.state_version += 1
        row.started_at = row.started_at or now
        claimed.append(row)
    db.flush()
    return claimed


def _complete(db, operation_id: str, *, owner_worker_id: str, expected_version: int):
    row = db.get(RuntimeControlOperation, operation_id)
    if row is None:
        raise OperationError(404, "OPERATION_NOT_FOUND", operation_id)
    if row.status in TERMINAL_STATUSES:
        return None  # idempotent: a replayed completion is not an error
    if row.owner_worker_id != owner_worker_id or row.state_version != expected_version:
        return None  # stale executor: the newer owner owns the outcome
    return row


def succeed_operation(
    db, operation_id: str, *, owner_worker_id: str, expected_version: int, result: dict[str, Any]
):
    row = _complete(db, operation_id, owner_worker_id=owner_worker_id, expected_version=expected_version)
    if row is None:
        return None
    row.status = "SUCCEEDED"
    row.result = result
    row.finished_at = utcnow()
    row.state_version += 1
    return row


def fail_operation(
    db,
    operation_id: str,
    *,
    owner_worker_id: str,
    expected_version: int,
    code: str,
    refused: bool = False,
    detail: dict[str, Any] | None = None,
):
    row = _complete(db, operation_id, owner_worker_id=owner_worker_id, expected_version=expected_version)
    if row is None:
        return None
    row.status = "REFUSED" if refused else "FAILED"
    row.failure_code = code
    if detail:
        row.result = detail
    row.finished_at = utcnow()
    row.state_version += 1
    return row


def get_operation(db, operation_id: str) -> RuntimeControlOperation | None:
    return db.get(RuntimeControlOperation, operation_id)


def reap_orphaned_operations(db) -> int:
    """Housekeeping: rows that never reached an executor age out, as history."""
    now = utcnow()
    rows = db.scalars(
        select(RuntimeControlOperation).where(RuntimeControlOperation.status == "PENDING")
    ).all()
    reaped = 0
    for row in rows:
        if row.created_at < now - timedelta(seconds=OPERATION_TTL_SECONDS):
            row.status = "FAILED"
            row.failure_code = "OPERATION_EXECUTOR_UNAVAILABLE"
            row.finished_at = now
            reaped += 1
    return reaped

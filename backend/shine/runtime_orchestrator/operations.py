"""Durable control-intent records for lifecycle actions.

One flat table, one row per control operation.  The API inserts intents and
polls them; the resident Worker claims and executes them.  Idempotency: the
same ``idempotency_key`` with the same semantics always returns the same
operation; the same key with different semantics is a 409.  Claiming and
completion are compare-and-set, so a stale executor can never overwrite a
newer outcome.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from shine.models import RuntimeControlOperation, utcnow
from shine.runtime_orchestrator.contracts import (
    KNOWN_ACTIONS,
    TERMINAL_STATUSES,
    OperationError,
)

OPERATION_TTL_SECONDS = 3600


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
    fresh = utcnow() - timedelta(seconds=30)
    return any(row.last_heartbeat_at > fresh for row in rows)


def create_or_get_operation(
    db,
    *,
    action: str,
    installation_id: str,
    idempotency_key: str,
    request: dict[str, Any],
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
    """
    rows = db.scalars(
        select(RuntimeControlOperation)
        .where(RuntimeControlOperation.status.in_(("PENDING", "RUNNING")))
        .order_by(RuntimeControlOperation.created_at)
        .limit(limit * 2)
    ).all()
    claimed: list[RuntimeControlOperation] = []
    from shine.models import WorkerInstance

    now = utcnow()
    for row in rows:
        if len(claimed) >= limit:
            break
        if row.status == "RUNNING":
            if row.owner_worker_id == worker_id and row.owner_birth == worker_birth:
                continue  # already ours (crash restart of this very worker)
            worker = db.get(WorkerInstance, row.owner_worker_id)
            alive = bool(
                worker
                and worker.last_heartbeat_at
                and worker.last_heartbeat_at > now - timedelta(seconds=30)
            )
            if alive:
                continue
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

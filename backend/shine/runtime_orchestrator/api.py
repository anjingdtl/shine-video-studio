"""HTTP surface of the control plane: intents in, derived state out.

The routes never start or stop a process in the request thread.  A write
request records a control intent (idempotent by key) and waits — bounded —
for the resident Worker to execute it, then answers with the terminal
operation and, for compatibility, the same payload shape the legacy route
produced.  When no worker is online the operation stays PENDING and the
caller learns that fact instead of a silent hang.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from shine.api_operator import Operator
from shine.runtime_orchestrator.contracts import OperationError, orchestrator_enabled
from shine.runtime_orchestrator.identity import installation_activity
from shine.runtime_orchestrator.operations import (
    any_worker_online,
    create_or_get_operation,
    get_operation,
)

router = APIRouter(prefix="/api/v1/runtime-orchestrator", tags=["runtime-orchestrator"])

WAIT_STEP_SECONDS = 0.4


class ControlBody(BaseModel):
    installation_id: str = Field(min_length=1, max_length=96)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=200)


def _serialize(operation) -> dict[str, Any]:
    return {
        "operation_id": operation.id,
        "action": operation.action,
        "installation_id": operation.installation_id,
        "status": operation.status,
        "state_version": operation.state_version,
        "failure_code": operation.failure_code,
        "result": operation.result or {},
        "created_at": operation.created_at.isoformat() if operation.created_at else None,
        "finished_at": operation.finished_at.isoformat() if operation.finished_at else None,
    }


def _submit_and_wait(request: Request, actor: str, body: ControlBody, *, action: str, wait_seconds: float):
    return submit_control_intent(
        request, actor,
        action=action,
        installation_id=body.installation_id,
        idempotency_key=body.idempotency_key,
        reason=body.reason,
        wait_seconds=wait_seconds,
    )


def submit_control_intent(
    request: Request,
    actor: str,
    *,
    action: str,
    installation_id: str,
    idempotency_key: str,
    reason: str = "",
    wait_seconds: float = 60.0,
):
    """Record a control intent and wait (bounded) for the Worker to execute it.

    Legacy HTTP process controls call this instead of touching a process from
    the request thread; the response keeps their old shape via the terminal
    operation's ``result``.
    """
    factory = request.app.state.session_factory
    with factory.begin() as db:
        operation = create_or_get_operation(
            db,
            action=action,
            installation_id=installation_id,
            idempotency_key=idempotency_key,
            request={"actor": actor, "params": {"reason": reason}},
        )
        operation_id = operation.id
        # Without a live Worker an intent can never leave PENDING; report it
        # immediately instead of blocking out the whole wait budget.
        worker_online = any_worker_online(db)
    if not worker_online:
        with factory() as db:
            return get_operation(db, operation_id)
    deadline = time.monotonic() + wait_seconds
    while True:
        with factory() as db:
            operation = get_operation(db, operation_id)
            if operation is None:
                raise OperationError(404, "OPERATION_NOT_FOUND", operation_id)
            if operation.status in ("SUCCEEDED", "FAILED", "REFUSED"):
                return operation
        if time.monotonic() >= deadline:
            return operation
        time.sleep(WAIT_STEP_SECONDS)


@router.get("/status")
def runtime_status(request: Request):
    """Read-only snapshot; never triggers a process or a model load."""
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    from shine.comfyops.image_renderer import image_renderer_state
    from shine.comfyops.runtime_configuration import resolve_runtime

    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "orchestrator_enabled": orchestrator_enabled(),
        "installations": [],
    }
    with factory() as session:
        runtime = resolve_runtime(session, settings)
        installation_id = getattr(runtime, "instance_id", "") or ""
        if installation_id:
            activity = installation_activity(session, installation_id)
            renderer = image_renderer_state(session, settings, installation_id)
            snapshot["installations"].append(
                {
                    "installation_id": installation_id,
                    "renderer_state": activity["renderer_state"],
                    "image_renderer": renderer,
                    "gpu_leases": activity["gpu_leases"],
                    "in_flight_h3_jobs": len(activity["in_flight_h3_jobs"]),
                    "in_flight_image_work": len(activity["in_flight_image_work"]),
                }
            )
    return snapshot


@router.post("/ensure", status_code=202)
def runtime_ensure(request: Request, actor: Operator, body: ControlBody):
    operation = _submit_and_wait(request, actor, body, action="ensure", wait_seconds=240.0)
    return _serialize(operation)


@router.post("/stop", status_code=202)
def runtime_stop(request: Request, actor: Operator, body: ControlBody):
    operation = _submit_and_wait(request, actor, body, action="stop", wait_seconds=90.0)
    return _serialize(operation)


@router.post("/probe", status_code=202)
def runtime_probe(request: Request, actor: Operator, body: ControlBody):
    operation = _submit_and_wait(request, actor, body, action="probe", wait_seconds=30.0)
    return _serialize(operation)


@router.post("/reconcile", status_code=202)
def runtime_reconcile(request: Request, actor: Operator, body: ControlBody):
    operation = _submit_and_wait(request, actor, body, action="reconcile", wait_seconds=30.0)
    return _serialize(operation)


@router.get("/operations/{operation_id}")
def runtime_operation(request: Request, operation_id: str):
    factory = request.app.state.session_factory
    with factory() as db:
        operation = get_operation(db, operation_id)
        if operation is None:
            raise OperationError(404, "OPERATION_NOT_FOUND", operation_id)
        return _serialize(operation)

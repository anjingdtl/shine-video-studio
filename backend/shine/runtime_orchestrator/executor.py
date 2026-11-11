"""The resident Worker executes control intents; nobody else does.

Every action re-derives its facts from the durable records inside the
installation lifecycle guard before it touches a process, so an operation
replayed after a worker crash is idempotent: "ensure" either reuses the
recorded renderer or refuses with the named conflict; "stop"/"release" only
act on processes whose ownership the existing proofs confirm.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.runtime_orchestrator.identity import installation_activity
from shine.runtime_orchestrator.operations import (
    claim_pending_operations,
    fail_operation,
    reap_orphaned_operations,
    succeed_operation,
)

logger = logging.getLogger(__name__)


def _activity_summary(db, installation_id: str) -> dict[str, Any]:
    activity = installation_activity(db, installation_id)
    return {
        "renderer_state": activity["renderer_state"],
        "h3_runs": len(activity["h3_records"]),
        "image_renderer": bool(activity["image_record"]),
        "gpu_leases": len(activity["gpu_leases"]),
        "in_flight_h3_jobs": len(activity["in_flight_h3_jobs"]),
        "in_flight_image_work": len(activity["in_flight_image_work"]),
    }


def _execute_operation(factory, settings, action: str, installation_id: str, request: dict) -> dict[str, Any]:
    if action == "probe":
        with factory() as db:
            return {"probed": True, "activity": _activity_summary(db, installation_id)}
    if action == "reconcile":
        # Read-only evidence gathering: the worker's own recovery tick owns
        # the real reconciliation; a control op never resubmits anything.
        from shine.comfyops.renderer_release import managed_process_state
        from shine.models import AppSetting
        from shine.runtime_orchestrator.identity import image_record_for_installation

        with factory() as db:
            uncertain = []
            image = image_record_for_installation(db, installation_id)
            if image:
                state = managed_process_state(image.get("record") or image)
                if state in ("UNVERIFIED",):
                    uncertain.append({"kind": "image_renderer", "state": state})
            rows = db.scalars(
                select(AppSetting).where(AppSetting.key.like("renderer-startup:%"))
            ).all()
            for row in rows:
                value = row.value if isinstance(row.value, dict) else {}
                if value.get("stage") == "RECONCILE_REQUIRED":
                    uncertain.append({"kind": "renderer_startup", "key": row.key})
            return {"reconciled": False, "uncertain_records": uncertain,
                    "note": "worker tick reconciles jobs/leases; nothing is resubmitted"}
    if action == "ensure":
        from shine.comfyops.image_renderer import ensure_image_renderer

        return ensure_image_renderer(factory, settings, instance_id=installation_id)
    if action == "stop":
        from shine.comfyops.image_renderer import stop_image_renderer

        actor = (request or {}).get("actor") or "worker:control-operation"
        return stop_image_renderer(factory, settings, instance_id=installation_id, actor=actor)
    if action == "release":
        from shine.comfyops.renderer_release import release_managed_processes

        actor = (request or {}).get("actor") or "worker:control-operation"
        with factory.begin() as db:
            return release_managed_processes(db, actor=actor, reason="control_operation")
    raise HTTPException(422, f"UNKNOWN_CONTROL_ACTION:{action}")


def advance_control_operations(factory, settings, *, worker_id: str, worker_birth: str, limit: int = 2) -> int:
    """Claim and execute pending control operations.  Returns executed count.

    Never raises: a control-plane failure must not kill the worker loop.  The
    worker's own heartbeat keeps operations claimable after a crash.
    """
    executed = 0
    try:
        with factory.begin() as db:
            reap_orphaned_operations(db)
            claimed = claim_pending_operations(db, worker_id=worker_id, worker_birth=worker_birth, limit=limit)
            batch = [
                {
                    "id": op.id,
                    "action": op.action,
                    "installation_id": op.installation_id,
                    "request": dict(op.request or {}),
                    "state_version": op.state_version,
                }
                for op in claimed
            ]
    except Exception:  # noqa: BLE001
        logger.exception("control operations could not be claimed")
        return 0
    for item in batch:
        try:
            result = _execute_operation(factory, settings, item["action"], item["installation_id"], item["request"])
            with factory.begin() as db:
                succeed_operation(
                    db, item["id"],
                    owner_worker_id=worker_id,
                    expected_version=item["state_version"],
                    result=result if isinstance(result, dict) else {"value": result},
                )
            executed += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("code", type(exc).__name__)
            refused = exc.status_code == 409
            with factory.begin() as db:
                fail_operation(
                    db, item["id"],
                    owner_worker_id=worker_id,
                    expected_version=item["state_version"],
                    code=str(detail or "CONTROL_OPERATION_REFUSED"),
                    refused=refused,
                )
            executed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("control operation %s failed", item["id"])
            with factory.begin() as db:
                fail_operation(
                    db, item["id"],
                    owner_worker_id=worker_id,
                    expected_version=item["state_version"],
                    code=f"CONTROL_OPERATION_ERROR:{type(exc).__name__}",
                    refused=False,
                )
            executed += 1
    return executed

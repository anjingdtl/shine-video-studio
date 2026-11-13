"""The resident Worker executes control intents; nobody else does.

Actions re-derive their facts under the installation guard before touching a
process. When a worker disappears after claiming a lifecycle operation, its
successor checks the durable evidence without repeating the operation.

Recovery reporting is explicit.  ``reconcile`` never resubmits anything: it
re-reads the same evidence the worker's recovery tick uses (startup records,
managed process identities, GPU leases, and jobs whose external outcome is
unknown) and answers with one of four outcomes, so the Web can say "核对完成"
instead of showing an eternal spinner:

* ``NOTHING_TO_RECONCILE`` — no uncertain record exists;
* ``RECONCILED``           — a provably dead record was cleared, nothing else;
* ``NEEDS_REVIEW``         — evidence is complete but the decision is a human's;
* ``FAILED``               — the evidence itself could not be read.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.runtime_orchestrator.contracts import BUSY_ACTIONS
from shine.runtime_orchestrator.guard import LifecycleGuardBusy, LifecycleGuardUnavailable
from shine.runtime_orchestrator.identity import installation_activity
from shine.runtime_orchestrator.operations import (
    claim_pending_operations,
    fail_operation,
    reap_orphaned_operations,
    succeed_operation,
)

logger = logging.getLogger(__name__)

OUTCOME_NOTHING = "NOTHING_TO_RECONCILE"
OUTCOME_RECONCILED = "RECONCILED"
OUTCOME_NEEDS_REVIEW = "NEEDS_REVIEW"
OUTCOME_FAILED = "FAILED"


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


def _probe(factory, settings, installation_id: str) -> dict[str, Any]:
    """Explicitly probe loopback transport without adopting the listener."""
    from shine.runtime_orchestrator.status import _probe_http, runtime_snapshot

    with factory() as db:
        activity = _activity_summary(db, installation_id)
        snapshot = runtime_snapshot(db, settings)
    port = (snapshot.get("ownership") or {}).get("port")
    if snapshot.get("installation_id") != installation_id or not isinstance(port, int) or not 0 < port < 65536:
        return {"probed": True, "activity": activity, "transport": "UNKNOWN", "listener_ownership": "UNKNOWN"}
    reachable = _probe_http(f"http://127.0.0.1:{port}")
    with factory() as db:
        after = runtime_snapshot(db, settings)
    # Re-prove the owned listener after HTTP; the port may have changed hands.
    ownership = ("VERIFIED" if reachable and after.get("installation_id") == installation_id
                 and (after.get("ownership") or {}).get("port") == port
                 and (after.get("health") or {}).get("comfy_http") == "UP" else "UNVERIFIED")
    return {"probed": True, "activity": activity, "transport": "UP" if reachable else "DOWN",
            "listener_ownership": ownership, "production_ready": False}



def _normalize_released_h3(factory, installation_id: str) -> list[dict[str, Any]]:
    """Close H3 history only after the existing lease and both process proofs agree."""
    from shine.comfyops.models import ComfyInstance
    from shine.models import AppSetting, Job, utcnow
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard
    from shine.runtime_orchestrator.identity import (
        _h3_runtime_process,
        _released_h3_processes_are_gone,
        h3_records_for_installation,
    )
    from shine.services import UNKNOWN_SUBMISSION_CODES, add_audit

    normalized = []
    with renderer_lifecycle_guard(installation_id):
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            activity = installation_activity(db, installation_id)
            unknown = db.scalar(select(Job.id).where(
                Job.status == "NEEDS_REVIEW", Job.failure_code.in_(sorted(UNKNOWN_SUBMISSION_CODES)),
            ).limit(1))
            if (unknown or activity["gpu_leases"] or activity["in_flight_h3_jobs"]
                    or activity["in_flight_image_work"] or activity["image_record"]):
                return []
            for record in h3_records_for_installation(db, installation_id):
                if record["state"] not in {"ACTIVE", "UNCERTAIN"} and not record.get("released_and_gone"):
                    continue
                row = db.get(AppSetting, record["key"])
                current = dict(row.value) if row and isinstance(row.value, dict) else {}
                latest = _h3_runtime_process(db, record["run_id"], current)
                if not latest or not _released_h3_processes_are_gone(db, current, latest):
                    continue
                # No prompt is submitted and the historical identity is retained.
                row.value = {**current, "stage": "STOPPED", "stopped_at": utcnow().isoformat(),
                             "reconciled_from_stage": current.get("stage"),
                             "stopped_reason": "released_h3_renderer_proven_gone"}
                instance = db.get(ComfyInstance, record["run_id"])
                if instance:
                    instance.status = "STOPPED"
                add_audit(db, "controller:reconcile", "H3_STARTUP_RECONCILED", "comfy_instance",
                          record["run_id"], {"resubmitted": False, "lease_id": record["lease_id"]})
                normalized.append({"kind": "h3_renderer", "run_id": record["run_id"],
                                   "outcome": "recorded_stopped"})
    return normalized


def _reconcile(factory, settings, installation_id: str) -> dict[str, Any]:
    """Read-only evidence gathering plus the one provably safe cleanup.

    The worker's own recovery tick owns task-level reconciliation.  A control
    operation must never resubmit a prompt, release a lease or kill a process;
    the single mutation allowed here is clearing a startup record whose process
    is proven gone, which is what unblocks the next start.
    """
    from shine.comfyops.image_renderer import reconcile_dead_image_renderer
    from shine.comfyops.renderer_release import describe_managed_processes
    from shine.models import Job
    from shine.runtime_orchestrator.identity import (
        image_record_for_installation,
        lease_facts,
    )
    from shine.services import UNKNOWN_SUBMISSION_CODES

    checked = {"managed_processes": 0, "image_record": False, "leases": 0, "unknown_submission_jobs": 0}
    uncertain: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    try:
        with factory() as db:
            described = describe_managed_processes(db)
            checked["managed_processes"] = len(described)
            for entry in described:
                if entry.get("state") == "UNVERIFIED":
                    uncertain.append({
                        "kind": "managed_process",
                        **{key: entry.get(key) for key in ("id", "stage", "port", "pid")},
                    })
            activity = installation_activity(db, installation_id)
            checked["h3_records"] = len(activity["h3_records"])
            for record in activity["h3_records"]:
                if record["state"] == "UNCERTAIN":
                    uncertain.append({"kind": "h3_renderer", "run_id": record["run_id"],
                                      "stage": record["stage"],
                                      "missing_nodes": record.get("missing_nodes") or []})
            image = image_record_for_installation(db, installation_id)
            checked["image_record"] = bool(image)
            leases = [lease for lease in lease_facts(db) if lease["status"] == "RECONCILE_REQUIRED"]
            checked["leases"] = len(leases)
            for lease in leases:
                uncertain.append({"kind": "gpu_lease", "lease_id": lease["lease_id"],
                                  "owner": lease["owner"]})
            unknown_jobs = db.scalars(
                select(Job).where(
                    Job.status == "NEEDS_REVIEW",
                    Job.failure_code.in_(sorted(UNKNOWN_SUBMISSION_CODES)),
                )
            ).all()
            checked["unknown_submission_jobs"] = len(unknown_jobs)
            for job in unknown_jobs:
                # Evidence is preserved for the existing history reconciliation;
                # this operation only reports it.
                uncertain.append({"kind": "unknown_submission", "job_id": job.id,
                                  "failure_code": job.failure_code})
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is a reported failure
        logger.exception("reconcile evidence could not be read")
        return {"outcome": OUTCOME_FAILED, "checked": checked, "uncertain": uncertain,
                "normalized": [], "error": type(exc).__name__}

    # One safe cleanup: a recorded renderer whose process is proven gone (and
    # whose port is free) is cleared so the next start is not refused by a
    # record nobody can act on.  Anything less certain stays for a human.
    if image and image.get("live_state") == "GONE":
        try:
            if reconcile_dead_image_renderer(factory, installation_id):
                normalized.append({"kind": "image_renderer", "outcome": "recorded_stopped"})
            else:
                uncertain.append({"kind": "image_renderer", "state": "RECORD_CHANGED"})
        except (LifecycleGuardBusy, LifecycleGuardUnavailable) as exc:
            uncertain.append({"kind": "image_renderer", "state": "REFUSED",
                              "code": str(exc)})

    try:
        closed = _normalize_released_h3(factory, installation_id)
        normalized.extend(closed)
        closed_runs = {item["run_id"] for item in closed}
        uncertain = [item for item in uncertain
                     if not (item.get("kind") == "h3_renderer" and item.get("run_id") in closed_runs)]
    except (LifecycleGuardBusy, LifecycleGuardUnavailable) as exc:
        uncertain.append({"kind": "h3_renderer", "state": "REFUSED", "code": str(exc)})

    if uncertain:
        return {"outcome": OUTCOME_NEEDS_REVIEW, "checked": checked, "uncertain": uncertain,
                "normalized": normalized,
                "note": "已有提交结果未知的任务保持原证据链，需人工核对；本操作不会重发任何任务。"}
    if normalized:
        return {"outcome": OUTCOME_RECONCILED, "checked": checked, "uncertain": uncertain,
                "normalized": normalized, "note": "已清理可证实的失效进程记录，其余无需处理。"}
    return {"outcome": OUTCOME_NOTHING, "checked": checked, "uncertain": uncertain,
            "normalized": [], "note": "没有需要核对的记录。"}


def _ensure(factory, settings, installation_id: str) -> dict[str, Any]:
    from shine.comfyops.image_renderer import ensure_image_renderer

    result = ensure_image_renderer(factory, settings, instance_id=installation_id)
    return {**result, "outcome": "REUSED" if result.get("reused") else "STARTED"}


def _stop(factory, settings, installation_id: str, actor: str) -> dict[str, Any]:
    from shine.comfyops.image_renderer import stop_image_renderer

    result = stop_image_renderer(factory, settings, instance_id=installation_id, actor=actor)
    already = result.get("status") == "NOT_STARTED" or result.get("outcome") in (
        "already_stopped", "recorded_stopped",
    )
    return {**result, "outcome": "ALREADY_STOPPED" if already else "STOPPED"}


def _release(factory, installation_id: str, actor: str) -> dict[str, Any]:
    from shine.comfyops.renderer_release import release_managed_processes_guarded

    report = release_managed_processes_guarded(factory, actor=actor, reason="control_operation")
    return {**report, "outcome": "RELEASED" if not report.get("refused") else "PARTIAL",
            "installation_id": installation_id}


def _execute_operation(factory, settings, action: str, installation_id: str, request: dict) -> dict[str, Any]:
    if action == "probe":
        return _probe(factory, settings, installation_id)
    if action == "reconcile":
        return _reconcile(factory, settings, installation_id)
    if action == "ensure":
        return _ensure(factory, settings, installation_id)
    if action == "stop":
        actor = (request or {}).get("actor") or "worker:control-operation"
        return _stop(factory, settings, installation_id, actor)
    if action == "release":
        actor = (request or {}).get("actor") or "worker:control-operation"
        return _release(factory, installation_id, actor)
    raise HTTPException(422, f"UNKNOWN_CONTROL_ACTION:{action}")


def advance_control_operations(factory, settings, *, worker_id: str, worker_birth: str, limit: int = 2) -> int:
    """Claim and execute pending control operations.  Returns executed count.

    Never raises: a control-plane failure must not kill the worker loop.  The
    worker's own heartbeat keeps operations claimable after a crash.
    """
    executed = 0
    try:
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            if reap_orphaned_operations(db):
                db.flush()
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
            # An operation starts at version 1 and each claim increments it.
            # A second claim may follow an external side effect of the first.
            interrupted = item["state_version"] > 2 and item["action"] in BUSY_ACTIONS
            if interrupted:
                evidence = _reconcile(factory, settings, item["installation_id"])
                result = {
                    "outcome": OUTCOME_NEEDS_REVIEW,
                    "reconciliation": evidence,
                    "note": "上次控制操作中断。已核对现有记录，未重复执行。",
                }
            else:
                result = _execute_operation(
                    factory, settings, item["action"], item["installation_id"], item["request"],
                )
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                if interrupted:
                    fail_operation(
                        db, item["id"], owner_worker_id=worker_id, expected_version=item["state_version"],
                        code="RUNTIME_OPERATION_INTERRUPTED", refused=True, detail=result,
                    )
                else:
                    succeed_operation(
                        db, item["id"], owner_worker_id=worker_id, expected_version=item["state_version"],
                        result=result,
                    )
            executed += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("code", type(exc).__name__)
            refused = exc.status_code == 409
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                fail_operation(
                    db, item["id"],
                    owner_worker_id=worker_id,
                    expected_version=item["state_version"],
                    code=str(detail or "CONTROL_OPERATION_REFUSED"),
                    refused=refused,
                    detail={"outcome": "REFUSED" if refused else OUTCOME_NEEDS_REVIEW,
                            "detail": exc.detail if isinstance(exc.detail, dict) else str(exc.detail)},
                )
            executed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("control operation %s failed", item["id"])
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                fail_operation(
                    db, item["id"],
                    owner_worker_id=worker_id,
                    expected_version=item["state_version"],
                    code=f"CONTROL_OPERATION_ERROR:{type(exc).__name__}",
                    refused=False,
                    detail={"outcome": OUTCOME_FAILED, "error": type(exc).__name__},
                )
            executed += 1
    return executed

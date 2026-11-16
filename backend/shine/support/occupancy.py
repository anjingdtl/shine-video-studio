"""One read-only occupancy answer, composed from the existing ledgers.

The copilot must never decide "the machine is idle" from its own subset of
tables.  Shine already has authoritative sources for live work:

* ``shine.media.output_layout.active_work_blockers`` — the storage-migration gate's
  own definition of live work (shot jobs, series, whole-film runs, library
  render tasks, including voice).
* ``ResourceLease`` — the single GPU admission ledger every model stage takes
  through ``resource_slot`` (H3, image, review, TTS, RelayVSR, LLM).
* ``describe_managed_processes`` — the owned renderers/reviewers and whether one
  is in flight.

This module only *reads* those, adds material-preparation batches (same
QUEUED/RUNNING vocabulary) and unresolved external outcomes, and fails closed
when any layer cannot be read.  It creates no second scheduler and releases
nothing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from shine.models import utcnow

#: A lease in either state proves a model stage owns, or may still own, the GPU.
BLOCKING_LEASE_STATUSES = ("ACTIVE", "RECONCILE_REQUIRED")
#: External outcomes that are unresolved.  A task in this state may still be
#: running upstream, so nothing may be re-submitted or re-configured over it.
#: ``submission_unknown`` is the worker's own name for an uncertain submit ACK;
#: a reconciled attempt is later rewritten to ``history_found`` / ``not_found``.
UNRESOLVED_OUTCOMES = (
    "submission_unknown",
    "unknown",
    "reconcile_required",
    "uncertain",
)

OCCUPANCY_UNKNOWN = "OCCUPANCY_UNKNOWN"
PRODUCTION_ACTIVE = "PRODUCTION_ACTIVE"
GPU_LEASE_ACTIVE = "GPU_LEASE_ACTIVE"
MATERIAL_ACTIVE = "MATERIAL_ACTIVE"
UNRESOLVED_OUTCOME = "UNRESOLVED_EXTERNAL_OUTCOME"


def resource_occupancy(db, settings=None) -> dict[str, Any]:
    """Compose the existing occupancy ledgers into one fail-closed answer."""
    signals: list[dict[str, Any]] = []
    unknown: list[str] = []

    live_work = False
    try:
        from shine.media.output_layout import active_work_blockers

        blockers = active_work_blockers(db)
        live_work = bool(blockers)
        for row in blockers:
            signals.append({"key": "live_work", "label": str(row)[:120]})
    except Exception as exc:  # noqa: BLE001 - an unreadable gate must not authorise a change
        unknown.append(f"live_work:{type(exc).__name__}")

    leases: list[dict[str, Any]] = []
    try:
        from shine.comfyops.models import ResourceLease

        rows = db.scalars(
            select(ResourceLease).where(ResourceLease.status.in_(BLOCKING_LEASE_STATUSES))
        ).all()
        leases = [
            {
                "owner": str(row.owner)[:120],
                "status": row.status,
                "budget_mb": row.budget_mb,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
            for row in rows
        ]
        for row in leases:
            signals.append({"key": "gpu_lease", "label": f"{row['owner']} / {row['status']}"})
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"gpu_lease:{type(exc).__name__}")

    managed_in_flight = 0
    try:
        from shine.comfyops.renderer_release import describe_managed_processes

        for row in describe_managed_processes(db):
            if isinstance(row, dict) and row.get("in_flight"):
                managed_in_flight += 1
                signals.append(
                    {"key": "managed_in_flight", "label": str(row.get("kind") or "renderer")}
                )
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"managed_processes:{type(exc).__name__}")

    materials = 0
    try:
        from sqlalchemy import func

        from shine.materialprep.models import MaterialRun
        from shine.materialprep.states import RUN_QUEUED, RUN_RUNNING

        materials = int(
            db.scalar(
                select(func.count(MaterialRun.id)).where(
                    MaterialRun.status.in_((RUN_QUEUED, RUN_RUNNING))
                )
            )
            or 0
        )
        if materials:
            signals.append({"key": "material_runs", "label": f"{materials} 个素材生成批次"})
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"material_runs:{type(exc).__name__}")

    unresolved = 0
    try:
        from sqlalchemy import func

        from shine.models import JobAttempt

        unresolved = int(
            db.scalar(
                select(func.count(JobAttempt.id)).where(
                    func.lower(JobAttempt.reconciliation_state).in_(UNRESOLVED_OUTCOMES)
                )
            )
            or 0
        )
        if unresolved:
            signals.append(
                {"key": "unresolved_outcome", "label": f"{unresolved} 个任务结果待核对"}
            )
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"unresolved_outcome:{type(exc).__name__}")

    if unknown:
        code = OCCUPANCY_UNKNOWN
    elif unresolved:
        code = UNRESOLVED_OUTCOME
    elif leases:
        code = GPU_LEASE_ACTIVE
    elif live_work:
        code = PRODUCTION_ACTIVE
    elif materials:
        code = MATERIAL_ACTIVE
    elif managed_in_flight:
        code = PRODUCTION_ACTIVE
    else:
        code = None

    labels = {
        OCCUPANCY_UNKNOWN: "无法完整读取资源占用情况，按占用中处理。",
        UNRESOLVED_OUTCOME: "有任务的外部结果尚未核对，不能在此之上执行配置动作。",
        GPU_LEASE_ACTIVE: "GPU 租约尚未释放，不能执行配置动作。",
        PRODUCTION_ACTIVE: "有生产任务或受管服务正在使用显卡。",
        MATERIAL_ACTIVE: "素材中心正在生成图片或语音。",
        None: "当前没有生产任务或 GPU 租约占用。",
    }
    return {
        "checked_at": utcnow().isoformat(),
        "busy": code is not None,
        "code": code,
        "label": labels[code],
        "signals": signals[:12],
        "leases": leases[:8],
        "managed_in_flight": managed_in_flight,
        "material_runs": materials,
        "unresolved_outcomes": unresolved,
        "unreadable_layers": unknown,
    }

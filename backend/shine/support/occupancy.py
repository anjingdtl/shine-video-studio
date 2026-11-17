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


def _collect(db) -> dict[str, Any]:
    """Read every existing occupancy layer once, without judging it yet."""
    live_work: list[str] = []
    unknown: list[str] = []

    try:
        from shine.media.output_layout import active_work_blockers

        live_work = [str(row)[:120] for row in active_work_blockers(db)]
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
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"gpu_lease:{type(exc).__name__}")

    processes: list[dict[str, Any]] = []
    try:
        from shine.comfyops.renderer_release import _record_key, describe_managed_processes

        described = describe_managed_processes(db)
        processes = [
            {
                "key": _record_key(row),
                "kind": row.get("kind"),
                "state": row.get("state"),
                "in_flight": bool(row.get("in_flight")),
            }
            for row in described
            if isinstance(row, dict)
        ]
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
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"material_runs:{type(exc).__name__}")

    image_work: list[dict[str, Any]] = []
    try:
        from shine.runtime_orchestrator.identity import in_flight_image_work

        image_work = [
            {"kind": str(row.get("kind")), "id": str(row.get("id"))}
            for row in in_flight_image_work(db)
            if isinstance(row, dict)
        ]
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"image_work:{type(exc).__name__}")

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
    except Exception as exc:  # noqa: BLE001
        unknown.append(f"unresolved_outcome:{type(exc).__name__}")

    return {
        "live_work": live_work,
        "leases": leases,
        "processes": processes,
        "materials": materials,
        "image_work": image_work,
        "unresolved": unresolved,
        "unknown": unknown,
    }


def occupancy_facts(db) -> dict[str, Any]:
    """A stable, non-volatile projection of every occupancy layer.

    Used by the action fingerprint: key extraction only, no timestamps, no
    expiry instants and no human labels, so an untouched machine keeps the
    same facts while a real lease, job or process change invalidates a preview.
    """
    layers = _collect(db)
    return {
        "unresolved": layers["unresolved"],
        "materials": layers["materials"],
        "image_work": sorted(layers["image_work"], key=lambda row: (row["kind"], row["id"])),
        "live_work": sorted(layers["live_work"]),
        "leases": sorted(
            ({"owner": row["owner"], "status": row["status"]} for row in layers["leases"]),
            key=lambda row: (row["owner"], row["status"]),
        ),
        "processes": sorted(
            (
                {"key": row["key"], "state": row["state"], "in_flight": row["in_flight"]}
                for row in layers["processes"]
            ),
            key=lambda row: row["key"],
        ),
        "unreadable": sorted(layers["unknown"]),
    }


def _signals(layers: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for label in layers["live_work"]:
        signals.append({"key": "live_work", "label": label})
    for row in layers["leases"]:
        signals.append({"key": "gpu_lease", "label": f"{row['owner']} / {row['status']}"})
    for row in layers["processes"]:
        if row["in_flight"]:
            signals.append({"key": "managed_in_flight", "label": str(row["kind"] or "renderer")})
    if layers["materials"]:
        signals.append({"key": "material_runs", "label": f"{layers['materials']} 个素材生成批次"})
    for row in layers["image_work"]:
        signals.append({"key": "image_work", "label": f"{row['kind']} / {row['id']}"})
    if layers["unresolved"]:
        signals.append(
            {"key": "unresolved_outcome", "label": f"{layers['unresolved']} 个任务结果待核对"}
        )
    return signals[:12]


def _managed_in_flight(layers: dict[str, Any]) -> int:
    return sum(1 for row in layers["processes"] if row["in_flight"])


def resource_occupancy(db, settings=None) -> dict[str, Any]:
    """Compose the existing occupancy ledgers into one fail-closed answer."""
    layers = _collect(db)
    unknown = layers["unknown"]
    leases = layers["leases"]
    live_work = bool(layers["live_work"])
    managed_in_flight = _managed_in_flight(layers)
    materials = layers["materials"]
    unresolved = layers["unresolved"]
    signals = _signals(layers)

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
        "signals": signals,
        "leases": leases[:8],
        "managed_in_flight": managed_in_flight,
        "material_runs": materials,
        "unresolved_outcomes": unresolved,
        "unreadable_layers": unknown,
    }


def release_occupancy(db, *, only: set[str] | None = None) -> dict[str, Any]:
    """Occupancy for releasing *this installation's* managed processes.

    A live GPU lease is the thing a release exists to clear, so the lease alone
    is not a reason to refuse: the guarded release executor still re-checks the
    lease's job and every physical ownership fact before stopping anything.
    Everything that would make a release unsafe - unreadable layers, unresolved
    external outcomes, material generation, review work or real production - is
    still fail-closed here.
    """
    layers = _collect(db)
    unknown = layers["unknown"]
    targets = [
        row for row in layers["processes"]
        if only is None or row["key"] in only
    ]
    target_in_flight = sum(1 for row in targets if row["in_flight"])
    signals = _signals(layers)

    if unknown:
        code = OCCUPANCY_UNKNOWN
    elif layers["unresolved"]:
        code = UNRESOLVED_OUTCOME
    elif layers["materials"] or layers["image_work"]:
        code = MATERIAL_ACTIVE
    elif layers["live_work"]:
        code = PRODUCTION_ACTIVE
    elif target_in_flight:
        code = PRODUCTION_ACTIVE
    else:
        code = None

    labels = {
        OCCUPANCY_UNKNOWN: "无法完整读取资源占用情况，已拒绝释放。",
        UNRESOLVED_OUTCOME: "有任务的外部结果尚未核对，不能释放相关服务。",
        MATERIAL_ACTIVE: "素材生成或审片正在进行，不能释放相关服务。",
        PRODUCTION_ACTIVE: "有生产任务或受管服务正在运行，不能释放。",
        None: "可以释放本工作台拥有且已空闲的受管服务。",
    }
    return {
        "checked_at": utcnow().isoformat(),
        "busy": code is not None,
        "code": code,
        "label": labels[code],
        "signals": signals,
        "leases": layers["leases"][:8],
        "idle_leases": [
            row for row in layers["leases"]
            if row["status"] == "ACTIVE"
        ][:8],
        "managed_in_flight": _managed_in_flight(layers),
        "target_in_flight": target_in_flight,
        "material_runs": layers["materials"],
        "unresolved_outcomes": layers["unresolved"],
        "unreadable_layers": unknown,
        "release_targets": targets,
    }

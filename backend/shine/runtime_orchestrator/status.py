"""The single read-only runtime snapshot.

Everything the Web shows about the local production engine is derived here
from the durable records and bounded probes — never a second persistent
"truth".  A snapshot call never starts a process, loads a model or writes a
ledger row (plan §3.3/§3.4, §6).

The four states the plan demands to distinguish are the axes of the state
machine:

* the process is provably gone            -> STOPPED
* HTTP is momentarily unreachable         -> DEGRADED (process alive: probe again)
* the model is loading / a start intent   -> STARTING (bounded, never "dead")
* a real submission's outcome is unknown  -> BLOCKED_UNVERIFIED / RECONCILING
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from sqlalchemy import select

from shine.comfyops.image_renderer import image_renderer_state, load_image_recipe
from shine.models import RuntimeControlOperation
from shine.runtime_orchestrator.identity import installation_activity

PROBE_TIMEOUT_SECONDS = 2.0
PROBE_CACHE_TTL_SECONDS = 4.0
# Short transport failure while the process is provably alive is DEGRADED,
# not down: one retry before the classification, and a cache so status
# polling (frontend 8s) can never turn into a probe storm (plan §6.1).
_probe_cache: dict[str, tuple[float, bool | None]] = {}


def _http_up(base_url: str) -> bool | None:
    """Bounded loopback probe; True/False, or None when it cannot be told."""
    if not base_url:
        return None
    now = time.monotonic()
    cached = _probe_cache.get(base_url)
    if cached and now - cached[0] < PROBE_CACHE_TTL_SECONDS:
        return cached[1]
    outcome: bool | None = None
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/system_stats",
            timeout=PROBE_TIMEOUT_SECONDS,
            trust_env=False,  # a local endpoint never goes through the host proxy
        )
        outcome = response.status_code == 200
    except (httpx.HTTPError, OSError):
        outcome = False
        try:  # one bounded retry: connection churn is not an outage
            retry = httpx.get(
                f"{base_url.rstrip('/')}/system_stats",
                timeout=PROBE_TIMEOUT_SECONDS,
                trust_env=False,
            )
            outcome = retry.status_code == 200
        except (httpx.HTTPError, OSError):
            outcome = False
    _probe_cache[base_url] = (now, outcome)
    return outcome


def _classify(activity: dict[str, Any], *, http_up: bool | None) -> str:
    reconcile_leases = [lease for lease in activity["gpu_leases"]
                        if lease["status"] == "RECONCILE_REQUIRED"]
    if reconcile_leases:
        return "RECONCILING"
    image = activity.get("image_record")
    if image:
        if image["state"] == "STARTING":
            return "STARTING"
        if image["state"] == "UNCERTAIN":
            return "BLOCKED_UNVERIFIED"
    if activity["renderer_state"] == "UNCERTAIN_H3":
        return "BLOCKED_UNVERIFIED"
    h3_active = any(r["state"] in ("ACTIVE", "STARTING") for r in activity["h3_records"])
    if h3_active or (image and image["state"] == "ACTIVE"):
        busy = bool(
            activity["in_flight_h3_jobs"]
            or activity["in_flight_image_work"]
            or [lease for lease in activity["gpu_leases"] if lease["status"] == "ACTIVE"]
            or any(r["state"] == "STARTING" for r in activity["h3_records"])
        )
        if busy:
            return "BUSY"
        if image and image["state"] == "ACTIVE":
            # Process provably alive; transport decides between READY and DEGRADED.
            if http_up is False:
                return "DEGRADED"
            return "READY_IDLE"
        return "BUSY"  # an H3 renderer is resident behind its series/keeper
    # Nothing recorded as running: a port that still listens is not ours.
    return "STOPPED"


_RECOVERY: dict[str, dict[str, str | None]] = {
    "NOT_BOUND": {"required": True, "reason_code": "IMAGE_RUNTIME_NOT_BOUND",
                  "safe_action": None, "advice": "先在基础配置里完成本机环境登记。"},
    "STOPPED": {"required": False, "reason_code": None,
                "safe_action": "ensure", "advice": "制作引擎已停止；需要出图时可安全启动。"},
    "STARTING": {"required": False, "reason_code": "START_IN_PROGRESS",
                 "safe_action": None, "advice": "引擎正在启动（含模型加载），请等待，不要重复启动。"},
    "READY_IDLE": {"required": False, "reason_code": None,
                   "safe_action": "stop", "advice": "引擎就绪空闲。"},
    "BUSY": {"required": False, "reason_code": "WORK_IN_FLIGHT",
             "safe_action": None, "advice": "引擎正在执行任务；按现有租约串行等待。"},
    "DEGRADED": {"required": False, "reason_code": "TRANSPORT_DEGRADED",
                 "safe_action": "probe", "advice": "进程仍在但 HTTP 暂时不可达；已自动重试，可再点“检查连接”。"},
    "RECONCILING": {"required": True, "reason_code": "LEASE_RECONCILE_REQUIRED",
                    "safe_action": "reconcile", "advice": "有租约需要对账；恢复动作只核对证据，不重发任务。"},
    "BLOCKED_UNVERIFIED": {"required": True, "reason_code": "OWNERSHIP_UNVERIFIED",
                           "safe_action": "reconcile", "advice": "渲染器归属或外部提交结果未核对；禁止盲启/盲杀。"},
    "EXTERNAL_CONFLICT": {"required": True, "reason_code": "EXTERNAL_INSTANCE_ON_PORT",
                          "safe_action": None, "advice": "端口上是不属于本工作台的 ComfyUI；不会接管也不会终止它。"},
}


def _recovery_for(state: str) -> dict[str, Any]:
    entry = dict(_RECOVERY.get(state) or {"required": False, "reason_code": None,
                                          "safe_action": None, "advice": None})
    return entry


def _external_conflict(port: int | None) -> bool:
    """A listener on the recipe port that no record of ours owns.

    Only a proven listener counts; an unreadable port table is uncertainty,
    which the caller reports as the underlying state, never as a conflict.
    """
    if not port:
        return False
    try:
        from shine.comfyops.renderer_supervisor import windows_port_listeners

        return bool(windows_port_listeners(int(port)))
    except (OSError, ValueError):  # includes non-Windows, where this never claims
        return False


def runtime_snapshot(db, settings) -> dict[str, Any]:
    """One installation's derived state, ownership, work and recovery advice.

    The installation identity of the *image runtime binding* is the scope
    here (the H3 runs bind the same adopted row).  Deliberately not
    resolve_runtime: it raises under offline simulation and knows nothing
    about the image binding.
    """
    installation_id = ""
    base_url = ""
    try:
        from shine.materialprep import runtime as image_runtime

        config = image_runtime.load_config(settings)
        installation_id = config.instance_id or ""
        base_url = config.base_url or ""
    except Exception:  # noqa: BLE001 - an unreadable config is NOT_BOUND, never a crash
        installation_id, base_url = "", ""
    port_hint = recipe_port(settings)
    if not installation_id and port_hint:
        # No image binding yet, but a generated recipe still names the
        # installation this workbench adopted.
        try:
            from shine.comfyops.image_renderer import load_image_recipe

            installation_id = load_image_recipe(settings)["renderer"].get("instance_id") or ""
        except Exception:  # noqa: BLE001
            installation_id = ""
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "installation_id": installation_id,
        "state": "NOT_BOUND",
        "health": {"worker": "DOWN", "comfy_http": "UNKNOWN"},
        "ownership": {"managed": False, "verified": False, "port": None, "pid": None},
        "current": {"owner_kind": None, "task": None, "operation_id": None},
        "resource": {"active_leases": 0, "waiting_leases": 0, "reconcile_leases": 0},
        "recovery": _recovery_for("NOT_BOUND"),
        "last_observed_at": None,
        "state_version": 0,
    }
    # Worker liveness is stack-level truth, independent of any installation
    # binding: it must be filled even on the NOT_BOUND early return.
    from shine.runtime_orchestrator.operations import any_worker_online

    snapshot["health"]["worker"] = "UP" if any_worker_online(db) else "DOWN"
    if not installation_id:
        return snapshot
    from shine.models import utcnow

    snapshot["last_observed_at"] = utcnow().isoformat()
    activity = installation_activity(db, installation_id)
    renderer_facts = image_renderer_state(db, settings, installation_id)
    # The startup record carries the port while a renderer was started; a
    # stopped/unstarted installation still owns the recipe's port claim, and
    # an external listener there is exactly the conflict to report.
    port = renderer_facts.get("port") or recipe_port(settings)
    http_up = _http_up(base_url) if activity["renderer_state"] != "NONE" else None
    state = _classify(activity, http_up=http_up)
    if state == "STOPPED" and _external_conflict(port):
        state = "EXTERNAL_CONFLICT"
    snapshot["state"] = state
    snapshot["recovery"] = _recovery_for(state)
    snapshot["health"]["comfy_http"] = "UP" if http_up else ("DOWN" if http_up is False else "UNKNOWN")
    image_record = activity.get("image_record")
    active_h3 = next((r for r in activity["h3_records"] if r["state"] == "ACTIVE"), None)
    owner_kind = "h3" if active_h3 else ("image" if image_record else None)
    snapshot["current"]["owner_kind"] = owner_kind
    snapshot["current"]["task"] = (active_h3 or {}).get("job_id") or (
        activity["in_flight_image_work"][0]["id"] if activity["in_flight_image_work"] else None
    )
    from sqlalchemy import func

    counts = dict(
        db.execute(
            select(RuntimeControlOperation.status, func.count())
            .where(RuntimeControlOperation.installation_id == installation_id)
            .group_by(RuntimeControlOperation.status)
        ).all()
    )
    pending = db.scalar(
        select(RuntimeControlOperation)
        .where(RuntimeControlOperation.status.in_(("PENDING", "RUNNING")))
        .order_by(RuntimeControlOperation.created_at.desc())
        .limit(1)
    )
    snapshot["current"]["operation_id"] = pending.id if pending else None
    snapshot["state_version"] = sum(
        int(counts.get(status, 0)) for status in ("SUCCEEDED", "REFUSED", "FAILED")
    )
    leases = activity["gpu_leases"]
    snapshot["resource"] = {
        "active_leases": len([lease for lease in leases if lease["status"] == "ACTIVE"]),
        "reconcile_leases": len([lease for lease in leases if lease["status"] == "RECONCILE_REQUIRED"]),
        "waiting_leases": 0,
    }
    process = (image_record or {}).get("process") if image_record else None
    snapshot["ownership"] = {
        "managed": bool(image_record or active_h3),
        "verified": bool(image_record),
        "port": port,
        "pid": (process or {}).get("pid") if process else None,
    }
    snapshot["activity"] = {
        "h3_runs": len(activity["h3_records"]),
        "in_flight_h3_jobs": len(activity["in_flight_h3_jobs"]),
        "in_flight_image_work": len(activity["in_flight_image_work"]),
    }
    return snapshot


def recipe_port(settings) -> int | None:
    try:
        recipe = load_image_recipe(settings)
    except Exception:  # noqa: BLE001 - no recipe means no port claim
        return None
    try:
        return int(recipe["renderer"].get("port") or 0) or None
    except (TypeError, ValueError):
        return None

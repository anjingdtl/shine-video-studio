"""The single read-only runtime snapshot.

Everything the Web shows about the local production engine is derived here
from the durable records and bounded probes — never a second persistent
"truth".  A snapshot call never starts a process, loads a model or writes a
ledger row (plan §3.3/§3.4, §6).

The states the plan demands to distinguish are the axes of the state machine:

* the process is provably gone            -> STOPPED
* HTTP is momentarily unreachable         -> DEGRADED (process alive: probe again)
* the model is loading / a start intent   -> STARTING (bounded, never "dead")
* a real submission's outcome is unknown  -> BLOCKED_UNVERIFIED / RECONCILING
* an unowned listener on our port         -> EXTERNAL_CONFLICT

The record is intent, not proof.  A startup record that says RUNNING while its
PID/birth/owned group cannot be confirmed is reported as BLOCKED_UNVERIFIED,
never as "制作中": believing a record is how a dead renderer used to look busy
forever (and how "stop" ended up refusing with a busy reason that never
cleared).
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

import httpx
from sqlalchemy import select

from shine.comfyops.image_renderer import image_renderer_state, load_image_recipe
from shine.models import RuntimeControlOperation
from shine.runtime_orchestrator.identity import installation_activity, verified_live_record
from shine.runtime_orchestrator.operations import state_version_for

PROBE_TIMEOUT_SECONDS = 2.0
PROBE_CACHE_TTL_SECONDS = 4.0
# Short transport failure while the process is provably alive is DEGRADED,
# not down: one retry before the classification, and a cache so status
# polling (frontend 8s) can never turn into a probe storm (plan §6.1).
_probe_cache: dict[str, tuple[float, str | None, bool]] = {}
_probes_in_flight: set[str] = set()
_probe_lock = Lock()


def _http_up(base_url: str, *, job_name: str | None = None) -> bool | None:
    """Bounded loopback probe; True/False, or None when it cannot be told."""
    if not base_url:
        return None
    with _probe_lock:
        entry = _probe_cache.get(base_url)
        cached = entry if entry and entry[1] == job_name else None
        if cached and time.monotonic() - cached[0] < PROBE_CACHE_TTL_SECONDS:
            return cached[2]
        if base_url in _probes_in_flight:
            return cached[2] if cached else None
        _probes_in_flight.add(base_url)
    try:
        outcome = _probe_http(base_url)
        with _probe_lock:
            _probe_cache[base_url] = (time.monotonic(), job_name, outcome)
        return outcome
    finally:
        with _probe_lock:
            _probes_in_flight.remove(base_url)


def _probe_http(base_url: str) -> bool:
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
    return outcome


def _classify(
    activity: dict[str, Any], *, live_image: str | None, h3_verified: bool,
    http_up: bool | None, workflow: str,
) -> str:
    reconcile_leases = [lease for lease in activity["gpu_leases"]
                        if lease["status"] == "RECONCILE_REQUIRED"]
    if reconcile_leases:
        return "RECONCILING"
    image = activity.get("image_record")
    stage = (image or {}).get("stage")
    # A record flagged for reconciliation owns an unresolved external side
    # effect; a dead process does not clear that by itself (only reconcile or
    # release may), so it outranks every live fact here.
    if image and (image.get("state") == "UNCERTAIN" or stage == "RECONCILE_REQUIRED"):
        return "BLOCKED_UNVERIFIED"
    # A live start intent: the process may not exist yet, and that is normal.
    if (image and stage in ("INTENT", "LAUNCHED", "LISTENING")
            or any(record["state"] == "STARTING" for record in activity["h3_records"])):
        return "STARTING"
    # The record claims a running renderer the OS cannot confirm.
    if live_image == "UNVERIFIED":
        return "BLOCKED_UNVERIFIED"
    if activity["renderer_state"] == "UNCERTAIN_H3":
        return "BLOCKED_UNVERIFIED"
    h3_active = any(record["state"] == "ACTIVE" for record in activity["h3_records"])
    busy = bool(
        activity["in_flight_h3_jobs"]
        or activity["in_flight_image_work"]
        or [lease for lease in activity["gpu_leases"] if lease["status"] == "ACTIVE"]
    )
    if live_image == "RUNNING":
        if busy:
            return "BUSY"
        # Process provably alive; transport decides between READY and DEGRADED.
        return "READY_IDLE" if http_up is True and workflow == "VERIFIED" else "DEGRADED"
    if h3_active:
        if not h3_verified:
            return "BLOCKED_UNVERIFIED"
        return "BUSY" if http_up is True else "DEGRADED"
    if busy:
        # Work is recorded but no renderer can be proven: reconcile, do not
        # start a second one on top of an unknown outcome.
        return "RECONCILING"
    # Nothing recorded as running: a port that still listens is not ours.
    return "STOPPED"


_RECOVERY: dict[str, dict[str, Any]] = {
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
    "RECONCILING": {"required": True, "reason_code": "RECONCILE_REQUIRED",
                    "safe_action": "reconcile",
                    "advice": "有任务或资源的真实状态需要核对；恢复动作只核对证据，不重发任务。"},
    "BLOCKED_UNVERIFIED": {"required": True, "reason_code": "OWNERSHIP_UNVERIFIED",
                           "safe_action": "reconcile", "advice": "渲染器归属或外部提交结果未核对；禁止盲启/盲杀。"},
    "EXTERNAL_CONFLICT": {"required": True, "reason_code": "EXTERNAL_INSTANCE_ON_PORT",
                          "safe_action": "probe",
                          "advice": "端口被外部程序占用；请手动关闭它后再启动，工作台不会接管或终止它。"},
}


def _recovery_for(state: str) -> dict[str, Any]:
    return dict(_RECOVERY.get(state) or {"required": False, "reason_code": None,
                                         "safe_action": None, "advice": None})


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


def _owned_listener_pid(port: int | None, process: dict[str, Any] | None) -> int | None:
    """Re-prove that HTTP on this port belongs to the renderer's Job Object."""
    from shine.comfyops.installer import process_identity
    from shine.comfyops.owned_process import inspect_named_group
    from shine.comfyops.renderer_supervisor import windows_loopback_listener_pid

    if not port or not process or not process.get("job_name"):
        return None
    try:
        pid = windows_loopback_listener_pid(port)
        birth = process_identity(pid)
        return pid if birth and inspect_named_group(process["job_name"], pid, birth) else None
    except (OSError, ValueError):
        return None


def _workflow_health(settings, *, live_state: str | None, image_record: dict[str, Any]) -> str:
    """Whether the listening renderer still runs the adapter the recipe names.

    A stored digest comparison, not a re-hash of the installation: the status
    endpoint must stay cheap enough to poll.  "UNVERIFIED" is a real answer and
    is never upgraded to "OK" by assumption.
    """
    if live_state != "RUNNING":
        return "NOT_RUNNING"
    try:
        pinned = (load_image_recipe(settings)["renderer"] or {}).get("adapter_sha256")
    except Exception:  # noqa: BLE001 - no recipe means nothing to compare
        return "UNVERIFIED"
    adapter_sha256 = image_record.get("adapter_sha256")
    if not pinned or not adapter_sha256:
        return "UNVERIFIED"
    if pinned != adapter_sha256:
        return "MISMATCH"
    return "VERIFIED" if image_record.get("graph_verified") is True else "UNVERIFIED"


def runtime_snapshot(db, settings) -> dict[str, Any]:
    """One installation's derived state, ownership, work and recovery advice.

    The installation identity of the *image runtime binding* is the scope
    here (the H3 runs bind the same adopted row).  Deliberately not
    resolve_runtime: it raises under offline simulation and knows nothing
    about the image binding.
    """
    installation_id = ""
    try:
        from shine.materialprep import runtime as image_runtime

        config = image_runtime.load_config(settings)
        installation_id = config.instance_id or ""
    except Exception:  # noqa: BLE001 - an unreadable config is NOT_BOUND, never a crash
        installation_id = ""
    port_hint = recipe_port(settings)
    if not installation_id and port_hint:
        # No image binding yet, but a generated recipe still names the
        # installation this workbench adopted.
        try:
            installation_id = load_image_recipe(settings)["renderer"].get("instance_id") or ""
        except Exception:  # noqa: BLE001
            installation_id = ""
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "installation_id": installation_id,
        "state": "NOT_BOUND",
        "health": {"worker": "DOWN", "api": "UP", "comfy_http": "UNKNOWN",
                   "workflow": "NOT_BOUND"},
        "ownership": {"managed": False, "verified": False, "port": None, "pid": None},
        "current": {"owner_kind": None, "task": None, "operation_id": None,
                    "operation_action": None, "operation_status": None},
        "resource": {"active_leases": 0, "waiting_leases": 0, "reconcile_leases": 0},
        "diagnostics": {"transport": "UNKNOWN", "ownership": "UNKNOWN",
                        "capability": "LIVE_CHECK_PENDING", "missing_nodes": [],
                        "production_ready": False},
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
    image_record = activity.get("image_record")
    live_image = renderer_facts.get("state")
    # The startup record carries the port while a renderer was started; a
    # stopped/unstarted installation still owns the recipe's port claim, and
    # an external listener there is exactly the conflict to report.
    active_h3 = next((record for record in activity["h3_records"] if record["state"] == "ACTIVE"), None)
    port = (active_h3 or {}).get("port") or renderer_facts.get("port") or port_hint
    h3_facts = verified_live_record(active_h3) if active_h3 else {}
    h3_verified = bool(h3_facts.get("alive") and h3_facts.get("owned"))
    process = ((active_h3 or {}).get("process") if h3_verified else
               (image_record or {}).get("process") if live_image == "RUNNING" else None)
    listener_pid = _owned_listener_pid(port, process)
    http_up = _http_up(f"http://127.0.0.1:{port}", job_name=process["job_name"]) if listener_pid else None
    workflow = _workflow_health(
        settings, live_state=live_image,
        image_record=image_record or {},
    )
    if active_h3 and h3_verified:
        workflow = "VERIFIED" if active_h3.get("node_inventory") else "UNVERIFIED"
    state = _classify(
        activity, live_image=live_image, h3_verified=h3_verified, http_up=http_up, workflow=workflow,
    )
    if state == "STOPPED" and _external_conflict(port):
        state = "EXTERNAL_CONFLICT"
    snapshot["state"] = state
    snapshot["recovery"] = _recovery_for(state)
    if state == "DEGRADED" and http_up is True:
        snapshot["recovery"].update({
            "reason_code": "WORKFLOW_NOT_READY",
            "advice": "引擎已连接，运行配置仍需检查。",
            # An idle owned image renderer can be stopped safely to reload its
            # configuration, even if the capability probe did not pass.
            "safe_action": "stop",
        })
    snapshot["health"]["comfy_http"] = "UP" if http_up else ("DOWN" if http_up is False else "UNKNOWN")
    snapshot["health"]["workflow"] = workflow
    owner_kind = "h3" if active_h3 else ("image" if live_image == "RUNNING" else None)
    snapshot["current"]["owner_kind"] = owner_kind
    snapshot["current"]["task"] = (active_h3 or {}).get("job_id") or (
        activity["in_flight_image_work"][0]["id"] if activity["in_flight_image_work"] else None
    )
    pending = db.scalar(
        select(RuntimeControlOperation)
        .where(
            RuntimeControlOperation.status.in_(("PENDING", "RUNNING")),
            (RuntimeControlOperation.installation_id == installation_id)
            | (RuntimeControlOperation.action == "release"),
        )
        .order_by(RuntimeControlOperation.created_at.desc())
        .limit(1)
    )
    snapshot["current"]["operation_id"] = pending.id if pending else None
    snapshot["current"]["operation_action"] = pending.action if pending else None
    snapshot["current"]["operation_status"] = pending.status if pending else None
    snapshot["state_version"] = state_version_for(db, installation_id)
    leases = activity["gpu_leases"]
    snapshot["resource"] = {
        "active_leases": len([lease for lease in leases if lease["status"] == "ACTIVE"]),
        "reconcile_leases": len([lease for lease in leases if lease["status"] == "RECONCILE_REQUIRED"]),
        "waiting_leases": 0,
    }
    snapshot["ownership"] = {
        "managed": bool(live_image == "RUNNING" or active_h3),
        # "verified" is an OS fact, not the presence of a record: PID + birth +
        # named Job Object membership must agree.
        "verified": bool(live_image == "RUNNING" or h3_verified),
        "port": port,
        "pid": listener_pid or (process or {}).get("pid"),
    }
    diagnostic_h3 = next((record for record in activity["h3_records"]
                          if record.get("missing_nodes") or record.get("reason")), None)
    missing = (diagnostic_h3 or {}).get("missing_nodes") or []
    reason = (diagnostic_h3 or {}).get("reason")
    known_reasons = {"COMFY_HANDOFF_OWNERSHIP_UNVERIFIED", "COMFY_STARTUP_OWNERSHIP_UNVERIFIED",
                     "COMFY_REQUIRED_NODE_MISSING", "COMFY_STARTUP_ALREADY_RECORDED"}
    reason_code = reason if isinstance(reason, str) and reason in known_reasons else None
    if missing:
        reason_code = "COMFY_REQUIRED_NODE_MISSING"
    missing = sorted({name for name in missing if isinstance(name, str) and len(name) <= 160})
    snapshot["diagnostics"] = {
        "transport": snapshot["health"]["comfy_http"],
        "ownership": "VERIFIED" if snapshot["ownership"]["verified"] else (
            "EXTERNAL" if state == "EXTERNAL_CONFLICT" else "UNVERIFIED"),
        "capability": "MISSING_NODES" if missing else "LIVE_CHECK_PENDING",
        "missing_nodes": missing, "reason_code": reason_code,
        # An image engine or HTTP 200 is not H3 production readiness.
        "production_ready": False,
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

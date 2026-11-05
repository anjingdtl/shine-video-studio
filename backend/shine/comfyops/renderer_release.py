"""Release the GPU: stop the managed renderer and the local review services.

ComfyUI and the local reviewers are started by this installation inside a
named Job Object, and their identity is persisted before the process exists.
They are therefore part of the workbench, not of the machine: closing the
workbench has to close them, and a renderer that outlives the stack keeps the
model resident in GPU memory and blocks the next run.

Two independent facts must agree before a process is touched: the recorded
PID/birth identity, and the membership of the named owned group.  Anything
else is reported as unverified and never killed.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select

from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.owned_process import (
    inspect_named_group,
    named_group_active_processes,
    terminate_named_group,
)
from shine.comfyops.renderer_supervisor import windows_port_listeners
from shine.models import AppSetting, Job, utcnow

RENDERER_PREFIX = "renderer-startup:"
REVIEW_PREFIX = "review-startup:"
# A record in one of these stages already states that its process is gone.
STOPPED_STAGES = {"STOPPED", "RECONCILE_COMPLETE", "RELEASED", "FAILED"}
# A record in this stage describes an uncertain event, not a running renderer:
# it is still judged by the physical facts, because a lease may only be released
# when the renderer is provably gone.
UNCERTAIN_STAGES = {"RECONCILE_REQUIRED"}
# A job in one of these states owns the renderer while it runs.  A renderer
# that is serving an attempt is never stopped under the operator's feet.
IN_FLIGHT_JOB_STATUSES = {"RUNNING", "SUBMITTED"}


def _records(db, prefix: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AppSetting).where(AppSetting.key.like(prefix + "%"))
    ).all()
    found = []
    for row in rows:
        value = row.value if isinstance(row.value, dict) else {}
        found.append({"key": row.key, "prefix": prefix, **value})
    return found


def live_process_facts(record: dict[str, Any]) -> dict[str, Any]:
    """The physical facts about one recorded process, for other owners.

    A renderer this installation started may be owned by a different subsystem
    (the image runtime, for example).  Both need the same proof: the recorded
    PID/birth identity and the membership of the named owned group.
    """
    return _live_facts(record)


def managed_process_state(record: dict[str, Any]) -> str:
    """RUNNING / UNVERIFIED / GONE / STOPPED for one recorded process."""
    return _state_of(record, _live_facts(record))


def managed_processes(db) -> list[dict[str, Any]]:
    """Every managed renderer and review service this installation recorded."""
    records = _records(db, RENDERER_PREFIX) + _records(db, REVIEW_PREFIX)
    records.sort(key=lambda item: item.get("recorded_at") or "")
    return records


def _lease_state(db, lease_id: str | None) -> dict[str, Any]:
    if not lease_id:
        return {"lease_id": None, "lease_status": None, "job_status": None, "in_flight": False}
    lease = db.get(ResourceLease, lease_id)
    owner = lease.owner if lease else None
    job_id = owner.removeprefix("h3:") if isinstance(owner, str) and owner.startswith("h3:") else None
    job = db.get(Job, job_id) if job_id else None
    return {
        "lease_id": lease_id,
        "lease_status": lease.status if lease else None,
        "job_id": job_id,
        "job_status": job.status if job else None,
        "in_flight": bool(job and job.status in IN_FLIGHT_JOB_STATUSES),
    }


def _live_facts(record: dict[str, Any]) -> dict[str, Any]:
    """What can be proven about one recorded process right now."""
    process = record.get("process") or {}
    pid, birth = process.get("pid"), process.get("birth")
    job_name = process.get("job_name")
    facts: dict[str, Any] = {
        "pid": pid if isinstance(pid, int) else None,
        "job_name": job_name if isinstance(job_name, str) else None,
        "alive": False,
        "owned": False,
        "group_members": None,
        "port_busy": None,
    }
    if not isinstance(pid, int) or pid <= 0 or not isinstance(birth, str) or not birth:
        return facts
    try:
        identity = process_identity(pid)
    except (OSError, ValueError):
        identity = None
    facts["alive"] = identity == birth
    if facts["alive"] and isinstance(job_name, str) and job_name:
        try:
            facts["owned"] = bool(inspect_named_group(job_name, pid, birth))
        except (OSError, ValueError):
            facts["owned"] = False
        try:
            facts["group_members"] = named_group_active_processes(job_name)
        except (OSError, ValueError):
            facts["group_members"] = None
    port = record.get("port")
    if isinstance(port, int) and port > 0:
        try:
            facts["port_busy"] = bool(windows_port_listeners(port))
        except (OSError, ValueError):
            facts["port_busy"] = None
    return facts


def _state_of(record: dict[str, Any], facts: dict[str, Any]) -> str:
    stage = record.get("stage")
    if stage in STOPPED_STAGES:
        return "STOPPED"
    if stage in UNCERTAIN_STAGES and not (facts["alive"] or facts["port_busy"]
                                          or (facts["group_members"] or 0) > 0):
        # The event was uncertain, but the renderer is provably gone now.
        return "GONE"
    if facts["alive"] and facts["owned"]:
        return "RUNNING"
    if facts["alive"]:
        # The process answers but this installation cannot prove it owns it.
        return "UNVERIFIED"
    if facts["port_busy"] or (facts["group_members"] or 0) > 0:
        # Something still answers on the port or in the owned group.
        return "UNVERIFIED"
    return "GONE"


def describe_managed_processes(db) -> list[dict[str, Any]]:
    """The state of every managed renderer and review service."""
    described = []
    for record in managed_processes(db):
        facts = _live_facts(record)
        lease = _lease_state(db, record.get("lease_id"))
        described.append({
            "kind": "review" if record.get("prefix") == REVIEW_PREFIX else "renderer",
            "id": record.get("service_id") or record.get("instance_id") or record.get("key"),
            "stage": record.get("stage"),
            "state": _state_of(record, facts),
            "port": record.get("port"),
            "pid": facts["pid"],
            "recorded_at": record.get("recorded_at"),
            **lease,
        })
    return described


def _mark_instance_stopped(db, job_name: str | None) -> None:
    if not job_name:
        return
    for row in db.scalars(select(ComfyInstance)).all():
        payload = row.payload if isinstance(row.payload, dict) else {}
        process = payload.get("process") or {}
        if process.get("job_name") == job_name:
            row.payload = {**payload, "process": None}
            row.status = "STOPPED"
            db.flush()


def _release_lease(db, lease_id: str | None) -> str | None:
    """Release one lease whose renderer is gone or has just been stopped."""
    if not lease_id:
        return None
    lease = db.get(ResourceLease, lease_id)
    if not lease or lease.status == "RELEASED":
        return "already_released"
    from shine.comfyops.scheduler import ResourceScheduler

    ResourceScheduler().release(db, lease_id, lease.owner, reconciled=True)
    return "released"


def release_managed_processes(db, *, actor: str, reason: str) -> dict[str, Any]:
    """Stop every managed process this installation owns and free the memory.

    A process that is serving an attempt, or whose ownership cannot be
    proven, is reported and left alone.  The report is the operator's evidence:
    it names every process that was stopped and every one that was not.
    """
    stopped: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for record in managed_processes(db):
        facts = _live_facts(record)
        state = _state_of(record, facts)
        lease = _lease_state(db, record.get("lease_id"))
        entry = {
            "kind": "review" if record.get("prefix") == REVIEW_PREFIX else "renderer",
            "id": record.get("service_id") or record.get("instance_id") or record.get("key"),
            "stage": record.get("stage"),
            "state": state,
            "port": record.get("port"),
            "pid": facts["pid"],
            "lease_id": record.get("lease_id"),
            "lease_status": lease.get("lease_status"),
        }
        if state == "STOPPED":
            entry["outcome"] = "already_stopped"
            stopped.append(entry)
            continue
        if lease.get("in_flight"):
            entry["outcome"] = "busy"
            entry["reason"] = "an attempt is running on this renderer"
            refused.append(entry)
            continue
        if state == "GONE":
            outcome = _release_lease(db, record.get("lease_id"))
            _mark_instance_stopped(db, facts["job_name"])
            _write_stopped(db, record)
            entry["outcome"] = outcome or "recorded_stopped"
            stopped.append(entry)
            released.append({"id": entry["id"], "lease_id": record.get("lease_id"), "outcome": outcome})
            continue
        if state != "RUNNING":
            entry["outcome"] = "unverified"
            entry["reason"] = "the recorded identity does not prove this installation owns it"
            refused.append(entry)
            continue
        try:
            evidence = terminate_named_group(facts["job_name"], timeout=20)
        except (OSError, ValueError, RuntimeError) as exc:
            entry["outcome"] = "unverified"
            entry["reason"] = type(exc).__name__
            refused.append(entry)
            continue
        if evidence.get("active_processes") != 0:
            entry["outcome"] = "unverified"
            entry["reason"] = "the owned group still has members"
            refused.append(entry)
            continue
        outcome = _release_lease(db, record.get("lease_id"))
        _mark_instance_stopped(db, facts["job_name"])
        _write_stopped(db, record)
        entry["outcome"] = "stopped"
        entry["evidence"] = evidence
        stopped.append(entry)
        released.append({"id": entry["id"], "lease_id": record.get("lease_id"), "outcome": outcome})
    from shine.services import add_audit

    add_audit(db, actor, "COMFY_MANAGED_PROCESSES_RELEASED", "app_setting", "managed-processes",
              {"reason": reason, "stopped": [item.get("id") for item in stopped],
               "refused": [item.get("id") for item in refused]})
    return {"stopped": stopped, "refused": refused, "released": released, "reason": reason}


def _write_stopped(db, record: dict[str, Any]) -> None:
    row = db.get(AppSetting, record["key"])
    if row is None:
        return
    value = row.value if isinstance(row.value, dict) else {}
    row.value = {**value, "stage": "STOPPED", "stopped_at": utcnow().isoformat(),
                 "stopped_reason": record.get("stopped_reason") or "operator_release"}
    db.flush()


def residual_owned_processes(ports: list[int], *, repo_root: str | None = None) -> list[dict[str, Any]]:
    """Processes of this installation that no process record names any more.

    The launcher overwrites its process record on every start.  A reviewer
    that was started by an earlier run therefore loses its record and would
    survive every stop.  Its command line still names this installation's
    script, which is the ownership proof used here.

    Only the ports this installation uses are scanned: a process that is not
    listening for this workbench is not this workbench's business.
    """
    found: list[dict[str, Any]] = []
    if os.name != "nt" or not ports:
        return found
    import ctypes
    from ctypes import wintypes

    class Row(ctypes.Structure):
        _fields_ = [("state", wintypes.DWORD), ("local_addr", wintypes.DWORD),
                    ("local_port", wintypes.DWORD), ("remote_addr", wintypes.DWORD),
                    ("remote_port", wintypes.DWORD), ("pid", wintypes.DWORD)]

    api = ctypes.WinDLL("iphlpapi", use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                        wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.GetExtendedTcpTable.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if api.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0) != 122:
        return found
    buffer = ctypes.create_string_buffer(size.value)
    if api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0) != 0:
        return found
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = ctypes.sizeof(wintypes.DWORD)
    wanted = {int(port) for port in ports}
    seen: set[int] = set()
    for index in range(count):
        row = Row.from_buffer_copy(buffer, offset + index * ctypes.sizeof(Row))
        if int(row.local_port) not in wanted:
            continue
        pid = int(row.pid)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        command = _process_command_line(pid)
        if not command:
            continue
        if repo_root and repo_root.lower() not in command.lower():
            continue
        if "local_review_server.py" not in command and "local_review_isolated_server.py" not in command:
            continue
        found.append({"pid": pid, "port": int(row.local_port), "command": command})
    return found


def managed_review_ports(db) -> list[int]:
    """The loopback ports the managed reviewers of this installation used."""
    ports = set()
    for record in managed_processes(db):
        port = record.get("port")
        if record.get("prefix") == REVIEW_PREFIX and isinstance(port, int) and port > 0:
            ports.add(port)
    return sorted(ports)


def _process_command_line(pid: int) -> str | None:
    if os.name != "nt":
        return None
    try:
        import subprocess

        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=20, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    command = (result.stdout or "").strip()
    return command or None

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
import re
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.owned_process import (
    inspect_named_group,
    named_group_active_processes,
    terminate_named_group,
)
from shine.comfyops.renderer_supervisor import windows_port_listeners
from shine.models import AppSetting, AuditEvent, Job, JobAttempt, MediaArtifact, utcnow

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
# ``SUBMITTING`` holds the renderer between the claim and the external
# submission; ``QC_PENDING`` holds it between the artifact and the gates.
# Missing either of them let a "release" kill a renderer whose attempt had
# an unknown external outcome (audit R5).
IN_FLIGHT_JOB_STATUSES = {"SUBMITTING", "SUBMITTED", "RUNNING", "QC_PENDING"}


def _image_work_in_flight(db) -> bool:
    """Whether any image item or library render task owns renderer time.

    The image startup record carries no lease id, so the lease lookup alone
    can never mark the image renderer busy; the work itself is the proof.
    """
    from shine.runtime_orchestrator.identity import in_flight_image_work

    return bool(in_flight_image_work(db))


def _valid_process(process: Any) -> bool:
    return (
        isinstance(process, dict)
        and type(process.get("pid")) is int
        and process["pid"] > 0
        and isinstance(process.get("birth"), str)
        and bool(process["birth"])
        and isinstance(process.get("job_name"), str)
        and bool(re.fullmatch(r"Local\\ShineHandoff-[a-f0-9]{32}", process["job_name"]))
    )


def _installation_pin_matches(startup: dict[str, Any], payload: dict[str, Any]) -> bool:
    startup_pin = startup.get("installation")
    payload_pin = payload.get("installation")
    return (
        isinstance(startup_pin, dict)
        and set(startup_pin) == {"id", "sha256", "attempt_id"}
        and startup_pin == payload_pin
        and all(isinstance(startup_pin.get(key), str) and startup_pin[key]
                for key in ("id", "attempt_id"))
        and isinstance(startup_pin.get("sha256"), str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", startup_pin["sha256"]))
    )


def _handoff_restarted_record(db, record: dict[str, Any]) -> dict[str, Any] | None:
    """Rebind a stale startup PID only through its durable, exact restart proof.

    H3 review replaces the renderer process inside the same owned Job Object.
    The first-startup row intentionally remains immutable during that handoff,
    while the RESTARTED journal and ComfyInstance row record the replacement.
    This overlay is valid only when every database identity and installation
    pin agrees; physical PID, Job Object membership and port checks still run
    after the overlay before any process is stopped.
    """
    if record.get("prefix") != RENDERER_PREFIX or not record.get("instance_id"):
        return None
    if record.get("stage") not in {"RUNNING", "RECONCILE_REQUIRED"}:
        return None
    instance = db.get(ComfyInstance, record["instance_id"])
    payload = instance.payload if instance and isinstance(instance.payload, dict) else {}
    process = payload.get("process")
    if (not instance or instance.status != "RUNNING_PENDING_HEALTH"
            or payload.get("managed") is not True or payload.get("is_mock") is not False
            or instance.sha256 != graph_hash(payload) or not _valid_process(process)
            or type(payload.get("port")) is not int or payload["port"] != record.get("port")
            or payload.get("startup_id") != record.get("startup_id")
            or not _installation_pin_matches(record, payload)
            or not isinstance(payload.get("url"), str)):
        return None
    parsed = urlsplit(payload["url"])
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != payload["port"]:
        return None

    rows = db.scalars(select(AppSetting).where(AppSetting.key.like("handoff:%"))).all()
    for row in rows:
        value = row.value if isinstance(row.value, dict) else {}
        stage_valid = (value.get("stage") == "RESTARTED"
                       or (value.get("stage") == "RECONCILE_REQUIRED"
                           and value.get("last_confirmed_stage") == "RESTARTED"))
        evidence = value.get("evidence") if isinstance(value.get("evidence"), dict) else {}
        restarted = evidence.get("RESTARTED") if isinstance(evidence.get("RESTARTED"), dict) else {}
        stopped = evidence.get("STOPPED") if isinstance(evidence.get("STOPPED"), dict) else {}
        restarted_process = restarted.get("process")
        original_process = record.get("process")
        if (not stage_valid or value.get("instance_id") != record["instance_id"]
                or value.get("resource_id") != record.get("lease_id")
                or value.get("process") != original_process
                or value.get("instance_sha256") != record.get("instance_sha256")
                or stopped.get("pid") != (original_process or {}).get("pid")
                or stopped.get("active_processes") != 0
                or not _valid_process(restarted_process) or restarted_process != process
                or value.get("restarted_instance_sha256") != instance.sha256
                or value.get("job_id") is None or value.get("attempt_id") is None):
            continue
        return {
            **record,
            "process": dict(process),
            "handoff_restart_proof": {
                "attempt_id": value["attempt_id"],
                "job_id": value["job_id"],
                "resource_id": value["resource_id"],
                "instance_id": record["instance_id"],
                "restarted_instance_sha256": instance.sha256,
                "installation_id": payload["installation"]["id"],
            },
        }
    return None


def _reconcile_pre_submit_reservation(db, record: dict[str, Any], *, actor: str) -> dict[str, Any] | None:
    """Release only a matching continuation lease proven to have never submitted."""
    proof = record.get("handoff_restart_proof")
    if not isinstance(proof, dict):
        return None
    candidates = db.scalars(select(AppSetting).where(
        AppSetting.key.like("handoff-reservation:%")
    )).all()
    reservation_row = next((row for row in candidates
                            if isinstance(row.value, dict)
                            and row.value.get("old_job_id") == proof["job_id"]
                            and row.value.get("old_attempt_id") == proof["attempt_id"]
                            and row.value.get("old_lease_id") == proof["resource_id"]
                            and row.value.get("instance_id") == proof["instance_id"]), None)
    if reservation_row is None:
        return None
    reservation = reservation_row.value
    job_id = reservation_row.key.removeprefix("handoff-reservation:")
    job = db.get(Job, job_id)
    lease = db.get(ResourceLease, reservation.get("lease_id"))
    handoff_row = db.get(AppSetting, "handoff:" + proof["attempt_id"])
    handoff_value = handoff_row.value if handoff_row and isinstance(handoff_row.value, dict) else {}
    if (reservation.get("stage") != "RECONCILE_REQUIRED"
            or reservation.get("reason") != "MANAGED_RUN_INCOMPLETE"
            or reservation.get("worker_pid") != handoff_value.get("worker_pid")
            or reservation.get("worker_birth") != handoff_value.get("worker_birth")
            or reservation.get("job_id") not in {None, job_id}
            or not job or job.provider != "comfyui" or job.status != "NEEDS_REVIEW"
            or job.failure_code != "PENDING_WORKFLOW_MAPPING" or job.stale
            or job.lease_owner is not None or job.lease_expires_at is not None
            or job.artifact_id or job.provider_job_id or not lease
            or lease.owner != "h3:" + job.id or lease.status != "RECONCILE_REQUIRED"):
        return None
    old_lease = db.get(ResourceLease, proof["resource_id"])
    if (not old_lease or old_lease.owner != "h3:" + proof["job_id"]
            or old_lease.status != "RELEASED"):
        return None

    attempts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id)).all()
    if not attempts or any(
        attempt.status != "FAILED" or attempt.failure_code != "PENDING_WORKFLOW_MAPPING"
        or attempt.provider_job_id or attempt.submit_ack_at or attempt.prompt_graph_sha256
        or attempt.output_path or attempt.output_sha256 or attempt.output_bytes
        or db.get(AppSetting, "renderer-activation:" + attempt.id) is not None
        for attempt in attempts
    ):
        return None
    mapping_proof = db.scalar(select(AuditEvent.id).where(
        AuditEvent.project_id == job.project_id,
        AuditEvent.entity_type == "job",
        AuditEvent.entity_id == job.id,
        AuditEvent.event_type == "COMFY_WORKFLOW_MAPPING_PENDING",
    ).limit(1))
    submit_intent = db.scalar(select(AuditEvent.id).where(
        AuditEvent.project_id == job.project_id,
        AuditEvent.entity_type == "job",
        AuditEvent.entity_id == job.id,
        AuditEvent.event_type == "COMFY_SUBMIT_INTENT",
    ).limit(1))
    artifact = db.scalar(select(MediaArtifact.id).where(MediaArtifact.job_id == job.id).limit(1))
    if not mapping_proof or submit_intent or artifact:
        return None

    from shine.comfyops.scheduler import ResourceScheduler

    ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    reservation_row.value = {
        **reservation,
        "stage": "RECONCILE_COMPLETE",
        "reason": "PENDING_WORKFLOW_MAPPING_PROVEN_BEFORE_SUBMIT",
        "external_submission": "NOT_SENT_PROVEN",
        "reconciled_by": actor,
        "reconciled_at": utcnow().isoformat(),
    }
    from shine.services import add_audit

    add_audit(db, actor, "COMFY_HANDOFF_PRE_SUBMIT_LEASE_RELEASED", "job", job.id,
              {"lease_id": lease.id, "external_submission": "NOT_SENT_PROVEN",
               "failure_code": "PENDING_WORKFLOW_MAPPING", "renderer_stopped": True},
              project_id=job.project_id)
    return {"id": job.id, "lease_id": lease.id, "outcome": "released",
            "external_submission": "NOT_SENT_PROVEN"}


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
        "identity_known": False,
        "owned": False,
        "group_members": None,
        "port_busy": None,
    }
    if not isinstance(pid, int) or pid <= 0 or not isinstance(birth, str) or not birth:
        return facts
    try:
        identity = process_identity(pid)
        facts["identity_known"] = True
    except (OSError, ValueError):
        identity = None
    facts["alive"] = identity == birth
    if isinstance(job_name, str) and job_name:
        if facts["alive"]:
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
    if facts["alive"] and facts["owned"]:
        return "RUNNING"
    if facts["alive"]:
        # The process answers but this installation cannot prove it owns it.
        return "UNVERIFIED"
    if (facts.get("identity_known") is True and facts["port_busy"] is False
            and facts["group_members"] == 0):
        # A readable, different birth identity plus an empty owned group and
        # a free port prove that the recorded renderer is gone.  Clock expiry
        # and an unreadable process identity never do.
        return "GONE"
    # The event may be uncertain, or its physical proof may be incomplete.
    return "UNVERIFIED"


def describe_managed_processes(db) -> list[dict[str, Any]]:
    """The state of every managed renderer and review service."""
    described = []
    for original in managed_processes(db):
        record = _handoff_restarted_record(db, original) or original
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
            "ownership_evidence": ("durable_handoff_restart"
                                    if record.get("handoff_restart_proof") else "startup_record"),
            **lease,
        })
    return described


def _mark_instance_stopped(db, job_name: str | None, instance_id: str | None = None) -> None:
    """Write the instance row back to STOPPED once its renderer is gone.

    Matching the row's own ``process`` mark is no longer sufficient: the image
    runtime deliberately keeps its process in its startup record instead of on
    the shared row, so a stopped image renderer has no mark left to match.  The
    record's explicit instance id closes that gap; a row matched by job name
    still loses its stale mark, exactly as before.
    """
    if not job_name and not instance_id:
        return
    for row in db.scalars(select(ComfyInstance)).all():
        payload = row.payload if isinstance(row.payload, dict) else {}
        process = payload.get("process") or {}
        by_mark = bool(job_name and process.get("job_name") == job_name)
        by_stopped_instance = bool(instance_id and row.id == instance_id and not process)
        if not by_mark and not by_stopped_instance:
            continue
        if by_mark:
            row.payload = {**payload, "process": None}
            row.sha256 = graph_hash(row.payload)
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
    for original in managed_processes(db):
        record = _handoff_restarted_record(db, original) or original
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
        if record.get("handoff_restart_proof"):
            entry["ownership_evidence"] = "durable_handoff_restart"
        if state == "STOPPED":
            # The startup record is the proof the renderer is gone; the shared
            # instance row must still follow it, or the next start is refused
            # with INSTANCE_NOT_STOPPED forever.
            _mark_instance_stopped(db, facts["job_name"], record.get("instance_id"))
            entry["outcome"] = "already_stopped"
            stopped.append(entry)
            continue
        if lease.get("in_flight"):
            entry["outcome"] = "busy"
            entry["reason"] = "an attempt is running on this renderer"
            refused.append(entry)
            continue
        if record.get("purpose") == "image" and _image_work_in_flight(db):
            entry["outcome"] = "busy"
            entry["reason"] = "an image task is running on this renderer"
            refused.append(entry)
            continue
        if state == "GONE":
            outcome = _release_lease(db, record.get("lease_id"))
            _mark_instance_stopped(db, facts["job_name"], record.get("instance_id"))
            _write_stopped(db, record)
            entry["outcome"] = outcome or "recorded_stopped"
            stopped.append(entry)
            released.append({"id": entry["id"], "lease_id": record.get("lease_id"), "outcome": outcome})
            continuation_release = _reconcile_pre_submit_reservation(db, record, actor=actor)
            if continuation_release:
                released.append(continuation_release)
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
        stopped_facts = _stopped_process_facts(record)
        if not all(stopped_facts.values()):
            entry["outcome"] = "unverified"
            entry["reason"] = "renderer exit or port release is not fully proven"
            entry["stop_checks"] = stopped_facts
            refused.append(entry)
            continue
        outcome = _release_lease(db, record.get("lease_id"))
        _mark_instance_stopped(db, facts["job_name"], record.get("instance_id"))
        _write_stopped(db, record)
        entry["outcome"] = "stopped"
        entry["evidence"] = evidence
        entry["stop_checks"] = stopped_facts
        stopped.append(entry)
        released.append({"id": entry["id"], "lease_id": record.get("lease_id"), "outcome": outcome})
        continuation_release = _reconcile_pre_submit_reservation(db, record, actor=actor)
        if continuation_release:
            released.append(continuation_release)
    from shine.services import add_audit

    add_audit(db, actor, "COMFY_MANAGED_PROCESSES_RELEASED", "app_setting", "managed-processes",
              {"reason": reason, "stopped": [item.get("id") for item in stopped],
               "refused": [item.get("id") for item in refused]})
    return {"stopped": stopped, "refused": refused, "released": released, "reason": reason}


def _stopped_process_facts(record: dict[str, Any]) -> dict[str, bool]:
    """Recheck PID identity, named group and port after a terminate call."""
    process = record.get("process") or {}
    pid, birth, job_name = process.get("pid"), process.get("birth"), process.get("job_name")
    if type(pid) is not int or not isinstance(birth, str) or not isinstance(job_name, str):
        return {"process_exited": False, "group_empty": False, "port_free": False}
    try:
        process_exited = process_identity(pid) != birth
    except (OSError, ValueError):
        process_exited = False
    try:
        group_empty = named_group_active_processes(job_name) == 0
    except (OSError, ValueError):
        group_empty = False
    port = record.get("port")
    try:
        port_free = type(port) is int and port > 0 and not windows_port_listeners(port)
    except (OSError, ValueError):
        port_free = False
    return {"process_exited": process_exited, "group_empty": group_empty, "port_free": port_free}


def _write_stopped(db, record: dict[str, Any]) -> None:
    row = db.get(AppSetting, record["key"])
    if row is None:
        return
    value = row.value if isinstance(row.value, dict) else {}
    row.value = {
        **value,
        "stage": "STOPPED",
        "process": dict(record["process"]) if isinstance(record.get("process"), dict) else value.get("process"),
        "stopped_at": utcnow().isoformat(),
        "stopped_reason": record.get("stopped_reason") or "operator_release",
        **({"resolved_from_handoff_restart": record["handoff_restart_proof"]}
           if isinstance(record.get("handoff_restart_proof"), dict) else {}),
    }
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

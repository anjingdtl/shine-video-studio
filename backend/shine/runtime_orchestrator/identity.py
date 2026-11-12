"""Read-only facts about what is active on one physical ComfyUI installation.

Three identities stay distinct (plan §3.1):

* ``installation_identity`` — the durable ``comfy_instances`` row of the
  adopted local installation; the scope of every lifecycle decision;
* ``runtime_identity`` — the per-run/per-use identity (an H3 attempt's run
  row); never a lock key;
* ``process_identity`` — PID/birth/named Job Object; re-proven before any
  destructive action by the existing OwnedProcessGroup helpers.

This module answers "what can be proven to be running on, or holding, this
installation right now" from the durable records the subsystems already
write: H3 run startup records, the image renderer record, GPU leases and
in-flight jobs.  It derives nothing twice — it reads the existing tables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from shine.comfyops.models import ResourceLease
from shine.models import AppSetting, Job
from shine.runtime_orchestrator.contracts import IMAGE_STARTUP_PREFIX

STARTUP_PREFIX = "renderer-startup:"
# Stages that describe a live or starting renderer; everything else is
# history (STOPPED / FAILED / RELEASED / RECONCILE_COMPLETE) or an uncertain
# record judged by its own physical facts.
ACTIVE_STAGES = {"INTENT", "LAUNCHED", "LISTENING", "RUNNING"}
UNCERTAIN_STAGES = {"RECONCILE_REQUIRED"}

IN_FLIGHT_JOB_STATUSES = {"SUBMITTING", "SUBMITTED", "RUNNING", "QC_PENDING"}


def _record_state(record: dict[str, Any]) -> str:
    stage = record.get("stage")
    if stage in ACTIVE_STAGES:
        return "ACTIVE" if stage == "RUNNING" else "STARTING"
    if stage in UNCERTAIN_STAGES:
        return "UNCERTAIN"
    return "INACTIVE"


def h3_records_for_installation(db, installation_id: str) -> list[dict[str, Any]]:
    """Every H3 run startup record bound to this installation."""
    rows = db.scalars(
        select(AppSetting).where(AppSetting.key.like(STARTUP_PREFIX + "%"))
    ).all()
    found = []
    for row in rows:
        if not row.key.startswith(STARTUP_PREFIX):
            continue
        key_tail = row.key[len(STARTUP_PREFIX):]
        if key_tail.startswith("image:"):
            continue
        value = row.value if isinstance(row.value, dict) else {}
        bound = (value.get("installation") or {}).get("id")
        if bound == installation_id:
            found.append(
                {
                    "key": row.key,
                    "run_id": key_tail,
                    "state": _record_state(value),
                    "stage": value.get("stage"),
                    "lease_id": value.get("lease_id"),
                    "job_id": value.get("job_id"),
                    "purpose": value.get("purpose") or "h3",
                    # Carried so a status read can re-prove the process without
                    # a second query; never used as an authority on its own.
                    "port": value.get("port"),
                    "process": value.get("process"),
                }
            )
    return found


def verified_live_record(record: dict[str, Any]) -> dict[str, Any]:
    """PID/birth/named-group facts for one startup record, read-only.

    A startup record is intent plus history; only these facts may ever be used
    to call a renderer "running" (plan §3.3).  Imported lazily so the control
    plane keeps working when comfyops cannot load its Windows helpers.
    """
    from shine.comfyops.renderer_release import live_process_facts

    return live_process_facts(record)


def image_record_for_installation(db, installation_id: str) -> dict[str, Any] | None:
    row = db.get(AppSetting, IMAGE_STARTUP_PREFIX + installation_id)
    if row is None or not isinstance(row.value, dict):
        return None
    value = row.value
    from shine.comfyops.renderer_release import managed_process_state

    return {
        "key": row.key,
        "state": _record_state(value),
        # The record says "RUNNING"; only the OS proves it.  Both are returned
        # so a conflict decision can stay conservative (stage) while the status
        # snapshot reports what is physically verifiable (live_state).
        "live_state": managed_process_state(value),
        "stage": value.get("stage"),
        "port": value.get("port"),
        "process": value.get("process"),
        "adapter_sha256": value.get("adapter_sha256"),
        "purpose": "image",
    }


def lease_facts(db, *, exclude_lease_id: str | None = None) -> list[dict[str, Any]]:
    """ACTIVE / RECONCILE_REQUIRED leases (the GPU is held or uncertain)."""
    rows = db.scalars(
        select(ResourceLease).where(ResourceLease.status.in_(("ACTIVE", "RECONCILE_REQUIRED")))
    ).all()
    return [
        {
            "lease_id": row.id,
            "owner": row.owner,
            "status": row.status,
        }
        for row in rows
        if row.id != exclude_lease_id
    ]


def in_flight_h3_jobs(db) -> list[dict[str, Any]]:
    rows = db.scalars(select(Job).where(Job.status.in_(IN_FLIGHT_JOB_STATUSES))).all()
    return [{"job_id": row.id, "status": row.status} for row in rows]


def in_flight_image_work(db) -> list[dict[str, Any]]:
    """Image items and asset-center tasks that own renderer time right now.

    An image item renders inside a resource slot; a library render task holds
    a submit intent or an external prompt id while it runs.  Both mean "the
    image renderer is not idle" and must stop a stop/release under them.
    """
    work: list[dict[str, Any]] = []
    from shine.materialprep.models import MaterialItem

    rows = db.scalars(
        select(MaterialItem).where(
            MaterialItem.status.in_(("SUBMIT_INTENT", "RUNNING", "OUTPUT_STORED", "QC_PENDING"))
        )
    ).all()
    work.extend({"kind": "material_item", "id": row.id} for row in rows)
    from shine.asset_center.models import LibraryRenderTask

    tasks = db.scalars(
        select(LibraryRenderTask).where(LibraryRenderTask.status.in_(("RUNNING", "SUBMITTED")))
    ).all()
    work.extend({"kind": "library_task", "id": row.id} for row in tasks)
    return work


def installation_activity(db, installation_id: str, *, exclude_lease_id: str | None = None) -> dict[str, Any]:
    """Everything an executor must respect before a lifecycle decision."""
    h3 = [record for record in h3_records_for_installation(db, installation_id)
          if record["state"] in ("ACTIVE", "STARTING", "UNCERTAIN")]
    image = image_record_for_installation(db, installation_id)
    leases = lease_facts(db, exclude_lease_id=exclude_lease_id)
    jobs = in_flight_h3_jobs(db)
    image_work = in_flight_image_work(db)
    image_state = image["state"] if image else "INACTIVE"
    if image_state in ("ACTIVE", "STARTING", "UNCERTAIN"):
        renderer_state = "IMAGE"
    elif any(r["state"] in ("ACTIVE", "STARTING") for r in h3):
        renderer_state = "H3"
    elif any(r["state"] == "UNCERTAIN" for r in h3):
        renderer_state = "UNCERTAIN_H3"
    else:
        renderer_state = "NONE"
    return {
        "installation_id": installation_id,
        "renderer_state": renderer_state,
        "h3_records": h3,
        "image_record": image if image_state in ("ACTIVE", "STARTING", "UNCERTAIN") else None,
        "gpu_leases": leases,
        "in_flight_h3_jobs": jobs,
        "in_flight_image_work": image_work,
    }

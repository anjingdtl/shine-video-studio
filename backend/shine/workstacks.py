"""One read-only view of every work stack this machine is running.

A "work stack" is a background unit the workbench itself launched or drives:
the H3 renderer, the local review services, the resident worker and the shots
it is producing right now.  The monitor is machine-wide on purpose: a renderer
started by another project is exactly the stack that keeps the GPU busy while
the current project looks idle.

Nothing here starts, stops or loads anything.  Closing a stack is a separate,
explicit action that goes through the same evidence-checked release path as
「关闭本机后台」.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from shine.models import Job, Project, WorkerInstance, utcnow

#: A worker whose heartbeat is older than this is not serving work any more.
WORKER_LIVENESS_SECONDS = 30

WORKER_ONLINE = {"RUNNING", "SUBMITTING", "SUBMITTED", "QC_PENDING", "CLAIMED"}

RENDERER_LABELS = {
    "renderer": "H3 生成服务",
    "review": "本机审片服务",
}

STATE_LABELS = {
    "RUNNING": "正在运行",
    "GONE": "进程已不在",
    "STOPPED": "已停止",
    "UNVERIFIED": "无法确认归属",
}


def _project_titles(db) -> dict[str, str]:
    return {row.id: row.title for row in db.scalars(select(Project))}


def _job_facts(db) -> list[dict[str, Any]]:
    titles = _project_titles(db)
    rows: list[dict[str, Any]] = []
    for job in db.scalars(select(Job).where(Job.status.in_(WORKER_ONLINE))):
        rows.append({
            "job_id": job.id,
            "project_id": job.project_id,
            "project_title": titles.get(job.project_id),
            "shot_id": job.shot_id,
            "status": job.status,
            "provider": job.provider,
            "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        })
    return rows


def _worker_facts(db) -> list[dict[str, Any]]:
    titles = _project_titles(db)
    now = utcnow()
    rows: list[dict[str, Any]] = []
    for worker in db.scalars(select(WorkerInstance)):
        heartbeat = worker.last_heartbeat_at
        age = (now - heartbeat).total_seconds() if heartbeat else None
        online = age is not None and age <= WORKER_LIVENESS_SECONDS
        current_job = db.get(Job, worker.current_job_id) if worker.current_job_id else None
        rows.append({
            "worker_id": worker.worker_id,
            "pid": worker.pid,
            "online": online,
            "phase": worker.current_phase,
            "current_job_id": worker.current_job_id,
            "project_id": current_job.project_id if current_job else None,
            "project_title": titles.get(current_job.project_id) if current_job else None,
            "heartbeat_age_s": round(age, 1) if age is not None else None,
            "processed_count": worker.processed_count,
            "last_error": worker.last_error,
        })
    rows.sort(key=lambda row: (not row["online"], row["worker_id"]))
    return rows


def work_stack_snapshot(db, settings=None) -> dict[str, Any]:
    """Everything that is running, with the honest state of each unit."""
    stacks: list[dict[str, Any]] = []
    try:
        from shine.comfyops.renderer_release import describe_managed_processes

        processes = describe_managed_processes(db)
    except Exception as exc:  # noqa: BLE001 - an unreadable list is a fact, not a crash
        processes = []
        process_error = str(exc)[:200]
    else:
        process_error = None

    titles = _project_titles(db)
    for item in processes:
        state = str(item.get("state") or "UNVERIFIED")
        kind = str(item.get("kind") or "renderer")
        working = state == "RUNNING"
        job_id = item.get("job_id")
        job = db.get(Job, job_id) if job_id else None
        stacks.append({
            "id": f"{kind}:{item.get('id') or item.get('pid') or 'unknown'}",
            "kind": kind,
            "label": RENDERER_LABELS.get(kind, kind),
            "state": state,
            "state_label": STATE_LABELS.get(state, state),
            "working": working,
            # 已停止的记录是历史台账，不是正在工作的栈：默认收进折叠区。
            "historical": state == "STOPPED",
            "port": item.get("port"),
            "pid": item.get("pid"),
            "lease_status": item.get("lease_status"),
            "job_id": job_id,
            "job_status": item.get("job_status"),
            "project_id": job.project_id if job else None,
            "project_title": titles.get(job.project_id) if job else None,
            "in_flight": bool(item.get("in_flight")),
            "closeable": state != "UNVERIFIED" and not item.get("in_flight"),
            "close_blocked_reason": (
                "正在出片，不能强制中断" if item.get("in_flight")
                else "无法确认这个进程属于本工作台，不能关闭" if state == "UNVERIFIED"
                else None
            ),
        })

    jobs = _job_facts(db)
    workers = _worker_facts(db)
    running = [row for row in jobs]

    return {
        "checked_at": utcnow().isoformat(),
        "stacks": stacks,
        "workers": workers,
        "jobs": jobs,
        "counts": {
            "stacks": len(stacks),
            "working_stacks": sum(1 for row in stacks if row["working"]),
            "historical_stacks": sum(1 for row in stacks if row["historical"]),
            "workers_online": sum(1 for row in workers if row["online"]),
            "running_jobs": len(running),
        },
        "process_error": process_error,
    }

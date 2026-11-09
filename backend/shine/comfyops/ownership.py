"""Who is shooting this shot right now?

The single-shot Job entry, the production Series and the Run dispatch all ask
the same question before they start anything: *does this shot already belong
to a shoot that is still alive?*  One answer keeps them symmetric.  A shot a
series reserved but has not queued yet is already taken, and a shot whose last
external outcome is unknown stays taken until that outcome was reconciled.

Nothing here queues, cancels or repairs anything.  It only answers, so every
entry can refuse a second active shoot with the same reason.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: The series states that own their shots.
LIVE_SERIES_STATUSES = ("QUEUED", "RUNNING", "PAUSED", "PAUSE_REQUESTED")

#: The job states that own their shot.
LIVE_JOB_STATUSES = ("PLANNED", "QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING")


def shot_ownership(db: Session, project_id: str, shot_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The live owner of every shot in the list, when there is one.

    A series reservation counts even while its job does not exist yet: the
    Worker creates that job when it reaches the shot, and a second entry must
    not take the shot in the meantime.
    """
    from shine.models import Job, ProductionSeries, ProductionSeriesItem

    wanted = [str(shot_id) for shot_id in shot_ids]
    if not wanted:
        return {}
    owned: dict[str, dict[str, Any]] = {}

    rows = db.execute(
        select(ProductionSeriesItem.shot_id, ProductionSeriesItem.job_id,
               ProductionSeriesItem.status, ProductionSeries.id, ProductionSeries.status)
        .join(ProductionSeries, ProductionSeries.id == ProductionSeriesItem.series_id)
        .where(
            ProductionSeries.project_id == project_id,
            ProductionSeries.status.in_(LIVE_SERIES_STATUSES),
            ProductionSeriesItem.shot_id.in_(wanted),
        )
    ).all()
    for shot_id, item_job_id, item_status, series_id, series_status in rows:
        owned[str(shot_id)] = {
            "kind": "series",
            "series_id": series_id,
            "job_id": item_job_id,
            "item_status": item_status,
            "status": series_status,
        }

    jobs = db.scalars(
        select(Job).where(
            Job.project_id == project_id,
            Job.shot_id.in_(wanted),
            Job.stale.is_(False),
            Job.status.in_(LIVE_JOB_STATUSES),
        ).order_by(Job.created_at)
    ).all()
    for job in jobs:
        current = owned.get(str(job.shot_id))
        if current is None:
            owned[str(job.shot_id)] = {
                "kind": "job",
                "job_id": job.id,
                "status": job.status,
            }
        elif current.get("kind") == "series" and not current.get("job_id"):
            current["job_id"] = job.id
            current["status"] = job.status
    return owned


def ownership_refusal(shot_id: str, owner: dict[str, Any]) -> dict[str, Any]:
    """The refusal a second entry gets, naming the task that owns the shot."""
    if owner.get("kind") == "series":
        message = "这个镜头已经属于一次连续制作（生产序列），请在那次任务里看它的进度"
    else:
        message = "这个镜头已经在一次拍摄中，请等这次拍摄结束或先处理它"
    return {
        "code": "SHOT_ALREADY_IN_PRODUCTION",
        "message": message,
        "shot_id": shot_id,
        "series_id": owner.get("series_id"),
        "job_id": owner.get("job_id"),
        "status": owner.get("status"),
        "action": "FOLLOW_THE_EXISTING_SHOOT",
    }

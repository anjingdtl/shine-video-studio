"""Bounded, explicitly requested machine QC over an existing immutable render.

This route cannot approve a shot or submit ComfyUI. It queues reconciliation of
the same current Attempt/Artifact, preserving every earlier QC report and gate.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from shine.api_common import DBSession, app_settings
from shine.config import Settings
from shine.models import AppSetting, QualityGate
from shine.operator_review import context
from shine.services import add_audit

router = APIRouter(prefix="/api/v1", tags=["machine-review"])


class ReviewRecheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_version: int = Field(ge=0)
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")


@router.post("/jobs/{job_id}/review/recheck", status_code=202)
def recheck(job_id: str, body: ReviewRecheck, db: DBSession, settings: Annotated[Settings, Depends(app_settings)]):
    db.execute(text("BEGIN IMMEDIATE"))
    job, attempt, artifact, _request, _path, _binding = context(db, settings, job_id)
    if job.state_version != body.state_version or job.failure_code != "QC_NEEDS_REVIEW":
        raise HTTPException(409, "QC_RECHECK_STATE_CHANGED")
    latest = {
        g.gate_name: g
        for g in db.scalars(
            select(QualityGate)
            .where(
                QualityGate.job_id == job.id,
                QualityGate.attempt_id == attempt.id,
            )
            .order_by(QualityGate.created_at)
        )
    }
    if any(g.status == "failed" for g in latest.values()):
        raise HTTPException(409, "QC_RECHECK_CANNOT_CLEAR_HARD_FAILURE")
    key = "qc-recheck:" + job.id
    row = db.get(AppSetting, key)
    previous = row.value if row else {}
    if previous.get("request_id") == body.request_id:
        raise HTTPException(409, "QC_RECHECK_REQUEST_REPLAYED")
    if previous.get("count", 0) >= 2:
        raise HTTPException(409, "QC_RECHECK_BUDGET_EXHAUSTED")
    value = {
        "count": previous.get("count", 0) + 1,
        "request_id": body.request_id,
        "artifact_id": artifact.id,
        "artifact_sha256": artifact.sha256,
        "attempt_id": attempt.id,
    }
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    job.failure_code = "QC_RECHECK_REQUESTED"
    job.state_version += 1
    add_audit(
        db,
        "user",
        "MACHINE_QC_RECHECK_REQUESTED",
        "job",
        job.id,
        {**value, "render_resubmission": False},
        project_id=job.project_id,
    )
    db.commit()
    return {
        "status": "QC_RECHECK_REQUESTED",
        "job_id": job.id,
        "state_version": job.state_version,
        "attempt_id": attempt.id,
        "artifact_id": artifact.id,
        "render_resubmission": False,
    }

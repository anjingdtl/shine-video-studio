"""Bind independent text proposals to exact production inputs; never approve media."""

import hashlib

from fastapi import HTTPException
from sqlalchemy import literal_column, select

from shine.agents.film import load_inputs, validate_output
from shine.agents.film_contracts import FilmRunRequest, StyleInputAssessment
from shine.h3.rulebook import digest
from shine.models import AgentArtifact, AgentRoute, AgentTaskRecord, ProviderProfile, StyleVersion
from shine.schemas import RenderRequest
from shine.security import is_loopback_url


def assessment_for_request(db, project_id, request, *, offline=False, required=False, select_latest=False):
    request = RenderRequest.model_validate(request)
    if bool(request.style_input_assessment_id) != bool(request.style_input_assessment_sha256):
        raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_PIN_INCOMPLETE")
    row = db.get(AgentArtifact, request.style_input_assessment_id) if request.style_input_assessment_id else None
    tasks = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == "qc_reviewer",
    ).order_by(AgentTaskRecord.created_at.desc(), literal_column("agent_task_records.rowid").desc()))
    latest = next((task for task in tasks if (
        task.trace.get("shot_spec_sha256"), task.trace.get("style_hash")
    ) == (digest(request.shot_spec.model_dump(mode="json")), request.style_hash)), None)
    if latest:
        # A rejected/unknown newest call has no artifact. Looking only at artifacts
        # would silently fall back to an older positive result.
        if latest.status != "SUCCEEDED":
            raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_NOT_READY")
        if row and row.source_task_id != latest.id:
            raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_OUTDATED")
        if not row and select_latest and not request.style_input_assessment_id:
            row = db.scalar(select(AgentArtifact).where(
                AgentArtifact.source_task_id == latest.id, AgentArtifact.kind == "StyleInputAssessment",
            ))
            if not row:
                raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_NOT_READY")
    if not row:
        if required or request.style_input_assessment_id:
            raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_REQUIRED")
        return None
    try:
        if row.kind != "StyleInputAssessment" or row.producer_role != "qc_reviewer":
            raise ValueError("wrong assessment kind")
        load_inputs(db, project_id, [row.id])
        task = db.get(AgentTaskRecord, row.source_task_id)
        style = db.get(StyleVersion, request.style_version_id)
        if (task.role_id != "qc_reviewer" or task.trace.get("scope") != "TEXT_INPUT_CONSISTENCY_ONLY_NOT_MEDIA_QC"
                or task.trace.get("shot_spec_sha256") != digest(request.shot_spec.model_dump(mode="json"))
                or task.trace.get("style_hash") != request.style_hash
                or task.style_version_id != request.style_version_id
                or digest(task.trace.get("shot_spec_snapshot")) != task.trace.get("shot_spec_sha256")
                or task.trace.get("output_hash") != row.sha256
                or not style or style.project_id != project_id or style.sha256 != request.style_hash
                or style.snapshot != request.style_snapshot.model_dump(mode="json")
                or task.trace.get("text_input_sha256") != digest({
                    "style": request.style_snapshot.model_dump(mode="json"),
                    "style_version_id": request.style_version_id, "style_hash": request.style_hash,
                    "shot_spec": request.shot_spec.model_dump(mode="json"),
                })
                or (request.style_input_assessment_sha256 and row.sha256 != request.style_input_assessment_sha256)):
            raise ValueError("assessment input changed")
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == "qc_reviewer"))
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        if (not profile or not profile.enabled or task.model_profile_id != profile.id or task.model_id != profile.model
                or task.trace.get("review_provider") != {
                    "profile_id": profile.id, "kind": profile.kind, "model": profile.model,
                    "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest(),
                }):
            raise ValueError("assessment route changed")
        if not offline and (task.trace.get("is_mock") is not False or task.trace.get("offline_preview")):
            raise ValueError("offline assessment is not real evidence")
        if (profile.kind != "mock" and not is_loopback_url(profile.base_url)
                and task.trace.get("remote_text_consent") is not True):
            raise ValueError("remote text consent missing")
        proposal = StyleInputAssessment.model_validate(row.payload_json)
        validate_output("qc_reviewer", proposal, FilmRunRequest(shot_spec=request.shot_spec), [], {
            "style": request.style_snapshot.model_dump(mode="json"),
            "shot_spec": request.shot_spec.model_dump(mode="json"),
        })
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(409, "STYLE_INPUT_ASSESSMENT_OUTDATED") from exc
    if proposal.recommendation != "proceed_to_preflight":
        raise HTTPException(409, "STYLE_INPUT_CONFLICT_NEEDS_REVIEW")
    return row

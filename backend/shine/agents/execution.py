"""Conservative film-agent outcome accounting and remote-only abandonment."""

import hashlib

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from shine.agents.film_contracts import FilmAgentRegistry, StyleInputWire
from shine.comfyops import approval
from shine.comfyops.models import ResourceLease
from shine.h3.rulebook import digest
from shine.llm.base import text_provider_snapshot
from shine.models import AgentTaskRecord, ProviderProfile
from shine.security import is_loopback_url
from shine.services import add_audit

UNKNOWN_CODES = {"LLM_TIMEOUT", "LLM_CONNECTION", "LLM_SERVER"}
SAFE_REJECTIONS = {
    "STYLE_INPUT_FIELD_COVERAGE", "STYLE_INPUT_RECOMMENDATION_CONTRADICTION",
    "STYLE_INPUT_FROZEN_QUOTE_CHANGED", "STYLE_INPUT_SHOT_QUOTE_INVENTED",
    "INPUT_CONTEXT_CHANGED_DURING_CALL",
    "STYLE_INPUT_SHOT_PATH_INVALID",
    "LOCKED_FACTS_CHANGED", "DUPLICATE_BEAT", "DIALOGUE_NOT_IN_USER_SCRIPT",
    "DUPLICATE_SHOT_INTENT", "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE",
    "EDITORIAL_TIMING_CONFLICT",
    "EDITORIAL_TIMING_UNGROUPABLE", "EDITORIAL_BEAT_COUNT_MISMATCH",
    "UNAPPROVED_ASSET_OR_UNKNOWN_BEAT", "CHARACTER_MASTER_BINDING_REQUIRED",
    "CAMERA_INTENT_COVERAGE", "EDIT_INTENT_INTENT_COVERAGE",
    "REPAIR_CHANGED_LOCKED_SHOT_STRUCTURE", "REPAIR_DID_NOT_CHANGE_PLAN",
    "H3_LOCKED_FACT_TRANSLATION_REQUIRED",
    "H3_STYLE_TRANSLATION_REQUIRED",
}


def rejection_reason(exc):
    """Never store exception text or a provider's rejected output."""
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_url=False):
            code = str((error.get("ctx") or {}).get("error", ""))
            if code in SAFE_REJECTIONS:
                return code
        return "SCHEMA_VALIDATION_FAILED"
    return str(exc) if str(exc) in SAFE_REJECTIONS else "SCHEMA_OR_POLICY_REJECTED"


def guard_request(db, project_id, role, input_hash, profile, *, text_hash=None, contract_hash=None,
                  replace_interrupted_task_id=None):
    if replace_interrupted_task_id is not None:
        raise ValueError("AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == role,
    ))
    for row in rows:
        same = (row.trace.get("text_input_sha256") == text_hash if text_hash else row.input_hash == input_hash)
        if not same:
            continue
        if row.status in {"REJECTED", "FAILED", "ABANDONED"} and row.trace.get("is_mock") is not True:
            if (row.trace.get("reconciliation") or {}).get("outcome") == "NOT_SENT_PROVEN":
                continue
            external_outcome = row.trace.get("external_outcome")
            if external_outcome == "UNKNOWN":
                raise ValueError("AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
            if external_outcome == "NOT_SENT":
                raise ValueError("AGENT_REQUEST_RECONCILIATION_REQUIRED")
            if external_outcome == "RESPONSE_RECEIVED":
                raise ValueError("AGENT_RESPONSE_RECEIVED_NO_REPLAY")
            raise ValueError("AGENT_REQUEST_RECONCILIATION_REQUIRED")
        old_contract = row.trace.get("review_contract_sha256")
        if row.status in {"RUNNING", "NEEDS_REVIEW", "RECONCILING"}:
            raise ValueError("AGENT_REQUEST_RECONCILIATION_REQUIRED")
        binding = row.trace.get("review_provider") or {}
        same_provider = (
            {key: binding.get(key) for key in ("kind", "model", "base_url_sha256")} == {
                "kind": profile.kind, "model": profile.model,
                "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest(),
            } if binding else row.model_profile_id == profile.id and row.model_id == profile.model
        )
        if (row.status == "ABANDONED" and same_provider
                and not (contract_hash and old_contract and old_contract != contract_hash)):
            raise ValueError("AGENT_REQUEST_REPLAY_BLOCKED")


def abandon_remote_response(db, row, actor, grant_id, snapshot, evidence):
    if row.role_id not in FilmAgentRegistry.roles or not (
        row.status == "NEEDS_REVIEW"
        or row.status == "REJECTED" and row.error_code in UNKNOWN_CODES
        and row.trace.get("external_outcome") != "NOT_SENT"
    ):
        raise HTTPException(409, "REMOTE_AGENT_OUTCOME_NOT_UNCERTAIN")
    binding = row.trace.get("review_provider") or {}
    profile = db.get(ProviderProfile, binding.get("profile_id"))
    if (not profile or profile.kind == "mock" or is_loopback_url(profile.base_url)
            or row.trace.get("is_mock") is True):
        raise HTTPException(409, "LOCAL_AGENT_RELEASE_REQUIRES_SEPARATE_RECONCILIATION")
    if binding != text_provider_snapshot(profile):
        raise HTTPException(409, "REMOTE_AGENT_PROVIDER_CHANGED")
    if db.scalar(select(ResourceLease.id).where(ResourceLease.status != "RELEASED")):
        raise HTTPException(409, "ACTIVE_RESOURCE_PREVENTS_RECONCILIATION")
    approval.consume(db, actor, "model_reconcile", row, grant_id, snapshot)
    previous = row.status
    row.status = "ABANDONED"
    row.state_version += 1
    # The local wait is verifiably over: no resource lease is active (guard
    # above) and the task is no longer running. Record it so the single-use
    # replacement path can engage without an out-of-band database edit.
    row.trace = {**row.trace, "prior_status_before_abandon": previous,
                 "external_outcome": "UNKNOWN", "automatic_replay": False,
                 "local_model_unload_claimed": False, "reconciled_by": actor,
                 "remote_response_abandon_evidence": evidence,
                 "local_interruption_verified": True,
                 "local_interruption_evidence": {
                     "verified_by": "abandon_remote_response",
                     "no_active_resource_leases": True,
                     "prior_status": previous,
                 }}
    if row.role_id == "qc_reviewer" and not row.trace.get("review_contract_sha256"):
        row.trace = {**row.trace, "review_contract_sha256": digest(StyleInputWire.model_json_schema())}
    add_audit(db, actor, "REMOTE_AGENT_RESPONSE_ABANDONED", "agent_task", row.id,
              {"prior_status": previous, "external_outcome": "UNKNOWN", "inference_calls": 0,
               "local_model_unload_claimed": False, "quality_approval": False}, project_id=row.project_id)
    db.commit()
    return {"id": row.id, "status": row.status, "external_outcome": "UNKNOWN", "automatic_replay": False}

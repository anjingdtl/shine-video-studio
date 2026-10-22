"""Source-specific consent and one-use execution authority shared by all entry points."""

from fastapi import HTTPException

from shine.comfyops import approval
from shine.comfyops.assets import require_rights, verify_candidate
from shine.comfyops.models import CandidateAsset


def execution_context(body):
    return body.model_dump(exclude={"execution_decision"})


def authorize(db, project_id, body, actor, settings):
    scope = "voice_clone" if body.source == "AUTHORIZED_CLONE" else "voice_design"
    rights = require_rights(db, project_id, body.rights_id, scope)
    require_rights(db, project_id, body.asset_rights_id)
    reference = None
    if body.source == "ORIGINAL_DESIGN":
        if body.reference_id:
            raise HTTPException(409, "VOICE_SOURCE_REFERENCE_MISMATCH")
    else:
        reference = db.get(CandidateAsset, body.reference_id)
        if not reference or reference.project_id != project_id or reference.payload["kind"] != "audio":
            raise HTTPException(409, "CROSS_PROJECT_OR_NON_AUDIO_REFERENCE")
        if body.source == "USER_IMPORT" and reference.payload.get("provenance", {}).get("source") == "AUTHORIZED_CLONE":
            raise HTTPException(409, "VOICE_SOURCE_CONSENT_MISMATCH")
        require_rights(db, project_id, reference.payload["rights_id"])
        verify_candidate(settings, reference)
        if body.source == "AUTHORIZED_CLONE":
            if rights.payload.get("reference_candidate_id") != reference.id:
                raise HTTPException(409, "VOICE_CONSENT_REQUIRED")
            if not actor:
                raise HTTPException(403, "VOICE_EXECUTION_OPERATOR_REQUIRED")
            if not body.execution_decision:
                raise HTTPException(409, "VOICE_EXECUTION_GRANT_REQUIRED")
            decision = body.execution_decision
            approval.consume(
                db,
                actor,
                "voice_execute",
                rights,
                decision.grant_id,
                decision.snapshot,
                context=execution_context(body),
            )
    return reference

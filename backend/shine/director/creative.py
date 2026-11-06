"""The two entries into one script pipeline: an idea, or an imported script.

Entry A (idea): the person describes what they want.  A typed ``CreativeBrief``
is persisted as a *versioned* ``AgentArtifact`` they can edit and confirm, and
a bounded text task turns a confirmed brief into a real ``ProjectDocument``.
From there the pipeline is exactly the one an imported script uses: outline
extraction, confirmation, sections, storyboard.  There is no second script
paradigm and no second prompt path.

Entry B (import) is the existing document upload and is not duplicated here.

Text leaving the machine follows the same rule as the document path: a remote
text model needs the person's explicit consent for *this brief* on *this
provider*.  The consent is pinned to the provider fingerprint when the task is
queued and re-checked immediately before the call, so a changed route is
refused instead of silently switching provider.  An uncertain outcome is never
replayed: the task goes to NEEDS_REVIEW.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.director.contracts import digest_of
from shine.models import AgentArtifact, AgentTaskRecord, Project, ProjectDocument, utcnow
from shine.schemas import APIModel

logger = logging.getLogger(__name__)

BRIEF_KIND = "CreativeBrief"
SCRIPT_TASK_ROLE_ID = "creative_script_run"
SCRIPT_AGENT_ROLE = "scriptwriter"
MAX_SCRIPT_CHARS = 60_000
SCRIPT_CONTRACT_VERSION = "creative-script-v1"


class CreativeBriefPayload(APIModel):
    """One editable brief version.  Every save is a new version."""

    idea: str = Field(min_length=1, max_length=8000)
    genre: str = Field(default="", max_length=200)
    audience: str = Field(default="", max_length=200)
    theme: str = Field(default="", max_length=400)
    core_conflict: str = Field(default="", max_length=800)
    ending_direction: str = Field(default="", max_length=400)
    language: str = Field(default="Chinese", min_length=2, max_length=32)
    aspect_ratio: str = Field(default="16:9", max_length=16)
    episode_count_hint: int | None = Field(default=None, ge=1, le=200)
    target_seconds_per_episode: int = Field(default=60, ge=8, le=3600)
    tolerance_seconds: int = Field(default=15, ge=0, le=900)
    locked_facts: list[str] = Field(default_factory=list, max_length=30)
    forbidden_changes: list[str] = Field(default_factory=list, max_length=30)
    visual_direction: str = Field(default="", max_length=1000)


class ScriptDraft(APIModel):
    """What the scriptwriter must return: a title and the full script text."""

    model_config = {**APIModel.model_config, "extra": "ignore"}

    title: str = Field(min_length=1, max_length=200)
    script_text: str = Field(min_length=1, max_length=MAX_SCRIPT_CHARS)


SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 200},
        "script_text": {"type": "string", "maxLength": MAX_SCRIPT_CHARS},
    },
    "required": ["title", "script_text"],
}


# ---------------------------------------------------------------------------
# Brief versions
# ---------------------------------------------------------------------------


def brief_rows(db: Session, project_id: str) -> list[AgentArtifact]:
    return list(db.scalars(
        select(AgentArtifact)
        .where(AgentArtifact.project_id == project_id, AgentArtifact.kind == BRIEF_KIND)
        .order_by(AgentArtifact.created_at.desc())
    ).all())


def save_brief(db: Session, project: Project, payload: CreativeBriefPayload, *,
               actor: str = "local-user") -> AgentArtifact:
    """Persist a new brief version; earlier versions stay as history.

    Every artifact in the ledger names the task that produced it.  A brief is
    written by a person, so its producing record is an explicit human authoring
    entry — never a model call.
    """
    version = len(brief_rows(db, project.id)) + 1
    body = {**payload.model_dump(mode="json"), "version": version}
    sha = digest_of(body)
    authoring = AgentTaskRecord(
        request_identity=digest_of({"role": "creative_brief_authoring", "project": project.id,
                                    "version": version, "sha256": sha}),
        project_id=project.id,
        role_id="creative_brief_authoring",
        input_hash=sha,
        asset_manifest_hash=sha,
        rulepack_id="creative-brief-v1",
        rulepack_hash=digest_of("creative-brief-v1"),
        model_profile_id="human",
        model_id="human",
        schema_version="1.0",
        status="SUCCEEDED",
        trace={"authored_by": actor, "version": version, "external_outcome": "NOT_SENT"},
    )
    db.add(authoring)
    db.flush()
    artifact = AgentArtifact(
        project_id=project.id,
        kind=BRIEF_KIND,
        schema_version="1.0",
        payload_json=body,
        sha256=sha,
        producer_role="user",
        source_task_id=authoring.id,
    )
    db.add(artifact)
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "CREATIVE_BRIEF_SAVED", "agent_artifact", artifact.id,
              {"version": version, "sha256": artifact.sha256}, project_id=project.id)
    return artifact


def confirm_brief(db: Session, artifact: AgentArtifact, *, actor: str,
                  expected_sha256: str | None = None) -> AgentArtifact:
    if artifact.kind != BRIEF_KIND:
        raise HTTPException(404, "CREATIVE_BRIEF_NOT_FOUND")
    if artifact.stale:
        raise HTTPException(409, "CREATIVE_BRIEF_STALE")
    if expected_sha256 and expected_sha256 != artifact.sha256:
        raise HTTPException(409, "CREATIVE_BRIEF_CHANGED")
    if artifact.approved_at:
        return artifact
    artifact.approved_by = actor
    artifact.approved_at = utcnow()
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "CREATIVE_BRIEF_CONFIRMED", "agent_artifact", artifact.id,
              {"sha256": artifact.sha256}, project_id=artifact.project_id)
    return artifact


def _task_public(task: AgentTaskRecord | None) -> dict[str, Any] | None:
    if task is None:
        return None
    trace = task.trace or {}
    return {
        "id": task.id,
        "status": task.status,
        "error_code": task.error_code,
        "document_id": trace.get("document_id"),
        "document_sha256": trace.get("document_sha256"),
        "external_outcome": trace.get("external_outcome"),
        "created_at": task.created_at.isoformat() + "Z",
    }


def brief_public(db: Session, artifact: AgentArtifact) -> dict[str, Any]:
    task = db.scalars(
        select(AgentTaskRecord)
        .where(AgentTaskRecord.role_id == SCRIPT_TASK_ROLE_ID,
               AgentTaskRecord.input_hash == artifact.sha256,
               AgentTaskRecord.project_id == artifact.project_id)
        .order_by(AgentTaskRecord.created_at.desc())
    ).first()
    return {
        "id": artifact.id,
        "version": (artifact.payload_json or {}).get("version", 0),
        "payload": artifact.payload_json,
        "sha256": artifact.sha256,
        "confirmed": bool(artifact.approved_at),
        "stale": artifact.stale,
        "created_at": artifact.created_at.isoformat() + "Z",
        "confirmed_at": artifact.approved_at.isoformat() + "Z" if artifact.approved_at else None,
        "script_task": _task_public(task),
    }


# ---------------------------------------------------------------------------
# Brief → script task
# ---------------------------------------------------------------------------


def _gateway(settings):
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore

    return LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))


def _provider_is_local(profile) -> bool:
    from shine.security import is_loopback_url

    return profile.kind == "mock" or is_loopback_url(profile.base_url)


def queue_script_generation(
    db: Session,
    project: Project,
    brief: AgentArtifact,
    *,
    settings,
    remote_text_consent: bool = False,
    actor: str = "local-user",
    gateway=None,
) -> AgentTaskRecord:
    """Queue the bounded brief→script task.  The Worker performs the call."""
    from shine.materialprep import text_consent

    if brief.kind != BRIEF_KIND:
        raise HTTPException(404, "CREATIVE_BRIEF_NOT_FOUND")
    if not brief.approved_at:
        raise HTTPException(409, "CREATIVE_BRIEF_NOT_CONFIRMED")
    if brief.stale:
        raise HTTPException(409, "CREATIVE_BRIEF_STALE")
    gateway = gateway or _gateway(settings)
    try:
        profile = gateway.profile_for_role(db, SCRIPT_AGENT_ROLE)
    except Exception as exc:  # noqa: BLE001 - a missing route is a readable refusal
        raise HTTPException(409, "CREATIVE_SCRIPT_ROLE_NOT_CONFIGURED") from exc
    local = _provider_is_local(profile)
    if not local and not remote_text_consent:
        # The brief is the person's own text; sending it to a remote model is
        # still their decision, made for this brief on this provider.
        raise HTTPException(409, {"code": "REMOTE_AGENT_CONSENT_REQUIRED",
                                  "provider_kind": profile.kind})
    fingerprint = text_consent.fingerprint(profile)
    request_identity = digest_of({
        "role": SCRIPT_TASK_ROLE_ID,
        "brief_artifact_id": brief.id,
        "brief_sha256": brief.sha256,
        "project_id": project.id,
        "provider": fingerprint,
    })
    existing = db.scalars(
        select(AgentTaskRecord).where(AgentTaskRecord.request_identity == request_identity)
    ).first()
    if existing is not None:
        return existing
    task = AgentTaskRecord(
        request_identity=request_identity,
        project_id=project.id,
        role_id=SCRIPT_TASK_ROLE_ID,
        input_hash=brief.sha256,
        asset_manifest_hash=request_identity,
        rulepack_id=SCRIPT_CONTRACT_VERSION,
        rulepack_hash=digest_of(SCRIPT_CONTRACT_VERSION),
        model_profile_id=profile.id,
        model_id=profile.model,
        schema_version="1.0",
        status="QUEUED",
        trace={
            "stage": "DRAFT",
            "brief_artifact_id": brief.id,
            "brief_sha256": brief.sha256,
            "contract_version": SCRIPT_CONTRACT_VERSION,
            "provider_fingerprint": fingerprint,
            "consent": {
                "required": not local,
                "granted": bool(local or remote_text_consent),
                "scope": "brief_text_only_no_asset_bytes",
            },
        },
    )
    db.add(task)
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "CREATIVE_SCRIPT_QUEUED", "agent_task", task.id,
              {"brief_artifact_id": brief.id, "brief_sha256": brief.sha256,
               "provider_fingerprint": fingerprint, "remote": not local},
              project_id=project.id)
    return task


def _script_prompt(brief_payload: dict[str, Any], project: Project) -> tuple[str, dict[str, Any]]:
    system = (
        "You are the scriptwriter of a short-video studio. Write a complete, filmable "
        "screenplay from the confirmed creative brief, in the brief's language. Keep every "
        "user-locked fact exactly as written and never touch a forbidden change. Structure "
        "the text with scene headings (第X场：场景名), action lines and dialogue lines "
        "(角色名：台词) so a scene splitter can cut it into production sections. Each beat "
        "must be shootable as a 4-15 second single take: short spoken lines, one main action "
        "per beat, at most three speaking characters per scene. Do not write camera "
        "directions or production notes and do not change the ending direction. If the brief "
        "is ambiguous, choose the most literal reading and keep it simple."
    )
    context = {
        "project_title": project.title,
        "brief": brief_payload,
        "limits": {"max_chars": MAX_SCRIPT_CHARS, "single_take_seconds": [4, 15]},
    }
    return system, context


def _consent_guard(task_id: str, expected_fingerprint: str, gateway):
    from shine.materialprep import text_consent

    def verify(db):
        profile = gateway.profile_for_role(db, SCRIPT_AGENT_ROLE)
        task = db.get(AgentTaskRecord, task_id)
        consent = ((task.trace or {}).get("consent") or {}) if task else {}
        if (task is None or text_consent.fingerprint(profile) != expected_fingerprint
                or not consent.get("granted")):
            raise text_consent.TextConsentRequired(
                "Text consent missing or the route changed since the request",
                details={"external_outcome": "NOT_SENT"},
            )
    return verify


def run_pending_creative_task(session_factory, settings, worker_id: str, *,
                              heartbeat_fn=None, gateway=None) -> bool:
    """Claim and run at most one queued brief→script task.

    The claim is a conditional UPDATE on the task's own version, so two workers
    can never both write the script.  Returns whether a task was handled.
    """
    from shine.materialprep.agent_call import call_material_agent
    from shine.security import sha256_bytes

    with session_factory() as db:
        task = db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.role_id == SCRIPT_TASK_ROLE_ID, AgentTaskRecord.status == "QUEUED")
            .order_by(AgentTaskRecord.created_at)
            .limit(1)
        ).first()
        if task is None:
            return False
        task_id, version = task.id, task.state_version
    with session_factory.begin() as db:
        claimed = db.execute(
            update(AgentTaskRecord)
            .where(AgentTaskRecord.id == task_id,
                   AgentTaskRecord.status == "QUEUED",
                   AgentTaskRecord.state_version == version)
            .values(status="RUNNING", lease_owner=worker_id,
                    state_version=AgentTaskRecord.state_version + 1)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            return False

    def finish(status: str, error_code: str | None, **trace_updates) -> None:
        with session_factory.begin() as db:
            row = db.get(AgentTaskRecord, task_id)
            row.status = status
            row.error_code = error_code
            row.trace = {**(row.trace or {}), **trace_updates}
            row.state_version += 1

    with session_factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        trace = dict(task.trace or {})
        brief = db.get(AgentArtifact, trace.get("brief_artifact_id") or "")
        project = db.get(Project, task.project_id)
        if (brief is None or brief.stale or not brief.approved_at
                or brief.sha256 != trace.get("brief_sha256") or project is None):
            finish("FAILED", "CREATIVE_BRIEF_CHANGED", external_outcome="NOT_SENT")
            return True
        system, context = _script_prompt(brief.payload_json or {}, project)
    if heartbeat_fn is not None:
        heartbeat_fn()
    gateway = gateway or _gateway(settings)
    outcome = call_material_agent(
        gateway, session_factory, SCRIPT_AGENT_ROLE,
        system=system, context=context, schema=SCRIPT_SCHEMA, contract=ScriptDraft,
        before_call=_consent_guard(task_id, trace.get("provider_fingerprint") or "", gateway),
    )
    if not outcome.ok:
        # An uncertain outcome may already have reached the provider: it goes
        # to review and is never replayed under a new request.
        finish("NEEDS_REVIEW" if outcome.uncertain else "FAILED",
               (outcome.error_code or "CREATIVE_SCRIPT_FAILED")[:64],
               external_outcome="UNKNOWN" if outcome.uncertain else "NOT_SENT",
               automatic_replay=False)
        return True
    draft = ScriptDraft.model_validate(outcome.parsed)
    text = draft.script_text.strip()
    with session_factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        brief = db.get(AgentArtifact, row.trace.get("brief_artifact_id"))
        document = ProjectDocument(
            project_id=row.project_id,
            filename=f"{draft.title.strip()[:60] or '创意剧本'}-创意版.txt",
            content_type="text/plain",
            sha256=sha256_bytes(text.encode("utf-8")),
            source_format="idea",
            text=text,
            parsed={
                "provenance": "creative_brief",
                "brief_artifact_id": brief.id,
                "brief_sha256": brief.sha256,
                "task_id": row.id,
                "contract_version": SCRIPT_CONTRACT_VERSION,
                "correction_used": outcome.correction_used,
            },
        )
        db.add(document)
        db.flush()
        row.status = "SUCCEEDED"
        row.error_code = None
        row.trace = {**row.trace, "document_id": document.id, "document_sha256": document.sha256,
                     "external_outcome": "COMPLETED"}
        row.state_version += 1
        from shine.services import add_audit

        add_audit(db, "worker", "CREATIVE_SCRIPT_WRITTEN", "project_document", document.id,
                  {"brief_artifact_id": brief.id, "sha256": document.sha256,
                   "chars": len(text)}, project_id=row.project_id)
    return True

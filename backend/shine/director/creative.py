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
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from typing import Any

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
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


CREATIVE_NEXT_STEPS = {
    "QUEUED": "已排队，常驻 Worker 会接着处理；关闭页面不会中断后台工作。",
    "RUNNING": "Worker 正在生成剧本。",
    "SUCCEEDED": "剧本已写入文档，可在「总体大纲」继续统筹。",
    "FAILED": "生成失败。确认原因后可重新发起一次生成。",
    "NEEDS_REVIEW": "这次请求的结果需要人工核对：不会自动重发。",
}


def _task_public(task: AgentTaskRecord | None) -> dict[str, Any] | None:
    """What the workbench shows about one brief→script task.

    The person must be able to tell "still working" from "the worker was lost
    and this needs checking" from "it failed", and must be told what to do
    next.  A task that is merely running is never the end of the story.
    """
    if task is None:
        return None
    trace = task.trace or {}
    status = task.status
    stage = trace.get("stage") or STAGE_DRAFT
    next_step = CREATIVE_NEXT_STEPS.get(status, "")
    if status == "RUNNING" and stage == STAGE_SENDING:
        next_step = ("请求已经发出，正在等待结果。若 Worker 中断，结果需要人工核对，不会自动重发。")
    elif status == "RUNNING" and stage == STAGE_RESPONSE:
        next_step = "结果已保存，正在写入文档。"
    return {
        "id": task.id,
        "status": status,
        "stage": stage,
        "error_code": task.error_code,
        "document_id": trace.get("document_id"),
        "document_sha256": trace.get("document_sha256"),
        "external_outcome": trace.get("external_outcome"),
        "reconciliation": trace.get("reconciliation"),
        "next_step": next_step,
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


CREATIVE_LEASE_MINUTES = 30
"""How long one worker's claim on a brief→script task stays valid."""

STAGE_DRAFT = "DRAFT"
"""No request was sent: the task is provably un-started and may be sent."""

STAGE_SENDING = "SENDING"
"""The send intent is durable.  If the worker dies here the request may or may
not have reached the provider, so the outcome is *unknown* and never replayed."""

STAGE_RESPONSE = "RESPONSE"
"""The provider answered and the answer is durable.  The document has not been
written yet, so the stored answer is reused instead of paying for a second
model call."""

STAGE_DOCUMENT = "DOCUMENT"
"""The script document is in the database; the task only has to be closed."""

DRAFT_KIND = "CreativeScriptDraft"
"""The durable answer of the scriptwriter, before it becomes a document."""


class _FenceLost(Exception):
    """The task is no longer owned by this worker; nothing may be written."""


def _consent_guard(task_id: str, expected_fingerprint: str, gateway, *,
                   version: int | None = None, worker_id: str | None = None):
    """Check consent, the provider route and this worker's ownership.

    The check runs immediately before the request leaves the machine.  A task
    whose lease was taken over must never reach the provider, and a changed
    route must be refused rather than silently switching provider.
    """
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
        if version is not None and worker_id is not None:
            if (task.state_version != version or task.lease_owner != worker_id
                    or task.status != "RUNNING"):
                # The lease expired or another worker took over while the
                # request was being prepared: this worker sends nothing.
                raise text_consent.TextConsentRequired(
                    "This task is no longer owned by this worker",
                    details={"external_outcome": "NOT_SENT", "code": "CREATIVE_TASK_NOT_OWNED"},
                )
    return verify


def _claim_creative_task(session_factory, worker_id: str):
    """Take one queued or expired brief→script task.

    An expired claim means *this needs checking*, not *the request was never
    sent*.  A task whose send intent is durable is reconciled, never replayed.
    The claim itself is a conditional UPDATE on the task's own version, so two
    workers can never both own the same script.
    """
    now = utcnow()
    for _ in range(3):
        with session_factory() as db:
            task = db.scalars(
                select(AgentTaskRecord)
                .where(
                    AgentTaskRecord.role_id == SCRIPT_TASK_ROLE_ID,
                    or_(
                        AgentTaskRecord.status == "QUEUED",
                        (AgentTaskRecord.status == "RUNNING") & (
                            AgentTaskRecord.lease_expires_at.is_(None)
                            | (AgentTaskRecord.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(AgentTaskRecord.created_at)
                .limit(1)
            ).first()
            if task is None:
                return None
            task_id, version = task.id, task.state_version
            trace = dict(task.trace or {})
            # The stable request identity travels with the stage so a
            # reconciled task can be traced back to the exact request.
            trace["request_identity"] = task.request_identity
        with session_factory.begin() as db:
            claimed = db.execute(
                update(AgentTaskRecord)
                .where(
                    AgentTaskRecord.id == task_id,
                    AgentTaskRecord.state_version == version,
                    or_(
                        AgentTaskRecord.status == "QUEUED",
                        (AgentTaskRecord.status == "RUNNING") & (
                            AgentTaskRecord.lease_expires_at.is_(None)
                            | (AgentTaskRecord.lease_expires_at <= now)
                        ),
                    ),
                )
                .values(status="RUNNING", lease_owner=worker_id,
                        lease_expires_at=now + timedelta(minutes=CREATIVE_LEASE_MINUTES),
                        state_version=AgentTaskRecord.state_version + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount == 1:
                return task_id, version + 1, trace
    return None


@contextmanager
def _renew_creative_claim(session_factory, task_id, version, worker_id, heartbeat_fn=None, interval=10):
    """Renew only this fenced claim; a changed owner or version ends renewal."""
    stop = Event()

    def renew():
        try:
            with session_factory.begin() as db:
                changed = db.execute(update(AgentTaskRecord).where(
                    AgentTaskRecord.id == task_id,
                    AgentTaskRecord.state_version == version,
                    AgentTaskRecord.lease_owner == worker_id,
                    AgentTaskRecord.status == "RUNNING",
                ).values(lease_expires_at=utcnow() + timedelta(minutes=CREATIVE_LEASE_MINUTES))).rowcount
            if not changed:
                stop.set()
                return
            if heartbeat_fn:
                heartbeat_fn()
        except OperationalError:
            # A busy SQLite writer does not prove the claim was lost.
            logger.warning("creative_heartbeat_deferred database_busy")

    def loop():
        while not stop.wait(interval):
            renew()

    renew()
    thread = Thread(target=loop, name="shine-creative-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _finish_task(session_factory, task_id, version, worker_id, trace,
                 status: str = "QUEUED", error: str | None = None) -> bool:
    """Close a task under the full fence: status, owner and version must match.

    A worker whose lease was taken over cannot write back: the conditional
    UPDATE matches nothing and the task keeps the new owner's decision.
    """
    with session_factory.begin() as db:
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id,
            AgentTaskRecord.state_version == version,
            AgentTaskRecord.lease_owner == worker_id,
            AgentTaskRecord.status == "RUNNING",
        ).values(trace=trace, status=status, error_code=error,
                 lease_owner=None, lease_expires_at=None,
                 state_version=version + 1)).rowcount
        return bool(changed)


def _persist_send_intent(session_factory, task_id, version, worker_id, trace) -> dict[str, Any] | None:
    """Record the intent to send *before* the external call happens.

    A worker that dies after this point leaves an unknown outcome, never a
    silent replay.  The task version is not bumped: it identifies the claim,
    and the fence keeps working for the whole claim.
    """
    updated = {**trace, "stage": STAGE_SENDING, "external_outcome": "SENDING",
               "sent_at": utcnow().isoformat() + "Z",
               "request_identity": trace.get("request_identity")}
    with session_factory.begin() as db:
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id,
            AgentTaskRecord.state_version == version,
            AgentTaskRecord.lease_owner == worker_id,
            AgentTaskRecord.status == "RUNNING",
        ).values(trace=updated)).rowcount
    return updated if changed == 1 else None


def _persist_response(session_factory, task_id, version, worker_id, trace, draft, outcome) -> dict[str, Any] | None:
    """Store the provider's answer durably before any document is written.

    The answer is a versioned artifact, so a crash between "the model answered"
    and "the document exists" costs no second model call: the next worker
    reuses the stored answer.
    """
    payload = {"title": draft.title, "script_text": draft.script_text}
    try:
        with session_factory.begin() as db:
            row = db.get(AgentTaskRecord, task_id)
            if row is None:
                return None
            artifact = AgentArtifact(
                project_id=row.project_id,
                kind=DRAFT_KIND,
                schema_version="1.0",
                payload_json=payload,
                sha256=digest_of(payload),
                producer_role=SCRIPT_AGENT_ROLE,
                source_task_id=task_id,
                input_revision_ids=[trace["brief_artifact_id"]] if trace.get("brief_artifact_id") else [],
            )
            db.add(artifact)
            db.flush()
            updated = {**trace, "stage": STAGE_RESPONSE,
                       "draft_artifact_id": artifact.id,
                       "draft_sha256": artifact.sha256,
                       "correction_used": bool(outcome.correction_used),
                       "external_outcome": "COMPLETED"}
            changed = db.execute(update(AgentTaskRecord).where(
                AgentTaskRecord.id == task_id,
                AgentTaskRecord.state_version == version,
                AgentTaskRecord.lease_owner == worker_id,
                AgentTaskRecord.status == "RUNNING",
            ).values(trace=updated)).rowcount
            if changed != 1:
                raise _FenceLost()
    except _FenceLost:
        return None
    return updated


def _write_script_document(session_factory, task_id, version, worker_id, trace) -> bool:
    """Write the script document exactly once, under the full task fence.

    The document and the task's terminal state commit together, so a task can
    never claim success without a document and a document can never be written
    twice.  A worker whose lease was taken over writes nothing at all.
    """
    from shine.security import sha256_bytes
    from shine.services import add_audit

    artifact_id = trace.get("draft_artifact_id")
    try:
        with session_factory.begin() as db:
            row = db.get(AgentTaskRecord, task_id)
            if row is None:
                return False
            draft = db.get(AgentArtifact, artifact_id) if artifact_id else None
            if draft is None:
                changed = db.execute(update(AgentTaskRecord).where(
                    AgentTaskRecord.id == task_id,
                    AgentTaskRecord.state_version == version,
                    AgentTaskRecord.lease_owner == worker_id,
                    AgentTaskRecord.status == "RUNNING",
                ).values(status="FAILED", error_code="CREATIVE_DRAFT_MISSING",
                         trace={**trace, "external_outcome": "COMPLETED"},
                         lease_owner=None, lease_expires_at=None,
                         state_version=version + 1)).rowcount
                return bool(changed)
            payload = draft.payload_json or {}
            title = str(payload.get("title") or "").strip()[:60]
            body = str(payload.get("script_text") or "").strip()
            brief = db.get(AgentArtifact, trace.get("brief_artifact_id") or "")
            existing_id = trace.get("document_id") or ""
            document = db.get(ProjectDocument, existing_id) if existing_id else None
            if document is None:
                document = ProjectDocument(
                    project_id=row.project_id,
                    filename=f"{title or '创意剧本'}-创意版.txt",
                    content_type="text/plain",
                    sha256=sha256_bytes(body.encode("utf-8")),
                    source_format="idea",
                    text=body,
                    parsed={
                        "provenance": "creative_brief",
                        "brief_artifact_id": brief.id if brief else None,
                        "brief_sha256": brief.sha256 if brief else None,
                        "task_id": row.id,
                        "contract_version": SCRIPT_CONTRACT_VERSION,
                        "correction_used": bool(trace.get("correction_used")),
                    },
                )
                db.add(document)
                db.flush()
            changed = db.execute(update(AgentTaskRecord).where(
                AgentTaskRecord.id == task_id,
                AgentTaskRecord.state_version == version,
                AgentTaskRecord.lease_owner == worker_id,
                AgentTaskRecord.status == "RUNNING",
            ).values(status="SUCCEEDED", error_code=None,
                     trace={**trace, "stage": STAGE_DOCUMENT,
                            "document_id": document.id,
                            "document_sha256": document.sha256,
                            "external_outcome": "COMPLETED"},
                     lease_owner=None, lease_expires_at=None,
                     state_version=version + 1)).rowcount
            if changed != 1:
                raise _FenceLost()
            add_audit(db, "worker", "CREATIVE_SCRIPT_WRITTEN", "project_document", document.id,
                      {"brief_artifact_id": brief.id if brief else None,
                       "sha256": document.sha256, "chars": len(body)},
                      project_id=row.project_id)
    except _FenceLost:
        # The lease was taken over: this worker writes nothing, so the task
        # cannot be revived and no duplicate document can appear.
        return False
    return True


def run_pending_creative_task(session_factory, settings, worker_id: str, *,
                              heartbeat_fn=None, gateway=None) -> bool:
    """Claim and run at most one queued brief→script task.

    The claim is a conditional UPDATE on the task's own version, so two workers
    can never both write the script.  Returns whether a task was handled.

    The durable stage decides what a reclaimed task means:

    * ``DRAFT``    — nothing was sent; the request may still be sent.
    * ``SENDING``  — the send intent is durable; the outcome is unknown, so
      the task goes to review and is never replayed.
    * ``RESPONSE`` — the answer is durable; it is reused, not re-bought.
    * ``DOCUMENT`` — the script is in the database; the task is just closed.
    """
    claimed = _claim_creative_task(session_factory, worker_id)
    if claimed is None:
        return False
    task_id, version, trace = claimed
    gateway = gateway or _gateway(settings)

    from shine.models import ProviderProfile

    with session_factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        pinned = db.get(ProviderProfile, task.model_profile_id) if task and task.model_profile_id else None
        pinned_model = task.model_id if task else None
    if pinned is None or not pinned.enabled or not pinned.model.strip() or (
            pinned_model and pinned.model != pinned_model):
        _finish_task(session_factory, task_id, version, worker_id,
                     {**trace, "external_outcome": "NOT_SENT"},
                     "NEEDS_REVIEW", "CREATIVE_SCRIPT_PIN_INVALID")
        return True
    # The task continues on the model it was queued with, even when the route
    # has moved on; the next task follows the new route (J05).
    gateway.pin_role(SCRIPT_AGENT_ROLE, pinned)
    try:
        return _run_claimed_creative(
            session_factory, settings, worker_id, task_id, version, trace,
            heartbeat_fn=heartbeat_fn, gateway=gateway)
    finally:
        gateway.pin_role(SCRIPT_AGENT_ROLE, None)


def _run_claimed_creative(session_factory, settings, worker_id, task_id, version, trace, *,
                          heartbeat_fn=None, gateway=None) -> bool:
    from shine.materialprep.agent_call import call_material_agent

    if gateway is None:
        gateway = _gateway(settings)
    with _renew_creative_claim(session_factory, task_id, version, worker_id, heartbeat_fn):
        stage = trace.get("stage") or STAGE_DRAFT

        # The document is already written: only the task has to close.
        if stage == STAGE_DOCUMENT and trace.get("document_id"):
            _finish_task(session_factory, task_id, version, worker_id,
                         {**trace, "stage": STAGE_DOCUMENT, "external_outcome": "COMPLETED"},
                         "SUCCEEDED")
            return True

        # A durable answer is reused; the model is never called a second time.
        if stage == STAGE_RESPONSE and trace.get("draft_artifact_id"):
            return _write_script_document(session_factory, task_id, version, worker_id, trace)

        # The send intent is durable: the outcome may already have reached the
        # provider.  It is reconciled, never replayed under a new identity.
        if stage == STAGE_SENDING:
            _finish_task(session_factory, task_id, version, worker_id,
                         {**trace, "stage": STAGE_SENDING, "external_outcome": "UNKNOWN",
                          "reconciliation": "SEND_OUTCOME_UNKNOWN",
                          "automatic_replay": False},
                         "NEEDS_REVIEW", "CREATIVE_SCRIPT_OUTCOME_UNKNOWN")
            return True

        with session_factory() as db:
            task = db.get(AgentTaskRecord, task_id)
            brief = db.get(AgentArtifact, trace.get("brief_artifact_id") or "")
            project = db.get(Project, task.project_id) if task else None
            if (task is None or brief is None or brief.stale or not brief.approved_at
                    or brief.sha256 != trace.get("brief_sha256") or project is None):
                _finish_task(session_factory, task_id, version, worker_id,
                             {**trace, "external_outcome": "NOT_SENT"},
                             "FAILED", "CREATIVE_BRIEF_CHANGED")
                return True
            system, context = _script_prompt(brief.payload_json or {}, project)
        if heartbeat_fn is not None:
            heartbeat_fn()
        gateway = gateway or _gateway(settings)

        # The send intent is durable BEFORE the external call.  A worker that
        # dies here leaves an unknown outcome, never a silent replay.
        sending = _persist_send_intent(session_factory, task_id, version, worker_id, trace)
        if sending is None:
            return True
        outcome = call_material_agent(
            gateway, session_factory, SCRIPT_AGENT_ROLE,
            system=system, context=context, schema=SCRIPT_SCHEMA, contract=ScriptDraft,
            before_call=_consent_guard(task_id, trace.get("provider_fingerprint") or "", gateway,
                                       version=version, worker_id=worker_id),
        )
        if not outcome.ok:
            if outcome.error_code == "CREATIVE_TASK_NOT_OWNED":
                # A newer owner took over: this worker writes nothing.
                return True
            external_outcome = outcome.details.get("external_outcome", "UNKNOWN")
            uncertain = outcome.uncertain or external_outcome == "UNKNOWN"
            # Only a provider transport proof of NOT_SENT permits another
            # request. A received but invalid answer is not a replay grant.
            _finish_task(session_factory, task_id, version, worker_id,
                         {**sending,
                          "external_outcome": external_outcome,
                          "automatic_replay": False},
                         "NEEDS_REVIEW" if uncertain else "FAILED",
                         (outcome.error_code or "CREATIVE_SCRIPT_FAILED")[:64])
            return True
        draft = ScriptDraft.model_validate(outcome.parsed)
        stored = _persist_response(session_factory, task_id, version, worker_id, sending, draft, outcome)
        if stored is None:
            return True
        return _write_script_document(session_factory, task_id, version, worker_id, stored)

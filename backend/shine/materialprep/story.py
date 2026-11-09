"""Long-document work units on AgentTaskRecord and the resident Worker.

Production sections are source ranges (chapters/scene groups). They are never
H3 generation segments. Model subcalls have their own durable input identity;
an expired orchestration claim cannot authorise replay of an unknown subcall.
"""

import hashlib
import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import OperationalError

from shine.h3.rulebook import digest
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import scenes, text_consent
from shine.materialprep.agent_call import budget_report, call_material_agent
from shine.materialprep.source_spans import HEADING, segment_document
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, ProviderProfile, utcnow
from shine.schemas import APIModel
from shine.services import add_audit

OUTLINE_RUN = "story_outline_run"
#: How long a claimed unit may go without a renewal before another Worker may
#: take it over.  The renewal thread keeps this alive during a model call.
STORY_CLAIM_SECONDS = 120
SECTION_RUN = "story_section_run"
OUTLINE_TOOL = "story_outline_merge"
VERSION = "story-outline-v1"
CONTINUITY_KIND = "SectionContinuityState"


class OutlinePart(APIModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=1200)
    story_line: list[Annotated[str, Field(max_length=300)]] = Field(max_length=12)
    timeline: list[Annotated[str, Field(max_length=300)]] = Field(max_length=16)
    relationships: list[Annotated[str, Field(max_length=300)]] = Field(max_length=24)
    questions: list[Annotated[str, Field(max_length=300)]] = Field(max_length=12)


def source_sections(project_id, document):
    import re

    source = document.text or ""
    boundaries = [0]
    titles = {}
    for line in re.finditer(r"[^\n]*\n|[^\n]+$", source):
        if HEADING.match(line.group()) or re.match(r"\s*第[^\n]{1,20}[章节回卷]", line.group()):
            boundaries.append(line.start())
            titles[line.start()] = line.group().strip()[:100]
    boundaries = sorted({*boundaries, len(source)})
    return [{"production_section_id": str(uuid5(NAMESPACE_URL,
                f"shine:{project_id}:{titles.get(left, 'preamble')}:"
                f"{sum(titles.get(other, 'preamble') == titles.get(left, 'preamble')
                       for other in boundaries[:index])}")),
             "order": index + 1, "title": titles.get(left, "前言与人物设定" if left == 0 else "生产段落"),
             "source_sha256": hashlib.sha256(source[left:right].encode()).hexdigest(),
             "start_offset": left, "end_offset": right,
             "estimated_duration_s": round((right - left) / 5, 1),
             "estimate_basis": "原文字数/5，仅供规划，不是 H3 时长或实测",
             "status": "OUTLINE_CONFIRMATION_REQUIRED"}
            for index, (left, right) in enumerate(zip(boundaries, boundaries[1:], strict=False)) if right > left]


#: A queued task may only be handed back when it can still move - or when
#: it already produced the answer.  A cancelled or failed attempt can never
#: continue, so returning it would make the button a dead end.
REUSABLE_TASK_STATUSES = ("QUEUED", "RUNNING", "PAUSED", "NEEDS_REVIEW", "SUCCEEDED")


def queue_outline(db, project_id, document, gateway, facts):
    profile = gateway.profile_for_role(db, "scriptwriter")
    fingerprint = text_consent.fingerprint(profile)
    base = {"project": project_id, "document": document.id, "hash": document.sha256,
            "facts": facts, "profile": fingerprint, "tool": OUTLINE_RUN, "version": VERSION}
    base_identity = digest(base)
    # Every attempt of this document is found, not only the first one: the
    # base identity travels in the trace of each attempt.
    attempts = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == OUTLINE_RUN,
        or_(AgentTaskRecord.input_hash == base_identity,
            AgentTaskRecord.trace["base_identity"].as_string() == base_identity),
    ).order_by(AgentTaskRecord.created_at.desc())).all()
    for old in attempts:
        if old.status in REUSABLE_TASK_STATUSES:
            return old
    # Every earlier attempt was cancelled or failed: none of them can move
    # again.  The next attempt gets its own identity, so the button hands back
    # work that can actually continue and the old identity stays as history.
    identity = digest({**base, "attempt": len(attempts) + 1})
    row = AgentTaskRecord(project_id=project_id, role_id=OUTLINE_RUN, request_identity=identity,
        input_hash=identity, asset_manifest_hash=identity, rulepack_id=VERSION, rulepack_hash=digest(VERSION),
        model_profile_id=profile.id, model_id=profile.model, schema_version="1.0", status="QUEUED",
        trace={"document_id": document.id, "document_sha256": document.sha256, "locked_facts": facts,
               "provider_fingerprint": fingerprint, "stage": "EXTRACT", "cursor": 0,
               "leaf_artifact_ids": [], "level": 0, "level_inputs": [], "level_outputs": [],
               "production_sections": source_sections(project_id, document), "automatic_replay": False,
               "base_identity": base_identity})
    db.add(row)
    db.flush()
    add_audit(db, "local-user", "STORY_OUTLINE_QUEUED", "agent_task", row.id,
              {"document_sha256": document.sha256}, project_id=project_id)
    return row


#: How often one scene may be asked again after a person explicitly
#: confirmed that an unknown result may be retried.
MAX_UNKNOWN_RESENDS = 1


def authorize_unknown_resend(db, row, subtask_id: str, actor: str, evidence: str) -> dict:
    """Let one person ask one unknown scene again, on the record.

    The old request may still own an answer upstream, so this never happens
    automatically: a person confirms, the decision is audited, and the scene
    is asked exactly once more.  The old task is retired with the honest
    reason - the answer is unknown and a person decided to ask again - and it
    is never recorded as "not sent".
    """
    blocked = list((row.trace or {}).get("blocked_subtasks") or [])
    entry = next((item for item in blocked
                  if isinstance(item, dict) and item.get("task_id") == subtask_id), None)
    if entry is None:
        raise HTTPException(409, "STORY_UNKNOWN_SUBTASK_NOT_FOUND")
    sub = db.get(AgentTaskRecord, subtask_id)
    if sub is None or sub.project_id != row.project_id:
        raise HTTPException(409, "STORY_UNKNOWN_SUBTASK_NOT_FOUND")
    # One resend per scene: a scene must not be able to burn a series of calls.
    used = len([other for other in db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == row.project_id,
        AgentTaskRecord.role_id == "scriptwriter",
        AgentTaskRecord.input_hash == sub.input_hash,
    )).all() if ((other.trace or {}).get("reconciliation") or {}).get("outcome") == "USER_AUTHORIZED_NEW_REQUEST"])
    if used >= MAX_UNKNOWN_RESENDS:
        raise HTTPException(409, "STORY_UNKNOWN_RESEND_ALREADY_USED")
    previous_identity = sub.request_identity
    sub.status = "SUPERSEDED"
    sub.request_identity = None
    sub.lease_owner, sub.lease_expires_at = None, None
    sub.state_version += 1
    sub.trace = {**(sub.trace or {}), "previous_request_identity": previous_identity,
                 "reconciliation": {"outcome": "USER_AUTHORIZED_NEW_REQUEST", "attempt": used + 1,
                                    "authorized_by": actor, "evidence": evidence,
                                    "checked_at": utcnow().isoformat()}}
    add_audit(db, actor, "STORY_UNKNOWN_SUBTASK_RESEND_AUTHORIZED", "agent_task", sub.id,
              {"scene_key": entry.get("scene_key"), "previous_request_identity": previous_identity,
               "external_outcome": sub.trace.get("external_outcome"), "evidence": evidence,
               "new_request_allowed": True, "automatic_replay": False},
              project_id=row.project_id)
    resolved = [{**item, "status": "RETRY_AUTHORIZED",
                 "reconciliation": "USER_AUTHORIZED_NEW_REQUEST"}
                if item.get("task_id") == subtask_id else item for item in blocked]
    row.trace = {**row.trace, "blocked_subtasks": resolved}
    row.state_version += 1
    return {"subtask_id": subtask_id, "scene_key": entry.get("scene_key"),
            "previous_request_identity": previous_identity, "attempt": used + 1,
            "resend_allowed": True}


def _merge(factory, gateway, project_id, document, ids, facts):
    with factory() as db:
        profile = gateway.profile_for_role(db, "scriptwriter")
        nodes = [db.get(AgentArtifact, key) for key in ids]
        if any(n is None or n.project_id != project_id or n.sha256 != digest(n.payload_json) for n in nodes):
            return {"status": "FAILED", "error_code": "STORY_SOURCE_ARTIFACT_CHANGED"}
        context = {"parts": [{"summary": n.payload_json.get("summary"),
                    "timeline": n.payload_json.get("timeline", []),
                    "relationships": n.payload_json.get("relationships", []),
                    "characters": [r.get("name") for r in n.payload_json.get("requirements", [])
                                   if r.get("family") == "character"]} for n in nodes],
                   "locked_facts": facts}
        schema = OutlinePart.model_json_schema()
        system = ("Merge only these source-backed outline parts in their order. Preserve chronology, conflicts, "
                  "relationships and locked facts; report uncertainty as questions. Do not invent scenes, "
                  "dialogue, asset IDs, production completion or H3 prompts. Return the declared shallow JSON.")
        budget = budget_report(context, schema, system, gateway.config_for(profile))
        if not budget["fits"]:
            return {"status": "FAILED", "error_code": "LLM_CONTEXT_BUDGET_EXCEEDED", "budget": budget}
        fingerprint = text_consent.fingerprint(profile)
    identity = digest({"tool": OUTLINE_TOOL, "version": VERSION, "project": project_id,
                       "document": document.sha256, "inputs": ids, "context": context, "provider": fingerprint})
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        old = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
        if old:
            artifact = db.scalar(select(AgentArtifact).where(AgentArtifact.source_task_id == old.id))
            # A merge whose worker died before the request was opened owns no
            # answer: the identity is released and the merge is asked again.
            if scenes.is_dead_scene_task(old) and scenes.retire_dead_scene_task(db, old, "outline-merge"):
                old = None
            else:
                return {"status": old.status, "task_id": old.id, "artifact_id": artifact.id if artifact else None}
        task = AgentTaskRecord(project_id=project_id, role_id="scriptwriter", input_hash=identity,
            request_identity=identity, asset_manifest_hash=identity, rulepack_id=VERSION, rulepack_hash=digest(VERSION),
            model_profile_id=profile.id, model_id=profile.model, schema_version="1.0", status="RUNNING",
            trace={"tool": OUTLINE_TOOL, "document_id": document.id, "document_sha256": document.sha256,
                   "phase": "PREPARING", "external_outcome": "UNKNOWN",
                   "budget": budget, "automatic_replay": False})
        db.add(task)
        db.flush()
        task_id = task.id
    def record_progress(phase: str) -> None:
        with factory.begin() as db:
            task = db.get(AgentTaskRecord, task_id)
            if task is not None:
                task.trace = {**task.trace, "phase": phase}

    result = call_material_agent(gateway, factory, "scriptwriter", system=system, context=context, schema=schema,
        contract=OutlinePart, on_progress=record_progress,
        before_call=text_consent.guard(project_id, document.id, document.sha256,
                                       "scriptwriter", fingerprint, gateway))
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        if not result.ok:
            external_outcome = result.details.get("external_outcome", "UNKNOWN")
            uncertain = result.uncertain or external_outcome == "UNKNOWN"
            task.status = "NEEDS_REVIEW" if uncertain else "FAILED"
            task.error_code = result.error_code
            task.state_version += 1
            task.trace = {**task.trace, "phase": "FAILED", "external_outcome": external_outcome}
            return {"status": task.status, "task_id": task.id, "error_code": task.error_code}
        payload = OutlinePart.model_validate(result.parsed).model_dump(mode="json")
        artifact = AgentArtifact(project_id=project_id, kind="StoryOutlinePart", schema_version="1.0",
            payload_json=payload, sha256=digest(payload), producer_role="scriptwriter", source_task_id=task_id,
            input_revision_ids=ids)
        db.add(artifact)
        db.flush()
        task.status = "SUCCEEDED"
        task.trace = {**task.trace, "external_outcome": "SUCCEEDED", "output_hash": artifact.sha256}
        return {"status": "SUCCEEDED", "task_id": task_id, "artifact_id": artifact.id}


def _finish_step(factory, task_id, version, worker_id, trace, status="QUEUED", error=None):
    with factory.begin() as db:
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id, AgentTaskRecord.state_version == version,
            AgentTaskRecord.lease_owner == worker_id, AgentTaskRecord.status == "RUNNING",
        ).values(trace=trace, status=status, error_code=error, lease_owner=None, lease_expires_at=None,
                 state_version=version + 1)).rowcount
        return bool(changed)


#: A material subcall records ``WAITING_PROVIDER`` immediately before it opens
#: the provider request (see ``LLMGateway.complete_for_role``). Only a durable
#: earlier phase or provider transport evidence of NOT_SENT can authorize a
#: fresh request. A missing phase is not proof.
PRE_REQUEST_PHASES = ("PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER")

#: A reconciled blocking subcall keeps blocking while its status stays one of
#: these; anything else has a decided outcome and no longer waits on a call.
UNDECIDED_BLOCK_STATUSES = ("RUNNING", "NEEDS_REVIEW", "FAILED")

#: How many provider-proven not-sent scene subcalls may be retried before a
#: person has to look at the resource failure. Each retry gets a new identity.
MAX_SUBTASK_RETRIES = 2


def reconcile_blocked_subtasks(db, row, actor="local-user"):
    """Decide what really happened to every blocking subcall, never resend it.

    Three honest outcomes per subcall:

    ``RESULT_ADOPTED``
        A persisted, non-stale artifact exists and the parent may keep it.
    ``NOT_SENT_PROVEN``
        The subcall stopped before the provider request was opened, so no
        answer exists and none was ever bought.  The row is retired as
        ``SUPERSEDED`` so the parent may ask again with its own identity.
    ``UNCONFIRMABLE``
        The request may have been sent or returned an unusable answer. The
        block stays and the original task identity is preserved.
    """
    trace = row.trace or {}
    blocked = list(trace.get("blocked_subtasks") or [])
    if not blocked:
        # Section runs persist film-agent failures under blocked_result rather
        # than blocked_subtasks.  Reconcile those durable task ids through the
        # same proof rules so a pre-request resource failure is not mistaken
        # for a provider response that may have been lost.
        rejections = (trace.get("blocked_result") or {}).get("rejections") or []
        blocked = [
            {"task_id": item["task_id"], "role": item.get("role"), "code": item.get("code")}
            for item in rejections
            if isinstance(item, dict) and isinstance(item.get("task_id"), str) and item["task_id"]
        ]
    resolved: list[dict] = []
    outcomes: list[dict] = []
    decided_outcomes = {"RESULT_ADOPTED", "NOT_SENT_PROVEN", "RESPONSE_RECEIVED_NO_ANSWER"}
    for entry in blocked:
        # A block that was already decided keeps its verdict.  Reconciling a
        # second time must never turn a released block back into an
        # unconfirmable one - that is how a task became impossible to resume.
        if isinstance(entry, dict) and entry.get("reconciliation") in decided_outcomes:
            resolved.append(entry)
            outcomes.append({"subtask_id": entry.get("task_id"), "outcome": entry.get("reconciliation")})
            continue
        if not isinstance(entry, dict) or not entry.get("task_id"):
            resolved.append({**entry, "status": "NOT_SENT", "reconciliation": "NO_SUBCALL"})
            outcomes.append({"subtask_id": None, "outcome": "NO_SUBCALL"})
            continue
        subtask_id = entry["task_id"]
        sub = db.get(AgentTaskRecord, subtask_id)
        if sub is None or sub.project_id != row.project_id:
            resolved.append({**entry, "status": "NOT_SENT", "reconciliation": "SUBTASK_RECORD_MISSING"})
            outcomes.append({"subtask_id": subtask_id, "outcome": "SUBTASK_RECORD_MISSING"})
            continue
        trace = sub.trace or {}
        artifact = db.scalar(select(AgentArtifact).where(
            AgentArtifact.source_task_id == sub.id, AgentArtifact.stale.is_(False)))
        if artifact is not None and (sub.status == "SUCCEEDED" or trace.get("external_outcome") == "SUCCEEDED"):
            resolved.append({**entry, "status": "REUSED", "artifact_id": artifact.id,
                             "reconciliation": "RESULT_ADOPTED"})
            outcomes.append({"subtask_id": sub.id, "outcome": "RESULT_ADOPTED",
                             "artifact_id": artifact.id})
            continue
        phase = trace.get("phase")
        if (artifact is None and phase in PRE_REQUEST_PHASES
                and sub.status in {"RUNNING", "NEEDS_REVIEW"}
                and trace.get("external_outcome") in {None, "UNKNOWN"}):
            sub.status = "SUPERSEDED"
            sub.error_code = None
            sub.lease_owner, sub.lease_expires_at = None, None
            # The request identity was never consumed by a provider, so it must
            # not keep the unique claim: a later request for this scene has to
            # be able to take that identity again.  The old value stays in the
            # trace as history.
            previous_identity = sub.request_identity
            sub.request_identity = None
            sub.trace = {**trace, "external_outcome": "NOT_SENT",
                         "previous_request_identity": previous_identity,
                         "reconciliation": {"outcome": "NOT_SENT_PROVEN", "last_phase": phase,
                                            "checked_at": utcnow().isoformat()}}
            resolved.append({**entry, "status": "NOT_SENT", "reconciliation": "NOT_SENT_PROVEN"})
            outcomes.append({"subtask_id": sub.id, "outcome": "NOT_SENT_PROVEN", "last_phase": phase})
            continue
        # A subcall that answered and was rejected has a decided outcome: the
        # request is finished, nothing usable came back, and asking again is a
        # new request rather than a replay of an unknown one.  It is still
        # bounded, so a scene the model can never satisfy stops and waits for a
        # person instead of burning calls.
        # The provider answered and produced no usable answer: a refusal
        # (wrong key, unknown model, rate limit) or an answer that broke the
        # contract.  The request is finished, so there is no answer that a
        # second request could duplicate.  This is neither "not sent" nor
        # "unknown" - the record keeps saying exactly what happened - and the
        # retry stays bounded, so a scene the model can never satisfy stops
        # and waits for a person instead of burning calls.
        answered = (sub.status in {"FAILED", "REJECTED"}
                    and trace.get("external_outcome") == "RESPONSE_RECEIVED")
        if answered and artifact is None:
            counts = dict((row.trace or {}).get("subtask_retries") or {})
            scene_key = entry.get("scene_key") or sub.id
            used = int(counts.get(scene_key, 0))
            if used >= MAX_SUBTASK_RETRIES:
                resolved.append({**entry, "status": "REJECTED", "reconciliation": "RETRIES_EXHAUSTED"})
                outcomes.append({"subtask_id": sub.id, "outcome": "RETRIES_EXHAUSTED",
                                 "attempts": used, "error_code": sub.error_code})
                continue
            counts[scene_key] = used + 1
            previous_identity = sub.request_identity
            sub.status = "SUPERSEDED"
            sub.request_identity = None
            sub.lease_owner, sub.lease_expires_at = None, None
            sub.trace = {**trace, "previous_request_identity": previous_identity,
                         "reconciliation": {"outcome": "RESPONSE_RECEIVED_NO_ANSWER",
                                            "attempt": used + 1, "error_code": sub.error_code,
                                            "response_status": trace.get("response_status"),
                                            "checked_at": utcnow().isoformat()}}
            resolved.append({**entry, "status": "REJECTED",
                             "reconciliation": "RESPONSE_RECEIVED_NO_ANSWER"})
            outcomes.append({"subtask_id": sub.id, "outcome": "RESPONSE_RECEIVED_NO_ANSWER",
                             "attempt": used + 1, "error_code": sub.error_code,
                             "response_status": trace.get("response_status")})
            row.trace = {**row.trace, "subtask_retries": counts}
            continue
        failed = sub.status in {"FAILED", "REJECTED"} and trace.get("external_outcome") == "NOT_SENT"
        if failed and artifact is None:
            counts = dict((row.trace or {}).get("subtask_retries") or {})
            scene_key = entry.get("scene_key") or sub.id
            used = int(counts.get(scene_key, 0))
            if used >= MAX_SUBTASK_RETRIES:
                resolved.append({**entry, "reconciliation": "RETRIES_EXHAUSTED"})
                outcomes.append({"subtask_id": sub.id, "outcome": "RETRIES_EXHAUSTED",
                                 "attempts": used, "error_code": sub.error_code})
                continue
            counts[scene_key] = used + 1
            previous_identity = sub.request_identity
            sub.status = "SUPERSEDED"
            sub.request_identity = None
            sub.lease_owner, sub.lease_expires_at = None, None
            sub.trace = {**trace, "previous_request_identity": previous_identity,
                         "reconciliation": {"outcome": "NOT_SENT_PROVEN",
                                            "attempt": used + 1, "error_code": sub.error_code,
                                            "checked_at": utcnow().isoformat()}}
            resolved.append({**entry, "status": "NOT_SENT", "reconciliation": "NOT_SENT_PROVEN"})
            outcomes.append({"subtask_id": sub.id, "outcome": "NOT_SENT_PROVEN",
                             "attempt": used + 1, "error_code": sub.error_code})
            row.trace = {**row.trace, "subtask_retries": counts}
            continue
        resolved.append({**entry, "reconciliation": "UNCONFIRMABLE"})
        outcomes.append({"subtask_id": sub.id, "outcome": "UNCONFIRMABLE", "status": sub.status,
                         "last_phase": phase, "external_outcome": trace.get("external_outcome")})
    row.trace = {**row.trace, "blocked_subtasks": resolved,
                 "reconciliation": {"checked_at": utcnow().isoformat(), "outcomes": outcomes}}
    released = _release_retired_identities(db, row.project_id)
    add_audit(db, actor, "STORY_SUBTASKS_RECONCILED", "agent_task", row.id,
              {"outcomes": outcomes, "identities_released": released}, project_id=row.project_id)
    return {"outcomes": outcomes,
            # The verdict must match the resume guard exactly, otherwise the
            # interface offers a button the server will refuse.
            "resumable": all(
                item.get("status") not in UNDECIDED_BLOCK_STATUSES
                and item.get("reconciliation") not in {"UNCONFIRMABLE", "RETRIES_EXHAUSTED"}
                for item in resolved
            ),
            "unconfirmable": [item for item in outcomes if item.get("outcome") == "UNCONFIRMABLE"],
            "identities_released": released}


def _release_retired_identities(db, project_id) -> list[str]:
    """A call that provably never left must not keep its unique identity claim.

    Retiring the row is not enough: the identity is what a later request for the
    same scene needs, and it is unique per project.  The original value stays in
    the trace, so the history still shows which identity was retired.
    """
    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.status == "SUPERSEDED",
        AgentTaskRecord.request_identity.is_not(None),
    )).all()
    released = []
    for retired in rows:
        trace = retired.trace or {}
        outcome = (trace.get("reconciliation") or {}).get("outcome")
        if (trace.get("external_outcome") != "NOT_SENT"
                and outcome not in {"NOT_SENT_PROVEN", "RESPONSE_RECEIVED_NO_ANSWER"}):
            continue
        retired.trace = {**trace, "previous_request_identity": retired.request_identity}
        retired.request_identity = None
        released.append(retired.id)
    return released


@contextmanager
def _renew_story_claim(factory, task_id, version, worker_id, heartbeat_fn=None, interval=10):
    """Renew only this fenced claim; a pause or changed owner ends renewal."""
    stop = Event()

    def renew():
        try:
            with factory.begin() as db:
                changed = db.execute(update(AgentTaskRecord).where(
                    AgentTaskRecord.id == task_id, AgentTaskRecord.state_version == version,
                    AgentTaskRecord.lease_owner == worker_id, AgentTaskRecord.status == "RUNNING",
                ).values(lease_expires_at=utcnow() + timedelta(minutes=30))).rowcount
            if not changed:
                stop.set()
                return
            if heartbeat_fn:
                heartbeat_fn()
        except OperationalError:
            # A busy SQLite writer does not prove the claim was lost.
            logging.getLogger(__name__).warning("story_heartbeat_deferred database_busy")

    def loop():
        while not stop.wait(interval):
            renew()

    renew()
    thread = Thread(target=loop, name="shine-story-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@contextmanager
def _pinned_story_provider(gateway, profile):
    """Run one unit on the profile the task was queued with.

    The route may have changed since the task was queued; that changes the next
    task, not this one.  Pinning here also keeps the consent fingerprint and the
    per-scene subcalls on the same identity, so a queued outline continues on
    its original model instead of stopping with STORY_PROVIDER_CHANGED (J05).
    """
    gateway.pin_role("scriptwriter", profile)
    try:
        yield
    finally:
        gateway.pin_role("scriptwriter", None)


def _pinned_profile(factory, task_id: str) -> tuple[ProviderProfile | None, str | None]:
    """The durable model snapshot of one task, if it can still be executed."""
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None:
            return None, None
        profile = db.get(ProviderProfile, task.model_profile_id) if task.model_profile_id else None
        snapshot = task.model_id
    return profile, snapshot


def run_pending_story_task(factory, settings, worker_id, *, gateway=None, heartbeat_fn=None):
    """One durable unit per tick, with no database writer held over a model call."""
    gateway = gateway or LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))
    now = utcnow()
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.role_id.in_([OUTLINE_RUN, SECTION_RUN]),
            or_(AgentTaskRecord.status == "QUEUED",
                (AgentTaskRecord.status == "RUNNING") & (
                    AgentTaskRecord.lease_expires_at.is_(None)
                    | (AgentTaskRecord.lease_expires_at <= now)
                )),
        ).order_by(AgentTaskRecord.created_at).limit(1))
        if row is None:
            return False
        row.status = "RUNNING"
        row.lease_owner = worker_id
        # A unit is renewed every few seconds while the model call runs, so the
        # lease only has to survive a lost worker.  Thirty minutes left a person
        # watching a stalled outline with no visible progress; two minutes
        # makes the task recoverable while the renewal still protects a running
        # unit (a lost owner is fenced out by the state version).
        row.lease_expires_at = now + timedelta(seconds=STORY_CLAIM_SECONDS)
        row.state_version += 1
        task_id, version, project_id, role = row.id, row.state_version, row.project_id, row.role_id
        state = dict(row.trace)
        document = db.get(ProjectDocument, state["document_id"])
    if role == SECTION_RUN:
        # A section run is driven by the crew's per-role routing; it has no
        # single pinned profile of its own (its marker is not a provider id).
        pinned, pinned_model = None, None
    else:
        pinned, pinned_model = _pinned_profile(factory, task_id)
        if (pinned is None or not pinned.enabled or not pinned.model.strip()
                or (pinned_model and pinned.model != pinned_model)):
            # The task keeps its durable identity and its completed units; only
            # the model snapshot is unusable.  Name it instead of switching.
            _finish_step(factory, task_id, version, worker_id, state, "NEEDS_REVIEW",
                         "STORY_PROVIDER_PIN_INVALID")
            return True
    with _pinned_story_provider(gateway, pinned), _renew_story_claim(
            factory, task_id, version, worker_id, heartbeat_fn):
        if document is None or document.project_id != project_id or document.sha256 != state["document_sha256"]:
            _finish_step(factory, task_id, version, worker_id, state, "FAILED", "STORY_DOCUMENT_CHANGED")
            return True
        try:
            if role == SECTION_RUN:
                return _run_section(factory, settings, gateway, task_id, version, worker_id,
                                     project_id, document, state)
            profile = pinned
            if profile is None or text_consent.fingerprint(profile) != state["provider_fingerprint"]:
                # The pinned profile itself was edited (endpoint, key reference
                # or options): the queued request no longer describes the same
                # provider, so it stops by name rather than silently switching.
                raise HTTPException(409, "STORY_PROVIDER_CHANGED")
            source = segment_document(document.text)
            # The number of source units is part of the progress the person
            # waits for; it is recorded once and reused after a restart.
            if state.get("segment_total") != len(source):
                state["segment_total"] = len(source)
            if state["stage"] == "EXTRACT" and state["cursor"] < len(source):
                current = source[state["cursor"]]
                result = scenes.prepare_scenes(gateway, factory, project_id, document,
                    locked_story_facts=state["locked_facts"], scene_keys=[current.key])
                successful = {"PREPARED", "REUSED"}
                if result.get("error_code") or any(s["status"] not in successful for s in result["scenes"]):
                    state["blocked_subtasks"] = result.get("scenes", [])
                    _finish_step(factory, task_id, version, worker_id, state, "NEEDS_REVIEW",
                                 result.get("error_code") or "STORY_EXTRACTION_NEEDS_REVIEW")
                    return True
                state["leaf_artifact_ids"] = [*state["leaf_artifact_ids"],
                                               *(s["artifact_id"] for s in result["scenes"])]
                state["cursor"] += 1
            elif state["stage"] == "EXTRACT":
                state.update(stage="MERGE", cursor=0, level_inputs=state["leaf_artifact_ids"], level_outputs=[])
            elif state["stage"] == "MERGE" and state["cursor"] < len(state["level_inputs"]):
                ids = state["level_inputs"][state["cursor"]:state["cursor"] + 3]
                merged = _merge(factory, gateway, project_id, document, ids, state["locked_facts"])
                if merged["status"] != "SUCCEEDED":
                    state["blocked_subtasks"] = [merged]
                    _finish_step(factory, task_id, version, worker_id, state,
                                 "NEEDS_REVIEW" if merged["status"] in {"RUNNING", "NEEDS_REVIEW"} else "FAILED",
                                 merged.get("error_code", "STORY_MERGE_NEEDS_REVIEW"))
                    return True
                state["level_outputs"] = [*state["level_outputs"], merged["artifact_id"]]
                state["cursor"] += len(ids)
            elif state["stage"] == "MERGE" and len(state["level_outputs"]) > 1:
                state.update(level=state["level"] + 1, cursor=0, level_inputs=state["level_outputs"], level_outputs=[])
            else:
                with factory.begin() as db:
                    # Control/version fencing also guards publication of the final artifact.
                    owner = db.get(AgentTaskRecord, task_id)
                    if owner.state_version != version or owner.lease_owner != worker_id or owner.status != "RUNNING":
                        return True
                    final = db.get(AgentArtifact, state["level_outputs"][0])
                    leaves = [db.get(AgentArtifact, key) for key in state["leaf_artifact_ids"]]
                    names = sorted({r["name"] for leaf in leaves for r in leaf.payload_json.get("requirements", [])
                                    if r.get("family") == "character"})
                    payload = {**final.payload_json, "document_id": document.id, "document_sha256": document.sha256,
                        "original_text_sha256": hashlib.sha256(document.text.encode()).hexdigest(),
                        "offset_unit": "unicode_codepoint",
                        "production_sections": state["production_sections"],
                        "characters": [{"name": name, "entity_id": name,
                                        "stable_identity": str(uuid5(NAMESPACE_URL,
                                                                     f"shine:{project_id}:character:{name}"))}
                                       for name in names],
                        "source_locked_facts": [{"scene_key": leaf.payload_json["scene_key"],
                                                 "locked_facts": leaf.payload_json.get("locked_facts", []),
                                                 "artifact_id": leaf.id} for leaf in leaves],
                        "locked_facts": state["locked_facts"], "leaf_artifact_ids": state["leaf_artifact_ids"],
                        "continuity_status": "PLANNING_ONLY_NO_PREVIOUS_RENDER_CLAIM"}
                    outline = AgentArtifact(project_id=project_id, kind="StoryOutline", schema_version="1.0",
                        payload_json=payload, sha256=digest(payload), producer_role="scriptwriter",
                        source_task_id=task_id, input_revision_ids=[final.id, *state["leaf_artifact_ids"]])
                    db.add(outline)
                    db.flush()
                    owner.trace = {**state, "stage": "AWAITING_OUTLINE_CONFIRMATION", "outline_artifact_id": outline.id}
                    owner.status, owner.lease_owner, owner.lease_expires_at = "SUCCEEDED", None, None
                    owner.state_version += 1
                return True
            _finish_step(factory, task_id, version, worker_id, state)
        except Exception as exc:  # keep the orchestration intent; child UNKNOWN is never replayed
            code = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else type(exc).__name__
            _finish_step(factory, task_id, version, worker_id, state, "NEEDS_REVIEW", str(code)[:64])
        return True


def _run_section(factory, settings, gateway, task_id, version, worker_id, project_id, document, state):
    from shine.agents.film import asset_context
    from shine.api_film import StoryboardRequest, generate_storyboard
    from shine.models import Project

    cursor = state.get("cursor", 0)
    units = state["units"]
    if cursor >= len(units):
        _finish_step(factory, task_id, version, worker_id, state,
                     "NEEDS_REVIEW" if state.get("invalidated_section_ids") else "SUCCEEDED")
        return True
    unit = units[cursor]
    if unit["production_section_id"] in state.get("invalidated_section_ids", []):
        state["cursor"] = cursor + 1
        _finish_step(factory, task_id, version, worker_id, state)
        return True
    with factory() as db:
        project = db.get(Project, project_id)
        if (project.frozen_style_version_id != state["style_version_id"]
                or digest(asset_context(db, project_id)) != state["asset_manifest_hash"]):
            raise HTTPException(409, "STORY_FROZEN_CONTRACT_CHANGED")
        outline = db.get(AgentArtifact, state["outline_artifact_id"])
        if not outline or not outline.approved_at or outline.stale or outline.sha256 != state["outline_sha256"]:
            raise HTTPException(409, "STORY_OUTLINE_NOT_CONFIRMED")
        for role in ("scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor"):
            profile = gateway.profile_for_role(db, role)
            if not text_consent.allowed(db, project_id, document, role, profile):
                raise HTTPException(409, "REMOTE_AGENT_CONSENT_REQUIRED")
        result = generate_storyboard(project_id, StoryboardRequest(
            text=document.text[unit["start_offset"]:unit["end_offset"]],
            locked_story_facts=state["locked_facts"], allow_remote_text=True,
            start_role=state.get("failed_role"), resume_steps=state.get("resume_steps", {}),
            replace_interrupted_task_id=state.get("replace_interrupted_task_id"),
            production_context={"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                "production_section_id": unit["production_section_id"], "source_offsets": unit,
                "story_line": outline.payload_json.get("story_line", []),
                "timeline": outline.payload_json.get("timeline", []),
                "characters": outline.payload_json.get("characters", []),
                "continuity_facts": unit.get("continuity_facts", []),
                "continuity": unit.get("continuity") or {"basis": "user_supplied_unverified"},
                "previous_render_status": "PENDING_SERVER_VERIFICATION"},
            consent_document_id=document.id, consent_document_sha256=document.sha256,
        ), SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))), db, gateway)
    # The authorization is one-shot. The Film dispatcher has its own durable
    # receipt; do not carry the parent's authorization into a later role.
    state["replace_interrupted_task_id"] = None
    if result["status"] != "PROPOSED":
        # The existing chain returns its own successful role artifacts. Resume
        # from the failed role only; unknown tasks retain the chain's guard.
        state.update(failed_role=result.get("failed_role"), resume_steps=result.get("resume_steps", {}),
                     blocked_result=result)
        _finish_step(factory, task_id, version, worker_id, state, "NEEDS_REVIEW", result["status"])
        return True
    state["results"] = [*state.get("results", []), {"unit": unit, "storyboard": result}]
    state.update(cursor=cursor + 1, failed_role=None, resume_steps={})
    _finish_step(factory, task_id, version, worker_id, state)
    return True


def _latest_section_run(db, project_id, production_section_id):
    """The newest section run that covers this section; its results are the
    current production (older runs only remain as history)."""
    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == SECTION_RUN)
        .order_by(AgentTaskRecord.created_at, AgentTaskRecord.id))
    return next((row for row in reversed(list(rows))
                 if any(u.get("production_section_id") == production_section_id
                        for u in row.trace.get("units", []))), None)


def _shot_state(db, project_id, shot_id, position):
    from shine.api_segments import accepted_job
    from shine.models import Asset, Job, MediaArtifact, Shot, ShotAssetBinding

    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        return {"shot_id": shot_id, "position": position, "state": "SHOT_MISSING"}
    jobs = [j for j in db.scalars(select(Job).where(Job.shot_id == shot_id, Job.stale.is_(False))
            .order_by(Job.created_at, Job.id))]
    in_flight = [j.id for j in jobs
                 if j.status in {"QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING", "NEEDS_REVIEW"}]
    current = jobs[-1] if jobs else None
    accepted = bool(current is not None and not in_flight and current.shot_revision == shot.revision
                    and accepted_job(db, current))
    media = db.get(MediaArtifact, current.artifact_id) if accepted else None
    row = {"shot_id": shot_id, "position": position, "name": shot.name,
           "shot_revision": shot.revision, "characters": list(shot.spec.get("characters", [])),
           "duration_s": shot.spec.get("duration_target_s"),
           "job_id": current.id if current else None,
           "state": "ACCEPTED" if accepted else ("RENDER_IN_FLIGHT" if in_flight else "NOT_ACCEPTED"),
           "artifact_id": media.id if media else None,
           "artifact_sha256": media.sha256 if media else None}
    row["assets"] = sorted({(asset.id, asset.role or "", asset.entity_id or "", asset.sha256)
                            for binding in db.scalars(select(ShotAssetBinding).where(
                                ShotAssetBinding.shot_id == shot_id))
                            if (asset := db.get(Asset, binding.asset_id)) is not None})
    return row


def section_storyboard_shot_ids(db, project_id, production_section_id):
    """The shots the newest applied storyboard of one section really created.

    Read from the section run's own results and the apply receipts the
    storyboard apply wrote, so an episode plan materialises to exactly the
    shots the person applied for that section — never to shots that merely
    exist in the project.  Returns ``{"shot_ids": [...], "missing": [...]}``.
    """
    from shine.models import AppSetting

    run = _latest_section_run(db, project_id, production_section_id)
    if run is None:
        return {"shot_ids": [], "missing": [{"reason": "SECTION_RUN_MISSING"}]}
    units = [u for u in run.trace.get("units", []) if u.get("production_section_id") == production_section_id]
    results = {(r.get("unit") or {}).get("start_offset"): r for r in run.trace.get("results", [])}
    shot_ids: list[str] = []
    missing: list[dict] = []
    for unit in units:
        result = results.get(unit.get("start_offset"))
        camera = ((result or {}).get("storyboard") or {}).get("steps", {}).get("cinematographer") or {}
        receipt = db.get(AppSetting, f"film-apply:{camera.get('artifact_id')}") if camera.get("artifact_id") else None
        if receipt is None:
            missing.append({"unit_start_offset": unit.get("start_offset"), "reason": "STORYBOARD_NOT_APPLIED"})
            continue
        shot_ids.extend(str(shot_id) for shot_id in receipt.value.get("shot_ids", []))
    return {"shot_ids": shot_ids, "missing": missing}


def compute_section_continuity(db, project_id, production_section_id):
    """Derive what this section's production really ended as, from the ledger.

    A versioned end state exists only when every work unit of the section was
    storyboarded, applied and accepted under the current shot revisions; a mock
    render is never acceptance.  The payload is deterministic (no clock), so
    the same production always hashes to the same continuity identity and any
    later retake changes the hash.
    """
    run = _latest_section_run(db, project_id, production_section_id)
    if run is None:
        return {"schema_version": "1.0", "production_section_id": production_section_id,
                "status": "NOT_PRODUCED", "shots": [], "missing": ["SECTION_RUN_MISSING"]}
    units = [u for u in run.trace.get("units", []) if u.get("production_section_id") == production_section_id]
    results = {(r.get("unit") or {}).get("start_offset"): r for r in run.trace.get("results", [])}
    shots, missing = [], []
    for unit in units:
        result = results.get(unit.get("start_offset"))
        camera = ((result or {}).get("storyboard") or {}).get("steps", {}).get("cinematographer") or {}
        from shine.models import AppSetting

        receipt = db.get(AppSetting, f"film-apply:{camera.get('artifact_id')}") if camera.get("artifact_id") else None
        if receipt is None:
            missing.append({"unit_start_offset": unit.get("start_offset"), "reason": "STORYBOARD_NOT_APPLIED"})
            continue
        for position, shot_id in enumerate(receipt.value.get("shot_ids", [])):
            shots.append(_shot_state(db, project_id, shot_id, position))
    missing.extend({"shot_id": s["shot_id"], "reason": s["state"]} for s in shots if s["state"] != "ACCEPTED")
    verified = bool(shots) and not missing
    end_state = None
    if verified:
        last = shots[-1]
        last_seen = {}
        for shot in shots:
            for name in shot["characters"]:
                last_seen[name] = shot["shot_id"]
        assets = sorted(({"asset_id": a, "role": role, "entity_id": entity, "sha256": sha}
                         for (a, role, entity, sha) in {t for shot in shots for t in shot["assets"]}),
                        key=lambda a: a["asset_id"])
        end_state = {"last_shot_id": last["shot_id"], "last_shot_name": last["name"],
                     "last_artifact_id": last["artifact_id"], "last_artifact_sha256": last["artifact_sha256"],
                     "characters": list(last["characters"]), "characters_last_seen": last_seen,
                     "assets_used": assets}
    for shot in shots:
        shot.pop("assets", None)
    return {"schema_version": "1.0", "production_section_id": production_section_id,
            "status": "VERIFIED" if verified else "PARTIAL", "shots": shots, "missing": missing,
            "end_state": end_state}


def freeze_section_continuity(db, project_id, production_section_id):
    """Freeze a fully accepted section end state as an immutable artifact.

    Only VERIFIED states become artifacts; a partial production stays a live
    view.  A later retake derives a different payload and therefore freezes a
    new version — the previous one is never rewritten.
    """
    payload = compute_section_continuity(db, project_id, production_section_id)
    if payload["status"] != "VERIFIED":
        return None, payload
    rows = [row for row in db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id, AgentArtifact.kind == CONTINUITY_KIND))
        if row.payload_json.get("production_section_id") == production_section_id and not row.stale]
    current = digest(payload)
    if rows and max(rows, key=lambda r: r.created_at).sha256 == current:
        return max(rows, key=lambda r: r.created_at), payload
    run = _latest_section_run(db, project_id, production_section_id)
    artifact = AgentArtifact(project_id=project_id, kind=CONTINUITY_KIND, schema_version="1.0",
        payload_json=payload, sha256=current, producer_role="continuity_derivation",
        source_task_id=run.id, input_revision_ids=[max(rows, key=lambda r: r.created_at).id] if rows else [])
    db.add(artifact)
    db.flush()
    return artifact, payload


def prior_section_of(section, outline_sections):
    """The section whose end state this section carries over from, if any."""
    earlier = [s for s in outline_sections if s["order"] < section["order"]]
    return max(earlier, key=lambda s: s["order"]) if earlier else None


def verified_production_context(db, project_id, source_text, supplied):
    """Rebuild approved context; never trust a caller's outline or source range."""
    outline = db.get(AgentArtifact, supplied.get("outline_artifact_id"))
    if (not outline or outline.project_id != project_id or outline.kind != "StoryOutline"
            or not outline.approved_at or outline.stale or outline.sha256 != supplied.get("outline_sha256")
            or outline.sha256 != digest(outline.payload_json)):
        raise ValueError("STORY_OUTLINE_NOT_CONFIRMED")
    document = db.get(ProjectDocument, outline.payload_json["document_id"])
    section = next((s for s in outline.payload_json["production_sections"]
                    if s["production_section_id"] == supplied.get("production_section_id")), None)
    span = supplied.get("source_offsets") or {}
    left, right = span.get("start_offset"), span.get("end_offset")
    if (document is None or document.sha256 != outline.payload_json["document_sha256"] or section is None
            or type(left) is not int or type(right) is not int
            or not section["start_offset"] <= left < right <= section["end_offset"]
            or source_text.strip() != document.text[left:right].strip()):
        raise ValueError("STORY_SOURCE_RANGE_INVALID")
    from shine.models import AppSetting
    current = db.get(AppSetting, f"current-story-outline:{project_id}")
    latest = db.get(AgentArtifact, current.value.get("artifact_id")) if current else None
    if latest and latest.id != outline.id:
        current_section = next((s for s in latest.payload_json["production_sections"]
                                if s["production_section_id"] == section["production_section_id"]), None)
        if (not current_section or current_section.get("source_sha256") != section.get("source_sha256")
                or latest.payload_json["locked_facts"] != outline.payload_json["locked_facts"]):
            raise ValueError("STORY_SOURCE_SUPERSEDED")
    continuity = supplied.get("continuity") or {}
    if continuity.get("basis") == "verified_section_state":
        # The run was planned against a specific frozen end state of the prior
        # section.  If that state no longer recomputes identically (a retake
        # passed, a shot changed), the plan consumed facts that are no longer
        # true — refuse instead of silently dropping them.
        artifact = db.get(AgentArtifact, continuity.get("continuity_artifact_id"))
        live = compute_section_continuity(db, project_id, continuity.get("prior_production_section_id"))
        if (not artifact or artifact.project_id != project_id or artifact.kind != CONTINUITY_KIND
                or artifact.stale or artifact.sha256 != continuity.get("continuity_sha256")
                or artifact.sha256 != digest(artifact.payload_json)
                or live.get("status") != "VERIFIED" or digest(live) != artifact.sha256):
            raise ValueError("STORY_CONTINUITY_SUPERSEDED")
        previous_render_status, continuity_evidence = "VERIFIED_FROM_PRIOR_SECTION", {
            "basis": "verified_section_state", "continuity_artifact_id": artifact.id,
            "continuity_sha256": artifact.sha256,
            "prior_production_section_id": continuity.get("prior_production_section_id"),
            "end_state": artifact.payload_json.get("end_state")}
    else:
        # User free text remains an explicitly unverified carry-over: it may
        # guide the crew but is never evidence that a prior render happened.
        previous_render_status, continuity_evidence = "NOT_CLAIMED", {"basis": "user_supplied_unverified"}
    return {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
            "document_id": document.id, "document_sha256": document.sha256,
            "production_section_id": section["production_section_id"], "source_offsets": span,
            "story_line": outline.payload_json.get("story_line", []),
            "timeline": outline.payload_json.get("timeline", []),
            "characters": outline.payload_json.get("characters", []),
            "continuity_facts": supplied.get("continuity_facts", []),
            "continuity": continuity_evidence,
            "previous_render_status": previous_render_status}


def select_outline_revision(db, project_id, outline):
    """Invalidate changed source sections and their actual job dependants only."""
    from shine.models import AppSetting, Job
    from shine.services import mark_stale_descendants

    key = f"current-story-outline:{project_id}"
    pointer = db.get(AppSetting, key)
    prior = db.get(AgentArtifact, pointer.value.get("artifact_id")) if pointer else None
    changed = set()
    if prior and prior.id != outline.id:
        new_sections = {s["production_section_id"]: s for s in outline.payload_json["production_sections"]}
        facts_changed = prior.payload_json["locked_facts"] != outline.payload_json["locked_facts"]
        changed = {s["production_section_id"] for s in prior.payload_json["production_sections"]
                   if facts_changed or s["production_section_id"] not in new_sections
                   or s.get("source_sha256") != new_sections[s["production_section_id"]].get("source_sha256")}
        rows = list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project_id)))
        for task in rows:
            context = task.trace.get("production_context") or {}
            if context.get("production_section_id") in changed:
                artifacts = db.scalars(select(AgentArtifact).where(AgentArtifact.source_task_id == task.id))
                for artifact in artifacts:
                    artifact.stale = True
                    receipt = db.get(AppSetting, f"film-apply:{artifact.id}")
                    if receipt:
                        jobs = list(db.scalars(select(Job).where(Job.shot_id.in_(receipt.value.get("shot_ids", [])))))
                        ids = [job.id for job in jobs]
                        for job in jobs:
                            job.stale, job.failure_code = True, "STORY_SOURCE_SUPERSEDED"
                        mark_stale_descendants(db, ids, project_id=project_id)
            if task.role_id == SECTION_RUN:
                affected = sorted({u["production_section_id"] for u in task.trace.get("units", [])} & changed)
                if affected:
                    task.trace = {**task.trace, "invalidated_section_ids": affected}
                    task.state_version += 1
                    if task.status == "RUNNING":
                        task.status, task.lease_owner, task.lease_expires_at = "QUEUED", None, None
                    elif task.status == "SUCCEEDED":
                        task.status, task.error_code = "NEEDS_REVIEW", "STORY_SOURCE_SUPERSEDED"
    # A changed section also invalidates the frozen end states derived from it:
    # a dependent section must not carry over from a section that no longer
    # exists in this form.
    for artifact in db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project_id, AgentArtifact.kind == CONTINUITY_KIND)):
        if artifact.payload_json.get("production_section_id") in changed:
            artifact.stale = True
    if pointer:
        pointer.value = {"artifact_id": outline.id, "sha256": outline.sha256}
    else:
        db.add(AppSetting(key=key, value={"artifact_id": outline.id, "sha256": outline.sha256}))
    # A document-scoped pointer records the same revision per source document,
    # so a per-volume pipeline resolves its own outline without the
    # project-level pointer colliding across volumes.  The project-level
    # pointer stays authoritative for the existing single-document flow.
    document_id = outline.payload_json.get("document_id")
    if document_id:
        doc_key = f"current-story-outline:{project_id}:{document_id}"
        db.merge(AppSetting(key=doc_key,
                            value={"artifact_id": outline.id, "sha256": outline.sha256}))
    return sorted(changed)


def confirm_outline_artifact(db, project_id, row, *, expected_sha256, acknowledge_questions_sha256,
                             actor) -> dict:
    """The one outline confirmation: sha gate, questions gate, invalidation.

    Both the expert endpoint and the four-stage pipeline's volume route go
    through this function, so the two entrances can never diverge.
    """
    from fastapi import HTTPException

    if (row is None or row.project_id != project_id or row.kind != "StoryOutline" or row.stale
            or row.sha256 != expected_sha256 or row.sha256 != digest(row.payload_json)):
        raise HTTPException(409, "STORY_OUTLINE_CHANGED")
    document = db.get(ProjectDocument, row.payload_json["document_id"])
    if document is None or document.project_id != project_id \
            or document.sha256 != row.payload_json.get("document_sha256"):
        raise HTTPException(409, "STORY_OUTLINE_CHANGED")
    questions = list(row.payload_json.get("questions") or [])
    if questions:
        expected = digest({"artifact": row.id, "questions": questions})
        if acknowledge_questions_sha256 != expected:
            raise HTTPException(409, "STORY_OUTLINE_QUESTIONS_REQUIRE_FACTS")
    was_approved = bool(row.approved_at)
    if not was_approved:
        row.approved_by, row.approved_at = actor, utcnow()
    changed = select_outline_revision(db, project_id, row)
    if changed or not was_approved:
        add_audit(db, actor, "STORY_OUTLINE_CONFIRMED", "agent_artifact", row.id,
                  {"sha256": row.sha256, "invalidated_section_ids": changed,
                   "questions_acknowledged": questions or None}, project_id=project_id)
    return {"artifact_id": row.id, "approved": True,
            "invalidated_section_ids": sorted(changed)}

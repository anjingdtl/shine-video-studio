"""The pre-production scriptwriter over one script document.

The material flow needs a requirement list before anything is frozen, so the
scriptwriter answers ONE numbered scene at a time:

* the server segments the document and owns the numbering;
* the model may only cite a fragment from the catalogue it was given, and an
  invented fragment is refused rather than dropped;
* every scene answer is a durable artifact, so a repeated request reuses it
  instead of paying for the same call twice;
* a failed scene does not stop the other scenes: the plan asks for a per-scene
  result, and the caller decides what to do with the gap.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select, text

from shine.agents.crew_prompts import script_prepare_prompt
from shine.h3.rulebook import digest
from shine.llm.base import LLMError
from shine.materialprep import text_consent
from shine.materialprep.agent_call import PRE_REQUEST_PHASES, budget_report, call_material_agent
from shine.materialprep.contracts import ScriptPreparation
from shine.materialprep.source_spans import MAX_SCENE_CHARS, Scene, known_span_ids, segment_document, unknown_span_ids
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from shine.services import add_audit

SCRIPTWRITER_ROLE = "scriptwriter"
RULEPACK_ID = "material-script-prepare"

#: A scene subcall is bounded by the provider timeout, and the profile allows
#: at most 600 s.  A scene task that is still RUNNING long after that cannot
#: have a live worker behind it: the worker died with the request in flight.
SCENE_DEAD_AFTER_SECONDS = 900

#: How often one scene may be retired and asked again after a dead worker.
MAX_SCENE_DEAD_RETRIES = 2


def _overlapping_source(previous: str, current: str) -> bool:
    if previous.split("@")[0] != current.split("@")[0]:
        return False
    if "@" not in previous or "@" not in current:
        return True
    left, right = (int(v) for v in previous.split("@")[1].split("-"))
    other_left, other_right = (int(v) for v in current.split("@")[1].split("-"))
    return left < other_right and right > other_left

def rulepack_digest() -> str:
    """The identity of the preparation prompt these answers are bound to."""
    return digest({
        "prompt_sha256": hashlib.sha256(script_prepare_prompt().encode("utf-8")).hexdigest(),
        "contract": ScriptPreparation.__name__,
    })


def build_scene_context(
    *,
    scene,
    locked_story_facts: list[str],
) -> dict[str, Any]:
    """What one scene is answered from."""
    return {
        "scene_key": scene.key,
        "scene_title": scene.title,
        "scene_text": scene.text,
        "source_spans": [span.as_public() for span in scene.spans],
        "locked_story_facts": list(locked_story_facts),
        "rules": [
            "Answer only this scene.",
            "Cite every requirement with source_span_ids taken from the source_spans list.",
            "Never invent a span number and never merge an ambiguous alias.",
            "Never create shots, durations, asset ids or H3 prompt text.",
        ],
    }


def is_dead_scene_task(task: AgentTaskRecord) -> bool:
    """True when a RUNNING scene task has no live worker behind it.

    A worker that dies with the request in flight leaves the row RUNNING
    forever.  Treating that as "still running" is the false progress signal
    that makes a whole outline look stuck.
    """
    if task.status != "RUNNING":
        return False
    age = (utcnow() - task.created_at).total_seconds()
    return age > SCENE_DEAD_AFTER_SECONDS


def retire_dead_scene_task(db, task: AgentTaskRecord, scene_key: str) -> bool:
    """Retire a scene task whose worker died before the request was opened.

    The evidence is durable: the task is still RUNNING long after the provider
    timeout could have elapsed, and its last phase is before the gateway
    opened the request.  Nothing was ever sent, so the identity is released
    and the scene may be asked again - exactly what NOT_SENT_PROVEN means.
    A request that may already have reached the provider is never retired
    here; it stays unknown and waits for a person.
    """
    trace = task.trace or {}
    phase = trace.get("phase")
    if phase not in PRE_REQUEST_PHASES or trace.get("external_outcome") not in {None, "UNKNOWN"}:
        return False
    used = int(db.scalar(select(func.count()).select_from(AgentTaskRecord).where(
        AgentTaskRecord.project_id == task.project_id,
        AgentTaskRecord.role_id == SCRIPTWRITER_ROLE,
        AgentTaskRecord.input_hash == task.input_hash,
        AgentTaskRecord.status == "SUPERSEDED",
    )) or 0)
    if used >= MAX_SCENE_DEAD_RETRIES:
        return False
    previous_identity = task.request_identity
    task.status = "SUPERSEDED"
    task.request_identity = None
    task.lease_owner, task.lease_expires_at = None, None
    task.state_version += 1
    task.trace = {**trace, "external_outcome": "NOT_SENT", "previous_request_identity": previous_identity,
                  "reconciliation": {"outcome": "NOT_SENT_PROVEN", "last_phase": phase,
                                     "attempt": used + 1, "checked_at": utcnow().isoformat()}}
    add_audit(db, "local-user", "MATERIAL_SCENE_DEAD_WORKER_NOT_SENT_PROVEN", "agent_task", task.id,
              {"scene_key": scene_key, "last_phase": phase,
               "previous_request_identity": previous_identity, "external_calls": 0},
              project_id=task.project_id)
    return True


def prepare_scenes(
    gateway,
    db_factory,
    project_id: str,
    document,
    *,
    locked_story_facts: list[str] | None = None,
    allow_remote_text: bool = False,
    replace_unknown_task_id: str | None = None,
    replacement_key: str | None = None,
    scene_keys: list[str] | None = None,
    segment_chars: int | None = None,
) -> dict[str, Any]:
    """Run the scriptwriter over every scene of one document.

    Each scene is an independent answer: a scene that fails is reported with
    its reason and the other scenes still run, because a long script must not
    lose the scenes that did work.
    """
    # The caller and this function must cut the document the same way: a scene
    # key only names the same bytes if both used the same window.  Two different
    # windows silently re-read overlapping text and pay for it twice.
    scenes = segment_document(document.text or "",
                              max_scene_chars=segment_chars or MAX_SCENE_CHARS)
    catalogue = known_span_ids(scenes)
    if scene_keys is not None:
        scenes = [scene for scene in scenes if scene.key in scene_keys]
    facts = list(locked_story_facts or [])
    if replace_unknown_task_id or replacement_key:
        return {"error_code": "MATERIAL_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION",
                "scenes": [], "preparations": []}

    refusal: dict[str, Any] = {}
    with db_factory() as db:
        try:
            profile = gateway.profile_for_role(db, SCRIPTWRITER_ROLE)
        except LLMError as exc:
            refusal = {"error_code": "LLM_NOT_CONFIGURED",
                       "detail": {"role": SCRIPTWRITER_ROLE, "code": exc.code}}
            profile = None
        # A request flag can never grant consent.  Only the persisted, scoped
        # and revocable grant written by the operator's consent action
        # authorises a remote text provider; the flag only states what the
        # caller believes.  Otherwise the very request that needs the consent
        # could hand it to itself.
        if profile is not None and not text_consent.allowed(db, project_id, document, SCRIPTWRITER_ROLE, profile):
            refusal = {"error_code": "REMOTE_AGENT_CONSENT_REQUIRED",
                       "detail": {"role": SCRIPTWRITER_ROLE,
                                  "requested_remote_text": bool(allow_remote_text),
                                  "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest()}}
            profile = None
        config = gateway.config_for(profile) if profile is not None else None

    if refusal:
        return {
            "document_id": document.id,
            "document_sha256": document.sha256,
            "scenes": [],
            "preparations": [],
            "source_span_ids": sorted(catalogue),
            **refusal,
        }

    schema = ScriptPreparation.model_json_schema()
    system_prompt = script_prepare_prompt()
    # Split against the actual configured window, including facts, schema and
    # output reserve. Keep original span IDs/offsets; never trim facts or source.
    budgeted_scenes = []
    for scene in scenes:
        remaining = list(scene.spans)
        while remaining:
            count = len(remaining)
            while count > 1:
                spans = remaining[:count]
                fragment = Scene(scene.key, scene.title, "".join(s.text for s in spans), tuple(spans))
                if budget_report(build_scene_context(scene=fragment, locked_story_facts=facts),
                                 schema, system_prompt, config, input_share=2 / 3)["fits"]:
                    break
                count = max(1, count // 2)
            spans = remaining[:count]
            key = scene.key if count == len(scene.spans) else (
                f"{scene.key}@{spans[0].start_offset}-{spans[-1].end_offset}")
            budgeted_scenes.append(Scene(key, scene.title, "".join(s.text for s in spans), tuple(spans)))
            remaining = remaining[count:]
    scenes = budgeted_scenes
    results: list[dict[str, Any]] = []
    preparations: list[dict[str, Any]] = []

    for scene in scenes:
        # The gap between two provider calls is owned by
        # agent_call.pace_provider_call(); a second sleep here would double it
        # and would still miss the calls that happen in separate task steps.
        context = build_scene_context(scene=scene, locked_story_facts=facts)
        input_hash = digest({
            "document_sha256": document.sha256,
            "context": context,
            "profile": profile.id,
            "model": profile.model,
        })
        legacy_hash = input_hash
        input_hash = digest({
            "project_id": project_id, "document_id": document.id,
            "input": legacy_hash, "tool": "script_prepare", "rulepack": rulepack_digest(),
            "provider": {"kind": profile.kind, "url": profile.base_url,
                         "options": profile.generation_options, "temperature": profile.temperature,
                         "capabilities": profile.capabilities},
        })
        budget = budget_report(context, schema, system_prompt, config)
        if not budget["fits"]:
            results.append({"scene_key": scene.key, "status": "REFUSED",
                            "error_code": "LLM_CONTEXT_BUDGET_EXCEEDED", "budget": budget})
            continue

        # SQLite owns the claim across API processes, browser refreshes and
        # Worker restarts. No writer transaction crosses the provider call.
        with db_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            existing = db.scalar(select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == project_id,
                AgentTaskRecord.role_id == SCRIPTWRITER_ROLE,
                AgentTaskRecord.rulepack_id == RULEPACK_ID,
                # A retired row (a call that provably never left this machine)
                # is history, not an answer: asking the same scene again must
                # create a new request instead of reusing the dead identity.
                AgentTaskRecord.status != "SUPERSEDED",
                AgentTaskRecord.input_hash.in_([input_hash, legacy_hash]),
            ).order_by(AgentTaskRecord.created_at, AgentTaskRecord.id))
            if existing is None:
                # Old tasks predate offset-bearing contexts and request_identity.
                # Their UNKNOWN must survive a prompt/segmentation upgrade too.
                prior = db.scalars(select(AgentTaskRecord).where(
                    AgentTaskRecord.project_id == project_id,
                    AgentTaskRecord.role_id == SCRIPTWRITER_ROLE,
                    AgentTaskRecord.rulepack_id == RULEPACK_ID,
                    AgentTaskRecord.status.in_(["RUNNING", "NEEDS_REVIEW"]),
                ).order_by(AgentTaskRecord.created_at, AgentTaskRecord.id)).all()
                # Only a genuinely legacy row (written before request
                # identities existed) may be adopted without an identity match.
                # A newer UNKNOWN belongs to a different input (facts, prompt,
                # provider) and must never silence a fresh submission: the new
                # submission keeps its own budget and its own request.
                existing = next((t for t in prior
                                 if t.request_identity is None
                                 and t.trace.get("document_sha256") == document.sha256
                                 and _overlapping_source(t.trace.get("scene_key", ""), scene.key)
                                 and (t.status == "RUNNING" or t.trace.get("external_outcome") == "UNKNOWN")), None)
            if existing is not None:
                artifact = db.scalar(select(AgentArtifact).where(
                    AgentArtifact.source_task_id == existing.id, AgentArtifact.stale.is_(False)))
                if existing.status == "SUCCEEDED" and artifact is not None:
                    results.append({"scene_key": scene.key, "status": "REUSED",
                                    "task_id": existing.id, "preparation": artifact.payload_json,
                                    "artifact_id": artifact.id, "state_version": existing.state_version})
                    preparations.append(artifact.payload_json)
                    continue
                # A scene task whose worker died is not "still running": decide it.
                # If it provably never opened the request, the identity is
                # released and the scene is asked again with a new request.
                dead = is_dead_scene_task(existing)
                if dead and retire_dead_scene_task(db, existing, scene.key):
                    existing = None
                else:
                    entry = {"scene_key": scene.key, "status": existing.status,
                             "task_id": existing.id, "error_code": existing.error_code,
                             "state_version": existing.state_version,
                             "external_outcome": existing.trace.get("external_outcome"),
                             "automatic_replay": False}
                    if dead:
                        # The worker is gone.  Say so instead of pretending the
                        # scene is still running.
                        entry["dead_worker"] = True
                        entry["outcome_unknown"] = existing.trace.get("external_outcome") == "UNKNOWN"
                    results.append(entry)
                    continue
            task = AgentTaskRecord(
                request_identity=input_hash,
                project_id=project_id, role_id=SCRIPTWRITER_ROLE, input_hash=input_hash,
                style_version_id=None, asset_manifest_hash=input_hash,
                rulepack_id=RULEPACK_ID, rulepack_hash=rulepack_digest(),
                model_profile_id=profile.id, model_id=profile.model,
                schema_version="1.0", status="RUNNING",
                trace={"tool": "script_prepare", "schema": ScriptPreparation.__name__,
                       "scene_key": scene.key, "document_id": document.id,
                       "document_sha256": document.sha256, "budget": budget,
                       "is_mock": profile.kind == "mock", "phase": "PREPARING",
                       "external_outcome": "UNKNOWN", "automatic_replay": False},
            )
            db.add(task)
            db.flush()
            task_id = task.id

        def record_progress(phase: str, task_id: str = task_id) -> None:
            with db_factory.begin() as db:
                row = db.get(AgentTaskRecord, task_id)
                if row is not None:
                    row.trace = {**row.trace, "phase": phase}

        outcome = call_material_agent(
            gateway,
            db_factory,
            SCRIPTWRITER_ROLE,
            system=system_prompt,
            context=context,
            schema=schema,
            contract=ScriptPreparation,
            on_progress=record_progress,
            project_id=project_id, task_id=task_id, purpose="scene_prep",
            before_call=text_consent.guard(project_id, document.id, document.sha256, SCRIPTWRITER_ROLE,
                                           text_consent.fingerprint(profile), gateway),
        )
        if not outcome.ok:
            external_outcome = outcome.details.get("external_outcome", "UNKNOWN")
            uncertain = outcome.uncertain or external_outcome == "UNKNOWN"
            status = "NEEDS_REVIEW" if uncertain else "FAILED"
            with db_factory.begin() as db:
                row = db.get(AgentTaskRecord, task_id)
                if row is not None:
                    row.status = "NEEDS_REVIEW" if uncertain else "REJECTED"
                    row.error_code = outcome.error_code
                    row.state_version += 1
                    row.trace = {**row.trace,
                                 "phase": "FAILED",
                                 "correction_count": 1 if outcome.correction_used else 0,
                                 "schema_errors": outcome.schema_errors,
                                 "external_outcome": external_outcome,
                                 "response_status": outcome.details.get("response_status"),
                                 "provider_code": outcome.details.get("provider_code"),
                                 # "phase" above is the task's own state, so the
                                 # phase that proves whether the request was ever
                                 # opened has to survive under its own key.
                                 "last_phase": outcome.details.get("last_phase"),
                                 "error_type": outcome.details.get("error_type"),
                                 # Why the answer was unusable: a starved output
                                 # budget and a broken contract field need
                                 # different next steps, so both are durable.
                                 "truncated": outcome.details.get("truncated"),
                                 "finish_reason": outcome.details.get("finish_reason"),
                                 "completion_tokens": outcome.details.get("completion_tokens"),
                                 "reasoning_tokens": outcome.details.get("reasoning_tokens"),
                                 "provider_schema_path": outcome.details.get("provider_schema_path"),
                                 "provider_schema_validator": outcome.details.get("provider_schema_validator"),
                                 "automatic_replay": False}
            with db_factory() as db:
                failed_task = db.get(AgentTaskRecord, task_id)
                failed_state_version = failed_task.state_version if failed_task else 0
            results.append({"scene_key": scene.key, "status": status, "task_id": task_id,
                            "state_version": failed_state_version, "error_code": outcome.error_code,
                            "external_outcome": external_outcome})
            continue

        preparation = ScriptPreparation.model_validate(outcome.parsed).model_dump(mode="json")
        invented = unknown_span_ids(preparation, {span.id for span in scene.spans})
        wrong_scene = preparation["scene_key"] != scene.key
        if invented or wrong_scene:
            code = "MATERIAL_SCENE_KEY_UNKNOWN" if wrong_scene else "MATERIAL_SOURCE_SPAN_UNKNOWN"
            with db_factory.begin() as db:
                row = db.get(AgentTaskRecord, task_id)
                if row is not None:
                    row.status = "REJECTED"
                    row.error_code = code
                    row.state_version += 1
                    row.trace = {**row.trace, "phase": "REJECTED",
                                 "external_outcome": "RESPONSE_RECEIVED",
                                 "invented_span_ids": invented, "automatic_replay": False}
            with db_factory() as db:
                rejected_task = db.get(AgentTaskRecord, task_id)
                rejected_state_version = rejected_task.state_version if rejected_task else 0
            results.append({"scene_key": scene.key, "status": "FAILED", "task_id": task_id,
                            "state_version": rejected_state_version, "error_code": code,
                            "span_ids": invented})
            continue

        with db_factory.begin() as db:
            artifact = AgentArtifact(
                project_id=project_id,
                kind=ScriptPreparation.__name__,
                schema_version="1.0",
                payload_json=preparation,
                sha256=digest(preparation),
                producer_role=SCRIPTWRITER_ROLE,
                source_task_id=task_id,
                input_revision_ids=[],
            )
            db.add(artifact)
            db.flush()
            artifact_id = artifact.id
            row = db.get(AgentTaskRecord, task_id)
            row.status = "SUCCEEDED"
            row.state_version += 1
            row.trace = {**row.trace, "phase": "SUCCEEDED", "output_hash": artifact.sha256,
                         "schema_valid": True, "external_outcome": "SUCCEEDED",
                         "correction_count": 1 if outcome.correction_used else 0}
            add_audit(db, "local-user", "MATERIAL_SCENE_PREPARED", "agent_artifact", artifact_id,
                      {"scene_key": scene.key, "document_sha256": document.sha256},
                      project_id=project_id)
        with db_factory() as db:
            succeeded_task = db.get(AgentTaskRecord, task_id)
            succeeded_state_version = succeeded_task.state_version if succeeded_task else 0
        results.append({"scene_key": scene.key, "status": "PREPARED", "task_id": task_id,
                        "state_version": succeeded_state_version,
                        "preparation": preparation, "artifact_id": artifact_id})
        preparations.append(preparation)

    return {
        "document_id": document.id,
        "document_sha256": document.sha256,
        "scenes": results,
        "preparations": preparations,
        "source_span_ids": sorted(catalogue),
    }

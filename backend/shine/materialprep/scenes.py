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
import time
from typing import Any

from shine.agents.crew_prompts import script_prepare_prompt
from shine.h3.rulebook import digest
from shine.llm.base import LLMError
from shine.materialprep.agent_call import budget_report, call_material_agent, step_delay_seconds
from shine.materialprep.contracts import ScriptPreparation
from shine.materialprep.source_spans import known_span_ids, segment_document, unknown_span_ids
from shine.models import AgentArtifact, AgentTaskRecord
from shine.security import is_loopback_url
from shine.services import add_audit

SCRIPTWRITER_ROLE = "scriptwriter"
RULEPACK_ID = "material-script-prepare"

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


def _reusable_artifact(db, project_id: str, input_hash: str):
    """An earlier successful answer for the very same scene."""
    from sqlalchemy import select

    task = db.scalar(
        select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == SCRIPTWRITER_ROLE,
            AgentTaskRecord.input_hash == input_hash,
            AgentTaskRecord.status == "SUCCEEDED",
        )
    )
    if task is None:
        return None
    return db.scalar(
        select(AgentArtifact).where(AgentArtifact.source_task_id == task.id)
    )

def prepare_scenes(
    gateway,
    db_factory,
    project_id: str,
    document,
    *,
    locked_story_facts: list[str] | None = None,
    allow_remote_text: bool = False,
) -> dict[str, Any]:
    """Run the scriptwriter over every scene of one document.

    Each scene is an independent answer: a scene that fails is reported with
    its reason and the other scenes still run, because a long script must not
    lose the scenes that did work.
    """
    scenes = segment_document(document.text or "")
    catalogue = known_span_ids(scenes)
    facts = list(locked_story_facts or [])

    refusal: dict[str, Any] = {}
    with db_factory() as db:
        try:
            profile = gateway.profile_for_role(db, SCRIPTWRITER_ROLE)
        except LLMError as exc:
            refusal = {"error_code": "LLM_NOT_CONFIGURED",
                       "detail": {"role": SCRIPTWRITER_ROLE, "code": exc.code}}
            profile = None
        if profile is not None and profile.kind != "mock" and not is_loopback_url(profile.base_url) \
                and not allow_remote_text:
            refusal = {"error_code": "REMOTE_AGENT_CONSENT_REQUIRED",
                       "detail": {"role": SCRIPTWRITER_ROLE,
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
    results: list[dict[str, Any]] = []
    preparations: list[dict[str, Any]] = []

    for index, scene in enumerate(scenes):
        if index:
            time.sleep(step_delay_seconds())
        context = build_scene_context(scene=scene, locked_story_facts=facts)
        input_hash = digest({
            "document_sha256": document.sha256,
            "context": context,
            "profile": profile.id,
            "model": profile.model,
        })
        with db_factory() as db:
            reused = _reusable_artifact(db, project_id, input_hash)
        if reused is not None:
            results.append({"scene_key": scene.key, "status": "REUSED",
                            "preparation": reused.payload_json, "artifact_id": reused.id})
            preparations.append(reused.payload_json)
            continue

        budget = budget_report(context, schema, system_prompt, config)
        if not budget["fits"]:
            results.append({"scene_key": scene.key, "status": "REFUSED",
                            "error_code": "LLM_CONTEXT_BUDGET_EXCEEDED", "budget": budget})
            continue

        with db_factory.begin() as db:
            task = AgentTaskRecord(
                project_id=project_id,
                role_id=SCRIPTWRITER_ROLE,
                input_hash=input_hash,
                style_version_id=None,
                asset_manifest_hash=input_hash,
                rulepack_id=RULEPACK_ID,
                rulepack_hash=rulepack_digest(),
                model_profile_id=profile.id,
                model_id=profile.model,
                schema_version="1.0",
                status="RUNNING",
                trace={
                    "tool": "script_prepare",
                    "schema": ScriptPreparation.__name__,
                    "scene_key": scene.key,
                    "document_sha256": document.sha256,
                    "budget": budget,
                    "is_mock": profile.kind == "mock",
                    "phase": "PREPARING",
                },
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
        )
        if not outcome.ok:
            status = "NEEDS_REVIEW" if outcome.uncertain else "FAILED"
            with db_factory.begin() as db:
                row = db.get(AgentTaskRecord, task_id)
                if row is not None:
                    row.status = "NEEDS_REVIEW" if outcome.uncertain else "REJECTED"
                    row.error_code = outcome.error_code
                    row.trace = {**row.trace,
                                 "phase": "FAILED",
                                 "correction_count": 1 if outcome.correction_used else 0,
                                 "schema_errors": outcome.schema_errors,
                                 "external_outcome": "UNKNOWN" if outcome.uncertain else "NOT_SENT",
                                 "automatic_replay": False}
            results.append({"scene_key": scene.key, "status": status,
                            "error_code": outcome.error_code})
            continue

        preparation = ScriptPreparation.model_validate(outcome.parsed).model_dump(mode="json")
        invented = unknown_span_ids(preparation, catalogue)
        if invented:
            with db_factory.begin() as db:
                row = db.get(AgentTaskRecord, task_id)
                if row is not None:
                    row.status = "REJECTED"
                    row.error_code = "MATERIAL_SOURCE_SPAN_UNKNOWN"
                    row.trace = {**row.trace, "phase": "REJECTED",
                                 "invented_span_ids": invented, "automatic_replay": False}
            results.append({"scene_key": scene.key, "status": "FAILED",
                            "error_code": "MATERIAL_SOURCE_SPAN_UNKNOWN",
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
            row.trace = {**row.trace, "phase": "SUCCEEDED", "output_hash": artifact.sha256,
                         "schema_valid": True,
                         "correction_count": 1 if outcome.correction_used else 0}
            add_audit(db, "local-user", "MATERIAL_SCENE_PREPARED", "agent_artifact", artifact_id,
                      {"scene_key": scene.key, "document_sha256": document.sha256},
                      project_id=project_id)
        results.append({"scene_key": scene.key, "status": "PREPARED",
                        "preparation": preparation, "artifact_id": artifact_id})
        preparations.append(preparation)

    return {
        "document_id": document.id,
        "document_sha256": document.sha256,
        "scenes": results,
        "preparations": preparations,
        "source_span_ids": sorted(catalogue),
    }

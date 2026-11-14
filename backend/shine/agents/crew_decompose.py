"""Per-beat / per-shot decomposition of the heavy crew roles.

A reasoning model spends most of its wall clock deliberating one monolithic
contract: measured live, the scriptwriter and director burn 10-13K reasoning
tokens for a ~2K visible answer, while the editor (a small focused contract)
spends ~1.4K and returns in seconds.  Coding agents stay fast by making many
small focused calls instead of one big one.  This module splits the
scriptwriter's beats into per-beat calls (plus one small globals call) and
assembles the original ``ScriptBreakdown`` server-side, so the downstream
artifact kind/sha/lineage is unchanged.

Safety: every external call owns its own durable intent and UNKNOWN gate.  Each
per-beat call is a separate ``AgentTaskRecord`` under a distinct internal role
(``scriptwriter_beat`` / ``scriptwriter_globals``) so it never collides with the
parent role's resume/replay scan; provider routing still uses the parent role.
The parent task owns the assembled artifact.  An UNKNOWN sub-call surfaces as
an UNKNOWN parent outcome — never an automatic replay.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.orm import Session

from shine.agents.execution import guard_request
from shine.agents.film_contracts import SceneBeat, ScriptBreakdown
from shine.h3.rulebook import digest
from shine.literary.budget import plan_call_budget
from shine.literary.requests import compile_request
from shine.llm.base import LLMError
from shine.llm.gateway import agent_output_config
from shine.models import AgentTaskRecord
from shine.schemas import StyleBible


# A small focused contract for the global, mostly-translational fields.  Keeping
# these out of the per-beat calls means each beat call reasons about one beat,
# not the whole cast and style bible.
def _globals_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "character_names": {"type": "object", "additionalProperties": {"type": "string"}},
            "prompt_story_facts": {"type": "array", "items": {"type": "string"}},
            "prompt_style": StyleBible.model_json_schema(),
        },
        "required": ["character_names", "prompt_story_facts"],
        "additionalProperties": False,
    }


def _subtask_call(
    db: Session,
    gateway,
    project_id: str,
    *,
    parent_role: str,
    sub_role: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    parent_task: AgentTaskRecord,
    output_ceiling: int,
    on_progress=None,
) -> tuple[dict, dict, int]:
    """One guarded small call: durable intent before, result sha after.

    Returns (parsed_payload, usage, latency_ms).  Raises LLMError/ValueError on
    refusal or UNKNOWN; the caller turns that into the parent outcome.
    """
    profile = gateway.profile_for_role(db, parent_role)
    bounded = agent_output_config(gateway.config_for(profile))
    request = compile_request(system=system, user=user, schema=schema)
    budget = plan_call_budget(bounded, request, purpose="structured",
                              output_ceiling=output_ceiling)
    if not budget.fits:
        raise ValueError(str((budget.refusal or {}).get("code") or "LLM_CREATIVE_CONTEXT_OVERFLOW"))
    input_hash = digest({"system": system, "user": user, "schema": schema})
    contract_hash = digest(schema)
    guard_request(db, project_id, sub_role, input_hash, profile,
                  contract_hash=contract_hash)
    task = AgentTaskRecord(
        project_id=project_id,
        role_id=sub_role,
        input_hash=input_hash,
        style_version_id=parent_task.style_version_id,
        asset_manifest_hash=parent_task.asset_manifest_hash,
        rulepack_id=parent_task.rulepack_id,
        rulepack_hash=parent_task.rulepack_hash,
        model_profile_id=profile.id,
        model_id=profile.model,
        schema_version="1.0",
        status="RUNNING",
        trace={
            "phase": "PREPARING",
            "parent_task_id": parent_task.id,
            "parent_role": parent_role,
            "is_mock": profile.kind == "mock",
            "decomposed": True,
            "call_budget": budget.as_dict(),
            "review_contract_sha256": contract_hash,
        },
    )
    db.add(task)
    db.commit()  # durable intent before the external call
    started = time.perf_counter()
    try:
        response = gateway.complete_for_role(
            db, parent_role, precompiled=request,
            budget={"completion_cap": budget.output_request_tokens,
                    "streaming": budget.streaming},
            on_progress=on_progress,
        )
    except LLMError as exc:
        from shine.agents.execution import rejection_reason
        task.status = "FAILED"
        task.error_code = str(exc)
        task.trace = {**task.trace, "phase": "FAILED", **rejection_reason(exc)}
        db.commit()
        raise
    latency_ms = int((time.perf_counter() - started) * 1000)
    task.status = "SUCCEEDED"
    task.trace = {
        **task.trace,
        "phase": "SUCCEEDED",
        "received_result_sha256": digest(response.parsed),
        "latency_ms": response.latency_ms,
        "usage": response.usage,
        "is_mock": bool(task.trace.get("is_mock") or response.raw_meta.get("is_mock")
                        or response.raw_meta.get("mock")),
    }
    db.commit()
    return response.parsed, response.usage or {}, latency_ms


def _split_beat_candidates(text: str, *, max_chars: int = 700) -> list[str]:
    """Deterministically pre-segment a scene into beat-sized text candidates.

    Paragraph boundaries first; a long paragraph is split on sentence ends so a
    single beat call never receives a wall of text.  The model still writes the
    beat description/dialogue; the server only fixes the text window it reads.
    """
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    candidates: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            candidates.append(para)
            continue
        buf = ""
        for sentence in para.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n"):
            sentence = sentence.strip()
            if not sentence:
                continue
            if buf and len(buf) + len(sentence) > max_chars:
                candidates.append(buf)
                buf = sentence
            else:
                buf = f"{buf}{sentence}" if not buf else f"{buf} {sentence}"
        if buf:
            candidates.append(buf)
    return candidates or [text.strip()]


def decompose_scriptwriter(
    db: Session,
    gateway,
    project_id: str,
    *,
    body,
    context: dict,
    system_prompt: str,
    parent_task: AgentTaskRecord,
    output_ceiling: int,
    on_progress=None,
) -> tuple[dict, dict, int, list[str]]:
    """Produce a ScriptBreakdown payload via one globals call + per-beat calls.

    Returns (payload, aggregate_usage, total_latency_ms, sub_task_roles).  The
    payload is validated by the caller through the unchanged validate_output.
    """
    text = body.brief
    candidates = _split_beat_candidates(text)
    assets = context.get("assets") or []
    style = context.get("style")
    locked = body.locked_story_facts or []
    sub_roles: list[str] = []
    total_latency = 0
    agg_usage: dict[str, int] = {}

    def _accumulate(usage: dict, latency: int, role: str):
        nonlocal total_latency
        total_latency += latency
        sub_roles.append(role)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(usage.get(key), int):
                agg_usage[key] = agg_usage.get(key, 0) + usage[key]
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if isinstance(rt, int):
            agg_usage["reasoning_tokens"] = agg_usage.get("reasoning_tokens", 0) + rt

    # 1. globals: character names + English style/fact translation (small task).
    globals_user = json.dumps({
        "scene_text": text,
        "locked_story_facts": locked,
        "style": style,
        "characters": [a.get("entity_id") or a.get("name") for a in assets
                       if a.get("kind") == "character" or a.get("role") == "character"],
    }, ensure_ascii=False, separators=(",", ":"))
    globals_payload, usage, latency = _subtask_call(
        db, gateway, project_id, parent_role="scriptwriter",
        sub_role="scriptwriter_globals",
        system=(system_prompt + "\n\nReturn ONLY the global fields: character_names "
                "(readable names keyed by original script label), prompt_story_facts "
                "(one faithful English visual translation per non-English locked fact, "
                "in order) and prompt_style (faithful English rendering of the seven "
                "frozen style fields; leave film_type unset). Do not return beats."),
        user=globals_user, schema=_globals_schema(),
        parent_task=parent_task, output_ceiling=output_ceiling, on_progress=on_progress,
    )
    _accumulate(usage, latency, "scriptwriter_globals")

    # 2. one small call per beat candidate.
    beats: list[dict] = []
    beat_schema = SceneBeat.model_json_schema()
    for index, chunk in enumerate(candidates, 1):
        beat_user = json.dumps({
            "beat_index": index,
            "beat_text": chunk,
            "locked_story_facts": locked,
            "character_names": globals_payload.get("character_names") or {},
            "characters": [a.get("entity_id") or a.get("name") for a in assets
                           if a.get("kind") == "character" or a.get("role") == "character"],
        }, ensure_ascii=False, separators=(",", ":"))
        beat_payload, usage, latency = _subtask_call(
            db, gateway, project_id, parent_role="scriptwriter",
            sub_role="scriptwriter_beat",
            system=(system_prompt + f"\n\nReturn ONLY one SceneBeat (id, description, "
                    f"characters, dialogue) for beat {index}, the text window provided. "
                    f"Keep dialogue verbatim with speaker and punctuation; dialogue "
                    f"timecodes are local to this beat. Use the approved character names."),
            user=beat_user, schema=beat_schema,
            parent_task=parent_task, output_ceiling=output_ceiling, on_progress=on_progress,
        )
        _accumulate(usage, latency, "scriptwriter_beat")
        beat = SceneBeat.model_validate(beat_payload)
        beats.append(beat.model_dump(mode="json"))

    payload = {
        "schema_version": "1.1",
        "character_names": globals_payload.get("character_names") or {},
        "locked_story_facts": locked,
        "prompt_story_facts": globals_payload.get("prompt_story_facts") or [],
        "prompt_style": globals_payload.get("prompt_style"),
        "beats": beats,
    }
    # Validate the assembled contract here so a bad assembly fails before the
    # caller records a SUCCEEDED parent; the caller re-validates with full context.
    ScriptBreakdown.model_validate(payload)
    return payload, agg_usage, total_latency, sub_roles
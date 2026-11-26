"""Controlled film work units on the existing gateway and application database."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from shine.agents import continuity_engine, scene_director
from shine.agents.cinema_skills import select_skills_for_role
from shine.agents.crew_prompts import arbiter_prompt, crew_prompt
from shine.agents.editorial_timing import (
    MAX_SEGMENT_S,
    editorial_timing_blocker,
    editorial_timing_plan,
    editorial_timing_preview,
)
from shine.agents.film_contracts import (
    CameraPlan,
    ContinuityArbitration,
    ContinuityReport,
    DirectorialPlan,
    DramaSceneScript,
    EditDecisionList,
    EditIntentPlan,
    FilmAgentRegistry,
    FilmRunRequest,
    ScriptBreakdown,
    SeriesArchitecture,
    SoundPlan,
    StyleInputAssessment,
    StyleInputWire,
)
from shine.asset_identity import (
    canonical_character_name_for_asset_id,
    split_character_names,
)
from shine.director_styles.service import bind_editing_style, validate_binding
from shine.h3.rulebook import H3RulebookRegistry, digest
from shine.h3.spoken_language import DEFAULT_SPOKEN_LANGUAGE, canonical_spoken_language
from shine.llm.base import LLMError, text_provider_snapshot
from shine.llm.context_window import model_context_for_profile
from shine.llm.gateway import LLMGateway
from shine.media.artifacts import safe_asset_path
from shine.models import (
    AgentArtifact,
    AgentTaskRecord,
    AppSetting,
    Asset,
    Job,
    MediaArtifact,
    QualityGate,
    StyleVersion,
    WorkflowProfile,
    utcnow,
)
from shine.schemas import DEFAULT_MAX_SHOT_SECONDS
from shine.security import is_loopback_url
from shine.services import add_audit, mark_stale_descendants, require_project, try_transition


def asset_context(db: Session, project_id: str) -> list[dict]:
    assets = db.scalars(
        select(Asset)
        .where(Asset.project_id == project_id, Asset.approved.is_(True), Asset.locked.is_(True))
        .order_by(Asset.id)
    ).all()
    return [
        {
            "id": a.id,
            "sha256": a.sha256,
            "kind": a.kind,
            "entity_id": a.entity_id,
            "role": a.role,
            "duration_s": a.duration_s,
        }
        for a in assets
    ]


# The reference set of a shot is a product decision, not a matter of taste:
# every shot of a sequence references the character masters that appear in it,
# plus relevant approved props. A location/picture master is recommended, and a
# voice master is optional: H3 speaks a line natively, so binding a generated
# voice is an enhancement for a fixed timbre, never a precondition.
SHOT_REFERENCE_REQUIRED_ROLES = ("character",)
SHOT_REFERENCE_OPTIONAL_ROLES = ("voice",)
SHOT_REFERENCE_LOCATION_ROLES = ("scene", "picture")
SHOT_REFERENCE_EXCLUDED_ROLES = ("style_reference", "last_frame")


def shot_reference_policy(db: Session, project_id: str, plan: dict) -> dict:
    """The machine-checked reference-set facts of one directorial plan.

    The reviewer of a plan must not have to infer the reference policy from
    the asset list: a deliberate exclusion reads exactly like an omission
    when it is left to interpretation.  This function states the policy and
    checks every shot against it, so a compliant shot is a fact rather than
    an opinion.
    """
    assets = {a["id"]: a for a in asset_context(db, project_id)}
    excluded = [
        {"id": a["id"], "entity_id": a["entity_id"], "role": a["role"]}
        for a in assets.values()
        if a["role"] in SHOT_REFERENCE_EXCLUDED_ROLES
    ]
    shots = []
    for shot in plan.get("shots", []):
        used = [assets[i] for i in shot.get("asset_ids", []) if i in assets]
        roles = sorted({a["role"] for a in used})
        missing = sorted(set(SHOT_REFERENCE_REQUIRED_ROLES) - set(roles))
        if not set(roles) & set(SHOT_REFERENCE_LOCATION_ROLES):
            missing.append("scene|picture")
        excluded_used = [a["id"] for a in used if a["role"] in SHOT_REFERENCE_EXCLUDED_ROLES]
        shots.append({
            "shot_id": shot.get("id"),
            "asset_ids": list(shot.get("asset_ids", [])),
            "roles": roles,
            "missing_required_roles": missing,
            # A master the material stage has not prepared yet is a request for
            # that stage, not a policy violation: the storyboard names what it
            # needs, the shot lands anyway and H3 generates what is missing.
            # Only a reference the policy forbids makes a shot non-compliant.
            "policy_compliant": not excluded_used,
            "material_request": missing,
            "excluded_assets_used": excluded_used,
            "continuity_mode": (shot.get("continuity") or {}).get("mode"),
        })
    return {
        "policy": {
            "shot_reference_set": "the character masters that appear in the shot, plus a location/picture "
                                  "master and approved props; a voice master is optional and only improves "
                                  "timbre continuity",
            "required_roles": list(SHOT_REFERENCE_REQUIRED_ROLES),
            "optional_roles": list(SHOT_REFERENCE_OPTIONAL_ROLES),
            "location_roles": list(SHOT_REFERENCE_LOCATION_ROLES),
            "excluded_from_shot_references": excluded,
            "material_request": "the roles a shot still waits for. The material stage prepares them; "
                                "the shot lands anyway and H3 generates what is missing, so a material "
                                "request is never a continuity conflict.",
            "last_frame_mode": (
                "a soft relation to the preceding shot: the shot continues from the previous shot's "
                "last frame. It is not a frame anchor and it needs no reference image."
            ),
            "dialogue_voice": (
                "H3 generates the spoken line from the dialogue block; a bound voice master is an "
                "optional fixed-timbre reference, never a requirement."
            ),
        },
        "reference_set_status": shots,
    }


_REFERENCE_MARKERS = ("缺少主素材", "主素材", "material_request", "missing_required_roles")
"""The wording a report uses when it names a master the material stage owes.

The markers are the product's own words for a master asset, so a report
about a missing master is recognised without swallowing a report about the
story, the timing or the dialogue.
"""


def material_request_issues(db: Session, project_id: str, plan: dict, issues: list[str]) -> list[str]:
    """The supervisor's issues that only report a master the material stage must prepare.

    A missing master is a request for the material step, never a continuity
    conflict: the storyboard names what it needs, the shot lands anyway and
    H3 generates what is missing.  An issue about the story, the timing or the
    dialogue keeps its own text and still blocks the unit.

    The classification uses the server's own facts: an issue that names only
    shots whose gap is a material request, and that speaks about the reference
    set, is a request.  Anything else stays a conflict, so an unclear report
    keeps blocking rather than passing unnoticed.
    """
    status = shot_reference_policy(db, project_id, plan or {})
    by_id = {row["shot_id"]: row for row in status.get("reference_set_status", []) if row.get("shot_id")}
    requests = []
    for issue in issues or []:
        named = [shot_id for shot_id in by_id if shot_id in issue]
        if not named:
            continue
        gaps = [by_id[shot_id].get("material_request") or [] for shot_id in named]
        if not all(gaps):
            continue
        roles = {role for gap in gaps for role in gap}
        if any(role in issue for role in roles) or any(marker in issue for marker in _REFERENCE_MARKERS):
            requests.append(issue)
    return requests


def workflow_reference_budget(db: Session, project_id: str) -> list[dict]:
    """The image/audio slot counts of this project's registered H3 workflows.

    The director proposes reference sets before any workflow is chosen for a
    shot; without the mapped slot counts a plan can promise more references
    than every registered workflow can carry, which only surfaces as
    PENDING_WORKFLOW_MAPPING at render time.
    """
    budgets = []
    for profile in db.scalars(
        select(WorkflowProfile).where(WorkflowProfile.project_id == project_id)
    ):
        keys = (profile.parameter_map or {}).keys()
        budgets.append(
            {
                "id": profile.id,
                "name": profile.name,
                "validated": bool(profile.validated_against),
                # A profile may carry images as one positional sequence
                # (`reference_image_1..n`) instead of per-family slots; either
                # spelling is the same capacity to the director.
                "image_slots": sum(1 for k in keys
                                   if k.startswith(("reference_subject_", "reference_picture_", "reference_image_"))),
                "audio_slots": sum(1 for k in keys if k.startswith("reference_audio_")),
            }
        )
    return budgets


def reference_budget_note(budgets: list[dict]) -> str:
    """Prompt text stating the verified slot budget, or nothing when unverified.

    Only a validated profile bounds the plan: an unregistered or unverified
    workflow proves nothing about what a render can carry.
    """
    usable = [b for b in budgets if b["validated"] and b["image_slots"]]
    if not usable:
        return ""
    best = max(usable, key=lambda b: (b["image_slots"], b["audio_slots"]))
    return (
        f" Registered workflow reference budget: the verified workflow '{best['name']}' maps at most "
        f"{best['image_slots']} image reference slots and {best['audio_slots']} audio reference slot(s) "
        "per render segment. Keep every shot's asset_ids within this budget: choose the identity-critical "
        "picture masters (the segment's lead performer as the subject image, plus the most continuity-critical "
        "location or partner images). A voice master is optional and only uses an audio slot when the "
        "project really wants that fixed timbre. "
        "A shot whose references exceed the mapped slots blocks the render."
    )


def eligible_edits(db: Session, project_id: str, *, offline: bool, artifact_root: Path | None = None) -> list[dict]:
    result = []
    for job in db.scalars(select(Job).where(Job.project_id == project_id, Job.status == "PASSED")):
        if job.stale or not job.artifact_id or not job.attempts or job.shot.revision != job.shot_revision:
            continue
        attempt = max(job.attempts, key=lambda a: a.attempt_no)
        artifact = db.get(MediaArtifact, job.artifact_id)
        if not artifact or artifact.attempt_id != attempt.id:
            continue
        if artifact_root is not None:
            path = safe_asset_path(artifact_root, artifact.storage_path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
                continue
        gates = db.scalars(
            select(QualityGate).where(QualityGate.job_id == job.id, QualityGate.attempt_id == attempt.id)
        ).all()
        by_name = {g.gate_name: g for g in sorted(gates, key=lambda g: g.created_at)}
        if any(name not in by_name for name in ("technical", "semantic", "audio", "style", "final")):
            continue
        if by_name["technical"].status not in {"passed", "not_applicable"}:
            continue
        if any(
            gate.status not in {"passed", "not_applicable"}
            and not (gate.evidence or {}).get("advisory")
            and not (offline and not gate.required)
            for name, gate in by_name.items()
            if name in {"semantic", "audio", "style"}
        ):
            continue
        final_gate = by_name["final"]
        if final_gate.status not in {"passed", "not_applicable"}:
            final_evidence = final_gate.evidence or {}
            advisory_only_pending = (
                bool(final_evidence.get("advisory_pending_human_review"))
                and not final_evidence.get("unresolved_gates")
                and not final_evidence.get("unresolved_optional_gates")
            )
            if not advisory_only_pending:
                continue
        if not offline and (
            artifact.is_mock
            or job.quality_verification not in {"real", "manual_verified"}
            or any(g.is_mock for g in by_name.values())
        ):
            continue
        result.append(
            {
                "job_id": job.id,
                "shot_id": job.shot_id,
                "shot_name": job.shot.name,
                "story_beat": job.shot.spec.get("story_beat", ""),
                "attempt_id": attempt.id,
                "artifact_id": artifact.id,
                "duration_s": job.request["expected_duration_s"],
                "sha256": artifact.sha256,
                "is_mock": artifact.is_mock,
                "verification": job.quality_verification,
                "shot_revision": job.shot_revision,
            }
        )
    return result


def load_inputs(db: Session, project_id: str, ids: list[str], *, seen: set[str] | None = None) -> list[AgentArtifact]:
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_INPUT_REVISION")
    seen = set() if seen is None else seen
    project = require_project(db, project_id)
    rows = []
    for identity in ids:
        if identity in seen:
            raise ValueError("ARTIFACT_DEPENDENCY_CYCLE")
        artifact = db.get(AgentArtifact, identity)
        if not artifact or artifact.project_id != project_id or artifact.stale:
            raise ValueError("INPUT_REVISION_UNAVAILABLE")
        if digest(artifact.payload_json) != artifact.sha256:
            raise ValueError("ARTIFACT_HASH_MISMATCH")
        task = db.get(AgentTaskRecord, artifact.source_task_id)
        if (
            not task
            or task.status != "SUCCEEDED"
            or task.asset_manifest_hash != digest(asset_context(db, project_id))
            or task.style_version_id != project.frozen_style_version_id
        ):
            raise ValueError("INPUT_CONTEXT_CHANGED")
        if task.trace.get("offline_preview") and artifact.kind == "EditDecisionList":
            # Preview proposals must never be consumed by production roles.
            raise ValueError("PREVIEW_NOT_PRODUCTION_INPUT")
        load_inputs(db, project_id, artifact.input_revision_ids, seen=seen | {identity})
        rows.append(artifact)
    return rows


def invalidate_artifact(db: Session, project_id: str, artifact_id: str):
    rows = list(db.scalars(select(AgentArtifact).where(AgentArtifact.project_id == project_id)))
    affected = {artifact_id}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.id not in affected and affected.intersection(row.input_revision_ids):
                affected.add(row.id)
                changed = True
    for row in rows:
        if row.id in affected:
            row.stale = True
            applied = db.get(AppSetting, f"film-apply:{row.id}")
            if applied:
                jobs = list(
                    db.scalars(
                        select(Job).where(
                            Job.project_id == project_id, Job.shot_id.in_(applied.value.get("shot_ids", []))
                        )
                    )
                )
                mark_stale_descendants(db, [job.id for job in jobs], project_id=project_id)
                for job in jobs:
                    job.stale = True
                    job.failure_code = "AGENT_DEPENDENCY_INVALIDATED"
                    if job.status != "FAILED":
                        try_transition(job, "NEEDS_REVIEW")
                    add_audit(
                        db,
                        "orchestrator",
                        "AGENT_DEPENDENCY_INVALIDATED",
                        "job",
                        job.id,
                        {"source_artifact_id": row.id},
                        project_id=project_id,
                    )


def input_text_at(spec: dict, pointer: str | None) -> str | None:
    if pointer is None:
        return None
    try:
        value = spec
        for raw in pointer[1:].split("/"):
            if "~" in raw.replace("~0", "").replace("~1", ""):
                raise ValueError("invalid pointer escape")
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                value = value[key]
            elif isinstance(value, list) and key.isdigit() and (key == "0" or not key.startswith("0")):
                value = value[int(key)]
            else:
                raise ValueError("not a source path")
        if not isinstance(value, str) or not value:
            raise ValueError("not source text")
        return value
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("STYLE_INPUT_SHOT_PATH_INVALID") from exc


def bind_input_judgments(data, context):
    wire = StyleInputWire.model_validate(data)
    checks = [{**check.model_dump(), "style_text": context["style"][check.field],
               "shot_text": input_text_at(context["shot_spec"], check.shot_path)} for check in wire.checks]
    return StyleInputAssessment(checks=checks, recommendation=wire.recommendation)


def registry_rules() -> list[dict]:
    """Every rule of the locked H3 rule pack, by id."""
    return H3RulebookRegistry().search(())


def _visual_language_error(field: str) -> ValueError:
    """Name the offending field so a bounded retry can repair exactly it."""
    error = ValueError("VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")
    error.details = {"visual_field": field}
    return error


#: A quoted span is content the plan wants rendered as such - a spoken line in
#: a ``<d>`` block, or an inscription the story needs shown verbatim - so the
#: language check reads the prose around it.  Every quotation convention the
#: models actually emit is recognised, not just ASCII: live evidence
#: (2026-11-15, scene 2) is the beat of this same pipeline quoting the carved
#: mark as “沈禾”, and a faithful English description that copied that span was
#: refused as Chinese prose three times.
_QUOTED_SPAN = re.compile(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」|“[^”]*”|‘[^’]*’|『[^』]*』")


def descriptive_text(text: str) -> str:
    """The prose of a visual or camera field, without its deliberate quotations."""
    return _QUOTED_SPAN.sub("", text or "")


def _normalised_dialogue(text: str) -> str:
    """Typography-insensitive form of a line: whitespace and ellipsis/dash
    variants carry no content, so a faithful copy must not fail on them."""
    text = re.sub(r"\s+", "", text or "")
    text = text.replace("……", "…").replace("...", "…")
    text = text.replace("——", "—").replace("--", "—")
    return text


def validate_output(role: str, proposal, body: FilmRunRequest, inputs: list[AgentArtifact], context: dict):
    if isinstance(proposal, StyleInputAssessment):
        def text_values(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from text_values(child)
            elif isinstance(value, list):
                for child in value:
                    yield from text_values(child)

        supplied = tuple(text_values(context["shot_spec"]))
        for check in proposal.checks:
            if check.style_text != context["style"][check.field]:
                raise ValueError("STYLE_INPUT_FROZEN_QUOTE_CHANGED")
            if check.shot_text is not None and not any(check.shot_text in text for text in supplied):
                raise ValueError("STYLE_INPUT_SHOT_QUOTE_INVENTED")
            if check.shot_path is not None and input_text_at(context["shot_spec"], check.shot_path) != check.shot_text:
                raise ValueError("STYLE_INPUT_SHOT_QUOTE_INVENTED")
    elif isinstance(proposal, ScriptBreakdown):
        if proposal.locked_story_facts != body.locked_story_facts:
            raise ValueError("LOCKED_FACTS_CHANGED")
        style_values = (context.get("style") or {}).values()
        if any(re.search(r"[\u3400-\u9fff]", value) for value in style_values if isinstance(value, str)):
            translated_style = proposal.prompt_style
            if not translated_style or any(re.search(r"[\u3400-\u9fff]", value)
                                           for value in translated_style.model_dump().values()
                                           if isinstance(value, str)):
                raise ValueError("H3_STYLE_TRANSLATION_REQUIRED")
        if any(re.search(r"[\u3400-\u9fff]", fact) for fact in body.locked_story_facts):
            translations = proposal.prompt_story_facts
            if (len(translations) != len(body.locked_story_facts)
                    or any(not text.strip() or re.search(r"[\u3400-\u9fff]", text) for text in translations)):
                raise ValueError("H3_LOCKED_FACT_TRANSLATION_REQUIRED")
        if len({beat.id for beat in proposal.beats}) != len(proposal.beats):
            raise ValueError("DUPLICATE_BEAT")
        ratio = context.get("dialogue_ratio_percent")
        narration_level = context.get("narration_level")
        timing = (context.get("editorial_timing_plan") or {}).get("editorial_shot_lengths_s") or []
        # The source cast is the one character fact source for this unit (the
        # resolved production_context characters), not a substring of the brief:
        # a segment may refer to an established character by pronoun throughout,
        # so requiring the literal name in the brief refused a real cast member's
        # adapted line. The brief stays a fallback for a name the cast list omits.
        source_cast: set[str] = set()
        for character in (context.get("production_context") or {}).get("characters") or []:
            if isinstance(character, dict):
                for key in ("name", "entity_id"):
                    value = character.get(key)
                    if isinstance(value, str) and value.strip():
                        source_cast.add(value.strip())
        for index, beat in enumerate(proposal.beats):
            lines = sorted(beat.dialogue, key=lambda line: line.start_s)
            for position, line in enumerate(lines):
                if line.text in body.brief:
                    continue
                if _normalised_dialogue(line.text) in _normalised_dialogue(body.brief):
                    # Faithful copies that only differ in whitespace or in
                    # ellipsis/dash variants are still the user's line; the
                    # no-invention contract is about content, not typography.
                    continue
                from shine.h3.narrator import is_narrator_speaker

                speakers = split_character_names(line.speaker_id)
                is_narration = bool(speakers) and all(is_narrator_speaker(name) for name in speakers)
                if is_narration and narration_level == "none":
                    # The person switched narration off; a narrator line is an
                    # invented line like any other.
                    error = ValueError("DIALOGUE_NOT_IN_USER_SCRIPT")
                    error.details = {"offending_line": line.text, "speaker": line.speaker_id}
                    raise error
                if is_narration and narration_level in {"low", "high"}:
                    # Narration is a chosen style, so the ratio gate does not
                    # apply to it; its cast and its window still do.
                    if any(name not in beat.characters for name in speakers):
                        raise ValueError("NARRATOR_NOT_LISTED_IN_BEAT")
                    window = timing[index] if index < len(timing) else 15
                    if (not line.text.strip() or line.end_s > window
                            or position > 0 and line.start_s < lines[position - 1].end_s
                            or position + 1 < len(lines) and line.end_s > lines[position + 1].start_s):
                        raise ValueError("ADAPTED_DIALOGUE_OUTSIDE_BEAT_WINDOW")
                    continue
                if ratio == 0:
                    # The user explicitly asked for no dialogue: any line the
                    # script does not contain is a refusal.
                    error = ValueError("DIALOGUE_NOT_IN_USER_SCRIPT")
                    error.details = {"offending_line": line.text, "speaker": line.speaker_id}
                    raise error
                # With no ratio stated (or a positive one) an adapted line is
                # allowed, but its speaker must belong to the beat and to the
                # source cast, and its timecodes must stay inside the beat window.
                if not speakers or any(
                    name not in beat.characters
                    or (name not in source_cast and name not in body.brief)
                    for name in speakers
                ):
                    error = ValueError("ADAPTED_DIALOGUE_SPEAKER_NOT_IN_SOURCE_BEAT")
                    error.details = {"offending_line": line.text, "speaker": line.speaker_id,
                                     "beat_characters": list(beat.characters)}
                    raise error
                window = timing[index] if index < len(timing) else 15
                if (not line.text.strip() or line.end_s > window
                        or position > 0 and line.start_s < lines[position - 1].end_s
                        or position + 1 < len(lines) and line.end_s > lines[position + 1].start_s):
                    raise ValueError("ADAPTED_DIALOGUE_OUTSIDE_BEAT_WINDOW")
    elif isinstance(proposal, DirectorialPlan):
        script = next(a for a in inputs if a.kind == "ScriptBreakdown")
        beats = {b["id"]: b for b in script.payload_json["beats"]}
        assets = {a["id"] for a in context["assets"]}
        if len({s.id for s in proposal.shots}) != len(proposal.shots):
            raise ValueError("DUPLICATE_SHOT_INTENT")
        timing = context.get("editorial_timing")
        cap = context.get("max_shot_seconds")
        timeline = []
        elapsed = 0.0
        for shot_index, shot in enumerate(proposal.shots):
            # The same per-shot ceiling the editorial segmentation and the
            # director prompt were given; a shot above it is refused by name
            # rather than landed and rejected at render time.
            if cap is not None and shot.duration_s > cap + 1e-6:
                raise ValueError("SHOT_DURATION_OVER_LIMIT")
            # Spoken words belong to the dialogue structure, never to the
            # visual prose: text the model renders as picture description is
            # otherwise read aloud by H3 outside the audio channel.
            visual_fields = [(f"shots[{shot_index}].visual_description", shot.visual_description)]
            visual_fields += [(f"shots[{shot_index}].cuts[{cut_index}].visual_description",
                               cut.visual_description)
                              for cut_index, cut in enumerate(shot.cuts)]
            for field, text in visual_fields:
                if re.search(r"[㐀-鿿]", descriptive_text(text)):
                    raise _visual_language_error(field)
            beat_ids = [shot.beat_id, *(cut.beat_id for cut in shot.cuts)]
            starts = [0.0, *(cut.start_s for cut in shot.cuts)]
            if any(b not in beats for b in beat_ids) or not set(shot.asset_ids).issubset(assets):
                raise ValueError("UNAPPROVED_ASSET_OR_UNKNOWN_BEAT")
            if starts != sorted(set(starts)) or starts[-1] >= shot.duration_s:
                raise ValueError("EDITORIAL_TIMING_CONFLICT")
            # Every spoken line must end inside the shot interval that performs
            # it, whether or not the script carries a cut table.  Without this a
            # shot shorter than its own line was accepted here and silently
            # squeezed at landing; the conflict is named where the model can
            # repair it.
            ends = starts[1:] + [shot.duration_s]
            for beat_id, start, end in zip(beat_ids, starts, ends, strict=True):
                if any(line["end_s"] > end - start + 1e-6 for line in beats[beat_id].get("dialogue", [])):
                    raise ValueError("EDITORIAL_TIMING_CONFLICT")
            locations = context.get("locations")
            if locations and shot.location not in {entry.get("name") for entry in locations if isinstance(entry, dict)}:
                # A place nobody registered cannot be bound to a master later.
                raise ValueError("SHOT_LOCATION_UNKNOWN")
            timeline.extend((beat_id, round(elapsed + start, 3))
                            for beat_id, start in zip(beat_ids, starts, strict=True))
            elapsed += shot.duration_s
            # 分镜提出的是**需求**，不是素材：这一镜要用的角色若还没有主素材，
            # 那是素材层要准备的事，不是分镜该停下的理由。拦住它的是后面的
            # 两道门：素材准备这一步（镜头点名的角色必须有绑定且已确认锁定的
            # 主素材），以及导演包冻结。需求清单由 API 层如实计算并随答案
            # 交给素材层。
        if timing and (round(elapsed, 3) != timing["total_s"]
                       or timeline != list(zip(beats, timing["cut_starts_s"], strict=False))
                       or len(timeline) != timing["editorial_shot_count"]
                       or len(beats) != len(timeline)):
            raise ValueError("EDITORIAL_TIMING_CONFLICT")
    elif isinstance(proposal, CameraPlan):
        plan = next(a for a in inputs if a.kind == "DirectorialPlan")
        expected = {s["id"] for s in plan.payload_json["shots"]}
        actual = [s.intent_id for s in proposal.shots]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("CAMERA_INTENT_COVERAGE")
        for shot_index, shot in enumerate(proposal.shots):
            # Same rule as the director's visual prose: camera language feeds
            # the compiled H3 prompt and must stay in the prompt language.
            for field in ("composition", "movement", "lighting", "axis"):
                if re.search(r"[㐀-鿿]", descriptive_text(getattr(shot, field))):
                    raise _visual_language_error(f"shots[{shot_index}].{field}")
    elif isinstance(proposal, SoundPlan):
        voices = {a["id"] for a in context["assets"] if a["kind"] == "audio"}
        if not set(proposal.voice_asset_ids).issubset(voices):
            raise ValueError("UNAPPROVED_VOICE")
    elif isinstance(proposal, EditDecisionList):
        eligible = {(r["job_id"], r["attempt_id"], r["artifact_id"]): r for r in context["eligible_edits"]}
        for clip in proposal.clips:
            source = eligible.get((clip.job_id, clip.attempt_id, clip.artifact_id))
            if not source or clip.out_s > source["duration_s"]:
                raise ValueError("EDL_SOURCE_NOT_PASSED_OR_INVALID_RANGE")
    elif isinstance(proposal, EditIntentPlan):
        plan = next(a for a in inputs if a.kind == "DirectorialPlan")
        intents = {s["id"] for s in plan.payload_json["shots"]}
        actual = [clip.intent_id for clip in proposal.clips]
        if set(actual) != intents or len(actual) != len(intents):
            raise ValueError("EDIT_INTENT_INTENT_COVERAGE")
    elif isinstance(proposal, ContinuityArbitration):
        # The arbitration is only usable when it rules on EVERY raised issue
        # and cites rules that exist in the locked pack.  A ruling that leaves
        # an issue unaddressed, or that cites a rule nobody can verify, is a
        # contract violation of the arbiter, not a decision.
        report = next(a for a in inputs if a.kind == "ContinuityReport")
        issues = list(report.payload_json.get("issues") or [])
        ruled = sorted(ruling.issue_index for ruling in proposal.rulings)
        if ruled != list(range(len(issues))):
            raise ValueError("ARBITRATION_ISSUE_COVERAGE")
        known = {rule["id"] for rule in registry_rules()}
        if any(ruling.rule_id not in known for ruling in proposal.rulings):
            raise ValueError("ARBITRATION_UNKNOWN_RULE")
        # The server binds the authoritative issue text of every ruling.
        for ruling in proposal.rulings:
            ruling.issue = issues[ruling.issue_index]
    elif isinstance(proposal, SeriesArchitecture):
        # The plan is the person's: an architecture that invents, drops or
        # repeats one of its episodes or sections, or stages a scene in a place
        # it never registered, is refused by name.  The same verdict reads the
        # same confirmed plan the architect read.
        from shine.literary.drama import architecture_verdict

        refusals = architecture_verdict(proposal.model_dump(mode="json"), context)
        if refusals:
            raise _refusal_error(refusals[0]["code"], refusals)
    elif isinstance(proposal, DramaSceneScript):
        # Speech that cannot be spoken inside a shot is the one hard refusal:
        # no storyboard can film a line that does not fit.  An inner state or
        # a character the story never established is reported with the scene,
        # never used to send it back.
        from shine.literary.drama import scene_verdict

        verdict = scene_verdict(proposal.model_dump(mode="json"), context)
        if verdict.refusals:
            raise _refusal_error(verdict.first_code(), verdict.refusals)
    elif isinstance(proposal, scene_director.SceneShotBoard):
        scene_artifact = next((a for a in inputs if a.kind == "DramaSceneScript"), inputs[0] if inputs else None)
        if scene_artifact is None:
            raise ValueError("MISSING_OR_AMBIGUOUS_HANDOFF")
        payload = proposal.model_dump(mode="json")
        production = context.get("production_context") or {}
        verdict = continuity_engine.review_scene_board(
            payload,
            scene_artifact.payload_json,
            {
                "max_shot_seconds": context.get("max_shot_seconds"),
                "continuity_predecessor": bool(production.get("continuity_predecessor")),
                "previous_blocking": production.get("previous_shot_blocking"),
                "registered_locations": production.get("registered_locations") or [],
                "character_tokens": production.get("character_tokens") or [],
            },
        )
        if verdict.refusals:
            raise _refusal_error(verdict.first_code(), verdict.refusals)
        for field, text_value in continuity_engine.english_prose_fields(payload):
            if re.search(r"[㐀-鿿]", descriptive_text(text_value)):
                raise _visual_language_error(field)
        assets_by_id = {a["id"]: a for a in context["assets"]}
        for shot in proposal.shots:
            if not set(shot.asset_ids).issubset(assets_by_id):
                raise ValueError("UNAPPROVED_ASSET_OR_UNKNOWN_BEAT")
            if any(assets_by_id[aid]["role"] in SHOT_REFERENCE_EXCLUDED_ROLES for aid in shot.asset_ids):
                raise ValueError("EXCLUDED_REFERENCE_USED")
        style_values = (context.get("style") or {}).values()
        if any(re.search(r"[\u3400-\u9fff]", value) for value in style_values if isinstance(value, str)):
            translated_style = proposal.prompt_style
            if not translated_style or any(
                re.search(r"[\u3400-\u9fff]", value)
                for value in translated_style.model_dump().values()
                if isinstance(value, str)
            ):
                raise ValueError("H3_STYLE_TRANSLATION_REQUIRED")
        if any(re.search(r"[\u3400-\u9fff]", fact) for fact in body.locked_story_facts):
            translations = proposal.prompt_story_facts
            if (
                len(translations) != len(body.locked_story_facts)
                or any(not text.strip() or re.search(r"[\u3400-\u9fff]", text) for text in translations)
            ):
                raise ValueError("H3_LOCKED_FACT_TRANSLATION_REQUIRED")


def _refusal_error(code: str, refusals: list[dict]) -> ValueError:
    """A named refusal that carries its details for the retry's repair note."""
    error = ValueError(code)
    error.details = {"refusal_details": refusals[:12]}
    return error


def verified_context(db: Session, project_id: str, body: FilmRunRequest) -> dict:
    """The one reader of a crew call's verified production context.

    Before and after every model call the context a caller claims is rebuilt
    from the ledger.  The architect addresses a whole confirmed plan; every
    other tool addresses one unit of the source, and the drama writer must
    find the architecture that ranks its scene.
    """
    from shine.materialprep import story

    if body.tool == "series_architecture":
        return story.verified_architecture_context(db, project_id, body.brief, body.production_context)
    return story.verified_production_context(
        db, project_id, body.brief, body.production_context,
        require_architecture=body.tool == "drama_scene")


def canonicalize_script_character_asset_ids(proposal, assets: list[dict]):
    """Keep asset UUIDs in binding fields, not in human-readable character fields."""
    if not isinstance(proposal, ScriptBreakdown):
        return proposal
    beats = []
    for beat in proposal.beats:
        characters = list(dict.fromkeys(
            canonical_character_name_for_asset_id(name, assets) or name
            for name in beat.characters
        ))
        dialogue = [
            line.model_copy(update={
                "speaker_id": canonical_character_name_for_asset_id(line.speaker_id, assets) or line.speaker_id
            })
            for line in beat.dialogue
        ]
        beats.append(beat.model_copy(update={"characters": characters, "dialogue": dialogue}))
    return proposal.model_copy(update={"beats": beats})


def _plan_beat_ids(role: str, inputs: list[AgentArtifact]) -> list[str] | None:
    """The beat id of every editorial shot, in the script's own order.

    The scriptwriter returns one beat per editorial shot, so the beat order is
    the editorial shot order.  A role that reads the director's plan sees the
    same order through each segment's opening beat and its internal cuts.
    """
    if role == "director":
        source = next((a for a in inputs if a.kind == "ScriptBreakdown"), None)
        beats = list(source.payload_json.get("beats") or []) if source else []
        return [beat.get("id") for beat in beats if beat.get("id")]
    if role in {"cinematographer", "continuity_supervisor", "editor"}:
        source = next((a for a in inputs if a.kind == "DirectorialPlan"), None)
        shots = list(source.payload_json.get("shots") or []) if source else []
        beat_ids: list[str] = []
        for shot in shots:
            if shot.get("beat_id"):
                beat_ids.append(shot["beat_id"])
            beat_ids.extend(cut.get("beat_id") for cut in shot.get("cuts") or [] if cut.get("beat_id"))
        return beat_ids
    return None


logger = logging.getLogger(__name__)


def _storyboard_memory_view(db, project_id: str, *, role: str) -> dict | None:
    """The bounded fact view one storyboard role may receive.

    Only in ``on``: ``shadow`` records facts but must not change a paid request.
    A memory fault returns ``None`` and the crew works exactly as before V2.1.
    """
    from shine.literary import flags, memory

    try:
        if not flags.affects_request("memory", db, project_id):
            return None
        return memory.storyboard_view(
            db, project_id, token_budget=900 if role == "scriptwriter" else 600)
    except Exception:  # noqa: BLE001 - guidance never blocks the crew
        logger.exception("storyboard_memory_view_failed project=%s role=%s", project_id, role)
        return None


def merge_continuity_reports(
    directorial_plan: dict,
    timing_report: ContinuityReport,
    physical_report: ContinuityReport,
) -> ContinuityReport:
    """Deterministically merge the two parallel continuity passes in shot order."""
    shots = list((directorial_plan or {}).get("shots") or [])
    shot_order = {str(s["id"]): idx for idx, s in enumerate(shots) if s.get("id")}
    beat_to_shot: dict[str, int] = {}
    for idx, shot in enumerate(shots):
        if shot.get("beat_id"):
            beat_to_shot.setdefault(str(shot["beat_id"]), idx)
        for cut in shot.get("cuts") or []:
            if cut.get("beat_id"):
                beat_to_shot.setdefault(str(cut["beat_id"]), idx)

    shot_patterns = [(sid, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(sid)}(?![A-Za-z0-9_])"), idx)
                     for sid, idx in shot_order.items()]
    beat_patterns = [(bid, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(bid)}(?![A-Za-z0-9_])"), idx)
                     for bid, idx in beat_to_shot.items()]
    fallback_idx = len(shots)

    def _sort_key(item: tuple[str, int, int]) -> tuple[int, int, int]:
        issue, lane_rank, pos = item
        matched_shots = [idx for _sid, pat, idx in shot_patterns if pat.search(issue)]
        if matched_shots:
            return (min(matched_shots), lane_rank, pos)
        matched_beats = [idx for _bid, pat, idx in beat_patterns if pat.search(issue)]
        if matched_beats:
            return (min(matched_beats), lane_rank, pos)
        return (fallback_idx, lane_rank, pos)

    tagged: list[tuple[str, int, int]] = []
    for lane_rank, report in ((0, timing_report), (1, physical_report)):
        for pos, raw in enumerate(report.issues):
            text_val = raw.strip()
            if text_val:
                tagged.append((text_val, lane_rank, pos))
    tagged.sort(key=_sort_key)

    merged_issues: list[str] = []
    seen_norm: set[str] = set()
    for text_val, _lane, _pos in tagged:
        norm = re.sub(r"\s+", " ", text_val).strip()
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        merged_issues.append(text_val)

    needs_review = (
        bool(merged_issues)
        or timing_report.recommendation == "needs_review"
        or physical_report.recommendation == "needs_review"
    )
    return ContinuityReport(
        issues=merged_issues,
        recommendation="needs_review" if needs_review else "proceed_to_preflight",
    )


def _load_script_from_director(
    db: Session,
    project_id: str,
    inputs: list[AgentArtifact],
) -> AgentArtifact | None:
    """Resolve ScriptBreakdown from inputs, or via DirectorialPlan's upstream revision."""
    script = next((a for a in inputs if a.kind == "ScriptBreakdown"), None)
    if script is not None:
        return script
    director = next((a for a in inputs if a.kind == "DirectorialPlan"), None)
    if director is None:
        return None
    for rev_id in director.input_revision_ids or []:
        cand = db.get(AgentArtifact, rev_id)
        if cand is not None and cand.project_id == project_id and not cand.stale and cand.kind == "ScriptBreakdown":
            if digest(cand.payload_json) == cand.sha256:
                return cand
    return None


def _continuity_lane_context(
    db: Session,
    project_id: str,
    base_context: dict,
    inputs: list[AgentArtifact],
    lane_tool: str,
) -> tuple[dict, list[str]]:
    """Build a focused, slim context for one orthogonal continuity sub-agent."""
    script = _load_script_from_director(db, project_id, inputs)
    director = next(a for a in inputs if a.kind == "DirectorialPlan")
    camera = next((a for a in inputs if a.kind == "CameraPlan"), None)

    if lane_tool == "continuity_timing":
        lane_artifacts = [a for a in (script, director) if a is not None]
        lane_ctx = {
            "brief": base_context["brief"],
            "locked_story_facts": base_context["locked_story_facts"],
            "style_version_id": base_context["style_version_id"],
            "style": base_context["style"],
            "style_hash": base_context["style_hash"],
            "assets": base_context["assets"],
            "inputs": [{"id": a.id, "kind": a.kind, "sha256": a.sha256, "payload": a.payload_json}
                       for a in lane_artifacts],
            "rules": base_context["rules"],
            "rulepack_id": base_context["rulepack_id"],
            "rulepack_hash": base_context["rulepack_hash"],
            "eligible_edits": [],
            "continuity_policy": base_context.get("continuity_policy"),
            **({"max_shot_seconds": base_context["max_shot_seconds"]}
               if "max_shot_seconds" in base_context else {}),
            **({"editorial_timing": base_context["editorial_timing"],
                "editorial_timing_plan": base_context["editorial_timing_plan"]}
               if "editorial_timing_plan" in base_context else {}),
            **({"production_context": base_context["production_context"]}
               if "production_context" in base_context else {}),
        }
        return lane_ctx, [a.id for a in lane_artifacts]

    if lane_tool == "continuity_physical":
        lane_artifacts = [a for a in (director, camera) if a is not None]
        lane_ctx = {
            "brief": base_context["brief"],
            "locked_story_facts": base_context["locked_story_facts"],
            "style_version_id": base_context["style_version_id"],
            "style": base_context["style"],
            "style_hash": base_context["style_hash"],
            "director_style": base_context.get("director_style"),
            "assets": base_context["assets"],
            "inputs": [{"id": a.id, "kind": a.kind, "sha256": a.sha256, "payload": a.payload_json}
                       for a in lane_artifacts],
            "rules": base_context["rules"],
            "rulepack_id": base_context["rulepack_id"],
            "rulepack_hash": base_context["rulepack_hash"],
            "eligible_edits": [],
            **({"max_shot_seconds": base_context["max_shot_seconds"]}
               if "max_shot_seconds" in base_context else {}),
            **({"production_context": base_context["production_context"]}
               if "production_context" in base_context else {}),
        }
        return lane_ctx, [a.id for a in lane_artifacts]

    raise ValueError("ROLE_OR_TOOL_DENIED")


def _record_task_failure(db: Session, task_id: str, role: str, exc: Exception) -> ValueError:
    """Persist the authoritative failure classification for a crew task and raise its named code."""
    from shine.agents.execution import UNKNOWN_CODES, AgentRequestBlocked, rejection_reason

    db.rollback()
    task = db.get(AgentTaskRecord, task_id)
    if isinstance(exc, ValueError | ValidationError | LLMError):
        external_outcome = (
            exc.details.get("external_outcome")
            if isinstance(exc, LLMError) else None
        )
        phase = task.trace.get("phase")
        if (isinstance(exc, LLMError) and exc.code in UNKNOWN_CODES
                and external_outcome != "NOT_SENT"):
            external_outcome = "UNKNOWN"
        elif external_outcome not in {"NOT_SENT", "UNKNOWN", "RESPONSE_RECEIVED"}:
            if (task.trace.get("received_result_sha256")
                    or phase == "VALIDATING_RESULT"
                    or isinstance(exc, LLMError) and isinstance(exc.details.get("status"), int)
                    or isinstance(exc, LLMError) and isinstance(exc.details.get("http_status"), int)):
                external_outcome = "RESPONSE_RECEIVED"
            elif phase in {"PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER"}:
                external_outcome = "NOT_SENT"
            else:
                external_outcome = "UNKNOWN"
        uncertain = external_outcome == "UNKNOWN" and not task.trace.get("is_mock")
        task.status = "NEEDS_REVIEW" if uncertain else "REJECTED"
        task.state_version += 1
        task.error_code = exc.code if isinstance(exc, LLMError) else "CONTRACT_OR_POLICY_REJECTED"
        diagnostic = ("schema_validation" if isinstance(exc, ValidationError) else
                      "provider_format" if isinstance(exc, LLMError) and exc.code == "LLM_FORMAT" else
                      "provider_error" if isinstance(exc, LLMError) else "contract_or_policy")
        schema_errors = None
        if isinstance(exc, ValidationError):
            schema_errors = [
                {"path": ".".join(str(part) for part in err.get("loc", ())),
                 "type": err.get("type")}
                for err in exc.errors()[:20]
            ]
        _transport_evidence = {}
        if isinstance(exc, LLMError):
            _transport_evidence = {key: exc.details[key] for key in (
                "error_type", "streamed", "first_event_ms", "last_event_ms",
                "partial", "headers_received") if key in exc.details}
        task.trace = {**task.trace, "automatic_replay": False,
                      "diagnostic_category": diagnostic,
                      **({"transport": _transport_evidence} if _transport_evidence else {}),
                      **({"schema_errors": schema_errors} if schema_errors else {}),
                      **({"http_status": exc.details["status"]}
                         if isinstance(exc, LLMError) and isinstance(exc.details.get("status"), int) else {}),
                      **({"provider_code": exc.details["provider_code"]}
                         if isinstance(exc, LLMError)
                         and isinstance(exc.details.get("provider_code"), int) else {}),
                      **({"provider_schema_path": exc.details["path"],
                          "provider_schema_validator": exc.details["validator"]}
                         if isinstance(exc, LLMError) and exc.code == "LLM_FORMAT"
                         and isinstance(exc.details.get("path"), str) else {}),
                      **({"visual_field": exc.details["visual_field"]}
                         if isinstance(getattr(exc, "details", None), dict)
                         and isinstance(exc.details.get("visual_field"), str) else {}),
                      **({"offending_line": exc.details["offending_line"][:200],
                          "offending_speaker": exc.details.get("speaker")}
                         if isinstance(getattr(exc, "details", None), dict)
                         and isinstance(exc.details.get("offending_line"), str) else {}),
                      # Which beat, line or section the server refused, as the
                      # verdict computed it: the retry is told exactly that.
                      **({"refusal_details": exc.details["refusal_details"]}
                         if isinstance(getattr(exc, "details", None), dict)
                         and isinstance(exc.details.get("refusal_details"), list) else {}),
                      **({"provider_finish_reason": exc.details["finish_reason"]}
                         if isinstance(exc, LLMError)
                         and isinstance(exc.details.get("finish_reason"), str) else {}),
                      **({"provider_text_chars": len(exc.details["provider_text"])}
                         if isinstance(exc, LLMError)
                         and isinstance(exc.details.get("provider_text"), str) else {}),
                      "external_outcome": external_outcome,
                      **({"blocking_task": exc.blocking}
                         if isinstance(exc, AgentRequestBlocked) else {}),
                      "rejection_reason_code": exc.code if isinstance(exc, LLMError) else rejection_reason(exc)}
        db.commit()
        out = ValueError(task.error_code)
        out.task_id = task.id
        return out

    phase = task.trace.get("phase")
    pre_request = phase in {"PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER"}
    unknown = (not pre_request and not task.trace.get("is_mock")
               and not task.trace.get("received_result_sha256"))
    if pre_request:
        detail = getattr(exc, "detail", None)
        safe_detail = (
            detail if isinstance(detail, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", detail) else None
        )
        logger.warning("film_crew_pre_request_failed role=%s safe_detail=%s exc_type=%s",
                       role, safe_detail, type(exc).__name__, exc_info=True)
        task.status = "REJECTED"
        task.state_version += 1
        task.error_code = "AGENT_PRE_REQUEST_FAILED"
        task.trace = {**task.trace, "automatic_replay": False,
                      "diagnostic_category": "pre_request_resource",
                      "external_outcome": "NOT_SENT",
                      "rejection_reason_code": task.error_code,
                      **({"pre_request_error_code": safe_detail} if safe_detail else {})}
    else:
        logger.exception("film_crew_fault role=%s task=%s phase=%s", role, task.id, phase)
        task.status = "NEEDS_REVIEW" if unknown else "FAILED"
        task.state_version += 1
        task.error_code = "AGENT_CALL_FAILED"
        task.trace = {**task.trace, "automatic_replay": False,
                      "error_type": type(exc).__name__,
                      **({"external_outcome": "UNKNOWN"} if unknown else {})}
    db.commit()
    out = ValueError(task.error_code)
    out.task_id = task.id
    return out


class FilmAgentDispatcher:
    def __init__(self, gateway: LLMGateway, data_dir: Path | None = None,
                 asset_root: Path | None = None):
        self.gateway = gateway
        self.data_dir = data_dir
        self.asset_root = asset_root or data_dir

    def run(
        self,
        db: Session,
        project_id: str,
        role: str,
        body: FilmRunRequest,
        *,
        storyboard_request_sha256: str | None = None,
        draft: bool = False,
        repair_visual_language: bool = False,
        repair_contract_note: str | None = None,
        replace_interrupted_task_id: str | None = None,
        authorize_replay_task_id: str | None = None,
        repair_director: bool = False,
        escalate_output_budget: bool = False,
    ) -> AgentArtifact:
        if replace_interrupted_task_id is not None:
            raise ValueError("AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
        contract, required = FilmAgentRegistry.get(role, body.tool)
        project = require_project(db, project_id)
        if body.tool != "style_proposal" and not project.frozen_style_version_id and not draft:
            raise ValueError("STYLE_NOT_FROZEN")
        registry = H3RulebookRegistry(self.data_dir / "h3-source-cache" if self.data_dir else None)
        cache_state = registry.cache_status() if self.data_dir else []
        if any(s["local_cache_status"] == "TAMPERED" for s in cache_state):
            raise ValueError("OFFICIAL_CACHE_TAMPERED")
        pack = registry.for_project(db, project_id)
        if not pack:
            raise ValueError("FREEZE_PROJECT_RULEPACK_FIRST")
        inputs = load_inputs(db, project_id, body.input_revision_ids)
        if any(sum(a.kind == kind for a in inputs) != 1 for kind in required):
            raise ValueError("MISSING_OR_AMBIGUOUS_HANDOFF")
        if body.supersedes_artifact_id:
            old = db.get(AgentArtifact, body.supersedes_artifact_id)
            if (
                not old
                or old.project_id != project_id
                or old.producer_role != role
                or old.kind != contract.__name__
                or old.id in body.input_revision_ids
            ):
                raise ValueError("INVALID_SUPERSESSION")
        profile = self.gateway.profile_for_role(db, role)
        if profile.kind != "mock" and not is_loopback_url(profile.base_url) and not body.allow_remote_text:
            raise ValueError("REMOTE_AGENT_CONSENT_REQUIRED")
        style = db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None
        director_binding = style.director_style_binding if style else None
        if draft and style is None and project.style_draft:
            # The draft lane substitutes the person's saved style draft as the
            # camera-language template; the frozen bible stays untouched.
            from types import SimpleNamespace

            from shine.schemas import StyleBible

            style = SimpleNamespace(
                snapshot=StyleBible.model_validate(project.style_draft).model_dump(mode="json"),
                sha256=None)
            director_binding = project.director_style_draft
        director_style_context = None
        if director_binding:
            try:
                entry, resolution = validate_binding(
                    director_binding,
                    expected_style=(style.snapshot if style else project.style_draft),
                )
            except Exception as exc:
                raise ValueError(getattr(exc, "detail", "STYLE_PROFILE_INVALID")) from exc
            director_style_context = {
                "profile_id": entry.profile.id,
                "profile_version": entry.profile.version,
                "profile_sha256": entry.sha256,
                "strength": director_binding["strength"],
                "style_bible": director_binding["style_bible_snapshot"],
                "resolution": resolution.model_dump(mode="json"),
            }
        if role == "qc_reviewer":
            if (body.shot_spec is None or not style or body.input_revision_ids
                    or body.shot_spec.style_version_id != style.id):
                raise ValueError("STYLE_INPUT_CURRENT_FROZEN_SNAPSHOT_REQUIRED")
        elif body.shot_spec is not None:
            raise ValueError("STYLE_INPUT_SPEC_ROLE_DENIED")
        context = {
            "brief": body.brief,
            "locked_story_facts": body.locked_story_facts,
            "style_version_id": project.frozen_style_version_id,
            "style": style.snapshot if style else None,
            "style_hash": style.sha256 if style else None,
            "director_style": director_style_context,
            "assets": asset_context(db, project_id),
            "inputs": [{"id": a.id, "kind": a.kind, "sha256": a.sha256, "payload": a.payload_json} for a in inputs],
            "rules": registry.search(() if body.tool == "continuity_arbitration"
                                     else FilmAgentRegistry.roles[role][3]),
            "rulepack_id": pack.id,
            "rulepack_hash": pack.sha256,
            "eligible_edits": eligible_edits(
                db, project_id, offline=body.offline_preview, artifact_root=self.asset_root
            )
            if role == "editor"
            else [],
        }
        if contract is DramaSceneScript:
            # The narration policy is the storyboard's own frozen decision; the
            # drama writer reads the same one the beat breakdown reads.
            narration = ((style.snapshot or {}).get("narration_level") if style else None)
            context["narration_level"] = narration if narration in {"none", "low", "high"} else "none"
            context["project_spoken_language"] = (
                canonical_spoken_language(project.language) or DEFAULT_SPOKEN_LANGUAGE
            )
        if contract is ScriptBreakdown and project.frozen_style_version_id and style:
            ratio = style.snapshot.get("dialogue_ratio_percent")
            if ratio is not None:
                context["dialogue_ratio_percent"] = ratio
                context["project_spoken_language"] = (
                    canonical_spoken_language(project.language) or DEFAULT_SPOKEN_LANGUAGE
                )
            narration = style.snapshot.get("narration_level")
            # Plan P0-E: an unset narration level means narration is NOT authorized
            # for newly created storyboard tasks.  The effective policy is derived
            # here in the existing style decision layer (no second config item);
            # the frozen snapshot and its hash are never rewritten, so old frozen
            # styles keep their bytes.  Explicit low/high still follow the script
            # and style; "none" never converts exposition into a narrator line.
            context["narration_level"] = narration if narration in {"none", "low", "high"} else "none"
        if project.frozen_style_version_id and style:
            # The per-shot ceiling is a production decision frozen with the
            # storyboard book, read here for every role so the editorial
            # segmentation, the director prompt and the landed-spec verdict
            # all enforce the same cap (one fact, one verdict).
            cap = style.snapshot.get("max_shot_seconds")
            if cap is not None:
                context["max_shot_seconds"] = float(cap)
        if body.tool == "scene_board":
            if context.get("max_shot_seconds") is None:
                context["max_shot_seconds"] = DEFAULT_MAX_SHOT_SECONDS
            narration = ((style.snapshot or {}).get("narration_level") if style else None)
            context["narration_level"] = narration if narration in {"none", "low", "high"} else "none"
        if body.production_context is not None:
            # The architect addresses the whole confirmed plan; every other
            # caller is one unit of the source.
            context["production_context"] = verified_context(db, project_id, body)
        if role in {"scriptwriter", "director"}:
            memory_view = _storyboard_memory_view(db, project_id, role=role)
            if memory_view is not None:
                context["memory"] = memory_view
        refusal: dict | None = None
        if role in {"scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor"} \
                and body.tool == "propose":
            cap = context.get("max_shot_seconds")
            timing = editorial_timing_preview(
                body.brief, max_segment_s=cap if cap is not None else MAX_SEGMENT_S)
            if timing:
                blocker = editorial_timing_blocker(
                    timing, max_segment_s=cap if cap is not None else MAX_SEGMENT_S)
                beat_ids = _plan_beat_ids(role, inputs)
                if blocker:
                    refusal = blocker
                elif role == "director" and len(beat_ids or []) != timing["editorial_shot_count"]:
                    # The beat count is a fact of the upstream scriptwriter
                    # artifact, not something the director can repair.  Naming
                    # it stops a doomed model call and tells the operator which
                    # of the two numbers is wrong.
                    refusal = {
                        "code": "EDITORIAL_BEAT_COUNT_MISMATCH",
                        "editorial_shot_count": timing["editorial_shot_count"],
                        "scriptwriter_beat_count": len(beat_ids or []),
                    }
                else:
                    # The crew is served the finished segmentation, not a
                    # suggestion: the server checks the returned plan against
                    # this same arithmetic, so a role that recomputes it is
                    # refused for a reason it can never repair.
                    context["editorial_timing"] = timing
                    context["editorial_timing_plan"] = editorial_timing_plan(timing, beat_ids)
        if role == "director":
            # The plan's reference sets must fit a workflow the project can
            # actually render; unverified registrations prove nothing.
            context["workflow_reference_budget"] = workflow_reference_budget(db, project_id)
        if role == "qc_reviewer":
            context["shot_spec"] = body.shot_spec.model_dump(mode="json")
        if role == "continuity_supervisor":
            # The reviewer of a plan needs the reference-set policy as a
            # checked fact.  Without it a deliberate exclusion reads like an
            # omission, and a shot that follows the policy is reported as an
            # unresolved reference mapping.
            plan = next((a.payload_json for a in inputs if a.kind == "DirectorialPlan"), None)
            context["continuity_policy"] = shot_reference_policy(db, project_id, plan or {})
        if body.tool == "continuity_arbitration":
            # The arbiter rules on the exact issue strings it must answer.
            report = next((a for a in inputs if a.kind == "ContinuityReport"), None)
            context["arbitration_subject"] = {
                "issues": [
                    {"index": index, "text": issue}
                    for index, issue in enumerate(
                        (report.payload_json.get("issues") or []) if report else []
                    )
                ],
                "rule_ids": [rule["id"] for rule in registry_rules()],
            }
            context["registered_production_workflows"] = [
                {"id": workflow.id, "mode": workflow.mode,
                 "validated": bool(workflow.validated_against)}
                for workflow in db.scalars(select(WorkflowProfile).where(WorkflowProfile.project_id == project_id))
            ]
        if role in {"comfyops", "director", "cinematographer", "sound_director", "producer"}:
            from shine.comfyops.models import EnvironmentSnapshot, ModelPackage, WorkflowRevision

            context["capabilities"] = [
                {
                    "id": w.id,
                    "category": w.payload["category"],
                    "status": w.status,
                    "sha256": w.sha256,
                    "parameter_names": sorted(w.payload["parameter_map"]),
                    "reference_slots": w.payload["reference_slots"],
                    "model_ids": w.payload["model_ids"],
                }
                for w in db.scalars(select(WorkflowRevision))
                if w.status == "PRODUCTION_APPROVED" or (body.offline_preview and w.status == "FAKE_EXECUTED")
            ]
            if role == "comfyops":
                snapshot = db.scalar(select(EnvironmentSnapshot).order_by(EnvironmentSnapshot.created_at.desc()))
                context["environment"] = (
                    {
                        "id": snapshot.id,
                        "sha256": snapshot.sha256,
                        "status": snapshot.status,
                        "source": snapshot.payload.get("source"),
                        "is_mock": snapshot.payload.get("is_mock"),
                        "gpu": snapshot.payload.get("gpu"),
                        "coverage": snapshot.payload.get("coverage"),
                    }
                    if snapshot
                    else {"status": "UNAVAILABLE"}
                )
                context["model_catalog"] = [
                    {
                        "id": m.id,
                        "name": m.payload["name"],
                        "revision": m.payload["revision"],
                        "license_id": m.payload["license_id"],
                        "status": m.status,
                        "sha256": m.sha256,
                    }
                    for m in db.scalars(select(ModelPackage))
                ]
        # The independent input critic receives text only, without the media
        # reviewer, asset manifest or prior role outputs biasing its comparison.
        model_context = (
            {key: context[key] for key in ("style", "style_version_id", "style_hash", "shot_spec")}
            if role == "qc_reviewer" else context
        )
        from shine.agents.execution import guard_request

        wire_contract = StyleInputWire if role == "qc_reviewer" else contract
        contract_hash = digest(wire_contract.model_json_schema())
        model_context_metadata = model_context_for_profile(profile)
        from shine.llm.gateway import agent_output_config

        raw_config = self.gateway.config_for(profile)
        bounded_config = agent_output_config(raw_config)
        agent_output_limit = (bounded_config.generation_options.get("max_completion_tokens")
                              or bounded_config.generation_options.get("max_tokens"))
        if bounded_config.kind == "anthropic":
            agent_output_limit = min(4096, bounded_config.max_output_tokens or 4096)

        if role == "continuity_supervisor" and body.tool == "propose":
            return self._run_parallel_continuity(
                db,
                project_id,
                project=project,
                body=body,
                inputs=inputs,
                base_context=context,
                pack=pack,
                profile=profile,
                raw_config=raw_config,
                bounded_config=bounded_config,
                model_context_metadata=model_context_metadata,
                agent_output_limit=agent_output_limit,
                cache_state=cache_state,
                storyboard_request_sha256=storyboard_request_sha256,
                repair_contract_note=repair_contract_note,
                authorize_replay_task_id=authorize_replay_task_id,
                escalate_output_budget=escalate_output_budget,
                refusal=refusal,
            )

        context_payload_utf8_bytes = len(
            json.dumps(model_context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        skill_selection = select_skills_for_role(
            role,
            tool=body.tool,
            brief=body.brief,
            style=context.get("style"),
            inputs=context.get("inputs"),
            assets=context.get("assets"),
            editorial_timing_plan=context.get("editorial_timing_plan"),
            production_context=context.get("production_context"),
        )
        # Claim the request while holding SQLite's writer lock, then release it
        # with the durable intent before any network call. Concurrent identical
        # requests cannot both pass the guard. A busy local writer (heartbeats,
        # concurrent users) can win the lock race; the claim retries briefly
        # instead of surfacing an SQLite error to the caller.
        db.commit()
        for attempt in range(5):
            try:
                db.execute(text("BEGIN IMMEDIATE"))
                break
            except Exception as exc:  # noqa: BLE001 - sqlite lock race
                if "database is locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.4 * (attempt + 1))
        guard_request(db, project_id, role, digest(context), profile,
                      text_hash=digest(model_context) if role == "qc_reviewer" else None,
                      contract_hash=contract_hash,
                      authorize_replay_task_id=authorize_replay_task_id)
        task = AgentTaskRecord(
            project_id=project_id,
            role_id=role,
            input_hash=digest(context),
            style_version_id=project.frozen_style_version_id,
            asset_manifest_hash=digest(context["assets"]),
            rulepack_id=pack.id,
            rulepack_hash=pack.sha256,
            model_profile_id=profile.id,
            model_id=profile.model,
            schema_version=contract.model_fields["schema_version"].default,
            status="RUNNING",
            trace={
                "tool": body.tool,
                "phase": "PREPARING",
                "offline_preview": body.offline_preview,
                "remote_text_consent": body.allow_remote_text,
                "is_mock": profile.kind == "mock",
                "input_revision_ids": body.input_revision_ids,
                **({"production_context": context["production_context"]}
                   if body.production_context is not None else {}),
                "schema": contract.__name__,
                "review_contract_sha256": contract_hash,
                "model_context": {
                    **model_context_metadata,
                    "context_payload_utf8_bytes": context_payload_utf8_bytes,
                    "agent_output_limit_tokens": agent_output_limit,
                    "input_policy": "full_context_without_fixed_truncation",
                },
                **({"storyboard_request_sha256": storyboard_request_sha256}
                   if storyboard_request_sha256 else {}),
                **({"wire_protocol": "independent-text-v2"} if role == "qc_reviewer" else {}),
                "review_provider": text_provider_snapshot(profile),
                "source_cache": [{"sha256": s["sha256"], "status": s["local_cache_status"]} for s in cache_state],
                **({"editorial_refusal": refusal} if refusal else {}),
                "cinema_skills": skill_selection.as_trace(),
                **({"scope": "TEXT_INPUT_CONSISTENCY_ONLY_NOT_MEDIA_QC",
                    "shot_spec_snapshot": context["shot_spec"],
                    "shot_spec_sha256": digest(context["shot_spec"]),
                    "style_hash": context["style_hash"], "text_input_sha256": digest(model_context)}
                   if role == "qc_reviewer" else {}),
            },
        )
        db.add(task)
        if authorize_replay_task_id:
            # One authorized new request replaces a finished refusal without
            # erasing it: the rejected record keeps its code and trace as
            # history, and only stops guarding this context.
            previous = db.get(AgentTaskRecord, authorize_replay_task_id)
            if previous is not None and previous.status in {"REJECTED", "FAILED"}:
                previous.status = "SUPERSEDED"
                previous.state_version += 1
        db.commit()  # Durable intent before the model call. Interrupted calls are never auto-replayed.
        def _build_system_prompt(skills_block: str) -> str:
            if body.tool == "continuity_arbitration":
                prompt_text = arbiter_prompt()
            else:
                prompt_text = crew_prompt(
                    role,
                    tool=body.tool,
                    plan=context.get("editorial_timing_plan"),
                    reference_budget_note=reference_budget_note(context.get("workflow_reference_budget") or []),
                    repair_director=repair_director,
                    dialogue_ratio_percent=context.get("dialogue_ratio_percent"),
                    narration_level=context.get("narration_level"),
                    max_shot_seconds=context.get("max_shot_seconds"),
                    creative_source=(context.get("production_context") or {}).get("creative_source"),
                    cinema_skills_block=skills_block,
                    continuity_predecessor=bool(
                        (context.get("production_context") or {}).get("continuity_predecessor")
                    ),
                    translation_supplied=False,
                    scene_budget_seconds=(context.get("production_context") or {}).get("scene_budget_seconds"),
                    episode_progress=(context.get("production_context") or {}).get("episode_progress"),
                )
            if repair_visual_language:
                prompt_text += (
                    " The previous proposal failed the visual language check. Rewrite all visual and camera "
                    "descriptions in English, including every segment's cuts. Never copy a Chinese name, title, "
                    "stage direction or lyric into a visual or camera field: refer to a person by an English "
                    "role label such as 'the lead performer', 'the woman in the grey cloak' or 'the guard', and "
                    "describe what they do instead of naming them. A name belongs in the asset ids and the "
                    "character fields, never in the prose the renderer reads. Keep the original timeline, "
                    "approved references and dialogue contract exactly."
                )
            if repair_contract_note:
                # The refusal is named by the server, never by the provider's text:
                # one targeted instruction, no answer text, no secret.
                prompt_text += repair_contract_note
            return prompt_text

        system_prompt = _build_system_prompt(skill_selection.prompt_block)

        from shine.llm.transport_policy import policy_for
        _transport_policy = policy_for(bounded_config)
        # The number the workbench shows as "模型超过 N 秒无回应" must be the
        # deadline that actually governs this call, not the profile's nominal
        # timeout: a streaming call is cut at the transport read ceiling, so
        # reporting 600s for a 300s ceiling misnames the failure.
        _effective_timeout_s = (max(_transport_policy.first_event_s, _transport_policy.idle_s)
                                if _transport_policy.streaming else profile.timeout_s)

        def record_progress(phase: str) -> None:
            # Commit diagnostic state before waiting externally; never hold
            # SQLite's writer across resource admission or a provider call.
            task.trace = {**task.trace, "phase": phase, "phase_started_at": utcnow().isoformat(),
                          "network_timeout_s": _effective_timeout_s}
            db.commit()

        try:
            if refusal:
                # No answer can satisfy this request: the cut table cannot be
                # grouped into H3 segments, or the upstream beat count cannot
                # match it.  The run is refused with the real reason instead
                # of spending a model call on a plan the server would reject.
                raise ValueError(refusal["code"])
            if body.consent_document_id or body.production_context is not None:
                from shine.materialprep import text_consent
                source = context.get("production_context") or {}
                text_consent.guard(project_id, source.get("document_id") or body.consent_document_id,
                                   source.get("document_sha256") or body.consent_document_sha256,
                                   role, text_consent.fingerprint(profile), self.gateway)(db)
            # The five crew roles share the literary chain's one call-budget
            # calculation (streamline plan §6.3): the rendered request is
            # measured exactly once, the completion budget follows the 60%
            # hard-cap rule with the role's own ceiling, and a request that
            # cannot fit the window is refused by name before it is sent.
            from shine.literary.budget import plan_call_budget
            from shine.literary.requests import compile_request

            crew_request = compile_request(
                system=system_prompt,
                user=json.dumps(model_context, ensure_ascii=False, separators=(",", ":")),
                schema=wire_contract.model_json_schema(),
            )
            # The role's task nature selects the budget purpose; the one verdict
            # still comes from plan_call_budget. The three plan roles write a
            # beats/shots/camera plan (structured); the continuity supervisor only
            # reviews the sequence and returns a small issues list, so sizing it
            # like a plan (6k goal ceiling) let it deliberate until the transport
            # wall (measured 323s) while an observation-sized budget answers in
            # seconds. The architect ranks every scene of a plan at once, which
            # is a long answer. This is a role classification, not a per-call
            # reserve hack.
            crew_purpose = ("architecture" if body.tool == "series_architecture"
                            else "drama_scene" if body.tool == "drama_scene"
                            else "scene_board" if body.tool == "scene_board"
                            else "observation" if role == "continuity_supervisor" else "structured")
            crew_budget = plan_call_budget(
                raw_config, crew_request, purpose=crew_purpose,
                output_ceiling=agent_output_limit,
                output_floor_tokens=agent_output_limit if escalate_output_budget else None,
            )
            if not crew_budget.fits and skill_selection.prompt_block:
                # Cinema Skills are optional guidance and must never push a request
                # over the provider's context window; drop them and re-measure once.
                system_prompt = _build_system_prompt("")
                crew_request = compile_request(
                    system=system_prompt,
                    user=json.dumps(model_context, ensure_ascii=False, separators=(",", ":")),
                    schema=wire_contract.model_json_schema(),
                )
                crew_budget = plan_call_budget(
                    raw_config, crew_request, purpose=crew_purpose,
                    output_ceiling=agent_output_limit,
                    output_floor_tokens=agent_output_limit if escalate_output_budget else None,
                )
                task.trace = {
                    **task.trace,
                    "cinema_skills": {
                        **task.trace["cinema_skills"],
                        "budget_trimmed_to_zero": True,
                        "selected": [],
                        "estimated_tokens": 0,
                    },
                }
            task.trace = {**task.trace, "call_budget": crew_budget.as_dict(),
                          **({"output_budget_escalated": True} if escalate_output_budget else {})}
            db.commit()  # the approved budget is durable evidence before the call
            if not crew_budget.fits:
                raise ValueError(str((crew_budget.refusal or {}).get("code")
                                     or "LLM_CREATIVE_CONTEXT_OVERFLOW"))
            response = self.gateway.complete_for_role(
                db,
                role,
                precompiled=crew_request,
                budget={"completion_cap": crew_budget.output_request_tokens,
                        "streaming": crew_budget.streaming},
                on_progress=record_progress,
            )
            task.trace = {**task.trace, "received_result_sha256": digest(response.parsed),
                          "response_latency_ms": response.latency_ms,
                          **{key: response.raw_meta[key] for key in ("http_status", "provider_code")
                             if isinstance(response.raw_meta.get(key), int)},
                          "response_model_sha256": hashlib.sha256((response.model or "").encode()).hexdigest(),
                          "is_mock": bool(task.trace.get("is_mock") or response.raw_meta.get("is_mock")
                                          or response.raw_meta.get("mock"))}
            db.commit()  # A returned response is durable evidence even if the contract rejects it.
            if body.production_context is not None:
                db.expire_all()
                verified_context(db, project_id, body)
            response_data = response.parsed
            if (contract is ScriptBreakdown and isinstance(response_data, dict)
                    and "locked_story_facts" not in response_data):
                # Immutable user facts are owned by the application, so the
                # model need not echo them just to reproduce the snapshot.
                response_data = {**response_data, "locked_story_facts": body.locked_story_facts}
            if role == "qc_reviewer":
                proposal = bind_input_judgments(response_data, context)
                salvage_notes: list[str] = []
            else:
                # A complete proposal inside a slightly wrong envelope is kept
                # (streamline plan §6.2): the local normalisation only drops
                # unknown keys and fills documented aliases, never content.
                # An answer that stays unusable re-raises the original
                # ValidationError, so the refusal code stays honest.
                from shine.agents.crew_salvage import parse_with_salvage

                proposal, salvage_notes = parse_with_salvage(response_data, contract)
            if salvage_notes:
                task.trace = {**task.trace, "salvage_notes": salvage_notes}
                db.commit()
            proposal = canonicalize_script_character_asset_ids(proposal, context["assets"])
            validate_output(role, proposal, body, inputs, context)
            if role == "editor" and body.tool == "edit_intent" and isinstance(proposal, EditIntentPlan):
                director_style = context.get("director_style")
                if director_style:
                    from shine.director_styles.contracts import StyleResolution

                    proposal = bind_editing_style(
                        proposal,
                        director_style["profile_id"],
                        StyleResolution.model_validate(director_style["resolution"]),
                    )
                else:
                    proposal = proposal.model_copy(update={
                        "director_style_id": None,
                        "director_style_resolution_sha256": None,
                        "editing_direction": None,
                    })
            if repair_director:
                old = next(a for a in inputs if a.kind == "DirectorialPlan")
                original = old.payload_json["shots"]
                revised = proposal.model_dump(mode="json")["shots"]
                def fixed_shape(shots):
                    return [(s["id"], s["beat_id"], s["duration_s"], s["asset_ids"],
                             [(c["beat_id"], c["start_s"]) for c in s.get("cuts", [])]) for s in shots]
                if fixed_shape(original) != fixed_shape(revised):
                    raise ValueError("REPAIR_CHANGED_LOCKED_SHOT_STRUCTURE")
                if digest(old.payload_json) == digest(proposal.model_dump(mode="json")):
                    raise ValueError("REPAIR_DID_NOT_CHANGE_PLAN")
            # Re-read context after a potentially long external call.
            db.expire_all()
            project = require_project(db, project_id)
            if (
                project.frozen_style_version_id != task.style_version_id
                or digest(asset_context(db, project_id)) != task.asset_manifest_hash
            ):
                raise ValueError("INPUT_CONTEXT_CHANGED_DURING_CALL")
            load_inputs(db, project_id, body.input_revision_ids)
            if role == "editor" and body.tool == "propose":
                context["eligible_edits"] = eligible_edits(
                    db, project_id, offline=body.offline_preview, artifact_root=self.asset_root
                )
                validate_output(role, proposal, body, inputs, context)
            payload = proposal.model_dump(mode="json")
            if isinstance(proposal, DramaSceneScript):
                # The model wrote the scene; the server writes where it came
                # from, how long it is, what it chose not to trust and the one
                # rendering of it the storyboard crew will read.
                from shine.literary.drama import drama_server_fields

                payload.update(drama_server_fields(payload, context))
            if isinstance(proposal, SeriesArchitecture):
                payload.update({
                    "outline_sha256": context["production_context"]["outline_sha256"],
                    "plan_sha256": context["production_context"]["plan_sha256"],
                })
            if isinstance(proposal, scene_director.SceneShotBoard):
                scene_artifact = next((a for a in inputs if a.kind == "DramaSceneScript"), inputs[0])
                production = context.get("production_context") or {}
                verdict = continuity_engine.review_scene_board(
                    payload,
                    scene_artifact.payload_json,
                    {
                        "max_shot_seconds": context.get("max_shot_seconds"),
                        "continuity_predecessor": bool(production.get("continuity_predecessor")),
                        "previous_blocking": production.get("previous_shot_blocking"),
                        "registered_locations": production.get("registered_locations") or [],
                        "character_tokens": production.get("character_tokens") or [],
                    },
                )
                payload.update(scene_director.board_server_fields(payload, scene_artifact, context, verdict))
            artifact = AgentArtifact(
                project_id=project_id,
                kind=contract.__name__,
                schema_version=proposal.schema_version,
                payload_json=payload,
                sha256=digest(payload),
                producer_role=role,
                source_task_id=task.id,
                input_revision_ids=body.input_revision_ids,
            )
            db.add(artifact)
            task.status = "SUCCEEDED"
            task.trace = {
                **task.trace,
                "phase": "SUCCEEDED",
                "is_mock": bool(task.trace.get("is_mock") or response.raw_meta.get("is_mock")
                                or response.raw_meta.get("mock")),
                "latency_ms": response.latency_ms,
                "usage": response.usage,
                "output_hash": artifact.sha256,
                "schema_valid": True,
            }
            if isinstance(proposal, ScriptBreakdown) and "dialogue_ratio_percent" in context:
                task.trace = {**task.trace, "dialogue_coverage": {
                    "target_percent": context["dialogue_ratio_percent"],
                    "total_beats": len(proposal.beats),
                    "dialogue_beats": sum(bool(beat.dialogue) for beat in proposal.beats),
                    "adapted_lines": sum(
                        line.text not in body.brief for beat in proposal.beats for line in beat.dialogue
                    ),
                }}
            if body.supersedes_artifact_id:
                invalidate_artifact(db, project_id, body.supersedes_artifact_id)
            add_audit(
                db,
                "film-agent",
                "FILM_PROPOSAL_CREATED",
                "agent_task",
                task.id,
                {"role": role, "kind": artifact.kind, "output_hash": artifact.sha256},
                project_id=project_id,
            )
            db.commit()
            return artifact
        except Exception as exc:  # noqa: BLE001 - classified in _record_task_failure
            raise _record_task_failure(db, task.id, role, exc) from None

    @staticmethod
    def _find_reusable_continuity_lane(
        db: Session,
        project_id: str,
        *,
        lane_kind: str,
        lane_tool: str,
        input_hash: str,
        style_version_id: str | None,
        asset_manifest_hash: str,
        rulepack_hash: str,
        contract_hash: str,
        provider_snapshot: dict,
    ) -> tuple[AgentArtifact, AgentTaskRecord] | None:
        """Return an intact, non-stale sub-artifact when the exact lane input already succeeded."""
        candidates = db.scalars(
            select(AgentArtifact)
            .where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == lane_kind,
                AgentArtifact.stale.is_(False),
            )
            .order_by(AgentArtifact.created_at.desc())
        ).all()
        for art in candidates:
            if digest(art.payload_json) != art.sha256:
                continue
            task = db.get(AgentTaskRecord, art.source_task_id)
            if (
                not task
                or task.status != "SUCCEEDED"
                or task.role_id != "continuity_supervisor"
                or (task.trace or {}).get("tool") != lane_tool
                or task.input_hash != input_hash
                or task.style_version_id != style_version_id
                or task.asset_manifest_hash != asset_manifest_hash
                or task.rulepack_hash != rulepack_hash
                or (task.trace or {}).get("review_contract_sha256") != contract_hash
                or (task.trace or {}).get("review_provider") != provider_snapshot
            ):
                continue
            return art, task
        return None

    def _run_parallel_continuity(
        self,
        db: Session,
        project_id: str,
        *,
        project,
        body: FilmRunRequest,
        inputs: list[AgentArtifact],
        base_context: dict,
        pack,
        profile,
        raw_config,
        bounded_config,
        model_context_metadata: dict,
        agent_output_limit: int | None,
        cache_state: list[dict],
        storyboard_request_sha256: str | None,
        repair_contract_note: str | None,
        authorize_replay_task_id: str | None,
        escalate_output_budget: bool,
        refusal: dict | None,
    ) -> AgentArtifact:
        """Run orthogonal timing/policy and physical/camera continuity passes in parallel,
        then merge deterministically in shot sequence order into one ContinuityReport."""
        from shine.agents.crew_salvage import parse_with_salvage
        from shine.agents.execution import guard_request
        from shine.literary.budget import plan_call_budget
        from shine.literary.requests import compile_request
        from shine.llm.transport_policy import policy_for

        contract_hash = digest(ContinuityReport.model_json_schema())
        provider_snapshot = text_provider_snapshot(profile)
        asset_manifest_hash = digest(base_context["assets"])
        _transport_policy = policy_for(bounded_config)
        _effective_timeout_s = (max(_transport_policy.first_event_s, _transport_policy.idle_s)
                                if _transport_policy.streaming else profile.timeout_s)

        lanes_meta = (
            ("continuity_timing", "ContinuityTimingReport"),
            ("continuity_physical", "ContinuityPhysicalReport"),
        )
        reused: dict[str, tuple[AgentArtifact, AgentTaskRecord]] = {}
        pending: list[dict] = []

        for lane_tool, lane_kind in lanes_meta:
            lane_ctx, lane_rev_ids = _continuity_lane_context(db, project_id, base_context, inputs, lane_tool)
            lane_input_hash = digest(lane_ctx)
            if not body.supersedes_artifact_id:
                hit = self._find_reusable_continuity_lane(
                    db,
                    project_id,
                    lane_kind=lane_kind,
                    lane_tool=lane_tool,
                    input_hash=lane_input_hash,
                    style_version_id=project.frozen_style_version_id,
                    asset_manifest_hash=asset_manifest_hash,
                    rulepack_hash=pack.sha256,
                    contract_hash=contract_hash,
                    provider_snapshot=provider_snapshot,
                )
                if hit is not None:
                    reused[lane_tool] = hit
                    continue
            skill_selection = select_skills_for_role(
                "continuity_supervisor",
                tool=lane_tool,
                brief=body.brief,
                style=lane_ctx.get("style"),
                inputs=lane_ctx.get("inputs"),
                assets=lane_ctx.get("assets"),
                editorial_timing_plan=lane_ctx.get("editorial_timing_plan"),
                production_context=lane_ctx.get("production_context"),
            )
            ctx_bytes = len(json.dumps(lane_ctx, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            pending.append({
                "tool": lane_tool,
                "kind": lane_kind,
                "context": lane_ctx,
                "input_revision_ids": lane_rev_ids,
                "input_hash": lane_input_hash,
                "ctx_bytes": ctx_bytes,
                "skill_selection": skill_selection,
            })

        if pending:
            db.commit()
            for attempt in range(5):
                try:
                    db.execute(text("BEGIN IMMEDIATE"))
                    break
                except Exception as exc:  # noqa: BLE001 - sqlite lock race
                    if "database is locked" not in str(exc).lower() or attempt == 4:
                        raise
                    time.sleep(0.4 * (attempt + 1))
            for item in pending:
                guard_request(
                    db, project_id, "continuity_supervisor", item["input_hash"], profile,
                    contract_hash=contract_hash,
                    authorize_replay_task_id=authorize_replay_task_id,
                )
            for item in pending:
                task = AgentTaskRecord(
                    project_id=project_id,
                    role_id="continuity_supervisor",
                    input_hash=item["input_hash"],
                    style_version_id=project.frozen_style_version_id,
                    asset_manifest_hash=asset_manifest_hash,
                    rulepack_id=pack.id,
                    rulepack_hash=pack.sha256,
                    model_profile_id=profile.id,
                    model_id=profile.model,
                    schema_version=ContinuityReport.model_fields["schema_version"].default,
                    status="RUNNING",
                    trace={
                        "tool": item["tool"],
                        "parent_tool": "propose",
                        "phase": "PREPARING",
                        "offline_preview": body.offline_preview,
                        "remote_text_consent": body.allow_remote_text,
                        "is_mock": profile.kind == "mock",
                        "input_revision_ids": item["input_revision_ids"],
                        **({"production_context": item["context"]["production_context"]}
                           if body.production_context is not None else {}),
                        "schema": ContinuityReport.__name__,
                        "review_contract_sha256": contract_hash,
                        "model_context": {
                            **model_context_metadata,
                            "context_payload_utf8_bytes": item["ctx_bytes"],
                            "agent_output_limit_tokens": agent_output_limit,
                            "input_policy": "full_context_without_fixed_truncation",
                        },
                        **({"storyboard_request_sha256": storyboard_request_sha256}
                           if storyboard_request_sha256 else {}),
                        "review_provider": provider_snapshot,
                        "source_cache": [{"sha256": s["sha256"], "status": s["local_cache_status"]}
                                         for s in cache_state],
                        **({"editorial_refusal": refusal} if refusal else {}),
                        "cinema_skills": item["skill_selection"].as_trace(),
                    },
                )
                db.add(task)
                item["task"] = task
            if authorize_replay_task_id:
                previous = db.get(AgentTaskRecord, authorize_replay_task_id)
                if previous is not None and previous.status in {"REJECTED", "FAILED"}:
                    previous.status = "SUPERSEDED"
                    previous.state_version += 1
            db.commit()

            def _build_lane_prompt(item: dict, skills_block: str) -> str:
                prompt_text = crew_prompt(
                    "continuity_supervisor",
                    tool=item["tool"],
                    plan=item["context"].get("editorial_timing_plan"),
                    max_shot_seconds=item["context"].get("max_shot_seconds"),
                    cinema_skills_block=skills_block,
                )
                if repair_contract_note:
                    prompt_text += repair_contract_note
                return prompt_text

            for item in pending:
                task = item["task"]
                try:
                    if refusal:
                        raise ValueError(refusal["code"])
                    if body.consent_document_id or body.production_context is not None:
                        from shine.materialprep import text_consent
                        source = item["context"].get("production_context") or {}
                        text_consent.guard(
                            project_id,
                            source.get("document_id") or body.consent_document_id,
                            source.get("document_sha256") or body.consent_document_sha256,
                            "continuity_supervisor",
                            text_consent.fingerprint(profile),
                            self.gateway,
                        )(db)
                    sys_prompt = _build_lane_prompt(item, item["skill_selection"].prompt_block)
                    user_json = json.dumps(item["context"], ensure_ascii=False, separators=(",", ":"))
                    crew_request = compile_request(
                        system=sys_prompt, user=user_json, schema=ContinuityReport.model_json_schema(),
                    )
                    crew_budget = plan_call_budget(
                        raw_config, crew_request, purpose="observation",
                        output_ceiling=agent_output_limit,
                        output_floor_tokens=agent_output_limit if escalate_output_budget else None,
                    )
                    if not crew_budget.fits and item["skill_selection"].prompt_block:
                        sys_prompt = _build_lane_prompt(item, "")
                        crew_request = compile_request(
                            system=sys_prompt, user=user_json, schema=ContinuityReport.model_json_schema(),
                        )
                        crew_budget = plan_call_budget(
                            raw_config, crew_request, purpose="observation",
                            output_ceiling=agent_output_limit,
                            output_floor_tokens=agent_output_limit if escalate_output_budget else None,
                        )
                        task.trace = {
                            **task.trace,
                            "cinema_skills": {
                                **task.trace["cinema_skills"],
                                "budget_trimmed_to_zero": True,
                                "selected": [],
                                "estimated_tokens": 0,
                            },
                        }
                    task.trace = {
                        **task.trace,
                        "call_budget": crew_budget.as_dict(),
                        **({"output_budget_escalated": True} if escalate_output_budget else {}),
                    }
                    db.commit()
                    if not crew_budget.fits:
                        raise ValueError(str((crew_budget.refusal or {}).get("code")
                                             or "LLM_CREATIVE_CONTEXT_OVERFLOW"))
                    budget_arg = {"completion_cap": crew_budget.output_request_tokens,
                                  "streaming": crew_budget.streaming}
                    item["crew_request"] = crew_request
                    item["budget"] = budget_arg
                    item["prepared"] = self.gateway.prepare_role_call(
                        db, "continuity_supervisor", precompiled=crew_request, budget=budget_arg,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise _record_task_failure(db, task.id, "continuity_supervisor", exc) from None

            can_parallel_remote = (
                len(pending) > 1
                and getattr(self.gateway.complete_for_role, "__qualname__", "") == "LLMGateway.complete_for_role"
                and all(not item["prepared"]["needs_local_gpu"] and item["prepared"]["profile"].kind != "mock"
                        for item in pending)
            )
            outcomes: dict[str, tuple[object | None, Exception | None]] = {}
            if can_parallel_remote:
                for item in pending:
                    task = item["task"]
                    task.trace = {
                        **task.trace,
                        "phase": "WAITING_PROVIDER",
                        "phase_started_at": utcnow().isoformat(),
                        "network_timeout_s": _effective_timeout_s,
                    }
                db.commit()

                def _call_remote(prepared_call):
                    return self.gateway.execute_prepared_call(prepared_call)

                with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                    futures = {item["tool"]: pool.submit(_call_remote, item["prepared"]) for item in pending}
                    for tool_name, fut in futures.items():
                        try:
                            outcomes[tool_name] = (fut.result(), None)
                        except Exception as exc:  # noqa: BLE001
                            outcomes[tool_name] = (None, exc)
            else:
                for item in pending:
                    task = item["task"]

                    def _progress(phase: str, _task=task) -> None:
                        _task.trace = {**_task.trace, "phase": phase, "phase_started_at": utcnow().isoformat(),
                                       "network_timeout_s": _effective_timeout_s}
                        db.commit()

                    try:
                        resp = self.gateway.complete_for_role(
                            db,
                            "continuity_supervisor",
                            precompiled=item["crew_request"],
                            budget=item["budget"],
                            on_progress=_progress,
                        )
                        outcomes[item["tool"]] = (resp, None)
                    except Exception as exc:  # noqa: BLE001
                        outcomes[item["tool"]] = (None, exc)

            first_error: ValueError | None = None
            for item in pending:
                task = item["task"]
                resp, call_exc = outcomes[item["tool"]]
                if call_exc is not None:
                    recorded = _record_task_failure(db, task.id, "continuity_supervisor", call_exc)
                    if first_error is None:
                        first_error = recorded
                    continue
                try:
                    task.trace = {
                        **task.trace,
                        "phase": "VALIDATING_RESULT",
                        "received_result_sha256": digest(resp.parsed),
                        "response_latency_ms": resp.latency_ms,
                        **{key: resp.raw_meta[key] for key in ("http_status", "provider_code")
                           if isinstance(resp.raw_meta.get(key), int)},
                        "response_model_sha256": hashlib.sha256((resp.model or "").encode()).hexdigest(),
                        "is_mock": bool(task.trace.get("is_mock") or resp.raw_meta.get("is_mock")
                                        or resp.raw_meta.get("mock")),
                    }
                    db.commit()
                    if body.production_context is not None:
                        db.expire_all()
                        verified_context(db, project_id, body)
                    proposal, salvage_notes = parse_with_salvage(resp.parsed, ContinuityReport)
                    if salvage_notes:
                        task.trace = {**task.trace, "salvage_notes": salvage_notes}
                        db.commit()
                    payload = proposal.model_dump(mode="json")
                    sub_artifact = AgentArtifact(
                        project_id=project_id,
                        kind=item["kind"],
                        schema_version=proposal.schema_version,
                        payload_json=payload,
                        sha256=digest(payload),
                        producer_role="continuity_supervisor",
                        source_task_id=task.id,
                        input_revision_ids=item["input_revision_ids"],
                    )
                    db.add(sub_artifact)
                    task.status = "SUCCEEDED"
                    task.trace = {
                        **task.trace,
                        "phase": "SUCCEEDED",
                        "is_mock": bool(task.trace.get("is_mock") or resp.raw_meta.get("is_mock")
                                        or resp.raw_meta.get("mock")),
                        "latency_ms": resp.latency_ms,
                        "usage": resp.usage,
                        "output_hash": sub_artifact.sha256,
                        "schema_valid": True,
                    }
                    db.commit()
                    reused[item["tool"]] = (sub_artifact, task)
                except Exception as exc:  # noqa: BLE001
                    recorded = _record_task_failure(db, task.id, "continuity_supervisor", exc)
                    if first_error is None:
                        first_error = recorded
            if first_error is not None:
                raise first_error

        timing_art, timing_task = reused["continuity_timing"]
        physical_art, physical_task = reused["continuity_physical"]

        db.expire_all()
        project = require_project(db, project_id)
        if (
            project.frozen_style_version_id != physical_task.style_version_id
            or digest(asset_context(db, project_id)) != physical_task.asset_manifest_hash
        ):
            raise ValueError("INPUT_CONTEXT_CHANGED_DURING_CALL")
        load_inputs(db, project_id, body.input_revision_ids)

        director_art = next(a for a in inputs if a.kind == "DirectorialPlan")
        merged = merge_continuity_reports(
            director_art.payload_json,
            ContinuityReport.model_validate(timing_art.payload_json),
            ContinuityReport.model_validate(physical_art.payload_json),
        )
        merged_payload = merged.model_dump(mode="json")
        artifact = AgentArtifact(
            project_id=project_id,
            kind=ContinuityReport.__name__,
            schema_version=merged.schema_version,
            payload_json=merged_payload,
            sha256=digest(merged_payload),
            producer_role="continuity_supervisor",
            source_task_id=physical_task.id,
            input_revision_ids=body.input_revision_ids,
        )
        db.add(artifact)
        physical_task.trace = {
            **physical_task.trace,
            "sub_artifacts": {
                "continuity_timing": timing_art.id,
                "continuity_physical": physical_art.id,
            },
            "sub_tasks": {
                "continuity_timing": timing_task.id,
                "continuity_physical": physical_task.id,
            },
            "merged_output_hash": artifact.sha256,
        }
        if body.supersedes_artifact_id:
            invalidate_artifact(db, project_id, body.supersedes_artifact_id)
        add_audit(
            db,
            "film-agent",
            "FILM_PROPOSAL_CREATED",
            "agent_task",
            physical_task.id,
            {"role": "continuity_supervisor", "kind": artifact.kind, "output_hash": artifact.sha256},
            project_id=project_id,
        )
        db.commit()
        return artifact

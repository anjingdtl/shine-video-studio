"""Controlled film work units on the existing gateway and application database."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from shine.agents.crew_prompts import arbiter_prompt, crew_prompt
from shine.agents.editorial_timing import (
    editorial_timing_blocker,
    editorial_timing_plan,
    editorial_timing_preview,
)
from shine.agents.film_contracts import (
    CameraPlan,
    ContinuityArbitration,
    DirectorialPlan,
    EditDecisionList,
    EditIntentPlan,
    FilmAgentRegistry,
    FilmRunRequest,
    ScriptBreakdown,
    SoundPlan,
    StyleInputAssessment,
    StyleInputWire,
)
from shine.asset_identity import (
    canonical_character_name_for_asset_id,
    resolve_character_asset,
    split_character_names,
)
from shine.h3.rulebook import H3RulebookRegistry, digest
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
        shots.append({
            "shot_id": shot.get("id"),
            "asset_ids": list(shot.get("asset_ids", [])),
            "roles": roles,
            "missing_required_roles": missing,
            "policy_compliant": not missing,
            "excluded_assets_used": [
                a["id"] for a in used if a["role"] in SHOT_REFERENCE_EXCLUDED_ROLES
            ],
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
                "image_slots": sum(1 for k in keys if k.startswith(("reference_subject_", "reference_picture_"))),
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
                                           for value in translated_style.model_dump().values()):
                raise ValueError("H3_STYLE_TRANSLATION_REQUIRED")
        if any(re.search(r"[\u3400-\u9fff]", fact) for fact in body.locked_story_facts):
            translations = proposal.prompt_story_facts
            if (len(translations) != len(body.locked_story_facts)
                    or any(not text.strip() or re.search(r"[\u3400-\u9fff]", text) for text in translations)):
                raise ValueError("H3_LOCKED_FACT_TRANSLATION_REQUIRED")
        if len({beat.id for beat in proposal.beats}) != len(proposal.beats):
            raise ValueError("DUPLICATE_BEAT")
        for beat in proposal.beats:
            if any(line.text not in body.brief for line in beat.dialogue):
                raise ValueError("DIALOGUE_NOT_IN_USER_SCRIPT")
    elif isinstance(proposal, DirectorialPlan):
        script = next(a for a in inputs if a.kind == "ScriptBreakdown")
        beats = {b["id"]: b for b in script.payload_json["beats"]}
        assets = {a["id"] for a in context["assets"]}
        if len({s.id for s in proposal.shots}) != len(proposal.shots):
            raise ValueError("DUPLICATE_SHOT_INTENT")
        timing = context.get("editorial_timing")
        timeline = []
        elapsed = 0.0
        for shot in proposal.shots:
            # Spoken words belong to the dialogue structure, never to the
            # visual prose: text the model renders as picture description is
            # otherwise read aloud by H3 outside the audio channel.
            visual = " ".join([shot.visual_description, *(cut.visual_description for cut in shot.cuts)])
            descriptive = re.sub(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」", "", visual)
            if re.search(r"[㐀-鿿]", descriptive):
                raise ValueError("VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")
            beat_ids = [shot.beat_id, *(cut.beat_id for cut in shot.cuts)]
            starts = [0.0, *(cut.start_s for cut in shot.cuts)]
            if any(b not in beats for b in beat_ids) or not set(shot.asset_ids).issubset(assets):
                raise ValueError("UNAPPROVED_ASSET_OR_UNKNOWN_BEAT")
            if starts != sorted(set(starts)) or starts[-1] >= shot.duration_s:
                raise ValueError("EDITORIAL_TIMING_CONFLICT")
            if timing or shot.cuts:
                ends = starts[1:] + [shot.duration_s]
                for beat_id, start, end in zip(beat_ids, starts, ends, strict=True):
                    if any(line["end_s"] > end - start + 1e-6 for line in beats[beat_id].get("dialogue", [])):
                        raise ValueError("EDITORIAL_TIMING_CONFLICT")
            timeline.extend((beat_id, round(elapsed + start, 3))
                            for beat_id, start in zip(beat_ids, starts, strict=True))
            elapsed += shot.duration_s
            bound_character_assets = [
                a
                for a in context["assets"]
                if a["id"] in shot.asset_ids and a["kind"] == "image" and a["role"] == "character"
            ]
            required_characters = [name for beat_id in beat_ids for name in beats[beat_id]["characters"]]
            for beat_id in beat_ids:
                for line in beats[beat_id].get("dialogue", []):
                    speaker = line.get("speaker_id")
                    if not speaker:
                        continue
                    # A chorus line names several speakers in one field
                    # ("阿德, 小米").  Every consumer splits a name list the
                    # same way; a whole-list speaker would otherwise look like
                    # a character nobody has a master for.
                    for part in split_character_names(speaker):
                        if part not in required_characters:
                            required_characters.append(part)
            if any(resolve_character_asset(name, bound_character_assets) is None for name in required_characters):
                raise ValueError("CHARACTER_MASTER_BINDING_REQUIRED")
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
        for shot in proposal.shots:
            # Same rule as the director's visual prose: camera language feeds
            # the compiled H3 prompt and must stay in the prompt language.
            for text in (shot.composition, shot.movement, shot.lighting, shot.axis):
                descriptive = re.sub(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」", "", text)
                if re.search(r"[㐀-鿿]", descriptive):
                    raise ValueError("VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")
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


class FilmAgentDispatcher:
    def __init__(self, gateway: LLMGateway, data_dir: Path | None = None):
        self.gateway = gateway
        self.data_dir = data_dir

    def run(
        self,
        db: Session,
        project_id: str,
        role: str,
        body: FilmRunRequest,
        *,
        storyboard_request_sha256: str | None = None,
        repair_visual_language: bool = False,
        replace_interrupted_task_id: str | None = None,
        repair_director: bool = False,
    ) -> AgentArtifact:
        if replace_interrupted_task_id is not None:
            raise ValueError("AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
        contract, required = FilmAgentRegistry.get(role, body.tool)
        project = require_project(db, project_id)
        if body.tool != "style_proposal" and not project.frozen_style_version_id:
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
            "assets": asset_context(db, project_id),
            "inputs": [{"id": a.id, "kind": a.kind, "sha256": a.sha256, "payload": a.payload_json} for a in inputs],
            "rules": registry.search(() if body.tool == "continuity_arbitration"
                                     else FilmAgentRegistry.roles[role][3]),
            "rulepack_id": pack.id,
            "rulepack_hash": pack.sha256,
            "eligible_edits": eligible_edits(db, project_id, offline=body.offline_preview, artifact_root=self.data_dir)
            if role == "editor"
            else [],
        }
        if body.production_context is not None:
            from shine.materialprep.story import verified_production_context
            context["production_context"] = verified_production_context(
                db, project_id, body.brief, body.production_context)
        refusal: dict | None = None
        if role in {"scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor"}:
            timing = editorial_timing_preview(body.brief)
            if timing:
                blocker = editorial_timing_blocker(timing)
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

        bounded_config = agent_output_config(self.gateway.config_for(profile))
        agent_output_limit = (bounded_config.generation_options.get("max_completion_tokens")
                              or bounded_config.generation_options.get("max_tokens"))
        if bounded_config.kind == "anthropic":
            agent_output_limit = min(4096, bounded_config.max_output_tokens or 4096)
        context_payload_utf8_bytes = len(
            json.dumps(model_context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
                      contract_hash=contract_hash)
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
                **({"scope": "TEXT_INPUT_CONSISTENCY_ONLY_NOT_MEDIA_QC",
                    "shot_spec_snapshot": context["shot_spec"],
                    "shot_spec_sha256": digest(context["shot_spec"]),
                    "style_hash": context["style_hash"], "text_input_sha256": digest(model_context)}
                   if role == "qc_reviewer" else {}),
            },
        )
        db.add(task)
        db.commit()  # Durable intent before the model call. Interrupted calls are never auto-replayed.
        if body.tool == "continuity_arbitration":
            system_prompt = arbiter_prompt()
        else:
            system_prompt = crew_prompt(
                role,
                tool=body.tool,
                plan=context.get("editorial_timing_plan"),
                reference_budget_note=reference_budget_note(context.get("workflow_reference_budget") or []),
                repair_director=repair_director,
            )

        def record_progress(phase: str) -> None:
            # Commit diagnostic state before waiting externally; never hold
            # SQLite's writer across resource admission or a provider call.
            task.trace = {**task.trace, "phase": phase, "phase_started_at": utcnow().isoformat(),
                          "network_timeout_s": profile.timeout_s}
            db.commit()

        if repair_visual_language:
            system_prompt += (
                " The previous proposal failed the visual language check. Rewrite all visual and camera "
                "descriptions in English, including every segment's cuts. Check for copied Chinese names, "
                "stage directions and lyrics. Keep the original timeline, approved references and dialogue contract."
            )

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
            response = self.gateway.complete_for_role(
                db,
                role,
                system=system_prompt,
                user=json.dumps(model_context, ensure_ascii=False, separators=(",", ":")),
                schema=wire_contract.model_json_schema(),
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
                verified_production_context(db, project_id, body.brief, body.production_context)
            response_data = response.parsed
            if (contract is ScriptBreakdown and isinstance(response_data, dict)
                    and "locked_story_facts" not in response_data):
                # Immutable user facts are owned by the application, so the
                # model need not echo them just to reproduce the snapshot.
                response_data = {**response_data, "locked_story_facts": body.locked_story_facts}
            proposal = (bind_input_judgments(response_data, context) if role == "qc_reviewer"
                        else contract.model_validate(response_data))
            proposal = canonicalize_script_character_asset_ids(proposal, context["assets"])
            validate_output(role, proposal, body, inputs, context)
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
                    db, project_id, offline=body.offline_preview, artifact_root=self.data_dir
                )
                validate_output(role, proposal, body, inputs, context)
            payload = proposal.model_dump(mode="json")
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
        except (ValueError, ValidationError, LLMError) as exc:
            from shine.agents.execution import UNKNOWN_CODES, rejection_reason

            db.rollback()
            task = db.get(AgentTaskRecord, task.id)
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
                        or isinstance(exc, LLMError) and isinstance(exc.details.get("status"), int)):
                    external_outcome = "RESPONSE_RECEIVED"
                elif phase in {"PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER"}:
                    external_outcome = "NOT_SENT"
                else:
                    external_outcome = "UNKNOWN"
            uncertain = external_outcome == "UNKNOWN" and not task.trace.get("is_mock")
            task.status = "NEEDS_REVIEW" if uncertain else "REJECTED"
            task.state_version += 1
            # Never persist provider text, prompts or exception strings that can contain secrets.
            task.error_code = exc.code if isinstance(exc, LLMError) else "CONTRACT_OR_POLICY_REJECTED"
            diagnostic = ("schema_validation" if isinstance(exc, ValidationError) else
                          "provider_format" if isinstance(exc, LLMError) and exc.code == "LLM_FORMAT" else
                          "provider_error" if isinstance(exc, LLMError) else "contract_or_policy")
            task.trace = {**task.trace, "automatic_replay": False,
                          "diagnostic_category": diagnostic,
                          **({"http_status": exc.details["status"]}
                             if isinstance(exc, LLMError) and isinstance(exc.details.get("status"), int) else {}),
                          **({"provider_code": exc.details["provider_code"]}
                             if isinstance(exc, LLMError)
                             and isinstance(exc.details.get("provider_code"), int) else {}),
                          **({"provider_schema_path": exc.details["path"],
                              "provider_schema_validator": exc.details["validator"]}
                             if isinstance(exc, LLMError) and exc.code == "LLM_FORMAT"
                             and isinstance(exc.details.get("path"), str) else {}),
                          "external_outcome": external_outcome,
                          "rejection_reason_code": exc.code if isinstance(exc, LLMError) else rejection_reason(exc)}
            db.commit()
            raise ValueError(task.error_code) from None
        except Exception as exc:
            db.rollback()
            task = db.get(AgentTaskRecord, task.id)
            phase = task.trace.get("phase")
            # The gateway persists WAITING_RESOURCE before queuing and only
            # advances to WAITING_PROVIDER after the local resource slot has
            # been admitted and pre-call release has completed.  A failure
            # before that durable boundary cannot have reached the provider.
            pre_request = phase in {"PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER"}
            unknown = (not pre_request and not task.trace.get("is_mock")
                       and not task.trace.get("received_result_sha256"))
            if pre_request:
                detail = getattr(exc, "detail", None)
                safe_detail = (
                    detail if isinstance(detail, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", detail) else None
                )
                task.status = "REJECTED"
                task.state_version += 1
                task.error_code = "AGENT_PRE_REQUEST_FAILED"
                task.trace = {**task.trace, "automatic_replay": False,
                              "diagnostic_category": "pre_request_resource",
                              "external_outcome": "NOT_SENT",
                              "rejection_reason_code": task.error_code,
                              **({"pre_request_error_code": safe_detail} if safe_detail else {})}
            else:
                task.status = "NEEDS_REVIEW" if unknown else "FAILED"
                task.state_version += 1
                task.error_code = "AGENT_CALL_FAILED"
                task.trace = {**task.trace, "automatic_replay": False,
                              **({"external_outcome": "UNKNOWN"} if unknown else {})}
            db.commit()
            raise ValueError(task.error_code) from None

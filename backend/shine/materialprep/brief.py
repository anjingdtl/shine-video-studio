"""The producer's appearance brief for one confirmed material requirement.

The material path does not borrow the film dispatcher.  That chain demands a
frozen Style Bible and a frozen H3 rule pack, while a material brief is
written *before* the style is frozen and has nothing to do with the H3 rule
pack.  This module therefore makes its own narrow, auditable call:

* the role routing is explicit (``producer``), and a remote provider is
  refused because the Worker path carries no per-request consent;
* the contract is ``MaterialImageBrief`` and nothing else;
* the identity (project, requirement, family, visual direction) is fixed by the
  server envelope and is never read back from the model;
* the context is budgeted *before* the call and an over-budget request is
  refused by name - the budget is never bought by truncating a locked fact;
* a schema error earns at most ONE targeted correction that carries only the
  broken fields, the validator and the original task;
* an uncertain external outcome becomes ``NEEDS_REVIEW`` and is never replayed;
* the call happens before the GPU lease is taken, because the gateway refuses
  to run a text model while a resource slot is held.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select

from shine.agents.crew_prompts import material_image_brief_prompt
from shine.h3.rulebook import digest
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import text_consent
from shine.materialprep.agent_call import budget_report, call_material_agent
from shine.materialprep.contracts import MaterialImageBrief
from shine.materialprep.image_prompts import template_evidence
from shine.materialprep.ledger import touch_item
from shine.materialprep.models import MaterialItem, MaterialRun
from shine.materialprep.states import (
    ITEM_BRIEF_READY,
    ITEM_BRIEF_RUNNING,
    ITEM_FAILED,
    ITEM_NEEDS_REVIEW,
    ITEM_QUEUED,
)
from shine.models import AgentArtifact, AgentTaskRecord, Project, utcnow
from shine.services import add_audit

BRIEF_ROLE = "producer"
BRIEF_CONTRACT = MaterialImageBrief
RULEPACK_ID = "material-image-prompt"

CONTEXT_MARGIN_TOKENS = 512
"""The safety margin kept free in the context window."""

MIN_OUTPUT_RESERVE_TOKENS = 4_096
MAX_OUTPUT_RESERVE_TOKENS = 131_072

CORRECTION_RULE = (
    " Your previous answer did not satisfy the contract. Fix only the fields the "
    "validator named, keep everything else that was already correct, and return the same "
    "JSON object shape."
)


def rulepack_digest() -> str:
    """The identity of the material prompt rules this brief is bound to."""
    evidence = template_evidence()
    return digest({
        "template_version": evidence["template_version"],
        "template_sha256": digest(evidence),
        "brief_prompt_sha256": hashlib.sha256(
            material_image_brief_prompt().encode("utf-8")
        ).hexdigest(),
    })


def build_context(
    *,
    requirement_payload: dict[str, Any],
    visual_direction: dict[str, Any],
    locked_facts: list[str],
    project_language: str,
    style_bible: dict[str, Any] | None = None,
    style_version: int | None = None,
    film_style: str = "",
    story_world: str = "",
    script_entity_dossier: dict[str, Any] | None = None,
    active_style_lora_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The server envelope the model receives.

    The identity is fixed here and is never read back from the answer: the
    model describes an appearance, it does not decide who the item is.
    """
    family = str(requirement_payload.get("family") or "").strip().lower()
    if family == "scene":
        family_rules = [
            "This is an EMPTY environment plate: describe ONLY the architecture, spatial layout, "
            "furniture, weather, practical lighting and surface materials. NEVER mention, imply or "
            "depict any person, human figure, silhouette, character, costume, posture or character action.",
            "Read story_world and locked_story_facts strictly to infer the era, architecture, weather "
            "and lighting atmosphere; strip out every character name, dialogue and plot action.",
            # A prop the characters carry is its own material, bound to the shots that use it.
            # Letting the producer plant it in the place (a carried lantern read as a practical
            # light on the altar step) puts the same object on screen twice once the shot also
            # binds the prop, so the boundary is stated here, where the scene brief is authored.
            "Props that a character carries, holds or uses in the story - weapons, lanterns, tools, "
            "letters, anything the script lists among its key props - are separate prop materials "
            "bound shot by shot: NEVER place, mention or depict them in the environment plate, even "
            "when they give off light. Describe only fixtures that belong to the place itself, such "
            "as wall torches, chandeliers, windows or built-in hearths.",
            "Write the appearance as one flowing descriptive passage for a Qwen-Image-2.1 prompt: name the "
            "space and architecture first, then layout and set dressing, then lighting, weather and surface "
            "materials. Use concrete nouns ('weathered dark timber', 'rain-beaded lattice glass'), not bare "
            "colours or bare materials.",
        ]
    elif family == "prop":
        family_rules = [
            "This is a standalone prop still-life study: describe ONLY the single complete object, its "
            "shape, surface materials, patina and legible carrier content (for paper/screen). NEVER include "
            "any person, hands, fingers, holder, wearer or room background.",
            "Read story_world and locked_story_facts strictly to infer the era and physical craftsmanship "
            "of the object; never depict characters or plot actions.",
            "Write the appearance as one flowing descriptive passage for a Qwen-Image-2.1 prompt: name the "
            "object first, then its structure and visible details, then its surface materials and textures. "
            "Use concrete nouns ('brushed brass', 'foxed rag paper'), not bare colours or bare materials.",
        ]
    else:
        family_rules = [
            "The subject belongs to the era and setting in story_world (and the locked story facts). "
            "Carry the period costume, hair, architecture and materials into the English prose; a "
            "period or fantasy script must not render as modern people in modern dress. Translate the "
            "world's intent into English, never paste the Chinese.",
            "Write the appearance as one flowing descriptive passage for a Qwen-Image-2.1 prompt: name the "
            "subject first, then its dress and build, then the surface materials and their textures. Use "
            "concrete nouns ('brushed brass', 'deep navy wool'), not bare colours or bare materials.",
        ]
    return {
        "task": "Write one appearance brief for one confirmed material requirement.",
        "requirement": {
            "family": requirement_payload.get("family") or "",
            "name": requirement_payload.get("entity_id") or "",
            "description": requirement_payload.get("description") or "",
            "scene_keys": requirement_payload.get("scene_keys") or [],
            "user_note": requirement_payload.get("brief_note") or "",
        },
        "visual_direction": visual_direction or {},
        "frozen_style_bible": style_bible or {},
        "frozen_style_version": style_version,
        "film_style_instruction": film_style,
        "story_world": story_world,
        "script_entity_dossier": script_entity_dossier or {},
        "active_style_lora_context": active_style_lora_context or {},
        "locked_story_facts": list(locked_facts),
        "script_language": project_language,
        "output_contract": BRIEF_CONTRACT.__name__,
        "rules": [
            "Write appearance, must_keep and avoid in English visual prose.",
            "The requirement text stays in the original script language; only the appearance prose is English.",
            "Never repeat the requirement name inside the appearance prose: describe the subject "
            "in English words instead. A Chinese name inside the English prose is a refused prompt, "
            "not a style choice.",
            "Add no new plot fact and change no confirmed identity.",
            "Translate the immutable traits in script_entity_dossier faithfully. Start appearance with "
            "the subject and its definitive visible traits, and preserve each locked visible trait in must_keep. "
            "Carry the exact age, sex, ancestry or region, facial features, hair and signature period garments "
            "where supplied. Do not replace period clothing with modern clothing or borrow another scene's outfit.",
            "Translate the frozen style into English prose. When active_style_lora_context is present, "
            "use it for compatible lighting and texture. Do not add a generic studio lighting clause or "
            "change any story trait or the frozen film medium to fit it.",
            "The picture's medium is fixed by film_style_instruction: apply it to every surface "
            "(cel-shaded flat fills and clean line art for animation; photorealistic physical materials "
            "and lens texture for live-action). A photoreal image for an animation film is a refused brief, "
            "not a style choice.",
            *family_rules,
            "Never write quality slogans such as masterpiece, best quality, 8k, 4k, highly detailed, "
            "award winning or trending on artstation: the provider guidance rules them out and they add "
            "no picture information.",
            "Describe only what is in the picture. The negative field exists, but the appearance prose "
            "itself must stay positive and observational.",
        ],
    }


class BriefError(RuntimeError):
    """A refusal that carries a stable reason code."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _load_context(factory, item_id: str) -> dict[str, Any]:
    """Everything the brief envelope is built from, read once."""
    with factory() as db:
        item = db.get(MaterialItem, item_id)
        if item is None:
            raise BriefError("MATERIAL_ITEM_NOT_FOUND", item_id=item_id)
        return context_for_item(db, item)


def _dossier_match(rows, names: set[str], family: str) -> dict[str, Any]:
    from shine.asset_identity import character_reference_matches

    candidates = [row for row in rows if isinstance(row, dict)]
    for compare in (
        lambda a, b: a == b,
        character_reference_matches if family == "character" else lambda a, b: a.casefold() == b.casefold(),
    ):
        matches = [row for row in candidates if any(
            compare(name, str(candidate)) for name in names
            for candidate in [row.get("entity_id"), row.get("name"), *(row.get("aliases") or [])] if candidate
        )]
        if matches:
            return dict(matches[0]) if len(matches) == 1 else {}
    return {}


def _entity_dossier(db, project_id: str, requirement: dict, document_id: str | None, scenes: list[dict]) -> dict:
    from shine.director.pipeline import BIBLE_KIND, current_bible
    from shine.materialprep.catalog import latest_entity_catalog
    from shine.materialprep.story import approved_outline_for

    family = requirement.get("family")
    entity_id = requirement.get("entity_id")
    collection = {"character": "characters", "scene": "scenes", "prop": "props"}.get(family)
    if not collection or not entity_id:
        return {}
    definition = requirement.get("character_definition") or {}
    names = {str(name) for name in (entity_id, definition.get("canonical_name"), *(definition.get("aliases") or []))
             if name}
    catalog = latest_entity_catalog(db, project_id, document_id)
    entity = _dossier_match((catalog.payload_json or {}).get(collection, []) if catalog else [], names, family)
    if entity:
        names.update(str(name) for name in (entity.get("name"), *(entity.get("aliases") or [])) if name)
    bible = current_bible(db, project_id) or db.scalar(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id, AgentArtifact.kind == BIBLE_KIND,
        AgentArtifact.approved_at.is_not(None), AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc()))
    outline = approved_outline_for(db, project_id, document_id)
    outline_payload = (outline.payload_json or {}) if outline else {}
    if document_id and outline_payload.get("document_id") not in (None, document_id):
        outline_payload = {}
    return {
        "family": family,
        "entity_id": entity_id,
        "confirmed_description": requirement.get("description") or "",
        "user_note": requirement.get("brief_note") or "",
        "catalog": entity or definition,
        "story_bible": _dossier_match((bible.payload_json or {}).get(collection, []) if bible else [], names, family),
        "story_outline": _dossier_match(outline_payload.get(collection, []), names, family),
        "related_scenes": [{key: scene[key] for key in ("scene_key", "summary", "locked_facts") if key in scene}
                           for scene in scenes],
    }


def context_for_item(db, item: MaterialItem) -> dict[str, Any]:
    from shine.comfyops.models import AssetRequirement, PreProductionPlan
    from shine.materialprep.prompt_source import film_style_for, project_visual_facts

    run = db.get(MaterialRun, item.run_id)
    requirement = db.get(AssetRequirement, item.requirement_id) if item.requirement_id else None
    plan = db.get(PreProductionPlan, run.plan_id) if run else None
    project = db.get(Project, item.project_id)
    plan_payload = (plan.payload or {}) if plan else {}
    payload = dict(item.payload or {})
    requirement_payload = dict((requirement.payload or {}) if requirement else {})
    scene_keys = set(requirement_payload.get("scene_keys") or [])
    scenes = [scene for scene in plan_payload.get("scene_preparations") or []
              if not scene_keys or scene.get("scene_key") in scene_keys]
    locked_facts = list(dict.fromkeys(fact for scene in scenes for fact in scene.get("locked_facts") or []))
    document_id = plan_payload.get("document_id")
    facts = project_visual_facts(db, item.project_id, document_id=document_id)
    dossier = _entity_dossier(db, item.project_id, requirement_payload, document_id, scenes if scene_keys else [])
    family = str(requirement_payload.get("family") or payload.get("family") or "")
    direction = dict(payload.get("visual_direction") or {})
    preset = payload.get("style_preset_snapshot") or {}
    loras = payload.get("style_loras") or []
    brief_basis = digest({
        "style_version_id": facts["style_version_id"],
        "world": facts["world"],
        "family": family,
        "visual_direction": direction,
        "locked_facts": locked_facts,
        "entity_dossier_sha256": digest(dossier),
        "style_preset_id": payload.get("style_preset"),
        "style_preset_snapshot": preset,
        "style_loras": [{key: row.get(key) for key in (
            "lora_id", "sha256", "strength_model", "trigger_word", "negative_trigger_word", "trigger_position",
        )} for row in loras],
        "rulepack": rulepack_digest(),
    })
    return {
        "style_bible": facts["snapshot"],
        "style_version": facts["style_version"],
        "film_style": film_style_for(facts["snapshot"], family=family) or facts["film_style"],
        "story_world": facts["world"],
        "script_entity_dossier": dossier,
        "active_style_lora_context": {
            "preset": preset.get("display_name"), "visual_cues": preset.get("positive_prompt"),
            "positive_triggers": [row["trigger_word"] for row in loras if row.get("trigger_word")],
            "negative_triggers": [row["negative_trigger_word"] for row in loras if row.get("negative_trigger_word")],
        } if loras or preset else {},
        "brief_basis": brief_basis,
        "existing_brief_basis": payload.get("brief_basis"),
        "document_id": document_id,
        "document_sha256": plan_payload.get("script_hash"),
        "item_id": item.id,
        "requirement_id": item.requirement_id,
        "project_id": item.project_id,
        "item_status": item.status,
        "item_payload": payload,
        "requirement_payload": requirement_payload,
        "visual_direction": direction,
        "locked_facts": locked_facts,
        "project_language": (project.language if project else "") or "",
        "existing_brief": payload.get("brief"),
    }


def _refuse(factory, item_id: str, version: int, code: str, *, detail: dict[str, Any] | None = None):
    """Stop the item with a named code and record why."""
    new_version = touch_item(factory, item_id, version, status=ITEM_FAILED, error_code=code)
    with factory.begin() as db:
        add_audit(db, "worker", "MATERIAL_BRIEF_REFUSED", "material_item", item_id,
                  {"code": code, **(detail or {})})
    return {"status": ITEM_FAILED, "code": code, "state_version": new_version, "brief": None, "ready": False}


def ensure_brief(factory, settings, item_id: str, worker_id: str, *, version: int) -> dict[str, Any]:
    """Make sure the item carries a producer brief, producing it if needed.

    Returns the new item state version so the caller can keep writing against
    the version it owns.  A brief bound to the current visual facts is never
    re-asked: the producer answered once, and a second answer would be a second
    opinion the user never requested.  When the frozen style or the world fact
    changes, the old brief is stale history and a new one is produced.
    """
    data = _load_context(factory, item_id)
    if data["existing_brief"] and (
        data["existing_brief_basis"] == data["brief_basis"]
        or data["item_payload"].get("kind") == "validation"
    ):
        return {"status": data["item_status"], "brief": data["existing_brief"],
                "state_version": version, "reused": True, "ready": True}

    context = build_context(
        requirement_payload=data["requirement_payload"],
        visual_direction=data["visual_direction"],
        locked_facts=data["locked_facts"],
        project_language=data["project_language"],
        style_bible=data.get("style_bible"),
        style_version=data.get("style_version"),
        film_style=data.get("film_style") or "",
        story_world=data.get("story_world") or "",
        script_entity_dossier=data["script_entity_dossier"],
        active_style_lora_context=data["active_style_lora_context"],
    )
    system_prompt = material_image_brief_prompt()
    schema = BRIEF_CONTRACT.model_json_schema()

    gateway = LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))
    with factory() as db:
        try:
            profile = gateway.profile_for_role(db, BRIEF_ROLE)
        except LLMError as exc:
            return _refuse(factory, item_id, version, "LLM_NOT_CONFIGURED",
                           detail={"role": BRIEF_ROLE, "code": exc.code})
        from shine.models import ProjectDocument
        document = db.get(ProjectDocument, data["document_id"]) if data["document_id"] else None
        if not text_consent.allowed(db, data["project_id"], document, BRIEF_ROLE, profile):
            # The Worker path carries no per-request consent, and the brief
            # carries script text. A remote provider needs an explicit consent
            # that only an operator action can give.
            return _refuse(factory, item_id, version, "REMOTE_AGENT_CONSENT_REQUIRED",
                           detail={"role": BRIEF_ROLE,
                                   "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest()})
        config = gateway.config_for(profile)

    budget = budget_report(context, schema, system_prompt, config)
    if not budget["fits"]:
        return _refuse(factory, item_id, version, "LLM_CONTEXT_BUDGET_EXCEEDED", detail=budget)

    # The brief is a real call: the item says so before the provider is asked.
    running_version = touch_item(factory, item_id, version, status=ITEM_BRIEF_RUNNING, error_code=None)
    if running_version is None:
        return {"status": "CLAIM_LOST", "code": "CLAIM_LOST", "state_version": None, "brief": None,
                "ready": False}
    version = running_version

    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=data["project_id"],
            role_id=BRIEF_ROLE,
            input_hash=digest(context),
            style_version_id=None,
            asset_manifest_hash=digest(context),
            rulepack_id=RULEPACK_ID,
            rulepack_hash=rulepack_digest(),
            model_profile_id=profile.id,
            model_id=profile.model,
            schema_version="1.0",
            status="RUNNING",
            trace={
                "tool": "material_image_brief",
                "schema": BRIEF_CONTRACT.__name__,
                "budget": budget,
                "is_mock": profile.kind == "mock",
                "requirement_id": data["requirement_id"],
                # The owning item decides whether this row is still alive, so
                # an orphan left by a lost worker can be recognised later.
                "item_id": item_id,
                "worker_id": worker_id,
                "phase": "PREPARING",
            },
        )
        db.add(task)
        db.flush()
        task_id = task.id

    def record_progress(phase: str) -> None:
        with factory.begin() as db:
            row = db.get(AgentTaskRecord, task_id)
            if row is not None:
                row.trace = {**row.trace, "phase": phase, "phase_started_at": utcnow().isoformat()}

    outcome = call_material_agent(
        gateway,
        factory,
        BRIEF_ROLE,
        system=system_prompt,
        context=context,
        schema=schema,
        contract=BRIEF_CONTRACT,
        on_progress=record_progress,
        before_call=text_consent.guard(data["project_id"], data["document_id"], data["document_sha256"],
                                       BRIEF_ROLE, text_consent.fingerprint(profile), gateway)
        if data["document_id"] else None,
    )
    if not outcome.ok:
        if outcome.error_code == "GPU_BUSY_RETRY_OR_QUEUE":
            # The stage was busy, so nothing was sent. Put the item back in the
            # queue: failing it here turned one occupied GPU into a batch of
            # "unknown provider faults" that could not be retried.
            new_version = touch_item(factory, item_id, version, status=ITEM_QUEUED, error_code=None)
            return {"status": ITEM_QUEUED, "code": "GPU_BUSY_RETRY_OR_QUEUE",
                    "state_version": new_version, "brief": None, "ready": False}
        external_outcome = outcome.details.get("external_outcome", "UNKNOWN")
        uncertain = outcome.uncertain or external_outcome == "UNKNOWN"
        status = ITEM_NEEDS_REVIEW if uncertain else ITEM_FAILED
        with factory.begin() as db:
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
                             "automatic_replay": False}
        new_version = touch_item(factory, item_id, version, status=status, error_code=outcome.error_code)
        return {"status": status, "code": outcome.error_code, "state_version": new_version,
                "brief": None, "ready": False}

    brief = BRIEF_CONTRACT.model_validate(outcome.parsed)
    correction_used = outcome.correction_used
    payload = brief.model_dump(mode="json")
    with factory.begin() as db:
        artifact = AgentArtifact(
            project_id=data["project_id"],
            kind=BRIEF_CONTRACT.__name__,
            schema_version="1.0",
            payload_json=payload,
            sha256=digest(payload),
            producer_role=BRIEF_ROLE,
            source_task_id=task_id,
            input_revision_ids=[],
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
        row = db.get(AgentTaskRecord, task_id)
        row.status = "SUCCEEDED"
        row.trace = {**row.trace, "phase": "SUCCEEDED", "output_hash": artifact.sha256,
                     "schema_valid": True, "correction_count": 1 if correction_used else 0,
                     "schema_errors": outcome.schema_errors, **outcome.details}
        add_audit(db, "worker", "MATERIAL_BRIEF_PRODUCED", "material_item", item_id,
                  {"artifact_id": artifact_id, "correction_used": correction_used,
                   "budget": budget}, project_id=data["project_id"])

    item_payload = {**data["item_payload"], "brief": payload, "brief_artifact_id": artifact_id,
                    "brief_basis": data["brief_basis"]}
    new_version = touch_item(factory, item_id, version, status=ITEM_BRIEF_READY, error_code=None,
                             brief_artifact_id=artifact_id, payload=item_payload)
    return {"status": ITEM_BRIEF_READY, "code": None, "state_version": new_version,
            "brief": payload, "artifact_id": artifact_id, "correction_used": correction_used, "ready": True}


def reconcile_orphan_brief_tasks(factory) -> int:
    """Close brief task rows whose owning item has already moved on.

    A brief task row is written inside the owning item's own execution: the
    item goes to ``BRIEF_RUNNING``, the row is created, and the same path
    finishes it.  A worker killed between those writes leaves the row
    ``RUNNING`` forever.  Such a row counts as a live task for its provider
    profile, so it locks the text model configuration for good - one lost
    worker retired every model setting on the machine.

    The phase decides what the orphan means.  A phase that proves the request
    was never opened releases the row; a phase at or after the request leaves
    the outcome unknown, so the row becomes reviewable instead.
    """
    from sqlalchemy import select

    from shine.materialprep.agent_call import PRE_REQUEST_PHASES

    closed = 0
    with factory.begin() as db:
        rows = db.scalars(
            select(AgentTaskRecord).where(
                AgentTaskRecord.role_id == BRIEF_ROLE,
                AgentTaskRecord.status == "RUNNING",
            )
        ).all()
        for row in rows:
            trace = dict(row.trace or {})
            # The project-wide catalog call is owned by the API request, not
            # by an item's brief execution; the orphan sweep must not kill it.
            if trace.get("tool") == "entity_catalog":
                continue
            item_id = trace.get("item_id")
            requirement_id = trace.get("requirement_id")
            if item_id:
                item = db.get(MaterialItem, item_id)
                alive = item is not None and item.status == ITEM_BRIEF_RUNNING
            else:
                # A row from before the item was recorded: the requirement
                # still owns the work, so the row is alive while any of its
                # items is running its brief.
                items = db.scalars(
                    select(MaterialItem).where(MaterialItem.requirement_id == requirement_id)
                ).all()
                alive = any(item.status == ITEM_BRIEF_RUNNING for item in items)
            if alive:
                continue
            phase = trace.get("phase")
            never_sent = phase in PRE_REQUEST_PHASES
            row.status = "REJECTED" if never_sent else "NEEDS_REVIEW"
            row.error_code = "BRIEF_ORPHAN_WORKER_LOST"
            row.trace = {
                **trace,
                "phase": "FAILED" if never_sent else "NEEDS_REVIEW",
                "external_outcome": "NOT_SENT" if never_sent else "UNKNOWN",
                "reconciliation": {
                    "outcome": "NOT_SENT_PROVEN" if never_sent else "UNCONFIRMABLE",
                    "last_phase": phase,
                    "checked_at": utcnow().isoformat(),
                },
            }
            row.state_version += 1
            add_audit(db, "worker", "MATERIAL_BRIEF_ORPHAN_RECONCILED", "agent_task", row.id,
                      {"phase": phase, "never_sent": never_sent,
                       "external_calls": 0}, project_id=row.project_id)
            closed += 1
    return closed

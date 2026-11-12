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

CONSERVATIVE_CONTEXT_WINDOW = 1_000_000
"""The window assumed when the provider declares none.

Current 1M-context reasoning models are the budget baseline, so the estimate
starts from that class.  A provider that declares its own smaller window still
overrides this default and is recorded by name.
"""

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
        "composition_rules_sha256": evidence["composition_rules_sha256"],
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
) -> dict[str, Any]:
    """The server envelope the model receives.

    The identity is fixed here and is never read back from the answer: the
    model describes an appearance, it does not decide who the item is.
    """
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
            "Put a missing fact that affects the picture into questions instead of deciding it yourself.",
            "The appearance must visibly belong to the frozen style bible: carry its colour palette, "
            "lighting tone, lens and texture language, and the production genre, into the English prose. "
            "The style bible is written in the script language; translate its intent, never paste it.",
            "The picture's medium is fixed by film_style_instruction: apply it to every surface "
            "(cel-shaded flat fills and clean line art for animation; real skin and lens texture for "
            "live-action). A photoreal portrait for an animation film is a refused brief, not a style choice.",
            "Write the appearance as one flowing descriptive passage for a Qwen-Image-2.1 prompt: name the "
            "subject first, then its dress and build, then the surface materials and their textures. Use "
            "concrete nouns ('brushed brass', 'deep navy wool'), not bare colours or bare materials.",
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
    from sqlalchemy import select

    from shine.comfyops.models import AssetRequirement, PreProductionPlan
    from shine.models import StyleVersion

    with factory() as db:
        item = db.get(MaterialItem, item_id)
        if item is None:
            raise BriefError("MATERIAL_ITEM_NOT_FOUND", item_id=item_id)
        run = db.get(MaterialRun, item.run_id)
        requirement = db.get(AssetRequirement, item.requirement_id) if item.requirement_id else None
        plan = db.get(PreProductionPlan, run.plan_id) if run else None
        project = db.get(Project, item.project_id)
        requirement_payload = dict((requirement.payload or {}) if requirement else {})
        scene_keys = set(requirement_payload.get("scene_keys") or [])
        locked_facts: list[str] = []
        for scene in ((plan.payload or {}).get("scene_preparations") or []) if plan else []:
            if scene_keys and scene.get("scene_key") not in scene_keys:
                continue
            for fact in scene.get("locked_facts") or []:
                if fact not in locked_facts:
                    locked_facts.append(fact)
        # The frozen style bible is the director's look. A material brief that
        # never sees it can only invent a different film, which is exactly how a
        # reference still ends up not matching the production it belongs to.
        style = db.scalar(
            select(StyleVersion)
            .where(StyleVersion.project_id == item.project_id, StyleVersion.frozen_at.is_not(None))
            .order_by(StyleVersion.version.desc())
        )
        film_style = ""
        if style is not None:
            from shine.h3.film_type import film_type_sentence
            from shine.schemas import FilmType
            try:
                film_style = film_type_sentence(FilmType((style.snapshot or {}).get("film_type")))
            except ValueError:
                film_style = ""
        return {
            "style_bible": dict((style.snapshot or {}) if style else {}),
            "style_version": style.version if style else None,
            "film_style": film_style,
            "document_id": (plan.payload or {}).get("document_id") if plan else None,
            "document_sha256": (plan.payload or {}).get("script_hash") if plan else None,
            "item_id": item.id,
            "requirement_id": item.requirement_id,
            "project_id": item.project_id,
            "item_status": item.status,
            "item_payload": dict(item.payload or {}),
            "requirement_payload": requirement_payload,
            "visual_direction": dict((item.payload or {}).get("visual_direction") or {}),
            "locked_facts": locked_facts,
            "project_language": (project.language if project else "") or "",
            "existing_brief": (item.payload or {}).get("brief"),
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
    the version it owns.  A brief that already exists is never re-asked: the
    producer answered once, and a second answer would be a second opinion the
    user never requested.
    """
    data = _load_context(factory, item_id)
    if data["existing_brief"]:
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

    item_payload = {**data["item_payload"], "brief": payload, "brief_artifact_id": artifact_id}
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

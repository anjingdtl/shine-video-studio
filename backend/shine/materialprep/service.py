"""API-side material-preparation service.

Everything here writes durable rows and returns.  No renderer is contacted, no
model is called and no image is waited for inside an HTTP request: the resident
Worker owns execution (``shine.materialprep.execution``).

Two invariants drive the whole module:

* **Idempotency is stored, not remembered.**  A confirmation or a batch is found
  again by its key and request hash, so a double click, a second browser tab or a
  retried request returns the original row instead of queueing a second GPU run.
* **A new version never rewrites the old one.**  Re-confirming creates a new plan
  revision; regenerating creates a new item revision.  Old candidates keep their
  provenance and are marked stale rather than deleted, because the user may still
  want to pick one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from shine.comfy.workflow import graph_hash
from shine.comfyops.assets import require_rights
from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    PLAN_PURPOSE_PRODUCTION,
    AssetRequirement,
    CandidateAsset,
    PreProductionPlan,
)
from shine.materialprep import image_prompts
from shine.materialprep.contracts import MaterialConfirmation, ScriptPreparation
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from shine.materialprep.source_spans import known_span_ids, segment_document, spans_for_scene_key, unknown_span_ids
from shine.materialprep.states import (
    ATTEMPT_UNKNOWN,
    EXECUTION_MODE_REAL,
    ITEM_CANDIDATE_READY,
    ITEM_FAILED,
    ITEM_IN_FLIGHT,
    ITEM_NEEDS_REVIEW,
    ITEM_QUEUED,
    ITEM_TERMINAL,
    OUTCOME_UNKNOWN,
    PLAN_CONFIRMED,
    PLAN_DRAFT,
    PLAN_MATERIALS_READY,
    PLAN_NEEDS_CLARIFICATION,
    RUN_CANCEL_REQUESTED,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_NEEDS_REVIEW,
    RUN_PARTIAL_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_TERMINAL,
)
from shine.models import Project, ProjectDocument, utcnow
from shine.services import add_audit, require_project

RESUMABLE_ITEM_STATES = frozenset({ITEM_FAILED})
"""What a user may resume.

``NEEDS_REVIEW`` is deliberately absent: an unknown external submission is
reconciled, never resumed into a second submission.
"""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def confirmation_id_for(project_id: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{project_id}:{_canonical(payload)}".encode()).hexdigest()


# --- plan revisions --------------------------------------------------------


def _validated_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """Enforce the published ScriptPreparation contract at the boundary.

    The scene answer is what the whole requirement list is built from. An
    unknown family or an extra field would otherwise be carried into the plan
    payload and from there into an image prompt, so the refusal happens here
    with a name the caller can act on.
    """
    try:
        prepared = ScriptPreparation.model_validate(scene)
    except ValidationError as exc:
        raise HTTPException(
            422,
            {"code": "MATERIAL_SCENE_PREPARATION_INVALID", "detail": str(exc)[:400]},
        ) from exc
    return prepared.model_dump(mode="json")


def aggregate_requirements(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-scene preparations into one identity-keyed requirement list.

    The server assigns the identity, never the model. A name that appears under
    two different descriptions is reported as ambiguous for the user to resolve
    instead of being merged by string equality.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for scene in scenes:
        scene_key = scene.get("scene_key")
        for requirement in scene.get("requirements") or []:
            family = requirement.get("family")
            name = (requirement.get("name") or "").strip()
            if not family or not name:
                continue
            key = (family, name)
            description = (requirement.get("description") or "").strip()
            entry = merged.setdefault(
                key,
                {"family": family, "entity_id": name, "descriptions": [], "scene_keys": [], "questions": [],
                 "carriers": [], "critical": requirement.get("critical", True),
                 "optional": requirement.get("optional", not requirement.get("critical", True)),
                 "variants": []},
            )
            if requirement.get("critical") is False:
                # One source that says "optional" makes the merged need
                # optional: a prop plate is never promoted to a hard gate by
                # a second scene that merely mentions it.
                entry["critical"] = False
                entry["optional"] = True
            for variant in requirement.get("variants") or []:
                if variant and variant not in entry["variants"]:
                    entry["variants"].append(variant)
            if description and description not in entry["descriptions"]:
                entry["descriptions"].append(description)
            if scene_key and scene_key not in entry["scene_keys"]:
                entry["scene_keys"].append(scene_key)
            for question in requirement.get("questions") or []:
                if question not in entry["questions"]:
                    entry["questions"].append(question)
            carrier = requirement.get("carrier")
            if family == "prop" and carrier and carrier not in entry["carriers"]:
                entry["carriers"].append(carrier)
            reason = (requirement.get("core_prop_reason") or "").strip()
            if family == "prop" and reason and requirement.get("source_span_ids"):
                entry.setdefault("core_prop_reason", reason)
    for (family, name), entry in merged.items():
        if len(entry["descriptions"]) > 1:
            conflicts.append({"family": family, "entity_id": name, "descriptions": entry["descriptions"],
                              "code": "MATERIAL_IDENTITY_AMBIGUOUS"})
    open_questions = [
        question
        for scene in scenes
        for question in scene.get("questions") or []
    ]
    return {
        "requirements": [
            {
                "family": family,
                "entity_id": name,
                "description": entry["descriptions"][0] if entry["descriptions"] else "",
                # The first cited carrier is the primary one; a prop cited with
                # different carriers across scenes keeps every variant as a
                # display note, never a hard conflict (a letter can be sealed
                # in one scene and open in the next).
                "carrier": entry["carriers"][0] if entry["carriers"] else ("object" if family == "prop" else None),
                "carrier_variants": entry["carriers"] if len(entry["carriers"]) > 1 else [],
                "scene_keys": entry["scene_keys"],
                "questions": entry["questions"],
                "critical": entry["critical"],
                "optional": entry["optional"],
                **({"variants": entry["variants"]} if entry["variants"] else {}),
                **({"core_prop_reason": entry["core_prop_reason"]} if entry.get("core_prop_reason") else {}),
            }
            for (family, name), entry in sorted(merged.items())
        ],
        "conflicts": conflicts,
        "open_questions": open_questions,
    }


def _persist_requirements(
    db, project_id: str, plan_id: str, requirements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Give every aggregated requirement a row and a stable id.

    The id is what the director page shows, what the confirmation quotes and
    what the generation batch references, so it has to exist before the user
    confirms anything. Without it the journey stops at "confirm the list": the
    page has nothing to send back.
    """
    stored: list[dict[str, Any]] = []
    for requirement in requirements:
        row = AssetRequirement(
            project_id=project_id,
            plan_id=plan_id,
            payload={
                **requirement,
                "critical": True,
                "strategy": None,
                "bound_asset_id": None,
                "brief_note": "",
                "source_plan_revision_id": plan_id,
            },
            sha256=graph_hash(requirement),
            status="PROPOSED",
        )
        db.add(row)
        db.flush()
        stored.append({**requirement, "requirement_id": row.id})
    return stored


def create_draft_plan(
    db,
    project: Project,
    document: ProjectDocument,
    preparations: list[dict[str, Any]],
    *,
    actor: str = "local-user",
) -> tuple[PreProductionPlan, bool]:
    """Open (or reuse) the material-preparation plan revision for this analysis.

    The same script analysed twice must not produce two draft revisions. The
    newer one would become the current material plan and silently drop the
    decisions the user already confirmed, so an identical analysis reuses the
    revision that is already current. The second value says whether it was a
    reuse.
    """
    if document.project_id != project.id:
        raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    scenes = [_validated_scene(scene) for scene in preparations]
    # The server owns the segmentation and the citation catalogue.  A scene
    # answer that cites a fragment the server never handed out is refused by
    # name: a silently dropped citation turns a fact into an uncited one.
    document_scenes = segment_document(document.text or "")
    known_spans = known_span_ids(document_scenes)
    known_scene_keys = {scene.key for scene in document_scenes}
    for preparation in scenes:
        scene_key = preparation.get("scene_key")
        scene_spans = spans_for_scene_key(document_scenes, scene_key)
        if scene_spans is None:
            raise HTTPException(
                422,
                {"code": "MATERIAL_SCENE_KEY_UNKNOWN", "scene_key": scene_key,
                 "known_scene_keys": sorted(known_scene_keys)},
            )
        invented = unknown_span_ids(preparation, scene_spans)
        if invented:
            raise HTTPException(
                422,
                {"code": "MATERIAL_SOURCE_SPAN_UNKNOWN", "span_ids": invented,
                 "scene_key": scene_key},
            )
    request_hash = graph_hash({
        "document_id": document.id,
        "script_hash": document.sha256,
        "scenes": scenes,
    })
    from shine.comfyops.preproduction import current_material_plan

    current = current_material_plan(db, project.id)
    if current is not None and (current.payload or {}).get("request_hash") == request_hash:
        # 同一个分析可以复用，但前提是这一版真的包含本次要派生的全部实体。
        # 旧草稿只覆盖子集时（例如某集当时还没落镜）复用它，新场次的需求就
        # 永远不会出现；此时必须新建一版，而不是让清单静默缺项。
        wanted = {
            (requirement.get("family"), requirement.get("name"))
            for scene in scenes
            for requirement in scene.get("requirements") or []
        }
        stored = {
            ((row.payload or {}).get("family"), (row.payload or {}).get("entity_id"))
            for row in db.scalars(select(AssetRequirement).where(
                AssetRequirement.plan_id == current.id,
                AssetRequirement.project_id == project.id,
            ))
        }
        if wanted.issubset(stored):
            return current, True
    aggregated = aggregate_requirements(scenes)
    draft_payload = {
        "purpose": PLAN_PURPOSE_ASSET_PREPARATION,
        "document_id": document.id,
        "script_hash": document.sha256,
        "request_hash": request_hash,
        "scene_preparations": scenes,
        "source_scenes": [scene.key for scene in document_scenes],
        "source_span_ids": sorted(known_spans),
        "requirements": [],
        "conflicts": aggregated["conflicts"],
        "visual_direction": None,
        "confirmation": None,
        "manifest": None,
    }
    plan = PreProductionPlan(
        project_id=project.id,
        payload=draft_payload,
        sha256=graph_hash(draft_payload),
        status=PLAN_DRAFT,
        purpose=PLAN_PURPOSE_ASSET_PREPARATION,
    )
    db.add(plan)
    db.flush()
    payload = {
        **draft_payload,
        "requirements": _persist_requirements(db, project.id, plan.id, aggregated["requirements"]),
    }
    plan.payload = payload
    plan.sha256 = graph_hash(payload)
    db.flush()
    add_audit(db, actor, "MATERIAL_PLAN_DRAFTED", "preproduction_plan", plan.id,
              {"scenes": len(scenes), "requirements": len(payload["requirements"])}, project_id=project.id)
    return plan, False


def aggregated_requirements(db, plan: PreProductionPlan) -> dict[str, Any]:
    """The stored requirement list of one plan revision, with its identities.

    The ids come from the ledger, so the page can quote exactly what the server
    holds. The conflicts and open questions are recomputed from the scene
    answers, which are the facts they were derived from.
    """
    payload = plan.payload or {}
    requirements = payload.get("requirements") or []
    recomputed = aggregate_requirements(payload.get("scene_preparations") or [])
    conflicts = payload.get("conflicts")
    if conflicts is None:
        conflicts = recomputed["conflicts"]
    return {
        "requirements": requirements,
        "conflicts": conflicts,
        "open_questions": recomputed["open_questions"],
    }


def confirmation_snapshot(plan: PreProductionPlan) -> dict[str, Any]:
    """The exact state a confirmation is allowed to be made against."""
    payload = plan.payload or {}
    return {
        "requirements": payload.get("requirements") or [],
        "visual_direction": payload.get("visual_direction"),
        "scene_preparations": payload.get("scene_preparations") or [],
    }


def confirmation_snapshot_hash(plan: PreProductionPlan) -> str:
    """The hash the client quotes back in a confirmation.

    The server publishes it. A client that had to recompute it would depend on
    this module's private canonicalisation, and a contract nobody can compute
    is a contract everybody fails.
    """
    return graph_hash(confirmation_snapshot(plan))


def confirm(db, actor: str, project_id: str, body: MaterialConfirmation, *,
            commit: bool = True) -> dict[str, Any]:
    """Record the human confirmation as a NEW immutable plan revision.

    ``commit=False`` lets a caller fold this confirmation, the generation batch
    and the commit receipt into one SQLite transaction: an interruption before
    that single commit leaves nothing behind, so the same request can be
    resubmitted cleanly instead of hitting a half-applied ``MATERIAL_PLAN_STALE``.
    """
    require_project(db, project_id)
    source = db.get(PreProductionPlan, body.plan_revision_id)
    if source is None or source.project_id != project_id:
        raise HTTPException(404, "MATERIAL_PLAN_NOT_FOUND")
    if source.purpose != PLAN_PURPOSE_ASSET_PREPARATION:
        raise HTTPException(409, "PREPRODUCTION_PLAN_PURPOSE_MISMATCH")

    actual = confirmation_snapshot_hash(source)
    if actual != body.expected_snapshot:
        # The list the user read is not the list the server holds now.
        raise HTTPException(409, {"code": "MATERIAL_PLAN_STALE", "actual_snapshot": actual})

    aggregated = aggregated_requirements(db, source)
    # Several descriptions of one name used to block the confirmation outright.
    # That is the normal shape of a script: the same person is introduced one
    # way in the first scene ("a pale old man in a white robe") and another way
    # later ("the minister's assistant who brings the coin"). Refusing made the
    # list impossible to confirm, so no material was ever produced for a real
    # script. The variants stay on the requirement for the person to read and
    # adjust; they are no longer a gate.
    identity_notes = list(aggregated["conflicts"])

    known = {(r["family"], r["entity_id"]) for r in aggregated["requirements"]}
    decisions: list[dict[str, Any]] = []
    canonical_selections = {}
    for decision in body.decisions:
        row = db.get(AssetRequirement, decision.requirement_id)
        if (row is not None and row.plan_id == source.id and row.payload.get("family") == "character"
                and not row.payload.get("canonical_entity_id") and decision.asset_id):
            canonical_selections[row.payload["entity_id"]] = decision.asset_id

    for decision in body.decisions:
        row = db.get(AssetRequirement, decision.requirement_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(404, "MATERIAL_REQUIREMENT_NOT_FOUND")
        if row.plan_id != source.id:
            # The decision belongs to another revision of this plan.
            raise HTTPException(409, {"code": "MATERIAL_PLAN_STALE", "requirement_id": row.id})
        requirement_payload = row.payload or {}
        if (requirement_payload.get("family"), requirement_payload.get("entity_id")) not in known:
            raise HTTPException(409, "MATERIAL_PLAN_STALE")
        from shine.models import Asset

        if decision.strategy == "omit_prop" and (requirement_payload.get("family") != "prop" or decision.asset_id):
            raise HTTPException(422, "MATERIAL_OMIT_PROP_ONLY")
        if decision.strategy == "skip" and (requirement_payload.get("family") == "character" or decision.asset_id):
            raise HTTPException(422, "MATERIAL_SKIP_OPTIONAL_ONLY")
        canonical_entity = requirement_payload.get("canonical_entity_id")
        selected_id = canonical_selections.get(canonical_entity, decision.asset_id)
        selected = db.get(Asset, selected_id) if selected_id else None
        if selected_id and (selected is None or selected.project_id != project_id
                            or selected.role != requirement_payload.get("family")
                            or selected.entity_id != (canonical_entity or requirement_payload.get("entity_id"))):
            raise HTTPException(409, "ASSET_IDENTITY_MISMATCH")
        if decision.strategy == "reuse_master" and selected is None:
            raise HTTPException(422, "MATERIAL_EXPLICIT_ASSET_REQUIRED")
        decisions.append({
            "bound_asset_sha256": selected.sha256 if selected else None,
            "requirement_id": row.id,
            "family": requirement_payload.get("family"),
            "entity_id": requirement_payload.get("entity_id"),
            "strategy": decision.strategy,
            "asset_id": selected_id,
            "brief_note": decision.brief_note,
        })

    confirmation_payload = {
        "decisions": decisions,
        "visual_direction": body.visual_direction.model_dump(mode="json"),
        "notes": body.notes,
        "confirmed_by": actor,
    }
    confirmation_id = confirmation_id_for(project_id, confirmation_payload)
    payload = {
        **source.payload,
        "requirements": aggregated["requirements"],
        "visual_direction": body.visual_direction.model_dump(mode="json"),
        "confirmation": {**confirmation_payload, "confirmation_id": confirmation_id,
                         "confirmation_hash": graph_hash(confirmation_payload),
                         "source_plan_revision_id": source.id},
        "manifest": None,
    }
    plan = PreProductionPlan(
        project_id=project_id,
        payload=payload,
        sha256=graph_hash(payload),
        status=PLAN_CONFIRMED,
        purpose=PLAN_PURPOSE_ASSET_PREPARATION,
    )
    db.add(plan)
    db.flush()
    for decision in decisions:
        row = db.get(AssetRequirement, decision["requirement_id"])
        # The requirement keeps its identity across the revision: the page
        # quotes the id it was shown and the batch references the same id.
        # Only its owning revision and its decision move forward.
        row.plan_id = plan.id
        previous = dict(row.payload or {})
        # 显式取消采用：从「复用」改走生成/上传/跳过时，记录被释放的旧主素材，
        # 由 bind 执行者解绑；从未采纳过的 generate 不释放任何已有参考。
        release = (previous.get("bound_asset_id")
                   if previous.get("strategy") == "reuse_master"
                   and decision["strategy"] != "reuse_master" else None)
        row.payload = {
            **(row.payload or {}),
            "strategy": decision["strategy"],
            "critical": decision["strategy"] not in {"omit_prop", "skip"},
            "bound_asset_id": decision["asset_id"],
            "bound_asset_sha256": decision["bound_asset_sha256"],
            "brief_note": decision["brief_note"],
            "source_plan_revision_id": source.id,
            "confirmed_by": actor,
            "release_asset_id": release,
        }
        row.sha256 = graph_hash(row.payload)
        row.status = "CONFIRMED"
    db.flush()
    # A partial commit (one upload, one regenerate) decides a single row, but the
    # requirements page reads the *current* plan's rows.  If the undecided rows
    # stayed on the old revision the list would collapse to just the decided row.
    # Every requirement therefore follows the new revision; the undecided ones
    # keep their identity, payload and decision unchanged.
    for row in db.scalars(select(AssetRequirement).where(
            AssetRequirement.plan_id == source.id,
            AssetRequirement.project_id == project_id)).all():
        row.plan_id = plan.id
    db.flush()
    for asset_id in set(canonical_selections.values()):
        selected = db.get(Asset, asset_id)
        if selected and selected.approved and selected.locked and not (selected.provenance or {}).get("offline_only"):
            retire_unused_masters(db, plan, selected, actor, entities={selected.entity_id})
    # Confirming a master IS adopting it into the shots that need it: the one
    # executor every confirmation path runs, so the material list, the journey
    # and the package readiness read the same shot.bindings fact.
    bound_shots = bind_adopted_masters_to_shots(db, project_id, plan.id, actor)
    add_audit(db, actor, "MATERIAL_PLAN_CONFIRMED", "preproduction_plan", plan.id,
              {"confirmation_id": confirmation_id, "decisions": len(decisions),
               "generate": sum(1 for d in decisions if d["strategy"] == "generate"),
               "bound_shots": bound_shots},
              project_id=project_id)
    if commit:
        db.commit()
    return {"plan_revision_id": plan.id, "confirmation_id": confirmation_id,
            "confirmation_hash": graph_hash(confirmation_payload), "decisions": decisions,
            "identity_notes": identity_notes, "bound_shots": bound_shots,
            "status": plan.status}


# --- batches ---------------------------------------------------------------


def _next_generation_revision(db, project_id: str, requirement_id: str) -> int:
    highest = db.scalar(
        select(func.max(MaterialItem.generation_revision)).where(
            MaterialItem.project_id == project_id, MaterialItem.requirement_id == requirement_id
        )
    )
    return int(highest or 0) + 1


def create_batch(
    db,
    project_id: str,
    *,
    confirmation_id: str,
    confirmation_hash: str,
    requirement_ids: list[str],
    idempotency_key: str,
    rights_id: str | None,
    kind: str = "production",
    seeds: dict[str, int] | None = None,
    briefs: dict[str, dict[str, Any]] | None = None,
    new_revision: bool = False,
    trust_current_list: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    """Create the durable batch and its items, then return.  Never renders.

    ``new_revision`` is False for an ordinary generate, which always targets
    revision 1 and is therefore refused when an item already exists.  Only the
    explicit regenerate route passes True, because silently bumping a revision
    would let a second browser tab queue a second GPU run for one confirmation.

    ``commit=False`` joins the caller's transaction (the director commit folds
    confirmation + batch + receipt into one atomic submit).
    """
    require_project(db, project_id)
    plan = _plan_for_confirmation(db, project_id, confirmation_id)
    if not trust_current_list and plan.payload["confirmation"]["confirmation_hash"] != confirmation_hash:
        raise HTTPException(409, "MATERIAL_PLAN_STALE")

    request_payload = {
        "confirmation_id": confirmation_id,
        "confirmation_hash": confirmation_hash,
        "requirement_ids": sorted(requirement_ids),
        "rights_id": rights_id,
        "kind": kind,
        # The brief is part of the request identity: the same idempotency key with
        # a different appearance description must conflict, not silently reuse the
        # first batch's image.
        "briefs": {key: briefs[key] for key in sorted(briefs or {})},
        "new_revision": new_revision,
    }
    request_hash = graph_hash(request_payload)

    existing = db.scalar(
        select(MaterialRun).where(
            MaterialRun.project_id == project_id, MaterialRun.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_CONFLICT", "run_id": existing.id})
        return {"run_id": existing.id, "status": existing.status, "replayed": True,
                "items": [public_item(db, i) for i in _items(db, existing.id)]}

    # The licence is checked at the point where a batch is really created. A
    # batch spends a GPU render, and a render that may not become a candidate is
    # a wasted render, so an unapproved licence must not reach the renderer.
    # A replayed request creates nothing and therefore keeps its original
    # answer: the same key with the same request hash returns the original run.
    if rights_id:
        require_rights(db, project_id, rights_id)

    generate_ids = _select_generable(db, plan, requirement_ids)
    if new_revision:
        # Fence allocation before computing the next revision. A second tab
        # cannot turn a duplicate click into another legitimate generation.
        from sqlalchemy import update
        db.execute(update(PreProductionPlan).where(PreProductionPlan.id == plan.id).values(sha256=plan.sha256))
        busy = db.scalar(select(MaterialItem).where(
            MaterialItem.project_id == project_id, MaterialItem.requirement_id.in_(generate_ids),
            MaterialItem.status.in_([*ITEM_IN_FLIGHT, ITEM_NEEDS_REVIEW, ITEM_QUEUED]),
        ))
        if busy is not None:
            raise HTTPException(409, {"code": "MATERIAL_REVISION_IN_FLIGHT", "item_id": busy.id,
                                      "status": busy.status})
    run = MaterialRun(
        project_id=project_id,
        plan_id=plan.id,
        confirmation_id=confirmation_id,
        confirmation_hash=confirmation_hash,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        mode=EXECUTION_MODE_REAL,
        status=RUN_QUEUED,
        payload={"kind": kind, "rights_id": rights_id, "requirement_ids": generate_ids,
                 "confirmation_snapshot": confirmation_hash},
        sha256=graph_hash(request_payload),
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(
            select(MaterialRun).where(
                MaterialRun.project_id == project_id, MaterialRun.idempotency_key == idempotency_key
            )
        )
        if raced is not None and raced.request_hash == request_hash:
            return {"run_id": raced.id, "status": raced.status, "replayed": True,
                    "items": [public_item(db, i) for i in _items(db, raced.id)]}
        raise HTTPException(409, "MATERIAL_BATCH_CONFLICT") from exc

    created, refused = [], []
    for requirement_id in generate_ids:
        revision = _next_generation_revision(db, project_id, requirement_id) if new_revision else 1
        requirement = db.get(AssetRequirement, requirement_id)
        seed = int((seeds or {}).get(requirement_id, 0))
        brief = (briefs or {}).get(requirement_id)
        item = MaterialItem(
            run_id=run.id,
            project_id=project_id,
            requirement_id=requirement_id,
            generation_revision=revision,
            status=ITEM_QUEUED,
            payload={
                "family": (requirement.payload or {}).get("family"),
                "entity_id": (requirement.payload or {}).get("entity_id"),
                "carrier": (requirement.payload or {}).get("carrier"),
                "brief_note": (requirement.payload or {}).get("brief_note") or "",
                "visual_direction": plan.payload.get("visual_direction") or {},
                "seed": seed,
                "kind": kind,
                # A batch without a brief is queued but cannot render yet: the
                # producer owns the appearance text, and the server refuses to
                # invent one rather than rendering an unconfirmed look.
                **({"brief": brief} if brief else {}),
            },
            sha256=graph_hash({"requirement_id": requirement_id, "revision": revision, "seed": seed}),
        )
        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
        except IntegrityError:
            # Another tab already queued this exact requirement revision.
            refused.append({"requirement_id": requirement_id, "code": "MATERIAL_ITEM_ALREADY_QUEUED"})
            continue
        created.append(item.id)

    if not created:
        db.rollback()
        raise HTTPException(409, {"code": "MATERIAL_NOTHING_TO_GENERATE", "refused": refused})
    run.payload = {**run.payload, "item_ids": created, "refused": refused}
    run.sha256 = graph_hash(run.payload)
    add_audit(db, "local-user", "MATERIAL_BATCH_CREATED", "material_run", run.id,
              {"items": len(created), "refused": len(refused), "kind": kind, "mode": EXECUTION_MODE_REAL},
              project_id=project_id)
    if commit:
        db.commit()
    return {"run_id": run.id, "status": run.status, "replayed": False, "items": created, "refused": refused}


VALIDATION_BRIEFS: dict[str, dict[str, Any]] = {
    "character": {
        "appearance": (
            "a thirty-year-old woman with short black hair, wearing a plain grey wool coat, "
            "standing calmly and facing the camera"
        ),
        "must_keep": ["complete head inside the frame", "clear unobstructed face"],
        "avoid": ["second person", "cropped head"],
    },
    "scene": {
        "appearance": (
            "a rain-wet harbour pier at dusk with wooden bollards and low mist over the water"
        ),
        "must_keep": ["no people present"],
        "avoid": ["any human figure", "silhouette"],
    },
    "prop": {
        "appearance": "a brass pocket watch with an engraved cover, lying open on a flat surface",
        "must_keep": ["the whole object inside the frame"],
        "avoid": ["hand", "person holding it"],
    },
}
"""Fixed sample briefs for the three readiness probes.

They are deliberately ordinary so the sample tests the runtime rather than the
prompt, and deliberately not a real project's material: a validation sample is
capability evidence, never a candidate the user should promote.
"""


def create_validation_batch(
    db,
    project_id: str,
    *,
    families: list[str],
    idempotency_key: str,
    rights_id: str | None,
) -> dict[str, Any]:
    """Queue one real small-sample render per family through the same Worker path."""
    require_project(db, project_id)
    if rights_id:
        require_rights(db, project_id, rights_id)
    request_payload = {
        "kind": "validation",
        "families": sorted(families),
        "rights_id": rights_id,
        "confirmation_id": "validation",
        # Readiness evidence is bound to the prompt template; a template bump
        # must never replay an older validation batch as if it proved the new
        # composition rules.
        "prompt_template_version": image_prompts.template_evidence()["template_version"],
    }
    request_hash = graph_hash(request_payload)
    existing = db.scalar(
        select(MaterialRun).where(
            MaterialRun.project_id == project_id, MaterialRun.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_CONFLICT", "run_id": existing.id})
        return {"run_id": existing.id, "status": existing.status, "replayed": True,
                "items": [public_item(db, item) for item in _items(db, existing.id)]}

    plan = _validation_plan(db, project_id)
    run = MaterialRun(
        project_id=project_id,
        plan_id=plan.id,
        confirmation_id="validation",
        confirmation_hash=request_hash,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        mode=EXECUTION_MODE_REAL,
        status=RUN_QUEUED,
        payload={"kind": "validation", "rights_id": rights_id, "families": sorted(families)},
        sha256=graph_hash(request_payload),
    )
    db.add(run)
    db.flush()
    created = []
    for index, family in enumerate(sorted(families)):
        brief = dict(VALIDATION_BRIEFS[family])
        item = MaterialItem(
            run_id=run.id,
            project_id=project_id,
            requirement_id=None,
            generation_revision=1,
            status=ITEM_QUEUED,
            payload={
                "family": family,
                "entity_id": f"validation-{family}",
                "brief": {**brief, "schema_version": "1.0", "questions": []},
                "visual_direction": {},
                "seed": 1000 + index,
                "kind": "validation",
            },
            sha256=graph_hash({"family": family, "seed": 1000 + index}),
        )
        db.add(item)
        db.flush()
        created.append(item.id)
    run.payload = {**run.payload, "item_ids": created}
    run.sha256 = graph_hash(run.payload)
    add_audit(db, "local-user", "IMAGE_VALIDATION_BATCH_CREATED", "material_run", run.id,
              {"families": sorted(families), "mode": EXECUTION_MODE_REAL}, project_id=project_id)
    db.commit()
    return {"run_id": run.id, "status": run.status, "replayed": False, "items": created, "refused": []}


def _validation_plan(db, project_id: str) -> PreProductionPlan:
    """The asset-preparation plan a validation batch hangs off.

    A validation run still needs a plan row for provenance, and it must be an
    ``asset_preparation`` one: a setup probe must never create or become a
    production contract.
    """
    from shine.comfyops.preproduction import current_material_plan

    plan = current_material_plan(db, project_id)
    if plan is not None:
        return plan
    payload = {
        "purpose": PLAN_PURPOSE_ASSET_PREPARATION,
        "document_id": None,
        "script_hash": None,
        "scene_preparations": [],
        "requirements": [],
        "visual_direction": None,
        "confirmation": {"confirmation_id": "validation", "confirmation_hash": ""},
        "manifest": None,
        "validation_only": True,
    }
    plan = PreProductionPlan(
        project_id=project_id,
        payload=payload,
        sha256=graph_hash(payload),
        status=PLAN_DRAFT,
        purpose=PLAN_PURPOSE_ASSET_PREPARATION,
    )
    db.add(plan)
    db.flush()
    return plan


def _plan_for_confirmation(db, project_id: str, confirmation_id: str) -> PreProductionPlan:
    """The newest plan carrying this confirmation.

    A confirmation can survive several plan revisions (each re-confirmation
    writes a new one), and the requirement rows always belong to the newest.
    Returning the oldest made `_select_generable` reject every row with a bare
    MATERIAL_PLAN_STALE, which is what broke "rerun every material".
    """
    for plan in db.scalars(
        select(PreProductionPlan).where(
            PreProductionPlan.project_id == project_id,
            PreProductionPlan.purpose == PLAN_PURPOSE_ASSET_PREPARATION,
        ).order_by(PreProductionPlan.created_at.desc())
    ).all():
        confirmation = (plan.payload or {}).get("confirmation") or {}
        if confirmation.get("confirmation_id") == confirmation_id:
            return plan
    raise HTTPException(404, "MATERIAL_CONFIRMATION_NOT_FOUND")


def _select_generable(db, plan: PreProductionPlan, requirement_ids: list[str]) -> list[str]:
    """Only currently-confirmed ``generate`` items may be queued.

    An item the user marked ``upload`` is a file on its way, not a missing
    material; generating one would duplicate an upload the user already made.
    """
    selected: list[str] = []
    for requirement_id in requirement_ids:
        row = db.get(AssetRequirement, requirement_id)
        if row is None or row.project_id != plan.project_id or row.plan_id != plan.id:
            raise HTTPException(409, "MATERIAL_PLAN_STALE")
        strategy = (row.payload or {}).get("strategy")
        if strategy != "generate":
            raise HTTPException(409, {"code": "MATERIAL_CONFIRMATION_REQUIRED",
                                      "requirement_id": requirement_id, "strategy": strategy})
        selected.append(requirement_id)
    return sorted(set(selected))


# --- recovery actions ------------------------------------------------------


def _items(db, run_id: str) -> list[MaterialItem]:
    return list(db.scalars(select(MaterialItem).where(MaterialItem.run_id == run_id)).all())


def _control_receipt(db, action, run_id, version, idempotency_key, item_ids=None):
    from sqlalchemy import text

    from shine.models import AppSetting

    # One SQLite writer serialises operator controls across processes too.
    if not db.connection().connection.driver_connection.in_transaction:
        db.execute(text("BEGIN IMMEDIATE"))
    db.flush()
    db.expire_all()
    key = "mat-ctrl:" + graph_hash({"action": action, "run_id": run_id, "key": idempotency_key})
    request_hash = graph_hash({"action": action, "run_id": run_id, "version": version, "items": item_ids or []})
    receipt = db.get(AppSetting, key) if idempotency_key else None
    if receipt and receipt.value["request_hash"] != request_hash:
        raise HTTPException(409, "IDEMPOTENCY_KEY_CONFLICT")
    return key if idempotency_key else None, request_hash, receipt.value["result"] if receipt else None


def _save_control(db, key, request_hash, result):
    from shine.models import AppSetting
    if key:
        db.add(AppSetting(key=key, value={"request_hash": request_hash, "result": result}))
    db.commit()
    return result


def request_cancel(db, run_id: str, expected_state_version: int, *, idempotency_key="") -> dict[str, Any]:
    """Ask for a cancel.  Requesting one is not the same as having stopped."""
    key, request_hash, prior = _control_receipt(db, "cancel", run_id, expected_state_version, idempotency_key)
    if prior is not None:
        return prior
    run = _require_run(db, run_id)
    if run.state_version != expected_state_version:
        raise HTTPException(409, {"code": "MATERIAL_RUN_STATE_STALE", "state_version": run.state_version})
    if run.status in RUN_TERMINAL:
        return {"run_id": run.id, "status": run.status, "cancel_requested": run.cancel_requested,
                "note": "RUN_ALREADY_FINISHED"}
    stopped = 0
    for item in _items(db, run.id):
        if item.status in ITEM_TERMINAL:
            continue
        if item.status == ITEM_QUEUED:
            item.status = "CANCELLED"
            item.error_code = "RUN_CANCELLED"
            item.state_version += 1
            stopped += 1
    run.cancel_requested = True
    run.status = RUN_CANCEL_REQUESTED
    run.state_version += 1
    add_audit(db, "local-user", "MATERIAL_BATCH_CANCEL_REQUESTED", "material_run", run.id,
              {"stopped_not_started": stopped}, project_id=run.project_id)
    result = {"run_id": run.id, "status": run.status, "cancel_requested": True,
            "stopped_not_started": stopped,
            "note": "已停止未开始的项；正在执行的项由 Worker 收尾后核对，不会被全局打断。"}
    return _save_control(db, key, request_hash, result)


def resume_items(db, run_id: str, item_ids: list[str], expected_state_version: int,
                 *, idempotency_key="") -> dict[str, Any]:
    """Re-queue items that definitely failed.  Unknown outcomes are refused."""
    key, request_hash, prior = _control_receipt(db, "resume", run_id, expected_state_version, idempotency_key, item_ids)
    if prior is not None:
        return prior
    run = _require_run(db, run_id)
    if run.state_version != expected_state_version:
        raise HTTPException(409, {"code": "MATERIAL_RUN_STATE_STALE", "state_version": run.state_version})
    queued, refused = [], []
    for item_id in item_ids:
        item = db.get(MaterialItem, item_id)
        if item is None or item.run_id != run.id:
            refused.append({"item_id": item_id, "code": "MATERIAL_ITEM_NOT_FOUND"})
            continue
        if item.status in RESUMABLE_ITEM_STATES:
            unknown = db.scalar(
                select(MaterialImageAttempt).where(
                    MaterialImageAttempt.item_id == item.id,
                    MaterialImageAttempt.external_outcome == OUTCOME_UNKNOWN,
                )
            )
            if unknown is not None:
                refused.append({"item_id": item_id, "code": "IMAGE_SUBMISSION_OUTCOME_UNKNOWN",
                                "remediation": "先对账该未知提交；未知结果不能重新提交。"})
                continue
            item.status = ITEM_QUEUED
            item.state_version += 1
            item.error_code = None
            queued.append(item_id)
        else:
            refused.append({"item_id": item_id, "code": "MATERIAL_ITEM_NOT_RESUMABLE", "status": item.status})
    if queued:
        if run.status in RUN_TERMINAL or run.status == RUN_CANCEL_REQUESTED:
            run.status = RUN_QUEUED
            run.cancel_requested = False
        run.state_version += 1
    add_audit(db, "local-user", "MATERIAL_BATCH_RESUMED", "material_run", run.id,
              {"queued": len(queued), "refused": len(refused)}, project_id=run.project_id)
    return _save_control(db, key, request_hash,
                         {"run_id": run.id, "queued": queued, "refused": refused, "status": run.status})


def reconcile_run(db, run_id: str, expected_state_version: int, *, idempotency_key="",
                 item_ids: list[str] | None = None) -> dict[str, Any]:
    """Record what is already known.  This never contacts a renderer to resubmit.

    The operator may reconcile one item of a batch.  Without that, a single
    "check this one" click would silently widen to every unknown item of the
    run, which is more than the action promised.
    """
    key, request_hash, prior = _control_receipt(db, "reconcile", run_id, expected_state_version,
                                                idempotency_key, item_ids)
    if prior is not None:
        return prior
    run = _require_run(db, run_id)
    if run.state_version != expected_state_version:
        raise HTTPException(409, {"code": "MATERIAL_RUN_STATE_STALE", "state_version": run.state_version})
    unknown = []
    wanted = set(item_ids or [])
    for item in _items(db, run.id):
        if wanted and item.id not in wanted:
            continue
        attempts = db.scalars(
            select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item.id)
        ).all()
        for attempt in attempts:
            if attempt.external_outcome == OUTCOME_UNKNOWN or attempt.status == ATTEMPT_UNKNOWN:
                unknown.append({"item_id": item.id, "attempt_id": attempt.id,
                                "prompt_id": attempt.prompt_id,
                                "submit_intent_at": attempt.submit_intent_at.isoformat()
                                if attempt.submit_intent_at else None,
                                "graph_sha256": attempt.graph_sha256,
                                "reconcilable": bool(attempt.prompt_id),
                                "note": ("可用 prompt_id 查询外部队列/历史后采用；查不到不等于未提交。"
                                         if attempt.prompt_id else
                                         "无 prompt_id：无法证明未提交，保持 UNKNOWN，不得重新提交。")})
    add_audit(db, "local-user", "MATERIAL_BATCH_RECONCILED", "material_run", run.id,
              {"unknown": len(unknown)}, project_id=run.project_id)
    result = {"run_id": run.id, "status": run.status, "unknown": unknown,
            "resubmitted": False,
            "note": "对账只核对与采用已有结果，不会发起第二次 /prompt 提交。"}
    return _save_control(db, key, request_hash, result)


def refresh_run_status(db, run_id: str) -> dict[str, Any]:
    """Recompute the batch status from its items.

    A batch is only ``COMPLETED`` when every item really reached a candidate.  A
    single unknown externaloutcome keeps the whole batch at ``NEEDS_REVIEW``,
    because "finished" would hide an unresolved submission.
    """
    run = _require_run(db, run_id)
    items = _items(db, run.id)
    if not items:
        return public_run(db, run_id)
    statuses = [item.status for item in items]
    if any(item.status == ITEM_NEEDS_REVIEW for item in items):
        target = RUN_NEEDS_REVIEW
    elif all(status == ITEM_CANDIDATE_READY for status in statuses):
        target = RUN_COMPLETED
    elif all(status in ITEM_TERMINAL for status in statuses):
        target = RUN_CANCELLED if all(status == "CANCELLED" for status in statuses) else RUN_PARTIAL_FAILED
    elif any(status in ITEM_IN_FLIGHT or status == ITEM_QUEUED for status in statuses):
        target = RUN_CANCEL_REQUESTED if run.cancel_requested else RUN_RUNNING
    else:
        target = run.status
    if target != run.status:
        run.status = target
        run.state_version += 1
        db.commit()
    return public_run(db, run_id)


def _require_run(db, run_id: str) -> MaterialRun:
    run = db.get(MaterialRun, run_id)
    if run is None:
        raise HTTPException(404, "MATERIAL_RUN_NOT_FOUND")
    return run


# --- public views ----------------------------------------------------------


def public_item(db, item: MaterialItem) -> dict[str, Any]:
    requirement = db.get(AssetRequirement, item.requirement_id) if item.requirement_id else None
    attempts = db.scalars(
        select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item.id)
        .order_by(MaterialImageAttempt.attempt_no)
    ).all()
    candidates = []
    for attempt in attempts:
        if not attempt.candidate_id:
            continue
        candidate = db.get(CandidateAsset, attempt.candidate_id)
        if candidate is None:
            continue
        candidates.append({
            "candidate_id": candidate.id,
            "status": candidate.status,
            "content_sha256": candidate.payload.get("content_sha256"),
            "is_mock": bool((candidate.payload.get("provenance") or {}).get("is_mock")),
            "formal_asset_id": candidate.formal_asset_id,
            "attempt_no": attempt.attempt_no,
            "qc": (candidate.payload or {}).get("qc"),
        })
    return {
        "item_id": item.id,
        "requirement_id": item.requirement_id,
        "family": (requirement.payload or {}).get("family") if requirement else None,
        "entity_id": (requirement.payload or {}).get("entity_id") if requirement else None,
        "carrier": (requirement.payload or {}).get("carrier") if requirement else (item.payload or {}).get("carrier"),
        "generation_revision": item.generation_revision,
        "status": item.status,
        "state_version": item.state_version,
        "error_code": item.error_code,
        "current_attempt_id": item.current_attempt_id,
        "heartbeat_at": item.heartbeat_at.isoformat() if item.heartbeat_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "brief": (item.payload or {}).get("brief"),
        "allowed_actions": (["resume"] if item.status == ITEM_FAILED else
                            ["reconcile"] if item.status == ITEM_NEEDS_REVIEW else []) +
                           (["regenerate"] if item.status in {ITEM_CANDIDATE_READY, ITEM_FAILED, "CANCELLED"}
                            and not any(a.external_outcome == OUTCOME_UNKNOWN for a in attempts) else []),
        "candidates": candidates,
        "attempts": [{
            "attempt_id": a.id,
            "attempt_no": a.attempt_no,
            "status": a.status,
            "prompt_id": a.prompt_id,
            "external_outcome": a.external_outcome,
            "graph_sha256": a.graph_sha256,
            "seed": a.seed,
            "width": a.width,
            "height": a.height,
            "output_node_id": a.output_node_id,
            "runtime_fingerprint": a.runtime_fingerprint,
            "content_sha256": a.content_sha256,
            "error_code": a.error_code,
            "reconciliation_state": a.reconciliation_state,
            "resubmitted": a.resubmitted,
            "submit_intent_at": a.submit_intent_at.isoformat() if a.submit_intent_at else None,
        } for a in attempts],
    }


def public_run(db, run_id: str) -> dict[str, Any]:
    run = _require_run(db, run_id)
    items = _items(db, run.id)
    statuses = [item.status for item in items]
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "plan_revision_id": run.plan_id,
        "confirmation_id": run.confirmation_id,
        "mode": run.mode,
        "kind": (run.payload or {}).get("kind"),
        "status": run.status,
        "state_version": run.state_version,
        "cancel_requested": run.cancel_requested,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "counts": {
            "total": len(items),
            "candidate_ready": statuses.count(ITEM_CANDIDATE_READY),
            "failed": statuses.count(ITEM_FAILED),
            "needs_review": statuses.count(ITEM_NEEDS_REVIEW),
            "queued": statuses.count(ITEM_QUEUED),
            "running": sum(1 for s in statuses if s in ITEM_IN_FLIGHT),
        },
        "items": [public_item(db, item) for item in items],
        "allowed_actions": _allowed_actions(run, statuses),
    }


def _allowed_actions(run: MaterialRun, statuses: list[str]) -> list[str]:
    actions = []
    if run.status not in RUN_TERMINAL:
        actions.append("cancel")
    if any(status == ITEM_FAILED for status in statuses):
        actions.append("resume")
    if any(status == ITEM_NEEDS_REVIEW for status in statuses):
        actions.append("reconcile")
    if any(status == ITEM_CANDIDATE_READY for status in statuses):
        actions.append("select_and_promote")
    actions.append("regenerate")
    return actions


def bind_material_master(db, requirement, asset, actor: str, *, canonical_entity_id: str | None = None):
    payload = dict(requirement.payload)
    if canonical_entity_id:
        payload["canonical_entity_id"] = canonical_entity_id
    requirement.payload = {
        **payload,
        "strategy": "reuse_master",
        "bound_asset_id": asset.id,
        "bound_asset_sha256": asset.sha256,
        "confirmed_by": actor,
    }
    requirement.sha256 = graph_hash(requirement.payload)
    add_audit(db, actor, "MATERIAL_MASTER_SELECTED", "asset_requirement", requirement.id,
              {"previous_asset_id": payload.get("bound_asset_id"), "asset_id": asset.id,
               "canonical_entity_id": payload.get("canonical_entity_id")}, project_id=asset.project_id)


def bind_adopted_masters_to_shots(db, project_id: str, plan_id: str, actor: str) -> int:
    """Adopt every confirmed "reuse this master" into the shots that asked for it.

    A confirmed requirement is not yet a shot binding: the shot table, the
    journey, the overview and the package readiness all read ``shot.bindings``,
    not the material list.  This is the single executor every confirmation path
    runs, so the material list and the package always agree.

    **One shot, one write.**  A shot can be affected by several confirmed
    requirements at once (two characters, or a character and the scene plate).
    Writing them one requirement at a time made the second write read the
    bindings from before the first one and refuse the whole confirmation with
    ``Bindings are missing assets required by the shot``.  So every decision is
    collected first, the shot's complete target set is composed once, and
    ``shot.bindings`` plus ``required_asset_ids`` are written exactly once.

    Confirming master B for a character that previously used A **replaces** A's
    subject slot with B and drops A from the shot's ``required_asset_ids``; it
    never leaves both A and B bound to one person, and every other valid
    reference on that shot stays.  Idempotent: a replayed confirmation (a lost
    response, a resumed batch) finds the target set already in place and writes
    nothing.  Frozen versions, live shoots and shots that already own a current
    passed take are never touched.
    """
    from sqlalchemy import select as sa_select

    from shine.comfyops.models import AssetRequirement
    from shine.models import Asset, Job, Shot, ShotAssetBinding
    from shine.schemas import ReferenceUse, ShotBindingInput
    from shine.services import (
        _binding_rows,
        bindings_unchanged,
        protected_shot_ids,
        require_project,
        set_shot_bindings,
    )

    project = require_project(db, project_id)
    rows = db.scalars(sa_select(AssetRequirement).where(
        AssetRequirement.plan_id == plan_id,
        AssetRequirement.project_id == project_id,
    )).all()

    # 与需求页同一份推导裁决：引用范围按目录×场次重算，不信旧的「全塞」
    # used_shot_ids。推导不可用（大纲未确认等）时回退台账既有引用。
    citations: dict[tuple[str, str], dict[str, Any]] | None = None
    try:
        from shine.director import requirements as requirement_source
        derived = requirement_source.derive(db, project)
        citations = {(str(r["family"]), str(r["entity_id"])): r
                     for r in derived["requirements"]}
    except Exception:
        citations = None

    # 1. Read every decision first.  One shot may answer to several requirements;
    #    the per-shot target set is composed before any row is written, so no
    #    write can be decided from bindings another write just replaced.
    #    row_citations 记录每个需求「采纳到哪些镜」：改策略（取消采用）或
    #    重新映射后，不在范围内的旧绑定由同一执行者解绑。
    decisions: dict[str, dict[tuple[str, str], str]] = {}
    row_citations: list[tuple[str, str, set[str]]] = []
    for requirement in sorted(rows, key=lambda row: row.id):
        payload = dict(requirement.payload or {})
        family = payload.get("family")
        if family not in {"character", "scene"}:
            continue
        entity = str(payload.get("entity_id") or "")
        if not entity:
            continue
        if citations is not None:
            evidence = citations.get((str(family), entity))
            used = {str(s) for s in (evidence or {}).get("shot_ids") or []}
            if evidence is not None and sorted(used) != sorted(str(s) for s in payload.get("used_shot_ids") or []):
                requirement.payload = {
                    **payload, "used_shot_ids": sorted(used),
                    "used_episodes": sorted({str(e) for e in evidence.get("used_episodes") or []}),
                }
                requirement.sha256 = graph_hash(requirement.payload)
                payload = dict(requirement.payload)
        else:
            used = {str(s) for s in payload.get("used_shot_ids") or []}
        adopted_shots = used if payload.get("strategy") == "reuse_master" else set()
        row_citations.append((str(family), entity, adopted_shots))
        if payload.get("strategy") != "reuse_master":
            continue
        asset_id = payload.get("bound_asset_id")
        if not asset_id:
            continue
        asset = db.get(Asset, asset_id)
        if asset is None or not asset.approved or not asset.locked:
            continue
        for shot_id in adopted_shots:
            # 同一需求（同一人物 / 同一场景）被确认过两次时，最后一次确认的那份胜出；
            # 场景底图就是这一镜渲染的环境图，Ref2VA 至少一个参考时由它兜底。
            decisions.setdefault(str(shot_id), {})[(str(family), entity)] = asset.id

    entities = {entity for (_, entity, _) in row_citations}
    bound_shot_ids: set[str] = set()
    if entities:
        bound_shot_ids = set(db.scalars(
            sa_select(ShotAssetBinding.shot_id).where(ShotAssetBinding.asset_id.in_(
                sa_select(Asset.id).where(Asset.project_id == project_id,
                                          Asset.entity_id.in_(entities))))))
    touched = sorted({sid for (_, _, used) in row_citations for sid in used} | bound_shot_ids)
    protected = protected_shot_ids(db, project_id, touched)

    def matches_decision(link, family: str, entity: str) -> bool:
        """Whether this existing binding is the slot the decision replaces."""
        if link.use_as != ("subject" if family == "character" else "picture"):
            return False
        if link.role == entity:
            return True
        if family == "character" and link.role:
            return False
        old = db.get(Asset, link.asset_id)
        return old is not None and str(old.entity_id or "") == entity

    scene_row_by_asset: dict[str, Any] = {}
    release_ids: set[str] = set()
    for row in rows:
        payload = row.payload or {}
        if payload.get("family") == "scene" and payload.get("bound_asset_id"):
            scene_row_by_asset[str(payload["bound_asset_id"])] = row
        if payload.get("release_asset_id"):
            release_ids.add(str(payload["release_asset_id"]))
    asset_role: dict[str, str | None] = {}

    def role_of(asset_id: str) -> str | None:
        if asset_id not in asset_role:
            asset = db.get(Asset, asset_id)
            asset_role[asset_id] = asset.role if asset is not None else None
        return asset_role[asset_id]

    bound = 0
    for shot_id in touched:
        if shot_id in protected:
            continue
        shot = db.get(Shot, shot_id)
        if shot is None or shot.project_id != project_id:
            continue
        passed = db.scalar(sa_select(Job).where(
            Job.shot_id == shot.id, Job.status == "PASSED", Job.stale.is_(False),
            Job.shot_revision == shot.revision,
        ))
        if passed is not None:
            continue
        declared_characters = list((shot.spec or {}).get("characters") or [])
        wanted = {key: asset_id for key, asset_id in decisions.get(shot_id, {}).items()
                  if key[0] != "character" or key[1] in declared_characters}

        # 2. The target set: keep every valid reference, drop the master a
        #    requirement is being moved off, then add the confirmed ones.
        current = _binding_rows(db, shot)
        keep, replaced = [], set()
        for link in current:
            swap = [asset_id for (family, entity), asset_id in wanted.items()
                    if matches_decision(link, family, entity) and link.asset_id != asset_id]
            if swap:
                replaced.add(link.asset_id)
                continue
            # 显式取消采用：confirm 记录了被释放的旧主素材，这里解绑。
            if link.asset_id in release_ids:
                replaced.add(link.asset_id)
                continue
            keep.append(link)
        target: list[ShotBindingInput] = [
            ShotBindingInput(asset_id=link.asset_id, use_as=ReferenceUse(link.use_as),
                             role=link.role, frame_role=link.frame_role, ordinal=index)
            for index, link in enumerate(keep)
        ]
        bound_ids = {row.asset_id for row in target}
        for (family, entity), asset_id in wanted.items():
            if asset_id in bound_ids:
                continue
            if family == "character":
                target.append(ShotBindingInput(asset_id=asset_id, use_as=ReferenceUse.SUBJECT,
                                               role=entity, ordinal=len(target)))
            else:
                target.append(ShotBindingInput(asset_id=asset_id, use_as=ReferenceUse.PICTURE,
                                               ordinal=len(target)))
            bound_ids.add(asset_id)
        # 每镜至多一张场景底图：历史「全塞」或多份采纳只留最新一张，其余解绑，
        # 保证人物主体+单场景不撞 H3 九图上限。
        scene_entries = [t for t in target
                         if t.use_as == ReferenceUse.PICTURE and role_of(t.asset_id) == "scene"]
        if len(scene_entries) > 1:
            from datetime import datetime

            def scene_priority(entry: ShotBindingInput):
                row = scene_row_by_asset.get(entry.asset_id)
                return row.created_at if row is not None else datetime.min
            keep_scene = max(scene_entries, key=scene_priority)
            for entry in scene_entries:
                if entry is not keep_scene:
                    replaced.add(entry.asset_id)
                    target.remove(entry)
            for index, entry in enumerate(target):
                entry.ordinal = index
        # 唯一"是否已经就是这个集合"的判定：复用 services 的同一个比较函数，
        # 读取前先让关系集合从台账重读，避免拿到另一笔写入之前的旧值。
        db.expire(shot, ["bindings"])
        if bindings_unchanged(shot, target):
            continue

        # 3. One write: the declaration follows this exact set, the bindings are
        #    written once, and the revision moves exactly when the set did.
        spec_payload = dict(shot.spec or {})
        declared = list(spec_payload.get("required_asset_ids") or [])
        merged = list(dict.fromkeys([
            *[asset_id for asset_id in declared if asset_id not in replaced],
            *[row.asset_id for row in target],
        ]))
        if merged != declared:
            spec_payload["required_asset_ids"] = merged
            shot.spec = spec_payload
            shot.revision = (shot.revision or 1) + 1
        set_shot_bindings(db, project, shot, target, actor=actor, bump_revision=False)
        bound += 1
    # 释放意图一次性消费：解绑完成后清掉 release_asset_id，避免后续 bind 重复解绑。
    for row in rows:
        payload = dict(row.payload or {})
        if payload.get("release_asset_id"):
            payload.pop("release_asset_id", None)
            row.payload = payload
            row.sha256 = graph_hash(payload)
    return bound


def retire_unused_masters(db, plan, selected, actor: str, *, entities: set[str]):
    from shine.models import Asset, ShotAssetBinding

    db.flush()
    bound_ids = select(AssetRequirement.payload["bound_asset_id"].as_string()).where(
        AssetRequirement.plan_id == plan.id,
        AssetRequirement.payload["bound_asset_id"].as_string().is_not(None),
    )
    for previous in db.scalars(select(Asset).where(
        Asset.project_id == selected.project_id,
        Asset.id != selected.id,
        Asset.role == selected.role,
        Asset.entity_id.in_(entities),
        Asset.approved.is_(True),
        Asset.locked.is_(True),
        Asset.id.not_in(bound_ids),
        Asset.id.not_in(select(ShotAssetBinding.asset_id)),
    )):
        previous.locked = False
        previous.locked_at = None
        add_audit(db, actor, "MATERIAL_MASTER_ARCHIVED", "asset", previous.id,
                  {"replacement_asset_id": selected.id}, project_id=previous.project_id)


def evaluate_materials(db, project_id: str, *, data_dir=None) -> dict[str, Any]:
    """The material completion condition.

    Every required requirement of the current confirmed material plan must be
    bound to a current master of this project that is approved and locked, has
    the right family and identity, still hashes to its registered digest and
    carries an approved licence.  A real project may not bind an offline-only
    mock master.

    The judgement is the same shape as the production readiness gate, so a
    reason code means the same thing in both places.
    """
    from shine.comfyops.models import RightsRecord
    from shine.comfyops.preproduction import current_material_plan
    from shine.models import Asset

    plan = current_material_plan(db, project_id)
    if plan is None or plan.status not in {PLAN_CONFIRMED, PLAN_MATERIALS_READY}:
        return {"materials_ready": False, "requirements": [], "missing": [],
                "reason": "MATERIAL_PLAN_NOT_CONFIRMED"}

    assets = db.scalars(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.approved.is_(True),
            Asset.locked.is_(True),
        )
    ).all()
    rows = db.scalars(
        select(AssetRequirement).where(AssetRequirement.plan_id == plan.id)
    ).all()

    judged: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload or {}
        family = payload.get("family")
        entity_id = payload.get("entity_id")
        critical = bool(payload.get("critical", True))
        if payload.get("strategy") in {"omit_prop", "skip"}:
            judged.append({"requirement_id": row.id, "family": family, "entity_id": entity_id,
                           "asset_id": None, "status": "OMITTED", "reason": None})
            continue
        bound = payload.get("bound_asset_id")
        # An explicit selection is authority. Never substitute another version
        # just because it has the same entity name, including when it is missing.
        match = next((a for a in assets if a.id == bound), None) if bound else None
        reason: str | None = None
        if match is None:
            reason = "ASSET_OR_VOICE_MISSING"
        else:
            if match.role != family or match.entity_id != (payload.get("canonical_entity_id") or entity_id):
                reason = "ASSET_IDENTITY_MISMATCH"
            if payload.get("bound_asset_sha256") and payload["bound_asset_sha256"] != match.sha256:
                reason = "ASSET_HASH_CHANGED"
            provenance = match.provenance or {}
            if provenance.get("offline_only", False) or provenance.get("is_mock", False):
                reason = "MOCK_ASSET_NOT_PRODUCTION"
            rights_id = provenance.get("rights_id")
            if rights_id:
                rights = db.get(RightsRecord, rights_id)
                if not rights or rights.status != "APPROVED":
                    reason = "LICENSE_NOT_APPROVED"
                else:
                    try:
                        require_rights(
                            db,
                            project_id,
                            rights_id,
                            rights.payload.get("scope", "asset"),
                        )
                    except HTTPException:
                        reason = "LICENSE_NOT_APPROVED"
            if data_dir:
                from shine.media.artifacts import safe_asset_path

                try:
                    media = safe_asset_path(data_dir, match.storage_path).read_bytes()
                    if hashlib.sha256(media).hexdigest() != match.sha256:
                        reason = "ASSET_HASH_CHANGED"
                except OSError:
                    reason = "ASSET_FILE_MISSING"
        satisfied = match is not None and reason is None
        entry = {
            "requirement_id": row.id,
            "family": family,
            "entity_id": entity_id,
            "asset_id": match.id if match is not None else None,
            "status": "SATISFIED" if satisfied else "MISSING",
            "reason": reason,
        }
        judged.append(entry)
        if critical and not satisfied:
            missing.append(entry)

    return {
        "materials_ready": bool(judged) and not missing,
        "requirements": judged,
        "missing": missing,
        "reason": None if not missing else "MATERIAL_REQUIREMENT_UNBOUND",
    }

def plan_progress(db, project_id: str, *, data_dir=None) -> dict[str, Any]:
    """The material page's state: plan, requirements, matches, batches, blockers."""
    from shine.comfyops.preproduction import current_material_plan

    project = require_project(db, project_id)
    plan = current_material_plan(db, project_id)
    if plan is None:
        return {"project_id": project_id, "stage": "no_plan", "plan_revision_id": None,
                "requirements": [], "runs": [], "blockers": []}
    from shine.models import Asset

    masters = {
        (a.role, a.entity_id): a
        for a in db.scalars(select(Asset).where(
            Asset.project_id == project_id, Asset.approved.is_(True), Asset.locked.is_(True)))
        if a.entity_id
    }
    requirements = []
    for row in db.scalars(select(AssetRequirement).where(AssetRequirement.plan_id == plan.id)).all():
        payload = row.payload or {}
        master = masters.get((payload.get("family"), payload.get("entity_id")))
        used_shot_ids = payload.get("used_shot_ids") or []
        requirements.append({
            "requirement_id": row.id,
            "requirement_hash": row.sha256,
            "family": payload.get("family"),
            "entity_id": payload.get("entity_id"),
            "canonical_entity_id": payload.get("canonical_entity_id"),
            "strategy": payload.get("strategy"),
            "status": row.status,
            "bound_asset_id": payload.get("bound_asset_id"),
            "brief_note": payload.get("brief_note"),
            "description": payload.get("description") or "",
            "core_prop_reason": payload.get("core_prop_reason") or "",
            "name": payload.get("entity_id"),
            "optional": payload.get("family") != "character",
            "critical": bool(payload.get("critical", True)),
            "used_shot_ids": used_shot_ids,
            "used_shot_count": len(used_shot_ids),
            "used_episodes": payload.get("used_episodes") or [],
            "matched_master": ({"asset_id": master.id, "sha256": master.sha256,
                                "entity_id": master.entity_id} if master else None),
        })
    runs = [public_run(db, run.id) for run in db.scalars(
        select(MaterialRun).where(MaterialRun.project_id == project_id)
        .order_by(MaterialRun.created_at.desc())
    ).all()]
    blockers = []
    if plan.status == PLAN_NEEDS_CLARIFICATION:
        blockers.append({"code": "MATERIAL_CONFIRMATION_REQUIRED",
                         "note": "存在待消歧的身份或未回答的问题，先处理再确认清单。"})
    # The completion condition is judged, not remembered: the page tells the
    # truth about what is still unbound even if a status was written earlier.
    evaluation = evaluate_materials(db, project_id, data_dir=data_dir)
    for entry in evaluation["missing"]:
        blockers.append({
            "code": entry["reason"] or "MATERIAL_REQUIREMENT_UNBOUND",
            "requirement_id": entry["requirement_id"],
            "note": f"{entry['family']} · {entry['entity_id']} 还没有绑定有效主素材（{entry['reason']}）。",
        })
    if evaluation["materials_ready"]:
        stage = "materials_ready"
    elif plan.status == PLAN_CONFIRMED:
        stage = "select_materials"
    elif plan.status == PLAN_DRAFT:
        stage = "confirm_requirements"
    else:
        stage = "read_script"
    return {
        "project_id": project_id,
        "project_title": project.title,
        "stage": stage,
        "plan_revision_id": plan.id,
        "plan_status": plan.status,
        "document_id": plan.payload.get("document_id"),
        "script_hash": plan.payload.get("script_hash"),
        "visual_direction": plan.payload.get("visual_direction"),
        "confirmation": plan.payload.get("confirmation"),
        # The hash a confirmation has to quote. The server publishes it so a
        # client never has to reproduce this module's canonicalisation.
        "expected_snapshot": confirmation_snapshot_hash(plan),
        "requirements": requirements,
        "runs": runs,
        "blockers": blockers,
        "materials_ready": evaluation["materials_ready"],
        "material_evaluation": evaluation,
        "production_plan_present": bool(
            db.scalar(select(PreProductionPlan.id).where(
                PreProductionPlan.project_id == project_id,
                PreProductionPlan.purpose == PLAN_PURPOSE_PRODUCTION,
            ).limit(1))
        ),
        "generated_at": utcnow().isoformat(),
    }


def generation_progress(db, project_id: str) -> dict[str, Any]:
    """Read-only progress of this project's material generation batches.

    The identical ``public_run`` projection the material page reads, minus the
    completion judgement (which hashes master files): a progress bar polled
    every few seconds must not re-hash the library each tick.  This is a read
    of the same ledger, not a second decision chain.
    """
    require_project(db, project_id)
    runs = [public_run(db, run.id) for run in db.scalars(
        select(MaterialRun).where(MaterialRun.project_id == project_id)
        .order_by(MaterialRun.created_at.desc())
    ).all() if (run.payload or {}).get("kind") == "production"]
    return {"project_id": project_id, "runs": runs, "generated_at": utcnow().isoformat()}

"""Phase A: plan-purpose isolation, material contracts and the batch ledger.

The safety property under test is the one the whole feature rests on: a
director-side material-preparation plan shares the ``preproduction_plans`` table
with the production contract, and it must never be selectable by a production
path.  If that leaks, a newer material revision silently replaces the frozen H3
manifest and every gate downstream validates the wrong contract.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shine.comfy.workflow import graph_hash
from shine.comfyops import preproduction
from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    PLAN_PURPOSE_PRODUCTION,
    AssetRequirement,
    PreProductionPlan,
)
from shine.materialprep.contracts import (
    MaterialConfirmation,
    MaterialImageBrief,
    RequirementDecision,
    ScriptPreparation,
)
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from shine.models import Project


def _add_plan(db, project_id, *, purpose, status="ANALYZED", payload=None, created_at=None):
    body = payload if payload is not None else {"document_id": "doc", "script_hash": "h", "requirements": []}
    row = PreProductionPlan(
        project_id=project_id,
        payload=body,
        sha256=graph_hash(body),
        status=status,
        purpose=purpose,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def _project(db, title="雾港素材") -> str:
    project = Project(title=title, language="Chinese")
    db.add(project)
    db.flush()
    return project.id


# --- purpose isolation -----------------------------------------------------


def test_analyze_writes_a_production_purpose_plan(api):
    """The existing production analyser must keep labelling its own rows."""
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("剧本.txt", "场景一：\n林知远：你来了。\n".encode(), "text/plain")},
    ).json()
    analyzed = client.post(
        f"/api/v1/projects/{project_id}/preproduction/analyze",
        json={"document_id": document["id"]},
    )
    assert analyzed.status_code == 200, analyzed.text
    with factory() as db:
        row = db.get(PreProductionPlan, analyzed.json()["id"])
        assert row.purpose == PLAN_PURPOSE_PRODUCTION


def test_material_plan_never_becomes_the_current_production_plan(api):
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        production = _add_plan(db, project_id, purpose=PLAN_PURPOSE_PRODUCTION, status="FROZEN")
        # Newer, and carrying a manifest-shaped payload: the two ways a material
        # row could try to impersonate the production contract.
        _add_plan(
            db,
            project_id,
            purpose=PLAN_PURPOSE_ASSET_PREPARATION,
            status="CONFIRMED",
            payload={"document_id": "doc", "script_hash": "h", "requirements": [], "manifest": {"links": []}},
        )
        db.commit()

        assert preproduction.current_plan(db, project_id).id == production.id
        assert preproduction.current_material_plan(db, project_id).purpose == PLAN_PURPOSE_ASSET_PREPARATION


def test_production_selectors_refuse_a_material_only_project(api):
    """A project with only a material plan has no production contract at all."""
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION, status="CONFIRMED")
        db.commit()

        assert preproduction.current_plan(db, project_id) is None
        with pytest.raises(HTTPException) as readiness_error:
            preproduction.readiness(db, project_id, offline=True)
        assert readiness_error.value.detail == "PREPRODUCTION_NOT_ANALYZED"
        # Legacy no-contract path: enforce returns instead of adopting the material plan.
        assert preproduction.enforce(db, project_id, offline=True) is None


def test_material_plan_cannot_satisfy_the_production_gate(api):
    """An ANALYZED production plan plus a newer material plan still gates on the production one."""
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        _add_plan(db, project_id, purpose=PLAN_PURPOSE_PRODUCTION, status="ANALYZED")
        _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION, status="MATERIALS_READY")
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            preproduction.enforce(db, project_id, offline=True)
        assert excinfo.value.detail == "PREPRODUCTION_NOT_READY"


def test_plan_for_manifest_skips_material_revisions(api):
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        manifest = {"links": [], "rule": "r", "models": [], "style_input_contract": "independent-text-v1"}
        _add_plan(
            db,
            project_id,
            purpose=PLAN_PURPOSE_ASSET_PREPARATION,
            status="CONFIRMED",
            payload={"document_id": "d", "script_hash": "h", "requirements": [], "manifest": manifest},
        )
        db.commit()

        assert preproduction._plan_for_manifest(db, project_id, graph_hash(manifest)) is None


def test_require_plan_for_purpose_rejects_the_wrong_purpose(api):
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        material = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        production = _add_plan(db, project_id, purpose=PLAN_PURPOSE_PRODUCTION)
        db.commit()

        assert preproduction.require_plan_for_purpose(db, material.id, PLAN_PURPOSE_ASSET_PREPARATION).id == material.id
        assert preproduction.require_plan_for_purpose(db, production.id, PLAN_PURPOSE_PRODUCTION).id == production.id
        with pytest.raises(HTTPException) as mismatch:
            preproduction.require_plan_for_purpose(db, material.id, PLAN_PURPOSE_PRODUCTION)
        assert mismatch.value.status_code == 409
        assert mismatch.value.detail == "PREPRODUCTION_PLAN_PURPOSE_MISMATCH"
        with pytest.raises(HTTPException) as missing:
            preproduction.require_plan_for_purpose(db, "no-such-plan", PLAN_PURPOSE_PRODUCTION)
        assert missing.value.status_code == 404


def test_same_timestamp_tie_break_is_stable_across_purposes(api):
    """Two plans in one host clock tick: rowid decides, and purpose is filtered first."""
    from datetime import datetime

    _, factory, _ = api
    tick = datetime(2026, 11, 5, 10, 0, 0)
    with factory() as db:
        project_id = _project(db)
        first = _add_plan(db, project_id, purpose=PLAN_PURPOSE_PRODUCTION, created_at=tick)
        _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION, created_at=tick)
        second = _add_plan(db, project_id, purpose=PLAN_PURPOSE_PRODUCTION, created_at=tick)
        db.commit()

        assert preproduction.current_plan(db, project_id).id == second.id
        assert first.id != second.id


# --- contracts -------------------------------------------------------------


def test_script_preparation_accepts_a_shallow_scene_answer():
    prepared = ScriptPreparation.model_validate(
        {
            "scene_key": "S1",
            "summary": "林知远在雨夜的码头等待。",
            "locked_facts": ["时代：1930年代"],
            "requirements": [
                {
                    "family": "character",
                    "name": "林知远",
                    "description": "三十岁上下，长风衣。",
                    "source_span_ids": ["S1#p1"],
                    "questions": [],
                }
            ],
            "questions": ["林知远的服装材质是否有剧情要求？"],
        }
    )
    assert prepared.schema_version == "1.0"
    assert prepared.requirements[0].family == "character"


def test_script_preparation_rejects_unknown_fields_and_production_intent():
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate({"scene_key": "S1", "shots": []})
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate({"scene_key": "S1", "schema_version": "2.0"})
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate(
            {"scene_key": "S1", "requirements": [{"family": "voice", "name": "林知远"}]}
        )


def test_script_preparation_limits_are_refusals_not_truncation():
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate({"scene_key": "S1", "locked_facts": ["x" * 301]})
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate({"scene_key": "S1", "requirements": [_requirement()] * 33})
    with pytest.raises(ValidationError):
        ScriptPreparation.model_validate(
            {"scene_key": "S1", "requirements": [{**_requirement(), "source_span_ids": ["s"] * 9}]}
        )


def _requirement() -> dict:
    return {"family": "prop", "name": "铜怀表", "description": "表盖刻字。", "source_span_ids": ["S1#p2"]}


def test_material_image_brief_carries_no_execution_authority():
    brief = MaterialImageBrief.model_validate(
        {
            "appearance": "a man in his thirties, short hair, a long dark wool coat.",
            "must_keep": ["1930s tailoring"],
            "avoid": ["a modern sports watch"],
            "questions": [],
        }
    )
    assert brief.must_keep == ["1930s tailoring"]
    # The prose that reaches the prompt is English: a Chinese appearance is a
    # contract refusal, not something the composer silently passes through.
    with pytest.raises(ValidationError) as excinfo:
        MaterialImageBrief.model_validate({
            "appearance": "三十岁上下男性，短发，深灰长风衣，羊毛质地。",
            "must_keep": ["1930年代剪裁"],
            "avoid": ["现代运动腕表"],
            "questions": [],
        })
    assert "English visual prose" in str(excinfo.value)
    for forbidden in (
        {"appearance": "x", "seed": 7},
        {"appearance": "x", "workflow": "qwen"},
        {"appearance": "x", "node_id": "461"},
        {"appearance": ""},
        {"appearance": "x", "must_keep": ["y" * 161]},
        {"appearance": "x", "questions": ["q"] * 7},
    ):
        with pytest.raises(ValidationError):
            MaterialImageBrief.model_validate(forbidden)


def test_confirmation_snapshot_binds_every_decision():
    confirmation = MaterialConfirmation.model_validate(
        {
            "plan_revision_id": "p" * 36,
            "expected_snapshot": "a" * 64,
            "decisions": [
                RequirementDecision(requirement_id="r" * 36, strategy="generate", entity_id="林知远").model_dump()
            ],
            "visual_direction": {"era": "1930年代", "palette": "青灰"},
        }
    )
    assert confirmation.decisions[0].strategy == "generate"
    with pytest.raises(ValidationError):
        MaterialConfirmation.model_validate(
            {
                "plan_revision_id": "p" * 36,
                "expected_snapshot": "short",
                "decisions": [{"requirement_id": "r", "strategy": "generate", "entity_id": "林"}],
            }
        )
    with pytest.raises(ValidationError):
        MaterialConfirmation.model_validate(
            {
                "plan_revision_id": "p" * 36,
                "expected_snapshot": "a" * 64,
                "decisions": [{"requirement_id": "r", "strategy": "auto", "entity_id": "林"}],
            }
        )


# --- ledger uniqueness -----------------------------------------------------


def _run(db, project_id, plan_id, *, idempotency_key="k1", request_hash="r1"):
    payload = {"requirement_ids": []}
    row = MaterialRun(
        project_id=project_id,
        plan_id=plan_id,
        confirmation_id="c" * 64,
        confirmation_hash="d" * 64,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        mode="real",
        status="QUEUED",
        payload=payload,
        sha256=graph_hash(payload),
    )
    db.add(row)
    db.flush()
    return row


def _requirement_row(db, project_id, plan_id, entity_id="林知远"):
    payload = {"family": "character", "entity_id": entity_id, "critical": True}
    row = AssetRequirement(
        project_id=project_id, plan_id=plan_id, payload=payload, sha256=graph_hash(payload), status="MISSING"
    )
    db.add(row)
    db.flush()
    return row


def _item(db, run, project_id, requirement_id, *, revision=1, status="QUEUED"):
    payload = {"family": "character"}
    row = MaterialItem(
        run_id=run.id,
        project_id=project_id,
        requirement_id=requirement_id,
        generation_revision=revision,
        status=status,
        payload=payload,
        sha256=graph_hash(payload),
    )
    db.add(row)
    db.flush()
    return row


def test_one_item_per_confirmed_requirement_revision(api):
    """Two browsers, two idempotency keys, one requirement revision: one item."""
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        first_run = _run(db, project_id, plan.id, idempotency_key="browser-a")
        _item(db, first_run, project_id, requirement.id)
        db.commit()

        second_run = _run(db, project_id, plan.id, idempotency_key="browser-b")
        with pytest.raises(IntegrityError):
            _item(db, second_run, project_id, requirement.id)
        db.rollback()


def test_explicit_regeneration_is_a_new_revision_and_keeps_the_old_item(api):
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        old = _item(db, run, project_id, requirement.id, revision=1, status="CANDIDATE_READY")
        new = _item(db, run, project_id, requirement.id, revision=2)
        db.commit()

        rows = db.scalars(
            select(MaterialItem).where(MaterialItem.requirement_id == requirement.id)
        ).all()
        assert {r.generation_revision for r in rows} == {1, 2}
        assert old.status == "CANDIDATE_READY"
        assert new.status == "QUEUED"


def test_idempotency_key_is_unique_per_project_and_reusable_across_projects(api):
    _, factory, _ = api
    with factory() as db:
        first = _project(db, "作品一")
        second = _project(db, "作品二")
        plan_a = _add_plan(db, first, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        plan_b = _add_plan(db, second, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        _run(db, first, plan_a.id, idempotency_key="same-key")
        db.commit()

        _run(db, second, plan_b.id, idempotency_key="same-key")
        db.commit()

        with pytest.raises(IntegrityError):
            _run(db, first, plan_a.id, idempotency_key="same-key")
        db.rollback()


def test_only_one_unfinished_attempt_per_item(api):
    """The active-intent key is what stops a resume from double-submitting."""
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        item = _item(db, run, project_id, requirement.id)

        def attempt(no, status, active):
            payload = {"attempt": no}
            row = MaterialImageAttempt(
                item_id=item.id,
                run_id=run.id,
                project_id=project_id,
                attempt_no=no,
                status=status,
                active_intent_key=active,
                payload=payload,
                sha256=graph_hash(payload),
            )
            db.add(row)
            db.flush()
            return row

        attempt(1, "SUBMIT_INTENT", f"{item.id}:1")
        db.commit()

        with pytest.raises(IntegrityError):
            attempt(2, "SUBMIT_INTENT", f"{item.id}:1")
        db.rollback()

        # Terminal attempts release the slot, so a bounded resume is allowed.
        live = db.scalar(select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item.id))
        live.status = "FAILED"
        live.active_intent_key = None
        db.commit()
        attempt(2, "SUBMIT_INTENT", f"{item.id}:2")
        db.commit()
        assert len(db.scalars(select(MaterialImageAttempt)).all()) == 2


def test_terminal_attempts_coexist_without_an_active_intent(api):
    _, factory, _ = api
    with factory() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        item = _item(db, run, project_id, requirement.id)
        for no in (1, 2, 3):
            payload = {"attempt": no}
            db.add(
                MaterialImageAttempt(
                    item_id=item.id,
                    run_id=run.id,
                    project_id=project_id,
                    attempt_no=no,
                    status="FAILED",
                    active_intent_key=None,
                    payload=payload,
                    sha256=graph_hash(payload),
                )
            )
        db.commit()
        assert len(db.scalars(select(MaterialImageAttempt)).all()) == 3


def test_seed_survives_the_wide_template_value(api):
    """The audited template ships a 48-bit seed; a 32-bit column would corrupt it."""
    _, factory, _ = api
    seed = 447606998181262
    with factory() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        item = _item(db, run, project_id, requirement.id)
        payload = {"attempt": 1}
        db.add(
            MaterialImageAttempt(
                item_id=item.id,
                run_id=run.id,
                project_id=project_id,
                attempt_no=1,
                status="PREPARED",
                seed=seed,
                payload=payload,
                sha256=graph_hash(payload),
            )
        )
        db.commit()
        assert db.scalar(select(MaterialImageAttempt)).seed == seed


# --- agent tool selection --------------------------------------------------


def test_producer_keeps_its_default_contract_and_gains_one_tool():
    """A second tool selects a second schema; the default is never rewritten."""
    from shine.agents.film_contracts import FilmAgentRegistry, ProductionPlan
    from shine.materialprep.contracts import MaterialImageBrief

    default_contract, _ = FilmAgentRegistry.get("producer")
    assert default_contract is ProductionPlan
    assert FilmAgentRegistry.get("producer", "propose")[0] is ProductionPlan

    brief_contract, required = FilmAgentRegistry.get("producer", "material_image_brief")
    assert brief_contract is MaterialImageBrief
    assert required == ()


def test_script_prepare_is_a_separate_contract_from_the_storyboard():
    from shine.agents.film_contracts import FilmAgentRegistry, ScriptBreakdown
    from shine.materialprep.contracts import ScriptPreparation

    assert FilmAgentRegistry.get("scriptwriter")[0] is ScriptBreakdown
    assert FilmAgentRegistry.get("scriptwriter", "script_prepare")[0] is ScriptPreparation
    assert ScriptPreparation is not ScriptBreakdown


def test_material_tools_are_denied_to_every_other_role():
    from shine.agents.film_contracts import FilmAgentRegistry

    for role in ("director", "cinematographer", "editor", "scriptwriter", "sound_director"):
        with pytest.raises(ValueError) as excinfo:
            FilmAgentRegistry.get(role, "material_image_brief")
        assert excinfo.value.args[0] == "ROLE_OR_TOOL_DENIED"
    for role in ("director", "producer", "editor", "continuity_supervisor"):
        with pytest.raises(ValueError):
            FilmAgentRegistry.get(role, "script_prepare")


def test_material_prompts_state_their_own_scope_and_no_execution_authority():
    from shine.agents.crew_prompts import crew_prompt, material_image_brief_prompt, script_prepare_prompt

    prepared = crew_prompt("scriptwriter", tool="script_prepare")
    assert prepared == script_prepare_prompt()
    # A preparation prompt must not carry the storyboard chain's timing rules.
    assert "4-to-15-second" not in prepared
    assert "editorial_timing_plan" not in prepared
    assert "source_span_ids" in prepared

    brief = crew_prompt("producer", tool="material_image_brief")
    assert brief == material_image_brief_prompt()
    for forbidden in ("seed", "sampler", "node id"):
        assert forbidden in brief  # each appears only inside an explicit prohibition
    assert "Never output node ids" in brief
    assert "the user owns the final selection and promotion" in brief
    # The three composition contracts the product fixed, stated to the model.
    assert "half-body" in brief and "no person holding it" in brief and "no people" in brief


def test_material_controls_replay_receipt_after_refresh_without_second_mutation(api):
    from shine.materialprep import service
    _, factory, _ = api
    with factory.begin() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        run.status = "FAILED"
        item = _item(db, run, project_id, requirement.id, status="FAILED")
        run_id, item_id, version = run.id, item.id, run.state_version
    with factory() as db:
        first = service.resume_items(db, run_id, [item_id], version, idempotency_key="refresh-safe")
    with factory() as db:
        assert service.resume_items(db, run_id, [item_id], version, idempotency_key="refresh-safe") == first
        assert db.get(MaterialRun, run_id).state_version == version + 1
        assert db.get(MaterialItem, item_id).state_version == 1
    with factory() as db, pytest.raises(HTTPException, match="IDEMPOTENCY_KEY_CONFLICT"):
        service.resume_items(db, run_id, [], version, idempotency_key="refresh-safe")


def test_unknown_item_resume_does_not_requeue_batch(api):
    from shine.materialprep import service
    _, factory, _ = api
    with factory.begin() as db:
        project_id = _project(db)
        plan = _add_plan(db, project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION)
        requirement = _requirement_row(db, project_id, plan.id)
        run = _run(db, project_id, plan.id)
        run.status = "NEEDS_REVIEW"
        item = _item(db, run, project_id, requirement.id, status="NEEDS_REVIEW")
        run_id, item_id, version = run.id, item.id, run.state_version
    with factory() as db:
        result = service.resume_items(db, run_id, [item_id], version, idempotency_key="unknown-safe")
        assert result["queued"] == [] and result["refused"]
        assert db.get(MaterialRun, run_id).status == "NEEDS_REVIEW"
        assert db.get(MaterialRun, run_id).state_version == version

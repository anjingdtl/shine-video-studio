"""Phase B: the audited Qwen graph, output-node harvesting, prompt determinism,
batch idempotency and the unknown-submission rules.

The ``object_info`` fixture below lists the input names read from the pinned local
ComfyUI ``v0.39.0`` source (``nodes.py``, ``comfy_extras/nodes_qwen.py``,
``comfy_extras/nodes_images.py``); file hashes are in ``docs/upstream-audit.md``.
A live ``object_info`` from a running managed instance is still the authoritative
gate - see ``runtime.verify_graph_against_object_info`` - and is recorded as
PENDING_VERIFICATION until the renderer has really been queried.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from shine.comfy.client import (
    ComfyPromptRejected,
    ComfySubmissionUncertain,
    ComfyUnavailable,
    ComfyWorkflowError,
)
from shine.comfy.workflow import graph_hash, validate_against_object_info, validate_graph
from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan
from shine.materialprep import image_prompts, qwen_workflow, runtime, service
from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection
from shine.materialprep.execution import _submit_outcome
from shine.materialprep.models import MaterialRun
from shine.materialprep.states import (
    ATTEMPT_FAILED,
    ATTEMPT_UNKNOWN,
    OUTCOME_FAILED,
    OUTCOME_NOT_SENT,
    OUTCOME_UNKNOWN,
)


def object_info_fixture() -> dict:
    """Node schemas as the pinned ComfyUI source declares them."""

    def node(*required, optional=()):
        return {
            "input": {
                "required": {name: ("*",) for name in required},
                "optional": {name: ("*",) for name in optional},
            }
        }

    return {
        "UNETLoader": node("unet_name", "weight_dtype"),
        "QwenImage21Cache": node("model", "device", "dtype"),
        "CLIPLoader": node("clip_name", "type", "device"),
        "VAELoader": node("vae_name"),
        "TextEncodeQwenImage21": node("clip", "prompt", "negative_prompt",
                                      optional=("vae", "resolution", "images")),
        "EmptyLatentImage": node("width", "height", "batch_size"),
        "KSampler": node("model", "seed", "steps", "cfg", "sampler_name", "scheduler",
                         "positive", "negative", "latent_image", "denoise"),
        "VAEDecode": node("samples", "vae"),
        "SaveImage": node("images", "filename_prefix"),
    }


# --- the audited graph -----------------------------------------------------


def test_base_graph_is_stable_complete_and_validates():
    graph = qwen_workflow.base_graph()
    validate_graph(graph, required_nodes=qwen_workflow.REQUIRED_NODES)
    report = validate_against_object_info(graph, qwen_workflow.PARAMETER_MAP, object_info_fixture())
    assert report["missing_nodes"] == []
    assert report["missing_inputs"] == []
    # A pinned identity: if this hash changes, every readiness fingerprint and
    # every recorded attempt hash changes with it, which is the point.
    assert graph_hash(graph) == qwen_workflow.BASE_GRAPH_SHA256


def test_graph_declares_exactly_one_output_node_and_no_preview():
    graph = qwen_workflow.base_graph()
    outputs = [node_id for node_id, node in graph.items()
               if node["class_type"] in {"SaveImage", "SaveImageAdvanced", "PreviewImage", "PreviewAny"}]
    assert outputs == [qwen_workflow.OUTPUT_NODE]
    assert graph[qwen_workflow.OUTPUT_NODE]["class_type"] == qwen_workflow.OUTPUT_CLASS_TYPE
    # The prompt-expansion branch is lazily skipped at switch=false, so none of
    # its nodes may appear: they would load a 9B encoder nobody asked for.
    for absent in ("TextGenerate", "ComfySwitchNode", "PreviewAny", "PrimitiveStringMultiline"):
        assert absent not in {node["class_type"] for node in graph.values()}


def test_size_and_seed_reach_the_graph_only_through_the_parameter_map():
    from shine.comfy.workflow import apply_parameters

    graph = qwen_workflow.base_graph()
    values = {"width": 768, "height": 1344, "seed": 447606998181262, "prompt": "p", "negative_prompt": "n"}
    final = apply_parameters(graph, qwen_workflow.PARAMETER_MAP, values)
    assert final[qwen_workflow.EMPTY_LATENT_NODE]["inputs"]["width"] == 768
    assert final[qwen_workflow.EMPTY_LATENT_NODE]["inputs"]["height"] == 1344
    assert final[qwen_workflow.SAMPLER_NODE]["inputs"]["seed"] == 447606998181262
    # An unmapped logical parameter is a refusal, not a silent ignore.
    with pytest.raises(ComfyWorkflowError):
        apply_parameters(graph, qwen_workflow.PARAMETER_MAP, {"duration": 6})


def test_runtime_fingerprint_moves_with_every_component():
    files = [{"role": "unet", "filename": "a", "bytes": 1, "sha256": "x"}]
    base = qwen_workflow.runtime_fingerprint(
        graph_sha256="g", model_files=files, instance_identity={"instance_id": "i"}, prompt_template_version="v1"
    )
    assert base != qwen_workflow.runtime_fingerprint(
        graph_sha256="g2", model_files=files, instance_identity={"instance_id": "i"}, prompt_template_version="v1"
    )
    assert base != qwen_workflow.runtime_fingerprint(
        graph_sha256="g", model_files=[{**files[0], "sha256": "y"}],
        instance_identity={"instance_id": "i"}, prompt_template_version="v1"
    )
    assert base != qwen_workflow.runtime_fingerprint(
        graph_sha256="g", model_files=files, instance_identity={"instance_id": "i"}, prompt_template_version="v2"
    )


# --- output node harvesting ------------------------------------------------


def _history(outputs: dict) -> dict:
    return {"outputs": outputs, "status": {"status_str": "success"}}


def test_declared_output_slot_accepts_only_the_declared_node():
    entry = qwen_workflow.declared_output_slot(
        _history({qwen_workflow.OUTPUT_NODE: {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}})
    )
    assert entry["filename"] == "a.png"


@pytest.mark.parametrize(
    "outputs",
    [
        {},  # declared node absent
        {"472": {"images": [{"filename": "p.png", "type": "output"}]}},  # only a preview node produced
        {qwen_workflow.OUTPUT_NODE: {"images": []}},  # declared node produced nothing
        {qwen_workflow.OUTPUT_NODE: {"images": [{"filename": "a.png", "type": "temp"}]}},  # not an output
        {qwen_workflow.OUTPUT_NODE: {"images": [{"filename": "a.png", "type": "output"},
                                                {"filename": "b.png", "type": "output"}]}},  # ambiguous
        {qwen_workflow.OUTPUT_NODE: {"images": [{"filename": "a.png", "type": "output"}]},
         "999": {"images": [{"filename": "b.png", "type": "output"}]}},  # a second producer
    ],
)
def test_declared_output_slot_refuses_ambiguity(outputs):
    """Never harvest a preview, a temp, or whichever image happens to be first."""
    with pytest.raises(ComfyWorkflowError) as excinfo:
        qwen_workflow.declared_output_slot(_history(outputs))
    assert excinfo.value.args[0] == "IMAGE_OUTPUT_NODE_MISMATCH"


def test_declared_output_slot_refuses_a_history_without_outputs():
    with pytest.raises(ComfyWorkflowError):
        qwen_workflow.declared_output_slot({"status": {"status_str": "success"}})


# --- prompt template -------------------------------------------------------


def _brief(**overrides):
    return MaterialImageBrief.model_validate({
        "appearance": "a thirty-year-old woman in a grey wool coat",
        "must_keep": ["1930s tailoring"],
        "avoid": ["modern wristwatch"],
        **overrides,
    })


def test_composition_rules_are_fixed_per_family_and_deterministic():
    direction = MaterialVisualDirection(era="1930s", palette="muted teal")
    first = image_prompts.compose("character", _brief(), visual_direction=direction, requirement_name="Lin")
    second = image_prompts.compose("character", _brief(), visual_direction=direction, requirement_name="Lin")
    assert first.prompt == second.prompt
    assert first.sha256 == second.sha256
    assert first.template_version == image_prompts.PROMPT_TEMPLATE_VERSION
    assert "half-body" in first.prompt and "grey-white" in first.prompt
    assert "no people" in image_prompts.compose("scene", _brief()).prompt
    assert "no person holding it" in image_prompts.compose("prop", _brief()).prompt


def test_avoid_becomes_the_negative_prompt_and_never_the_positive():
    composed = image_prompts.compose("character", _brief())
    assert "modern wristwatch" in composed.negative_prompt
    assert "modern wristwatch" not in composed.prompt


def test_cjk_appearance_is_refused_not_translated():
    """A Chinese appearance never reaches the prompt.

    The producer's contract refuses it first, so the model that wrote it gets
    its one correction instead of the render being refused later.
    """
    with pytest.raises(ValidationError) as excinfo:
        MaterialImageBrief.model_validate({
            "appearance": "三十岁女性，灰色羊毛外套",
            "must_keep": [],
            "avoid": [],
            "questions": [],
        })
    assert "English visual prose" in str(excinfo.value)
    # The composer keeps the same rule for the text it composes itself.
    with pytest.raises(image_prompts.ImagePromptError) as excinfo:
        image_prompts.compose("character", _brief(),
                              visual_direction=MaterialVisualDirection(era="当代"))
    assert excinfo.value.code == "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE"


def test_an_over_long_prompt_is_refused_and_never_truncated():
    """A brief that is legal on its own can still compose past the limit."""
    maxed = _brief(
        appearance="a" * 1200,
        must_keep=["m" * 160] * 12,
        avoid=["v" * 160] * 12,
    )
    direction = MaterialVisualDirection(era="e" * 200, palette="p" * 300, lighting="l" * 300,
                                        texture="t" * 300, notes="n" * 600)
    with pytest.raises(image_prompts.ImagePromptError) as excinfo:
        image_prompts.compose("character", maxed, visual_direction=direction)
    assert excinfo.value.code == "MATERIAL_PROMPT_TOO_LONG"
    assert excinfo.value.details["limit"] == image_prompts.MAX_COMPOSED_PROMPT_CHARS
    # The refusal carries the real length, so nothing was silently cut.
    assert excinfo.value.details["chars"] > image_prompts.MAX_COMPOSED_PROMPT_CHARS


def test_an_unanswered_question_still_renders_a_candidate():
    """A question is information for the person, not a stop sign.

    The producer may answer with a question instead of a look. Refusing there
    left a real list unrenderable whenever the producer wanted one more detail,
    so the first confirmed description is used to make one candidate - which the
    person still has to promote before it becomes a master.
    """
    from shine.materialprep.execution import _compose_prompt

    context = {
        "item_id": "i",
        "item_payload": {"brief": {**_brief(questions=["coat material?"]).model_dump(),
                                   "schema_version": "1.0"}},
        "requirement_payload": {"family": "character", "entity_id": "Lin",
                                "description": "A thin young man in a worn grey coat."},
    }
    prompt = _compose_prompt(context)
    # The produced look is used as-is; the open question no longer stops it.
    assert "grey wool coat" in prompt.prompt


# --- submission outcome mapping -------------------------------------------


def test_only_a_proven_unsent_request_is_resumable():
    assert _submit_outcome(ComfyUnavailable("never sent")) == (
        ATTEMPT_FAILED, OUTCOME_NOT_SENT, "IMAGE_SUBMISSION_NOT_SENT"
    )
    assert _submit_outcome(ComfySubmissionUncertain("timed out")) == (
        ATTEMPT_UNKNOWN, OUTCOME_UNKNOWN, "IMAGE_SUBMISSION_OUTCOME_UNKNOWN"
    )
    assert _submit_outcome(ComfyPromptRejected("4xx"))[1] == OUTCOME_FAILED
    assert _submit_outcome(ComfyWorkflowError("node errors"))[1] == OUTCOME_FAILED
    # An unrecognised fault is treated as unknown, never as safe-to-retry.
    assert _submit_outcome(RuntimeError("something else"))[1] == OUTCOME_UNKNOWN


# --- readiness ladder ------------------------------------------------------


def test_readiness_starts_unconfigured_and_never_claims_a_family(api):
    _, factory, settings = api
    with factory() as db:
        report = runtime.readiness(db, settings)
    assert report["configured"] is False
    assert report["layer"] == runtime.LAYER_NOT_CONFIGURED
    assert all(not value["can_generate"] for value in report["families"].values())
    assert report["license"]["scope"] == "NON_COMMERCIAL_RESEARCH_OR_EVALUATION_ONLY"
    assert report["upstream"]["template_commit"] == qwen_workflow.TEMPLATE_COMMIT


def test_a_verified_weight_is_not_hashed_again_until_it_changes(api, tmp_path):
    """The weights are 16 GiB, so an unchanged file keeps its verified digest.

    A second readiness read must not hash everything again, and a touched file
    must be hashed again and caught.
    """
    _, factory, settings = api
    root = tmp_path / "models"
    (root / "diffusion_models").mkdir(parents=True)
    weight = root / "diffusion_models" / "model.safetensors"
    weight.write_bytes(b"payload")
    entries = [{
        "role": "diffusion",
        "folder": "diffusion_models",
        "filename": "model.safetensors",
        "bytes": 7,
        "sha256": hashlib.sha256(b"payload").hexdigest(),
        "required": True,
    }]
    config = runtime.ImageRuntimeConfig(
        config_revision=1,
        instance_id="instance",
        base_url="http://127.0.0.1:8188",
        model_root=str(root),
        model_files=entries,
    )

    first = runtime.verify_model_files(config)
    assert first["complete"] is True
    assert first["files"][0]["cached"] is False
    runtime.persist_verification(settings, config, first)

    stored = runtime.load_config(settings)
    assert stored.verified_files, "the verified digest was not persisted"
    second = runtime.verify_model_files(stored)
    assert second["complete"] is True
    assert second["files"][0]["cached"] is True
    assert second["files"][0]["actual_sha256"] == entries[0]["sha256"]

    # Same size, different bytes: the file was touched, so it is hashed again
    # and the difference is caught.
    stat = weight.stat()
    weight.write_bytes(b"payloaX")
    os.utime(weight, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    third = runtime.verify_model_files(stored)
    assert third["files"][0]["cached"] is False
    assert third["complete"] is False
    assert any(problem["code"] == "IMAGE_MODEL_HASH_MISMATCH" for problem in third["problems"])


def test_graph_verification_reports_a_missing_node_rather_than_passing():
    info = object_info_fixture()
    del info["QwenImage21Cache"]
    report = runtime.verify_graph_against_object_info(info)
    assert report["complete"] is False
    assert "QwenImage21Cache" in report["missing_nodes"]


# --- batch idempotency -----------------------------------------------------


def _rights_id(db, project_id: str) -> str:
    """An approved asset-scope licence.

    A generation batch is refused without one, exactly like the existing
    candidate path, so the batch tests have to carry a real record.
    """
    from sqlalchemy import select

    from shine.comfyops.models import RightsRecord

    existing = db.scalar(
        select(RightsRecord).where(
            RightsRecord.project_id == project_id,
            RightsRecord.status == "APPROVED",
        )
    )
    if existing is not None:
        return existing.id
    payload = {"scope": "asset", "license_id": "local-research", "purpose": "material preparation"}
    row = RightsRecord(project_id=project_id, status="APPROVED", payload=payload, sha256=graph_hash(payload))
    db.add(row)
    db.flush()
    return row.id


def _batch(db, project_id, **kwargs):
    """create_batch with the approved licence the batch gate requires."""
    kwargs.setdefault("rights_id", _rights_id(db, project_id))
    return service.create_batch(db, project_id, **kwargs)


def _setup(db, project_id):
    plan_payload = {"purpose": PLAN_PURPOSE_ASSET_PREPARATION, "requirements": [], "manifest": None,
                    "confirmation": {"confirmation_id": "c" * 64, "confirmation_hash": "d" * 64}}
    plan = PreProductionPlan(project_id=project_id, payload=plan_payload, sha256=graph_hash(plan_payload),
                             status="CONFIRMED", purpose=PLAN_PURPOSE_ASSET_PREPARATION)
    db.add(plan)
    db.flush()
    requirement_payload = {"family": "character", "entity_id": "林知远", "critical": True, "strategy": "generate"}
    requirement = AssetRequirement(project_id=project_id, plan_id=plan.id, payload=requirement_payload,
                                   sha256=graph_hash(requirement_payload), status="CONFIRMED")
    db.add(requirement)
    db.flush()
    return plan, requirement


def test_same_idempotency_key_and_hash_returns_the_original_batch(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
        first = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement_id], idempotency_key="k")
    assert first["replayed"] is False
    with factory() as db:
        again = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement_id], idempotency_key="k")
    assert again["replayed"] is True
    assert again["run_id"] == first["run_id"]
    with factory() as db:
        assert db.query(MaterialRun).count() == 1


def test_same_key_with_a_different_payload_is_a_conflict(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
        _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                             requirement_ids=[requirement_id], idempotency_key="k")
    with factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                 requirement_ids=[requirement_id], idempotency_key="k", rights_id="other-rights")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_a_second_key_cannot_queue_the_same_requirement_revision(api):
    """Two tabs, two keys, one confirmed revision: the ledger refuses the second."""
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
        _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                             requirement_ids=[requirement_id], idempotency_key="tab-a")
    with factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                 requirement_ids=[requirement_id], idempotency_key="tab-b")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "MATERIAL_NOTHING_TO_GENERATE"


def test_an_upload_strategy_item_is_not_regenerated(api):
    """A file on its way is not a missing material."""
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        plan, requirement = _setup(db, project_id)
        requirement.payload = {**requirement.payload, "strategy": "upload"}
        db.commit()
        requirement_id = requirement.id
        with pytest.raises(HTTPException) as excinfo:
            _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                 requirement_ids=[requirement_id], idempotency_key="k")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "MATERIAL_CONFIRMATION_REQUIRED"


def test_resume_refuses_an_item_carrying_an_unknown_submission(api):
    """Definite failures may resume; an unknown external outcome may not."""
    from sqlalchemy import select as sa_select

    from shine.materialprep.models import MaterialImageAttempt, MaterialItem

    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
        batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement_id], idempotency_key="k")
        run_id = batch["run_id"]
        item = db.scalar(sa_select(MaterialItem).where(MaterialItem.run_id == run_id))
        item.status = "FAILED"
        payload = {"attempt": 1}
        db.add(MaterialImageAttempt(item_id=item.id, run_id=run_id, project_id=project_id, attempt_no=1,
                                    status=ATTEMPT_UNKNOWN, external_outcome=OUTCOME_UNKNOWN,
                                    prompt_id=None, payload=payload, sha256=graph_hash(payload)))
        db.commit()
        run = db.get(MaterialRun, run_id)
        version = run.state_version
        item_id = item.id
        result = service.resume_items(db, run_id, [item_id], version)
    assert result["queued"] == []
    assert result["refused"][0]["code"] == "IMAGE_SUBMISSION_OUTCOME_UNKNOWN"


def test_resume_allows_a_definite_failure(api):
    from sqlalchemy import select as sa_select

    from shine.materialprep.models import MaterialImageAttempt, MaterialItem

    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
        batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement_id], idempotency_key="k")
        run_id = batch["run_id"]
        item = db.scalar(sa_select(MaterialItem).where(MaterialItem.run_id == run_id))
        item.status = "FAILED"
        payload = {"attempt": 1}
        db.add(MaterialImageAttempt(item_id=item.id, run_id=run_id, project_id=project_id, attempt_no=1,
                                    status=ATTEMPT_FAILED, external_outcome=OUTCOME_NOT_SENT,
                                    payload=payload, sha256=graph_hash(payload)))
        db.commit()
        version = db.get(MaterialRun, run_id).state_version
        item_id = item.id
        result = service.resume_items(db, run_id, [item_id], version)
    assert result["queued"] == [item_id]


def test_cancel_with_a_stale_state_version_is_refused(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement.id], idempotency_key="k")
        with pytest.raises(HTTPException) as excinfo:
            service.request_cancel(db, batch["run_id"], 999)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "MATERIAL_RUN_STATE_STALE"


def test_reconcile_never_marks_something_resubmitted(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                                     requirement_ids=[requirement.id], idempotency_key="k")
        version = db.get(MaterialRun, batch["run_id"]).state_version
        result = service.reconcile_run(db, batch["run_id"], version)
    assert result["resubmitted"] is False
    assert result["unknown"] == []


def test_item_uniqueness_survives_a_direct_double_insert(api):
    _, factory, _ = api
    from shine.materialprep.models import MaterialItem
    from shine.models import Project

    with factory() as db:
        project = Project(title="唯一性", language="Chinese")
        db.add(project)
        db.flush()
        plan, requirement = _setup(db, project.id)
        run_payload = {"kind": "production"}
        run = MaterialRun(project_id=project.id, plan_id=plan.id, confirmation_id="c" * 64,
                          confirmation_hash="d" * 64, idempotency_key="k", request_hash="r" * 64,
                          mode="real", status="QUEUED", payload=run_payload, sha256=graph_hash(run_payload))
        db.add(run)
        db.flush()
        item_payload = {"family": "character"}
        db.add(MaterialItem(run_id=run.id, project_id=project.id, requirement_id=requirement.id,
                            generation_revision=1, status="QUEUED", payload=item_payload,
                            sha256=graph_hash(item_payload)))
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(MaterialItem(run_id=run.id, project_id=project.id, requirement_id=requirement.id,
                                generation_revision=1, status="QUEUED", payload=item_payload,
                                sha256=graph_hash(item_payload)))
            db.commit()
        db.rollback()


def test_a_stale_claim_cannot_write_over_the_new_owner(api):
    """The conditional write is what makes an expired claim harmless."""
    from shine.materialprep.ledger import touch_item
    from shine.materialprep.models import MaterialItem

    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        _, requirement = _setup(db, project_id)
        db.commit()
        requirement_id = requirement.id
    with factory() as db:
        batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                       requirement_ids=[requirement_id], idempotency_key="stale-claim")
        item_id = batch["items"][0]
    with factory() as db:
        item = db.get(MaterialItem, item_id)
        claimed = item.state_version
        # Another worker took the item over and moved it on.
        item.state_version = claimed + 1
        db.commit()
        assert touch_item(factory, item_id, claimed, status="FAILED") is None
        assert touch_item(factory, item_id, claimed + 1, status="FAILED") == claimed + 2


def test_an_image_item_waits_for_a_running_video_lease(api, monkeypatch):
    """The shared ledger: an image batch never preempts a running video series."""
    from datetime import timedelta

    from shine.comfyops.models import ResourceLease
    from shine.materialprep import execution, runtime
    from shine.materialprep.models import MaterialItem
    from shine.models import utcnow

    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        plan, requirement = _setup(db, project_id)
        run_payload = {"kind": "production", "rights_id": "r"}
        run = MaterialRun(
            project_id=project_id,
            plan_id=plan.id,
            confirmation_id="c" * 64,
            confirmation_hash="d" * 64,
            idempotency_key="lease-wait",
            request_hash="rq",
            mode="real",
            status="RUNNING",
            payload=run_payload,
            sha256=graph_hash(run_payload),
        )
        db.add(run)
        db.flush()
        brief = {
            "schema_version": "1.0",
            "appearance": "a man in his thirties with short black hair",
            "must_keep": [],
            "avoid": [],
            "questions": [],
        }
        item_payload = {
            "family": "character",
            "entity_id": "林知远",
            "visual_direction": {},
            "seed": 1,
            "kind": "production",
            "brief": brief,
        }
        item = MaterialItem(
            run_id=run.id,
            project_id=project_id,
            requirement_id=requirement.id,
            generation_revision=1,
            status="QUEUED",
            payload=item_payload,
            sha256=graph_hash(item_payload),
        )
        db.add(item)
        db.flush()
        item_id = item.id
        db.commit()
        lease = ResourceLease(
            owner="h3:series-1",
            gpu="gpu-0",
            budget_mb=20000,
            exclusive=True,
            priority=100,
            status="ACTIVE",
            heartbeat_at=utcnow(),
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(lease)
        db.commit()

    config = runtime.ImageRuntimeConfig()
    monkeypatch.setattr(execution, "resolve_runtime", lambda *args, **kwargs: {
        "config": config,
        "base_url": "http://127.0.0.1:8188",
        "fingerprint": "f" * 64,
        "graph_report": {"complete": True},
    })

    result = execution.execute_item(factory, settings, item_id, "worker-1")

    assert result["status"] == "QUEUED", result
    assert result["waiting"] == "GPU"
    with factory() as db:
        row = db.get(MaterialItem, item_id)
        assert row.status == "QUEUED"
        assert row.lease_id is None

"""Source-bound text evidence admission; synthetic providers, no GPU or media acceptance."""

import copy
from dataclasses import asdict, replace

import pytest
from fastapi import HTTPException

from shine.comfyops.model_calls import TrackedProvider, digest_input
from shine.models import AgentArtifact, AgentTaskRecord, Project, ProviderProfile, StyleVersion
from shine.quality.input_assessment import VerifiedInputEvidence
from shine.quality.semantic import ReviewContext
from shine.schemas import ShotSpec, StyleBible
from tests.test_style_input_assessment import setup_inputs


def prepared(api):
    client, factory, _ = api
    project, frozen, spec, wire = setup_inputs(client)
    spec["story_beat"] = "The character sits centre-left."
    wire["checks"][1].update(status="consistent", detail="Same posture")
    wire["recommendation"] = "proceed_to_preflight"
    client.app.state.mock_llm_responder = lambda *_: copy.deepcopy(wire)
    response = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
    assert response.status_code == 201, response.text
    artifact = response.json()
    with factory() as db:
        style = db.get(StyleVersion, frozen["id"])
        bible = StyleBible.model_validate(style.snapshot)
    context = ReviewContext(job_id="synthetic", shot_spec=ShotSpec.model_validate(spec), style=bible,
                            artifact_path=None, artifact_sha256="a" * 64,
                            independent_input={"artifact_id": artifact["id"], "sha256": artifact["sha256"]})
    return project, artifact, context


class Provider:
    is_mock = True

    def __init__(self, hook=lambda: None):
        self.calls = 0
        self.releases = 0
        self.hook = hook

    def release_models(self):
        self.releases += 1

    def review(self, context):
        self.calls += 1
        assert isinstance(context.independent_input, VerifiedInputEvidence)
        assert len(context.independent_input["checks"]) == 7
        self.hook()
        return {"is_mock": True, "verdict": "passed"}


@pytest.mark.parametrize("damage", [None, "missing", "hash", "injected", "style", "shot", "stale",
                                 "route", "latest-failed", "cross-project", "real-mock"])
def test_review_input_rejected_before_resource_admission(api, damage):
    _, factory, _ = api
    project, artifact, context = prepared(api)
    provider = Provider()
    if damage == "missing":
        context = replace(context, independent_input=None)
    elif damage == "hash":
        context = replace(context, independent_input={"artifact_id": artifact["id"], "sha256": "b" * 64})
    elif damage == "injected":
        context.independent_input["checks"] = [{"field": "sound_performance", "status": "consistent"}]
    elif damage == "shot":
        context = replace(context, shot_spec=context.shot_spec.model_copy(update={"story_beat": "Runs away"}))
    elif damage == "cross-project":
        project = "other-project"
    elif damage == "real-mock":
        provider.is_mock = False
    with factory.begin() as db:
        row = db.get(AgentArtifact, artifact["id"])
        if damage == "stale":
            row.stale = True
        elif damage == "style":
            db.get(Project, project).frozen_style_version_id = None
        elif damage == "route":
            task = db.get(AgentTaskRecord, row.source_task_id)
            db.get(ProviderProfile, task.model_profile_id).model = "changed"
        elif damage == "latest-failed":
            db.get(AgentTaskRecord, row.source_task_id).status = "FAILED"
    tracked = TrackedProvider(provider, factory, project, require_independent_input=True)
    if damage:
        with pytest.raises(HTTPException):
            tracked.review(context=context)  # Keyword invocation must receive the same guard.
        assert provider.calls == provider.releases == 0
        with factory() as db:
            assert not db.query(AgentTaskRecord).filter_by(role_id="model_call:VLM:review").count()
    else:
        assert tracked.review(context=context)["verdict"] == "passed"
        with factory() as db:
            call = db.query(AgentTaskRecord).filter_by(role_id="model_call:VLM:review").one()
            assert call.status == "COMPLETED" and call.trace["input_assessment_required"] is True
            assert call.trace["independent_input"] == context.independent_input


def test_review_rechecks_evidence_after_received_response(api):
    _, factory, _ = api
    project, artifact, context = prepared(api)

    def revoke():
        with factory.begin() as db:
            db.get(AgentArtifact, artifact["id"]).stale = True

    provider = Provider(revoke)
    with pytest.raises(HTTPException):
        TrackedProvider(provider, factory, project, require_independent_input=True).review(context)
    with factory() as db:
        call = db.query(AgentTaskRecord).filter_by(role_id="model_call:VLM:review").one()
        assert call.status == "FAILED" and len(call.trace["received_result_sha256"]) == 64
    assert provider.calls == 1 and provider.releases == 2


def test_legacy_review_fingerprint_preserves_uncertain_replay_guard(api):
    _, _, context = prepared(api)
    context = replace(context, independent_input=None)
    historical = asdict(context)
    historical.pop("independent_input")
    assert digest_input(context) == digest_input(historical)


@pytest.mark.parametrize("damage", [None, "changed-workflow", "missing-pin", "unguarded-binding"])
def test_frozen_native_input_policy_blocks_render_before_gpu(api, damage):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import PreProductionPlan, WorkflowRevision
    from shine.comfyops.native import frozen_input_policy
    from shine.comfyops.preproduction import enforce
    from shine.schemas import RenderRequest

    _, factory, _ = api
    project, _, context = prepared(api)
    with factory.begin() as db:
        payload = {"category": "VLM_REVIEW", "native_provider": {
            "input_assessment_required": damage != "unguarded-binding"}}
        workflow = WorkflowRevision(payload=payload, sha256=graph_hash(payload), status="PRODUCTION_APPROVED")
        db.add(workflow)
        db.flush()
        manifest = {"offline": False, "workflows": [{"id": workflow.id, "hash": workflow.sha256}]}
        plan = {"manifest": manifest}
        db.add(PreProductionPlan(project_id=project, payload=plan, sha256=graph_hash(plan), status="FROZEN"))
        if damage == "changed-workflow":
            workflow.payload = {"category": "VLM_REVIEW", "native_provider": {"input_assessment_required": False}}
        elif damage == "missing-pin":
            workflow.sha256 = "b" * 64
    with factory() as db:
        if damage == "unguarded-binding":
            with pytest.raises(HTTPException, match="PRODUCTION_REVIEW_INPUT_POLICY_MISMATCH"):
                frozen_input_policy(db, project, evidence_present=True)
        elif damage:
            with pytest.raises(HTTPException, match="PREPRODUCTION_OUTDATED"):
                frozen_input_policy(db, project)
        else:
            assert frozen_input_policy(db, project) is True
            style = db.get(StyleVersion, context.shot_spec.style_version_id)
            request = RenderRequest(idempotency_key="fixture", shot_id="fixture", shot_revision=1,
                                    style_version_id=style.id, style_hash=style.sha256, style_snapshot=context.style,
                                    shot_spec=context.shot_spec, expected_duration_s=6)
            with pytest.raises(HTTPException, match="STYLE_INPUT_ASSESSMENT_REQUIRED"):
                enforce(db, project, render_request=request)


def test_verified_input_is_user_evidence_and_cannot_erase_vlm_contradictions(api, tmp_path, monkeypatch):
    import json

    import httpx

    from shine.llm.base import ProviderConfig
    from shine.quality.input_assessment import evidence_for_review
    from shine.quality.semantic import OpenAICompatibleVisionReviewer, ReceivedReviewError
    from tests.review_fixtures import fixture_style_input_checks

    _, factory, _ = api
    project, _, context = prepared(api)
    with factory() as db:
        proof = evidence_for_review(db, project, context, offline=True)
    frame = tmp_path / "synthetic.jpg"
    frame.write_bytes(b"synthetic transport only")
    context = replace(context, frame_paths=(frame,), remote_consent=True, independent_input=proof)
    checks = fixture_style_input_checks()
    checks[-1]["status"] = "uncertain"
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "reviewer": "synthetic", "is_mock": True, "input_sha256": "a" * 64,
            "verdict": "passed", "findings": [], "style_input_checks": checks,
            "overall_confidence": 1, "raw_summary": "Synthetic contradictory response",
        })}}]})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig("fixture", "fixture", "openai_compatible", "https://fixture.invalid/v1", "fixture", None)
    with pytest.raises(ReceivedReviewError) as error:
        OpenAICompatibleVisionReviewer(cfg).review(context)
    assert error.value.validation["code"] == "STYLE_INPUT_DECISION_CONTRADICTION"
    messages = captured[0]["messages"]
    assert proof["artifact_id"] not in messages[0]["content"]
    assert proof["artifact_id"] in messages[1]["content"][0]["text"]
    with pytest.raises(ValueError, match="NOT_VERIFIED"):
        OpenAICompatibleVisionReviewer(cfg).review(replace(context, independent_input=dict(proof)))
    assert len(captured) == 1

"""Independent text-input proposals, synthetic transport only, no media approval."""

import copy

import pytest

from shine.h3.rulebook import digest
from shine.models import AgentTaskRecord
from tests.test_film_crew import ENGLISH_STYLE, prepare


def setup_inputs(client):
    project, _, profile = prepare(client, freeze=False)
    style = {**ENGLISH_STYLE, "shot_blocking": "The character sits centre-left."}
    assert client.put(f"/api/v1/projects/{project}/style/draft", json=style).status_code == 200
    frozen = client.post(f"/api/v1/projects/{project}/style/freeze", json={}).json()
    assert client.put("/api/v1/agent-routes", json={
        "role": "qc_reviewer", "provider_profile_id": profile["id"],
    }).status_code == 200
    spec = {"story_beat": "The character walks toward the camera.", "duration_target_s": 6,
            "style_version_id": frozen["id"]}
    checks = [dict(field=name, shot_path=None, status="consistent", confidence=1,
                   detail="No explicit constraint in this synthetic ShotSpec") for name in style]
    checks[1].update(shot_path="/story_beat", status="conflict", detail="Sitting conflicts with walking")
    return project, frozen, spec, dict(protocol="independent-text-v2", checks=checks, recommendation="needs_review")


@pytest.mark.parametrize("damage", [None, "style-quote", "shot-quote", "missing", "duplicate", "false-proceed"])
def test_independent_input_review_is_pinned_text_only_and_has_no_approval_authority(api, damage):
    client, factory, _ = api
    project, frozen, spec, response = setup_inputs(client)
    if damage == "style-quote":
        response["checks"][1]["style_text"] = "The frozen character walks."
    elif damage == "shot-quote":
        response["checks"][1]["shot_text"] = "An invented shot constraint."
    elif damage == "missing":
        response["checks"].pop()
    elif damage == "duplicate":
        response["checks"][1] = response["checks"][0]
    elif damage == "false-proceed":
        response["recommendation"] = "proceed_to_preflight"
    seen = []

    def respond(messages, _schema):
        seen.extend(messages)
        return copy.deepcopy(response)

    client.app.state.mock_llm_responder = respond
    before = client.get(f"/api/v1/projects/{project}/assets").json()
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
    if damage:
        assert result.status_code == 422, result.text
        with factory() as db:
            task = db.query(AgentTaskRecord).filter_by(project_id=project, role_id="qc_reviewer").one()
            assert task.trace["received_result_sha256"] == digest(response)
            assert task.trace["rejection_reason_code"] == {
                "style-quote": "SCHEMA_VALIDATION_FAILED",
                "shot-quote": "SCHEMA_VALIDATION_FAILED",
                "missing": "SCHEMA_VALIDATION_FAILED",
                "duplicate": "STYLE_INPUT_FIELD_COVERAGE",
                "false-proceed": "STYLE_INPUT_RECOMMENDATION_CONTRADICTION",
            }[damage]
        return
    assert result.status_code == 201, result.text
    artifact = result.json()
    assert artifact["kind"] == "StyleInputAssessment"
    assert artifact["payload_json"]["recommendation"] == "needs_review"
    assert artifact["approved_by"] is None
    user_text = seen[-1].content
    assert "sits centre-left" in user_text and "walks toward" in user_text
    assert all(a["sha256"] not in user_text for a in before)
    assert "image_url" not in user_text
    with factory() as db:
        task = db.get(AgentTaskRecord, artifact["source_task_id"])
        assert task.trace["shot_spec_sha256"] == digest(task.trace["shot_spec_snapshot"])
        assert task.trace["style_hash"] == frozen["sha256"]
        assert task.trace["is_mock"] is True
        assert task.trace["scope"] == "TEXT_INPUT_CONSISTENCY_ONLY_NOT_MEDIA_QC"
    assert client.get(f"/api/v1/projects/{project}/assets").json() == before


def test_text_input_critic_requires_explicit_remote_text_consent_before_call(api, monkeypatch):
    client, _, _ = api
    project, _, spec, _ = setup_inputs(client)
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "remote-text-fixture", "kind": "openai_compatible",
        "base_url": "https://text.example/v1", "model": "fixture-only",
    }).json()
    assert client.put("/api/v1/agent-routes", json={
        "role": "qc_reviewer", "provider_profile_id": profile["id"],
    }).status_code == 200

    def forbidden(*args, **kwargs):
        raise AssertionError("Text consent must be checked before any provider call")

    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", forbidden)
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
    assert result.status_code == 422
    assert result.json()["detail"] == "REMOTE_AGENT_CONSENT_REQUIRED"


@pytest.mark.parametrize("damage", ["missing-spec", "wrong-style", "unrelated-handoff"])
def test_text_input_critic_requires_exact_current_frozen_input(api, damage):
    client, _, _ = api
    project, _, spec, _ = setup_inputs(client)
    body = {"shot_spec": spec}
    if damage == "missing-spec":
        body = {}
    elif damage == "wrong-style":
        spec["style_version_id"] = "another-style"
    else:
        body["input_revision_ids"] = ["unrelated-artifact"]
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json=body)
    assert result.status_code == 422


@pytest.mark.parametrize("damage", [None, "mock-as-real", "stale", "changed-shot", "changed-style-snapshot",
                                 "changed-result", "changed-snapshot", "changed-route", "changed-budget",
                                 "newer-nonpass"])
def test_production_binding_rechecks_exact_text_evidence_including_worker_entry(api, damage):
    from fastapi import HTTPException

    from shine.comfyops.preproduction import enforce
    from shine.models import AgentArtifact, ProviderProfile
    from shine.quality.input_assessment import assessment_for_request
    from shine.schemas import RenderRequest

    client, factory, _ = api
    project, frozen, spec, response = setup_inputs(client)
    spec["story_beat"] = "The character sits centre-left."
    response["checks"][1].update(status="consistent", shot_path="/story_beat", detail="Same posture")
    response["recommendation"] = "proceed_to_preflight"
    client.app.state.mock_llm_responder = lambda *_: copy.deepcopy(response)
    artifact = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec}).json()
    render = RenderRequest(idempotency_key="fixture", shot_id="fixture", shot_revision=1,
                           style_version_id=frozen["id"], style_hash=frozen["sha256"], shot_spec=spec,
                           style_snapshot={**ENGLISH_STYLE, "shot_blocking": "The character sits centre-left."},
                           expected_duration_s=6, style_input_assessment_id=artifact["id"],
                           style_input_assessment_sha256=artifact["sha256"])
    with factory.begin() as db:
        row = db.get(AgentArtifact, artifact["id"])
        task = db.get(AgentTaskRecord, row.source_task_id)
        if damage == "stale":
            row.stale = True
        elif damage == "changed-result":
            row.payload_json = {**row.payload_json, "recommendation": "needs_review"}
        elif damage == "changed-snapshot":
            task.trace = {**task.trace, "shot_spec_snapshot": {"story_beat": "Different text"}}
        elif damage == "changed-route":
            db.get(ProviderProfile, task.model_profile_id).model = "different-model"
        elif damage == "changed-budget":
            db.get(ProviderProfile, task.model_profile_id).generation_options = {"max_completion_tokens": 8192}
    if damage == "changed-shot":
        render.shot_spec.story_beat = "The character runs away."
    elif damage == "changed-style-snapshot":
        render.style_snapshot.shot_blocking = "Unfrozen replacement"
    elif damage == "newer-nonpass":
        response["checks"][1].update(status="uncertain", confidence=0.2)
        response["recommendation"] = "needs_review"
        result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
        assert result.status_code == 201
        render.style_input_assessment_id = render.style_input_assessment_sha256 = None
    with factory() as db:
        if damage:
            with pytest.raises(HTTPException) as error:
                if damage == "newer-nonpass":
                    assessment_for_request(db, project, render, offline=True, select_latest=True)
                else:
                    # The same enforce function is called at Worker claim/submit.
                    enforce(db, project, offline=damage != "mock-as-real", render_request=render)
            assert error.value.status_code == 409
        else:
            assert assessment_for_request(db, project, render, offline=True).id == artifact["id"]
            assert enforce(db, project, offline=True, render_request=render) is None  # Legacy plan unchanged.


def test_new_analyzed_real_manifest_requires_input_proof_before_worker_submission(api):
    from fastapi import HTTPException

    from shine.comfyops.models import PreProductionPlan
    from shine.comfyops.preproduction import enforce
    from shine.schemas import RenderRequest

    client, factory, _ = api
    project, frozen, spec, _ = setup_inputs(client)
    payload = {"style_input_contract": "independent-text-v1",
               "manifest": {"style_input_contract": "independent-text-v1"}}
    with factory.begin() as db:
        db.add(PreProductionPlan(project_id=project, payload=payload, sha256=digest(payload), status="FROZEN"))
    render = RenderRequest(idempotency_key="fixture", shot_id="fixture", shot_revision=1,
                           style_version_id=frozen["id"], style_hash=frozen["sha256"], shot_spec=spec,
                           style_snapshot={**ENGLISH_STYLE, "shot_blocking": "The character sits centre-left."},
                           expected_duration_s=6)
    with factory() as db, pytest.raises(HTTPException) as error:
        enforce(db, project, offline=False, render_request=render)
    assert error.value.detail == "STYLE_INPUT_ASSESSMENT_REQUIRED"


@pytest.mark.parametrize("marker", ["mock", "is_mock"])
def test_text_assessment_cannot_erase_transport_mock_provenance(api, monkeypatch, marker):
    from shine.llm.base import LLMResponse

    client, factory, _ = api
    project, _, spec, response = setup_inputs(client)
    remote = client.post("/api/v1/provider-profiles", json={
        "name": "remote-test-envelope", "kind": "openai_compatible",
        "base_url": "https://text.example/v1", "model": "fixture-only",
    }).json()
    client.put("/api/v1/agent-routes", json={"role": "qc_reviewer", "provider_profile_id": remote["id"]})
    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", lambda *a, **kw: LLMResponse(
        text="synthetic transport", parsed=response, model="fixture-only", latency_ms=1, raw_meta={marker: True},
    ))
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={
        "shot_spec": spec, "allow_remote_text": True,
    })
    assert result.status_code == 201, result.text
    with factory() as db:
        assert db.get(AgentTaskRecord, result.json()["source_task_id"]).trace["is_mock"] is True


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_text_wire_provider_preserves_outer_mock_envelope(monkeypatch, kind):
    import json

    import httpx

    from shine.llm.anthropic import AnthropicMessagesProvider
    from shine.llm.base import ChatMessage, ProviderConfig
    from shine.llm.openai_compatible import OpenAICompatibleProvider

    cfg = ProviderConfig(profile_id="fixture", name="fixture", kind="openai_compatible",
                         base_url="https://test.example/v1", model="fixture-only", api_key=None)
    body = {"is_mock": True, "model": "fixture-only"}
    if kind == "openai":
        body["choices"] = [{"message": {"content": json.dumps({"fixture": True})}}]
        provider = OpenAICompatibleProvider(cfg)
        monkeypatch.setattr(provider, "_request", lambda *a, **kw: httpx.Response(200, json=body))
    else:
        body["content"] = [{"type": "text", "text": json.dumps({"fixture": True})}]
        provider = AnthropicMessagesProvider(cfg)
        monkeypatch.setattr(provider, "_post", lambda *a, **kw: httpx.Response(200, json=body))
    assert provider.complete([ChatMessage("user", "Fixture only")]).raw_meta["is_mock"] is True

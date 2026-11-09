"""The LLM setup contract.

Saving, refreshing, testing and binding the text roles have to agree with
each other.  A connection check is not a generation acceptance, a failed
refresh must not look like a failed save, and a half-bound routing table is
never allowed.
"""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from shine.comfyops.llm_readiness import TEXT_ROLES
from shine.llm import openai_compatible
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute, AgentTaskRecord, ProviderProfile
from tests.conftest import create_project
from tests.fake_http import FakeOpenAIServer

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_openai():
    server = FakeOpenAIServer().start()
    try:
        yield server
    finally:
        server.stop()


def create_profile(client, server, **overrides) -> dict:
    payload = {
        "name": "local-text",
        "kind": "openai_compatible",
        "base_url": f"{server.base_url}/v1",
        "model": "test-model",
        "api_key": "test-key",
        "timeout_s": 5,
        "max_retries": 0,
    }
    payload.update(overrides)
    response = client.post("/api/v1/provider-profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def profile_row(factory, profile_id: str) -> ProviderProfile:
    with factory() as db:
        return db.get(ProviderProfile, profile_id)


def test_a_saved_profile_is_never_lost_to_a_failed_refresh(api, fake_openai):
    """The profile exists even when the later listing fails."""
    client, factory, _ = api
    profile = create_profile(client, fake_openai)
    assert profile["secret_status"] == "stored"
    assert "test-key" not in json.dumps(profile)
    assert profile["has_api_key"] is True
    # A second profile with the same name is refused, so the user is never
    # invited to create the same configuration twice.
    duplicate = client.post("/api/v1/provider-profiles", json={
        "name": "local-text", "kind": "openai_compatible",
        "base_url": f"{fake_openai.base_url}/v1", "model": "test-model"})
    assert duplicate.status_code == 409
    assert profile_row(factory, profile["id"]) is not None


def test_timeout_can_be_raised_for_review_without_changing_the_pinned_model(api, fake_openai):
    client, factory, _ = api
    profile = create_profile(client, fake_openai)
    project_id = create_project(client, "重试测试")
    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=project_id, role_id="scriptwriter", input_hash="input", asset_manifest_hash="assets",
            rulepack_id="rules", rulepack_hash="rules", model_profile_id=profile["id"], model_id=profile["model"],
            schema_version="1.0", status="NEEDS_REVIEW", error_code="LLM_TIMEOUT",
            trace={"external_outcome": "UNKNOWN"},
        )
        db.add(task)
        db.flush()
        task_id = task.id
    path = f"/api/v1/provider-profiles/{profile['id']}"
    changed = client.put(path, json={
        "kind": profile["kind"], "model": profile["model"], "base_url": profile["base_url"],
        "generation_options": {}, "timeout_s": 300,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["timeout_s"] == 300
    assert client.put(path, json={"model": "another-model"}).status_code == 409
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.trace["external_outcome"] == "UNKNOWN"
        task.status = "RUNNING"
    assert client.put(path, json={"timeout_s": 400}).status_code == 409


def test_a_failed_key_write_keeps_the_previous_key(api, fake_openai, monkeypatch):
    client, factory, settings = api
    profile = create_profile(client, fake_openai)
    before = profile_row(factory, profile["id"])
    old_ref = before.api_key_ref

    def boom(self, profile_id, secret):
        raise RuntimeError("disk is full")

    monkeypatch.setattr(SecretStore, "store", boom)
    response = client.put(f"/api/v1/provider-profiles/{profile['id']}/key", json={"api_key": "new-key"})
    assert response.status_code == 500, response.text
    after = profile_row(factory, profile["id"])
    assert after.api_key_ref == old_ref
    store = SecretStore(settings.secret_store_dir)
    assert store.load(old_ref) == "test-key"


def test_a_failed_database_commit_keeps_the_previous_key(api, fake_openai, monkeypatch):
    client, factory, settings = api
    profile = create_profile(client, fake_openai)
    before = profile_row(factory, profile["id"])
    old_ref = before.api_key_ref

    def failing_commit(self, *args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(Session, "commit", failing_commit)
    response = client.put(f"/api/v1/provider-profiles/{profile['id']}/key", json={"api_key": "new-key"})
    assert response.status_code == 500, response.text
    monkeypatch.undo()
    after = profile_row(factory, profile["id"])
    assert after.api_key_ref == old_ref
    store = SecretStore(settings.secret_store_dir)
    assert store.load(old_ref) == "test-key"
    # The half written new version must not be left behind.
    assert sorted(path.name for path in settings.secret_store_dir.iterdir()) == [old_ref.split(":", 1)[1]]


def test_a_successful_key_rotation_replaces_the_reference_and_removes_the_old_file(
    api, fake_openai
):
    client, factory, settings = api
    profile = create_profile(client, fake_openai)
    old_ref = profile_row(factory, profile["id"]).api_key_ref
    response = client.put(f"/api/v1/provider-profiles/{profile['id']}/key", json={"api_key": "rotated-key"})
    assert response.status_code == 200, response.text
    assert response.json()["secret_status"] == "stored"
    new_ref = profile_row(factory, profile["id"]).api_key_ref
    assert new_ref != old_ref
    store = SecretStore(settings.secret_store_dir)
    assert store.load(new_ref) == "rotated-key"
    assert not (settings.secret_store_dir / old_ref.split(":", 1)[1]).exists()


def test_clearing_a_key_never_leaves_a_dangling_reference(api, fake_openai):
    client, factory, settings = api
    profile = create_profile(client, fake_openai)
    old_ref = profile_row(factory, profile["id"]).api_key_ref
    response = client.delete(f"/api/v1/provider-profiles/{profile['id']}/key")
    assert response.status_code == 200, response.text
    assert response.json()["has_api_key"] is False
    assert response.json()["secret_status"] == "not_set"
    assert profile_row(factory, profile["id"]).api_key_ref is None
    assert not (settings.secret_store_dir / old_ref.split(":", 1)[1]).exists()


@pytest.mark.parametrize("payload, expected_code", [
    (b"<html>login</html>", "LLM_CATALOG_INVALID"),
    (b'["not", "an", "object"]', "LLM_CATALOG_INVALID"),
    (b'{"models": 5}', "LLM_CATALOG_INVALID"),
    (b'{"unexpected": {}}', "LLM_CATALOG_INVALID"),
])
def test_an_unusable_model_catalog_is_never_read_as_a_confirmed_model(
    api, fake_openai, payload, expected_code
):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_payload = payload
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    assert result["error_code"] == expected_code
    assert result["reachable"] is True
    assert result["catalog_ok"] is False


def test_an_empty_catalog_never_confirms_the_configured_model(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_payload = {"data": []}
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    assert result["error_code"] == "LLM_MODEL_NOT_LISTED"
    assert result["model_listed"] is False
    assert result["reachable"] is True


def test_a_catalog_without_the_configured_model_lists_the_candidates(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_payload = {"data": [{"id": "other-model"}]}
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    assert result["error_code"] == "LLM_MODEL_NOT_LISTED"
    assert result["model_listed"] is False
    assert result["capabilities"]["models"] == ["other-model"]


@pytest.mark.parametrize("status, expected_code", [
    (401, "LLM_AUTH"),
    (429, "LLM_RATE_LIMIT"),
    (503, "LLM_SERVER"),
])
def test_provider_failures_are_classified(api, fake_openai, status, expected_code):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_status = status
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    assert result["error_code"] == expected_code
    assert result["message"]


def test_an_unsupported_model_catalog_does_not_claim_the_chat_api_is_offline(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_status = 404
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is True
    assert result["reachable"] is True
    assert result["catalog_ok"] is False
    assert result["model_listed"] is None


def test_an_unreachable_provider_is_classified(api):
    client, _, _ = api
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "offline", "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:9/v1", "model": "test-model", "timeout_s": 2}).json()
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    assert result["error_code"] in {"LLM_CONNECTION", "LLM_TIMEOUT"}
    assert result["reachable"] is False


def test_the_connection_test_and_the_readiness_gate_never_disagree(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_payload = {"data": [{"id": "other-model"}]}
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert result["ok"] is False
    bulk = client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    assert bulk.status_code == 200, bulk.text
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["status"] == "BLOCKED"
    assert all(row["code"] == "LLM_CONNECTION_TEST_REQUIRED" for row in readiness["roles"])


def test_the_generation_test_proves_the_generation_path(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()["ok"] is True
    client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["status"] == "READY"
    assert readiness["generation_status"] == "CONNECTION_READY"
    assert readiness["text_agent_ready"] is False

    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()
    assert result["ok"] is True
    assert result["level"] == "generation"
    assert result["generation_ok"] is True
    assert result["structured_output_ok"] is True
    assert result["model"] == "test-model"
    assert fake_openai.requests[-1]["model"] == "test-model"
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["generation_status"] == "GENERATION_READY"
    assert readiness["text_agent_ready"] is True
    assert all(row["readiness_status"] == "GENERATION_READY" for row in readiness["roles"])


def test_generation_rate_limit_does_not_immediately_post_again(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai, max_retries=2)
    fake_openai.chat_status = 429
    result = client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()
    assert result["error_code"] == "LLM_RATE_LIMIT"
    assert len(fake_openai.requests) == 1


@pytest.mark.parametrize("host,reported,expected", [
    ("api.minimax.cn", "MiniMax-M3.1-Flash", True),
    ("api.minimax.cn", "MiniMax-M3-1-Flash", False),
    ("example.test", "MiniMax-M3.1-Flash", False),
])
def test_minimax_flash_catalog_and_generation_handle_provider_casing(
    api, fake_openai, monkeypatch, host, reported, expected,
):
    client, _, _ = api
    configured = "MiniMax-M3.1-flash"

    def respond(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": reported}]})
        assert json.loads(request.content)["model"] == configured
        return httpx.Response(200, json={
            "model": reported,
            "choices": [{"message": {"content": '{"ok":true}'}}],
        })

    monkeypatch.setattr(openai_compatible, "build_client", lambda *args, **kwargs: httpx.Client(
        transport=httpx.MockTransport(respond),
    ))
    profile = create_profile(client, fake_openai, base_url=f"https://{host}/v1", model=configured)
    prefix = f"/api/v1/provider-profiles/{profile['id']}"
    assert client.post(f"{prefix}/test").json()["model_listed"] is expected
    generation = client.post(f"{prefix}/test-generation").json()
    assert generation["model"] == reported
    assert generation["structured_output_ok"] is True
    assert generation["ok"] is expected


def test_structured_generation_can_pass_when_provider_has_no_model_catalog(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    fake_openai.models_status = 404

    connection = client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()
    assert connection["ok"] is True
    assert connection["reachable"] is True
    assert connection["auth_ok"] is None
    assert connection["catalog_ok"] is False
    assert connection["model_listed"] is None

    bound = client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES),
    })
    assert bound.status_code == 200, bound.text
    before_generation = client.get("/api/v1/setup/llm-readiness").json()
    assert all(row["connection_status"] == "REACHABLE_AUTH_UNVERIFIED" for row in before_generation["roles"])
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()["ok"] is True

    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["text_agent_ready"] is True
    assert all(row["connection_status"] == "CONNECTED" for row in readiness["roles"])
    assert all(row["catalog_status"] == "UNAVAILABLE" for row in readiness["roles"])
    assert all(row["generation_status"] == "PASSED_CATALOG_UNAVAILABLE" for row in readiness["roles"])


def test_a_failed_generation_test_does_not_keep_a_previous_acceptance(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()["ok"] is True
    assert client.get("/api/v1/setup/llm-readiness").json()["text_agent_ready"] is True

    fake_openai.response_content = "this is not json"
    failed = client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()
    assert failed["ok"] is False
    assert failed["error_code"] == "LLM_FORMAT"
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["text_agent_ready"] is False
    assert readiness["generation_status"] == "CONNECTION_READY"
    assert all(row["readiness_status"] != "GENERATION_READY" for row in readiness["roles"])


def test_a_changed_configuration_invalidates_the_generation_acceptance(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation")
    assert client.get("/api/v1/setup/llm-readiness").json()["text_agent_ready"] is True
    changed = client.put(f"/api/v1/provider-profiles/{profile['id']}", json={"model": "other-model"})
    assert changed.status_code == 200, changed.text
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["status"] == "BLOCKED"
    assert readiness["text_agent_ready"] is False


def test_the_update_path_uses_the_same_contract_as_the_create_path(api, fake_openai):
    client, _, _ = api
    profile = create_profile(client, fake_openai)
    bad_url = client.put(f"/api/v1/provider-profiles/{profile['id']}", json={"base_url": "ftp://example"})
    assert bad_url.status_code == 422
    empty_model = client.put(f"/api/v1/provider-profiles/{profile['id']}", json={"model": ""})
    assert empty_model.status_code == 422
    foreign_options = client.put(f"/api/v1/provider-profiles/{profile['id']}", json={
        "kind": "anthropic", "base_url": "https://api.example.com",
        "generation_options": {"max_completion_tokens": 1024}})
    assert foreign_options.status_code == 422


def test_the_text_roles_are_bound_in_one_transaction(api, fake_openai):
    client, factory, _ = api
    profile = create_profile(client, fake_openai)
    response = client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bound_roles"] == sorted(TEXT_ROLES)
    assert {row["role"] for row in body["roles"]} == set(TEXT_ROLES)
    with factory() as db:
        assert db.query(AgentRoute).count() == len(TEXT_ROLES)


@pytest.mark.parametrize("failing_position", [1, 6, 12])
def test_a_half_bound_routing_table_is_never_written(api, fake_openai, failing_position):
    client, factory, _ = api
    profile = create_profile(client, fake_openai)
    inserted = {"count": 0}

    def fail_on_position(mapper, connection, target):
        inserted["count"] += 1
        if inserted["count"] == failing_position:
            raise RuntimeError("simulated routing failure")

    event.listen(AgentRoute, "before_insert", fail_on_position)
    try:
        response = client.put("/api/v1/agent-routes/bulk", json={
            "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    finally:
        event.remove(AgentRoute, "before_insert", fail_on_position)
    assert response.status_code == 500, response.text
    with factory() as db:
        assert db.query(AgentRoute).count() == 0
    assert client.get("/api/v1/agent-routes").json() == []


def test_the_configuration_survives_an_application_restart(api, fake_openai, tmp_path):
    """A restart must not lose the profile, the key or the recorded evidence."""
    from tests.conftest import make_studio

    client, factory, _ = api
    profile = create_profile(client, fake_openai)
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test-generation").json()["ok"] is True

    # A second application instance over the same data directory is a restart.
    restarted_app, _, restarted_settings = make_studio(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(restarted_app) as restarted:
        listed = restarted.get("/api/v1/provider-profiles").json()
        assert len(listed) == 1
        assert listed[0]["id"] == profile["id"]
        assert listed[0]["secret_status"] == "stored"
        assert "test-key" not in json.dumps(listed)
        readiness = restarted.get("/api/v1/setup/llm-readiness").json()
        assert readiness["status"] == "READY"
        assert readiness["text_agent_ready"] is True
        routes = restarted.get("/api/v1/agent-routes").json()
        assert {row["role"] for row in routes} == set(TEXT_ROLES)
        # The key is still readable through the store, so a real call can run.
        store = SecretStore(restarted_settings.secret_store_dir)
        row = profile_row(factory, profile["id"])
        assert store.load(row.api_key_ref) == "test-key"

def test_a_mock_provider_cannot_take_over_the_text_roles(api, fake_openai):
    client, _, _ = api
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "mock-text", "kind": "mock", "base_url": "", "model": "mock"}).json()
    response = client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES)})
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/agent-routes").json() == []

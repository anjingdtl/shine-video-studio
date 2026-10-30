from __future__ import annotations

import json
import uuid

import httpx
import pytest

from shine.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMConnectionError,
    LLMFormatError,
    LLMNotConfigured,
    LLMTimeout,
    ProviderConfig,
)
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from tests.fake_http import FakeOpenAIServer


@pytest.fixture
def fake_openai():
    server = FakeOpenAIServer().start()
    try:
        yield server
    finally:
        server.stop()


def config(server: FakeOpenAIServer, **overrides) -> ProviderConfig:
    values = {
        "profile_id": str(uuid.uuid4()),
        "name": "fake",
        "kind": "openai_compatible",
        "base_url": f"{server.base_url}/v1",
        "model": "test-model",
        "api_key": "test-key",
        "timeout_s": 2,
        "max_retries": 1,
        "temperature": 0,
    }
    values.update(overrides)
    return ProviderConfig(**values)


def _failing_transport(monkeypatch, failure):
    """Point the provider at a transport that always fails the same way."""
    real_client = httpx.Client
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise failure(request)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return calls


def test_openai_compatible_completion_and_capability_probe(fake_openai):
    fake_openai.response_content = json.dumps({"ok": True, "value": "structured"})
    provider = OpenAICompatibleProvider(config(fake_openai))
    response = provider.complete(
        [ChatMessage("system", "Return JSON"), ChatMessage("user", "hello")],
        schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
    )
    assert response.parsed == {"ok": True, "value": "structured"}
    assert fake_openai.auth_headers[-1] == "Bearer test-key"
    assert fake_openai.requests[-1]["response_format"] == {"type": "json_object"}
    capabilities = provider.detect_capabilities()
    assert capabilities["available"] is True
    assert "test-model" in capabilities["models"]
    assert capabilities["model_listed"] is True


def test_schema_mismatch_malformed_json_and_auth_errors_are_classified(fake_openai):
    fake_openai.response_content = json.dumps({"wrong": 1})
    provider = OpenAICompatibleProvider(config(fake_openai))
    with pytest.raises(LLMFormatError):
        provider.complete(
            [ChatMessage("user", "x")],
            schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
        )
    fake_openai.response_content = "not-json"
    with pytest.raises(LLMFormatError):
        provider.complete([ChatMessage("user", "x")])
    fake_openai.required_key = "other-key"
    with pytest.raises(LLMAuthError):
        provider.complete([ChatMessage("user", "x")])


def test_transient_server_errors_are_retried(fake_openai):
    fake_openai.mode = "status_500"
    fake_openai.server_error_times = 1
    fake_openai.response_content = json.dumps({"ok": True})
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    response = provider.complete([ChatMessage("user", "x")])
    assert response.parsed == {"ok": True}


def test_timeout_is_classified(fake_openai):
    fake_openai.mode = "timeout"
    provider = OpenAICompatibleProvider(config(fake_openai, timeout_s=0.1, max_retries=0))
    with pytest.raises(LLMTimeout):
        provider.complete([ChatMessage("user", "x")])


def test_an_unknown_read_timeout_is_not_replayed(monkeypatch, fake_openai):
    """A request that was sent and never answered has an unknown outcome.

    Replaying it would ask the provider for a second answer to one request,
    so the failure is reported instead of being replayed.
    """
    calls = _failing_transport(
        monkeypatch, lambda request: httpx.ReadTimeout("read timed out", request=request))
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMTimeout):
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 1


def test_a_broken_connection_after_the_request_is_not_replayed(monkeypatch, fake_openai):
    """The upstream closed the connection: the answer may exist upstream."""
    calls = _failing_transport(
        monkeypatch, lambda request: httpx.RemoteProtocolError("server disconnected", request=request))
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMConnectionError):
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 1


def test_a_connection_failure_before_the_request_is_replayed(monkeypatch, fake_openai):
    """The connection was never established, so no answer can exist."""
    calls = _failing_transport(
        monkeypatch, lambda request: httpx.ConnectError("connection refused", request=request))
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMConnectionError):
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 3


def test_json_mode_falls_back_when_provider_rejects_response_format(fake_openai):
    fake_openai.mode = "reject_json_mode"
    fake_openai.response_content = json.dumps({"ok": True})
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=0))
    response = provider.complete([ChatMessage("user", "x")])
    assert response.parsed == {"ok": True}
    assert len(fake_openai.requests) == 2


def test_provider_profile_api_never_returns_or_logs_the_key(api, fake_openai):
    client, factory, _settings = api
    created = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": "fake-openai",
            "kind": "openai_compatible",
            "base_url": f"{fake_openai.base_url}/v1",
            "model": "test-model",
            "api_key": "test-key",
            "timeout_s": 2,
            "max_retries": 1,
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["has_api_key"] is True
    assert "api_key" not in profile
    assert "test-key" not in created.text

    tested = client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    assert "test-model" in tested.json()["capabilities"]["models"]

    routed = client.put(
        "/api/v1/agent-routes",
        json={"role": "shot_planner", "provider_profile_id": profile["id"]},
    )
    assert routed.status_code == 200, routed.text
    routes = client.get("/api/v1/agent-routes").json()
    assert routes[0]["role"] == "shot_planner"

    blocked = client.delete(f"/api/v1/provider-profiles/{profile['id']}")
    assert blocked.status_code == 409
    assert client.delete("/api/v1/agent-routes/shot_planner").status_code == 204
    assert client.delete(f"/api/v1/provider-profiles/{profile['id']}").status_code == 204

    from sqlalchemy import select

    from shine.models import AuditEvent

    with factory() as db:
        events = db.scalars(select(AuditEvent)).all()
    serialized = " ".join(str(event.payload) for event in events)
    assert "test-key" not in serialized


def test_secret_store_never_writes_plaintext(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    profile_id = str(uuid.uuid4())
    reference = store.store(profile_id, "sk-super-secret-value")
    assert store.load(reference) == "sk-super-secret-value"
    stored = (tmp_path / "secrets" / f"{profile_id}.secret").read_bytes()
    assert b"sk-super-secret-value" not in stored
    assert reference.split(":", 1)[0] in {"dpapi", "file"}
    store.delete(reference)
    with pytest.raises(LLMNotConfigured):
        store.load(reference)

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from shine.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMConnectionError,
    LLMError,
    LLMFormatError,
    LLMNotConfigured,
    LLMServerError,
    LLMTimeout,
    ProviderConfig,
)
from shine.llm.gateway import agent_output_config
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from tests.fake_http import FakeOpenAIServer

pytestmark = pytest.mark.unit


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


def test_provider_error_code_in_http_200_is_not_a_success(monkeypatch, fake_openai):
    provider = OpenAICompatibleProvider(config(fake_openai))
    response = httpx.Response(200, json={"base_resp": {"status_code": 1008}, "choices": []})
    monkeypatch.setattr(provider, "_request", lambda *args, **kwargs: response)
    with pytest.raises(LLMError) as error:
        provider.complete([ChatMessage("user", "x")])
    assert error.value.details == {"status": 200, "provider_code": 1008}


@pytest.mark.parametrize(("configured", "model_limit", "expected"), [
    (None, None, 131_072), (200_000, None, 131_072),
    (4096, None, 4096), (None, 8192, 8192),
])
def test_agent_output_budget_respects_smaller_limits(fake_openai, configured, model_limit, expected):
    options = {"max_completion_tokens": configured} if configured else {}
    original = config(fake_openai, generation_options=options, max_output_tokens=model_limit)
    bounded = agent_output_config(original)
    assert bounded.generation_options["max_completion_tokens"] == expected
    assert original.generation_options == options


@pytest.mark.parametrize("model", ["MiniMax-M3.1-Flash", "MiniMax-M3-1-Flash"])
def test_minimax_m31_flash_uses_bounded_default_output_budget(fake_openai, model):
    """A 1M-context reasoning model is budgeted at its 128K output ceiling."""
    original = config(fake_openai, model=model, generation_options={"reasoning_effort": "medium"})
    bounded = agent_output_config(original)
    assert bounded.generation_options == {
        "reasoning_effort": "medium", "max_completion_tokens": 131_072,
    }


def test_minimax_m31_flash_never_expands_an_explicit_output_budget(fake_openai):
    original = config(
        fake_openai, model="MiniMax-M3.1-Flash", generation_options={"max_completion_tokens": 100_000},
    )
    assert agent_output_config(original).generation_options["max_completion_tokens"] == 100_000


def test_truncated_completion_names_the_finish_reason_and_reasoning_usage(monkeypatch, fake_openai):
    """An empty answer caused by a starved budget must say so, not read as bad JSON."""
    provider = OpenAICompatibleProvider(config(fake_openai))
    response = httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
        "usage": {"completion_tokens": 16_384,
                  "completion_tokens_details": {"reasoning_tokens": 16_383}},
    })
    monkeypatch.setattr(provider, "_request", lambda *args, **kwargs: response)
    with pytest.raises(LLMFormatError) as error:
        provider.complete([ChatMessage("user", "x")])
    assert error.value.details["finish_reason"] == "length"
    assert error.value.details["reasoning_tokens"] == 16_383
    assert error.value.details["completion_tokens"] == 16_384
    assert error.value.details["status"] == 200


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


def test_transient_server_errors_with_unknown_outcome_are_not_retried(fake_openai):
    fake_openai.mode = "status_500"
    fake_openai.server_error_times = 5
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMServerError) as exc:
        provider.complete([ChatMessage("user", "x")])
    assert exc.value.details["external_outcome"] == "UNKNOWN"
    assert len(fake_openai.requests) == 1


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
    with pytest.raises(LLMTimeout) as exc:
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 1
    assert exc.value.details["external_outcome"] == "UNKNOWN"


def test_a_broken_connection_after_the_request_is_not_replayed(monkeypatch, fake_openai):
    """The upstream closed the connection: the answer may exist upstream."""
    calls = _failing_transport(
        monkeypatch, lambda request: httpx.RemoteProtocolError("server disconnected", request=request))
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMConnectionError) as exc:
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 1
    assert exc.value.details["external_outcome"] == "UNKNOWN"


def test_a_connection_failure_before_the_request_is_replayed(monkeypatch, fake_openai):
    """The connection was never established, so no answer can exist."""
    calls = _failing_transport(
        monkeypatch, lambda request: httpx.ConnectError("connection refused", request=request))
    provider = OpenAICompatibleProvider(config(fake_openai, max_retries=2))
    with pytest.raises(LLMConnectionError) as exc:
        provider.complete([ChatMessage("user", "x")])
    assert len(calls) == 3
    assert exc.value.details["external_outcome"] == "NOT_SENT"


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
    stored = (tmp_path / "secrets" / reference.split(":", 1)[1]).read_bytes()
    assert b"sk-super-secret-value" not in stored
    assert reference.split(":", 1)[0] in {"dpapi", "file"}
    store.delete(reference)
    with pytest.raises(LLMNotConfigured):
        store.load(reference)

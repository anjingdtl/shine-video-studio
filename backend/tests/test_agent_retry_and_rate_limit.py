"""A throttled or refused provider must not lose a scene.

Two failures were found in the browser acceptance run:

* the provider answered 429 and the transport raised it straight away, so one
  throttled call killed a whole storyboard unit;
* a refused contract was retried without saying what the server refused, so
  the same answer was bought twice and refused twice.

Both are transport-and-contract facts, not a matter of taste: a 429 is a
refusal that produced no answer, and a retry that names the refusal is the
only retry that can repair anything.
"""
from __future__ import annotations

import time

import pytest

from shine.llm import openai_compatible
from shine.llm.base import ChatMessage, LLMFormatError, LLMRateLimit, ProviderConfig
from shine.llm.gateway import AGENT_RATE_LIMIT_RETRY_ATTEMPTS
from tests.fake_http import FakeOpenAIServer
from tests.test_film_crew import BRIEF, FACTS
from tests.test_storyboard import _responder, _studio_with_chain

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_openai():
    server = FakeOpenAIServer().start()
    try:
        yield server
    finally:
        server.stop()
        openai_compatible.clear_rate_limit_cooldowns()


def _config(server: FakeOpenAIServer, **overrides) -> ProviderConfig:
    values = {
        "profile_id": "profile", "name": "local", "kind": "openai_compatible",
        "base_url": f"{server.base_url}/v1", "model": "test-model", "api_key": "test-key",
        "timeout_s": 5, "max_retries": 2,
    }
    values.update(overrides)
    return ProviderConfig(**values)


def test_a_throttled_call_waits_and_asks_again(fake_openai, monkeypatch):
    """A 429 produced no answer, so asking again cannot buy a second one."""
    monkeypatch.setattr(openai_compatible, "RATE_LIMIT_BACKOFF_SECONDS", (0.01, 0.01, 0.01, 0.01))
    fake_openai.chat_status = 429
    provider = openai_compatible.OpenAICompatibleProvider(
        _config(fake_openai, rate_limit_retry_attempts=2))
    with pytest.raises(LLMRateLimit) as excinfo:
        provider.complete([ChatMessage("user", "hello")])
    assert excinfo.value.code == "LLM_RATE_LIMIT"
    # One refusal plus the two allowed retries: the lane asked again instead
    # of losing the scene on the first refusal.
    assert len(fake_openai.requests) == 3


def test_a_plain_probe_never_posts_again_after_a_refusal(fake_openai, monkeypatch):
    """The connectivity probe keeps its own stricter budget."""
    monkeypatch.setattr(openai_compatible, "RATE_LIMIT_BACKOFF_SECONDS", (0.01, 0.01, 0.01, 0.01))
    fake_openai.chat_status = 429
    provider = openai_compatible.OpenAICompatibleProvider(_config(fake_openai))
    with pytest.raises(LLMRateLimit):
        provider.complete([ChatMessage("user", "hello")])
    assert len(fake_openai.requests) == 1


def test_a_refusal_makes_the_next_call_wait_for_the_same_endpoint(fake_openai, monkeypatch):
    """One refusal throttles the endpoint, so the next call pauses too."""
    monkeypatch.setattr(openai_compatible, "RATE_LIMIT_BACKOFF_SECONDS", (0.2, 0.2, 0.2, 0.2))
    fake_openai.chat_status = 429
    provider = openai_compatible.OpenAICompatibleProvider(
        _config(fake_openai, rate_limit_retry_attempts=1))
    with pytest.raises(LLMRateLimit):
        provider.complete([ChatMessage("user", "hello")])
    started = time.monotonic()
    with pytest.raises(LLMRateLimit):
        provider.complete([ChatMessage("user", "again")])
    assert time.monotonic() - started >= 0.2


def test_the_agent_lane_budgets_a_throttled_call(api):
    """The agent lane may wait out a throttle; the probe may not."""
    from shine.llm.base import LLMResponse
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore

    client, factory, settings = api
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "budget-probe", "kind": "mock", "model": "mock"}).json()
    assert client.put("/api/v1/agent-routes", json={
        "role": "scriptwriter", "provider_profile_id": profile["id"]}).status_code == 200
    captured: dict = {}

    def provider_factory(config, _responder):
        captured["config"] = config
        return type("Provider", (), {"complete": lambda self, *a, **k: LLMResponse(
            text="{}", parsed={}, model="mock", latency_ms=1, usage={})})()

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), provider_factory=provider_factory)
    with factory.begin() as db:
        gateway.complete_for_role(db, "scriptwriter", system="s", user="u")
    assert captured["config"].rate_limit_retry_attempts == AGENT_RATE_LIMIT_RETRY_ATTEMPTS
    assert AGENT_RATE_LIMIT_RETRY_ATTEMPTS > 0
    # A plain provider config carries no retry budget: the probe that uses it
    # never posts again after a refusal.
    assert ProviderConfig(
        profile_id="p", name="n", kind="openai_compatible", base_url="http://127.0.0.1:1/v1",
        model="m", api_key="k").rate_limit_retry_attempts == 0


def _chain_responder(calls: list, registry: dict, *, first_call_error=None):
    """One responder that records every call and can fail the first one."""
    base = _responder(registry)

    def respond(messages, schema):
        system = " ".join(str(message.content) for message in messages if message.role == "system")
        calls.append(system)
        if first_call_error is not None and len(calls) == 1:
            raise first_call_error
        return base(messages, schema)

    return respond


def test_a_rate_limited_crew_call_is_retried_instead_of_losing_the_scene(api):
    """A 429 in the crew chain must not end the unit."""
    client, _, _ = api
    project_id, _, registry = _studio_with_chain(client)
    calls: list[str] = []
    client.app.state.mock_llm_responder = _chain_responder(
        calls, registry, first_call_error=LLMRateLimit(
            "Provider rate limit reached", details={"status": 429}))
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/generate", json={
        "text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED", body
    assert len(calls) >= 2


def test_a_refused_contract_is_retried_with_a_named_correction(api):
    """The retry must say what the server refused, or it repeats itself."""
    client, _, _ = api
    project_id, _, registry = _studio_with_chain(client)
    calls: list[str] = []

    def respond(messages, schema):
        system = " ".join(str(message.content) for message in messages if message.role == "system")
        calls.append(system)
        if schema.get("title") == "ScriptBreakdown" and len(calls) == 1:
            # A line that is not in the user's script: the server refuses it.
            return {
                "locked_story_facts": FACTS,
                "beats": [{
                    "id": "B1", "description": "The woman waits beside a sealed letter.",
                    "characters": ["Lin"],
                    "dialogue": [{"speaker_id": "Lin", "text": "我来了。", "start_s": 1, "end_s": 2}],
                }],
            }
        return _responder(registry)(messages, schema)

    client.app.state.mock_llm_responder = respond
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/generate", json={
        "text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED", response.json()
    assert any("not in the user's script" in system for system in calls)


def test_a_schema_failure_is_retried_with_the_failing_field_named(api):
    """A format failure names the broken field so the retry can repair it."""
    client, _, _ = api
    project_id, _, registry = _studio_with_chain(client)
    calls: list[str] = []

    def respond(messages, schema):
        system = " ".join(str(message.content) for message in messages if message.role == "system")
        calls.append(system)
        if schema.get("title") == "ScriptBreakdown" and len(calls) == 1:
            raise LLMFormatError(
                "Model JSON does not match the required schema",
                details={"status": 200, "path": "beats", "validator": "required"})
        return _responder(registry)(messages, schema)

    client.app.state.mock_llm_responder = respond
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/generate", json={
        "text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED", response.json()
    assert any("did not satisfy the JSON contract" in system for system in calls)
    assert any("beats" in system for system in calls)


def test_the_correction_never_carries_provider_text():
    """The note names the rule, never the refused answer."""
    from shine.api_film import CONTRACT_REPAIR_NOTES

    assert CONTRACT_REPAIR_NOTES
    for reason, note in CONTRACT_REPAIR_NOTES.items():
        assert reason.isupper()
        assert "{" not in note
        assert len(note) < 400


def test_a_bracketed_no_proxy_entry_does_not_break_the_renderer_client(monkeypatch):
    """The renderer is on this machine: the host proxy may never touch it.

    httpx reads ``[::1]`` in NO_PROXY as a host carrying the port ``:1]`` and
    refuses to build a client at all, so one host-level environment detail
    used to take the whole render path down.
    """
    from shine.comfy.client import ComfyUIClient
    from tests.fake_http import FakeComfyServer

    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,[::1]")
    monkeypatch.setenv("no_proxy", "")
    fake = FakeComfyServer().start()
    try:
        client = ComfyUIClient(fake.base_url, timeout_s=2)
        client.free_models()
        assert fake.queue_running == []
    finally:
        fake.stop()

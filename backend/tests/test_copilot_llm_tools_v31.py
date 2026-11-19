"""V3.1 LLM layer: inherit the configured text model, and speak tools safely.

Two behaviours are pinned here:

* the copilot never requires its own provider profile - an explicitly routed
  ``support_agent`` wins, and otherwise an already-configured text role is
  inherited (GRANT-15);
* a provider that cannot do native tool calling degrades to a *validated*
  structured intent, and the degradation is recorded rather than hidden.
"""
from __future__ import annotations

import httpx
import pytest

from shine.llm.base import LLMNotConfigured, ProviderConfig
from shine.llm.gateway import ROLE_INHERITANCE, LLMGateway, inherited_profile
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from shine.llm.tool_calls import ToolSpec
from shine.models import AgentRoute, ProviderProfile

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}


def _profile(db, *, name: str, kind: str = "openai_compatible", enabled: bool = True) -> ProviderProfile:
    profile = ProviderProfile(
        name=name,
        kind=kind,
        base_url="http://127.0.0.1:9/v1",
        model=f"{name}-model",
        timeout_s=5,
        max_retries=0,
        temperature=0.2,
        enabled=enabled,
        capabilities={},
        generation_options={},
    )
    db.add(profile)
    db.flush()
    return profile


def _route(db, role: str, profile: ProviderProfile) -> AgentRoute:
    row = AgentRoute(role=role, provider_profile_id=profile.id)
    db.add(row)
    db.flush()
    return row


def test_an_explicit_support_agent_route_wins(studio):
    _, factory, _, _ = studio
    with factory() as db:
        generic = _profile(db, name="generic")
        copilot = _profile(db, name="copilot-dedicated")
        _route(db, "comfyops", generic)
        _route(db, "support_agent", copilot)
        db.commit()
        profile, source = inherited_profile(db, "support_agent")
    assert source == "support_agent"
    assert profile.name == "copilot-dedicated"


def test_an_unrouted_copilot_inherits_the_configured_text_model(studio):
    _, factory, _, _ = studio
    with factory() as db:
        generic = _profile(db, name="only-configured")
        _route(db, "comfyops", generic)
        db.commit()
        profile, source = inherited_profile(db, "support_agent")
    assert source == "comfyops"
    assert profile.name == "only-configured"


def test_inheritance_follows_the_declared_order(studio):
    """The fallback chain is deterministic and auditable, not incidental."""
    _, factory, _, _ = studio
    with factory() as db:
        writer = _profile(db, name="writer")
        director = _profile(db, name="director")
        _route(db, "scriptwriter", writer)
        _route(db, "director", director)
        db.commit()
        profile, source = inherited_profile(db, "support_agent")
    assert source == "scriptwriter"
    assert ROLE_INHERITANCE.index("scriptwriter") < ROLE_INHERITANCE.index("director")


def test_a_disabled_profile_is_not_inherited(studio):
    _, factory, _, _ = studio
    with factory() as db:
        disabled = _profile(db, name="disabled", enabled=False)
        _route(db, "comfyops", disabled)
        db.commit()
        with pytest.raises(LLMNotConfigured):
            inherited_profile(db, "support_agent")


def test_no_configuration_is_a_named_refusal(studio):
    _, factory, _, _ = studio
    with factory() as db:
        with pytest.raises(LLMNotConfigured):
            inherited_profile(db, "support_agent")


def test_no_api_key_is_needed_to_use_the_inherited_model(studio):
    """The inherited profile carries its own key reference, never a new one."""
    _, factory, _, settings = studio
    with factory() as db:
        generic = _profile(db, name="with-key")
        generic.api_key_ref = "no-such-ref"
        _route(db, "comfyops", generic)
        db.commit()
        gateway = LLMGateway(SecretStore(settings.secret_store_dir))
        profile, source = gateway.profile_for_role_or_inherit(db, "support_agent")
        assert source == "comfyops"
        assert profile.id == generic.id


# --------------------------------------------------------------------------
# Transport: native tool calling, and a recorded degradation
# --------------------------------------------------------------------------


class _Recorder:
    """Records the request the provider would send and returns a canned body."""

    def __init__(self, body: dict, status: int = 200):
        self.body = body
        self.status = status
        self.requests: list[dict] = []


def _provider(monkeypatch, recorder: _Recorder) -> OpenAICompatibleProvider:
    config = ProviderConfig(
        profile_id="p",
        name="local",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        model="local-text",
        api_key=None,
        timeout_s=1,
        max_retries=0,
    )
    provider = OpenAICompatibleProvider(config)

    def fake_request(method, url, *, payload=None):
        recorder.requests.append(payload or {})
        return httpx.Response(
            recorder.status,
            json=recorder.body,
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(provider, "_request", fake_request)
    return provider


def test_native_tool_request_carries_the_tools(monkeypatch):
    recorder = _Recorder(
        {
            "model": "local-text",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "setup_reconcile", "arguments": "{}"}}
                        ],
                    },
                }
            ],
        }
    )
    provider = _provider(monkeypatch, recorder)
    raw = provider.complete_with_tools(
        [{"role": "user", "content": "开始"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "setup_reconcile",
                    "description": "核验已存在文件",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    sent = recorder.requests[-1]
    assert sent["tools"][0]["function"]["name"] == "setup_reconcile"
    assert sent["tool_choice"] == "auto"
    assert raw["body"]["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "setup_reconcile"


def test_gateway_returns_none_when_the_provider_has_no_native_support(studio):
    """The caller then uses the validated fallback; it is not an error."""
    _, factory, _, settings = studio
    with factory() as db:
        generic = _profile(db, name="mock-kind", kind="mock")
        _route(db, "comfyops", generic)
        db.commit()
        gateway = LLMGateway(SecretStore(settings.secret_store_dir))
        turn = gateway.complete_tools_for_role(
            db,
            "support_agent",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                ToolSpec(
                    name="setup_reconcile",
                    description="核验",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )
    assert turn is None


def test_gateway_degrades_when_the_provider_rejects_the_tools_shape(studio, monkeypatch):
    """A 400 on ``tools`` is a recorded degradation, not a crash."""
    from shine.llm.base import LLMError

    _, factory, _, settings = studio
    with factory() as db:
        generic = _profile(db, name="strict-gateway")
        _route(db, "comfyops", generic)
        db.commit()
        gateway = LLMGateway(SecretStore(settings.secret_store_dir))

        def refuse(*args, **kwargs):
            raise LLMError("tools unsupported", details={"status": 400})

        gateway.provider_factory = lambda config, responder: type(
            "P", (), {"complete_with_tools": staticmethod(refuse)}
        )()
        turn = gateway.complete_tools_for_role(
            db,
            "support_agent",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                ToolSpec(
                    name="setup_reconcile",
                    description="核验",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )
    assert turn is None

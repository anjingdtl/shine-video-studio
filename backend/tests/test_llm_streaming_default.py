"""Streaming is the default agent transport; a cut stream stays UNKNOWN.

The live run lost scenes to LLM_CONNECTION/UNKNOWN: long non-streaming
completions idle for minutes while the model thinks, and proxies/gateways cut
such connections.  SSE keeps the connection alive with events; the no-event
fallback keeps unprobed providers from buying a second answer.
"""
from __future__ import annotations

import json

import httpx
import pytest

from shine.llm import openai_compatible
from shine.llm.base import ChatMessage, LLMConnectionError, ProviderConfig
from shine.llm.transport_policy import streaming_enabled


def _config(kind: str = "openai_compatible", **kw) -> ProviderConfig:
    return ProviderConfig(profile_id="p", name="p", kind=kind,
                          base_url="https://provider.example/v1", model="m",
                          api_key="k", timeout_s=600, **kw)


def test_streaming_is_on_by_default_for_sse_adapters(monkeypatch):
    monkeypatch.delenv("SHINE_LLM_STREAMING", raising=False)
    assert streaming_enabled(_config()) is True
    assert streaming_enabled(_config(kind="anthropic")) is False
    monkeypatch.setenv("SHINE_LLM_STREAMING", "off")
    assert streaming_enabled(_config()) is False


def test_the_agent_lane_actually_streams(monkeypatch):
    """The crew chain config must carry the streaming flag, not just the policy."""
    from shine.llm.gateway import agent_output_config
    monkeypatch.delenv("SHINE_LLM_STREAMING", raising=False)
    assert agent_output_config(_config()).streaming is True
    monkeypatch.setenv("SHINE_LLM_STREAMING", "off")
    assert agent_output_config(_config()).streaming is False


def _sse_body(text: str) -> str:
    events = [json.dumps({"choices": [{"delta": {"content": text}}]}),
              json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
              "[DONE]"]
    return "".join(f"data: {e}\n\n" for e in events)


def test_complete_uses_the_stream_when_it_answers(monkeypatch):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if body.get("stream"):
            return httpx.Response(200, text=_sse_body('{"ok": true}'),
                                  headers={"content-type": "text/event-stream"})
        raise AssertionError("plain request must not follow a answered stream")

    monkeypatch.setattr(openai_compatible, "build_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    response = provider.complete([ChatMessage(role="user", content="hi")])
    assert response.parsed == {"ok": True}
    assert calls == [True]
    assert response.raw_meta.get("streamed") is True


def test_a_refused_stream_falls_back_to_exactly_one_plain_request(monkeypatch):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if body.get("stream"):
            return httpx.Response(400, json={"error": "stream unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(openai_compatible, "build_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    response = provider.complete([ChatMessage(role="user", content="hi")])
    assert response.parsed == {"ok": True}
    assert calls == [True, False]


def test_a_stream_cut_after_events_is_unknown_and_never_replayed(monkeypatch):
    calls = []

    class CutStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices": [{"delta": {"content": "{\\"ok\\": true}"}}]}\n\n'
            raise httpx.ReadError("connection cut mid-answer")

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if not body.get("stream"):
            raise AssertionError("a cut stream must not fall back to a second request")
        return httpx.Response(200, stream=CutStream(),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(openai_compatible, "build_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMConnectionError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert excinfo.value.details.get("external_outcome") == "UNKNOWN"
    assert calls == [True]
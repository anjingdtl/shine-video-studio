"""Streaming is the default agent transport; a cut stream stays UNKNOWN.

The live run lost scenes to LLM_CONNECTION/UNKNOWN: long non-streaming
completions idle for minutes while the model thinks, and proxies/gateways cut
such connections. SSE keeps the connection alive with events; an acknowledged
empty or interrupted stream is never silently resubmitted.
"""
from __future__ import annotations

import json

import httpx
import pytest

from shine.literary.governor import classify_external_outcome
from shine.llm import openai_compatible
from shine.llm.base import ChatMessage, LLMConnectionError, LLMFormatError, ProviderConfig
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

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    response = provider.complete([ChatMessage(role="user", content="hi")])
    assert response.parsed == {"ok": True}
    assert calls == [True]
    assert response.raw_meta.get("streamed") is True


def test_stream_request_reuses_a_complete_json_response(monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content).get("stream", False))
        return httpx.Response(200, json={
            "model": "reported-model", "choices": [{"message": {"content": '{"ok": true}'}}],
        })

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    response = openai_compatible.OpenAICompatibleProvider(_config(streaming=True)).complete(
        [ChatMessage(role="user", content="hi")])
    assert response.parsed == {"ok": True}
    assert response.model == "reported-model"
    assert calls == [True]


def test_a_refused_stream_falls_back_to_exactly_one_plain_request(monkeypatch):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if body.get("stream"):
            return httpx.Response(400, json={"error": "stream unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    response = provider.complete([ChatMessage(role="user", content="hi")])
    assert response.parsed == {"ok": True}
    assert calls == [True, False]


@pytest.mark.parametrize("body, error, outcome", [
    ('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
     LLMFormatError, "RESPONSE_RECEIVED"),
    ('data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n',
     LLMConnectionError, "UNKNOWN"),
    ("", LLMConnectionError, "UNKNOWN"),
])
def test_acknowledged_empty_stream_never_sends_a_second_request(monkeypatch, body, error, outcome):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content).get("stream", False))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(error) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert classify_external_outcome(excinfo.value) == outcome
    assert calls == [True]


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

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMConnectionError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert excinfo.value.details.get("external_outcome") == "UNKNOWN"
    assert calls == [True]


def test_a_stream_silent_after_acknowledgement_is_unknown_and_never_replayed(monkeypatch):
    # 活缺陷（2026-11-16）：剧组步骤拿到 200 后流式静默，旧逻辑把首事件超时当成
    # 「无事件」回退非流式再发一次——重发了已被接受的请求（违反 UNKNOWN 门禁），
    # 且再等一个完整超时。200 已收即请求已发出，静默超时只能判 UNKNOWN，不回退。
    calls = []

    class SilentStream(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout("no event before the first-event deadline")

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if not body.get("stream"):
            raise AssertionError("a silent acknowledged stream must not fall back to a re-send")
        return httpx.Response(200, stream=SilentStream(),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMConnectionError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert excinfo.value.details.get("external_outcome") == "UNKNOWN"
    assert excinfo.value.details.get("headers_received") is True
    assert calls == [True]


def test_read_timeout_before_headers_does_not_resubmit(monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content).get("stream", False))
        raise httpx.ReadTimeout("response headers unavailable", request=request)

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMConnectionError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert excinfo.value.details["external_outcome"] == "UNKNOWN"
    assert calls == [True]


def _sse_answer(text: str, finish_reason: str, *, completion_tokens: int = 320,
                reasoning_tokens: int = 96) -> str:
    events = [json.dumps({"choices": [{"delta": {"content": text}}]}),
              json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]}),
              json.dumps({"usage": {"completion_tokens": completion_tokens,
                                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}}),
              "[DONE]"]
    return "".join(f"data: {e}\n\n" for e in events)


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_a_streamed_format_failure_is_answered_not_unknown(monkeypatch, finish_reason):
    # 穿测活缺陷（2026-11-21，大纲 S1）：流式传输把格式失败的证据写成 http_status，
    # 而唯一裁决 classify_external_outcome 读 status——服务商明明已答复（200 +
    # 终止 finish_reason），却被判 UNKNOWN，场景卡在「服务商结果未知」的人工重发门，
    # 既不走 governor 的一次有界纠错，也不走 RESPONSE_RECEIVED 的自动断点续跑。
    # 修复收敛证据键：流式与非流式写同一份事实，裁决口不变。
    calls = []
    answer = "这不是一个 JSON 对象" if finish_reason == "stop" else '{"scene_key": "S1"'

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(bool(body.get("stream")))
        if not body.get("stream"):
            raise AssertionError("an answered stream must not fall back to a second request")
        return httpx.Response(200, text=_sse_answer(answer, finish_reason),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMFormatError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    details = excinfo.value.details
    assert details.get("status") == 200, "the classifier reads status, not http_status"
    assert details.get("finish_reason") == finish_reason
    assert details.get("completion_tokens") == 320
    assert details.get("reasoning_tokens") == 96
    assert classify_external_outcome(excinfo.value) == "RESPONSE_RECEIVED"
    assert calls == [True]


def test_the_stream_read_ceiling_honors_the_idle_deadline(monkeypatch):
    # 穿测活缺陷（2026-11-21，镜头拆解导演/场记）：流式客户端曾把整条流的读超时
    # 设成首事件上限 first_event_s（180s），声明的 idle_s（300s，「已在流式、
    # 服务商还活着」的间隔上限）是死代码——模型中途思考静默 >180s 就被切成
    # UNKNOWN。修复把读上限接到 max(first_event_s, idle_s)，连接超时用 connect_s。
    from shine.llm.transport_policy import policy_for
    seen = {}

    def capture(url, timeout, **kwargs):
        seen["timeout"] = timeout
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=_sse_body('{"ok": true}'),
                                           headers={"content-type": "text/event-stream"})))

    monkeypatch.setattr(openai_compatible, "build_text_client", capture)
    cfg = _config(streaming=True)
    provider = openai_compatible.OpenAICompatibleProvider(cfg)
    assert provider.complete([ChatMessage(role="user", content="hi")]).parsed == {"ok": True}
    policy = policy_for(cfg, streaming=True)
    timeout = seen["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == max(policy.first_event_s, policy.idle_s) == 300
    assert timeout.connect == policy.connect_s == 30


def test_a_broken_stream_records_the_underlying_error_type(monkeypatch):
    # 取证：断流 UNKNOWN 必须留下底层异常类名，才能把「读上限切断」
    # （ReadTimeout）与「对端/代理关闭」（ReadError/RemoteProtocolError）区分开。
    class CutStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices": [{"delta": {"content": "{\\"ok\\": true}"}}]}\n\n'
            raise httpx.ReadError("connection cut mid-answer")

    monkeypatch.setattr(openai_compatible, "build_text_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(
                            lambda request: httpx.Response(200, stream=CutStream(),
                                                           headers={"content-type": "text/event-stream"}))))
    provider = openai_compatible.OpenAICompatibleProvider(_config(streaming=True))
    with pytest.raises(LLMConnectionError) as excinfo:
        provider.complete([ChatMessage(role="user", content="hi")])
    assert excinfo.value.details.get("external_outcome") == "UNKNOWN"
    assert excinfo.value.details.get("error_type") == "ReadError"
    assert excinfo.value.details.get("streamed") is True

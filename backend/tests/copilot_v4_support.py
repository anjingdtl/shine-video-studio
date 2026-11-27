"""Shared helpers for the V4 copilot tests.

V4 moved the copilot from "keyword rules + template replies" to "a configured
model investigates real ledgers with read-only tools".  Two facts follow for
tests:

* without a configured text model there is **no** conversation: a message is
  refused with ``409 COPILOT_LLM_REQUIRED`` and nothing is stored;
* with a configured model the turn runs :mod:`shine.copilot.chat_investigator`,
  which may spend up to six read-only tool calls and then answers; a change
  is proposed as a card, never executed.

The helpers here configure a ``mock``-kind text profile (so no network is
touched) and let a test script the model's turns through the validated
structured-intent transport the real gateway uses for providers without native
tool calling.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

from shine.llm.tool_calls import FALLBACK_SCHEMA, ToolCall, ToolTurn

HEADERS = {"x-shine-local-action": "1"}


def configure_mock_text_model(
    client,
    responder: Callable[[list[dict[str, Any]], dict[str, Any] | None], Any] | None = None,
    *,
    role: str = "comfyops",
    name: str = "copilot-v4-mock",
) -> str:
    """Give the workbench one configured ``mock`` text model.

    The copilot inherits it (``support_agent`` → the configured text role) or
    an explicit ``support_agent`` route wins when ``role="support_agent"``.
    Returns the profile id.
    """
    created = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": name,
            "kind": "mock",
            "base_url": "http://mock.invalid/v1",
            "model": "mock-text",
            "enabled": True,
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    routed = client.put(
        "/api/v1/agent-routes",
        json={"role": role, "provider_profile_id": profile_id},
        headers=HEADERS,
    )
    assert routed.status_code in {200, 201}, routed.text
    if responder is not None:
        client.app.state.mock_llm_responder = responder
    return profile_id


def tool_reply(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """One scripted structured-intent tool call."""
    return {"action": "call_tool", "tool": name, "arguments": arguments or {}}


def finish_reply(final: str) -> dict[str, Any]:
    return {"action": "finish", "final": final}


def _content(item: Any) -> str:
    """ChatMessage (attribute) or a plain dict (key): either way the text."""
    content = getattr(item, "content", None)
    if content is None and isinstance(item, dict):
        content = item.get("content")
    return str(content or "")


def scripted_responder(replies: list[dict[str, Any]], *, record: list[str] | None = None):
    """A mock responder that plays ``replies`` in order, then finishes.

    ``record`` (optional) collects the text of every call so a test can prove
    the model saw the real evidence / the memory / the salutation.
    """
    calls = list(replies)

    def respond(messages, schema):
        if record is not None:
            record.append("\n".join(_content(item) for item in messages))
        if isinstance(schema, dict) and "action" in (schema.get("properties") or {}):
            return calls.pop(0) if calls else finish_reply("（脚本结束）")
        return "模型回答"

    return respond


class ScriptedGateway:
    """A gateway whose model turns are fixed (used when a test calls the
    service layer directly instead of going through HTTP).

    ``native=True`` returns :class:`ToolTurn` objects from
    ``complete_tools_for_role``; ``native=False`` exercises the validated
    fallback with ``fallback`` as the parsed reply.
    """

    def __init__(self, turns: list[ToolTurn], *, native: bool = True, fallback: dict[str, Any] | None = None):
        self.turns = list(turns)
        self.native = native
        self.fallback = fallback or finish_reply("结束")
        self.tool_turns = 0
        self.fallback_calls = 0
        self.pins: dict[str, object] = {}

    def profile_for_role(self, db, role):
        return SimpleNamespace(id="scripted", name="Scripted", kind="mock", base_url="", model="scripted")

    def profile_for_role_or_inherit(self, db, role):
        return self.profile_for_role(db, role), role

    def pin_role(self, role, profile):
        self.pins[role] = profile

    def clear_pins(self):
        self.pins.clear()

    def complete_tools_for_role(self, db, role, **kwargs):
        if not self.native:
            return None
        if self.tool_turns >= len(self.turns):
            return ToolTurn(text="没有更多步骤", tool_calls=[], native=False)
        turn = self.turns[self.tool_turns]
        self.tool_turns += 1
        return turn

    def complete_for_role(self, db, role, **kwargs):
        self.fallback_calls += 1
        return SimpleNamespace(
            text=json.dumps(self.fallback, ensure_ascii=False), parsed=dict(self.fallback),
            model="scripted", latency_ms=1, usage={},
        )


def call(name: str, arguments: dict[str, Any] | None = None, *, call_id: str = "call-1") -> ToolTurn:
    return ToolTurn(text="", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments or {})], native=True)


def final(text: str) -> ToolTurn:
    return ToolTurn(text=text, tool_calls=[], native=False)


__all__ = [
    "HEADERS",
    "ScriptedGateway",
    "call",
    "configure_mock_text_model",
    "finish_reply",
    "final",
    "scripted_responder",
    "tool_reply",
]

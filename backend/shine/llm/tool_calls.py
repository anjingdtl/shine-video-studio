"""Model-facing tool protocol for the copilot (V3.1).

Two transports reach the same server-side decision:

* **native tool calling** for an OpenAI-compatible or Anthropic profile - the
  provider returns structured ``tool_calls`` instead of prose;
* **a validated structured intent** for any other configured model - the tool
  menu is rendered into one JSON contract and the answer must validate against
  it before a single field is read.

Neither transport carries power.  A tool call is a *proposal*: it still goes
through the grant, the capability contract, the target fingerprint, the
occupancy gate and the original executor.  Nothing here evaluates model text,
builds a command line, or maps a free-form string onto a Python callable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from shine.llm.base import LLMFormatError

#: A tool description is bounded: a menu larger than this cannot be reviewed by
#: a person, and an unbounded menu invites the model to invent names.
MAX_TOOLS = 24
MAX_TOOL_DESCRIPTION = 600


@dataclass(frozen=True)
class ToolSpec:
    """One reviewed capability, as the model sees it."""

    name: str
    description: str
    parameters: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description[:MAX_TOOL_DESCRIPTION],
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ToolTurn:
    """One model turn: either prose, or one bounded batch of tool calls."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    native: bool = False
    raw_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def json_schema_for(spec: ToolSpec) -> dict[str, Any]:
    """OpenAI's function-tool shape, which Anthropic also accepts field-wise."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description[:MAX_TOOL_DESCRIPTION],
            "parameters": spec.parameters,
        },
    }


def openai_tools(specs: Iterable[ToolSpec]) -> list[dict[str, Any]]:
    return [json_schema_for(spec) for spec in list(specs)[:MAX_TOOLS]]


def anthropic_tools(specs: Iterable[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description[:MAX_TOOL_DESCRIPTION],
            "input_schema": spec.parameters,
        }
        for spec in list(specs)[:MAX_TOOLS]
    ]


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMFormatError("A tool call carried malformed arguments") from exc
        if not isinstance(parsed, dict):
            raise LLMFormatError("Tool call arguments must be a JSON object")
        return parsed
    raise LLMFormatError("Tool call arguments must be an object or a JSON string")


def parse_openai_turn(body: dict[str, Any], *, latency_ms: int, model: str | None) -> ToolTurn:
    """Read ``choices[0].message`` from an OpenAI-compatible response."""
    choices = body.get("choices") or []
    if not choices:
        raise LLMFormatError("Provider returned no completions")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    calls: list[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = str(function.get("name") or "")
        if not name:
            continue
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{index}"),
                name=name,
                arguments=_arguments(function.get("arguments")),
            )
        )
    return ToolTurn(
        text=str(content or "").strip(),
        tool_calls=calls[:MAX_TOOLS],
        model=body.get("model") or model,
        latency_ms=latency_ms,
        usage=body.get("usage") or {},
        native=bool(calls),
        raw_meta={
            "finish_reason": choices[0].get("finish_reason"),
            "id": body.get("id"),
            "is_mock": bool(body.get("is_mock", False)),
        },
    )


def parse_anthropic_turn(body: dict[str, Any], *, latency_ms: int, model: str | None) -> ToolTurn:
    """Read Anthropic's ``content`` block list into the same shape."""
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for index, block in enumerate(body.get("content") or []):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            name = str(block.get("name") or "")
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=str(block.get("id") or f"toolu_{index}"),
                    name=name,
                    arguments=_arguments(block.get("input")),
                )
            )
    return ToolTurn(
        text="".join(text_parts).strip(),
        tool_calls=calls[:MAX_TOOLS],
        model=body.get("model") or model,
        latency_ms=latency_ms,
        usage=body.get("usage") or {},
        native=bool(calls),
        raw_meta={"stop_reason": body.get("stop_reason"), "id": body.get("id")},
    )


#: The structured fallback's response contract.  It is a *closed* union: the
#: model may name one of the offered tools or answer with a final message, and
#: nothing else validates.
FALLBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["call_tool", "finish"]},
        "tool": {"type": "string"},
        "arguments": {"type": "object"},
        "final": {"type": "string"},
        "reason": {"type": "string"},
    },
}


def fallback_system_prompt(specs: list[ToolSpec]) -> str:
    """The one place a model is told which tools exist in fallback mode."""
    menu = "\n".join(
        f"- {spec.name}: {spec.description[:200]} 参数 schema: "
        f"{json.dumps(spec.parameters, ensure_ascii=False, separators=(',', ':'))}"
        for spec in specs[:MAX_TOOLS]
    )
    return (
        "你是 Shine 本机 AI 管家。你只能通过下列已注册工具推进任务。\n"
        "规则：\n"
        "1. 需要执行操作时，action=call_tool，并在 tool 中填工具名、arguments 中填参数对象。\n"
        "2. 目标达成或必须停止时，action=finish，并在 final 中说明结果。\n"
        "3. 不要编造工具名或参数；不要输出命令、脚本、代码块或补丁。\n"
        "4. 工具执行结果由服务端给出，你不得自行宣布环境就绪或工作流通过。\n"
        "5. 一次只提出一个工具调用。\n"
        "可用工具：\n" + menu
    )


def fallback_user_prompt(state: dict[str, Any]) -> str:
    return (
        "当前任务状态（服务端事实摘要）：\n"
        + json.dumps(state, ensure_ascii=False, sort_keys=True)[:12000]
        + "\n\n请给出下一步：一个工具调用，或结束。"
    )


def parse_fallback_reply(
    reply: dict[str, Any], *, tool_names: set[str]
) -> ToolTurn:
    """Turn a validated JSON intent into the same ``ToolTurn`` shape.

    The validation is deliberately strict: an unknown tool name, a missing
    argument object, or an ``action`` outside the enum is a format error the
    caller counts as no progress, not a call that might work.
    """
    action = str(reply.get("action") or "")
    if action == "finish":
        return ToolTurn(text=str(reply.get("final") or "").strip(), native=False)
    if action != "call_tool":
        raise LLMFormatError("Structured tool reply has an unknown action")
    name = str(reply.get("tool") or "")
    if name not in tool_names:
        raise LLMFormatError(
            "Structured tool reply named a tool that was not offered",
            details={"tool": name[:80]},
        )
    arguments = reply.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise LLMFormatError("Structured tool reply arguments must be an object")
    return ToolTurn(
        text=str(reply.get("reason") or "").strip(),
        tool_calls=[ToolCall(id="fallback-1", name=name, arguments=arguments)],
        native=False,
    )


def summarise_messages(messages: list[dict[str, Any]], limit: int = 6000) -> str:
    """A bounded textual summary used when the context has to be compressed.

    The copilot keeps a rolling summary instead of replaying every receipt: a
    long task must not grow its prompt without bound.
    """
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        text = str(content or "").replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"[{role}] {text[:400]}")
    joined = "\n".join(lines)
    if len(joined) <= limit:
        return joined
    return "...\n" + joined[-limit:]

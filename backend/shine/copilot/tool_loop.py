"""Bounded autonomous tool loop for one task grant (V3.1).

The loop is the *only* place a model's proposal becomes work, and it is
deliberately small:

    LLM proposal -> grant policy -> original SupportAction -> original executor
                 -> read the original receipt -> next proposal

Four properties are non-negotiable and encoded here rather than promised in a
prompt:

* **bounded** - a step budget, a wall-clock budget, a tool-call budget and a
  no-progress detector.  There is no ``while True``: the loop ends by name.
* **evidence-based** - "the model said it is done" is never success.  The
  server decides from the original action receipts, and a model that stops
  without one is paused for a human, not congratulated.
* **no replay of the unknown** - an ``UNKNOWN``/``NEEDS_REVIEW`` receipt stops
  the loop.  Re-submitting is a person's decision, not a retry.
* **bounded context** - the transcript is compressed as it grows, so a long
  task does not silently become a huge prompt.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from shine.copilot import task_grant as grant_contract
from shine.llm.base import LLMError, LLMNotConfigured
from shine.llm.tool_calls import (
    ToolSpec,
    ToolTurn,
    fallback_system_prompt,
    fallback_user_prompt,
    FALLBACK_SCHEMA,
    parse_fallback_reply,
    summarise_messages,
)
from shine.models import utcnow
from shine.support import grants as grant_service

#: Outcomes that are real evidence the executor finished this sub-action.
SATISFIED_OUTCOMES: frozenset[str] = frozenset(
    {"VERIFIED", "PLAN_FROZEN", "SUCCEEDED", "RELEASED", "EXECUTED"}
)

#: Outcomes that must stop the loop and keep the scene for a person.
STOP_OUTCOMES: frozenset[str] = frozenset(
    {"UNKNOWN", "NOT_VERIFIED", "REGISTERED_NOT_EXECUTABLE"}
)

MAX_TRANSCRIPT = 24
"""Messages kept verbatim; older ones are folded into one running summary."""


@dataclass
class LoopLimits:
    max_steps: int = 12
    max_wall_seconds: int = 1800
    max_no_progress: int = 3

    def public(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_wall_seconds": self.max_wall_seconds,
            "max_no_progress": self.max_no_progress,
        }


@dataclass
class LoopResult:
    status: str
    stop_reason: str
    final: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "final": self.final,
            "steps": self.steps,
            "llm": self.llm,
            "usage": self.usage,
        }


def _satisfied(receipt: dict[str, Any]) -> bool:
    outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
    if outcome in SATISFIED_OUTCOMES:
        return True
    return str(receipt.get("status") or "") in {"SUCCEEDED", "EXECUTED"}


def _must_stop(receipt: dict[str, Any]) -> str | None:
    """A named stop reason, or None when the loop may continue."""
    outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
    status = str(receipt.get("status") or "")
    if outcome in STOP_OUTCOMES:
        return "PAUSED_UNKNOWN" if outcome == "UNKNOWN" else "PAUSED_USER_DECISION"
    if status == "NEEDS_REVIEW":
        return "PAUSED_UNKNOWN"
    if status in {"FAILED", "REFUSED", "SUPERSEDED"}:
        return "PAUSED_USER_DECISION"
    if status == "EXPIRED":
        return "PAUSED_CONTRACT"
    return None


def _state_summary(
    *,
    objective: str,
    grant: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """What the model is allowed to see as "current state".

    Server facts only: the remaining budget, the objective, and one line per
    executed step.  Raw receipts are bounded so a long task cannot flood the
    prompt with its own history.
    """
    return {
        "objective": objective,
        "grant_status": grant_contract.grant_status(grant, now=utcnow()),
        "remaining_budget": grant_contract.remaining(grant),
        "permissions": {
            key: value
            for key, value in (grant.get("permissions") or {}).items()
            if value
        },
        "steps": [
            {
                "n": index + 1,
                "capability_id": step.get("capability_id"),
                "status": step.get("status"),
                "outcome": step.get("outcome"),
                "original_task_id": step.get("original_task_id"),
                "error_code": step.get("error_code"),
                "message": str(step.get("message") or "")[:240],
            }
            for index, step in enumerate(steps[-8:])
        ],
        "satisfied_count": sum(1 for step in steps if step.get("satisfied")),
    }


def _compress(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= MAX_TRANSCRIPT:
        return messages
    head = messages[:1]
    tail = messages[-(MAX_TRANSCRIPT - 2):]
    summary = {
        "role": "user",
        "content": "【较早步骤摘要】\n" + summarise_messages(messages[1:-(MAX_TRANSCRIPT - 2)]),
    }
    return [*head, summary, *tail]


def run(
    db,
    settings,
    *,
    grant_id: str,
    objective: str,
    tools: list[ToolSpec],
    actor: str = "local-user",
    session_id: str | None = None,
    app=None,
    gateway=None,
    limits: LoopLimits | None = None,
    max_completion_tokens: int = 1200,
) -> LoopResult:
    """Advance one authorized task until it is done or must stop.

    The loop never calls an executor directly: every tool call becomes a
    :func:`shine.support.actions.execute_under_grant` receipt.
    """
    from shine.support import actions as action_service

    config = limits or LoopLimits()
    spec_by_name = {spec.name: spec for spec in tools}
    started = time.monotonic()
    steps: list[dict[str, Any]] = []
    no_progress = 0
    last_block_code: str | None = None
    llm_info: dict[str, Any] = {"native_tool_calling": None, "turns": 0, "model": None}
    final_text = ""

    loaded = grant_service.load(db, grant_id)
    granted_capabilities = sorted(
        name for name in spec_by_name
        if grant_contract.capability_allowed(loaded.grant, name)
    )
    usable_tools = [spec_by_name[name] for name in granted_capabilities]
    if not usable_tools:
        return LoopResult(
            status="PAUSED_SCOPE",
            stop_reason="NO_CAPABILITY_IN_GRANT",
            final="本次授权没有覆盖任何可执行的准备能力；请在授权时勾选需要的范围。",
            steps=steps,
            llm=llm_info,
        )

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"任务目标：{objective}\n"
                "请调用已注册的工具推进；每次只做一个操作，并在收到服务端回执后再决定下一步。"
                "目标达成或必须停下时调用 finish。"
            ),
        }
    ]

    for step_index in range(config.max_steps):
        loaded = grant_service.load(db, grant_id)
        if grant_contract.grant_status(loaded.grant, now=utcnow()) not in grant_contract.AUTHORIZED_STATUSES:
            return LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="GRANT_NOT_RUNNING",
                final="本次授权已不在可执行状态，已停止后续自动操作。",
                steps=steps,
                llm=llm_info,
            )
        try:
            grant_service.assert_tool_call_budget(loaded.grant)
        except grant_contract.GrantError as exc:
            return LoopResult(
                status="PAUSED_BUDGET", stop_reason=exc.code,
                final=exc.message, steps=steps, llm=llm_info,
            )
        if time.monotonic() - started > config.max_wall_seconds:
            return LoopResult(
                status="PAUSED_BUDGET", stop_reason="WALL_CLOCK_EXHAUSTED",
                final="本次自动执行已达到时间上限，已停止。", steps=steps, llm=llm_info,
            )

        state = _state_summary(objective=objective, grant=loaded.grant, steps=steps)
        try:
            turn = _next_turn(
                db,
                gateway,
                system=_copilot_system_prompt(),
                messages=_compress(messages),
                tools=usable_tools,
                state=state,
                llm_info=llm_info,
                max_completion_tokens=max_completion_tokens,
            )
        except LLMNotConfigured as exc:
            return LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="LLM_NOT_CONFIGURED",
                final=f"当前没有可用的文本模型，无法继续自动执行：{exc}",
                steps=steps,
                llm={**llm_info, "error": "LLM_NOT_CONFIGURED"},
            )
        if turn is None:
            return LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="LLM_UNAVAILABLE",
                final="当前没有可用的文本模型，无法继续自动执行；请先在设置中配置模型。",
                steps=steps,
                llm=llm_info,
            )
        llm_info["turns"] = int(llm_info.get("turns") or 0) + 1
        llm_info["model"] = turn.model or llm_info.get("model")
        if turn.native:
            llm_info["native_tool_calling"] = True

        if not turn.wants_tools:
            final_text = turn.text or ""
            # The model stopped.  Whether the task *is* done is the server's
            # call, from evidence it already collected.
            if any(step.get("satisfied") for step in steps):
                return LoopResult(
                    status="SUCCEEDED",
                    stop_reason="MODEL_FINISHED_WITH_EVIDENCE",
                    final=final_text or "目标已在本机权威回执中达成。",
                    steps=steps,
                    llm=llm_info,
                    usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
                )
            return LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="NO_AUTHORITATIVE_EVIDENCE",
                final=(
                    final_text
                    or "AI 认为可以结束，但没有产生任何可核验的原任务回执；"
                    "为避免误报完成，已停在待确认状态。"
                ),
                steps=steps,
                llm=llm_info,
                usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
            )

        call = turn.tool_calls[0]
        if call.name not in spec_by_name:
            # A tool the model invented is a refusal, not an attempt.
            steps.append(
                {
                    "capability_id": call.name,
                    "status": "REFUSED",
                    "outcome": "UNKNOWN_TOOL",
                    "error_code": "TOOL_NOT_DECLARED",
                    "message": "模型提出了未注册的工具，已拒绝。",
                    "satisfied": False,
                }
            )
            no_progress += 1
            if no_progress >= config.max_no_progress:
                return LoopResult(
                    status="FAILED", stop_reason="TOOL_NOT_DECLARED",
                    final="模型反复提出未注册的工具，已停止。", steps=steps, llm=llm_info,
                )
            messages.append({"role": "assistant", "content": f"（请求了未注册工具 {call.name}）"})
            messages.append(
                {
                    "role": "user",
                    "content": "该工具未注册，不能执行。请只使用已提供的工具，或调用 finish。",
                }
            )
            continue

        tool_call_id = f"{grant_id}:{step_index + 1}:{call.id}"[:96]
        receipt = action_service.execute_under_grant(
            db,
            settings,
            grant_id=grant_id,
            capability_id=call.name,
            params=dict(call.arguments or {}),
            tool_call_id=tool_call_id,
            actor=actor,
            session_id=session_id,
            expected_target=_expected_from(call.arguments),
            app=app,
            gateway=gateway,
        )
        outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
        steps.append(
            {
                "capability_id": call.name,
                "status": receipt.get("status"),
                "outcome": outcome,
                "error_code": receipt.get("error_code"),
                "message": receipt.get("message"),
                "action_id": receipt.get("action_id"),
                "original_task_id": receipt.get("original_task_id"),
                "satisfied": _satisfied(receipt),
                "reused": bool((receipt.get("observed_facts") or {}).get("reused_existing_action")),
            }
        )

        stop = _must_stop(receipt)
        if stop:
            return LoopResult(
                status=stop,
                stop_reason=str(receipt.get("error_code") or outcome or receipt.get("status")),
                final=(
                    "原执行器给出了需要人工核对的结果，已停止自动推进并保留现场："
                    f"{receipt.get('message') or receipt.get('status')}"
                ),
                steps=steps,
                llm=llm_info,
                usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
            )

        code = receipt.get("error_code")
        if not _satisfied(receipt):
            if code and code == last_block_code:
                no_progress += 1
            else:
                no_progress = 1 if code else 0
            last_block_code = code
            if no_progress >= config.max_no_progress:
                return LoopResult(
                    status="FAILED" if code else "PAUSED_USER_DECISION",
                    stop_reason=str(code or "NO_PROGRESS"),
                    final=f"连续 {no_progress} 次没有取得进展，已停止：{receipt.get('message') or code}",
                    steps=steps,
                    llm=llm_info,
                )
        else:
            no_progress = 0
            last_block_code = None

        messages.append(
            {
                "role": "assistant",
                "content": f"调用 {call.name}，参数 {call.arguments}",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": "服务端回执（原执行者事实）："
                + _receipt_text(receipt),
            }
        )

    return LoopResult(
        status="PAUSED_BUDGET",
        stop_reason="STEP_BUDGET_EXHAUSTED",
        final="本次自动执行已达到步数上限，已停止并保留全部已产生的回执。",
        steps=steps,
        llm=llm_info,
        usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
    )


def _expected_from(arguments: dict[str, Any]) -> dict[str, Any]:
    """Only declared expectation keys travel; nothing else is inferred."""
    expected: dict[str, Any] = {}
    for key in ("installation_id", "target_identity"):
        value = (arguments or {}).get(key)
        if isinstance(value, str) and value:
            expected[key] = value
    return expected


def _receipt_text(receipt: dict[str, Any]) -> str:
    facts = receipt.get("observed_facts") or {}
    import json

    return json.dumps(
        {
            "status": receipt.get("status"),
            "action_id": receipt.get("action_id"),
            "original_task_id": receipt.get("original_task_id"),
            "error_code": receipt.get("error_code"),
            "message": receipt.get("message"),
            "facts": facts,
            "next_available_actions": receipt.get("next_available_actions"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )[:2000]


def _copilot_system_prompt() -> str:
    return (
        "你是 Shine Video Studio 的本机 AI 管家，正在执行一次已经获得用户授权的任务。\n"
        "规则：\n"
        "1. 只能调用已提供的工具；不得编造工具名、参数或文件路径。\n"
        "2. 环境是否就绪只以 /api/v1/setup/verdict 为准；工作流是否可生产只以原验证回执为准。\n"
        "3. 不得修改源码、节点源码或内置模板，不得执行命令、脚本、pip 或 git。\n"
        "4. 收到 UNKNOWN / 需要核对 的回执时必须停止，不得重试提交。\n"
        "5. 目标达成后调用 finish 并简述依据；没有原任务回执时不要声称完成。\n"
        "6. 一次只提出一个工具调用。\n"
    )


def _next_turn(
    db,
    gateway,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec],
    state: dict[str, Any],
    llm_info: dict[str, Any],
    max_completion_tokens: int,
) -> ToolTurn | None:
    """One model turn: native tool calling when available, validated intent otherwise."""
    if gateway is None:
        return None
    # Native first.  A provider that cannot do it returns None, and the
    # degradation is recorded once per task rather than per turn.
    if llm_info.get("native_tool_calling") is not False:
        try:
            turn = gateway.complete_tools_for_role(
                db,
                "support_agent",
                system=system,
                messages=_native_messages(messages),
                tools=tools,
                options={"max_completion_tokens": max_completion_tokens},
            )
        except LLMNotConfigured:
            raise
        except LLMError:
            turn = None
        if turn is not None:
            return turn
        llm_info["native_tool_calling"] = False
        llm_info["degraded_reason"] = "PROVIDER_WITHOUT_NATIVE_TOOL_CALLS"

    # Structured fallback through the ordinary, schema-validated path.
    try:
        response = gateway.complete_for_role(
            db,
            "support_agent",
            system=system + "\n\n" + fallback_system_prompt(tools),
            user=fallback_user_prompt(state),
            schema=FALLBACK_SCHEMA,
            budget={"completion_cap": max_completion_tokens},
        )
    except LLMNotConfigured:
        raise
    except LLMError as exc:
        llm_info["degraded_error"] = exc.code
        return None
    reply = response.parsed
    if reply is None:
        return None
    try:
        turn = parse_fallback_reply(reply, tool_names={spec.name for spec in tools})
    except LLMError as exc:
        llm_info["degraded_error"] = exc.code
        return None
    turn.model = response.model
    turn.latency_ms = response.latency_ms
    turn.usage = response.usage
    return turn


def _native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript is already in a protocol-neutral shape (role/content)."""
    return [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in messages
    ]


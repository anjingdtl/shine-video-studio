"""The one call-budget calculation for literary work.

``plan_call_budget`` answers a single question: given the *rendered* request and
a provider profile, what completion budget may this request ask for, and does it
fit the window at all?

The rule is the plan's §6.1, expressed once:

    O_hard    = floor(0.60 x verified_effective_output_max)
    O_request = min(O_hard, ceil(visible_goal + reasoning_reserve + format_overhead))
    I_final + O_request + M_safety <= verified_context_window

Three deliberate positions:

* ``O_hard`` is a ceiling, not the default demand.  Asking for 60% of a model's
  maximum on every small structured call spends the provider's patience and
  makes reasoning-heavy models truncate their visible answer.
* a provider that declares no window is **not** treated as a 1M-token window.
  The budget is marked unverified and computed against a conservative planning
  window, so nobody can quote a 1M figure the workbench invented.
* a schema's ``maxLength`` x ``maxItems`` product is a *risk check*, never the
  target output size; the target comes from the task's visible goal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from shine.literary.requests import CompiledRequest
from shine.llm.base import ProviderConfig
from shine.llm.output_budget import CREATIVE_COMPLETION_SHARE, creative_completion_cap
from shine.llm.transport_policy import streaming_enabled

SAFETY_MARGIN_TOKENS = 512
"""Room kept free in the window for the provider's own accounting."""

FORMAT_OVERHEAD_TOKENS = 256
"""The JSON keys, braces and quoting a structured answer adds to the prose."""

MIN_COMPLETION_TOKENS = 4_096
"""The smallest completion budget worth sending.

A reasoning model spends thousands of tokens before it writes the first visible
character; a 512-token ceiling does not produce a shorter answer, it produces a
timeout.  Measured live: a scene with a 3.3k budget hit the 300s wall twice while
a 6.4k budget finished the same kind of scene in under two minutes.
"""
"""A request that cannot reserve this much completion room is not sent."""

UNVERIFIED_PLANNING_WINDOW = 262_144
"""The window an undeclared provider is planned against.

The plan forbids pretending an undeclared model has a 1M window.  This value is
a *planning* figure, never a capability claim: every budget computed from it is
marked ``window_verified=False`` and carries ``CONTEXT_WINDOW_UNVERIFIED``.
"""

SOFT_INPUT_SHARE = 0.80
BURST_INPUT_SHARE = 0.95

PURPOSE_GOAL_MULTIPLIER = {
    "scene": 0.55,
    "creative": 0.50,
    "outline": 0.45,
    "observation": 0.10,
    "qa": 0.08,
    "structured": 0.30,
}
PURPOSE_MIN_GOAL = {
    "scene": 2_048,
    "creative": 4_096,
    "outline": 4_096,
    "observation": 1_024,
    "qa": 768,
    "structured": 1_024,
}


@dataclass(frozen=True)
class CallBudget:
    """One approved request budget with all of its evidence."""

    purpose: str
    input_tokens: int
    context_window_tokens: int | None
    window_verified: bool
    window_source: str
    output_cap_tokens: int
    output_request_tokens: int
    margin_tokens: int
    visible_goal_tokens: int
    reasoning_reserve_tokens: int
    reasoning_reserve_known: bool
    soft_input_limit: int
    burst_input_limit: int
    fits: bool
    streaming: bool = False
    refusal: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def completion_cap(self) -> int:
        return max(0, self.output_request_tokens)

    @property
    def total_tokens_estimated(self) -> int:
        return self.input_tokens + self.output_request_tokens + self.margin_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "input_tokens_estimated": self.input_tokens,
            "context_window_tokens": self.context_window_tokens,
            "window_verified": self.window_verified,
            "window_source": self.window_source,
            "output_cap_tokens": self.output_cap_tokens,
            "completion_cap": self.output_request_tokens,
            "output_request_tokens": self.output_request_tokens,
            "margin_tokens": self.margin_tokens,
            "visible_goal_tokens": self.visible_goal_tokens,
            "reasoning_reserve_tokens": self.reasoning_reserve_tokens,
            "reasoning_reserve_known": self.reasoning_reserve_known,
            "soft_input_limit": self.soft_input_limit,
            "streaming": self.streaming,
            "burst_input_limit": self.burst_input_limit,
            "total_tokens_estimated": self.total_tokens_estimated,
            "fits": self.fits,
            "completion_share": CREATIVE_COMPLETION_SHARE,
            "warnings": list(self.warnings),
            "sources": list(self.sources),
            **({"refusal": dict(self.refusal)} if self.refusal else {}),
        }


def _positive(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def default_visible_goal(input_tokens: int, purpose: str) -> int:
    """A defensible visible-output target from the input size and the task."""
    multiplier = PURPOSE_GOAL_MULTIPLIER.get(purpose, PURPOSE_GOAL_MULTIPLIER["creative"])
    floor = PURPOSE_MIN_GOAL.get(purpose, PURPOSE_MIN_GOAL["creative"])
    return max(floor, int(math.ceil(input_tokens * multiplier)))


def plan_call_budget(
    config: ProviderConfig,
    request: CompiledRequest,
    *,
    purpose: str = "creative",
    visible_goal_tokens: int | None = None,
    reasoning_reserve_tokens: int | None = None,
    output_ceiling: int | None = None,
) -> CallBudget:
    """Compute the one approved completion budget for this request."""
    cap_info = creative_completion_cap(config)
    warnings: list[str] = []
    sources = list(cap_info.get("sources") or [])

    hard_cap = int(cap_info["completion_cap"])
    if output_ceiling:
        hard_cap = min(hard_cap, output_ceiling)

    goal = visible_goal_tokens if isinstance(visible_goal_tokens, int) and visible_goal_tokens > 0 \
        else default_visible_goal(request.input_tokens, purpose)
    goal = min(goal, hard_cap)

    configured_reserve = _positive(config.generation_options.get("reasoning_reserve_tokens"))
    reserve_known = reasoning_reserve_tokens is not None or configured_reserve is not None
    # A reasoning model's private deliberation is proportional to the task, not
    # a rounding error: reserve at least twice the visible goal, never less than
    # 4k, and cap it so one call still fits a generous window.
    reserve = reasoning_reserve_tokens or configured_reserve or \
        max(4_096, min(16_384, int(math.ceil(goal * 2.0))))
    if not reserve_known:
        warnings.append("REASONING_RESERVE_CONSERVATIVE")

    requested = min(hard_cap, int(math.ceil(goal + reserve + FORMAT_OVERHEAD_TOKENS)))
    requested = max(requested, min(hard_cap, MIN_COMPLETION_TOKENS))

    declared_window = _positive(config.context_window_tokens)
    window = declared_window or UNVERIFIED_PLANNING_WINDOW
    window_verified = bool(declared_window)
    window_source = config.context_window_source if declared_window else "conservative_planning_window"
    if not window_verified:
        warnings.append("CONTEXT_WINDOW_UNVERIFIED")

    free = max(0, window - requested - SAFETY_MARGIN_TOKENS)
    soft_limit = int(math.floor(free * SOFT_INPUT_SHARE))
    burst_limit = int(math.floor(free * BURST_INPUT_SHARE))

    refusal: dict[str, Any] | None = None
    # Order matters: when the source alone does not fit, the honest reason is
    # the window, not the output allowance.  Reporting "budget starved" there
    # would send the operator to look at the wrong setting.
    if request.input_tokens + MIN_COMPLETION_TOKENS + SAFETY_MARGIN_TOKENS > window:
        refusal = {
            "code": "LLM_CREATIVE_CONTEXT_OVERFLOW",
            "message_zh": "本单元内容超出模型窗口，尚未发送；请拆分该单元后重试。",
            "next_action": "split_unit_then_retry",
            "input_tokens_estimated": request.input_tokens,
            "context_window_tokens": window,
        }
    elif requested < MIN_COMPLETION_TOKENS:
        refusal = {
            "code": "LLM_OUTPUT_BUDGET_STARVED",
            "message_zh": "这个模型可用的输出额度太小，无法写出一场完整剧本。",
            "next_action": "choose_model_with_larger_output",
            "requested_tokens": requested,
        }
    elif request.input_tokens + requested + SAFETY_MARGIN_TOKENS > window:
        refusal = {
            "code": "LLM_CREATIVE_CONTEXT_OVERFLOW",
            "message_zh": "本单元内容超出模型窗口，尚未发送；请拆分该单元后重试。",
            "next_action": "split_unit_then_retry",
            "external_outcome": "NOT_SENT",
            "context_window_tokens": window,
            "estimated_input_tokens": request.input_tokens,
            "output_request_tokens": requested,
            "exceeded_by": request.input_tokens + requested + SAFETY_MARGIN_TOKENS - window,
            "suggested_max_input_tokens": max(0, window - requested - SAFETY_MARGIN_TOKENS),
        }
        warnings.append("REQUEST_NOT_SENT_CONTEXT_OVERFLOW")

    return CallBudget(
        purpose=purpose,
        input_tokens=request.input_tokens,
        context_window_tokens=window,
        window_verified=window_verified,
        window_source=window_source,
        output_cap_tokens=hard_cap,
        output_request_tokens=requested,
        margin_tokens=SAFETY_MARGIN_TOKENS,
        visible_goal_tokens=goal,
        reasoning_reserve_tokens=reserve,
        reasoning_reserve_known=reserve_known,
        soft_input_limit=soft_limit,
        burst_input_limit=burst_limit,
        fits=refusal is None,
        refusal=refusal,
        warnings=warnings,
        sources=sources,
        streaming=streaming_enabled(config),
    )


def schema_shape_risk(schema: dict[str, Any] | None) -> dict[str, Any]:
    """The worst-case shape of a schema, reported as risk rather than budget.

    ``maxLength x maxItems`` can describe megabytes of JSON.  It says what the
    contract *permits*, not what one answer should contain, so it is recorded
    for diagnostics and never used as the completion target.
    """
    if not isinstance(schema, dict):
        return {}
    worst_chars = 0

    def walk(node: Any, factor: int) -> None:
        nonlocal worst_chars
        if not isinstance(node, dict):
            return
        own = 1
        if node.get("type") == "array":
            own = _positive(node.get("maxItems")) or 0
        length = _positive(node.get("maxLength")) or 0
        if length and own:
            worst_chars = max(worst_chars, length * own * factor)
        for value in node.get("properties", {}).values():
            walk(value, factor * own)
        if isinstance(node.get("items"), dict):
            walk(node["items"], factor * own)

    walk(schema, 1)
    if not worst_chars:
        return {}
    return {
        "worst_case_chars": worst_chars,
        # A CJK character is ~1 token and 3 UTF-8 bytes, so the shared
        # bytes/3 estimate of a CJK-only worst case is the character count.
        "worst_case_tokens": worst_chars,
        "is_risk_only": True,
    }

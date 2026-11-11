"""Creative-task output budgets: the 60% completion cap with auditable sources.

Long-form creative calls (global story bible, volume planning, scene scripts)
get a production-relevant budget rule that small structured agents do not.
The four-stage plan pins the rule:

    per-call completion cap = floor(60% x verified effective output maximum)

* effective_output_max = min(provider-declared max output, user-configured
  cap, adapter cap).  Reasoning/thinking tokens count toward the same
  completion budget when the provider bills them as completion, so the cap
  includes them by construction.
* With nothing verifiable, the conservative defaults stay (131,072 - the 128K
  output ceiling a 1M-context reasoning model is budgeted against) and
  output_max_verified is False: a fallback never certifies a model maximum, it
  only states the ceiling the workbench refuses to exceed.
* On a shared window the cap can never exceed window minus the estimated
  input: the completion budget is reserved out of the same window the input
  uses, so a huge input can only shrink the cap, never grow it.
"""
from __future__ import annotations

import math

from shine.llm.base import ProviderConfig

CREATIVE_COMPLETION_SHARE = 0.60
CONSERVATIVE_DEFAULT_LIMIT = 131_072
MINIMAX_M31_FLASH_DEFAULT_LIMIT = 131_072
ANTHROPIC_DEFAULT_MAX_TOKENS = 4_096


def _model_id(config: ProviderConfig) -> str:
    return str(config.model or "").strip().casefold().replace("_", "-")


def _positive_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _adapter_cap(config: ProviderConfig) -> int | None:
    """The per-kind adapter ceiling the request itself cannot exceed."""
    if config.kind == "anthropic":
        option_max = config.generation_options.get("max_tokens")
        option_max = _positive_int(option_max) if not isinstance(option_max, bool) else None
        if option_max:
            return option_max
        return ANTHROPIC_DEFAULT_MAX_TOKENS
    return None


def _probed_cap(config: ProviderConfig) -> int | None:
    """A cap an actual probe established, when the profile recorded one."""
    return _positive_int(config.generation_options.get("probed_max_output_tokens"))


def adapter_token_semantics(config: ProviderConfig) -> dict:
    """How one adapter counts the completion budget, recorded for the audit.

    The three kinds do not mean the same thing by "max tokens": an
    OpenAI-compatible request caps visible plus reasoning completion, an
    Ollama request caps generated tokens, and an Anthropic ``max_tokens`` is
    the whole assistant turn.  Recording the semantics keeps a later reader
    from comparing two adapters' numbers as if they were the same unit.
    """
    semantics = {
        "openai_compatible": "max_completion_tokens caps visible+reasoning completion tokens",
        "ollama": "max_tokens caps generated tokens for this call",
        "anthropic": "max_tokens caps the whole assistant turn",
        "mock": "no provider budget applies",
    }
    return {"capability_class": config.kind,
            "completion_budget_semantics": semantics.get(config.kind, "unknown adapter semantics")}


def creative_completion_cap(config: ProviderConfig, *, estimated_input_tokens: int = 0) -> dict:
    """The per-call completion cap for one creative call, with its sources.

    Three different things can bound a completion budget and they are not
    interchangeable evidence:

    * a *capability* bound - the provider declared it in its model metadata,
      or a probe established it.  Only these may certify the ceiling.
    * a *constraint* - the profile's configured cap or the adapter's own
      ceiling.  A configuration states what the operator allows, never what
      the model can do, so it can only lower the cap.
    * a *fallback* - no capability evidence at all, so the conservative
      default applies and ``output_max_verified`` stays False.

    Returns completion_cap, output_max_verified, capability_basis, sources,
    share and ``requires_capability_confirmation``.
    """
    sources: list[dict] = []
    declared = _positive_int(config.max_output_tokens)
    probed = _probed_cap(config)
    configured = _positive_int(config.generation_options.get("max_completion_tokens"))
    adapter_cap = _adapter_cap(config)

    if probed:
        sources.append({"source": "probed_output_max", "max": probed, "kind": "capability"})
    if declared:
        sources.append({"source": "provider_declared_max_output", "max": declared,
                        "kind": "capability"})
    if configured:
        sources.append({"source": "profile_configured_cap", "max": configured,
                        "kind": "constraint"})
    if adapter_cap:
        sources.append({"source": "adapter_cap", "max": adapter_cap, "kind": "constraint"})

    capability = [int(item["max"]) for item in sources if item["kind"] == "capability"]
    constraints = [int(item["max"]) for item in sources if item["kind"] == "constraint"]
    if capability:
        ceiling = min([min(capability), *constraints])
        cap = int(math.floor(ceiling * CREATIVE_COMPLETION_SHARE))
        verified = True
        basis = "capability_min_chain"
    else:
        model = _model_id(config)
        baseline = MINIMAX_M31_FLASH_DEFAULT_LIMIT if model in {
            "minimax-m3.1-flash", "minimax-m3-1-flash"} else CONSERVATIVE_DEFAULT_LIMIT
        # Without capability evidence the conservative default is the ceiling:
        # a configured value may lower it but can never raise it, so an
        # unverified provider is never sent a budget it merely asked for.
        ceiling = min([baseline, *(int(math.floor(value * CREATIVE_COMPLETION_SHARE))
                                   for value in constraints)])
        cap = ceiling
        verified = False
        basis = "unverified_conservative_ceiling"
        sources.append({"source": "conservative_default_not_declared", "max": cap,
                        "kind": "fallback"})

    window = _positive_int(config.context_window_tokens)
    if window:
        share_of_window = window - max(0, estimated_input_tokens)
        if share_of_window > 0 and share_of_window < cap:
            cap = share_of_window
            sources.append({"source": "shared_window_reserve", "max": share_of_window,
                            "kind": "constraint"})
    return {
        "completion_cap": cap,
        "output_max_verified": verified,
        "capability_basis": basis,
        "requires_capability_confirmation": not verified,
        "sources": sources,
        "share": CREATIVE_COMPLETION_SHARE,
        **adapter_token_semantics(config),
    }


def usage_summary(usage: dict | None) -> dict:
    """A readable extraction of a response usage object.

    input_tokens / output_tokens come from the provider's own usage; the
    reasoning count is what the provider reports inside completion details -
    it is never estimated, and stays unavailable when the provider does not
    report it, because guessing a reasoning number would be evidence the
    workbench does not have.
    """
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _positive_int(usage.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _positive_int(usage.get("input_tokens"))
    output_tokens = _positive_int(usage.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _positive_int(usage.get("output_tokens"))
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    reasoning = _positive_int(details.get("reasoning_tokens"))
    if reasoning is None:
        reasoning = _positive_int(details.get("reasoning"))
    if reasoning is None:
        reasoning = _positive_int(usage.get("reasoning_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning if reasoning is not None else None,
        "reasoning_available": reasoning is not None,
    }


def truncated_output(exc: Exception) -> bool:
    """True when an LLMError says the provider ended the completion early.

    A truncated completion is never a finished scene script: the task must
    stop as INCOMPLETE/NEEDS_REVIEW, keep any persisted evidence, and let a
    new request continue from the persisted boundary instead of pretending.
    """
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        finish = details.get("finish_reason")
        if finish in {"length", "max_tokens", "content_filter"}:
            return True
    return "truncated" in str(exc).casefold()


def apply_output_budget(config: ProviderConfig, budget: dict) -> ProviderConfig:
    """Inject the computed completion cap into the request, per adapter kind.

    The cap is the whole per-call completion budget (visible text, JSON
    overhead and any reasoning tokens the provider bills as completion).
    """
    cap = budget.get("completion_cap")
    streaming = bool(budget.get("streaming"))
    if isinstance(config.kind, str) and cap:
        if config.kind == "openai_compatible":
            from dataclasses import replace
            return replace(config, generation_options={
                **config.generation_options, "max_completion_tokens": cap}, streaming=streaming)
        if config.kind == "ollama":
            from dataclasses import replace
            return replace(config, generation_options={
                **config.generation_options, "max_tokens": cap}, streaming=streaming)
        if config.kind == "anthropic":
            from dataclasses import replace
            return replace(config, generation_options={
                **config.generation_options, "max_tokens": cap}, streaming=streaming)
    return replace(config, streaming=streaming)


def estimate_input_tokens(text: str) -> int:
    """The same deliberately conservative estimate the agent calls use.

    CJK costs about one token per character and UTF-8 spends three bytes on
    it; dividing the byte count by three over-estimates ASCII, which is the
    safe direction for a budget that refuses rather than truncates.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


#: A creative call that cannot leave this much completion room in a shared
#: window is refused before sending: a request whose input nearly fills the
#: window can neither produce a usable answer nor fail as a clean refusal.
MIN_CREATIVE_COMPLETION_RESERVE = 4_096


def context_overflow(config: ProviderConfig, estimated_input_tokens: int) -> dict | None:
    """The refusal evidence when the input cannot fit the shared window.

    Returns None when the request fits; otherwise a named-evidence dict.  A
    refusal raised from this check is provably NOT_SENT: the request never
    left the workbench, so a new request after a real fix is a new decision,
    not a replay of an unknown outcome.
    """
    window = config.context_window_tokens
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        return None
    headroom = window - estimated_input_tokens
    if headroom >= MIN_CREATIVE_COMPLETION_RESERVE:
        return None
    return {
        "code": "LLM_CREATIVE_CONTEXT_OVERFLOW",
        "context_window_tokens": window,
        "estimated_input_tokens": estimated_input_tokens,
        "headroom_tokens": headroom,
        "required_reserve_tokens": MIN_CREATIVE_COMPLETION_RESERVE,
        "external_outcome": "NOT_SENT",
    }

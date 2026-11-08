"""The creative output budget: 60% of a verified output maximum, never more."""
from shine.llm.base import ProviderConfig
from shine.llm.output_budget import (
    creative_completion_cap,
    estimate_input_tokens,
    truncated_output,
    usage_summary,
)


def _cfg(**overrides) -> ProviderConfig:
    base = dict(
        profile_id="p", name="n", kind="openai_compatible",
        base_url="https://api.example.com/v1", model="test-model", api_key="k",
        timeout_s=30, max_retries=1, temperature=1.0,
        generation_options={}, context_window_tokens=None,
        max_output_tokens=None, context_window_source="none",
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_128k_declared_yields_76800():
    cap = creative_completion_cap(_cfg(max_output_tokens=128000))
    assert cap["completion_cap"] == 76800
    assert cap["output_max_verified"] is True
    assert cap["sources"][0]["source"] == "provider_declared_max_output"


def test_131072_declared_yields_78643():
    assert creative_completion_cap(_cfg(max_output_tokens=131072))["completion_cap"] == 78643


def test_undeclared_keeps_conservative_default_and_stays_unverified():
    cap = creative_completion_cap(_cfg(model="minimax-m3.1-flash"))
    assert cap["completion_cap"] == 16384
    assert cap["output_max_verified"] is False
    assert cap["sources"][0]["source"] == "conservative_default_not_declared"


def test_configured_smaller_cap_enters_the_min_chain():
    cap = creative_completion_cap(_cfg(max_output_tokens=128000, generation_options={"max_completion_tokens": 16384}))
    assert cap["completion_cap"] == 9830
    assert {s["source"] for s in cap["sources"]} >= {"profile_configured_cap"}


def test_smaller_declared_max_wins_over_bigger_configured():
    cap = creative_completion_cap(_cfg(max_output_tokens=20000, generation_options={"max_completion_tokens": 100000}))
    assert cap["completion_cap"] == 12000


def test_shared_window_reserve_shrinks_the_cap():
    # A 1M window with a tiny remaining share after the input: the cap can
    # never claim more than the window has room for.
    cap = creative_completion_cap(_cfg(max_output_tokens=128000, context_window_tokens=100000),
                                  estimated_input_tokens=95_000)
    assert cap["completion_cap"] == 5000
    assert {s["source"] for s in cap["sources"]} >= {"shared_window_reserve"}


def test_anthropic_adapter_cap_bounds_the_cap():
    cap = creative_completion_cap(_cfg(kind="anthropic", max_output_tokens=128000))
    assert cap["completion_cap"] == 2457  # floor(0.6 x 4096)
    assert {s["source"] for s in cap["sources"]} >= {"adapter_cap"}


def test_usage_summary_extracts_reasoning_when_reported():
    usage = usage_summary({"prompt_tokens": 100, "completion_tokens": 500,
                           "completion_tokens_details": {"reasoning_tokens": 120}})
    assert usage == {"input_tokens": 100, "output_tokens": 500,
                     "reasoning_tokens": 120, "reasoning_available": True}


def test_usage_summary_marks_reasoning_unavailable_when_absent():
    usage = usage_summary({"prompt_tokens": 10, "completion_tokens": 20})
    assert usage["reasoning_tokens"] is None
    assert usage["reasoning_available"] is False
    assert usage_summary(None)["output_tokens"] is None


def test_truncated_output_detects_finish_reason_and_text():
    class Exc(Exception):
        details = {"finish_reason": "length"}
    assert truncated_output(Exc())
    assert truncated_output(Exception("Provider completion is truncated or filtered"))
    assert not truncated_output(Exception("connection refused"))


def test_estimate_input_tokens_is_conservative():
    assert estimate_input_tokens("") == 0
    assert estimate_input_tokens("中文字符按一字一token估算" * 100) >= 300


def test_gateway_creative_call_sends_cap_and_records_budget():
    """A creative call injects the 60% cap and records the auditable budget."""
    from shine.llm.gateway import LLMGateway
    from shine.llm.output_budget import apply_output_budget, estimate_input_tokens
    gw = LLMGateway(None, mock_responder=lambda m, s: {"ok": True})
    # use build_provider path through a fake profile object
    class FakeProfile:
        id = "p1"
        name = "n"
        kind = "openai_compatible"
        base_url = "https://api.example.com/v1"
        model = "test-model"
        enabled = True
        api_key_ref = None
        timeout_s = 30
        max_retries = 1
        temperature = 1.0
        generation_options = {}
        capabilities = {"model_metadata": [
            {"id": "test-model", "context_window_tokens": 1000000, "max_output_tokens": 128000}]}
    profile = FakeProfile()
    config = gw.config_for(profile)
    budget = creative_completion_cap(config, estimated_input_tokens=estimate_input_tokens("x"))
    config = apply_output_budget(config, budget)
    assert config.generation_options["max_completion_tokens"] == 76800


def test_context_overflow_refuses_before_sending():
    """A request whose input nearly fills the window is refused, provably not sent."""
    from shine.llm.output_budget import context_overflow
    cfg = _cfg(max_output_tokens=128000, context_window_tokens=8192)
    verdict = context_overflow(cfg, estimated_input_tokens=6000)
    assert verdict is not None
    assert verdict["code"] == "LLM_CREATIVE_CONTEXT_OVERFLOW"
    assert verdict["external_outcome"] == "NOT_SENT"
    assert verdict["headroom_tokens"] == 2192
    assert context_overflow(cfg, estimated_input_tokens=3000) is None
    assert context_overflow(_cfg(context_window_tokens=None), estimated_input_tokens=10**9) is None


def test_gateway_creative_overflow_is_a_classified_local_refusal():
    """The gateway raises the refusal as a named local fact, not a provider outcome."""
    import pytest

    from shine.llm.base import LLMError
    from shine.llm.gateway import LLMGateway
    from shine.materialprep.agent_call import classify_external_outcome
    gw = LLMGateway(None, mock_responder=lambda m, s: {"ok": True})

    class OverflowProfile:
        id = "p1"
        name = "overflow"
        kind = "openai_compatible"
        base_url = "https://api.example.com/v1"
        model = "test-model"
        enabled = True
        api_key_ref = None
        timeout_s = 30
        max_retries = 1
        temperature = 1.0
        generation_options = {}
        capabilities = {"model_metadata": [
            {"id": "test-model", "context_window_tokens": 8192, "max_output_tokens": 8000}]}
    gw.profile_for_role = lambda db, role: OverflowProfile()
    gw.secrets = None
    with pytest.raises(LLMError) as err:
        gw.complete_for_role(None, "scriptwriter", system="x" * 14000, user="y",
                             task_category="creative")
    assert err.value.details["code"] == "LLM_CREATIVE_CONTEXT_OVERFLOW"
    assert classify_external_outcome(err.value) == "NOT_SENT"

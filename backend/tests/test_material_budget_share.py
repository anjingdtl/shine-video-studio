from __future__ import annotations

from shine.llm.base import ProviderConfig
from shine.materialprep.agent_call import budget_report


def _config(**overrides):
    base = dict(
        profile_id="p", name="n", kind="openai_compatible",
        base_url="https://api.example.com/v1", model="m", api_key="k",
        timeout_s=30, max_retries=1, temperature=1.0, generation_options={},
        context_window_tokens=120_000, max_output_tokens=30_000,
        context_window_source="test_window",
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_budget_report_can_spend_only_two_thirds_of_the_window():
    context = {"text": "x" * 30_000}
    schema = {"type": "object"}
    system = "Return the declared JSON."
    full = budget_report(context, schema, system, _config())
    share = budget_report(context, schema, system, _config(), input_share=2 / 3)
    assert full["input_share"] == 1.0
    assert full["fits"] is True
    assert share["input_share"] == 2 / 3
    assert share["available_tokens"] == 80_000
    assert share["total_tokens_estimated"] == full["total_tokens_estimated"]
    assert share["fits"] == (share["total_tokens_estimated"] <= share["available_tokens"])
    # The output reserve is the completion budget identified by the shared
    # mechanism (plan_call_budget), not a six-figure constant.
    assert full["output_reserve_tokens"] == full["completion_cap"]
    assert full["output_reserve_tokens"] < 30_000


def test_budget_report_refuses_when_the_third_share_is_too_small():
    # 240k ASCII chars ~ 80k input tokens: the input alone fills the two-thirds
    # share, so adding the identified completion budget pushes it over.
    context = {"text": "x" * 240_000}
    assert budget_report(context, {}, "", _config())["fits"] is True
    assert budget_report(context, {}, "", _config(), input_share=2 / 3)["fits"] is False
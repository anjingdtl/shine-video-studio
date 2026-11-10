from __future__ import annotations

from types import SimpleNamespace

from shine.materialprep.agent_call import budget_report


def _config(**overrides):
    base = {
        "context_window_tokens": 120_000,
        "context_window_source": "test_window",
        "max_output_tokens": 30_000,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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


def test_budget_report_refuses_when_the_third_share_is_too_small():
    context = {"text": "x" * 180_000}
    assert budget_report(context, {}, "", _config())["fits"] is True
    assert budget_report(context, {}, "", _config(), input_share=2 / 3)["fits"] is False

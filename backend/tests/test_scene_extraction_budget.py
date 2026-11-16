"""场景提取的输出预算按结构化任务右尺寸（穿测发现的卡住根因）。

《雨夜归人》487 字符被逐场提取时，purpose="scene_prep" 不在 PURPOSE 映射里，
回退到 creative 档（visible goal 4096 + reasoning reserve 8192 → completion_cap
约 12–13K）。模型把大额度用来反复思考、迟迟不出可见答案，流式静默超过 idle
被切成 UNKNOWN，用户被迫逐段手动重试。健康修法不是加时限，而是让结构化抽取
拿结构化档（goal 1024 + reserve 4096 → cap 约 5–6K），约束思考量本身。
"""
import pytest

from shine.literary.budget import default_visible_goal, plan_call_budget
from shine.literary.requests import compile_request
from shine.llm.base import ProviderConfig

pytestmark = pytest.mark.unit


def _config(**overrides) -> ProviderConfig:
    base = dict(
        profile_id="p", name="n", kind="openai_compatible",
        base_url="https://api.example.com/v1", model="m", api_key="k",
        timeout_s=30, max_retries=1, temperature=1.0, generation_options={},
        context_window_tokens=200_000, max_output_tokens=32_000,
        context_window_source="provider_declared",
    )
    base.update(overrides)
    return ProviderConfig(**base)


def _request():
    return compile_request(system="Extract this scene's facts.",
                           user="深夜，雨丝斜落。阿远撑着一把黑伞走在街道上。" * 3,
                           schema={"type": "object"})


def test_scene_prep_uses_the_structured_goal_not_the_creative_fallback():
    # scene_prep covers a whole multi-scene segment (goal floor 4096) but keeps
    # the small low-effort reserve, unlike creative's deliberation budget.
    assert default_visible_goal(1_800, "scene_prep") == 4_096
    assert default_visible_goal(1_800, "creative") == 4_096
    prep = plan_call_budget(_config(), _request(), purpose="scene_prep")
    creative = plan_call_budget(_config(), _request(), purpose="creative")
    assert prep.reasoning_reserve_tokens == 1_024
    assert creative.reasoning_reserve_tokens >= 4_096


def test_scene_prep_asks_for_a_smaller_completion_than_creative():
    request = _request()
    scene_prep = plan_call_budget(_config(), request, purpose="scene_prep")
    creative = plan_call_budget(_config(), request, purpose="creative")
    assert scene_prep.visible_goal_tokens == 4_096
    assert scene_prep.reasoning_reserve_tokens == 1_024
    assert scene_prep.output_request_tokens < creative.output_request_tokens
    assert scene_prep.output_request_tokens <= scene_prep.output_cap_tokens


def test_a_large_scene_still_scales_but_stays_below_creative():
    request = compile_request(system="s", user="浪" * 6_000, schema={"type": "object"})
    scene_prep = plan_call_budget(_config(), request, purpose="scene_prep")
    creative = plan_call_budget(_config(), request, purpose="creative")
    assert scene_prep.output_request_tokens < creative.output_request_tokens


def test_low_effort_purposes_share_one_small_reserve_in_the_mechanism():
    # The reserve is decided by purpose inside plan_call_budget — the same rule
    # for crew (structured), scene extraction (scene_prep) and outline. Callers
    # do not inject their own reserve; that would fork the one verdict.
    request = compile_request(system="s", user="浪" * 6_000, schema={"type": "object"})
    structured = plan_call_budget(_config(), request, purpose="structured")
    scene_prep = plan_call_budget(_config(), request, purpose="scene_prep")
    outline = plan_call_budget(_config(), request, purpose="outline")
    creative = plan_call_budget(_config(), request, purpose="creative")
    for budget in (structured, scene_prep, outline):
        assert budget.reasoning_reserve_tokens == 1_024
        assert "REASONING_RESERVE_CONSERVATIVE" not in budget.warnings
    # Creative keeps the model's own deliberation budget.
    assert creative.reasoning_reserve_tokens >= 4_096
    assert "REASONING_RESERVE_CONSERVATIVE" in creative.warnings
    assert structured.output_request_tokens < creative.output_request_tokens


def test_a_truncation_retry_still_escalates_to_the_hard_cap():
    request = compile_request(system="s", user="浪" * 6_000, schema={"type": "object"})
    normal = plan_call_budget(_config(), request, purpose="structured")
    escalated = plan_call_budget(_config(), request, purpose="structured",
                                 output_ceiling=32_000, output_floor_tokens=32_000)
    assert escalated.output_request_tokens > normal.output_request_tokens
    assert escalated.output_request_tokens == escalated.output_cap_tokens
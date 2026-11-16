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
    assert default_visible_goal(1_800, "scene_prep") == 1_024
    assert default_visible_goal(1_800, "creative") == 4_096


def test_scene_prep_asks_for_a_smaller_completion_than_creative():
    request = _request()
    scene_prep = plan_call_budget(_config(), request, purpose="scene_prep")
    creative = plan_call_budget(_config(), request, purpose="creative")
    assert scene_prep.visible_goal_tokens == 1_024
    assert scene_prep.reasoning_reserve_tokens == 4_096
    assert scene_prep.output_request_tokens < creative.output_request_tokens
    assert scene_prep.output_request_tokens <= scene_prep.output_cap_tokens


def test_a_large_scene_still_scales_but_stays_below_creative():
    request = compile_request(system="s", user="浪" * 6_000, schema={"type": "object"})
    scene_prep = plan_call_budget(_config(), request, purpose="scene_prep")
    creative = plan_call_budget(_config(), request, purpose="creative")
    assert scene_prep.output_request_tokens < creative.output_request_tokens
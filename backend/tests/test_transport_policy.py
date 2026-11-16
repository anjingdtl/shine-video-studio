"""传输超时策略：长思考模型的事件间隔容忍度。

穿测《雨夜归人》时 MiniMax 在第 4/6 段静默思考约 5 分钟，旧 idle_s=base/2
（base=600 时 300s）把活着的长思考切成 LLM_CONNECTION/UNKNOWN，用户被迫逐段
手动重试。idle 提高到与首事件同等的 base 容忍度，让长但活着的思考跑完；总
deadline 仍约束整次调用。
"""
from shine.llm.base import ProviderConfig
from shine.llm.transport_policy import policy_for


def _config(timeout_s: float) -> ProviderConfig:
    return ProviderConfig(
        profile_id="p", name="n", kind="openai_compatible",
        base_url="http://127.0.0.1:1/v1", model="m", api_key="k",
        timeout_s=timeout_s, max_retries=0, temperature=0.2, generation_options={},
        context_window_tokens=100_000, max_output_tokens=8_000,
        context_window_source="provider_declared", streaming=True,
    )


def test_a_long_thinking_provider_gets_full_base_idle_headroom():
    policy = policy_for(_config(600.0), streaming=True)
    assert policy.idle_s == 600.0
    assert policy.first_event_s == 600.0
    assert policy.total_s == 3600.0
    assert policy.connect_s == 30.0


def test_idle_never_drops_below_the_floor():
    policy = policy_for(_config(10.0), streaming=True)
    assert policy.idle_s == 45.0
    assert policy.first_event_s == 120.0
    assert policy.total_s == 180.0


def test_a_short_provider_timeout_still_bounds_the_whole_call():
    policy = policy_for(_config(60.0), streaming=True)
    assert policy.idle_s == 60.0
    assert policy.total_s == 360.0
    assert policy.idle_s < policy.total_s
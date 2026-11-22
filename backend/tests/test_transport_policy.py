"""传输超时策略：idle 保持有界，长思考靠预算右尺寸而非加时限。

穿测《雨夜归人》时曾把 idle_s 从 base/2 提到 base 以容纳 MiniMax 的长思考，
但那是治标：场景提取拿了 creative 档的大额度，模型把额度用来反复思考。健康
修法是按任务右尺寸输出预算（见 test_scene_extraction_budget），让思考量本身
变小；idle 仍保持 base/2，及时识别真正停滞的连接，总 deadline 约束整次调用。
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


def test_idle_stays_bounded_at_half_the_base_timeout():
    policy = policy_for(_config(600.0), streaming=True)
    assert policy.idle_s == 300.0
    # 首事件死线独立封顶：推理模型边思考边发事件，静默超过上限即挂起，
    # 不应拿满 base（600s）等首事件让一个卡死的剧组步骤空等十分钟。
    assert policy.first_event_s == 300.0
    assert policy.total_s == 3600.0
    assert policy.connect_s == 30.0


def test_normal_profile_waits_300_seconds_for_stream_reads():
    policy = policy_for(_config(300.0), streaming=True)
    assert max(policy.first_event_s, policy.idle_s) == 300.0


def test_idle_never_drops_below_the_floor():
    policy = policy_for(_config(10.0), streaming=True)
    assert policy.idle_s == 45.0
    assert policy.first_event_s == 120.0
    assert policy.total_s == 180.0


def test_a_short_provider_timeout_still_bounds_the_whole_call():
    policy = policy_for(_config(60.0), streaming=True)
    assert policy.idle_s == 45.0
    assert policy.total_s == 360.0
    assert policy.idle_s < policy.total_s

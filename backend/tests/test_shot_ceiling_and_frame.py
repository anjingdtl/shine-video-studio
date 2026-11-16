"""单镜秒数上限（需求1）与导演包画幅/分辨率档位（需求2）并入既有统一控制链路。

两条都不另起链路：时长上限走 editorial_timing / crew 提示词 / validate_output
同一裁决（事实是冻结风格里的 max_shot_seconds）；画幅档位只产出 OutputSpec
宽高，走既有导演包冻结 → 方案宽高映射门禁 → 渲染器 31.inputs 链路。
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from shine.agents.crew_prompts import crew_prompt
from shine.agents.editorial_timing import (
    editorial_timing_blocker,
    editorial_timing_preview,
)
from shine.agents.film import validate_output
from shine.agents.film_contracts import DirectorialPlan, ShotIntent
from shine.h3.output_frame import frame_options, frame_size
from shine.schemas import OutputSpec, StyleBible

SEVEN = {
    "photography_language": "naturalistic handheld",
    "shot_blocking": "balanced depth",
    "color_palette": "muted teal",
    "lighting_tone": "soft key",
    "lens_texture": "fine grain",
    "editing_rhythm": "measured",
    "sound_performance": "diegetic only",
}


def _table() -> str:
    cuts = [0, 2400, 4300, 6000, 7700, 9400, 11100, 12800, 15000]
    table = "模式 **Ref2VA**\n| # | 切点 | 时长 |\n|---|---|---|\n"
    for i, (start, end) in enumerate(zip(cuts[:-1], cuts[1:], strict=True), 1):
        table += f"| {i} | {start / 1000:.3f} | {(end - start) / 1000:.3f}s |\n"
    return table


# --- 需求1：单镜秒数上限 ----------------------------------------------------------


def test_default_ceiling_keeps_the_pinned_eight_shot_grouping():
    plan = editorial_timing_preview(_table())
    assert [segment["duration_s"] for segment in plan["segments"]] == [4.3, 5.1, 5.6]


def test_a_lower_ceiling_caps_every_render_segment():
    default = editorial_timing_preview(_table())
    assert max(segment["duration_s"] for segment in default["segments"]) > 5.0
    capped = editorial_timing_preview(_table(), max_segment_s=5.0)
    if capped["segments"]:
        assert all(segment["duration_s"] <= 5.0 for segment in capped["segments"])
    else:
        blocker = editorial_timing_blocker(capped, max_segment_s=5.0)
        assert blocker["code"] == "EDITORIAL_TIMING_UNGROUPABLE"
        assert blocker["max_segment_s"] == 5.0


def test_the_ceiling_never_exceeds_h3_own_maximum():
    plan = editorial_timing_preview(_table(), max_segment_s=99.0)
    assert all(segment["duration_s"] <= 15.0 for segment in plan["segments"])


def test_the_director_prompt_states_the_same_ceiling_the_server_checks():
    prompt = crew_prompt("director", tool="propose", max_shot_seconds=10)
    assert "4-10 second render segment" in prompt
    assert "above 10 seconds" in prompt
    assert "4-15 second render segment" in crew_prompt("director", tool="propose")


def test_style_bible_ceiling_is_bounded_by_h3_floor_and_maximum():
    assert StyleBible(**SEVEN, max_shot_seconds=10).max_shot_seconds == 10
    with pytest.raises(ValidationError):
        StyleBible(**SEVEN, max_shot_seconds=3)
    with pytest.raises(ValidationError):
        StyleBible(**SEVEN, max_shot_seconds=16)


def test_an_unset_ceiling_keeps_the_older_snapshot_hash_shape():
    assert "max_shot_seconds" not in StyleBible(**SEVEN).model_dump(mode="json")


def _director_proposal(duration: float) -> DirectorialPlan:
    return DirectorialPlan(shots=[ShotIntent(
        id="S1", beat_id="B1", duration_s=duration,
        visual_description="The lead performer opens the door and steps inside.",
    )])


def _director_context(cap):
    script = SimpleNamespace(kind="ScriptBreakdown",
                             payload_json={"beats": [{"id": "B1", "characters": [], "dialogue": []}]})
    context = {"assets": []}
    if cap is not None:
        context["max_shot_seconds"] = cap
    return [script], context


def test_validate_output_refuses_a_shot_above_the_ceiling_by_name():
    inputs, context = _director_context(10)
    with pytest.raises(ValueError, match="SHOT_DURATION_OVER_LIMIT"):
        validate_output("director", _director_proposal(12), SimpleNamespace(brief=""), inputs, context)


def test_validate_output_accepts_a_shot_within_the_ceiling():
    inputs, context = _director_context(10)
    validate_output("director", _director_proposal(10), SimpleNamespace(brief=""), inputs, context)


def test_without_a_ceiling_the_h3_maximum_still_applies():
    inputs, context = _director_context(None)
    validate_output("director", _director_proposal(15), SimpleNamespace(brief=""), inputs, context)
    with pytest.raises(ValidationError):
        _director_proposal(16)


# --- 需求2：画幅与分辨率档位 ------------------------------------------------------


def test_the_baseline_is_the_model_native_canvas():
    assert frame_size("landscape", 1.0) == (1344, 768)
    assert frame_size("portrait", 1.0) == (768, 1344)


def test_every_tier_is_a_valid_output_spec_on_the_h3_grid():
    options = frame_options()
    assert len(options) == 8  # 2 orientations x 4 scales
    for option in options:
        width, height = option["width"], option["height"]
        assert width % 32 == 0 and height % 32 == 0
        assert 256 <= width <= 2048 and 256 <= height <= 2048
        OutputSpec(width=width, height=height, fps=24)


def test_the_lowest_tier_clamps_the_short_side_to_the_h3_floor():
    # 768 * 0.3 = 230.4 would round below the 256 floor; it is clamped, not promised.
    assert frame_size("landscape", 0.3) == (416, 256)
    assert frame_size("portrait", 0.3) == (256, 416)
    assert frame_size("landscape", 0.5) == (672, 384)
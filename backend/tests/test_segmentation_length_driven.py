"""长度驱动分段：标题只是切点候选，短脚本合并、超长按长度切。

穿测《雨夜归人》487 字符六场曾被按标题强制切成 8 段（含标题/设定两个无剧情
段），每段一次 LLM 调用。分段改为长度驱动贪心打包：相邻标题区间合并进一个
提取单元直到超过 max_scene_chars，标题只在需要切分时作切点。segment_document
仍是唯一分段权威（一套事实/一个裁决/一个执行者）。
"""
from shine.materialprep.source_spans import segment_document


def _six_scene_script() -> str:
    return ("雨夜归人\n\n人物：阿远（三十岁男子，深色风衣）\n场景：雨夜街道（暖黄路灯）\n\n"
            + "".join(f"第{i}场\n阿远撑伞走在湿漉漉的街道上，路灯把影子拉得很长。\n" for i in range(1, 7)))


def test_a_short_script_is_read_once_not_once_per_heading():
    text = _six_scene_script()
    assert len(text) < 4000
    scenes = segment_document(text)
    assert len(scenes) == 1, "a short script is one extraction unit"
    assert scenes[0].text == text


def test_short_scenes_pack_until_the_ceiling_then_break_at_a_heading():
    # Each scene ~30 chars; a 70-char ceiling packs two (60) but not three (90).
    text = "".join(f"场景{i}：\n角色行动{i}。\n" for i in range(1, 7))
    scenes = segment_document(text, max_scene_chars=70)
    assert len(scenes) >= 2
    assert all(len(s.text) <= 70 for s in scenes)
    # Every chunk starts at a heading boundary (or the document start).
    assert scenes[0].text.startswith("场景1")
    assert "".join(s.text for s in scenes) == text


def test_a_single_scene_longer_than_the_ceiling_is_subdivided():
    text = "场景一：\n" + "剧情推进。" * 200
    scenes = segment_document(text, max_scene_chars=100)
    assert len(scenes) > 1
    assert all(len(s.text) <= 100 for s in scenes)
    assert "".join(s.text for s in scenes) == text


def test_segmentation_never_loses_or_reorders_source_bytes():
    text = _six_scene_script()
    for ceiling in (40, 100, 4000):
        scenes = segment_document(text, max_scene_chars=ceiling)
        assert "".join(s.text for s in scenes) == text
        # Span offsets are contiguous and cover the whole document.
        spans = [span for s in scenes for span in s.spans]
        assert spans[0].start_offset == 0
        assert spans[-1].end_offset == len(text)
        for left, right in zip(spans, spans[1:], strict=False):
            assert left.end_offset == right.start_offset
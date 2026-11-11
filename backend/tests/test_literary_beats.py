"""P4: split at real boundaries, assemble verbatim, isolate a failed beat."""

from __future__ import annotations

import pytest

from shine.literary import beats as beat_lib
from shine.literary.blueprint import digest_from_outline, merge_digests

pytestmark = pytest.mark.unit

SOURCE = (
    "第一章 出海\n"
    "德明站在船头，风把帆吹得鼓起来。\n\n"
    "场景一 码头\n"
    "阿娘把干粮塞进他怀里。\n阿娘：早去早回。\n\n"
    "场景二 海上\n"
    "浪头一个接一个。\n德明：收帆！\n\n"
    "场景三 归航\n"
    "远处出现灯塔。\n"
)


def test_a_section_that_fits_is_one_beat():
    plan = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=len(SOURCE))
    assert len(plan) == 1
    assert plan[0].start == 0 and plan[0].end == len(SOURCE)


def test_the_split_covers_the_source_exactly():
    plan = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    assert len(plan) > 1
    report = beat_lib.coverage(plan, len(SOURCE))
    assert report["complete"] is True, report
    assert report["gaps"] == []
    assert sum(beat.chars for beat in plan) == len(SOURCE)


def test_the_split_prefers_the_scripts_own_scene_boundaries():
    plan = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    joined = "".join(SOURCE[beat.start:beat.end] for beat in plan)
    assert joined == SOURCE, "no character may be dropped or repeated"
    starts = [SOURCE[beat.start:beat.end].lstrip()[:2] for beat in plan[1:]]
    assert any(text == "场景" for text in starts), starts


def test_beat_ids_are_deterministic_so_a_rerun_adopts_the_same_units():
    first = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    second = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    assert [beat.beat_id for beat in first] == [beat.beat_id for beat in second]


def test_a_long_paragraph_is_cut_and_says_so():
    text = "很长" * 500
    plan = beat_lib.plan_beats(text, section_id="s1", target_chars=600, min_chars=1)
    assert len(plan) > 1
    assert any("hard_cut_within_paragraph" in beat.notes for beat in plan)
    assert beat_lib.coverage(plan, len(text))["complete"] is True


def test_a_tiny_tail_is_merged_instead_of_becoming_its_own_beat():
    text = "甲" * 900 + "\n\n" + "乙" * 50
    plan = beat_lib.plan_beats(text, section_id="s1", target_chars=800, min_chars=400)
    assert len(plan) == 1
    assert "tail_merged" in plan[0].notes


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_assembly_keeps_every_character_and_every_deliberate_repeat():
    plan = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    pieces = []
    for beat in plan:
        text = SOURCE[beat.start:beat.end]
        pieces.append({"beat": beat, "text": text, "continuity_out": f"状态{beat.ordinal}"})
    assembly = beat_lib.assemble("s1", pieces)
    assert assembly.complete is True
    assert assembly.text == SOURCE, "assembly must be byte-identical to the source split"
    assert assembly.continuity == [f"状态{beat.ordinal}" for beat in plan]


def test_a_missing_beat_makes_the_scene_a_partial_assembly():
    plan = beat_lib.plan_beats(SOURCE, section_id="s1", target_chars=40, min_chars=1)
    pieces = [{"beat": beat, "text": "" if beat.ordinal == 2 else SOURCE[beat.start:beat.end],
               "continuity_out": ""} for beat in plan]
    assembly = beat_lib.assemble("s1", pieces)
    assert assembly.complete is False
    assert assembly.missing_ordinals == [2]
    assert assembly.text, "the beats that did arrive are still kept"
    assert "assembled_from_partial_beats" in assembly.notes


def test_propagation_marks_only_what_depends_on_the_failed_beat():
    class Row:
        def __init__(self, ordinal, status):
            self.ordinal, self.status, self.state = ordinal, status, {}

    rows = [Row(1, "PASSED"), Row(2, "FAILED"), Row(3, "PENDING"), Row(4, "PASSED")]
    counts = beat_lib.propagate(rows, failed_ordinal=2)
    assert rows[0].status == "PASSED", "a written beat keeps its text"
    assert rows[2].status == "WAITING_DEPENDENCY"
    assert rows[3].state["dependency_pending"] == 2
    assert rows[3].status == "PASSED", "a written beat is not deleted by a later failure"
    assert counts == {"waiting_dependency": 1, "kept": 1}


# ---------------------------------------------------------------------------
# Blueprint merge
# ---------------------------------------------------------------------------


def _outline(volume_id: str, order: int, events: int, *, complete: bool = True) -> dict:
    return {
        "story_line": [f"事件{i}" for i in range(events)],
        "production_sections": [{"production_section_id": f"{volume_id}-s1",
                                 "start_offset": 0, "end_offset": 1000}],
        "characters": [{"name": "德明"}],
        "questions": ["罗盘为什么裂了"],
    }


def test_every_event_id_survives_the_merge_even_when_the_prose_is_compressed():
    digests = [
        digest_from_outline(_outline("v1", 1, 40), volume={"volume_id": "v1", "order": 1,
                                                           "title": "出海",
                                                           "start_offset": 0, "end_offset": 1000}),
        digest_from_outline(_outline("v2", 2, 40), volume={"volume_id": "v2", "order": 2,
                                                           "title": "归来",
                                                           "start_offset": 1000, "end_offset": 2000}),
    ]
    merged = merge_digests(digests, token_budget=200)
    assert merged.coverage["events_total"] == 80
    assert merged.coverage["events_compressed"] > 0, "a small budget must compress"
    assert merged.coverage["every_event_traceable"] is True
    assert len(set(merged.event_ids)) == 80
    assert merged.text.count("事件") < 80, "the prose really was compressed"


def test_an_incomplete_volume_outline_is_reported_by_name():
    digest = digest_from_outline(
        {"story_line": ["一件事"], "production_sections": []},
        volume={"volume_id": "v3", "order": 3, "title": "残卷",
                "start_offset": 0, "end_offset": 1000})
    merged = merge_digests([digest], token_budget=3_000)
    assert merged.coverage["complete"] is False
    assert "BLUEPRINT_VOLUME_COVERAGE_INCOMPLETE:v3" in merged.warnings

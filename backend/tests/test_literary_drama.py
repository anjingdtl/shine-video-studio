"""The drama layer between a novel and the storyboard: the numbers the server owns.

Speech time, scene budgets, the creative text and the architecture coverage are
computed here, never asked of a model.  These tests pin the arithmetic and the
verdicts; the model-facing contracts are pinned in ``test_film_crew.py`` and the
run that uses them in ``test_v4_drama_layer.py``.
"""

from __future__ import annotations

import pytest

from shine.literary import drama

pytestmark = pytest.mark.unit


def test_speech_time_is_the_characters_of_a_line_at_a_natural_pace():
    assert round(drama.spoken_seconds("遗" * 24), 2) == 5.71
    # A pause counts: the performer takes the comma and the ellipsis.
    assert drama.spoken_seconds("你来了，……真的来了。") > drama.spoken_seconds("你来了真的来了")
    # However few its characters, a line is never shorter than a breath.
    assert drama.spoken_seconds("嗯") == drama.MIN_LINE_SECONDS
    assert drama.spoken_seconds("") == drama.MIN_LINE_SECONDS


def test_a_line_longer_than_a_shot_window_is_refused_by_name():
    line = "遗" * 28
    verdict = drama.check_drama_scene({"location": "钟表铺", "beats": [
        {"visible_action": "老周把怀表贴近耳朵。", "spoken_lines": [{"speaker": "老周", "text": line}]},
    ]})
    assert [refusal["code"] for refusal in verdict.refusals] == ["DRAMA_LINE_TOO_LONG"]
    assert verdict.refusals[0]["characters"] == 28
    assert verdict.refusals[0]["max_characters"] == 27
    # 24 characters is the longest line that still fits: it is accepted.
    fitting = drama.check_drama_scene({"location": "钟表铺", "beats": [
        {"visible_action": "老周把怀表贴近耳朵。", "spoken_lines": [{"speaker": "老周", "text": "遗" * 24}]},
    ]})
    assert fitting.accepted


def test_a_beat_may_not_carry_more_speech_than_one_shot_can_hold():
    # Four short lines are each inside the line window, together they are not.
    verdict = drama.check_drama_scene({"location": "钟表铺", "beats": [
        {"visible_action": "两人隔着柜台争了起来。",
         "spoken_lines": [{"speaker": "老周", "text": "遗" * 18}] * 4},
    ]})
    assert "DRAMA_BEAT_SPEECH_TOO_LONG" in [refusal["code"] for refusal in verdict.refusals]
    # Split across beats, the same speech is fine: that is the repair asked for.
    # Two 14-character lines are 6.67 s of speech, inside one beat's window.
    split = drama.check_drama_scene({"location": "钟表铺", "beats": [
        {"visible_action": "老周压低声音。", "spoken_lines": [{"speaker": "老周", "text": "遗" * 14}] * 2},
        {"visible_action": "客人把硬币按在柜台上。", "spoken_lines": [{"speaker": "客人", "text": "遗" * 14}] * 2},
    ]})
    assert split.refusals == []
    assert round(drama.beat_speech_seconds({"spoken_lines": [{"text": "遗" * 14}] * 2}), 2) == 6.67


def test_an_unregistered_place_or_a_missing_bridge_is_refused():
    base = {"location": "码头", "beats": [{"visible_action": "雨落在甲板上。", "spoken_lines": []}]}
    unknown = drama.check_drama_scene(base, locations=("钟表铺",))
    assert [refusal["code"] for refusal in unknown.refusals] == ["DRAMA_LOCATION_UNKNOWN"]
    # With no registry the writer names the place freely.
    assert drama.check_drama_scene(base).accepted
    continued = drama.check_drama_scene(
        {**base, "location": "钟表铺"}, locations=("钟表铺",), transition="continuous_time")
    assert [refusal["code"] for refusal in continued.refusals] == ["DRAMA_ENTRY_BRIDGE_MISSING"]
    bridged = drama.check_drama_scene(
        {**base, "location": "钟表铺", "entry_bridge": "老周没有抬头，手里仍攥着怀表。"},
        locations=("钟表铺",), transition="continuous_time")
    assert bridged.accepted
    assert [refusal["code"] for refusal in drama.check_drama_scene(
        {"location": "", "beats": []}).refusals] == ["DRAMA_LOCATION_MISSING", "DRAMA_BEATS_MISSING"]


def test_an_inner_state_is_reported_never_refused():
    verdict = drama.check_drama_scene({"location": "钟表铺", "beats": [
        {"visible_action": "老周心想客人不会回来。", "characters": ["老周", "客人"], "spoken_lines": []},
        {"visible_action": "她害怕地后退一步，手按住桌沿。", "characters": [], "spoken_lines": []},
    ]}, cast=("老周",))
    assert verdict.accepted
    codes = [warning["code"] for warning in verdict.warnings]
    assert codes == ["DRAMA_INNER_STATE_REPORTED", "DRAMA_CHARACTER_UNESTABLISHED"]
    # Fear a performer can show is filmable: the second beat raises nothing.
    assert all(warning["beat"] == 0 for warning in verdict.warnings)


def test_scene_budgets_share_the_episode_goal_by_dramatic_weight():
    plan = {"target_seconds": 60, "episodes": [
        {"episode_id": "ep-01", "section_ids": ["a", "b", "c"]},
        {"episode_id": "ep-02", "section_ids": ["d", "e"]},
    ]}
    architecture = {"scene_slots": [
        {"production_section_id": "a", "weight": 3}, {"production_section_id": "b", "weight": 1},
        {"production_section_id": "c", "weight": 1}, {"production_section_id": "d", "weight": 0.5},
        {"production_section_id": "e", "weight": 1.5},
    ]}
    budgets = drama.scene_budgets(architecture, plan)
    assert budgets == {"a": 36.0, "b": 12.0, "c": 12.0, "d": 15.0, "e": 45.0}
    # Every episode's scenes sum back to its own confirmed goal.
    assert round(sum(budgets[key] for key in ("a", "b", "c")), 1) == 60
    assert round(sum(budgets[key] for key in ("d", "e")), 1) == 60
    # With no architecture the share is even; a scene never goes below one shot.
    assert drama.scene_budgets(None, plan) == {"a": 20.0, "b": 20.0, "c": 20.0, "d": 30.0, "e": 30.0}
    tiny = drama.scene_budgets(None, {"target_seconds": 6, "episodes": [{"section_ids": ["a", "b"]}]})
    assert tiny == {"a": 4.0, "b": 4.0}


def test_an_architecture_must_address_the_confirmed_plan_exactly_once():
    plan = {"episodes": [{"episode_id": "ep-01", "section_ids": ["a", "b"]},
                         {"episode_id": "ep-02", "section_ids": ["c"]}]}
    good = {"locations": [{"name": "钟表铺"}, {"name": "码头"}],
            "episodes": [{"episode_id": "ep-01"}, {"episode_id": "ep-02"}],
            "scene_slots": [
                {"production_section_id": "a", "location": "钟表铺", "transition_from_prev": "episode_open"},
                {"production_section_id": "b", "location": "钟表铺", "transition_from_prev": "continuous_time"},
                {"production_section_id": "c", "location": "码头", "transition_from_prev": "time_jump"},
            ]}
    assert drama.architecture_refusals(good, plan) == []

    def codes(architecture):
        return [refusal["code"] for refusal in drama.architecture_refusals(architecture, plan)]

    assert codes({**good, "episodes": good["episodes"][:1]}) == ["ARCHITECTURE_EPISODE_COVERAGE"]
    assert codes({**good, "scene_slots": good["scene_slots"][:2]}) == ["ARCHITECTURE_SCENE_COVERAGE"]
    assert codes({**good, "scene_slots": good["scene_slots"] + [good["scene_slots"][0]]}) == [
        "ARCHITECTURE_SCENE_COVERAGE"]
    unknown_place = [{**slot, "location": "酒馆"} if slot["production_section_id"] == "c" else slot
                     for slot in good["scene_slots"]]
    assert codes({**good, "scene_slots": unknown_place}) == ["ARCHITECTURE_LOCATION_UNKNOWN"]
    # A repeated place leaves the other one unregistered, so both are named.
    assert codes({**good, "locations": [{"name": "钟表铺"}, {"name": "钟表铺"}]}) == [
        "ARCHITECTURE_LOCATION_REPEATED", "ARCHITECTURE_LOCATION_UNKNOWN"]
    odd_transition = [{**slot, "transition_from_prev": "meanwhile"} if slot["production_section_id"] == "b"
                      else slot for slot in good["scene_slots"]]
    assert codes({**good, "scene_slots": odd_transition}) == ["ARCHITECTURE_TRANSITION_UNKNOWN"]


def test_the_creative_text_is_written_once_for_the_queue_and_the_verifier():
    payload = {"location": "钟表铺", "time_of_day": "night", "weather_atmosphere": "雨",
               "entry_bridge": "老周没有抬头。", "exit_state": "灯灭了。",
               "beats": [
                   {"visible_action": "老周把怀表贴近耳朵。",
                    "spoken_lines": [{"speaker": "老周", "emotion": "压低声音", "text": "又停在十二点。"}]},
                   {"visible_action": "客人把硬币按在柜台上。", "spoken_lines": []},
               ]}
    text = drama.drama_scene_text(payload, {"order": 3, "title": "雨夜"})
    assert text == (
        "第3场 雨夜\n"
        "场景：钟表铺（夜晚），雨\n"
        "（承接上一场）老周没有抬头。\n"
        "1. 老周把怀表贴近耳朵。\n"
        "（老周，压低声音）\n"
        "老周：又停在十二点。\n"
        "2. 客人把硬币按在柜台上。\n"
        "（本场收尾）灯灭了。"
    )
    # Every spoken line stands on its own as 说话方：台词, so a beat breakdown
    # finds it verbatim and the local dialogue index can read it.
    from shine.literary.adapters import extract_dialogue

    assert extract_dialogue(text) == [{"speaker": "老周", "line": "又停在十二点。"}]
    # Without a time of day the heading says so instead of inventing one.
    bare = drama.drama_scene_text({"location": "码头", "beats": [{"visible_action": "雨。", "spoken_lines": []}]})
    assert bare.splitlines() == ["第?场", "场景：码头（时间未定）", "1. 雨。"]


def test_the_architect_reads_an_excerpt_of_every_scene_beside_the_plan():
    outline = {"characters": [{"name": "老周", "introduction": "钟表匠"}], "production_sections": [
        {"production_section_id": "a", "order": 1, "title": "第1场", "start_offset": 0, "end_offset": 30},
        {"production_section_id": "b", "order": 2, "title": "第2场", "start_offset": 30, "end_offset": 60},
    ]}
    plan = {"target_seconds": 60, "episodes": [{"episode_id": "ep-01", "order": 1, "title": "上",
                                                "section_ids": ["a", "b"]}]}
    document = ("甲" * 30) + ("乙" * 30)
    brief = drama.architecture_brief(outline, plan, document)
    # The plan reaches the architect once, as the context's own plan; the
    # brief only adds the prose the plan cannot hold.
    assert brief.startswith("【各场原文摘录】")
    assert "ep-01" not in brief
    assert [line[:2] for line in brief.splitlines()[1:]] == ["a：", "b："]
    assert "a：甲甲甲" in brief and "b：乙乙乙" in brief
    # An excerpt is shortened to fit the request; its id never is.
    tight = drama.architecture_brief(outline, plan, document, brief_limit=len(brief) - 20)
    assert len(tight) <= len(brief) - 20
    assert "a：" in tight and "b：" in tight


def scene_context(**overrides):
    production = {"production_section_id": "sec-1", "episode_id": "ep-01",
                  "outline_sha256": "o" * 64, "plan_sha256": "p" * 64,
                  "architecture_artifact_id": "arch", "architecture_sha256": "a" * 64,
                  "source_offsets": {"start_offset": 10, "end_offset": 50, "merged_section_ids": ["t2", "t1"]},
                  "section": {"order": 3, "title": "雨夜"},
                  "characters": [{"name": "老周"}, {"name": "客人"}],
                  "locations": [{"name": "钟表铺"}, {"name": "码头"}],
                  "scene_slot": {"production_section_id": "sec-1", "location": "钟表铺",
                                 "transition_from_prev": "continuous_time", "budget_seconds": 20.0},
                  "scene_budget_seconds": 20.0}
    production.update(overrides)
    return {"production_context": production}


SCENE = {"location": "钟表铺", "time_of_day": "night", "weather_atmosphere": "雨",
         "entry_bridge": "老周没有抬头。", "exit_state": "灯灭了。",
         "beats": [{"visible_action": "老周把怀表贴近耳朵。",
                    "characters": ["老周"],
                    "spoken_lines": [{"speaker": "老周", "emotion": "压低声音", "text": "又停在十二点。"}]},
                   {"beat_type": "reveal", "visible_action": "客人把硬币按在柜台上。",
                    "characters": ["客人", "路人"], "spoken_lines": []}]}


def test_the_scene_verdict_reads_the_same_cast_and_places_the_writer_read():
    assert drama.scene_verdict(SCENE, scene_context()).accepted
    # An unregistered place is a refusal; a person the story never established
    # is only reported.
    refused = drama.scene_verdict({**SCENE, "location": "山顶"}, scene_context())
    assert [item["code"] for item in refused.refusals] == ["DRAMA_LOCATION_UNKNOWN"]
    assert refused.refusals[0]["location"] == "山顶"
    warnings = drama.scene_verdict(SCENE, scene_context()).warnings
    assert [item["code"] for item in warnings] == ["DRAMA_CHARACTER_UNESTABLISHED"]
    assert warnings[0]["character"] == "路人"


def test_the_server_writes_where_a_scene_came_from_and_how_long_it_is():
    fields = drama.drama_server_fields(SCENE, scene_context())
    # Two seconds of action per beat, plus the line's 7 characters at 4.2 a second.
    assert fields["scene_seconds"] == pytest.approx(2.0 + round(2 + 7 / 4.2, 2))
    assert fields["scene_budget_seconds"] == 20.0
    assert fields["production_section_id"] == "sec-1" and fields["episode_id"] == "ep-01"
    assert fields["source_offsets"] == {"start_offset": 10, "end_offset": 50,
                                        "merged_section_ids": ["t1", "t2"]}
    assert fields["architecture_sha256"] == "a" * 64 and fields["plan_sha256"] == "p" * 64
    assert fields["text"].splitlines()[0] == "第3场 雨夜"
    # A scene far from its budget is reported, never refused: the storyboard
    # still shoots the whole scene.
    over = drama.drama_server_fields(SCENE, scene_context(scene_budget_seconds=4.0))
    assert "DRAMA_SCENE_OVER_BUDGET" in [item["code"] for item in over["warnings"]]
    under = drama.drama_server_fields(SCENE, scene_context(scene_budget_seconds=90.0))
    assert "DRAMA_SCENE_UNDER_BUDGET" in [item["code"] for item in under["warnings"]]
    # The server fields sit beside the scene, never over one of its own fields.
    assert not set(fields) & set(SCENE)

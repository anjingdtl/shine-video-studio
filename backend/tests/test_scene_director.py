"""The scene director's contract, prompt and the reading of a finished board.

One call directs a whole scene; the continuity engine judges it; the landing
gate reads what the engine decided.  These tests pin the three parts that need
no model and no database: what the contract admits, what the prompt states,
and that a finished board reaches the landing gate as the director wrote it
plus the engine's decisions — never an interpretation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from shine.agents import continuity_engine as engine
from shine.agents import scene_director as director
from shine.h3.rulebook import digest

SCENE = {
    "location": "钟表铺",
    "beats": [
        {"beat_type": "action", "visible_action": "老周把怀表贴近耳边。", "characters": ["老周"],
         "spoken_lines": [{"speaker": "老周", "emotion": "压低声音", "text": "又停在十二点。"}]},
        {"beat_type": "dialogue_exchange", "visible_action": "客人把硬币按在柜台上。",
         "characters": ["老周", "客人"],
         "spoken_lines": [{"speaker": "客人", "emotion": "冷", "text": "这次别再停了。"}]},
        {"beat_type": "transition_out", "visible_action": "客人走进雨里。", "characters": ["客人"],
         "spoken_lines": []},
    ],
}


def directed(key, beats, **overrides):
    shot = {
        "id": key, "beat_ids": list(beats), "duration_s": 6.0,
        "visual_description": "A watchmaker lifts a pocket watch to his ear.",
        "asset_ids": [], "location": "钟表铺", "dialogue": [],
        "camera": {"composition": "Medium close-up, subject left third.", "move": "static", "motive": "",
                   "movement": "The camera holds still.", "lighting": "Warm lamp key from screen right."},
        "ambience": "Rain on the shop window.", "music": "none",
        "blocking": {"axis": "counter", "placements": [
            {"character": "老周", "zone": "left_fg", "facing": "facing_right", "posture": "leaning forward",
             "props": []}]},
        "environment": {"depth": "Counter foreground, clock wall behind.", "light": "Warm desk lamp.",
                        "micro_motion": "Dust in the lamplight."},
        "timeline": [{"start_s": 0.0, "end_s": 3.0, "action": "He raises the watch.", "expression": "Eyes narrowing.",
                      "camera": "The camera holds still."}],
        "entry_state": "The lamp-lit counter.", "exit_state": "The watch back on the velvet.",
        "transition_motivation": "match_on_action", "handoff_cue": "action match on the raised watch",
        "internal_cuts": [],
    }
    shot.update(overrides)
    return shot


def board_of(*shots):
    return director.SceneShotBoard.model_validate({"shots": list(shots)}).model_dump(mode="json")


def test_the_contract_admits_one_cut_and_only_shootable_segments():
    cut = {"start_s": 3.0, "beat_ids": ["B2"], "visual_description": "The guest at the counter."}
    assert director.SceneShotBoard.model_validate({"shots": [directed("S1", ["B1"], internal_cuts=[cut])]})
    with pytest.raises(ValidationError):
        director.SceneShotBoard.model_validate(
            {"shots": [directed("S1", ["B1"], internal_cuts=[cut, {**cut, "start_s": 4.5}])]})
    for broken in ({"duration_s": 3.0}, {"duration_s": 16.0}, {"entry_state": ""}, {"location": ""},
                   {"timeline": []}, {"camera": {"composition": "Wide.", "move": "spin"}}):
        with pytest.raises(ValidationError):
            director.SceneShotBoard.model_validate({"shots": [directed("S1", ["B1"], **broken)]})
    with pytest.raises(ValidationError):
        director.SceneShotBoard.model_validate({"shots": []})


def test_the_prompt_states_what_the_engine_checks_and_lets_the_server_decide_the_rest():
    prompt = director.scene_director_prompt(max_shot_seconds=10.0, continuity_predecessor=False,
                                            translation_supplied=False, narration_allowed=False)
    # The numbers the engine refuses by.
    assert "4 to 10 seconds" in prompt and "8 to 10 seconds" in prompt
    assert "exactly ONE internal cut" in prompt and "at least 2 seconds on each side of the cut" in prompt
    assert "N divided by 4.2" in prompt
    # What the director states and what the server decides.
    assert "you never set a mode" in prompt
    assert "No earlier shot of this episode exists" in prompt and "establishing_reset" in prompt
    assert "Narration is switched off" in prompt
    assert "provide prompt_style" in prompt
    # The 180-degree rule in the director's own words.
    assert "Whoever stands on the left keeps standing on the left" in prompt

    inside = director.scene_director_prompt(max_shot_seconds=8.0, continuity_predecessor=True,
                                            translation_supplied=True, narration_allowed=True)
    assert "4 to 8 seconds" in inside and "ONE segment of about 8 seconds" in inside
    assert "may continue its final frame" in inside
    assert "leave prompt_style unset" in inside
    assert "only where the scene itself has a narrator line" in inside


def test_a_finished_board_reaches_the_landing_gate_as_written_plus_the_engine_s_decisions():
    exchange = directed(
        "S12", ["B1"], duration_s=8.0,
        dialogue=[{"speaker_id": "老周", "text": "又停在十二点。", "start_s": 1.0, "end_s": 2.6},
                  {"speaker_id": "客人", "text": "这次别再停了。", "start_s": 4.4, "end_s": 6.0}],
        blocking={"axis": "counter", "placements": [
            {"character": "老周", "zone": "left_fg", "facing": "facing_right", "posture": "leaning forward",
             "props": ["a pocket watch"]},
            {"character": "客人", "zone": "right_fg", "facing": "facing_left", "posture": "standing", "props": []}]},
        internal_cuts=[{"start_s": 4.0, "beat_ids": ["B2"], "camera": "The camera holds still.",
                        "visual_description": "Medium shot of the guest at the counter."}],
        transition_motivation="establishing_reset")
    last = directed("S3", ["B3"], duration_s=5.0, transition_motivation="continuous_action",
                    blocking={"axis": "counter", "placements": [
                        {"character": "客人", "zone": "right_fg", "facing": "facing_left", "posture": "standing",
                         "props": []}]})
    board = board_of(exchange, last)

    rows, verdict = director.landing_rows(board, SCENE, {"max_shot_seconds": 10.0, "continuity_predecessor": False})
    assert verdict.accepted
    first, second = rows
    # The director's own fields pass through untouched.
    assert first["id"] == "S12" and first["duration_s"] == 8.0
    assert first["visual"] == "A watchmaker lifts a pocket watch to his ear."
    assert first["dialogue"][1]["text"] == "这次别再停了。"
    assert first["blocking"]["placements"][1]["zone"] == "right_fg"
    assert first["timeline"][0]["action"] == "He raises the watch."
    assert first["ambience"] == "Rain on the shop window." and first["music"] == "none"
    assert first["entry_state"] == "The lamp-lit counter." and first["exit_state"] == "The watch back on the velvet."
    # A cut's picture and its camera travel as one line of prose.
    assert first["internal_cuts"] == [
        {"start_s": 4.0, "beat_id": "B2",
         "visual_description": "Medium shot of the guest at the counter. The camera holds still."}]
    # The engine's decisions, not the model's.
    assert first["cast"] == ["老周", "客人"] and second["cast"] == ["客人"]
    # The scene's first shot opens on a cut; a stated continuation after it
    # carries the previous final frame forward.
    assert (first["mode"], first["motivation"]) == ("cut", "establishing_reset")
    assert (second["mode"], second["motivation"]) == ("last_frame", "continuous_action")
    assert second["note"] == "action match on the raised watch"
    assert first["camera"]["axis_rule"] == director.AXIS_SENTENCE
    assert "shot_size" not in first["camera"]  # nothing is invented for a field left empty

    # The scene's first shot continues an earlier shot of its episode only when
    # the director says the take continues AND an earlier shot exists.
    continuing = board_of({**exchange, "transition_motivation": "continuous_action"}, last)
    rows, verdict = director.landing_rows(continuing, SCENE, {"max_shot_seconds": 10.0})
    assert [row["mode"] for row in rows] == ["cut", "last_frame"]
    assert [item["code"] for item in verdict.warnings] == ["CONTINUATION_IGNORED_WITHOUT_PREDECESSOR"]
    rows, verdict = director.landing_rows(continuing, SCENE,
                                          {"max_shot_seconds": 10.0, "continuity_predecessor": True})
    assert [row["mode"] for row in rows] == ["last_frame", "last_frame"]
    assert verdict.warnings == []


def test_a_board_the_engine_would_refuse_is_refused_at_landing_too():
    board = board_of(directed("S1", ["B1"], location="码头"), directed("S2", ["B2", "B3"]))
    _rows, verdict = director.landing_rows(board, SCENE, {"max_shot_seconds": 10.0})
    assert verdict.first_code() == "SHOT_LOCATION_CONFLICT"


def test_the_server_writes_where_a_board_came_from_and_what_it_decided():
    board = board_of(directed("S1", ["B1"], dialogue=[
        {"speaker_id": "老周", "text": "又停在十二点。", "start_s": 1.0, "end_s": 2.6}]))
    context = {"production_context": {"production_section_id": "sec-01", "episode_id": "ep-01"},
               "locked_story_facts": ["主角叫老周"], "style_hash": "s" * 64}

    scene = SimpleNamespace(id="scene-1", sha256="c" * 64)
    rows, verdict = director.landing_rows(board, SCENE, {"max_shot_seconds": 10.0})
    fields = director.board_server_fields(board, scene, context, verdict)
    assert fields["drama_scene_artifact_id"] == "scene-1" and fields["drama_scene_sha256"] == "c" * 64
    assert fields["production_section_id"] == "sec-01" and fields["episode_id"] == "ep-01"
    assert fields["cast"] == {"S1": ["老周"]}
    assert fields["boundaries"] == [{"shot_id": "S1", "mode": "cut", "motivation": "match_on_action"}]
    assert fields["translated_style_sha256"] == "s" * 64
    assert fields["facts_sha256"] == digest(["主角叫老周"])
    # The server's fields sit beside the board, never over one of its own.
    assert not set(fields) & set(board)


def test_every_prompt_prose_field_the_compiler_reads_is_in_the_language_list():
    board = board_of(directed("S1", ["B1"], internal_cuts=[
        {"start_s": 3.0, "beat_ids": ["B2"], "visual_description": "The guest."}]))
    fields = dict(engine.english_prose_fields(board))
    assert {"shots[0].visual_description", "shots[0].camera.composition", "shots[0].camera.movement",
            "shots[0].camera.lighting", "shots[0].ambience", "shots[0].entry_state", "shots[0].exit_state",
            "shots[0].timeline[0].action", "shots[0].timeline[0].expression", "shots[0].timeline[0].camera",
            "shots[0].blocking.placements[0].posture", "shots[0].environment.depth",
            "shots[0].environment.light", "shots[0].environment.micro_motion",
            "shots[0].internal_cuts[0].visual_description"} <= set(fields)
    assert fields["shots[0].blocking.placements[0].posture"] == "leaning forward"


def test_scene_director_prompt_sizes_shot_count_to_scene_budget():
    short_prompt = director.scene_director_prompt(
        max_shot_seconds=8.0,
        continuity_predecessor=False,
        translation_supplied=False,
        narration_allowed=False,
        scene_budget_seconds=20.0,
    )
    assert "production_context.scene_budget_seconds (20s)" in short_prompt
    assert "3 to 4 shots" in short_prompt
    assert "6 to 10 shots" not in short_prompt

    mid_prompt = director.scene_director_prompt(
        max_shot_seconds=8.0,
        continuity_predecessor=True,
        translation_supplied=False,
        narration_allowed=False,
        scene_budget_seconds=40.0,
    )
    assert "production_context.scene_budget_seconds (40s)" in mid_prompt
    assert "5 to 7 shots" in mid_prompt
    assert "6 to 10 shots" not in mid_prompt

"""The continuity engine: the one verdict on a shot board, with no model call.

A board that states its facts — which beat a shot covers, who stands where,
when each line is spoken, why the camera moves — can be checked rather than
argued about.  These tests pin what the engine refuses by name, what it only
reports, and the two things it decides instead of the director: the people of
every shot and how every shot joins the one before it.
"""

from __future__ import annotations

import pytest

from shine.agents import continuity_engine as engine

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
CONTEXT = {"max_shot_seconds": 10.0, "continuity_predecessor": False}
ZHOU, GUEST = "老周", "客人"
FIRST = "又停在十二点。"
SECOND = "这次别再停了。"


def line(text, speaker=ZHOU, start=1.0, end=2.6):
    return {"speaker_id": speaker, "text": text, "start_s": start, "end_s": end}


def placement(name, zone, facing, props=()):
    return {"character": name, "zone": zone, "facing": facing, "posture": "standing", "props": list(props)}


def shot(key, beats, *, duration=6.0, dialogue=(), placements=(), location="钟表铺",
         motivation="match_on_action", move="static", motive="", movement="The camera holds still.",
         cuts=(), axis="counter"):
    return {
        "id": key, "beat_ids": list(beats), "duration_s": duration,
        "visual_description": "A watchmaker lifts a pocket watch to his ear.",
        "asset_ids": [], "location": location, "dialogue": list(dialogue),
        "camera": {"composition": "Medium close-up, subject left third.", "move": move, "motive": motive,
                   "movement": movement, "lighting": "Warm lamp key from screen right."},
        "ambience": "", "music": "", "blocking": {"axis": axis, "placements": list(placements)},
        "environment": {"depth": "Counter foreground, clock wall behind.", "light": "Warm desk lamp.",
                        "micro_motion": "Dust in the lamplight."},
        "timeline": [{"start_s": 0.0, "end_s": 3.0, "action": "He raises the watch.",
                      "expression": "Eyes narrowing.", "camera": "The camera holds still."}],
        "entry_state": "The lamp-lit counter.", "exit_state": "The watch on the velvet.",
        "transition_motivation": motivation, "handoff_cue": "", "internal_cuts": list(cuts),
    }


def board(*shots):
    return {"shots": list(shots)}


def first_of(**overrides):
    """The scene's opening shot: 老周 alone with his one line."""
    options = {"dialogue": [line(FIRST, ZHOU, 1.0, 2.6)],
               "placements": [placement(ZHOU, "left_fg", "facing_right")],
               "motivation": "establishing_reset"}
    options.update(overrides)
    return shot("S1", ["B1"], **options)


BOTH = (placement(ZHOU, "left_fg", "facing_right"), placement(GUEST, "right_fg", "facing_left"))
SECOND_SHOT = shot("S2", ["B2"], dialogue=[line(SECOND, GUEST, 0.8, 2.2)], placements=BOTH,
                   motivation="shot_reverse_shot")
LAST_SHOT = shot("S3", ["B3"], duration=5.0, placements=[placement(GUEST, "right_fg", "facing_left")])
GOOD = board(first_of(), SECOND_SHOT, LAST_SHOT)


def codes(verdict):
    return [item["code"] for item in verdict.refusals]


def test_a_stated_board_passes_and_the_engine_decides_people_and_boundaries():
    verdict = engine.review_scene_board(GOOD, SCENE, CONTEXT)
    assert codes(verdict) == []
    # The people of a shot are read from its beats, never listed by the model.
    assert verdict.cast == {"S1": [ZHOU], "S2": [ZHOU, GUEST], "S3": [GUEST]}
    # A scene's first shot opens on a cut; the stated reasons after it are cuts.
    assert verdict.boundaries == [
        {"shot_id": "S1", "mode": "cut", "motivation": "establishing_reset"},
        {"shot_id": "S2", "mode": "cut", "motivation": "shot_reverse_shot"},
        {"shot_id": "S3", "mode": "cut", "motivation": "match_on_action"},
    ]


def test_a_fast_exchange_is_one_segment_with_one_cut_and_each_line_in_its_own_picture():
    exchange = shot(
        "S12", ["B1"], duration=8.0,
        dialogue=[line(FIRST, ZHOU, 1.0, 2.6), line(SECOND, GUEST, 4.4, 6.0)],
        placements=BOTH, cuts=[{"start_s": 4.0, "beat_ids": ["B2"],
                                "visual_description": "Medium shot of the guest at the counter.",
                                "camera": "The camera holds still."}])
    assert engine.review_scene_board(board(exchange, LAST_SHOT), SCENE, CONTEXT).accepted
    views, _ = engine.read_board(board(exchange), SCENE)
    assert [(sub.start_s, sub.end_s, sub.beat_ids) for sub in views[0].sub_shots] == [
        (0.0, 4.0, ["B1"]), (4.0, 8.0, ["B2"])]
    assert views[0].cast == [ZHOU, GUEST]

    # A line said after the cut cannot sit in the opening picture.
    moved = {**exchange, "dialogue": [line(FIRST, ZHOU, 1.0, 2.6), line(SECOND, GUEST, 3.0, 4.4)]}
    assert codes(engine.review_scene_board(board(moved, LAST_SHOT), SCENE, CONTEXT)) == [
        "DIALOGUE_OUTSIDE_SUB_SHOT"]


@pytest.mark.parametrize("dialogue, code", [
    ([line(FIRST, ZHOU, 1.0, 2.6), line("你到底修不修？", GUEST, 3.0, 4.4)], "DIALOGUE_NOT_IN_SCENE"),
    ([], "DIALOGUE_DROPPED"),
    ([line(FIRST, GUEST, 1.0, 2.6)], "DIALOGUE_SPEAKER_CHANGED"),
    ([line(FIRST, ZHOU, 1.0, 1.2)], "DIALOGUE_TOO_FAST_TO_SPEAK"),
])
def test_a_line_that_is_not_the_scene_s_own_is_refused_by_what_is_wrong_with_it(dialogue, code):
    # The opening shot carries beat B1, whose one line is 老周's: anything else is wrong.
    assert codes(engine.review_scene_board(board(first_of(dialogue=dialogue), SECOND_SHOT, LAST_SHOT),
                                           SCENE, CONTEXT)) == [code]


def test_lines_are_in_the_scene_s_order_and_never_speak_over_each_other():
    # One segment carrying both spoken beats, as a single uncut take would.
    def both(dialogue):
        return board(shot("S12", ["B1", "B2"], duration=9.0, dialogue=dialogue,
                          placements=BOTH, motivation="establishing_reset"), LAST_SHOT)

    # The first beat's line is spoken only after the second beat's: that is out of order.
    swapped = [line(SECOND, GUEST, 0.8, 2.2), line(FIRST, ZHOU, 3.0, 4.4)]
    assert set(codes(engine.review_scene_board(both(swapped), SCENE, CONTEXT))) == {"DIALOGUE_OUT_OF_ORDER"}

    overlapping = [line(FIRST, ZHOU, 1.0, 2.6), line(SECOND, GUEST, 2.0, 3.4)]
    assert codes(engine.review_scene_board(both(overlapping), SCENE, CONTEXT)) == ["DIALOGUE_OVERLAP"]

    spaced = [line(FIRST, ZHOU, 1.0, 2.6), line(SECOND, GUEST, 3.2, 4.8)]
    assert engine.review_scene_board(both(spaced), SCENE, CONTEXT).accepted


def test_every_beat_is_shot_exactly_once_in_the_scene_s_order():
    missing = board(first_of(), LAST_SHOT)
    assert engine.review_scene_board(missing, SCENE, CONTEXT).refusals == [
        {"code": "BEAT_NOT_COVERED", "beat_ids": ["B2"]}]

    twice = board(first_of(), SECOND_SHOT,
                  shot("S3", ["B2", "B3"], duration=9.0, dialogue=[line(SECOND, GUEST, 0.8, 2.2)],
                       placements=[placement(GUEST, "right_fg", "facing_left")]))
    assert "BEAT_COVERED_TWICE" in codes(engine.review_scene_board(twice, SCENE, CONTEXT))

    reordered = board(SECOND_SHOT, first_of(), LAST_SHOT)
    assert "BEAT_OUT_OF_ORDER" in codes(engine.review_scene_board(reordered, SCENE, CONTEXT))

    # An id the scene does not have makes the board unreadable: nothing else is judged.
    invented = board(first_of(), shot("S9", ["B9"], duration=5.0), LAST_SHOT)
    assert codes(engine.review_scene_board(invented, SCENE, CONTEXT)) == ["UNKNOWN_BEAT"]


def test_nobody_stands_in_a_shot_whose_beats_do_not_show_them_and_nobody_leaves_the_place():
    stranger = board(first_of(placements=[placement(ZHOU, "left_fg", "facing_right"),
                                         placement("路人", "right_fg", "facing_left")]),
                     SECOND_SHOT, LAST_SHOT)
    assert engine.review_scene_board(stranger, SCENE, CONTEXT).refusals == [
        {"code": "PLACEMENT_NOT_IN_CAST", "shot_id": "S1", "character": "路人"}]

    elsewhere = board(first_of(location="码头"), SECOND_SHOT, LAST_SHOT)
    assert engine.review_scene_board(elsewhere, SCENE, CONTEXT).refusals == [
        {"code": "SHOT_LOCATION_CONFLICT", "shot_id": "S1", "location": "码头", "scene_location": "钟表铺"}]


def test_segments_stay_inside_the_ceiling_and_a_cut_leaves_two_readable_pictures():
    cut = {"start_s": 3.0, "beat_ids": ["B2"], "visual_description": "The guest."}

    over = board(first_of(duration=12.0), SECOND_SHOT, LAST_SHOT)
    assert engine.review_scene_board(over, SCENE, CONTEXT).refusals[0] == {
        "code": "SHOT_DURATION_OVER_LIMIT", "shot_id": "S1", "duration_s": 12.0, "max_shot_seconds": 10.0}
    assert engine.review_scene_board(GOOD, SCENE, {**CONTEXT, "max_shot_seconds": 12.0}).accepted

    too_early = board(first_of(duration=6.0, cuts=[{**cut, "start_s": 1.0}]), LAST_SHOT)
    assert "INTERNAL_CUT_WINDOW" in codes(engine.review_scene_board(too_early, SCENE, CONTEXT))

    outside = board(first_of(duration=6.0, cuts=[{**cut, "start_s": 6.0}]), LAST_SHOT)
    assert "EDITORIAL_TIMING_CONFLICT" in codes(engine.review_scene_board(outside, SCENE, CONTEXT))

    two_cuts = board(first_of(duration=9.0, cuts=[{**cut, "start_s": 3.0},
                                                   {**cut, "start_s": 6.0, "beat_ids": ["B3"]}]))
    assert "INTERNAL_CUT_COUNT" in codes(engine.review_scene_board(two_cuts, SCENE, CONTEXT))


def test_the_camera_moves_only_for_a_stated_reason_and_as_its_prose_says():
    rest = (SECOND_SHOT, LAST_SHOT)
    unmotivated = board(first_of(move="push_in", movement="A slow push-in."), *rest)
    assert engine.review_scene_board(unmotivated, SCENE, CONTEXT).refusals == [
        {"code": "CAMERA_MOVE_WITHOUT_MOTIVE", "shot_id": "S1", "move": "push_in"}]

    contradicts = board(first_of(movement="A slow push-in as he listens."), *rest)
    assert engine.review_scene_board(contradicts, SCENE, CONTEXT).refusals == [
        {"code": "CAMERA_PROSE_MISMATCH", "shot_id": "S1", "move": "static"}]

    motivated = board(first_of(move="push_in", motive="he realises the watch has stopped",
                               movement="A slow small push-in as he listens."), *rest)
    assert engine.review_scene_board(motivated, SCENE, CONTEXT).accepted

    # A segment that is already two pictures keeps the camera small.
    cut = {"start_s": 3.0, "beat_ids": ["B2"], "visual_description": "The guest."}
    with_cut = {"duration": 6.0, "cuts": [cut], "move": "crane", "motive": "to reveal the clock wall",
                "movement": "A crane up to the clock wall."}
    crane = board(first_of(**with_cut), LAST_SHOT)
    assert "CAMERA_MOVE_IN_CUT_SEGMENT" in codes(engine.review_scene_board(crane, SCENE, CONTEXT))
    small = board(first_of(**{**with_cut, "move": "push_in", "motive": "he realises it stopped",
                              "movement": "A slow push-in."}), LAST_SHOT)
    assert "CAMERA_MOVE_IN_CUT_SEGMENT" not in codes(engine.review_scene_board(small, SCENE, CONTEXT))


def test_only_a_stated_continuation_carries_the_previous_final_frame_forward():
    def scene_board(first_reason, second_reason="shot_reverse_shot"):
        return board(first_of(motivation=first_reason), {**SECOND_SHOT, "transition_motivation": second_reason},
                     LAST_SHOT)

    # Inside an episode the first shot may continue the shot before it, and
    # only when the director says the take continues.
    continuing = engine.review_scene_board(scene_board("continuous_action", "emotional_hold"), SCENE,
                                           {**CONTEXT, "continuity_predecessor": True})
    assert continuing.accepted
    assert [item["mode"] for item in continuing.boundaries] == ["last_frame", "last_frame", "cut"]

    # An episode's first shot has nothing to continue: it opens on a cut, and
    # the board records that the stated continuation could not be honoured.
    opening = engine.review_scene_board(scene_board("continuous_action"), SCENE, CONTEXT)
    assert opening.accepted
    assert [item["mode"] for item in opening.boundaries] == ["cut", "cut", "cut"]
    assert [item["code"] for item in opening.warnings] == ["CONTINUATION_IGNORED_WITHOUT_PREDECESSOR"]

    # A cut reason is a cut, whatever came before.
    cut = engine.review_scene_board(scene_board("establishing_reset", "time_or_space_jump"), SCENE,
                                    {**CONTEXT, "continuity_predecessor": True})
    assert [item["mode"] for item in cut.boundaries] == ["cut", "cut", "cut"]


def test_the_axis_holds_sides_and_gazes_across_the_whole_scene():
    # A scene whose opening beat shows both people, so their sides are fixed
    # from the first shot on.
    two_hander = {**SCENE, "beats": [{**SCENE["beats"][0], "characters": [ZHOU, GUEST]}, *SCENE["beats"][1:]]}
    opened = first_of(placements=BOTH)

    swapped = board(opened, {**SECOND_SHOT, "blocking": {"axis": "counter", "placements": [
        placement(GUEST, "left_fg", "facing_right"), placement(ZHOU, "right_fg", "facing_left")]}}, LAST_SHOT)
    assert engine.review_scene_board(swapped, two_hander, CONTEXT).refusals[0] == {
        "code": "AXIS_ORDER_FLIPPED", "shot_id": "S2", "left": ZHOU, "right": GUEST}

    # The guest stands right of 老周, so the guest never looks off screen-right.
    turned = board(first_of(), SECOND_SHOT,
                   shot("S3", ["B3"], duration=5.0, placements=[placement(GUEST, "right_fg", "facing_right")]))
    assert engine.review_scene_board(turned, SCENE, CONTEXT).refusals == [
        {"code": "AXIS_FACING_REVERSED", "shot_id": "S3", "character": GUEST,
         "faces": "facing_right", "toward": ZHOU}]

    # A new arrangement needs a boundary that can carry people to new places.
    regrouped = {"axis": "across-the-counter", "placements": [
        placement(GUEST, "left_fg", "facing_right"), placement(ZHOU, "right_fg", "facing_left")]}
    kept_take = board(first_of(), {**SECOND_SHOT, "blocking": regrouped, "transition_motivation": "continuous_action"},
                      LAST_SHOT)
    assert "AXIS_CHANGED_WITHOUT_BREAK" in codes(engine.review_scene_board(kept_take, SCENE, CONTEXT))

    after_a_jump = board(first_of(),
                         {**SECOND_SHOT, "blocking": regrouped, "transition_motivation": "time_or_space_jump"},
                         shot("S3", ["B3"], duration=5.0, axis="across-the-counter",
                              placements=[placement(GUEST, "left_fg", "facing_right")]))
    assert engine.review_scene_board(after_a_jump, SCENE, CONTEXT).accepted


def test_a_prop_from_nowhere_in_a_continued_take_is_reported_never_refused():
    holding = {"axis": "counter", "placements": [placement(ZHOU, "left_fg", "facing_right", props=["a pocket watch"]),
                                                 placement(GUEST, "right_fg", "facing_left", props=["a coin"])]}
    continued = {**SECOND_SHOT, "blocking": holding, "transition_motivation": "continuous_action"}
    opening = first_of(placements=[placement(ZHOU, "left_fg", "facing_right", props=["a pocket watch"])])
    verdict = engine.review_scene_board(board(opening, continued, LAST_SHOT), SCENE, CONTEXT)
    assert verdict.accepted
    assert verdict.warnings == [{"code": "PROP_FROM_NOWHERE", "shot_id": "S2", "prop": "a coin"}]

    # In a shot that is a cut the prop may simply have been taken out of view.
    cut_in = {**continued, "transition_motivation": "shot_reverse_shot"}
    assert engine.review_scene_board(board(opening, cut_in, LAST_SHOT), SCENE, CONTEXT).warnings == []


def test_the_prompt_language_fields_are_listed_once_for_the_language_check():
    fields = dict(engine.english_prose_fields(GOOD))
    assert fields["shots[1].camera.movement"] == "The camera holds still."
    assert fields["shots[2].exit_state"] == "The watch on the velvet."
    # Spoken lines keep their own language: they are not the renderer's prose.
    assert not any(key.endswith(".text") for key in fields)
    cut = board(shot("S12", ["B1"], duration=8.0, dialogue=[line(FIRST), line(SECOND, GUEST, 4.4, 6.0)],
                     placements=BOTH,
                     cuts=[{"start_s": 4.0, "beat_ids": ["B2"], "visual_description": "怀里的硬币。"}]))
    assert dict(engine.english_prose_fields(cut))["shots[0].internal_cuts[0].visual_description"] == "怀里的硬币。"


def test_a_line_copy_ignores_typography_but_not_one_word():
    assert engine.normalised_dialogue("又停在…… 十二点。") == engine.normalised_dialogue("又停在…十二点。")
    assert engine.normalised_dialogue("又停在十三点。") != engine.normalised_dialogue(FIRST)


def test_every_refusal_has_one_sentence_for_a_person_and_one_instruction_for_the_retry():
    every = engine.refusal_codes()
    assert len(every) == len(set(every)) == 26
    assert all(engine.refusal_label(code) for code in every)
    assert engine.refusal_label("NOT_A_CODE") is None
    # Each refusal the engine can raise is told to the retry in the verdict's own words.
    refused = set()
    for candidate, context in [
        (GOOD, CONTEXT),
        (board(first_of(dialogue=[line("你到底修不修？")]), SECOND_SHOT, LAST_SHOT), CONTEXT),
        (board(first_of(dialogue=[]), SECOND_SHOT, LAST_SHOT), CONTEXT),
        (board(first_of(move="orbit", movement="An arcing orbit."), SECOND_SHOT, LAST_SHOT), CONTEXT),
        (board(first_of(), {**SECOND_SHOT, "transition_motivation": "continuous_action",
                            "blocking": {"axis": "far side", "placements": list(BOTH)}},
         {**LAST_SHOT, "blocking": {"axis": "far side", "placements": [
             placement(GUEST, "right_fg", "facing_right")]}}), CONTEXT),
    ]:
        refused.update(codes(engine.review_scene_board(candidate, SCENE, context)))
    assert refused >= {"DIALOGUE_NOT_IN_SCENE", "DIALOGUE_DROPPED", "CAMERA_MOVE_WITHOUT_MOTIVE",
                       "AXIS_CHANGED_WITHOUT_BREAK"}
    for code in refused:
        assert engine.repair_note(code, [{"code": code, "shot_id": "S1"}]) is not None
        assert "The previous answer" in engine.repair_note(code, [{"code": code, "shot_id": "S1"}])


def test_a_repair_note_names_the_place_in_the_verdict_s_own_numbers():
    note = engine.repair_note("DIALOGUE_TOO_FAST_TO_SPEAK", [
        {"code": "DIALOGUE_TOO_FAST_TO_SPEAK", "shot_id": "S2", "line": 1,
         "characters": 12, "needs_seconds": 2.14}])
    assert note == (" The previous answer gave a line too little time (shot S2, line 2). A line of 12 characters "
                    "needs at least 2.14 seconds: give it that long plus a little air, and lengthen the shot if "
                    "it must.")
    # Only the refusals of that code are listed, and only the engine's own details are quoted.
    overlap = engine.repair_note("DIALOGUE_OVERLAP", [
        {"code": "DIALOGUE_OVERLAP", "shot_id": "S2", "line": 1},
        {"code": "DIALOGUE_OVERLAP", "shot_id": "S2", "line": 2},
        {"code": "AXIS_ORDER_FLIPPED", "shot_id": "S9"}])
    assert overlap == (" The previous answer let two lines overlap in time (shot S2, line 2; shot S2, line 3). "
                       "Two lines of one shot never overlap.")
    assert engine.repair_note("SOMETHING_ELSE", []) is None


def test_cross_unit_last_frame_enforces_previous_blocking_axis_and_resets_on_break():
    prev_blocking = {
        "axis": "counter",
        "placements": [placement(ZHOU, "left_fg", "facing_right"), placement(GUEST, "right_fg", "facing_left")],
    }
    two_hander = {**SCENE, "beats": [{**SCENE["beats"][0], "characters": [ZHOU, GUEST]}, *SCENE["beats"][1:]]}
    flipped_open = first_of(
        motivation="continuous_action",
        placements=[placement(GUEST, "left_fg", "facing_right"), placement(ZHOU, "right_fg", "facing_left")],
    )
    verdict = engine.review_scene_board(
        board(flipped_open, SECOND_SHOT, LAST_SHOT),
        two_hander,
        {**CONTEXT, "continuity_predecessor": True, "previous_blocking": prev_blocking},
    )
    assert verdict.refusals[0] == {"code": "AXIS_ORDER_FLIPPED", "shot_id": "S1", "left": ZHOU, "right": GUEST}

    changed_axis = first_of(motivation="continuous_action", axis="across-room", placements=list(BOTH))
    verdict_axis = engine.review_scene_board(
        board(changed_axis, SECOND_SHOT, LAST_SHOT),
        two_hander,
        {**CONTEXT, "continuity_predecessor": True, "previous_blocking": prev_blocking},
    )
    assert "AXIS_CHANGED_WITHOUT_BREAK" in codes(verdict_axis)

    cut_reset = first_of(
        motivation="establishing_reset",
        placements=[placement(GUEST, "left_fg", "facing_right"), placement(ZHOU, "right_fg", "facing_left")],
    )
    verdict_cut = engine.review_scene_board(
        board(
            cut_reset,
            {**SECOND_SHOT, "blocking": {
                "axis": "counter",
                "placements": [placement(GUEST, "left_fg", "facing_right"),
                               placement(ZHOU, "right_fg", "facing_left")],
            }},
            {**LAST_SHOT, "blocking": {
                "axis": "counter", "placements": [placement(GUEST, "left_fg", "facing_right")]}},
        ),
        two_hander,
        {**CONTEXT, "continuity_predecessor": True, "previous_blocking": prev_blocking},
    )
    assert verdict_cut.accepted
    assert verdict_cut.boundaries[0]["mode"] == "cut"


def test_cross_space_shot_accepts_registered_location_with_location_switch_cut():
    indoor = first_of(location="钟表铺")
    outdoor_cut = {**SECOND_SHOT, "location": "雨夜街巷", "transition_motivation": "time_or_space_jump"}
    outdoor_stay = {**LAST_SHOT, "location": "雨夜街巷", "transition_motivation": "continuous_action"}
    reg_ctx = {**CONTEXT, "registered_locations": [{"name": "钟表铺"}, {"name": "雨夜街巷"}]}

    verdict = engine.review_scene_board(board(indoor, outdoor_cut, outdoor_stay), SCENE, reg_ctx)
    assert verdict.accepted
    assert verdict.boundaries[1] == {"shot_id": "S2", "mode": "cut", "motivation": "time_or_space_jump"}
    assert verdict.boundaries[2]["mode"] == "last_frame"

    forced_cut = {**SECOND_SHOT, "location": "雨夜街巷", "transition_motivation": "continuous_action"}
    verdict_forced = engine.review_scene_board(board(indoor, forced_cut, outdoor_stay), SCENE, reg_ctx)
    assert verdict_forced.accepted
    assert verdict_forced.boundaries[1]["mode"] == "cut"
    assert any(w["code"] == "CONTINUATION_IGNORED_ON_LOCATION_CHANGE" for w in verdict_forced.warnings)

    unreg_shot = {**SECOND_SHOT, "location": "码头", "transition_motivation": "time_or_space_jump"}
    unregistered = engine.review_scene_board(board(indoor, unreg_shot, LAST_SHOT), SCENE, reg_ctx)
    assert unregistered.first_code() == "SHOT_LOCATION_CONFLICT"


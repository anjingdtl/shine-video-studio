"""The duration-goal control chain (three principles: one fact, one verdict,
one executor).

``target_seconds`` used to stop at episode grouping and a displayed deviation,
so the crew only saw the per-shot ceiling and hugged the dialogue minimum — a
60s target came out as ~26s of 5s shots. These tests pin the goal carried to the
crew, the one verdict shared by plan and episode confirmation, and the explicit
acknowledgement a deviating confirmation requires. The continuity guidance that
keeps a sequence coherent (instead of fragmenting into independent cuts) is
pinned here too.
"""

from __future__ import annotations

from shine.agents.crew_prompts import crew_prompt
from shine.director import streamline
from shine.director.contracts import duration_target_verdict
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def test_duration_target_verdict_is_one_window_check():
    assert duration_target_verdict(60, 60, 30) is None
    assert duration_target_verdict(30, 60, 30) is None  # lower bound inclusive
    assert duration_target_verdict(90, 60, 30) is None  # upper bound inclusive
    low = duration_target_verdict(26, 60, 30)
    assert low and low["code"] == "DURATION_TARGET_DEVIATION"
    assert low["deviation_seconds"] == -34.0
    assert low["window_low_s"] == 30.0 and low["window_high_s"] == 90.0
    high = duration_target_verdict(97.4, 60, 30)
    assert high and high["deviation_seconds"] == 37.4


def test_unit_targets_allocate_the_episode_goal_by_source_length():
    units = [
        {"episode_id": "ep-01", "start_offset": 0, "end_offset": 100},
        {"episode_id": "ep-01", "start_offset": 100, "end_offset": 300},
        {"episode_id": "ep-02", "start_offset": 300, "end_offset": 500},
    ]
    streamline._allocate_unit_targets(units, 60.0)
    ep1 = [u["target_seconds"] for u in units if u["episode_id"] == "ep-01"]
    ep2 = [u["target_seconds"] for u in units if u["episode_id"] == "ep-02"]
    # Each episode's units sum back to the goal; the longer unit carries more.
    assert round(sum(ep1), 1) == 60.0
    assert ep1[1] > ep1[0]
    assert ep2 == [60.0]


def test_unit_target_never_below_the_h3_segment_floor():
    units = [
        {"episode_id": "ep-01", "start_offset": 0, "end_offset": 10},
        {"episode_id": "ep-01", "start_offset": 10, "end_offset": 1000},
    ]
    streamline._allocate_unit_targets(units, 60.0)
    # A 1% fragment would allocate 0.6s, below the renderer's 4s minimum: it is
    # bumped to the floor so the crew is never asked for an impossible segment.
    assert units[0]["target_seconds"] >= 4.0


def test_director_prompt_carries_the_unit_goal_and_prefers_continuity():
    targeted = crew_prompt("director", tool="storyboard", max_shot_seconds=10,
                           unit_target_seconds=20)
    assert "20 seconds of total screen time" in targeted
    assert "develop each shot's action" in targeted
    assert "10 seconds" in targeted  # the ceiling is still stated
    # Continuity follows narrative flow: a continuing action prefers last_frame,
    # cut is reserved for a genuine narrative break — not every angle change.
    assert "prefer" in targeted and "last_frame" in targeted
    assert "genuine narrative break" in targeted
    plain = crew_prompt("director", tool="storyboard", max_shot_seconds=10)
    assert "total screen time" not in plain  # no goal invented when none is given


def test_book_confirm_refuses_duration_deviation_until_acknowledged(api):
    client, _factory, _settings = api
    # 4 sections × 60s estimate → 2 episodes of ~120s, outside the 60±30 window.
    prepared = _prepare_project(client, sections=4, section_seconds=60)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    assert book["preview"]["episodes"][0]["deviation_seconds"] > 30

    refused = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": book["plan_version"], "expected_sha256": book["plan_sha256"],
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action"}},
        headers=LOCAL)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "EPISODE_PLAN_DURATION_DEVIATION"

    accepted = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": book["plan_version"], "expected_sha256": book["plan_sha256"],
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action"},
              "acknowledge_deviation": True},
        headers=LOCAL)
    assert accepted.status_code == 200, accepted.text


def test_manual_episode_confirm_enforces_the_goal_but_auto_confirm_proceeds(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    # The mock lands 26s per episode (below the 30s window floor). Auto-confirm is
    # the authorised continuation of the confirmed plan: it proceeds and records
    # the deviation rather than stalling unattended production.
    with factory() as db:
        plan = streamline._latest_plan(db, project)
    by_id = {e["episode_id"]: e for e in plan.payload["episodes"]}
    assert by_id["ep-01"]["shot_ids"], "auto-confirm materialises the episode"

    # The person's explicit manual confirmation is the checkpoint: re-confirming
    # the deviating episode without acknowledgement is refused by name.
    refused = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={}, headers=LOCAL)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "EPISODE_DURATION_OUT_OF_TARGET"
    assert detail["actual_seconds"] < detail["window_low_s"]

    accepted = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert accepted.status_code == 200, accepted.text
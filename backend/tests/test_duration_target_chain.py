"""The duration-goal control chain (three principles: one fact, one verdict,
one executor).

``target_seconds`` is the person's confirmed per-episode goal.  It reaches the
production in one way: the drama layer shares an episode's goal among its
scenes by dramatic weight, and each scene is WRITTEN to its share.  It is never
a landing gate.  These tests pin the one verdict shared by the storyboard book
and the episode confirmation, the explicit acknowledgement a deviating
confirmation requires, and the fact that an episode is never cut short because
its landed seconds reached the goal.  The continuity guidance that keeps a
sequence coherent (instead of fragmenting into independent cuts) is pinned here
too.
"""

from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.agents.crew_prompts import crew_prompt
from shine.director import streamline
from shine.director.contracts import duration_target_verdict
from shine.models import AgentTaskRecord, Shot
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


def test_the_director_prompt_states_the_ceiling_and_prefers_continuity():
    prompt = crew_prompt("director", tool="storyboard", max_shot_seconds=10)
    assert "4-10 second render segment" in prompt  # the per-shot ceiling is stated
    # The episode goal is not the director's number: a scene arrives already
    # written to its share of it, and the director shoots that scene whole.
    assert "total screen time" not in prompt
    # Continuity follows narrative flow: a continuing action prefers last_frame,
    # cut is reserved for a genuine narrative break — not every angle change.
    assert "prefer" in prompt and "last_frame" in prompt
    assert "genuine narrative break" in prompt


def test_book_confirm_refuses_duration_deviation_until_acknowledged(api):
    client, _factory, _settings = api
    # 目标秒数驱动分组后，60s 的场会各自成集（偏差 0）；要触发偏差裁决，
    # 用单场 120s 的超长场景：每集 120s，落在 60±30 窗之外。
    prepared = _prepare_project(client, sections=4, section_seconds=120)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    # 每集展示的是视频计划：未拆镜时等于目标秒数，不冒充场景覆盖秒数。
    # 覆盖超出只进头部覆盖标识，并由确认门禁具名拒绝（下方断言）。
    assert book["preview"]["episodes"][0]["planned_seconds"] == 60
    assert book["preview"]["episodes"][0]["deviation_seconds"] == 0
    assert book["preview"]["covered_scope"]["covered_seconds"] > 120

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


def test_an_episode_is_never_cut_short_once_its_landed_seconds_reach_the_goal(api):
    client, factory, settings = api
    # Two 5-second scenes per episode and a 10-second goal: the first scene's
    # two shots (13 s) already exceed the goal before the second scene runs.
    prepared = _prepare_project(client, sections=4, section_seconds=5)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=10, tolerance_seconds=0)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                         json={}, headers=LOCAL).json()
    _run_storyboard_to_rest(client, settings, project, max_ticks=60)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert state["cursor"] == state["total_units"] == 4
    first = next(ep for ep in state["episodes"] if ep["episode_id"] == "ep-01")
    # The second scene was still storyboarded and landed: the episode is whole.
    assert [unit["state"] for unit in first["units"]] == ["APPLIED", "APPLIED"]
    assert len(first["shot_ids"]) == 4
    assert first["status"] == "APPLIED"
    assert "fit_closed" not in first

    with factory() as db:
        plan = streamline._latest_plan(db, project)
        run = db.get(AgentTaskRecord, queued["task_id"])
        run_trace = dict(run.trace)
    episode = next(e for e in plan.payload["episodes"] if e["episode_id"] == "ep-01")
    # Confirmed with every landed shot, and the overrun is the recorded
    # deviation — no scene was declared out of scope to make the number.
    assert episode["shot_ids"] == first["shot_ids"]
    assert "sec-02" not in plan.payload["out_of_scope_section_ids"]
    assert "fit_closed_episodes" not in run_trace
    assert [unit["production_section_id"] for unit in run_trace["units"]] == [
        "sec-01", "sec-02", "sec-03", "sec-04"]
    assert not any("target_seconds" in unit for unit in run_trace["units"])


def test_a_scene_without_shots_blocks_confirmation_however_long_the_others_ran(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2, section_seconds=5)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=10, tolerance_seconds=0)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    # Only the first scene ran: its 13 s are already past the 10 s goal.
    _run_storyboard_to_rest(client, settings, project, max_ticks=1)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    episode = state["episodes"][0]
    assert len(episode["shot_ids"]) == 2 and episode["status"] == "IN_PROGRESS"

    with factory() as db:
        with pytest.raises(HTTPException) as refused:
            streamline.confirm_episode_shots(db, project, "ep-01", actor="local-user",
                                             acknowledge_duration_deviation=True)
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "EPISODE_SECTION_NOT_STORYBOARDED"
    assert [row["section_id"] for row in refused.value.detail["pending"]] == ["sec-02"]


def test_confirming_a_prefix_of_the_landed_shots_is_no_longer_a_mechanism(api):
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2, section_seconds=5)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=10, tolerance_seconds=0)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    # The body field that asked for a target-fitting prefix is gone with the
    # mechanism: it is refused, never silently ignored.
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"fit_target": True}, headers=LOCAL)
    assert response.status_code == 422, response.text


def test_shots_landed_after_confirmation_make_it_stale(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2, section_seconds=5)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=10, tolerance_seconds=0)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert len(table["shots"]) == 4 and table["confirmation_stale"] is False
    assert "target_fit" not in table
    assert table["duration_verdict"]["code"] == "DURATION_TARGET_DEVIATION"

    # One more shot is landed after the episode was confirmed (a re-run): the
    # person has not confirmed it, so the episode is stale — even though the
    # confirmed shots are still exactly its first four.
    with factory.begin() as db:
        landed = db.get(Shot, table["shots"][-1]["shot_id"])
        extra = Shot(project_id=project, name="重拍", revision=1, spec=copy.deepcopy(landed.spec))
        db.add(extra)
        db.flush()
        run = db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == streamline.DIRECTOR_STORYBOARD_RUN))
        trace = dict(run.trace)
        results = [dict(result) for result in trace["results"]]
        results[-1]["apply"] = {**results[-1]["apply"], "shot_ids": [*results[-1]["apply"]["shot_ids"], extra.id]}
        run.trace = {**trace, "results": results}
    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert len(table["shots"]) == 5 and table["confirmation_stale"] is True

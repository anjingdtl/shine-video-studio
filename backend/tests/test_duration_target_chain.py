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


def test_fit_closed_episode_is_applied_and_confirmable(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4, section_seconds=30)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    # Simulate an episode that stopped early after hitting target_seconds (fit_closed)
    # before its second section ran: it must surface as APPLIED (not IN_PROGRESS)
    # and allow confirmation without rejecting the skipped section.
    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL).json()
    task_id = queued["task_id"]

    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.models import AgentTaskRecord
    from tests.test_director_streamline import _chain_responder

    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_chain_responder({"asset": master}))

    # Run only the first unit (sec-01 of ep-01), then mark ep-01 fit_closed with 60s
    # while leaving sec-02 un-run.
    assert streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway)
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        trace = dict(task.trace)
        trace["fit_closed_episodes"] = {"ep-01": {"landed_seconds": 60.0}}
        task.trace = trace

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    ep1 = next(ep for ep in state["episodes"] if ep["episode_id"] == "ep-01")
    assert ep1["fit_closed"] is True
    assert ep1["status"] == "APPLIED"
    assert ep1["shot_ids"]

    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["shot_ids"] == ep1["shot_ids"]


def test_target_window_reached_episode_confirms_without_fit_closed_trace(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4, section_seconds=10)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=20, tolerance_seconds=10)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL).json()
    task_id = queued["task_id"]

    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.models import AgentTaskRecord
    from tests.test_director_streamline import _chain_responder

    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_chain_responder({"asset": master}))

    # Land only sec-01 (13s, inside 20±10 window) and mark task SUCCEEDED without
    # fit_closed_episodes in trace (simulating pre-fix persisted runs).
    assert streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway)
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        trace = dict(task.trace)
        trace.pop("fit_closed_episodes", None)
        task.trace = trace
        task.status = "SUCCEEDED"

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    ep1 = next(ep for ep in state["episodes"] if ep["episode_id"] == "ep-01")
    assert ep1["fit_closed"] is True
    assert ep1["status"] == "APPLIED"

    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["shot_ids"] == ep1["shot_ids"]


def test_single_long_section_split_into_units_fit_closed_confirms(api):
    """A single long section split into multiple units that stops early via fit_closed
    must keep that section in kept_sections and NOT also list it in out_of_scope."""
    client, factory, settings = api
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from tests.test_director_streamline import _approved_outline, _chain_responder, _document
    from tests.test_film_crew import prepare

    project, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    # 2000 chars -> split into 2 units by segment_document(max_scene_chars=1200)
    long_body = ("第1场\n老周把怀表贴近耳朵，又放回丝绒垫上。客人留下硬币，走进雨里。\n" + "。" * 1950 + "\n")
    document = _document(client, project, "长场.txt", long_body)
    _approved_outline(factory, project, document, [{
        "production_section_id": "sec-01", "order": 1, "title": "第1场",
        "estimated_duration_s": 400.0, "start_offset": 0, "end_offset": len(long_body),
    }])
    book = _storyboard_book(client, project, episode_count=1, target_seconds=10, tolerance_seconds=5)
    assert client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": book["plan_version"], "expected_sha256": book["plan_sha256"],
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action"},
              "acknowledge_deviation": True},
        headers=LOCAL).status_code == 200

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_chain_responder({"asset": master}))
    # Run first unit (13s >= target 10s), then next tick marks ep-01 fit_closed and skips unit 2.
    assert streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway)
    streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    ep1 = state["episodes"][0]
    assert ep1["fit_closed"] is True
    assert ep1["status"] == "APPLIED"

    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"fit_target": True}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["shot_ids"] == ep1["shot_ids"]



"""The unified director overview: one read projection, per-episode isolation.

The projection must answer two questions the V2 plan makes non-negotiable:

* a half-finished project is not a global stop — the finished episode keeps its
  own state and its own next action;
* every value comes from an existing ledger row, so two reads of an unchanged
  project return the same answer (a browser reload cannot invent progress).

These tests drive the real services (the same chain the resident Worker runs),
never hand-written plan rows pretending to be confirmed.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from shine.director.overview import build_episode_overview, build_overview
from shine.models import Project
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def _overview(client, project: str) -> dict:
    response = client.get(f"/api/v1/projects/{project}/director/overview")
    assert response.status_code == 200, response.text
    return response.json()


def _generate_storyboard(client, project: str) -> dict:
    response = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                           json={}, headers=LOCAL)
    assert response.status_code in (200, 202), response.text
    return response.json()


def _confirmed_first_episode(client, project: str, episode_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/{episode_id}/confirm",
        json={}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def test_overview_keeps_a_finished_episode_usable_while_another_is_blocked(api):
    """Episode 1 lands and self-confirms; episode 2 stays blocked on one section.

    The whole-project answer must not become "not materialised": episode 1 is
    already past storyboarding and keeps its own state, its own shot count and
    its own next action, and the blocked episode never borrows it.  Since the
    autopilot round, episode 1 materialises itself once its units land (no manual
    per-episode click); an explicit confirm stays idempotent.
    """
    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    # Section 3 belongs to the second episode and its answer is a decided
    # refusal with genuinely missing content: only that episode pauses.
    _generate_storyboard(client, project)
    _run_storyboard_to_rest(client, settings, project, fail_section="sec-03")

    overview = _overview(client, project)
    assert overview["counts"]["episode_count"] == 2
    episodes = {row["episode_id"]: row for row in overview["episodes"]}
    first = episodes["ep-01"]
    second = episodes["ep-02"]

    # Episode 1 auto-confirms once its units land; episode 2 stays blocked.
    assert first["state"] in {"MATERIAL_MISSING", "READY_TO_PACKAGE"}
    assert first["shots"]["landed_count"] > 0
    assert first["shots"]["confirmed"] is True
    assert second["state"] == "STORYBOARD_BLOCKED"

    confirmed = _confirmed_first_episode(client, project, "ep-01")
    assert confirmed["shot_ids"]

    overview = _overview(client, project)
    episodes = {row["episode_id"]: row for row in overview["episodes"]}
    first = episodes["ep-01"]
    second = episodes["ep-02"]
    # Episode 1 is fully confirmed and has its own forward action; episode 2
    # still waits for a repair, and the project summary counts one of two.
    assert first["shots"]["confirmed"] is True
    assert first["state"] in {"MATERIAL_MISSING", "READY_TO_PACKAGE"}
    assert first["next_action"]["kind"] in {"PREPARE_MATERIALS", "BUILD_PACKAGE"}
    assert second["state"] == "STORYBOARD_BLOCKED"
    assert second["next_action"]["kind"] == "RETRY_STORYBOARD"
    assert overview["counts"]["confirmed_episode_count"] == 1
    # The project's single main action points at real work, never at a state
    # machine name.
    assert overview["next_action"]["kind"] in {
        "PREPARE_MATERIALS", "BUILD_PACKAGE", "CONFIRM_EPISODE_SHOTS"}


def test_overview_reads_are_stable_and_read_only(api):
    """Two reads of an unchanged project agree: nothing is written."""
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    first = _overview(client, project)
    second = _overview(client, project)
    assert first == second
    # And the read really describes the confirmed book, not a guess.
    assert first["plan"]["version"] == book["plan_version"]
    assert first["style"] is not None


def test_overview_gaps_are_grouped_by_missing_master():
    """One missing master used by many shots is one item with its shot count."""
    from shine.director.overview import _aggregate_gaps

    rows = (
        [{"shot_id": f"s{index}", "code": "SHOT_CHARACTER_MASTER_MISSING:罗兰"} for index in range(62)]
        + [{"shot_id": f"s{index}", "code": "SHOT_CHARACTER_MASTER_MISSING:Anna"} for index in range(3)]
        + [{"shot_id": "s1", "code": "SHOT_REQUIRED_ASSET_MISSING"}]
    )
    gaps = _aggregate_gaps(rows)
    by_entity = {row["entity_id"]: row for row in gaps if row["entity_id"]}
    assert set(by_entity) == {"罗兰", "Anna"}
    assert by_entity["罗兰"]["shot_count"] == 62
    assert by_entity["Anna"]["shot_count"] == 3
    assert by_entity["罗兰"]["family"] == "character"
    # The anonymous reference problem is its own single row, not folded in.
    assert any(row["entity_id"] is None and row["code"] == "SHOT_REQUIRED_ASSET_MISSING"
               for row in gaps)
    # The biggest gap is listed first, so the panel names the real blocker.
    assert gaps[0]["entity_id"] == "罗兰"


def test_episode_overview_names_only_its_own_episode(api):
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    response = client.get(
        f"/api/v1/projects/{project}/director/episodes/ep-01/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["episode"]["episode_id"] == "ep-01"
    assert body["plan"]["version"] == book["plan_version"]

    missing = client.get(
        f"/api/v1/projects/{project}/director/episodes/ep-99/overview")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "EPISODE_NOT_IN_PLAN"


def test_build_overview_accepts_a_project_object(api):
    """The service entry point works without the HTTP layer (worker/other callers)."""
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2)
    project_id = prepared["project"]
    factory = client.app.state.session_factory
    with factory() as db:
        project = db.get(Project, project_id)
        body = build_overview(db, project)
        assert body["project_id"] == project_id
        with pytest.raises(HTTPException) as raised:
            build_episode_overview(db, project, "ep-none")
        assert raised.value.status_code == 404


def test_starting_episode_two_never_hides_the_take_episode_one_delivered(api):
    """拍第 2 集不能把第 1 集的成片弄丢，也不能把第 1 集显示成"又开拍了"。

    回归用例：先前总览只认"当前这一次运行"，于是第 2 集一开始，第 1 集既没有
    交付、也显示成 0/3 镜在拍。本集进度必须回它自己那次运行去读。
    """
    from datetime import datetime, timedelta

    from shine.director.models import DirectorPackageVersion
    from shine.production.models import ProductionRun, ProductionRunEpisode

    client, factory, _settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    first_artifact = "11111111-2222-3333-4444-555555555555"
    start = datetime(2026, 11, 13, 6, 0, 0)
    with factory.begin() as db:
        package = DirectorPackageVersion(
            project_id=project, revision=1, status="CONFIRMED", manifest={},
            scope_digest="a" * 64, manifest_sha256="b" * 64, policy_hash="c" * 64,
            episode_count=2, shot_count=3)
        db.add(package)
        db.flush()
        first_run = ProductionRun(
            project_id=project, package_id=package.id, package_revision=1,
            manifest_sha256=package.manifest_sha256, scope_digest=package.scope_digest,
            policy_hash=package.policy_hash, scope={"shot_count": 3}, budget={}, counters={},
            status="COMPLETED", idempotency_key="test-first-episode", request_hash="d" * 64,
            created_at=start, updated_at=start + timedelta(minutes=20))
        second_run = ProductionRun(
            project_id=project, package_id=package.id, package_revision=1,
            manifest_sha256=package.manifest_sha256, scope_digest=package.scope_digest,
            policy_hash=package.policy_hash, scope={"shot_count": 3}, budget={}, counters={},
            status="RUNNING", idempotency_key="test-second-episode", request_hash="e" * 64,
            created_at=start + timedelta(hours=1), updated_at=start + timedelta(hours=1))
        db.add_all([first_run, second_run])
        db.flush()
        db.add_all([
            ProductionRunEpisode(
                run_id=first_run.id, project_id=project, episode_id="ep-01", position=0,
                title="第1集", manifest_sha256="f" * 64, shot_ids=[], status="DELIVERED",
                delivered_artifact_id=first_artifact, delivered_sha256="9" * 64,
                actual_seconds=20.479, delivered_at=start + timedelta(minutes=21)),
            # The second run only covers episode 2: episode 1 is not part of it.
            ProductionRunEpisode(
                run_id=second_run.id, project_id=project, episode_id="ep-02", position=0,
                title="第2集", manifest_sha256="7" * 64, shot_ids=[], status="PRODUCING"),
        ])
        first_run_id, second_run_id = first_run.id, second_run.id

    overview = _overview(client, project)
    episodes = {row["episode_id"]: row for row in overview["episodes"]}
    first, second = episodes["ep-01"], episodes["ep-02"]

    # 第 1 集仍然是"已交付"，成片来自它自己那次运行。
    assert first["state"] == "DELIVERED"
    assert first["delivery"]["artifact_id"] == first_artifact
    assert first["delivery"]["run_id"] == first_run_id
    assert first["production"]["run_id"] == first_run_id
    assert first["production"]["in_current_run"] is False
    assert first["next_action"] is None

    # 第 2 集才是正在拍的那一集。
    assert second["state"] == "SHOOTING"
    assert second["delivery"] is None
    assert second["production"]["run_id"] == second_run_id
    assert second["production"]["in_current_run"] is True

    # 项目主行动指向还在进行的那一集，不会把已交付的集再推一次。
    assert overview["next_action"]["episode_id"] == "ep-02"

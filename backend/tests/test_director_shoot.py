"""⑤⑥ One shoot action: freeze the selected episodes, start the real run once.

The director page keeps exactly one main production operation.  These tests pin
the three properties the V2 plan requires of it:

* a ready episode can start while another episode is still unfinished;
* an unready episode is a named, per-episode refusal — not a global stop;
* replaying the same selection returns the same run instead of a second one.
"""

from __future__ import annotations

from tests.conftest import register_workflow_profile
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def _ready_first_episode(client, settings) -> tuple[str, str]:
    """A project whose episode 1 is confirmed and whose episode 2 is blocked."""
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return project, pending_section


def test_one_shoot_action_builds_the_package_and_replays_the_same_run(api):
    client, _factory, settings = api
    project, _pending = _ready_first_episode(client, settings)
    profile = register_workflow_profile(client, mode="Ref2VA")

    first = client.post(
        f"/api/v1/projects/{project}/director/production/start",
        json={"episode_ids": ["ep-01"], "workflow_profile_id": profile["id"]},
        headers=LOCAL)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    assert body["package_built"] is True
    assert body["episode_ids"] == ["ep-01"]
    assert [row["episode_id"] for row in body["episodes"]] == ["ep-01"]
    assert body["run_status"] in {"DRAFT", "QUEUED"}

    # The same selection again is the same run: a double click, a reload, or a
    # retried POST never starts a second production.
    again = client.post(
        f"/api/v1/projects/{project}/director/production/start",
        json={"episode_ids": ["ep-01"], "workflow_profile_id": profile["id"]},
        headers=LOCAL)
    assert again.status_code == 201, again.text
    assert again.json()["run_id"] == body["run_id"]
    assert again.json()["created"] is False


def test_shoot_action_names_an_unready_episode_instead_of_stopping_everything(api):
    client, _factory, settings = api
    project, _pending = _ready_first_episode(client, settings)
    profile = register_workflow_profile(client, mode="Ref2VA")

    refused = client.post(
        f"/api/v1/projects/{project}/director/production/start",
        json={"episode_ids": ["ep-02"], "workflow_profile_id": profile["id"]},
        headers=LOCAL)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "EPISODE_NOT_SHOOTABLE"
    assert [row["episode_id"] for row in detail["episodes"]] == ["ep-02"]

    # The refusal did not create a run for the ready episode either.
    runs = client.get(f"/api/v1/projects/{project}/production-runs").json()
    assert runs == []


def test_shoot_action_refuses_an_unknown_episode(api):
    client, _factory, settings = api
    project, _pending = _ready_first_episode(client, settings)
    profile = register_workflow_profile(client, mode="Ref2VA")

    response = client.post(
        f"/api/v1/projects/{project}/director/production/start",
        json={"episode_ids": ["ep-99"], "workflow_profile_id": profile["id"]},
        headers=LOCAL)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EPISODE_NOT_IN_PLAN"


def test_director_path_pins_the_builtin_rulepack_before_storyboarding(api):
    """③ 不该让普通用户撞上「先冻结规则包」这种工程错误。

    规则包是唯一的内置不可变版本，不是创作选择；导演路径第一次进入分镜/开拍时
    自动钉住它，重复调用不会换版本，已有钉住也不会被覆盖。
    """
    from shine.director import streamline
    from shine.models import ProjectRuleLock
    from tests.conftest import create_project

    client, factory, _settings = api
    project = create_project(client, title="未冻结规则包的导演作品")

    with factory.begin() as db:
        first = streamline.ensure_project_rulepack(db, project)
    assert first, "the built-in pack must be pinned on the first director use"

    with factory() as db:
        lock = db.get(ProjectRuleLock, project)
        assert lock is not None
        assert lock.rulepack_id == first
        pinned_hash = lock.rulepack_hash

    # A second use is idempotent: the same immutable pin, never a new version.
    with factory.begin() as db:
        again = streamline.ensure_project_rulepack(db, project)
    assert again == first
    with factory() as db:
        assert db.get(ProjectRuleLock, project).rulepack_hash == pinned_hash

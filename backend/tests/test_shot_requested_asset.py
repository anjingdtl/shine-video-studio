"""A material the person adds to one shot is a requirement first, a rewrite second."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from shine.director.shot_revision import SHOT_REVISION_RUN, run_pending_shot_revision
from shine.llm.base import LLMNotConfigured
from shine.models import AgentTaskRecord, Shot
from shine.schemas import ShotSpec
from tests.conftest import create_project, create_shot, freeze_style

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}


def _setup(client):
    project_id = create_project(client, "用户指定素材")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"], beat="A man sits at a table.")
    return project_id, shot


def _task(factory, project_id: str) -> AgentTaskRecord:
    with factory() as db:
        return db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == SHOT_REVISION_RUN,
        )).one()


def test_requested_asset_is_recorded_and_queued(studio):
    client, factory, _tmp, _settings = studio
    project_id, shot = _setup(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "旧怀表", "description": "父亲留下的黄铜怀表"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["shot_revision"] == shot["revision"] + 1
    assert body["task_status"] == "QUEUED"
    assert body["requested_assets"][0]["name"] == "旧怀表"

    with factory() as db:
        spec = ShotSpec.model_validate(db.get(Shot, shot["id"]).spec)
        assert [(row.family, row.name) for row in spec.requested_assets] == [("prop", "旧怀表")]

    replay = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "旧怀表", "description": "父亲留下的黄铜怀表"})
    assert replay.status_code == 201, replay.text
    assert replay.json()["task_created"] is False
    assert replay.json()["shot_revision"] == body["shot_revision"]


def test_missing_text_route_only_skips_the_rewrite(studio):
    client, factory, _tmp, settings = studio
    project_id, shot = _setup(client)
    client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "旧怀表", "description": ""})

    class NoRouteGateway:
        def profile_for_role(self, db, role):
            raise LLMNotConfigured("no route")

    assert run_pending_shot_revision(factory, settings, "worker-test", gateway=NoRouteGateway())
    task = _task(factory, project_id)
    assert task.status == "FAILED"
    assert task.error_code == "SKIPPED_NO_TEXT_ROUTE"
    with factory() as db:
        spec = ShotSpec.model_validate(db.get(Shot, shot["id"]).spec)
        assert [row.name for row in spec.requested_assets] == ["旧怀表"]


def test_successful_rewrite_preserves_dialogue_and_duration(studio):
    client, factory, _tmp, settings = studio
    project_id, shot = _setup(client)
    with factory() as db:
        row = db.get(Shot, shot["id"])
        data = ShotSpec.model_validate(row.spec).model_dump(mode="json")
        data["characters"] = ["Lin"]
        data["dialogue"] = [{"speaker_id": "Lin", "text": "你来了。", "start_s": 1, "end_s": 2}]
        row.spec = ShotSpec.model_validate(data).model_dump(mode="json")
        db.commit()

    client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "old pocket watch", "description": ""})

    class Rewriter:
        def profile_for_role(self, db, role):
            return object()

        def complete_for_role(self, db, role, **kwargs):
            return SimpleNamespace(parsed={
                "story_beat": "A man turns the old pocket watch over in his hand at the table."})

    assert run_pending_shot_revision(factory, settings, "worker-test", gateway=Rewriter())
    task = _task(factory, project_id)
    assert task.status == "SUCCEEDED"
    with factory() as db:
        row = db.get(Shot, shot["id"])
        spec = ShotSpec.model_validate(row.spec)
        assert "old pocket watch" in spec.story_beat
        assert [line.text for line in spec.dialogue] == ["你来了。"]
        assert spec.duration_target_s == 6


def test_rewrite_that_ignores_the_asset_is_refused(studio):
    client, factory, _tmp, settings = studio
    project_id, shot = _setup(client)
    client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "旧怀表", "description": ""})
    before = _task(factory, project_id)

    class Forgetful:
        def profile_for_role(self, db, role):
            return object()

        def complete_for_role(self, db, role, **kwargs):
            return SimpleNamespace(parsed={"story_beat": "A man sits quietly at the table."})

    assert run_pending_shot_revision(factory, settings, "worker-test", gateway=Forgetful())
    after = _task(factory, project_id)
    assert after.id == before.id
    assert after.status == "FAILED"
    assert after.error_code == "SHOT_REVISION_ASSET_NOT_MENTIONED"

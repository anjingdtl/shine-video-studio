"""Per-shot subtitle lines compile into the existing external subtitle track."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from shine.director.contracts import EpisodePlanPayload, digest_of
from shine.director.models import DirectorEpisodePlan
from shine.director.subtitles import track_matches_episode
from tests.conftest import create_project, create_shot, freeze_style

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}


def _setup(client, factory):
    project_id = create_project(client, "镜头字幕")
    style = freeze_style(client, project_id)
    first = create_shot(client, project_id, style["id"], title="第一镜", duration=6)
    second = create_shot(client, project_id, style["id"], title="第二镜", duration=5)
    plan = EpisodePlanPayload.model_validate({
        "mode": "shots", "target_seconds": 60, "tolerance_seconds": 15,
        "episodes": [{"episode_id": "e1", "order": 1, "title": "第 1 集",
                      "shot_ids": [first["id"], second["id"]], "planned_seconds": 11}],
    })
    with factory() as db:
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=15,
            payload=plan.model_dump(mode="json"),
            sha256=digest_of(plan.model_dump(mode="json")), source="supplied",
        ))
        db.commit()
    return project_id, first, second


def test_lines_are_saved_frozen_and_compiled_with_shot_offsets(studio):
    client, factory, _tmp, _settings = studio
    project_id, first, second = _setup(client, factory)

    saved = client.put(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{first['id']}/subtitles",
        headers=LOCAL,
        json={"lines": [
            {"start_s": 0, "end_s": 2, "text": "第一行"},
            {"start_s": 2, "end_s": 4, "text": "第二行"},
        ], "freeze": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "FROZEN"
    assert saved.json()["episode_id"] == "e1"

    client.put(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{second['id']}/subtitles",
        headers=LOCAL,
        json={"lines": [{"start_s": 1, "end_s": 3, "text": "下一镜"}], "freeze": True})

    view = client.get(f"/api/v1/projects/{project_id}/director/storyboard/episodes/e1/subtitles")
    assert view.status_code == 200, view.text
    assert view.json()["configured_count"] == 2
    assert view.json()["frozen_count"] == 2

    compiled = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/episodes/e1/subtitles/compile",
        headers=LOCAL)
    assert compiled.status_code == 201, compiled.text
    body = compiled.json()
    assert body["basis"] == "planned"
    assert body["cue_count"] == 3
    starts = [cue["start_s"] for cue in body["cues"]]
    # The second shot starts after the first shot's planned six seconds.
    assert starts == [0.0, 2.0, 7.0]
    assert any("真实成片时长" in warning for warning in body["warnings"])


def test_unfrozen_lines_do_not_enter_the_track(studio):
    client, factory, _tmp, _settings = studio
    project_id, first, _second = _setup(client, factory)
    client.put(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{first['id']}/subtitles",
        headers=LOCAL,
        json={"lines": [{"start_s": 0, "end_s": 2, "text": "草稿"}], "freeze": False})
    compiled = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/episodes/e1/subtitles/compile",
        headers=LOCAL)
    assert compiled.status_code == 409
    assert compiled.json()["detail"]["code"] == "EPISODE_SUBTITLE_EMPTY"


def test_lines_outside_the_shot_are_refused(studio):
    client, factory, _tmp, _settings = studio
    project_id, first, _second = _setup(client, factory)
    refused = client.put(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{first['id']}/subtitles",
        headers=LOCAL,
        json={"lines": [{"start_s": 0, "end_s": 30, "text": "太长"}], "freeze": True})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "SHOT_SUBTITLE_OUTSIDE_SHOT"


def test_a_revision_compiled_for_one_episode_is_not_used_for_another():
    assert track_matches_episode(SimpleNamespace(episode_id="e2"), "e1") is False
    assert track_matches_episode(SimpleNamespace(episode_id=None), "e1") is True


def test_overlapping_lines_are_refused(studio):
    client, factory, _tmp, _settings = studio
    project_id, first, _second = _setup(client, factory)
    with pytest.raises(HTTPException) as excinfo:
        from shine.director.subtitles import save_shot_subtitle_lines

        with factory() as db:
            from shine.models import Shot

            shot = db.get(Shot, first["id"])
            save_shot_subtitle_lines(
                db, shot.project, shot,
                [{"start_s": 0, "end_s": 3, "text": "A"},
                 {"start_s": 2, "end_s": 4, "text": "B"}],
                freeze=True,
            )
    assert excinfo.value.detail["code"] == "SHOT_SUBTITLE_OVERLAP"

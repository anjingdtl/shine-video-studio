from __future__ import annotations

from shine.providers import MockH3Provider
from shine.worker import run_one
from tests.conftest import (
    create_job,
    create_project,
    create_shot,
    freeze_style,
    shot_payload,
    style_payload,
)


def _freeze_new_version(client, project_id: str) -> dict:
    draft = style_payload()
    draft["color_palette"] = "冷蓝夜景与低饱和肤色"
    assert client.put(f"/api/v1/projects/{project_id}/style/draft", json=draft).status_code == 200
    response = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_new_shot_defaults_to_current_frozen_style(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    body = shot_payload("")
    response = client.post(f"/api/v1/projects/{project_id}/shots", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["spec"]["style_version_id"] == style["id"]


def test_superseded_style_cannot_be_used_for_new_shots_or_jobs(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    old_style = freeze_style(client, project_id)
    historical = create_shot(client, project_id, old_style["id"], title="历史镜头")
    new_style = _freeze_new_version(client, project_id)
    assert new_style["version"] == 2

    rejected = client.post(
        f"/api/v1/projects/{project_id}/shots",
        json=shot_payload(old_style["id"], title="新镜头"),
    )
    assert rejected.status_code == 422, rejected.text

    revised = client.post(
        f"/api/v1/shots/{historical['id']}/revise",
        json=shot_payload(old_style["id"], title="历史镜头", beat="历史镜头保留原风格")["spec"],
    )
    assert revised.status_code == 200, revised.text

    job = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={"shot_id": historical["id"]},
    )
    assert job.status_code == 422, job.text
    assert "current frozen" in job.text


def test_identical_style_draft_cannot_create_a_duplicate_version(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    freeze_style(client, project_id)
    response = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={})
    assert response.status_code == 409
    assert "identical" in response.json()["detail"]


def test_style_change_invalidates_unstarted_jobs_but_preserves_passed_work(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    old_style = freeze_style(client, project_id)
    passed_shot = create_shot(client, project_id, old_style["id"], title="已完成")
    passed_job = create_job(client, project_id, passed_shot["id"])
    assert run_one(factory, MockH3Provider(), "worker-test") == passed_job["id"]
    queued_shot = create_shot(client, project_id, old_style["id"], title="待运行")
    queued_job = create_job(client, project_id, queued_shot["id"])

    frozen = _freeze_new_version(client, project_id)
    assert queued_job["id"] in frozen["impact"]["invalidated_job_ids"]
    assert queued_shot["id"] in frozen["impact"]["affected_shot_ids"]

    passed = client.get(f"/api/v1/jobs/{passed_job['id']}").json()
    assert passed["status"] == "PASSED" and passed["stale"] is False
    queued = client.get(f"/api/v1/jobs/{queued_job['id']}").json()
    assert queued["status"] == "NEEDS_REVIEW"
    assert queued["stale"] is True
    assert queued["failure_code"] == "STYLE_VERSION_SUPERSEDED"


def test_style_from_another_project_is_rejected(studio):
    client, _factory, _root, _settings = studio
    first = create_project(client, "甲项目")
    second = create_project(client, "乙项目")
    first_style = freeze_style(client, first)
    freeze_style(client, second)
    response = client.post(
        f"/api/v1/projects/{second}/shots",
        json=shot_payload(first_style["id"], title="跨项目风格"),
    )
    assert response.status_code == 422, response.text
    assert "this project" in response.text


def test_style_versions_record_hash_approver_and_time(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    frozen = freeze_style(client, project_id)
    assert frozen["approved_by"] == "user"
    assert len(frozen["sha256"]) == 64
    assert frozen["frozen_at"]
    styles = client.get(f"/api/v1/projects/{project_id}/styles").json()
    assert len(styles) == 1 and styles[0]["sha256"] == frozen["sha256"]

from __future__ import annotations

from shine.models import JobDependency
from shine.providers import MockH3Provider
from shine.worker import run_one
from tests.conftest import create_job, create_project, create_shot, freeze_style


def _continuity(previous_shot_id: str, previous_job_id: str, mode: str = "last_frame") -> dict:
    return {
        "mode": mode,
        "previous_shot_id": previous_shot_id,
        "previous_job_id": previous_job_id,
    }


def test_continuity_requires_a_previous_job_reference(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(
        client,
        project_id,
        style["id"],
        title="续接",
        continuity={"mode": "last_frame", "previous_shot_id": "missing"},
    )
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": shot["id"]})
    assert response.status_code == 422
    assert "previous_job_id" in response.text


def test_continuity_dependency_is_recorded_and_scheduled(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    parent_shot = create_shot(client, project_id, style["id"], title="前镜")
    parent_job = create_job(client, project_id, parent_shot["id"])
    assert run_one(factory, MockH3Provider(), "worker-continuity") == parent_job["id"]
    child_shot = create_shot(
        client,
        project_id,
        style["id"],
        title="后镜",
        continuity=_continuity(parent_shot["id"], parent_job["id"]),
    )
    child = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": child_shot["id"]})
    assert child.status_code == 201, child.text
    assert child.json()["status"] == "QUEUED"
    with factory() as db:
        dependency = db.query(JobDependency).filter_by(child_job_id=child.json()["id"]).one()
        assert dependency.kind == "continuity:last_frame"
    assert run_one(factory, MockH3Provider(), "worker-continuity") == child.json()["id"]
    assert client.get(f"/api/v1/jobs/{child.json()['id']}").json()["status"] == "PASSED"


def test_comfy_continuity_requires_a_real_source_artifact(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    parent_shot = create_shot(client, project_id, style["id"], title="前镜")
    parent_job = create_job(client, project_id, parent_shot["id"])
    child_shot = create_shot(
        client,
        project_id,
        style["id"],
        title="后镜",
        continuity=_continuity(parent_shot["id"], parent_job["id"], "video_reference"),
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={"shot_id": child_shot["id"], "provider": "comfyui", "workflow_profile_id": "missing"},
    )
    assert response.status_code == 409
    assert "continuity" in response.text.lower() or "workflow" in response.text.lower()


def test_continuity_source_must_still_be_the_current_revision(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    parent_shot = create_shot(client, project_id, style["id"], title="前镜")
    parent_job = create_job(client, project_id, parent_shot["id"])
    run_one(factory, MockH3Provider(), "worker")
    child_shot = create_shot(
        client,
        project_id,
        style["id"],
        title="后镜",
        continuity=_continuity(parent_shot["id"], parent_job["id"]),
    )
    client.post(
        f"/api/v1/shots/{parent_shot['id']}/revise",
        json={
            "story_beat": "前镜已经被替换",
            "duration_target_s": 6,
            "style_version_id": style["id"],
        },
    )
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": child_shot["id"]})
    assert response.status_code == 409
    assert "stale" in response.text.lower() or "current" in response.text.lower()

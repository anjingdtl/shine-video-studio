"""The synthetic renderer verifies orchestration only, never real video quality."""

from dataclasses import replace

import pytest
from fastapi import HTTPException

from shine.comfy.client import ComfyUIClient
from shine.comfyops.native import production_review_binding
from shine.llm.base import ProviderConfig
from shine.models import Job
from shine.quality.basic_character_reviewer import BasicCharacterReviewer
from shine.worker import run_one
from tests.conftest import (
    create_comfy_job,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
    register_workflow_profile,
)
from tests.test_comfy_completion_window import fake_comfy  # noqa: F401


def test_basic_scope_records_gates_but_offline_render_cannot_be_real_pass(api, fake_comfy):  # noqa: F811
    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"], self_review_scope="basic",
                           basic_review_resolution={"width": 768, "height": 1344})
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    worker_settings = replace(settings, render_provider="comfyui", offline_simulation=True)
    run_one(factory, worker_id="basic-fixture", settings=worker_settings,
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2))
    state = client.get(f"/api/v1/jobs/{job['id']}").json()
    gates = client.get(f"/api/v1/jobs/{job['id']}/quality-gates").json()
    assert fake_comfy.prompt_count == 1
    assert state["status"] != "PASSED"
    assert state["quality_verification"] == "mock_verified"
    final = next(gate for gate in gates if gate["gate_name"] == "final")
    assert final["evidence"]["scope"] == "basic-self-review-v1"
    assert final["status"] != "passed"


def test_basic_reviewer_cannot_use_legacy_unfrozen_production_capability(api):
    client, factory, _settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"], self_review_scope="basic",
                           basic_review_resolution={"width": 768, "height": 1344})
    config = ProviderConfig(profile_id="p", name="fixture", kind="openai_compatible",
                            base_url="http://127.0.0.1:8116", model="fixture", api_key=None)
    with factory() as db, pytest.raises(HTTPException, match="BASIC_REVIEW_REQUIRES_FROZEN_NATIVE_CAPABILITY"):
        production_review_binding(db, project, job["id"], "VLM", BasicCharacterReviewer(config), live=True)


def test_basic_character_job_rejected_before_h3_without_frozen_qualified_reviewer(api):
    client, _factory, _settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], characters=["A"])
    profile = register_workflow_profile(client)
    response = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui", "workflow_profile_id": profile["id"],
        "self_review_scope": "basic", "basic_review_resolution": {"width": 768, "height": 1344},
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "BASIC_REVIEW_REQUIRES_FROZEN_NATIVE_CAPABILITY"
    assert client.get(f"/api/v1/projects/{project}/jobs").json() == []


def test_worker_rechecks_basic_capability_before_submitting(api, fake_comfy):  # noqa: F811
    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], characters=["A"])
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        stored = db.get(Job, job["id"])
        stored.request = {**stored.request, "self_review_scope": "basic",
                          "basic_review_resolution": {"width": 768, "height": 1344}}
    run_one(factory, worker_id="basic-capability-fixture", settings=replace(settings, render_provider="comfyui"),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2))
    state = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["failure_code"] == "BASIC_REVIEW_REQUIRES_FROZEN_NATIVE_CAPABILITY"
    assert fake_comfy.prompt_count == 0

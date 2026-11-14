"""Mainline V3 WP2 (C-04a): one H3 admission, not two.

The manual ``POST /comfyops/install-plans`` route used to build LOCAL_ISOLATED
plans from any registered package ids without the H3 variant qualification or
the candidate acknowledgement that ``POST /setup/install-plan`` enforces.  A
local plan that names an H3 catalog's video weights now defers to the setup
admission by name; the expert route keeps serving diagnosis (fake executor),
review weights and custom packages.
"""

from __future__ import annotations

from shine.comfyops.deployment import h3_catalog_closure_requested
from tests.conftest import create_project

H3_WEIGHTS = ["minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae"]
REVIEW_ONLY = ["qwen3-vl-8b-fp8"]
STRUCTURE = ["comfyui", "comfy-runtime-wheels"]


def test_h3_weights_are_detected_in_any_h3_catalog():
    assert h3_catalog_closure_requested(H3_WEIGHTS) is True
    assert h3_catalog_closure_requested([H3_WEIGHTS[0]]) is True
    assert h3_catalog_closure_requested(STRUCTURE + H3_WEIGHTS) is True


def test_review_and_image_and_custom_packages_are_not_h3():
    """Review weights, image weights and custom packages keep their own path."""
    assert h3_catalog_closure_requested(REVIEW_ONLY) is False
    assert h3_catalog_closure_requested(["qwen-image-2.1-unet", "qwen-image-2.1-vae"]) is False
    assert h3_catalog_closure_requested(["my-custom-package"]) is False
    assert h3_catalog_closure_requested([]) is False


def test_local_h3_plan_defers_to_setup_admission(api):
    client, _, _ = api
    project_id = create_project(client)

    response = client.post("/api/v1/comfyops/install-plans", json={
        "project_id": project_id, "package_ids": H3_WEIGHTS,
        "disk_free": 10 ** 12, "isolated": True, "executor": "local",
    })
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "H3_INSTALL_PLAN_USES_SETUP_ADMISSION"
    assert "/setup/install-plan" in detail["message"]


def test_fake_executor_stays_a_diagnosis_path(api):
    """The fake executor never downloads; it must keep working for diagnosis."""
    client, _, _ = api
    project_id = create_project(client)

    response = client.post("/api/v1/comfyops/install-plans", json={
        "project_id": project_id, "package_ids": H3_WEIGHTS,
        "disk_free": 10 ** 12, "isolated": True, "executor": "fake",
    })
    # Not the H3 admission refusal: it proceeds to package resolution.
    assert response.status_code == 409
    assert response.json()["detail"] == "PACKAGE_MISSING"


def test_review_weights_and_custom_packages_keep_the_expert_route(api):
    client, _, _ = api
    project_id = create_project(client)

    for package_ids in (REVIEW_ONLY, ["my-custom-package"]):
        response = client.post("/api/v1/comfyops/install-plans", json={
            "project_id": project_id, "package_ids": package_ids,
            "disk_free": 10 ** 12, "isolated": True, "executor": "local",
        })
        assert response.status_code == 409
        assert response.json()["detail"] == "PACKAGE_MISSING"

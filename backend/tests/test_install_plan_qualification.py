"""The install plan re-checks the hardware on the server.

A disabled button is not a gate.  These tests call the endpoint directly -
the way a script or a stale browser tab would - and prove that a host which
does not meet the variant's thresholds is refused with a named reason, and
that a candidate variant is never installed automatically.
"""

# ruff: noqa: F811 - the shared "prep" fixture is imported on purpose
from __future__ import annotations

import copy

from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

H3_MODEL_COMPONENTS = ["minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae"]


def _unqualified_host(*args, **kwargs) -> dict:
    """A host whose GPU cannot be identified at all."""
    from tests.conftest import qualified_host_facts

    facts = copy.deepcopy(qualified_host_facts())
    facts["gpus"] = []
    facts["gpu"] = {"status": "BLOCKED", "reason": "NVIDIA_DRIVER_UNAVAILABLE"}
    facts["cuda"] = {"status": "UNKNOWN", "reason": "CUDA_COMPUTE_CAPABILITY_UNKNOWN"}
    facts["evidence"] = {**facts["evidence"], "failures": [
        {"section": "gpu", "code": "NVIDIA_DRIVER_UNAVAILABLE"},
        {"section": "cuda", "code": "CUDA_COMPUTE_CAPABILITY_UNKNOWN"},
    ]}
    return facts


def _too_small_host(*args, **kwargs) -> dict:
    """A host with a card that is simply too small for the audited baseline."""
    from tests.conftest import qualified_host_facts

    facts = copy.deepcopy(qualified_host_facts())
    facts["gpus"][0].update({"vram_total_mb": 8192, "vram_free_mb": 8192})
    facts["gpu"].update({"vram_mb": 8192, "vram_total_mb": 8192, "vram_free_mb": 8192})
    return facts


def test_an_unqualified_host_is_refused_even_when_the_call_bypasses_the_ui(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _unqualified_host)
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
    }, headers=HEADERS)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "HOST_NOT_QUALIFIED"
    assert detail["status"] == "UNKNOWN"
    assert detail["variant_id"] == "h3-ref2va-int8-convrot"
    assert any(row["code"] == "GPU_NOT_IDENTIFIED" for row in detail["unknowns"])


def test_a_card_below_the_floor_is_refused_with_the_real_numbers(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _too_small_host)
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
    }, headers=HEADERS)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "HOST_NOT_QUALIFIED"
    assert detail["status"] == "BLOCKED"
    blocker = detail["blockers"][0]
    assert blocker["code"] == "VRAM_INSUFFICIENT"
    assert blocker["required"] == 15360
    assert blocker["actual"] == 8192


def test_a_qualified_host_gets_a_plan_that_records_the_variant(prep):
    client, factory, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["variant_id"] == "h3-ref2va-int8-convrot"
    assert body["qualification"]["status"] == "SUPPORTED"
    with factory() as db:
        from shine.comfyops.models import InstallationPlan

        plan = db.get(InstallationPlan, body["plan_id"])
        assert plan.payload["variant"]["variant_id"] == "h3-ref2va-int8-convrot"
        assert plan.payload["qualification"]["status"] == "SUPPORTED"
        assert plan.payload["download_breakdown"]["selected_bytes"] > 0


def test_a_candidate_variant_is_never_installed_automatically(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
        "variant_id": "ref2va-lowend-candidate",
    }, headers=HEADERS)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VARIANT_NOT_DOWNLOADABLE"
    assert detail["acceptance_status"] == "CANDIDATE"


def test_the_blocked_w4_slot_is_never_installed_either(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
        "variant_id": "ref2va-w4-convrot",
    }, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "VARIANT_NOT_DOWNLOADABLE"


def test_a_component_only_plan_is_not_gated_by_the_video_variant(prep):
    """A plan for the ComfyUI source alone carries no production variant."""
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": ["comfyui"], "project_id": project, "disk_free": 10 ** 15,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["variant_id"] is None

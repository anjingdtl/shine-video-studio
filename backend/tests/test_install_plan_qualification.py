"""The install plan re-checks the hardware on the server.

A disabled button is not a gate.  These tests call the endpoint directly -
the way a script or a stale browser tab would - and prove that a host which
does not meet the variant's thresholds is refused with a named reason, and
that a candidate variant is never installed automatically.
"""

# ruff: noqa: F811 - the shared "prep" fixture is imported on purpose
from __future__ import annotations

import copy

from sqlalchemy import select

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
    # A candidate cannot be paired with the default INT8 catalog.
    assert detail["code"] == "VARIANT_CATALOG_MISMATCH"


def test_a_removed_variant_slot_is_refused(prep):
    """W4 ConvRot 槽位按用户裁定（2026-11-14）删除；未知变体一律具名拒绝。"""
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "catalog": "qwen-image-2.1-windows-x64", "components": ["qwen-image-2.1-unet"],
        "project_id": project, "disk_free": 10 ** 15,
        "variant_id": "ref2va-w4-convrot",
    }, headers=HEADERS)
    assert response.status_code == 404, response.text
    assert "VARIANT_MANIFEST_UNKNOWN" in response.json()["detail"]


def test_a_component_only_plan_is_not_gated_by_the_video_variant(prep):
    """A plan for the ComfyUI source alone carries no production variant."""
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "components": ["comfyui"], "project_id": project, "disk_free": 10 ** 15,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["variant_id"] is None


LOWEND_CATALOG = "h3-ref2va-lowend-candidate-windows-x64"
LOWEND_COMPONENTS = [
    "minimax-h3-diffusion-w6a8", "minimax-h3-text-encoder-nvfp4", "minimax-h3-vae-int8",
]


def test_a_candidate_download_needs_explicit_acknowledgement(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "catalog": LOWEND_CATALOG, "components": LOWEND_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 15,
        "variant_id": "ref2va-lowend-candidate",
    }, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "VARIANT_NOT_DOWNLOADABLE"


def test_an_acknowledged_candidate_download_is_planned_and_audited(prep):
    """2026-11-14 用户裁定：候选权重可显式确认后下载；验收与生产门禁不变。"""
    client, factory, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/install-plan", json={
        "catalog": LOWEND_CATALOG, "components": LOWEND_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 15,
        "variant_id": "ref2va-lowend-candidate",
        "candidate_download_acknowledged": True,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["variant_id"] == "ref2va-lowend-candidate"
    with factory() as db:
        from shine.models import AuditEvent

        rows = db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "CANDIDATE_VARIANT_DOWNLOAD_AUTHORIZED"
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].entity_id == "ref2va-lowend-candidate"

"""The resource preview derives pins from the selected graph, never installs."""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_project, make_studio


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def test_candidate_preview_is_exact_and_does_not_claim_trial(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="资源预检")
    response = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project,
        "strategy": {"model_variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20"},
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert {row["component_id"] for row in data["components"]} == {
        "minimax-h3-diffusion-w6a8", "minimax-h3-vae-int8",
        "minimax-h3-audio-vae-fp32", "minimax-h3-text-encoder-nvfp4",
    }
    assert data["missing_download_bytes"] == 35087209179
    assert data["trial_ready"] is False and data["production_ready"] is False
    assert data["node_abi"] == "LIVE_CHECK_PENDING"
    assert any(row["code"] for row in data["production_blockers"])


def test_candidate_pdd_preview_refuses_instead_of_substitution(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="资源预检")
    response = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project, "strategy": {"model_variant_id": "ref2va-lowend-candidate"},
    })
    assert response.status_code == 200
    assert response.json()["blockers"][0]["code"] == "VARIANT_RECIPE_UNVERIFIED"
    assert response.json()["download_eligible"] is False


def test_candidate_plan_is_approval_required_and_revalidates_catalog(studio, tmp_path, monkeypatch):
    from shine.comfyops.models import InstallationPlan
    from shine.comfyops.resource_resolver import validate_resource_plan

    client, factory, settings = studio
    monkeypatch.setattr("shine.comfyops.storage.require_storage", lambda settings: tmp_path)
    monkeypatch.setattr("shine.comfyops.storage.disk_info", lambda path: {"free_bytes": 10**12})
    monkeypatch.setattr("shine.comfyops.resource_resolver.managed_installation_pin",
                        lambda db, instance_id: {"id": instance_id, "sha256": "a" * 64})
    project = create_project(client, title="授权资源计划")
    strategy = {"model_variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20"}
    preview = client.post("/api/v1/setup/resources/resolve", json={"project_id": project, "strategy": strategy}).json()
    request = {"project_id": project, "strategy": strategy, "snapshot_sha256": preview["snapshot_sha256"],
               "instance_id": "managed-one", "source_preference": "MIRROR_FIRST", "disk_free": 10**12}
    response = client.post("/api/v1/setup/resources/install-plan", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "AWAITING_APPROVAL"
    with factory() as db:
        plan = db.get(InstallationPlan, response.json()["plan_id"])
        assert plan.payload["resource_context"]["source_preference"] == "MIRROR_FIRST"
        validate_resource_plan(db, settings, plan.payload)
        bad = {**plan.payload, "packages": plan.payload["packages"][:-1]}
        with pytest.raises(ValueError, match="RESOURCE_PLAN_COMPONENTS_CHANGED"):
            validate_resource_plan(db, settings, bad)
    request["snapshot_sha256"] = "0" * 64
    assert client.post("/api/v1/setup/resources/install-plan", json=request).status_code == 409


def test_candidate_catalog_cannot_be_installed_without_variant(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="候选门禁")
    response = client.post("/api/v1/setup/install-plan", json={
        "project_id": project, "catalog": "h3-ref2va-lowend-candidate-windows-x64",
        "components": ["minimax-h3-diffusion-w6a8"], "disk_free": 10**12,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "VARIANT_ID_REQUIRED"


def test_generic_plan_cannot_bypass_candidate_variant_binding():
    from shine.comfyops.resource_resolver import reject_unbound_candidate_checkpoint
    from shine.comfyops.variants import load_variant_manifest

    manifest = load_variant_manifest("ref2va-lowend-candidate")
    dit = next(row for row in manifest["files"] if row["role"] == "dit")
    payload = {"packages": [{"id": "renamed", "spec": {"files": [{"sha256": dit["sha256"]}]}}]}
    with pytest.raises(ValueError, match="VARIANT_ID_REQUIRED"):
        reject_unbound_candidate_checkpoint(payload)
    reject_unbound_candidate_checkpoint({**payload, "resource_context": {"schema": "shine.resource-plan.v2"}})


def test_catalog_pin_mismatch_is_not_download_eligible(studio, monkeypatch):
    from copy import deepcopy
    from dataclasses import replace

    from shine.comfyops import resource_resolver

    client, _factory, _settings = studio
    binding = resource_resolver.load_binding("ref2va-lowend-candidate")
    catalog = deepcopy(binding.catalog)
    component = next(row for row in catalog["components"] if row["component_id"] == "minimax-h3-diffusion-w6a8")
    component["files"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(resource_resolver, "load_binding", lambda variant: replace(binding, catalog=catalog))
    project = create_project(client, title="错位清单")
    data = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project, "strategy": {"model_variant_id": binding.variant_id, "accelerator_id": "native20"},
    }).json()
    assert not data["download_eligible"]
    assert any(row["code"] == "VARIANT_CATALOG_MISMATCH" for row in data["blockers"])


def test_resource_install_holds_lifecycle_guard_and_refuses_busy_renderer(studio, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from shine.comfyops import installation_worker

    _client, factory, settings = studio
    held = []

    @contextmanager
    def guard(instance_id):
        held.append(instance_id)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr("shine.runtime_orchestrator.guard.renderer_lifecycle_guard", guard)
    monkeypatch.setattr("shine.comfyops.resource_resolver.validate_resource_plan", lambda *args: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_port_listeners", lambda port: [])
    from shine.comfyops.models import ComfyInstance
    with factory.begin() as db:
        db.add(ComfyInstance(id="shared", payload={"port": 8188}, sha256="a" * 64, status="STOPPED"))
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda *args: {
        "renderer_state": "H3", "gpu_leases": [], "in_flight_h3_jobs": [], "in_flight_image_work": [],
    })
    finished = []
    monkeypatch.setattr(installation_worker, "_finish", lambda *args, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(installation_worker, "_run_local_attempt", lambda *args, **kwargs: pytest.fail("downloaded"))
    plan = SimpleNamespace(payload={"resource_context": {"instance_id": "shared"}})
    result = installation_worker._run_resource_attempt(factory, "worker", "attempt", plan, settings=settings)
    assert result == "FAILED_RETRYABLE"
    assert finished[0]["error_code"] == "RESOURCE_INSTALLATION_BUSY"
    assert held == []


def test_resource_instance_requires_real_integrity_pinned_physical_installation(studio):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.instances import instance_identity
    from shine.comfyops.models import ComfyInstance
    from shine.comfyops.resource_resolver import managed_installation_pin

    _client, factory, _settings = studio
    identity = instance_identity("local", 8188)
    payload = {"managed": True, "is_mock": False, "port": 8188}
    with factory.begin() as db:
        db.add(ComfyInstance(id=identity, payload=payload, sha256=graph_hash(payload), status="STOPPED"))
    with factory() as db:
        assert managed_installation_pin(db, identity) == {"id": identity, "sha256": graph_hash(payload)}
    with factory.begin() as db:
        db.get(ComfyInstance, identity).payload = {**payload, "is_mock": True}
    with factory() as db, pytest.raises(ValueError, match="RESOURCE_INSTANCE_UNVERIFIED"):
        managed_installation_pin(db, identity)

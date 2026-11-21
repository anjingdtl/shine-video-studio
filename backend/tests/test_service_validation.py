"""Live setup-service validation: fake probes, no model load, no real process."""
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from shine.comfyops import deployment, service_validation
from shine.comfyops.runtime_configuration import (
    ACTIVE_KEY,
    CANDIDATE_KEY,
    runtime_report,
    service_evidence_status,
)
from shine.models import AgentRoute, AppSetting, ProviderProfile, WorkflowProfile
from tests.conftest import make_studio
from tests.test_new_machine_setup import test_configuration_generation_and_recipe_validation as exercise_configuration
from tests.test_v13_preparation import HEADERS, TOKEN, post

pytestmark = pytest.mark.integration

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="managed service validation is Windows-only")


@pytest.fixture
def prep(tmp_path, fake_deployment_host):
    app, factory, settings = make_studio(tmp_path)
    settings = replace(settings, operator_id="setup-operator",
                       operator_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest())
    app.state.settings = settings
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def _fake_object_info() -> dict:
    object_info = {}
    for path in deployment.PROFILE_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in raw["graph"].values():
            object_info.setdefault(node["class_type"], {"input": {}})
    return object_info


def _fake_review(role, spec):
    manifest = json.loads(Path(spec["manifest"]).read_text(encoding="utf-8"))
    model = manifest["asr"]["model_id"] if role == "ASR" else manifest["model_id"]
    return {
        "role": role,
        "model": model,
        "base_url": f"http://127.0.0.1:{spec['port']}/v1",
        "port": spec["port"],
        "runtime_sha256": "b" * 64,
        "manifest_sha256": manifest["sha256"],
        "capabilities": {
            "available": True,
            "is_mock": False,
            "model_listed": True,
            "review_response_protocol": "local-review-json-schema-v1",
            "review_decoder_sha256": "c" * 64,
        },
    }


@WINDOWS_ONLY
def test_machine_service_validation_needs_no_project(prep, monkeypatch):
    client, factory, settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory.begin() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        candidate.value = {**candidate.value, "project_id": None}
        digest = candidate.value["identity"]["recipe_sha256"]
    monkeypatch.setattr(service_validation, "_probe_renderer",
                        lambda recipe, **_kw: {"port": recipe["renderer"]["port"], "object_info": _fake_object_info()})
    with factory() as db:
        result = service_validation.validate_services(db, settings, None, digest, actor="local-user")
    assert result["status"] == "READY"
    with factory() as db:
        assert service_evidence_status(db, settings)["status"] == "READY"


@pytest.mark.skipif(__import__('os').name != 'nt', reason='managed renderer probing is Windows-only')
def test_registered_workflow_managed_probe_records_only_node_validation(prep, monkeypatch):
    client, factory, settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory.begin() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        identity = candidate.value['identity']
        project_id = candidate.value['project_id']
        db.add(AppSetting(key=ACTIVE_KEY, value={'identity': identity, 'project_id': project_id}))
    registered = post(client, '/setup/profiles', {'project_id': project_id})
    profile_id = registered['profiles'][0]['profile_id']
    monkeypatch.setattr('shine.comfyops.runtime_configuration._current_identity', lambda db, settings: identity)
    monkeypatch.setattr(service_validation, 'load_runtime_recipe',
                        lambda *a, **kw: ({'renderer': {}}, identity['recipe_sha256']))
    monkeypatch.setattr(service_validation, '_probe_renderer',
                         lambda recipe, **_kw: {'object_info': _fake_object_info()})

    response = client.post(f'/api/v1/workflow-profiles/{profile_id}/validate-managed')
    assert response.status_code == 200, response.text
    proof = response.json()['validated_against']
    assert proof['missing_nodes'] == [] and proof['missing_inputs'] == []
    assert proof['generation_verified'] is False and proof['services_stopped'] is True
    assert proof['source'] == 'managed_live_object_info'

    def cleanup_failed(recipe, **_kw):
        raise HTTPException(409, 'SERVICE_VALIDATION_CLEANUP_FAILED')

    monkeypatch.setattr(service_validation, '_probe_renderer', cleanup_failed)
    with pytest.raises(RuntimeError, match='SERVICE_VALIDATION_CLEANUP_FAILED'):
        client.post(f'/api/v1/workflow-profiles/{profile_id}/validate-managed')
    from shine.comfyops.models import ResourceLease
    with factory() as db:
        assert db.scalar(select(ResourceLease.id).where(ResourceLease.status == 'RECONCILE_REQUIRED'))


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_service_validation_binds_reviewer_models_and_l4_evidence(prep, monkeypatch):
    client, factory, settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        project_id = candidate.value["project_id"]
        recipe_sha256 = candidate.value["identity"]["recipe_sha256"]
    post(client, "/setup/profiles", {"project_id": project_id})

    monkeypatch.setattr(service_validation, "_probe_renderer",
                        lambda recipe, **_kw: {"object_info": _fake_object_info(),
                                        "port": recipe["renderer"]["port"]})
    monkeypatch.setattr(service_validation, "_probe_review", _fake_review)
    monkeypatch.setattr(service_validation, "_review_resource_blockers", lambda recipe: [])

    result = post(client, "/setup/services/validate", {
        "project_id": project_id, "recipe_sha256": recipe_sha256,
    }, operator=True)

    assert result["status"] == "READY"
    assert result["node_count"] > 0
    assert result["services_stopped"] is True
    with factory() as db:
        profiles = {row.role: db.get(ProviderProfile, row.provider_profile_id)
                    for row in db.scalars(select(AgentRoute)).all()
                    if row.role in {"vlm_reviewer", "asr_reviewer"}}
        assert profiles["vlm_reviewer"].model == "qwen3-vl-8b-fp8"
        assert profiles["asr_reviewer"].model == "qwen3-asr-0.6b"
        status = service_evidence_status(db, settings)
        assert status["status"] == "READY", status
        assert status["review_models"] == {
            "VLM": "qwen3-vl-8b-fp8", "ASR": "qwen3-asr-0.6b",
        }
        assert status["advisory_reviews"]["VLM"]["status"] == "READY"
        assert status["advisory_reviews"]["ASR"]["status"] == "READY"
        # Evidence exists before activation, but the active runtime has not
        # claimed it yet.
        assert runtime_report(db, settings)["status"] == "NOT_ACTIVATED"

    monkeypatch.setattr(deployment, "installed_instance_blockers", lambda *args, **kwargs: [])
    with factory() as db:
        readiness = deployment.deployment_readiness(db, settings)
    layer4 = next(layer for layer in readiness["layers"] if layer["layer"] == "L4_services_and_contracts")
    assert layer4["status"] == "READY"
    assert layer4["node_count"] > 0


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_service_validation_rejects_live_workflow_contract_missing_node(prep, monkeypatch):
    client, factory, settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        project_id = candidate.value["project_id"]
        recipe_sha256 = candidate.value["identity"]["recipe_sha256"]
    post(client, "/setup/profiles", {"project_id": project_id})
    object_info = _fake_object_info()
    object_info.pop("MiniMaxH3ReferenceToVideo", None)
    monkeypatch.setattr(service_validation, "_probe_renderer",
                        lambda recipe, **_kw: {"object_info": object_info,
                                        "port": recipe["renderer"]["port"]})
    monkeypatch.setattr(service_validation, "_review_resource_blockers", lambda recipe: [])
    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": project_id, "recipe_sha256": recipe_sha256,
    }, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CORE_MODE_NODES_MISSING"


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_missing_review_models_and_runtime_are_advisory_and_do_not_block_renderer(prep, monkeypatch):
    client, factory, settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        project_id = candidate.value["project_id"]
        recipe_sha256 = candidate.value["identity"]["recipe_sha256"]
    started = []
    monkeypatch.setattr(service_validation, "_probe_renderer", lambda recipe: (
        started.append(True) or {"object_info": _fake_object_info(), "port": recipe["renderer"]["port"]}
    ))

    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": project_id, "recipe_sha256": recipe_sha256,
    }, headers=HEADERS)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "READY"
    assert started == [True]
    assert result["auto_registered_profiles"] == result["workflow_profiles"] == []
    assert set(result["mode_capabilities"]["accelerators"]) == {"pdd8", "native20"}
    assert result["advisory_reviews"]["VLM"]["status"] == "NOT_READY"
    assert result["advisory_reviews"]["ASR"]["status"] == "NOT_READY"
    assert not result["review_models"]
    with factory() as db:
        registered = db.scalars(select(WorkflowProfile).where(WorkflowProfile.project_id == project_id)).all()
        assert registered == []
        status = service_evidence_status(db, settings)
        assert status["status"] == "READY", status
        assert status["advisory_reviews"]["VLM"]["status"] == "NOT_READY"
        assert status["advisory_reviews"]["ASR"]["status"] == "NOT_READY"


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_unavailable_review_probes_are_advisory_after_clean_process_exit(prep, monkeypatch):
    client, factory, _settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        project_id = candidate.value["project_id"]
        recipe_sha256 = candidate.value["identity"]["recipe_sha256"]
    post(client, "/setup/profiles", {"project_id": project_id})
    monkeypatch.setattr(service_validation, "_review_resource_blockers", lambda _recipe: [])
    monkeypatch.setattr(service_validation, "_probe_renderer", lambda recipe: {
        "object_info": _fake_object_info(), "port": recipe["renderer"]["port"],
    })
    monkeypatch.setattr(service_validation, "_probe_review", lambda role, _spec: (_ for _ in ()).throw(
        HTTPException(409, f"SERVICE_VALIDATION_REVIEW_UNAVAILABLE:{role}"),
    ))

    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": project_id, "recipe_sha256": recipe_sha256,
    }, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "READY"
    assert {role: item["status"] for role, item in response.json()["advisory_reviews"].items()} == {
        "VLM": "UNAVAILABLE", "ASR": "UNAVAILABLE",
    }


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_review_probe_cleanup_failure_keeps_core_environment_unconfirmed(prep, monkeypatch):
    client, factory, _settings = prep
    exercise_configuration(prep, monkeypatch)
    with factory() as db:
        candidate = db.get(AppSetting, CANDIDATE_KEY)
        project_id = candidate.value["project_id"]
        recipe_sha256 = candidate.value["identity"]["recipe_sha256"]
    post(client, "/setup/profiles", {"project_id": project_id})
    monkeypatch.setattr(service_validation, "_review_resource_blockers", lambda _recipe: [])
    monkeypatch.setattr(service_validation, "_probe_renderer", lambda recipe: {
        "object_info": _fake_object_info(), "port": recipe["renderer"]["port"],
    })
    monkeypatch.setattr(service_validation, "_probe_review", lambda _role, _spec: (_ for _ in ()).throw(
        HTTPException(409, "SERVICE_VALIDATION_CLEANUP_FAILED"),
    ))

    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": project_id, "recipe_sha256": recipe_sha256,
    }, headers=HEADERS)

    assert response.status_code == 409
    assert "SERVICE_VALIDATION_CLEANUP_FAILED" in response.text


def test_service_validation_stops_waiting_when_owned_process_exits(monkeypatch):
    class ExitedGroup:
        def exit_code_if_stopped(self):
            return 9

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(service_validation, "_json_get", unavailable)
    with pytest.raises(HTTPException) as failure:
        service_validation._wait_json(
            "http://127.0.0.1:8116/health", timeout_s=300,
            predicate=lambda body: True, owned_group=ExitedGroup(),
        )
    assert failure.value.detail["code"] == "SERVICE_VALIDATION_PROCESS_EXITED"
    assert failure.value.detail["exit_code"] == 9


@pytest.mark.windows_only
@WINDOWS_ONLY
def test_service_validation_requires_candidate_and_operator(prep):
    client, _, _ = prep
    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": "missing", "recipe_sha256": "a" * 64,
    }, headers=HEADERS)
    assert response.status_code == 409
    assert "GENERATE_RUNTIME_CONFIGURATION_FIRST" in response.text
    response = client.post("/api/v1/setup/services/validate", json={
        "project_id": "missing", "recipe_sha256": "a" * 64,
    })
    assert response.status_code in {401, 403}


def test_dynamic_baseline_uses_native_when_pdd_and_audio_nodes_are_absent():
    from shine.comfyops.mode_capabilities import mode_workflows
    from shine.foundation.h3 import compile_h3_ref2va_graph

    native, pdd = mode_workflows()
    object_info = {name: {"input": {}} for name in native.required_nodes}
    report = service_validation._validate_mode_workflows(object_info)
    assert set(report["accelerators"]) == {"native20"}
    assert report["accelerator_advisories"]["pdd8"]["missing_nodes"] == sorted(
        set(pdd.required_nodes) - set(native.required_nodes),
    )
    audio = compile_h3_ref2va_graph(
        image_count=1, audio_count=1, accelerator_id="native20", object_info=object_info,
    )
    assert audio.validated_against["missing_nodes"] == ["VHS_LoadAudioUpload"]

    object_info.pop("MiniMaxH3ReferenceToVideo")
    with pytest.raises(HTTPException) as exc_core:
        service_validation._validate_mode_workflows(object_info)
    assert exc_core.value.detail["code"] == "CORE_MODE_NODES_MISSING"
    assert exc_core.value.detail["missing_nodes"] == ["MiniMaxH3ReferenceToVideo"]


def test_pdd_sampler_can_validate_without_native_only_sage_attention():
    from shine.comfyops.mode_capabilities import mode_workflows

    _, pdd = mode_workflows()
    report = service_validation._validate_mode_workflows({name: {} for name in pdd.required_nodes})
    assert set(report["accelerators"]) == {"pdd8"}
    missing = report["accelerator_advisories"]["native20"]["missing_nodes"]
    assert missing == ["MiniMaxH3MemoryEfficientSageAttentionPatch"]

"""Independent acceptance of production setup contracts; temporary fixtures only."""
import hashlib
import json
import shutil
import zipfile
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment
from shine.comfyops.instances import dependency_lock, instance_root
from shine.comfyops.models import ComfyInstance
from shine.models import WorkflowProfile
from tests.conftest import create_project, make_studio
from tests.test_new_machine_setup import (
    test_configuration_generation_and_recipe_validation as exercise_configuration,
)
from tests.test_v13_preparation import HEADERS, TOKEN


@pytest.fixture
def prep(tmp_path, fake_deployment_host):
    app, factory, settings = make_studio(tmp_path)
    settings = replace(settings, operator_id="acceptance-operator",
                       operator_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest())
    app.state.settings = settings
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def test_wizard_install_payload_is_accepted(prep):
    client, _, _ = prep
    project = create_project(client)
    components = [c["component_id"] for c in deployment.load_default_catalog()["components"]]
    # These are the values createInstallPlan actually sends from SetupPanel.
    response = client.post("/api/v1/setup/install-plan", json={
        "components": components, "project_id": project, "disk_free": 10**15,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text


def test_wizard_instance_endpoint_exists(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/instance-chain", json={
        "project_id": project, "instance_name": "shine-h3", "port": 8188, "mode": "local",
    }, headers=HEADERS)
    assert response.status_code != 404, response.text


def test_shipped_profiles_can_be_registered_on_empty_db(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.post("/api/v1/setup/profiles", json={"project_id": project}, headers=HEADERS)
    assert response.status_code == 200, response.text


def test_profile_registration_rejects_same_name_with_different_graph(prep):
    client, factory, _ = prep
    project = create_project(client)
    graph = {"1": {"class_type": "UnrelatedWorkflow", "inputs": {}}}
    with factory.begin() as db:
        for name in deployment.load_default_catalog()["workflow_profiles"]:
            db.add(WorkflowProfile(project_id=project, name=name, mode="Ref2VA", graph=graph,
                                   graph_sha256=graph_hash(graph), parameter_map={}, required_nodes=[]))
    response = client.post("/api/v1/setup/profiles", json={"project_id": project}, headers=HEADERS)
    assert response.status_code == 409, response.text


def test_install_node_plan_can_build_its_offline_dependency_lock(prep):
    client, factory, settings = prep
    project = create_project(client)
    catalog = deployment.load_default_catalog()
    name, port = "node-fixture", 8197
    root = instance_root(settings, name, "fake")
    root.mkdir(parents=True)
    (root / "requirements.txt").write_text("torch\n", encoding="utf-8")
    # Tiny valid wheel bytes stand in for CREATE's verified package input.
    # No pip or model download is needed to exercise the actual lock builder.
    package_root = settings.data_dir / "comfyops" / "isolated-packages"
    wheel_dir = package_root / deployment.WHEEL_COMPONENT_ID
    wheel_dir.mkdir(parents=True)
    wheel = wheel_dir / "torch-2.7.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("torch-2.7.1.dist-info/METADATA", "Name: torch\nVersion: 2.7.1\n")
    component = next(c for c in catalog["components"] if c["component_id"] == deployment.WHEEL_COMPONENT_ID)
    component['source_builds'] = []  # This fixture replaces the complete runtime with one tiny wheel.
    component["files"] = [{
        "url": "https://files.pythonhosted.org/fixture/torch.whl", "relative_path": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "size": wheel.stat().st_size,
    }]
    (root / ".wheelhouse").mkdir()
    shutil.copyfile(wheel, root / ".wheelhouse" / wheel.name)
    with factory.begin() as db:
        rows = deployment.register_catalog_packages(
            db, catalog, [c["component_id"] for c in catalog["components"]],
        )
        for row in rows.values():
            row.status = "FILES_VERIFIED_PENDING_RUNTIME"
        identity = str(uuid5(NAMESPACE_URL, f"shine-comfy-instance:fake:http://127.0.0.1:{port}"))
        db.add(ComfyInstance(id=identity, status="STOPPED", sha256="0" * 64, payload={
            "managed": True, "name": name, "root": str(root), "port": port, "is_mock": True,
        }))
    with factory() as db:
        plan, action = deployment.next_instance_plan(
            db, settings, catalog, project, instance_name=name, port=port, mode="fake",
        )
        assert action == "INSTALL_NODE"
        packages = plan.payload["packages"]
    # Execute the actual lock builder used before pip, with no subprocess or download.
    dependency_lock(root, packages, package_root)


def test_generated_asr_manifest_matches_existing_executor_contract(prep, monkeypatch):
    exercise_configuration(prep, monkeypatch)
    _, _, settings = prep
    manifest = json.loads((settings.comfyops_dir / "setup/manifests/asr.json").read_text("utf-8"))
    assert manifest.get("asr", {}).get("executor") == "qwen3-asr", (
        "local_review_server reads the ASR model only from manifest['asr']; generated manifest has none"
    )
    assert isinstance(manifest.get("sha256"), str), "executor /health reads manifest['sha256']"


def test_review_runtime_wheels_include_server_dependencies():
    catalog = deployment.load_default_catalog()
    wheel_component = next(c for c in catalog["components"] if c["kind"] == "wheel")
    names = {f.get("dependency") for f in wheel_component["files"]}
    required = {"fastapi", "httpx", "uvicorn"}
    assert required <= names, f"Review server imports are missing from deployment wheels: {required - names}"


def test_services_layer_does_not_pass_a_nonexistent_recipe(prep, monkeypatch):
    _, factory, settings = prep
    root = settings.data_dir / "managed-fixture"
    (root / ".venv").mkdir(parents=True)
    with factory.begin() as db:
        db.add(ComfyInstance(id="fixture", status="STOPPED", sha256="0" * 64,
                            payload={"managed": True, "root": str(root)}))
    monkeypatch.setenv("SHINE_MANAGED_RECIPE", str(settings.data_dir / "nonexistent-recipe.json"))
    with factory() as db:
        report = deployment.deployment_readiness(db, settings)
    layer = next(x for x in report["layers"] if x["layer"] == "L4_services_and_contracts")
    assert layer["status"] != "READY", layer
    installed = next(x for x in report["layers"] if x["layer"] == "L3_environment_installed")
    assert installed["status"] != "READY", installed


def test_environment_layer_rejects_real_flag_without_install_evidence(prep):
    _, factory, settings = prep
    root = settings.data_dir / 'comfyops/instances/unverified'
    (root / '.venv/Scripts').mkdir(parents=True)
    (root / '.venv/Scripts/python.exe').write_bytes(b'fixture; not a real executable')
    (root / 'main.py').write_text('# fixture')
    with factory.begin() as db:
        payload = {'managed': True, 'is_mock': False, 'root': str(root)}
        db.add(ComfyInstance(id='unverified', status='STOPPED', sha256=graph_hash(payload), payload=payload))
    with factory() as db:
        report = deployment.deployment_readiness(db, settings)
    installed = next(x for x in report['layers'] if x['layer'] == 'L3_environment_installed')
    assert installed['status'] == 'PENDING'
    assert 'COMPLETED_REAL_INSTALL_EVIDENCE_REQUIRED' in installed['blockers']

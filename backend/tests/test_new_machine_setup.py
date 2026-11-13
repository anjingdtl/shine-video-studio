"""New-machine deployment: catalog contract, controlled plan assembly, generated configuration, readiness layers.

Offline: isolated databases, tmp directories and synthetic bytes. No model
download, no real ComfyUI start and no personal paths. The committed catalog is
validated as real contract data; plan execution uses the existing fake/local
machinery (fake runners, offline caches), and every test states what it does
NOT prove: real model inference is never simulated.
"""

# ruff: noqa: F811
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from shine.comfyops import deployment
from shine.comfyops.managed_recipe import adapter_identity, load_managed_recipe
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, approve, post, prep  # noqa: F401

CORE_SMALL = ["comfyui", "comfy-runtime-wheels"]
FULL_COMPONENTS = [
    "comfyui", "comfy-runtime-wheels", "ComfyUI-KJNodes", "comfyui-videohelpersuite",
    "ComfyUI-MiniMax-H3-PDD-Acc", "ComfyUI-SolAttn_triton", "Comfyui_Minimax_h3_latent_Upscaler",
    "minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae", "minimax-h3-lora",
    "minimax-h3-pdd", "qwen3-vl-8b-fp8", "qwen3-asr-0.6b",
]


def mutated_catalog(tmp_path: Path, mutate) -> Path:
    """Write a mutated copy of the committed catalog into a temp catalog dir."""
    raw = json.loads(deployment.load_default_catalog_path().read_text(encoding="utf-8"))
    mutate(raw)
    target = tmp_path / "mutated.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


def test_catalog_is_valid_contract_data():
    raw = deployment.load_default_catalog()
    by_id = deployment._component_map(raw)
    assert set(by_id) == set(FULL_COMPONENTS)
    for component in raw["components"]:
        assert component["status"] == "READY"
        for f in component["files"]:
            assert len(f["sha256"]) == 64 and f["size"] > 0 and f["url"].startswith("https://")
    # The four families the production graph binds actually exist.
    families = {c["destination_family"] for c in raw["components"] if c["kind"] == "model"}
    assert {"diffusion_models", "text_encoders", "vae", "loras", "pdd_acc"} <= families


def test_catalog_rejects_untrusted_url(tmp_path, monkeypatch):
    mutated_catalog(tmp_path, lambda raw: raw["components"][0]["files"][0].__setitem__(
        "url", "https://evil.example/comfyui.zip"))
    monkeypatch.setattr(deployment, "CATALOG_DIR", tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        deployment.load_deployment_catalog("mutated")
    assert "UNTRUSTED_CATALOG_URL" in str(excinfo.value.detail)


def test_catalog_rejects_unsafe_relative_path(tmp_path, monkeypatch):
    mutated_catalog(tmp_path, lambda raw: raw["components"][0]["files"][0].__setitem__(
        "relative_path", "../../escape.zip"))
    monkeypatch.setattr(deployment, "CATALOG_DIR", tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        deployment.load_deployment_catalog("mutated")
    assert "UNSAFE_CATALOG_PATH" in str(excinfo.value.detail)


@pytest.mark.windows_only
def test_install_plan_via_setup_endpoint(prep):
    client, factory, settings = prep
    project = create_project(client)
    body = {
        "components": CORE_SMALL, "project_id": project, "disk_free": 10**12,
    }
    result = post(client, "/setup/install-plan", body)
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["download_bytes"] > 0
    plan_id = result["plan_id"]
    # Approval flows through the existing grant -> approve -> execute machinery.
    plan = approve(client, {"id": plan_id}, "install", f"/comfyops/install-plans/{plan_id}/approve")
    assert plan["status"] == "APPROVED"

    class AvailableInstaller:
        """Executor-port injection: records what a real verified install would produce."""

        def install(self, spec):
            return {"status": "AVAILABLE", "files": [f.model_dump() for f in spec.files]}

    import os

    if os.name != "nt":
        pytest.skip("the local package executor only runs on the authorized Windows host")
    from unittest import mock
    with mock.patch.dict("os.environ", {"SHINE_ENABLE_LOCAL_EXECUTION": "1"}):
        exec_result = post(client, f"/comfyops/install-plans/{plan_id}/execute",
                           {"plan_hash": plan["sha256"], "executor": "local"}, operator=True,
                           expected=(200, 202))
    # The installation is a persistent task: the API queues the attempt, the
    # Worker executes it, and the browser re-attaches by attempt id.
    assert exec_result["status"] == "QUEUED" and exec_result["attempt_id"]
    from shine.comfyops.installation import claim_next_attempt
    from shine.comfyops.installation_worker import run_attempt
    assert claim_next_attempt(factory, "test-worker") == exec_result["attempt_id"]
    with mock.patch("shine.comfyops.installation_worker.local_execution_enabled", lambda: True):
        stage = run_attempt(factory, "test-worker", exec_result["attempt_id"], settings=settings,
                            installer=AvailableInstaller())
    assert stage == "FILES_VERIFIED_PENDING_RUNTIME"
    # Packages carry the catalog identity; re-planning stays idempotent.
    again = post(client, "/setup/install-plan", body)
    assert again["status"] == "AWAITING_APPROVAL"
    assert again["packages"] == result["packages"]


def test_install_plan_requires_real_disk_budget(prep):
    client, factory, settings = prep
    project = create_project(client)
    post(client, "/setup/install-plan",
         {"components": CORE_SMALL, "project_id": project, "disk_free": 0}, expected=409)


def test_component_content_change_is_detected(prep, tmp_path):
    client, factory, settings = prep
    project = create_project(client)
    raw = deployment.load_default_catalog()
    post(client, "/setup/install-plan",
         {"components": ["comfyui"], "project_id": project, "disk_free": 10**12})
    # Register a mutated comfyui component under the same id: content identity must refuse.
    import copy
    mutated = copy.deepcopy(raw)
    for component in mutated["components"]:
        if component["component_id"] == "comfyui":
            component["revision"] = "0" * 40
    target = tmp_path / "h3-ref2va-windows-x64.json"
    target.write_text(json.dumps(mutated), encoding="utf-8")
    from unittest import mock
    with mock.patch.object(deployment, "CATALOG_DIR", tmp_path):
        response = client.post("/api/v1/setup/install-plan",
                               json={"components": ["comfyui"], "project_id": project, "disk_free": 10**12})
    assert response.status_code == 409
    assert "CATALOG_COMPONENT_CHANGED" in str(response.json()["detail"])


def test_next_instance_plan_state_machine(prep):
    """The setup flow advances one controlled step at a time; nothing executes here."""
    from sqlalchemy import select

    client, factory, settings = prep
    project = create_project(client)
    post(client, "/setup/install-plan",
         {"components": FULL_COMPONENTS, "project_id": project, "disk_free": 10**15})
    # Simulate a completed real install (the state a verified local install reaches).
    from shine.comfyops.models import ModelPackage, NodePackage
    with factory() as db:
        for row in db.scalars(select(ModelPackage)):
            row.status = "FILES_VERIFIED_PENDING_RUNTIME"
        for row in db.scalars(select(NodePackage)):
            row.status = "FILES_VERIFIED_PENDING_RUNTIME"
        db.commit()
    # Step 1: no managed instance -> CREATE plan carrying the catalog source + wheels.
    step = post(client, "/setup/next-instance-plan",
                {"project_id": project, "instance_name": "shine-h3", "port": 8193, "mode": "fake"})
    assert step["action"] == "CREATE" and not step["complete"]
    from sqlalchemy import select

    from shine.comfyops.models import ComfyInstance, InstallationPlan
    with factory() as db:
        plan = db.get(InstallationPlan, step["plan_id"])
        spec_names = [p["spec"]["name"] for p in plan.payload["packages"]]
        assert "comfyui" in spec_names and "comfy-runtime-wheels" in spec_names
        assert plan.payload["operation"]["source_package_id"] == "comfyui"
    # Without executing, the state machine stays at CREATE (the instance row only
    # appears after the CREATE plan really executes).
    step_again = post(client, "/setup/next-instance-plan",
                      {"project_id": project, "instance_name": "shine-h3", "port": 8193, "mode": "fake"})
    assert step_again["action"] == "CREATE"
    from uuid import NAMESPACE_URL, uuid5
    identity = str(uuid5(NAMESPACE_URL, "shine-comfy-instance:fake:http://127.0.0.1:8193"))
    with factory() as db:
        payload = {"managed": True, "name": "shine-h3", "port": 8193,
                   "root": str(Path(settings.data_dir) / "comfyops" / "fake-instances" / "shine-h3"),
                   "is_mock": True}
        db.add(ComfyInstance(id=identity, payload=payload, sha256="0" * 64, status="STOPPED"))
        db.commit()
    node_step = post(client, "/setup/next-instance-plan",
                     {"project_id": project, "instance_name": "shine-h3", "port": 8193, "mode": "fake"})
    assert node_step["action"] == "INSTALL_NODE"
    with factory() as db:
        plan = db.get(InstallationPlan, node_step["plan_id"])
        node_names = {p["spec"]["name"] for p in plan.payload["packages"]}
        assert node_names == {"comfy-runtime-wheels", "ComfyUI-KJNodes", "comfyui-videohelpersuite",
                              "ComfyUI-MiniMax-H3-PDD-Acc", "ComfyUI-SolAttn_triton",
                              "Comfyui_Minimax_h3_latent_Upscaler"}
    # With node_versions recorded, the next step is BIND_MODELS with all five families.
    with factory() as db:
        instance = db.get(ComfyInstance, identity)
        instance.payload = {**instance.payload,
                            "node_versions": {cid: c['revision'] for cid, c in deployment._component_map(
                                deployment.load_default_catalog()).items() if c['kind'] == 'node'}}
        instance.sha256 = "1" * 64
        db.commit()
    bind_step = post(client, "/setup/next-instance-plan",
                     {"project_id": project, "instance_name": "shine-h3", "port": 8193, "mode": "fake"})
    assert bind_step["action"] == "BIND_MODELS"
    with factory() as db:
        plan = db.get(InstallationPlan, bind_step["plan_id"])
        assert plan.payload["operation"]["model_bindings"] == {
            "diffusion_models": "minimax-h3-diffusion",
            "text_encoders": "minimax-h3-text-encoder",
            "vae": "minimax-h3-vae",
            "loras": "minimax-h3-lora",
            "pdd_acc": "minimax-h3-pdd",
        }


def test_configuration_generation_and_recipe_validation(prep, monkeypatch):
    """Config generation against a fixture-built instance (v13-style synthetic bytes)."""
    client, factory, settings = prep
    project = create_project(client)
    # Synthetic files exercise configuration only. The local identity is stopped;
    # it is not an installation receipt or evidence of real runtime acceptance.

    from shine.comfyops.instances import instance_identity, instance_root
    from shine.comfyops.models import ComfyInstance
    root = instance_root(settings, 'shine-cfg', 'local')
    root.mkdir(parents=True)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    venv_python.write_bytes(b"FAKE EXECUTABLE - NEVER EXECUTE")
    (root / "custom_nodes").mkdir()
    (root / "main.py").write_bytes(b"# synthetic entrypoint")
    (root / '.shine-requirements.txt').write_text('# synthetic lock for configuration contract', encoding='utf-8')
    with factory() as db:
        payload = {"managed": True, "name": "shine-cfg", "port": 8195,
                   "root": str(root), "is_mock": False,
                   "model_bindings": {"diffusion_models": "minimax-h3-diffusion"}}
        db.add(ComfyInstance(id=instance_identity('local', 8195), payload=payload,
                             sha256=deployment.graph_hash(payload), status="STOPPED"))
        db.commit()
    def fake_gpu():
        return {"status": "OK", "index": "0", "uuid": "GPU-TEST-UUID", "name": "Test GPU",
                "driver_version": "1.0", "vram_mb": 16384, "at_floor": True}

    monkeypatch.setattr("shine.comfyops.host_env.probe_gpu", fake_gpu)
    env_target = Path(settings.data_dir) / "setup-env"
    monkeypatch.setattr(deployment, "ENV_PATH", env_target)
    result = post(client, "/setup/configuration", {
        "project_id": project, "instance_name": "shine-cfg", "port": 8195}, operator=True)
    recipe_raw = json.loads(Path(result["recipe"]).read_text(encoding="utf-8"))
    assert result["adapter_sha256"] == adapter_identity(recipe_raw["renderer"]["adapter_dir"])
    recipe = load_managed_recipe(result["recipe"])
    assert recipe["renderer"]["adapter_sha256"] == result["adapter_sha256"]
    assert "shine_h3_sampling_mode.py" in " ".join(recipe["renderer"]["argv"])
    assert recipe["renderer"]["gpu_uuid"] == "GPU-TEST-UUID"
    assert result["env_keys"] and env_target.exists()
    env_text = env_target.read_text(encoding="utf-8")
    assert "SHINE_SETUP_RECIPE=" in env_text
    assert "SHINE_RENDER_PROVIDER=" not in env_text and "SHINE_MANAGED_RECIPE=" not in env_text
    for role in ("vlm", "asr"):
        manifest = json.loads(Path(result["review_manifests"][role.upper()]).read_text(encoding="utf-8"))
        assert manifest["files"] and all(len(f["sha256"]) == 64 for f in manifest["files"])
    with factory() as db:
        instance = db.get(ComfyInstance, instance_identity('local', 8195))
        assert instance.payload["model_bindings"]["diffusion_models"] == "minimax-h3-diffusion"


def test_readiness_never_claims_real_inference(prep):
    client, factory, settings = prep
    readiness = client.get("/api/v1/setup/readiness").json()
    layers = {layer["layer"]: layer for layer in readiness["layers"]}
    assert layers["L5_real_inference"]["status"] == "NOT_VERIFIED"
    assert "never inferred" in layers["L5_real_inference"]["note"]
    assert readiness["production_gates"]["operator_configured"] is True
    assert readiness["production_gates"]["local_confirmation_available"] is True
    assert "确认下载安装" in " ".join(readiness["human_steps"])


def test_open_probes_require_no_operator(prep):
    client, factory, settings = prep
    for path in ("/api/v1/setup/environment", "/api/v1/setup/catalog", "/api/v1/setup/readiness"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)


def test_environment_probe_reports_this_host(tmp_path):
    from shine.comfyops.host_env import probe_host_environment, setup_blockers

    report = probe_host_environment(tmp_path / "probe-data")
    assert report["gpu"]["status"] in {"OK", "BLOCKED"}
    assert set(report["tools"]) == {"uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe"}
    blockers = setup_blockers(report)
    for blocker in blockers:
        assert blocker["code"]


# ---------------------------------------------------------------------------
# One physical target, one live attempt (U01) and one approved combination (U02)
# ---------------------------------------------------------------------------


@pytest.mark.windows_only
def test_the_same_install_request_is_one_plan_and_one_attempt(prep, monkeypatch):
    """Two tabs, a refresh and a lost receipt re-attach the same physical work.

    The old defect queued two attempts for the same plan hash.  The identity now
    belongs to the normalized storage root and the component closure, so a
    repeated request returns the one plan and the one attempt it already owns.
    """
    client, _, _ = prep
    monkeypatch.setenv("SHINE_ENABLE_LOCAL_EXECUTION", "1")
    if os.name != "nt":
        # Queueing the attempt needs the Windows local executor.  The Windows
        # CI job runs this same contract on the real platform; here the endpoint
        # correctly answers LOCAL_EXECUTOR_UNAVAILABLE.
        pytest.skip("the local package executor only runs on the authorized Windows host")
    project = create_project(client)
    body = {"components": CORE_SMALL, "project_id": project, "disk_free": 10**12}

    first = post(client, "/setup/install-plan", body)
    second = post(client, "/setup/install-plan", body)
    assert second["plan_id"] == first["plan_id"], "the same work must not become a second plan"
    assert second["sha256"] == first["sha256"]

    plan = approve(client, {"id": first["plan_id"]}, "install",
                   f"/comfyops/install-plans/{first['plan_id']}/approve")
    execute_body = {"plan_hash": plan["sha256"], "executor": "local"}
    first_attempt = post(client, f"/comfyops/install-plans/{first['plan_id']}/execute",
                         execute_body, operator=True, expected=(200, 202))
    again = post(client, f"/comfyops/install-plans/{first['plan_id']}/execute",
                 execute_body, operator=True, expected=(200, 202))
    assert again["attempt_id"] == first_attempt["attempt_id"]

    from sqlalchemy import func, select

    from shine.comfyops.models import InstallationAttempt

    with client.app.state.session_factory() as db:
        total = db.scalar(select(func.count()).select_from(InstallationAttempt)) or 0
    assert total == 1, "exactly one attempt exists for this target"


@pytest.mark.windows_only
def test_two_different_component_sets_cannot_share_the_same_storage_root(prep, monkeypatch):
    """Different content in the same directory is a conflicting identity."""
    client, _, _ = prep
    monkeypatch.setenv("SHINE_ENABLE_LOCAL_EXECUTION", "1")
    if os.name != "nt":
        pytest.skip("the local package executor only runs on the authorized Windows host")
    project_a = create_project(client, "甲")
    project_b = create_project(client, "乙")
    first = post(client, "/setup/install-plan",
                 {"components": CORE_SMALL, "project_id": project_a, "disk_free": 10**12})
    second = post(client, "/setup/install-plan",
                  {"components": ["comfyui"], "project_id": project_b, "disk_free": 10**12})
    plan_a = approve(client, {"id": first["plan_id"]}, "install",
                     f"/comfyops/install-plans/{first['plan_id']}/approve")
    plan_b = approve(client, {"id": second["plan_id"]}, "install",
                     f"/comfyops/install-plans/{second['plan_id']}/approve")
    post(client, f"/comfyops/install-plans/{first['plan_id']}/execute",
         {"plan_hash": plan_a["sha256"], "executor": "local"}, operator=True, expected=(200, 202))
    refused = client.post(
        f"/api/v1/comfyops/install-plans/{second['plan_id']}/execute",
        json={"plan_hash": plan_b["sha256"], "executor": "local"}, headers=HEADERS,
    )
    assert refused.status_code == 409, refused.text
    assert "INSTALL_TARGET_CONFLICT" in refused.text


def test_the_install_plan_states_the_combination_that_was_really_chosen(prep):
    """The card's choice reaches the approved plan, and image capability is separate."""
    client, _, _ = prep
    project = create_project(client)
    without_image = post(client, "/setup/install-plan", {
        "components": CORE_SMALL, "project_id": project, "disk_free": 10**12,
    })
    assert without_image["configuration"] == {"h3_video": True, "image_prep": False}
    assert without_image["companion_plans"] == []

    with_image = post(client, "/setup/install-plan", {
        "components": CORE_SMALL, "project_id": project, "disk_free": 10**12,
        "include_image_capability": True,
    })
    # A different combination is a different plan, and the H3 plan never
    # silently absorbs the image catalog's bytes.
    assert with_image["plan_id"] != without_image["plan_id"]
    assert with_image["configuration"] == {"h3_video": True, "image_prep": True}
    assert with_image["download_bytes"] == without_image["download_bytes"]
    companion = with_image["companion_plans"][0]
    assert companion["capability"] == "image_prep"
    assert companion["bytes"] > 0 and companion["file_declarations"] > 0
    assert companion["catalog"] == "shine-qwen-image-21-windows-x64"


def test_package_metadata_counts_the_catalog_it_ships():
    """The ZIP metadata is counted from the catalog, not typed by hand."""
    import re

    script = (Path(__file__).resolve().parents[2] / "scripts" / "build_test_package.ps1").read_text(
        encoding="utf-8-sig")
    assert "packages = 154" not in script
    assert "statistics_basis" in script and "file_declarations" in script
    catalog = deployment.load_default_catalog()
    declarations = sum(len(component["files"]) for component in catalog["components"])
    declared_bytes = sum(
        int(row["size"]) for component in catalog["components"] for row in component["files"])
    assert declarations == 189, declarations
    assert declared_bytes == 60_804_054_569
    # The shipped catalog's own total agrees with the per-file declarations.
    assert int(catalog["total_bytes"]) == declared_bytes
    assert re.search(r"components = \$catalogComponents\.Count", script)

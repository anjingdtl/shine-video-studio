"""V4.1 one-stop setup: one fact view, honest layering, durable preparation.

The two production blockers this file pins down:

* **P0-A** - weights that are already on disk must be verified and registered by
  the system, not re-requested from the person.
* **P0-B** - a saved image binding without a startable recipe is ``PENDING_RECIPE``
  and never "configured, ready to render".

Each test states one fact that must not be able to impersonate another:
FILES_FOUND ≠ FILES_VERIFIED ≠ INSTANCE_BOUND ≠ WORKFLOW_VALIDATED ≠ RENDER_VERIFIED.
"""
# ruff: noqa: F811  (the `prep` fixture is shadowed by design)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment, setup_reconcile, setup_summary
from shine.comfyops.instances import instance_identity, instance_root
from shine.comfyops.models import ComfyInstance
from shine.materialprep import runtime as image_runtime
from shine.models import AppSetting
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

pytestmark = pytest.mark.integration


def _managed_instance(prep, *, port: int = 8188, name: str = "shine-h3"):
    """A managed instance row whose root really looks like a ComfyUI checkout."""
    _, factory, settings = prep
    root = instance_root(settings, name, "local")
    (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"SYNTHETIC - NEVER EXECUTE")
    (root / "main.py").write_text("# fixture\n", encoding="utf-8")
    (root / "comfy").mkdir(parents=True, exist_ok=True)
    (root / "comfyui_version.py").write_text('__version__ = "0.3.40"\n', encoding="utf-8")
    # The installer writes this pin file with the instance; the runtime
    # identity hashes every pinned file, so a synthetic instance needs it too.
    (root / ".shine-requirements.txt").write_text("# fixture\n", encoding="utf-8")
    payload = {
        "managed": True, "name": name, "port": port, "root": str(root), "is_mock": False,
        "node_versions": {}, "model_bindings": {}, "process": None,
    }
    identity = instance_identity("local", port)
    with factory.begin() as db:
        db.add(ComfyInstance(id=identity, payload=payload, sha256=graph_hash(payload),
                             status="STOPPED"))
    return root, identity


def _bind_image_runtime(settings, *, root: Path, instance_id: str, port: int = 8188):
    config = image_runtime.ImageRuntimeConfig(
        config_revision=2,
        instance_id=instance_id,
        base_url=f"http://127.0.0.1:{port}",
        installation_id=instance_id,
        port=port,
        model_root=str(root / "models"),
        bound_at="2026-11-14T00:00:00Z",
    )
    image_runtime.save_config(settings, config)
    return config


# --- P0-B: a binding is not a renderer -------------------------------------


def test_bound_image_runtime_without_a_recipe_is_pending_not_ready(prep):
    """The exact screenshot state: binding saved, managed_recipe.json absent."""
    client, _, settings = prep
    root, identity = _managed_instance(prep)
    _bind_image_runtime(settings, root=root, instance_id=identity)

    body = client.get("/api/v1/setup/image-readiness").json()

    assert body["configured"] is True, "the binding itself must still be reported"
    assert body["binding_state"] == "PENDING_RECIPE"
    assert body["runnable"] is False, "a binding must never be reported as startable"
    assert body["layer"] == "NOT_CONFIGURED", "no family may look ready without a recipe"
    assert body["recipe"]["state"] == "MISSING"
    codes = {row["code"] for row in body["blockers"]}
    assert "IMAGE_RENDERER_RECIPE_MISSING" in codes
    assert all(not family["can_generate"] for family in body["families"].values())


def test_summary_and_image_readiness_tell_the_same_story(prep):
    """Two entry points, one fact: the page must not disagree with the API."""
    client, _, settings = prep
    project = create_project(client)
    root, identity = _managed_instance(prep)
    _bind_image_runtime(settings, root=root, instance_id=identity)

    readiness = client.get("/api/v1/setup/image-readiness").json()
    summary = client.get(f"/api/v1/setup/summary?project_id={project}").json()

    image = summary["capabilities"]["image"]
    assert image["facts"]["BINDING_STATE"] == readiness["binding_state"] == "PENDING_RECIPE"
    assert image["facts"]["INSTANCE_BOUND"] is False
    assert image["next_step"]["code"] == "PENDING_RECIPE"
    assert "完善本机运行配置" in image["next_step"]["label"]
    # The user must never be told to download a video model to fix an image
    # runtime, and the raw error code must not be the label.
    assert "IMAGE_RENDERER_RECIPE_MISSING" not in image["next_step"]["label"]


def test_summary_is_read_only(prep):
    """A GET that quietly wrote a recipe would make every check a side effect."""
    client, _, settings = prep
    project = create_project(client)
    root, identity = _managed_instance(prep)
    _bind_image_runtime(settings, root=root, instance_id=identity)

    client.get(f"/api/v1/setup/summary?project_id={project}")
    client.get(f"/api/v1/setup/summary?project_id={project}")

    assert not (settings.comfyops_dir / "setup" / "managed_recipe.json").exists()
    assert not (settings.comfyops_dir / "setup").exists()
    assert not (root / "custom_nodes").exists()


def test_the_recipe_is_only_written_for_the_authorized_capability(prep):
    """An image preparation must not create a video runtime candidate."""
    client, factory, settings = prep
    project = create_project(client)
    root, identity = _managed_instance(prep)
    _bind_image_runtime(settings, root=root, instance_id=identity)

    from shine.comfyops.setup_preparation import _step_image_bind

    task = {"project_id": project, "request": {"capabilities": ["image"]},
            "target": {"instance_id": identity, "name": "shine-h3", "root": str(root), "port": 8188}}
    # Without a recipe the bind step refuses by name instead of inventing one.
    result = _step_image_bind(None, settings, task)
    assert result["status"] == "FAILED"
    assert result["code"] == "IMAGE_RENDERER_RECIPE_MISSING"


# --- P0-A: existing bytes are registered by the system ---------------------


def test_found_weights_are_verified_and_registered_without_a_manual_step(prep, monkeypatch):
    """A directory that already holds the weights is proven, not re-requested."""
    client, factory, settings = prep
    project = create_project(client)
    root, _ = _managed_instance(prep)
    models = root / "models" / "diffusion_models"
    models.mkdir(parents=True, exist_ok=True)
    payload = b"synthetic-weight-bytes"
    (models / "synthetic.safetensors").write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()

    catalog = {
        "catalog_id": "test-catalog", "catalog_version": "1", "variant": None,
        "components": [{
            "component_id": "synthetic-model", "kind": "model", "revision": "r1",
            "destination_family": "diffusion_models", "license_id": "TEST",
            "usage": "research", "status": "READY", "dependencies": [],
            "files": [{"relative_path": "synthetic.safetensors", "sha256": sha,
                       "size": len(payload), "url": "https://example.invalid/file"}],
        }],
    }
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)

    with factory() as db:
        result = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), project_id=project, capability="h3")

    assert result["status"] == "RECONCILED"
    assert result["verified"] == 1
    assert result["complete"] is True
    assert result["registered"] == ["synthetic-model"]
    assert result["downloaded_bytes"] == 0 and result["copied_bytes"] == 0

    # Running it again registers nothing twice and reads no byte twice.
    with factory() as db:
        again = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), project_id=project, capability="h3")
    assert again["status"] == "RECONCILED"
    from shine.comfyops.models import ModelPackage

    with factory() as db:
        rows = db.scalars(select(ModelPackage).where(ModelPackage.id == "synthetic-model")).all()
    assert len(rows) == 1


# --- one identity: the resolved port, never a silent 8188 ------------------


def test_configuration_uses_the_resolved_port_not_a_default(prep, monkeypatch):
    """A machine whose instance lives on 8189 must not get an 8188 recipe."""
    client, factory, settings = prep
    project = create_project(client)
    root, identity = _managed_instance(prep, port=8189)
    (root / "custom_nodes").mkdir(parents=True, exist_ok=True)
    # A real H3 installation carries the pinned node packs; the configuration
    # contract now refuses by name when their directories are absent.
    for cid, comp in deployment._component_map(deployment.load_default_catalog()).items():
        if comp["kind"] == "node":
            (root / "custom_nodes" / cid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deployment, "ENV_PATH", settings.data_dir / "isolated-env")
    monkeypatch.setattr("shine.comfyops.host_env.probe_gpu",
                        lambda: {"status": "OK", "uuid": "GPU-TEST"})
    monkeypatch.setattr("shine.comfyops.review_runtime.prepare_review_runtime",
                        lambda *args, **kwargs: {"status": "SKIPPED"})

    response = client.post("/api/v1/setup/configuration",
                           json={"project_id": project}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["port"] == 8189
    assert body["installation_id"] == identity
    recipe = json.loads((settings.comfyops_dir / "setup" / "managed_recipe.json").read_text("utf-8"))
    assert recipe["renderer"]["port"] == 8189
    assert recipe["renderer"]["instance_id"] == identity


# --- the authorization becomes durable work --------------------------------


def test_prepare_execute_is_idempotent_and_survives_the_browser(prep):
    client, _, settings = prep
    project = create_project(client)
    _managed_instance(prep)

    body = {"project_id": project, "capabilities": ["image"], "verify_runtime": False}
    first = client.post("/api/v1/setup/prepare/execute", json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    task_id = first.json()["task_id"]

    second = client.post("/api/v1/setup/prepare/execute", json=body, headers=HEADERS)
    assert second.json()["task_id"] == task_id, "the same work must not become two tasks"

    summary = client.get(f"/api/v1/setup/summary?project_id={project}").json()
    assert summary["preparation"]["status"] == "QUEUED"
    assert summary["preparation"]["task_id"] == task_id
    assert summary["preparation"]["current"] == "RECONCILE"


def test_prepare_plan_never_promises_a_change_it_will_not_make(prep):
    client, factory, settings = prep
    project = create_project(client)
    _managed_instance(prep)
    body = {"project_id": project, "capabilities": ["image"], "verify_runtime": False}
    plan = client.post("/api/v1/setup/prepare-plan", json=body, headers=HEADERS).json()
    assert plan["will_modify_user_comfyui"] is False
    assert plan["plans"][0]["needs_recipe"] is True
    assert plan["plans"][0]["changes_external_installation"] is False
    assert plan["download_bytes"] > 0
    # A preview creates no plan row.
    from shine.comfyops.models import InstallationPlan

    with factory() as db:
        assert db.scalars(select(InstallationPlan)).all() == []


def test_missing_files_come_from_the_catalogue_not_from_a_failure(prep):
    client, _, settings = prep
    body = client.get("/api/v1/setup/missing-files?capabilities=image").json()
    assert body["groups"][0]["capability"] == "image"
    names = {row["filename"] for row in body["groups"][0]["files"]}
    assert "qwen_image_2.1_int8_convrot.safetensors" in names
    assert all(row["save_to"].startswith(str(settings.comfyops_dir)) for row in body["groups"][0]["files"])


def test_manual_files_import_into_the_existing_cache(prep, monkeypatch):
    """Hand-downloaded bytes join the same content-addressed cache."""
    _, factory, settings = prep
    payload = b"manually downloaded weight"
    sha = hashlib.sha256(payload).hexdigest()
    catalog = {
        "catalog_id": "manual-catalog", "catalog_version": "1", "variant": None,
        "components": [{
            "component_id": "manual-model", "kind": "model", "revision": "r1",
            "destination_family": "diffusion_models", "license_id": "TEST",
            "usage": "research", "status": "READY", "dependencies": [],
            "files": [{"relative_path": "manual.safetensors", "sha256": sha,
                       "size": len(payload), "url": "https://example.invalid/file"}],
        }],
    }
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    staging = settings.comfyops_dir / "manual-downloads" / "manual-catalog" / "manual-model"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "manual.safetensors").write_bytes(payload)

    with factory() as db:
        result = setup_reconcile.manual_files_reconcile(db, settings, capability="h3")

    assert result["status"] == "RECONCILED"
    assert [row["sha256"] for row in result["imported"]] == [sha]
    cache = settings.comfyops_dir / "isolated-packages" / ".cache" / sha
    assert cache.is_file() and cache.read_bytes() == payload
    assert (staging / "manual.safetensors").is_file(), "the original is never removed"


def test_a_wrong_manual_file_is_refused_and_kept(prep, monkeypatch):
    _, factory, settings = prep
    payload = b"not the pinned bytes"
    sha = hashlib.sha256(b"the pinned bytes").hexdigest()
    catalog = {
        "catalog_id": "manual-catalog", "catalog_version": "1", "variant": None,
        "components": [{
            "component_id": "manual-model", "kind": "model", "revision": "r1",
            "destination_family": "diffusion_models", "license_id": "TEST",
            "usage": "research", "status": "READY", "dependencies": [],
            "files": [{"relative_path": "manual.safetensors", "sha256": sha,
                       "size": len(payload), "url": "https://example.invalid/file"}],
        }],
    }
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    staging = settings.comfyops_dir / "manual-downloads" / "manual-catalog" / "manual-model"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "manual.safetensors").write_bytes(payload)

    with factory() as db:
        result = setup_reconcile.manual_files_reconcile(db, settings, capability="h3")

    assert result["imported"] == []
    assert len(result["absent"]) == 1
    assert not (settings.comfyops_dir / "isolated-packages" / ".cache" / sha).exists()
    assert (staging / "manual.safetensors").read_bytes() == payload


# --- the choice is asked once ---------------------------------------------


def test_the_installation_choice_is_remembered(prep):
    client, factory, settings = prep
    root, identity = _managed_instance(prep)
    other = settings.data_dir / "another-comfy"
    (other / "custom_nodes").mkdir(parents=True, exist_ok=True)
    (other / "main.py").write_text("# fixture\n", encoding="utf-8")

    response = client.post("/api/v1/setup/installation-choice",
                           json={"path": str(other), "port": 8190}, headers=HEADERS)
    assert response.status_code == 200, response.text

    summary = client.get("/api/v1/setup/summary").json()
    assert summary["installation"]["selected"]["root"] == str(other.resolve())
    assert summary["installation"]["choice_required"] is False
    with factory() as db:
        row = db.get(AppSetting, setup_summary.CHOICE_KEY)
    assert row is not None and row.value["port"] == 8190

"""New-machine setup reuses the installation, byte proof and recipe once."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from shine.comfyops import deployment, existing_installation, resource_discovery, setup_preparation, setup_reconcile
from shine.comfyops.models import ComfyInstance
from shine.comfyops.setup_summary import model_file_presence, record_installation_choice, resolve_installation

pytestmark = pytest.mark.integration


@pytest.fixture
def installed(studio, monkeypatch):
    client, factory, temporary, settings = studio
    root = temporary / "已有安装 空格" / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("# synthetic entry\n", encoding="utf-8")
    (root / "comfyui_version.py").write_text('__version__ = "0.39.0"\n', encoding="utf-8")
    python = root.parent / "python_embeded" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"synthetic interpreter, never executed")
    catalogs = {}
    for capability, family in (("h3", "diffusion_models"), ("image", "text_encoders")):
        data = ("synthetic weight for " + capability).encode()
        target = root / "models" / family / (capability + ".safetensors")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        catalogs[capability] = {
            "catalog_id": "test-" + capability, "catalog_version": "1",
            "components": [{
                "component_id": "minimax-h3-test" if capability == "h3" else "qwen-image-test",
                "kind": "model", "status": "READY", "revision": "r1", "license_id": "TEST",
                "usage": "research", "dependencies": [], "destination_family": family,
                "files": [{"relative_path": target.name, "sha256": hashlib.sha256(data).hexdigest(),
                           "size": len(data), "url": "https://example.invalid/weight"}],
            }],
        }
    monkeypatch.setattr(deployment, "load_default_catalog", lambda: catalogs["h3"])
    monkeypatch.setattr(deployment, "load_deployment_catalog", lambda name: catalogs["image"])
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalogs[capability])
    monkeypatch.setattr(deployment, "REVIEW_COMPONENTS", {})
    monkeypatch.setattr(deployment, "ENV_PATH", temporary / "test.env")
    monkeypatch.setattr("shine.comfyops.host_env.probe_gpu", lambda: {"status": "OK", "uuid": "GPU-test"})
    monkeypatch.setattr("shine.comfyops.adoption.probe_comfy_service",
                        lambda port: {"is_comfyui": False, "version": None})
    return factory, settings, root, catalogs


def test_reconcile_adopts_without_project_or_preinstalled_shine_nodes(installed):
    factory, settings, root, _ = installed
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    with factory() as db:
        result = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True)
    assert result["complete"] is True
    assert result["adoption"]["status"] == "ADOPTED"
    assert result["copied_bytes"] == result["downloaded_bytes"] == 0
    assert before == sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    with factory() as db:
        instance = db.get(ComfyInstance, result["adoption"]["instance_id"])
        assert not deployment.installed_instance_blockers(db, settings, instance, deployment.load_default_catalog())


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows paths are case-insensitive")
def test_windows_filename_case_does_not_turn_reuse_into_a_copy(installed):
    factory, settings, root, _ = installed
    source = root / "models/diffusion_models/h3.safetensors"
    source.rename(source.with_name("H3.SAFETENSORS"))
    with factory() as db:
        result = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True)
    assert result["adoption"]["status"] == "ADOPTED"
    assert result["copied_bytes"] == 0


def test_adding_image_evidence_preserves_h3_and_one_instance(installed):
    factory, settings, root, _ = installed
    for capability in ("h3", "image", "h3"):
        with factory() as db:
            result = setup_reconcile.reconcile_installation(
                db, settings, path=str(root), capability=capability, adopt=True)
        assert result["adoption"]["status"] in {"ADOPTED", "ALREADY_ADOPTED"}, result
    with factory() as db:
        instances = db.scalars(select(ComfyInstance)).all()
        assert len(instances) == 1
        adoption = instances[0].payload["adoption"]
        assert adoption["capabilities"] == ["h3", "image"]
        assert {row["capability"] for row in adoption["verified_files"]} == {"h3", "image"}


def test_recipe_deploys_only_to_shine_data_and_does_not_inherit_image_variant(installed):
    factory, settings, root, _ = installed
    with factory() as db:
        setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="image", adopt=True)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    with factory() as db:
        result = deployment.generate_configuration(
            db, settings, None, capability="image", variant_id="ref2va-lowend-candidate")
    recipe = json.loads(Path(result["recipe"]).read_text(encoding="utf-8"))
    renderer = recipe["renderer"]
    assert Path(renderer["adapter_dir"]).is_relative_to(settings.comfyops_dir)
    assert "--extra-model-paths-config" in renderer["argv"]
    assert before == sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    assert result["activation_required"] is False


def test_yaml_groups_multiline_and_json_bindings_share_the_same_parser(installed, tmp_path):
    _, _, root, catalogs = installed
    first = tmp_path / "external one"
    second = tmp_path / "external two"
    for name in ("unet-a", "unet-b"):
        (first / name).mkdir(parents=True, exist_ok=True)
    (second / "clip").mkdir(parents=True)
    (root / "extra_model_paths.yaml").write_text(
        "first:\n  base_path: '" + str(first) + "'\n  unet: |\n    unet-a\n    unet-b\n"
        "second:\n  base_path: '" + str(second) + "'\n  clip: clip\n", encoding="utf-8")
    families = existing_installation.declared_model_family_roots(root)
    assert families["unet"] == [first / "unet-a", first / "unet-b"]
    assert families["clip"] == [second / "clip"]
    source = root / "models/diffusion_models/h3.safetensors"
    source.replace(first / "unet-b/h3.safetensors")
    presence = model_file_presence(root / "models", catalogs["h3"], declared=families)
    assert presence["missing"] == []
    (root / "extra_model_paths.yaml").write_text(json.dumps({"shine": {"unet": str(first / "unet-b")}}))
    assert existing_installation.declared_model_family_roots(root)["unet"] == [first / "unet-b"]


def test_scan_index_is_used_by_the_same_installation_resolver(installed):
    factory, settings, root, _ = installed
    with factory() as db:
        state = resource_discovery.read_state(db)
        state["resources"]["comfy_roots"] = [{"root": str(root)}]
        resource_discovery._save(db, state)
        db.commit()
        assert resolve_installation(db, settings)["selected"]["root"] == str(root)
        record_installation_choice(db, root=str(root.parent), port=8193, actor="local-user")
        db.commit()
        selected = resolve_installation(db, settings)["selected"]
        assert selected["root"] == str(root)
        assert selected["port"] == 8193


def test_one_recipe_is_generated_for_h3_and_image(installed, monkeypatch):
    factory, settings, _, _ = installed
    calls = []
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report", lambda *a: {})
    monkeypatch.setattr(deployment, "generate_configuration",
                        lambda *a, **kw: calls.append(kw) or {"recipe_sha256": "a" * 64})
    task = {"project_id": None, "target": {"instance_id": "installed", "port": 8193},
            "request": {"capabilities": ["h3", "image"], "variant_id": "h3-ref2va-int8-convrot"}}
    result = setup_preparation._step_recipe(factory, settings, task)
    assert result["status"] == "DONE"
    assert len(calls) == 1
    assert calls[0]["capability"] == "h3"


def test_image_only_task_drops_unrelated_h3_variant(installed):
    factory, settings, _, _ = installed
    with factory() as db:
        task = setup_preparation.enqueue(db, project_id=None, capabilities=["image"],
                                         variant_id="ref2va-lowend-candidate", settings=settings)
    assert task["request"]["variant_id"] is None


def test_machine_validation_is_delegated_without_a_project(installed, monkeypatch):
    factory, settings, _, _ = installed
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report",
                        lambda *a: {"candidate": {"recipe_sha256": "a" * 64}})
    monkeypatch.setattr(setup_preparation, "_delegate_supervisor_validation",
                        lambda settings: {"status": "VALIDATING"})
    result = setup_preparation._step_validate(factory, settings, {"project_id": None})
    assert result["status"] == "PENDING"


def test_existing_managed_installation_still_asks_the_same_instance_ladder(installed, monkeypatch):
    from shine.comfyops.instances import instance_identity, instance_root

    factory, settings, _, _ = installed
    root = instance_root(settings, "continued-install", "local")
    root.mkdir(parents=True)
    with factory.begin() as db:
        db.add(ComfyInstance(id=instance_identity("local", 8193), sha256="0" * 64, status="STOPPED",
                             payload={"root": str(root), "name": "continued-install", "port": 8193,
                                      "managed": True, "node_versions": {}, "model_bindings": {}}))
    with factory() as db:
        task = setup_preparation.enqueue(db, project_id=None, capabilities=["h3"],
                                         installation_path=str(root), port=8193, settings=settings)
    assert "INSTANCE" in [row["id"] for row in task["steps"]]
    calls = []
    monkeypatch.setattr(deployment, "next_instance_plan",
                        lambda *a, **kw: (calls.append(kw) or (None, None)))
    assert setup_preparation._step_instance(factory, settings, task)["status"] == "DONE"
    assert len(calls) == 1
    assert calls[0]["instance_name"] == "continued-install"
    assert calls[0]["port"] == 8193


def test_explicit_probe_roots_use_the_settings_value(installed, monkeypatch):
    factory, settings, root, _ = installed
    monkeypatch.setattr(resource_discovery, "fixed_drives", lambda: [])
    with factory() as db:
        resource_discovery.start_scan(db, probe_roots=(str(root),))
        queue = resource_discovery.read_state(db)["scan"]["queue"]
    assert queue[0]["root"] == str(root)


@pytest.mark.parametrize("explicit_variant", [False, True])
def test_machine_card_inherits_the_same_bound_and_validated_contract(installed, monkeypatch, explicit_variant):
    from shine.comfyops import setup_summary
    from shine.comfyops.variant_binding import VariantBinding

    factory, settings, root, catalogs = installed
    with factory() as db:
        setup_reconcile.reconcile_installation(db, settings, path=str(root), capability="h3", adopt=True)
    variant = "h3-ref2va-int8-convrot"
    binding = VariantBinding(variant, catalogs["h3"]["catalog_id"], catalogs["h3"],
                             {"variant_id": variant, "acceptance_status": "SUPPORTED"},
                             ({"name": "machine-ref2va"},))
    original = setup_summary._catalog_for
    monkeypatch.setattr(setup_summary, "_catalog_for",
                        lambda key: catalogs["h3"] if key == "h3" else original(key))
    monkeypatch.setattr("shine.comfyops.variant_binding.load_binding", lambda *a, **k: binding)
    monkeypatch.setattr("shine.comfyops.runtime_configuration.service_evidence_status", lambda *a: {
        "status": "READY", "workflow_profiles": ["machine-ref2va"],
        "node_inventory_sha256": "n" * 64,
    })
    with factory() as db:
        selected = resolve_installation(db, settings)["selected"]
        report = setup_summary.capability_report(
            db, settings, "h3", installation=selected, project_id=None,
            variant_id=variant if explicit_variant else None, runtime_verified=True)
    assert report["facts"]["INSTANCE_BOUND"] is True
    assert report["facts"]["WORKFLOW_VALIDATED"] is True
    assert report["facts"]["RUNTIME_SERVICES_VERIFIED"] is True
    assert report["facts"]["RENDER_VERIFIED"] is False
    assert report["state"] == "READY"
    assert report["next_step"]["action"] == "TRIAL"
    assert report["two_step"]["run"]["state"] == "READY"

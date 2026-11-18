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


# --- R5 B1/B2/B8：物理安装身份稳定、端点可变、选择确定性 ---------------------


def _second_installation(temporary, catalogs):
    """A second physical ComfyUI with the same pinned bytes, on another root."""
    root = temporary / "另一套安装" / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("# synthetic entry\n", encoding="utf-8")
    (root / "comfyui_version.py").write_text('__version__ = "0.39.0"\n', encoding="utf-8")
    python = root.parent / "python_embeded" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"synthetic interpreter, never executed")
    for capability, family in (("h3", "diffusion_models"), ("image", "text_encoders")):
        data = ("synthetic weight for " + capability).encode()
        target = root / "models" / family / (capability + ".safetensors")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def test_port_change_keeps_one_installation_and_its_byte_evidence(installed):
    """INV-03/05：端口是端点，不是物理安装身份；换端口不产生第二条行、不丢证据。"""
    factory, settings, root, _ = installed
    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    assert first["adoption"]["status"] == "ADOPTED"
    with factory() as db:
        second = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8189)
    assert second["adoption"]["status"] in {"ADOPTED", "ALREADY_ADOPTED"}
    assert second["adoption"]["instance_id"] == first["adoption"]["instance_id"]
    with factory() as db:
        rows = db.scalars(select(ComfyInstance)).all()
        assert len(rows) == 1
        assert rows[0].payload["port"] == 8189
        adoption = rows[0].payload["adoption"]
        assert adoption["verified_files"], "换端口不得清空已核验字节证据"
        assert adoption["service_identity"]["port"] == 8189


def test_legacy_port_row_keeps_its_id_when_the_endpoint_moves(installed):
    """历史端口主键行不重算 ID：同一 root 在新端口被继承，证据保留。"""
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.instances import instance_identity

    factory, settings, root, _ = installed
    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    current_id = first["adoption"]["instance_id"]
    # 把行改造成历史形态：旧版本以端口 uuid 为主键写下的行。
    legacy_id = instance_identity("local", 8188)
    with factory.begin() as db:
        row = db.get(ComfyInstance, current_id)
        payload = dict(row.payload)
        db.delete(row)
        db.add(ComfyInstance(id=legacy_id, payload=payload,
                             sha256=graph_hash(payload), status="STOPPED"))
    with factory() as db:
        again = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8200)
    assert again["adoption"]["instance_id"] == legacy_id, "历史行必须保留原 ID"
    with factory() as db:
        assert db.get(ComfyInstance, legacy_id).payload["port"] == 8200
        assert len(db.scalars(select(ComfyInstance)).all()) == 1


def test_same_port_different_root_is_not_a_dead_end(installed):
    """INV-04：两套真实安装先后用同一端口，第二套必须可收编，第一套历史不动。"""
    factory, settings, root, _ = installed
    other = _second_installation(Path(settings.data_dir), None)
    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    with factory() as db:
        second = setup_reconcile.reconcile_installation(
            db, settings, path=str(other), capability="image", adopt=True, port=8188)
    assert second["adoption"]["status"] == "ADOPTED", second
    assert second["adoption"]["instance_id"] != first["adoption"]["instance_id"]
    with factory() as db:
        prior = db.get(ComfyInstance, first["adoption"]["instance_id"])
        assert prior.payload["root"] == str(root)
        assert prior.payload["port"] == 8188
        rows = db.scalars(select(ComfyInstance)).all()
        assert len(rows) == 2
    # 同端口两行（历史端口行 + 新稳定身份行）不得把配置生成卡成歧义死路：
    # 最近显式确认的安装优先，配方指向第二套物理 root。
    with factory() as db:
        result = deployment.generate_configuration(
            db, settings, None, capability="image", instance_name="shine-h3", port=8188)
    recipe = json.loads(Path(result["recipe"]).read_text(encoding="utf-8"))
    assert recipe["renderer"]["root"] == str(other)


def test_duplicate_legacy_rows_resolve_deterministically_to_the_chosen_port(installed):
    """INV-04/B8：历史同 root 多行时，选择跟随用户确认的端口，不随遍历顺序漂移。"""
    from shine.comfyops.instances import instance_identity

    factory, settings, root, _ = installed
    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    # 人为制造历史重复行（旧版本端口漂移的遗留）：同 root、不同端口。
    with factory.begin() as db:
        payload = dict(db.get(ComfyInstance, first["adoption"]["instance_id"]).payload)
        payload["port"] = 8189
        payload["adoption"] = {**payload["adoption"],
                               "service_identity": {**payload["adoption"]["service_identity"], "port": 8189}}
        from shine.comfy.workflow import graph_hash
        db.add(ComfyInstance(id=instance_identity("local", 8189), payload=payload,
                             sha256=graph_hash(payload), status="STOPPED"))
    with factory() as db:
        # 用户确认的是 8188；后插入的遗留行是 8189。last-wins 会选错。
        record_installation_choice(db, root=str(root), port=8188, actor="local-user")
        db.commit()
    for _ in range(3):
        with factory() as db:
            selected = resolve_installation(db, settings)["selected"]
        assert selected["port"] == 8188


def test_recipe_step_regenerates_when_the_endpoint_moved(installed, monkeypatch):
    """INV-09/B2：同一实例、端口已变 → 不得「沿用现有配置」跳过重生成。"""
    factory, settings, root, _ = installed
    with factory() as db:
        adoption = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    instance_id = adoption["adoption"]["instance_id"]
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report", lambda *a: {
        "status": "ACTIVE", "services_verified": True})
    from shine.models import AppSetting
    with factory.begin() as db:
        db.add(AppSetting(key="deployment-runtime-active", value={
            "version": 1, "identity": {"instance_id": instance_id,
                                       "comfyui_base_url": "http://127.0.0.1:8188"},
            "sha256": "x" * 64}))
    calls = []
    monkeypatch.setattr(deployment, "generate_configuration",
                        lambda *a, **kw: calls.append(kw) or {"recipe_sha256": "b" * 64})
    task = {"project_id": None,
            "target": {"instance_id": instance_id, "port": 8189, "name": "shine-h3"},
            "request": {"capabilities": ["h3"], "variant_id": "h3-ref2va-int8-convrot"}}
    result = setup_preparation._step_recipe(factory, settings, task)
    assert result["status"] == "DONE"
    assert len(calls) == 1, "端口变化必须重新生成运行配方"
    assert calls[0]["port"] == 8189


def test_recipe_step_regenerates_when_the_active_variant_is_not_the_requested_one(installed, monkeypatch):
    """INV-09/B2：激活的是候选变体而本次请求默认基线 → 不得静默沿用候选配方。"""
    factory, settings, root, _ = installed
    with factory() as db:
        adoption = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    instance_id = adoption["adoption"]["instance_id"]
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report", lambda *a: {
        "status": "ACTIVE", "services_verified": True})
    from shine.models import AppSetting
    with factory.begin() as db:
        db.add(AppSetting(key="deployment-runtime-active", value={
            "version": 1, "variant_id": "ref2va-lowend-candidate",
            "identity": {"instance_id": instance_id,
                         "comfyui_base_url": "http://127.0.0.1:8188"},
            "sha256": "x" * 64}))
    calls = []
    monkeypatch.setattr(deployment, "generate_configuration",
                        lambda *a, **kw: calls.append(kw) or {"recipe_sha256": "c" * 64})
    task = {"project_id": None,
            "target": {"instance_id": instance_id, "port": 8188, "name": "shine-h3"},
            "request": {"capabilities": ["h3"], "variant_id": "h3-ref2va-int8-convrot"}}
    result = setup_preparation._step_recipe(factory, settings, task)
    assert result["status"] == "DONE"
    assert len(calls) == 1, "变体变化必须重新生成运行配方"


def test_multiple_discovered_installations_ask_once_then_remember(installed):
    """R5 INV-04：多套可信候选只问一次；选定后重启/切页读取不漂移。"""
    factory, settings, root, _ = installed
    other = _second_installation(Path(settings.data_dir), None)
    with factory() as db:
        state = resource_discovery.read_state(db)
        state["resources"]["comfy_roots"] = [{"root": str(root)}, {"root": str(other)}]
        resource_discovery._save(db, state)
        db.commit()
    with factory() as db:
        resolved = resolve_installation(db, settings)
    assert resolved["choice_required"] is True
    assert resolved["selected"] is None
    with factory() as db:
        record_installation_choice(db, root=str(other), port=8189, actor="local-user")
        db.commit()
    for _ in range(3):
        with factory() as db:
            resolved = resolve_installation(db, settings)
        assert resolved["choice_required"] is False
        assert resolved["selected"]["root"] == str(other)
        assert resolved["selected"]["port"] == 8189


def test_port_change_invalidates_the_seal_but_not_the_byte_evidence(installed):
    """R5 INV-03/09：端点变化精准失效运行封条，权重核验证据不受牵连。"""
    from shine.comfyops.runtime_configuration import same_configuration

    factory, settings, root, _ = installed
    with factory() as db:
        adoption = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8188)
    base = {"instance_id": adoption["adoption"]["instance_id"], "instance_sha256": "s",
            "catalog_sha256": "c", "recipe_sha256": "r",
            "comfyui_base_url": "http://127.0.0.1:8188", "files": {"/x/main.py": "d"}}
    moved = {**base, "comfyui_base_url": "http://127.0.0.1:8189"}
    assert same_configuration(base, dict(base), settings=settings) is True
    assert same_configuration(base, moved, settings=settings) is False, "端点变化必须让旧封条失效"
    # 权重回执与端点无关：换端口后同一份字节证据仍然有效，且不重下。
    with factory() as db:
        again = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), capability="h3", adopt=True, port=8189)
    assert again["complete"] is True
    assert again["downloaded_bytes"] == 0
    assert again["adoption"]["instance_id"] == adoption["adoption"]["instance_id"]


def test_a_superseded_task_cannot_be_resurrected_by_a_late_worker_write(installed):
    """R5 INV-10：旧任务被取代/取消后，旧 Worker 的迟到回写不得覆盖新任务。"""
    factory, settings, _, _ = installed
    with factory() as db:
        first = setup_preparation.enqueue(db, project_id=None, capabilities=["image"], settings=settings)
    with factory() as db:
        second = setup_preparation.enqueue(db, project_id=None, capabilities=["h3"], settings=settings)
    assert second["task_id"] != first["task_id"]
    stale = dict(first)
    stale["status"] = "RUNNING"
    stale["message"] = "旧 Worker 的迟到回写"
    setup_preparation._save(factory, stale)
    with factory() as db:
        current = setup_preparation.read_task(db)
    assert current["task_id"] == second["task_id"]
    assert current["status"] == "QUEUED"
    assert current["message"] != "旧 Worker 的迟到回写"
    # 取消后的任务同样不得被迟到回写复活。
    with factory() as db:
        setup_preparation.cancel(db, actor="local-user")
    cancelled = dict(second)
    cancelled["status"] = "RUNNING"
    setup_preparation._save(factory, cancelled)
    with factory() as db:
        assert setup_preparation.read_task(db)["status"] == "CANCELLED"


def test_root_identity_ignores_windows_case_and_spaces(installed):
    """INV-13：同一物理 root 的大小写/空格变体只对应一个稳定身份。"""
    import os

    from shine.comfyops.instances import adopted_instance_identity

    factory, settings, root, _ = installed
    base = adopted_instance_identity(root)
    variant = str(root).upper() if os.name == "nt" else str(root)
    assert adopted_instance_identity(variant) == base
    assert adopted_instance_identity(str(root) + os.sep) == base


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

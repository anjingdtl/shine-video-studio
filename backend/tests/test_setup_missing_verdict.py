"""卡片的「① 文件」与「缺少 N 个文件」必须是同一个裁决（用户报障 2026-11-17）。

真机复现：H3 INT8 权重已在本机逐字节核验（凭据记录的是用户自己的目录），卡片
① 显示「文件已就绪并核验」，下面却报「缺少 6 个文件（25.1 GiB）」并给下载链接；
画面审片/语音核对的字节在隔离包目录（审片运行时真正读取的根），卡片却按
ComfyUI models 目录报「缺少 13/10 个文件（0 GiB）」。根因是 presence 只读
<选中实例>/models，把「已核验但尚未落位」与「本机真的没有」混成一份缺件表。

收敛：capability_report 对每个文件只分类一次——``missing_files`` 只列需要外取
的字节（显示与下载体积），``unplaced_files`` 列本机已有但需本地落位的字节
（执行者建 0 下载计划）；审片能力的 presence 与 advisory_review_readiness 同根。
整备停步时裸 ValueError 的具名码必须成为记录码，页面不得只显示类名。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from shine.comfyops import deployment, setup_preparation, setup_summary
from shine.comfyops.verification_receipts import save_receipt
from tests.conftest import make_sparse_file

pytestmark = pytest.mark.integration

PAYLOAD = b"\x89H3" + b"weight-bytes-" * 32
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _catalog() -> dict:
    return {
        "catalog_id": "test-catalog",
        "catalog_version": "1.0",
        "components": [{
            "component_id": "minimax-h3-test", "kind": "model", "revision": "r1",
            "status": "READY",
            "license_id": "test-license", "usage": "research",
            "destination_family": "diffusion_models",
            "files": [{"relative_path": "w.safetensors", "sha256": SHA,
                       "size": len(PAYLOAD), "url": "https://example.invalid/w",
                       "mirrors": []}],
        }],
    }


def _installation(root) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# test installation\n", encoding="utf-8")
    (root / "comfy").mkdir(exist_ok=True)
    (root / "models" / "diffusion_models").mkdir(parents=True, exist_ok=True)
    return {"root": str(root), "instance_id": None, "port": None}


def _report(factory, settings, installation, capability="h3") -> dict:
    with factory() as db:
        return setup_summary.capability_report(
            db, settings, capability, installation=installation, project_id=None)


def _cache(settings) -> Path:
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def test_verified_bytes_in_the_persons_own_folder_are_not_missing(tmp_path, api, monkeypatch):
    client, factory, settings = api
    catalog = _catalog()
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    own = tmp_path / "own-weights"
    own.mkdir()
    (own / "w.safetensors").write_bytes(PAYLOAD)
    with factory.begin() as db:
        save_receipt(db, variant_id=None, catalog_id=catalog["catalog_id"],
                     catalog_version=catalog["catalog_version"],
                     component=catalog["components"][0], root=own, directory=None,
                     file_paths={"w.safetensors": own / "w.safetensors"})

    report = _report(factory, settings, installation)
    assert report["facts"]["FILES_VERIFIED"] is True
    assert report["two_step"]["files"]["state"] == "READY"
    assert report["missing_files"] == [], "已核验的字节不得再被报成缺件"
    assert report["missing_bytes"] == 0
    assert [row["relative_path"] for row in report["unplaced_files"]] == ["w.safetensors"]
    assert report["facts"]["FILES_FOUND"] is True
    # ② 仍如实报未绑定：落位/绑定是执行者的事，不是①的缺件。
    assert report["two_step"]["run"]["state"] == "BLOCKED"


def test_cached_bytes_owe_placement_not_download(tmp_path, api, monkeypatch):
    client, factory, settings = api
    catalog = _catalog()
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    monkeypatch.setattr(setup_summary, "runtime_closure_gap",
                        lambda db, settings, installation: {
                            "instance_present": True, "missing_components": [],
                            "instance_ladder": []})
    installation = _installation(tmp_path / "ComfyUI")
    (_cache(settings) / SHA).write_bytes(PAYLOAD)

    report = _report(factory, settings, installation)
    assert report["two_step"]["files"]["state"] == "READY"
    assert report["missing_files"] == []
    assert report["missing_bytes"] == 0
    assert len(report["unplaced_files"]) == 1

    # 执行者仍要为落位建计划，但下载体积必须是 0（与卡片同一个数）。
    resp = client.post("/api/v1/setup/prepare-plan", json={"capabilities": ["h3"]})
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plans"][0]
    assert plan["components"] == ["minimax-h3-test"]
    assert plan["download_bytes"] == 0
    assert plan["needs_download"] is False

    (_cache(settings) / SHA).unlink()
    bare = _report(factory, settings, installation)
    assert [row["relative_path"] for row in bare["missing_files"]] == ["w.safetensors"]
    assert bare["missing_bytes"] == len(PAYLOAD)
    assert bare["two_step"]["files"]["state"] == "MISSING"
    assert bare["two_step"]["files"]["missing_count"] == 1
    resp = client.post("/api/v1/setup/prepare-plan", json={"capabilities": ["h3"]})
    plan = resp.json()["plans"][0]
    assert plan["download_bytes"] == len(PAYLOAD)
    assert plan["needs_download"] is True


def test_partial_machine_label_count_and_bytes_match_the_missing_list(tmp_path, api, monkeypatch):
    client, factory, settings = api
    catalog = _catalog()
    second = {
        "component_id": "minimax-h3-test2", "kind": "model", "revision": "r1",
        "status": "READY",
        "license_id": "test-license", "usage": "research",
        "destination_family": "diffusion_models",
        "files": [{"relative_path": "v.safetensors", "sha256": "b" * 64,
                   "size": 2048, "url": "https://example.invalid/v", "mirrors": []}],
    }
    catalog["components"].append(second)
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    (_cache(settings) / SHA).write_bytes(PAYLOAD)

    report = _report(factory, settings, installation)
    assert [row["relative_path"] for row in report["missing_files"]] == ["v.safetensors"]
    # unplaced 是「尚未落位到消费根」的全集（含本机还没有的 v）；
    # missing 是其中需要外取的子集。执行者按全集建计划，下载只算子集。
    assert [row["relative_path"] for row in report["unplaced_files"]] == ["w.safetensors", "v.safetensors"]
    assert report["missing_bytes"] == 2048
    files = report["two_step"]["files"]
    assert files["state"] == "MISSING"
    assert files["missing_count"] == len(report["missing_files"]) == 1
    assert "1 个文件" in files["label"]


def test_review_capability_reads_the_same_root_as_its_readiness(tmp_path, api):
    client, factory, settings = api
    installation = _installation(tmp_path / "ComfyUI")
    catalog = deployment.load_default_catalog()
    component = next(row for row in catalog["components"]
                     if row["component_id"] == "qwen3-vl-8b-fp8")
    files = component["files"]
    assert files, "审片清单必须钉住文件"
    store = Path(settings.comfyops_dir) / "isolated-packages" / component["component_id"]

    bare = _report(factory, settings, installation, capability="vlm")
    assert len(bare["missing_files"]) == len(files)
    assert bare["missing_bytes"] == sum(int(row["size"]) for row in files) > 0
    assert bare["two_step"]["files"]["state"] == "MISSING"
    assert bare["two_step"]["files"]["missing_count"] == len(bare["missing_files"])
    assert bare["two_step"]["run"]["reason"] == "文件未就绪，先补齐文件"

    # 稀疏文件只满足尺寸过滤（presence 的语义），不声称核验。
    for row in files:
        make_sparse_file(store / str(row["relative_path"]), int(row["size"]))
    readiness = deployment.advisory_review_readiness(settings)["VLM"]
    assert readiness["model_files_present"] == len(files)

    placed = _report(factory, settings, installation, capability="vlm")
    assert placed["missing_files"] == [], "审片字节已在消费根，不得再报缺件"
    assert placed["missing_bytes"] == 0
    assert placed["facts"]["FILES_FOUND"] is True
    assert placed["two_step"]["files"]["state"] == "UNVERIFIED"
    # 运行时包仍缺：② 的原因读同一份审片就绪裁决。
    assert placed["two_step"]["run"]["state"] == "BLOCKED"
    assert any("运行时包缺" in str(row.get("message") or "") for row in placed["advisories"])


def test_optional_catalog_files_the_graph_never_loads_are_not_missing(tmp_path, api, monkeypatch):
    """有作品时缺件表与事实层跟同一份「图真正加载的组件」。

    复现边缘：默认清单里的可选 LoRA 不在基线图里，台账权重（图必需组件）已
    核验 → ① 已核验；旧缺件表按全清单算 → 又报缺 LoRA。收敛后两者同集合。
    """
    from types import SimpleNamespace

    from shine.comfyops import variant_binding, variants

    client, factory, settings = api
    catalog = _catalog()
    catalog["components"].append({
        "component_id": "minimax-h3-optional-lora", "kind": "model", "revision": "r1",
        "status": "READY",
        "license_id": "test-license", "usage": "research",
        "destination_family": "loras",
        "files": [{"relative_path": "lora.safetensors", "sha256": "c" * 64,
                   "size": 4096, "url": "https://example.invalid/l", "mirrors": []}],
    })
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    binding = SimpleNamespace(
        catalog=catalog, variant_id="test-variant", catalog_id="test-catalog",
        profile_names=("p1",), manifest={"acceptance_status": "SUPPORTED"},
        profiles=({"graph": {"31": {"inputs": {"unet_name": "w.safetensors"}}}},),
    )
    monkeypatch.setattr(variant_binding, "load_binding", lambda vid, fresh=False: binding)
    monkeypatch.setattr(variants, "list_variant_manifests",
                        lambda: [{"variant_id": "test-variant", "acceptance_status": "SUPPORTED"}])
    installation = _installation(tmp_path / "ComfyUI")
    own = tmp_path / "own-weights"
    own.mkdir()
    (own / "w.safetensors").write_bytes(PAYLOAD)
    with factory.begin() as db:
        save_receipt(db, variant_id=None, catalog_id="test-catalog", catalog_version="1.0",
                     component=catalog["components"][0], root=own, directory=None,
                     file_paths={"w.safetensors": own / "w.safetensors"})
        db.commit()

    with factory() as db:
        report = setup_summary.capability_report(
            db, settings, "h3", installation=installation, project_id="proj-x")
    assert report["required_components"] == ["minimax-h3-test"]
    assert report["missing_files"] == [], "图不加载的可选文件不得进缺件表"
    assert report["two_step"]["files"]["state"] == "READY"
    assert [row["relative_path"] for row in report["unplaced_files"]] == ["w.safetensors"]


def test_named_code_in_a_bare_value_error_is_the_recorded_code(tmp_path, api, monkeypatch):
    client, factory, settings = api
    named = "INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED"
    assert setup_preparation._code_of(ValueError(named)) == named
    assert setup_preparation._code_of(ValueError("free text explanation")) == "ValueError"

    with factory.begin() as db:
        setup_preparation.enqueue(db, project_id=None, capabilities=["h3"], settings=settings)

    def _boom(factory, settings, task, step_id):
        raise ValueError(named)

    monkeypatch.setattr(setup_preparation, "_run_step", _boom)
    outcome = setup_preparation.advance(factory, settings, "worker-test")
    assert outcome.endswith(":failed")
    with factory() as db:
        task = setup_preparation.read_task(db)
    assert task["status"] == "FAILED"
    assert task["error"] == named, "页面要读到具名原因，不是异常类名"
    failed = next(row for row in task["steps"] if row["status"] == "FAILED")
    assert failed["code"] == named


def test_summary_verdict_and_verdict_endpoint_are_one_answer(tmp_path, api):
    """R5 INV-07：同一台机器，summary 内嵌裁决与 /setup/verdict 必须是同一答案。"""
    client, _factory, _settings = api
    summary = client.get("/api/v1/setup/summary").json()
    verdict = client.get("/api/v1/setup/verdict").json()
    assert summary["verdict"], "summary 必须内嵌唯一裁决"
    for key in ("mode", "label", "detail", "action", "code", "services_verified"):
        assert summary["verdict"][key] == verdict[key], key
    # 机器进度条的 100% 只允许出现在唯一裁决 READY/RUNNING 时。
    progress = summary["machine_progress"]
    if progress["percent"] == 100 and not progress["active"]:
        assert verdict["mode"] in {"READY", "RUNNING"}

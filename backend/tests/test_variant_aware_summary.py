"""「已有权重」必须跟随用户选定的变体，而不是默认那套。

机器上有基线权重时，选择低显存候选的用户不能被报成「已准备好」：
summary 的文件系统事实、reconcile 的核验清单都要跟着选定变体自己的目录走，
否则下载决策按基线做，候选权重永远不会被下载，开拍时才被门禁拦下。
不传变体参数的旧行为完全不变。

用两份小合成清单钉住「选择→清单」的接线；真实清单的逐字节核验由
test_variant_binding_and_gate 与安装链测试覆盖。
"""
# ruff: noqa: F811  (the `prep` fixture is shadowed by design)

from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException

from shine.comfyops import setup_reconcile, setup_summary
from shine.comfyops.variant_binding import VariantBinding
from tests.conftest import create_project
from tests.test_setup_one_stop_v41 import _managed_instance
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

pytestmark = pytest.mark.integration

LOWEND_VARIANT = "ref2va-lowend-candidate"
BASELINE_CATALOG = "synthetic-baseline-catalog"
LOWEND_CATALOG = "synthetic-lowend-catalog"
BASELINE_COMPONENT = "minimax-h3-synthetic"
LOWEND_COMPONENT = "minimax-h3-w6a8-synthetic"


def _synthetic_catalog(catalog_id: str, component_id: str, filename: str):
    # 两套权重必须字节不同：核验按内容匹配，相同载荷会让候选冒认基线的文件。
    payload = b"synthetic-weight-bytes:" + catalog_id.encode()
    catalog = {
        "catalog_id": catalog_id, "catalog_version": "1",
        "components": [{
            "component_id": component_id, "kind": "model", "revision": "r1",
            "destination_family": "diffusion_models", "status": "READY",
            "license_id": "TEST", "usage": "research", "dependencies": [],
            "files": [{"relative_path": filename, "sha256": hashlib.sha256(payload).hexdigest(),
                       "size": len(payload), "url": "https://example.invalid/file"}],
        }],
    }
    return catalog, payload


def _fake_binding(catalog: dict, variant_id: str) -> VariantBinding:
    return VariantBinding(
        variant_id=variant_id, catalog_id=catalog["catalog_id"], catalog=catalog,
        manifest={"variant_id": variant_id, "acceptance_status": "CANDIDATE",
                  "auto_download_allowed": False, "title": "合成候选"},
        profiles=(),
    )


@pytest.fixture()
def two_stacks(monkeypatch, tmp_path):
    """基线权重在盘上；候选是另一份清单、另一组文件。"""
    baseline, payload = _synthetic_catalog(BASELINE_CATALOG, BASELINE_COMPONENT, "baseline.safetensors")
    candidate, _ = _synthetic_catalog(LOWEND_CATALOG, LOWEND_COMPONENT, "candidate.safetensors")
    binding = _fake_binding(candidate, LOWEND_VARIANT)

    original_catalog_for = setup_summary._catalog_for

    def catalog_for(capability):
        if capability == "h3":
            return baseline
        return original_catalog_for(capability)

    def fake_load_binding(variant_id, *, catalog_id=None, fresh=False):
        if variant_id == LOWEND_VARIANT:
            return binding
        if variant_id == "h3-ref2va-int8-convrot":
            # 默认路径的台账层也读合成基线，保持旧行为可观察。
            return _fake_binding(baseline, variant_id)
        raise HTTPException(404, {"code": "VARIANT_MANIFEST_UNKNOWN", "variant_id": variant_id})

    monkeypatch.setattr(setup_summary, "_catalog_for", catalog_for)
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: baseline)
    monkeypatch.setattr("shine.comfyops.variant_binding.load_binding", fake_load_binding)
    return baseline, candidate, payload


def _write_baseline(root, payload):
    target = root / "models" / "diffusion_models" / "baseline.safetensors"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def test_summary_follows_the_selected_variant(prep, two_stacks):
    client, _, _ = prep
    _, _, payload = two_stacks
    project = create_project(client)
    root, _ = _managed_instance(prep)
    _write_baseline(root, payload)

    default = client.get(f"/api/v1/setup/summary?project_id={project}", headers=HEADERS).json()
    assert default["variant_id"] is None
    assert default["capabilities"]["h3"]["facts"]["FILES_FOUND"] is True

    chosen = client.get(
        f"/api/v1/setup/summary?project_id={project}&variant_id={LOWEND_VARIANT}", headers=HEADERS).json()
    assert chosen["variant_id"] == LOWEND_VARIANT
    h3 = chosen["capabilities"]["h3"]
    assert h3["facts"]["FILES_FOUND"] is False
    assert {row["component_id"] for row in h3["missing_files"]} == {LOWEND_COMPONENT}


def test_reconcile_verifies_the_selected_variants_own_catalog(prep, two_stacks):
    client, factory, settings = prep
    _, _, payload = two_stacks
    project = create_project(client)
    root, _ = _managed_instance(prep)
    _write_baseline(root, payload)

    with factory() as db:
        chosen = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), project_id=project, capability="h3",
            variant_id=LOWEND_VARIANT)
    assert chosen["catalog"] == LOWEND_CATALOG
    assert chosen["complete"] is False
    assert LOWEND_COMPONENT in chosen["missing_components"]

    # 不传变体：仍是默认清单，旧行为不变（基线文件核验通过并登记）。
    with factory() as db:
        default = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), project_id=project, capability="h3")
    assert default["catalog"] == BASELINE_CATALOG
    assert default["complete"] is True
    assert default["registered"] == [BASELINE_COMPONENT]


def test_an_unknown_variant_is_refused_by_name(prep):
    client, _, _ = prep
    project = create_project(client)
    response = client.get(
        f"/api/v1/setup/summary?project_id={project}&variant_id=does-not-exist", headers=HEADERS)
    assert response.status_code == 404
    assert str(response.json()["detail"]).startswith("VARIANT_MANIFEST_UNKNOWN")


def test_the_candidate_summary_is_still_read_only(prep, two_stacks):
    client, _, _ = prep
    _, _, payload = two_stacks
    project = create_project(client)
    root, _ = _managed_instance(prep)
    _write_baseline(root, payload)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    response = client.get(
        f"/api/v1/setup/summary?project_id={project}&variant_id={LOWEND_VARIANT}", headers=HEADERS)
    assert response.status_code == 200
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert before == after


def test_a_renamed_weight_verified_by_reconcile_is_not_downloaded_again(prep, two_stacks):
    """指定目录里与清单内容一致的改名权重，核验登记后整备不再计划下载。

    缺件判定必须消费同一份核验事实：reconcile 按尺寸+哈希找到改名文件并登记，
    capability_report 就不能再按文件名把它算成缺件、触发第二次下载。基础配置
    与后台下载步读的是同一个结论。
    """
    client, factory, settings = prep
    _, _, payload = two_stacks
    project = create_project(client)
    root, _ = _managed_instance(prep)
    renamed = root / "models" / "diffusion_models" / "我改名的权重.safetensors"
    renamed.parent.mkdir(parents=True, exist_ok=True)
    renamed.write_bytes(payload)

    with factory() as db:
        result = setup_reconcile.reconcile_installation(
            db, settings, path=str(root), project_id=project, capability="h3")
    assert result["complete"] is True
    assert result["registered"] == [BASELINE_COMPONENT]

    summary = client.get(f"/api/v1/setup/summary?project_id={project}", headers=HEADERS).json()
    h3 = summary["capabilities"]["h3"]
    assert h3["missing_files"] == [], "已核验登记的改名权重不得再被算成缺件"
    assert h3["missing_bytes"] == 0
    assert h3["facts"]["FILES_FOUND"] is True
    assert h3["facts"]["FILES_VERIFIED"] is True
"""PDD8 / 二次精修 / 极速4步 / RelayVSR 2X 的整备与下载能力（工作包5b）。

锁定四件事：
* RelayVSR X2 权重已用官方真实 SHA-256 钉住，清单可加载并归类到 relayvsr 能力；
* RelayVSR 整备只核验+下载权重，不拉 H3 受管配方/方案/核验链；
* 二次精修（upscale）与 PDD8 纯图方案进入默认清单并可解析登记；
* 极速4步走 NVFP4 候选清单（候选确认下载路径）。
"""

from __future__ import annotations

import re

from shine.comfyops import capability, deployment, relayvsr
from shine.comfyops.setup_preparation import _steps_for
from shine.comfyops.setup_summary import capability_report


def test_relayvsr_weights_are_pinned_with_real_digests():
    pins = relayvsr.relayvsr_weight_pins()
    assert len(pins) == 2
    for p in pins:
        assert re.fullmatch(r"[a-f0-9]{64}", p["sha256"]), p
        assert p["size"] > 0
        assert p["url"].startswith("https://huggingface.co/Comfy-Org/RelayVSR/resolve/")
        assert p["source_revision"] == relayvsr.RELAYVSR_HF_REVISION
    # readiness no longer refuses on "not pinned"; missing files still block.
    report = relayvsr.relayvsr_readiness()
    codes = {b["code"] for b in report["blockers"]}
    assert "RELAYVSR_WEIGHTS_NOT_PINNED" not in codes


def test_relayvsr_catalog_loads_and_is_classified():
    catalog = deployment.load_deployment_catalog("relayvsr-x2-windows-x64")
    assert capability.catalog_capability("relayvsr-x2-windows-x64", catalog) == "relayvsr"
    ids = {c["component_id"] for c in catalog["components"]}
    assert ids == {"relayvsr-x2-diffusion", "relayvsr-x2-flash-vae"}
    # catalog digests match the pinned upstream facts exactly.
    by_id = {c["component_id"]: c for c in catalog["components"]}
    pins = {p["filename"]: p for p in relayvsr.relayvsr_weight_pins()}
    dit = by_id["relayvsr-x2-diffusion"]["files"][0]
    assert dit["sha256"] == pins[relayvsr.RELAYVSR_X2_MODEL]["sha256"]
    assert dit["size"] == pins[relayvsr.RELAYVSR_X2_MODEL]["size"]


def test_relayvsr_capability_report_names_missing_weights():
    # No installation root: the weights are simply not present yet.
    report = capability_report(None, None, "relayvsr", installation=None, project_id=None)
    assert report["catalog_file"] == "relayvsr-x2-windows-x64"
    assert report["optional"] is True
    missing_ids = {row["component_id"] for row in report["missing_files"]}
    assert missing_ids == {"relayvsr-x2-diffusion", "relayvsr-x2-flash-vae"}


def test_relayvsr_preparation_only_reconciles_and_downloads():
    steps = _steps_for(["relayvsr"], verify_runtime=True)
    assert steps == ["RECONCILE", "DOWNLOAD"]
    # With the deep scan requested it leads, but still no H3 runtime chain.
    assert _steps_for(["relayvsr"], verify_runtime=True, deep_scan=True) == [
        "SCAN", "RECONCILE", "DOWNLOAD"]
    # H3 keeps its full chain.
    h3 = _steps_for(["h3"], verify_runtime=True)
    assert "INSTANCE" in h3 and "VALIDATE" in h3


def test_upscale_and_pdd8_profiles_are_registered_and_resolve():
    from shine.comfyops.variant_binding import profile_files

    catalog = deployment.load_default_catalog()
    names = catalog["workflow_profiles"]
    assert "local-h3-ref2va-pdd8-upscale-2img" in names  # 二次精修
    assert "local-h3-ref2va-pdd8-2img" in names          # PDD8 纯图
    resolved = {p["name"] for p in profile_files(names)}
    assert "local-h3-ref2va-pdd8-upscale-2img" in resolved
    # The refine workflow's checkpoint is the INT8 baseline already in the catalog.
    upscale = next(p for p in profile_files(names)
                   if p["name"] == "local-h3-ref2va-pdd8-upscale-2img")
    assert "int8_convrot" in upscale["checkpoint_id"]
    assert "MinimaxH3LatentUpscaler3DRefineHandoff" in upscale["required_nodes"]


def test_four_step_lives_in_the_nvfp4_candidate_catalog():
    catalog = deployment.load_deployment_catalog("h3-ref2va-nvfp4-lowend-windows-x64")
    assert "local-h3-ref2va-nvfp4-turbo-4step-2img" in catalog["workflow_profiles"]
    ids = {c["component_id"] for c in catalog["components"]}
    assert "minimax-h3-turbo-4step" in ids
    assert capability.catalog_capability("h3-ref2va-nvfp4-lowend-windows-x64", catalog) == "h3"
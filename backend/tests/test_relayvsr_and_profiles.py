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


def test_strategy_and_setup_read_one_relayvsr_verdict(api, tmp_path):
    """已下载就位的权重不得被策略矩阵报成「本机未就绪」（三原则反例）。"""
    from shine.comfyops.setup_summary import (
        record_installation_choice,
        relayvsr_machine_readiness,
    )
    from shine.strategy import (
        GenerationStrategy,
        UpscaleConfig,
        strategy_blockers,
        strategy_options,
    )

    client, factory, settings = api
    with factory.begin() as db:
        record_installation_choice(db, root=str(tmp_path), port=None, actor="local-user")
    strategy = GenerationStrategy(upscale=UpscaleConfig(enabled=True))

    with factory() as db:
        machine = relayvsr_machine_readiness(db, settings)
        assert machine["available"] is False
        options = strategy_options(db, settings)
        assert options["upscale"][0]["enabled"] is False
        codes = {b["code"] for b in strategy_blockers(strategy, db, settings)}
        assert "UPSCALE_BACKEND_UNAVAILABLE" in codes

    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    (models / "vae").mkdir(parents=True)
    (models / "diffusion_models" / relayvsr.RELAYVSR_X2_MODEL).write_bytes(b"x")
    (models / "vae" / relayvsr.RELAYVSR_X2_FLASH_VAE).write_bytes(b"x")

    with factory() as db:
        machine = relayvsr_machine_readiness(db, settings)
        assert machine["available"] is True
        options = strategy_options(db, settings)
        assert options["upscale"][0]["enabled"] is True
        assert strategy_blockers(strategy, db, settings) == []


def test_relayvsr_preparation_only_reconciles_and_downloads():
    steps = _steps_for(["relayvsr"], verify_runtime=True)
    assert steps == ["TOOL_REPAIR", "RECONCILE", "DOWNLOAD"]
    # With the deep scan requested it leads, but still no H3 runtime chain.
    assert _steps_for(["relayvsr"], verify_runtime=True, deep_scan=True) == [
        "SCAN", "TOOL_REPAIR", "RECONCILE", "DOWNLOAD"]
    # H3 keeps its full chain.
    h3 = _steps_for(["h3"], verify_runtime=True)
    assert "INSTANCE" in h3 and "VALIDATE" in h3


def test_upscale_profile_is_deferred_until_its_weight_is_pinned():
    from shine.comfyops.variant_binding import profile_files

    catalog = deployment.load_default_catalog()
    # 二次精修需要独立的 latent upscaler 权重，尚未钉住、节点未真机验证，
    # 因此刻意留在 deferred：文件可解析，但不进默认自动登记/下载集合。
    deferred = {e["name"] for e in catalog.get("deferred_workflow_profiles") or []}
    assert "local-h3-ref2va-pdd8-upscale-2img" in deferred
    assert "local-h3-ref2va-pdd8-upscale-2img" not in catalog["workflow_profiles"]
    resolved = {p["name"] for p in profile_files(["local-h3-ref2va-pdd8-upscale-2img"])}
    assert "local-h3-ref2va-pdd8-upscale-2img" in resolved
    upscale = next(p for p in profile_files(["local-h3-ref2va-pdd8-upscale-2img"]))
    assert "int8_convrot" in upscale["checkpoint_id"]
    assert "MinimaxH3LatentUpscaler3DRefineHandoff" in upscale["required_nodes"]


def test_four_step_lives_in_the_nvfp4_candidate_catalog():
    catalog = deployment.load_deployment_catalog("h3-ref2va-nvfp4-lowend-windows-x64")
    assert "local-h3-ref2va-nvfp4-turbo-4step-2img" in catalog["workflow_profiles"]
    ids = {c["component_id"] for c in catalog["components"]}
    assert "minimax-h3-turbo-4step" in ids
    assert capability.catalog_capability("h3-ref2va-nvfp4-lowend-windows-x64", catalog) == "h3"
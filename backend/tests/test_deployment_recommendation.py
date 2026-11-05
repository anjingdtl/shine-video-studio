"""Deployment recommendation: the rules must be honest, not optimistic.

A recommendation never promises that a combination runs, never adds VRAM across
cards, and never replaces a blocked H3 combination with another model.  These
tests pin those three behaviours plus the reuse estimate and the evidence level.
"""

from __future__ import annotations

import pytest

from shine.comfyops.recommendation import EVIDENCE_MEASURED, evaluate, recommend

CATALOG = {
    "catalog_id": "test-h3-combo",
    "catalog_version": "1.0",
    "platform": {
        "os": "windows-x64",
        "gpu_vendor": "nvidia",
        "min_vram_mb": 15360,
        "min_ram_mb": 32768,
        "min_disk_gb": 180,
    },
    "components": [
        {"component_id": "model", "files": [
            {"relative_path": "MiniMax-H3-Base.safetensors", "size": 1000},
            {"relative_path": "vae.safetensors", "size": 500},
        ]},
    ],
}


def _env(*, vram: int = 16303, ram: int = 65536, free_gb: float = 400.0, gpu_status: str = "OK") -> dict:
    return {
        "platform": {"system": "Windows", "machine": "AMD64"},
        "gpu": {"status": gpu_status, "name": "GeForce RTX 5080", "vram_mb": vram},
        "ram": {"status": "OK", "ram_mb": ram},
        "disk": {"status": "OK", "free_gb": free_gb},
    }


def test_a_supported_combination_reports_the_full_download_when_nothing_is_reusable(tmp_path):
    result = evaluate(CATALOG, _env(), reuse_root=tmp_path / "empty")
    assert result["status"] == "SUPPORTED"
    assert result["total_bytes"] == 1500
    assert result["reuse_bytes"] == 0
    assert result["download_bytes"] == 1500
    assert result["verified"] is False
    assert result["evidence"]["level"] == "estimated"


def test_insufficient_vram_blocks_and_says_the_real_numbers():
    result = evaluate(CATALOG, _env(vram=12288))
    assert result["status"] == "BLOCKED"
    codes = [row["code"] for row in result["blockers"]]
    assert codes == ["VRAM_INSUFFICIENT"]
    blocker = result["blockers"][0]
    assert blocker["required"] == 15360
    assert blocker["actual"] == 12288
    assert "不会自动相加" in blocker["message"]


def test_an_unidentified_gpu_is_a_warning_not_a_blocker():
    result = evaluate(CATALOG, _env(gpu_status="DETECTION_FAILED"))
    assert result["status"] == "SUPPORTED"
    assert [row["code"] for row in result["warnings"]] == ["GPU_NOT_IDENTIFIED"]
    assert result["blockers"] == []


def test_an_existing_file_of_the_same_size_reduces_the_download(tmp_path):
    reuse = tmp_path / "comfyops"
    (reuse / "models").mkdir(parents=True)
    (reuse / "models" / "vae.safetensors").write_bytes(b"x" * 500)
    result = evaluate(CATALOG, _env(), reuse_root=reuse)
    assert result["reuse_bytes"] == 500
    assert result["download_bytes"] == 1000


def test_a_measured_record_is_the_only_thing_that_counts_as_verified():
    validation = {"test-h3-combo": {
        "machine": "local", "verified_at": "2026-11-01T00:00:00Z",
        "peak_vram_mb": 15200, "peak_ram_mb": 24000, "duration_s": 96.5,
        "config_fingerprint": "f" * 64,
    }}
    result = evaluate(CATALOG, _env(), validation=validation)
    assert result["verified"] is True
    assert result["evidence"]["level"] == EVIDENCE_MEASURED
    assert result["evidence"]["peak_vram_mb"] == 15200


def test_low_disk_blocks_before_anything_is_downloaded():
    result = evaluate(CATALOG, _env(free_gb=40))
    assert [row["code"] for row in result["blockers"]] == ["DISK_INSUFFICIENT"]


def test_low_memory_blocks_and_reports_the_host_amount():
    result = evaluate(CATALOG, _env(ram=16000))
    assert [row["code"] for row in result["blockers"]] == ["RAM_INSUFFICIENT"]
    assert result["blockers"][0]["required"] == 31744


def test_a_32gb_class_machine_is_not_rejected_by_a_round_gib_floor():
    # 32 GB installed reports ~31.7 GB usable; the installer accepts that machine,
    # so the recommendation must not be the stricter of the two.
    assert evaluate(CATALOG, _env(ram=32418))["status"] == "SUPPORTED"
    assert evaluate(CATALOG, _env(ram=30000))["status"] == "BLOCKED"


def test_recommend_defaults_to_the_two_audited_product_choices():
    report = recommend(_env(), reuse_root=None)
    purposes = [row["purpose"] for row in report["options"]]
    assert purposes == ["h3_video", "image_prep"]
    for option in report["options"]:
        assert option["catalog_id"]
        assert option["status"] in {"SUPPORTED", "BLOCKED", "UNAVAILABLE"}
    assert report["host"]["vram_mb"] == 16303
    assert any("estimated" in note for note in report["notes"])


def test_an_unknown_purpose_is_refused_instead_of_ignored():
    with pytest.raises(ValueError, match="UNKNOWN_DEPLOYMENT_PURPOSE"):
        recommend(_env(), purposes=["h3_video", "some_other_video_model"])

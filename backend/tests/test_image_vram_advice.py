"""Image VRAM adaptation advice: one verdict, honest evidence, never a gate."""

from __future__ import annotations

import pytest

from shine.comfyops import capability, deployment
from shine.comfyops.recommendation import recommend


def _host_4070_super_env() -> dict:
    """Synthetic RTX 4070 Super (12 GB VRAM, 32 GB kit with firmware reservation)."""
    return {
        "platform": {"system": "Windows", "machine": "AMD64"},
        "gpus": [{
            "index": "0",
            "uuid": "GPU-4070S",
            "name": "NVIDIA GeForce RTX 4070 SUPER",
            "driver_version": "560.0",
            "vram_total_mb": 12282,
            "vram_used_mb": 400,
            "vram_free_mb": 11882,
            "selected": True,
        }],
        "gpu": {
            "status": "OK",
            "name": "NVIDIA GeForce RTX 4070 SUPER",
            "vram_mb": 12282,
            "vram_total_mb": 12282,
            "vram_free_mb": 11882,
        },
        "cuda": {"status": "OK", "compute_capability": "8.9"},
        "ram": {"status": "OK", "ram_mb": 31539, "ram_available_mb": 20000, "installed_mb": 32768},
        "commit": {"status": "OK", "commit_total_mb": 65536, "commit_available_mb": 40000,
                   "page_file_status": "PAGE_FILE_PRESENT"},
        "disk": {"status": "OK", "free_gb": 400.0},
    }


@pytest.mark.parametrize(
    ("total_mb", "free_mb", "expected_status", "expected_class", "expected_evidence", "expected_headroom"),
    [
        (24564, 23800, "fits", "24gb", "estimated", 24564 - 15473),
        (16303, 15400, "fits", "16gb", "measured_on_similar_hardware", 830),
        (15360, 14800, "fits", "16gb", "measured_on_similar_hardware", None),
        (12282, 11800, "fits", "12gb", "reported_by_users", None),
        (8188, 8000, "unverified", "under_12gb", "estimated", None),
        (6144, 6000, "cannot_start", "below_start", "estimated", None),
        (None, None, "unknown", None, None, None),
    ],
)
def test_vram_classes_evidence_and_non_blocking(
    total_mb, free_mb, expected_status, expected_class, expected_evidence, expected_headroom,
):
    advice = capability.image_vram_advice(total_mb, free_mb, supported_floor_mb=11776)
    assert advice["blocks"] is False
    assert advice["status"] == expected_status
    assert advice["headroom_mb"] == expected_headroom
    assert advice["evidence"]["level"] == expected_evidence
    assert "None" not in advice["headline"]
    assert len(advice["classes"]) == 5

    if expected_class is None:
        assert advice["class"] is None
        assert advice["gpu"] is None
    else:
        assert advice["class"]["id"] == expected_class
        assert advice["gpu"] == {"total_mb": total_mb, "free_mb": free_mb}

    if expected_class == "12gb":
        joined = "\n".join(advice["notes"])
        assert "换页到系统内存" in joined
        assert "32 GB 级内存" in joined
        assert "没有验证过" in joined


def test_host_probe_failure_or_missing_gpu_returns_unknown_without_raising(monkeypatch):
    monkeypatch.setattr("shine.comfyops.host_env.probe_gpus", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    broken = capability.image_vram_advice_for_host(None)
    assert broken["status"] == "unknown"
    assert broken["blocks"] is False
    assert broken["class"] is None

    monkeypatch.setattr("shine.comfyops.host_env.probe_gpus", lambda: [])
    empty = capability.image_vram_advice_for_host(None)
    assert empty["status"] == "unknown"
    assert empty["blocks"] is False
    assert empty["gpu"] is None


def test_runtime_verified_three_states():
    verified = capability.image_vram_advice(12282, 11800, supported_floor_mb=11776, runtime_verified=True)
    assert verified["evidence"]["level"] == "measured_on_this_machine"
    assert verified["notes"][0] == "本机已通过小样验证。"

    unverified = capability.image_vram_advice(12282, 11800, supported_floor_mb=11776, runtime_verified=False)
    assert unverified["evidence"]["level"] == "reported_by_users"
    assert any("本机还没有通过小样验证" in note for note in unverified["notes"])

    neutral = capability.image_vram_advice(12282, 11800, supported_floor_mb=11776, runtime_verified=None)
    assert neutral["evidence"]["level"] == "reported_by_users"
    assert not any("小样验证" in note for note in neutral["notes"])


def test_free_vram_advisories_by_class():
    # Below start floor (8000 MB) warns regardless of 16 GB or 12 GB total VRAM.
    low_16 = capability.image_vram_advice(16303, 6000, supported_floor_mb=11776)
    low_12 = capability.image_vram_advice(12282, 6000, supported_floor_mb=11776)
    assert any("低于渲染器启动所需的 8000 MB" in note for note in low_16["notes"])
    assert any("低于渲染器启动所需的 8000 MB" in note for note in low_12["notes"])

    # Between 8000 MB and 14580 MB warns on 16 GB / 24 GB, but not as a false conflict on 12 GB.
    crowded_16 = capability.image_vram_advice(16303, 12000, supported_floor_mb=11776)
    crowded_24 = capability.image_vram_advice(24564, 12000, supported_floor_mb=11776)
    crowded_12 = capability.image_vram_advice(12282, 11000, supported_floor_mb=11776)
    assert any("低于实测占用约 14580 MB" in note for note in crowded_16["notes"])
    assert any("低于实测占用约 14580 MB" in note for note in crowded_24["notes"])
    assert not any("低于实测占用约 14580 MB" in note for note in crowded_12["notes"])


def test_single_source_of_truth_and_measurement_consistency():
    catalog = deployment.load_deployment_catalog(deployment.IMAGE_CAPABILITY_CATALOG)
    floor_12gb = next(row["min_mb"] for row in capability.IMAGE_VRAM_CLASSES if row["id"] == "12gb")
    assert catalog["platform"]["min_vram_mb"] == 11776 == floor_12gb

    m = capability.IMAGE_VRAM_MEASUREMENT
    assert abs(m["model_mb"] - (m["peak_total_mb"] - m["desktop_mb"])) <= 1

    for vram in (24564, 16303, 12282, 8188, 6144, None):
        advice = capability.image_vram_advice(vram, vram, supported_floor_mb=floor_12gb)
        text = advice["headline"] + "\n" + "\n".join(advice["notes"])
        assert "调低分辨率" not in text
        assert "降低分辨率" not in text
        if vram in (24564, 16303):
            assert any("640 到 1024 像素" in note and "不改变显存占用" in note for note in advice["notes"])


def test_recommend_reports_4070_super_image_prep_supported_with_reported_advice():
    report = recommend(_host_4070_super_env(), reuse_root=None)
    by_purpose = {row["purpose"]: row for row in report["options"]}

    image_opt = by_purpose["image_prep"]
    assert image_opt["status"] == "SUPPORTED"
    assert image_opt["requirements"]["min_vram_mb"] == 11776
    assert image_opt["vram_advice"]["blocks"] is False
    assert image_opt["vram_advice"]["class"]["id"] == "12gb"
    assert image_opt["vram_advice"]["evidence"]["level"] == "reported_by_users"

    h3_opt = by_purpose["h3_video"]
    assert h3_opt["status"] == "BLOCKED"
    assert "vram_advice" not in h3_opt


def test_api_loras_and_image_readiness_expose_vram_advice(studio, monkeypatch):
    client, _factory, _tmp_path, _settings = studio
    monkeypatch.setattr(
        "shine.comfyops.host_env.probe_gpus",
        lambda: [{
            "index": "0",
            "uuid": "GPU-4070S",
            "name": "NVIDIA GeForce RTX 4070 SUPER",
            "vram_total_mb": 12282,
            "vram_free_mb": 11800,
            "selected": True,
        }],
    )

    image_loras = client.get("/api/v1/loras?base_model=qwen_image_21")
    assert image_loras.status_code == 200
    image_body = image_loras.json()
    assert image_body["vram_advice"]["blocks"] is False
    assert image_body["vram_advice"]["class"]["id"] == "12gb"
    assert image_body["vram_advice"]["evidence"]["level"] == "reported_by_users"

    h3_default = client.get("/api/v1/loras")
    h3_explicit = client.get("/api/v1/loras?base_model=minimax_h3")
    assert h3_default.status_code == 200 and "vram_advice" not in h3_default.json()
    assert h3_explicit.status_code == 200 and "vram_advice" not in h3_explicit.json()

    readiness = client.get("/api/v1/setup/image-readiness")
    assert readiness.status_code == 200
    readiness_body = readiness.json()
    advice = readiness_body["vram_advice"]
    assert advice["blocks"] is False
    assert advice["class"]["id"] == "12gb"
    assert any("本机还没有通过小样验证" in note for note in advice["notes"])

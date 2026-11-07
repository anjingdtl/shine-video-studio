"""The host facts and the one qualification service behind them.

These tests pin the rules that the repair plan calls out:

* an unidentified GPU, an unreadable RAM or commit, an unknown CUDA, a
  non-Windows host and a non-x64 host are never SUPPORTED;
* the memory of several cards is never added up;
* total memory and free memory are separate numbers;
* the same thresholds decide the new-machine blocker, the recommender, the
  install plan and the production start.
"""

from __future__ import annotations

import copy

import pytest

from shine.comfyops import host_env, variants
from shine.comfyops.variants import STATUS_BLOCKED, STATUS_SUPPORTED, STATUS_UNKNOWN, qualify

BASELINE = "h3-ref2va-int8-convrot"


def _requirements():
    return variants.variant_requirements(variants.load_variant_manifest(BASELINE))


def _host(**overrides) -> dict:
    host = {
        "platform": {"system": "Windows", "release": "11", "machine": "AMD64"},
        "gpus": [{
            "index": "0", "uuid": "GPU-ONE", "name": "Contract GPU", "driver_version": "1.0",
            "vram_total_mb": 16384, "vram_used_mb": 1024, "vram_free_mb": 15360, "selected": True,
        }],
        "gpu": {
            "status": "OK", "index": "0", "uuid": "GPU-ONE", "name": "Contract GPU",
            "driver_version": "1.0", "vram_mb": 16384, "vram_total_mb": 16384,
            "vram_used_mb": 1024, "vram_free_mb": 15360,
        },
        "cuda": {"status": "OK", "compute_capability": "8.6"},
        "ram": {"status": "OK", "ram_mb": 65536, "ram_available_mb": 32768},
        "commit": {"status": "OK", "commit_total_mb": 131072, "commit_available_mb": 65536,
                   "page_file_status": "PAGE_FILE_PRESENT"},
        "disk": {"status": "OK", "free_gb": 400.0},
        "evidence": {"probed_at": "2026-11-07T00:00:00Z"},
    }
    host.update(copy.deepcopy(overrides))
    return host


def test_a_measured_host_qualifies_for_the_audited_baseline():
    verdict = qualify(_requirements(), _host())
    assert verdict["status"] == STATUS_SUPPORTED
    assert verdict["blockers"] == []
    assert verdict["unknowns"] == []
    assert verdict["gpus_considered"] == 1


def test_an_unidentified_gpu_is_unknown_and_never_supported():
    host = _host(gpus=[], gpu={"status": "BLOCKED", "reason": "NVIDIA_DRIVER_UNAVAILABLE"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_UNKNOWN
    assert verdict["status"] != STATUS_SUPPORTED
    assert [row["code"] for row in verdict["unknowns"]] == ["GPU_NOT_IDENTIFIED"]


def test_unreadable_ram_is_unknown():
    host = _host(ram={"status": "BLOCKED", "reason": "RAM_UNAVAILABLE"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_UNKNOWN
    assert "RAM_UNKNOWN" in [row["code"] for row in verdict["unknowns"]]


def test_unreadable_commit_is_unknown():
    host = _host(commit={"status": "BLOCKED", "reason": "COMMIT_UNAVAILABLE"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_UNKNOWN
    assert "COMMIT_UNKNOWN" in [row["code"] for row in verdict["unknowns"]]


def test_an_unknown_cuda_is_unknown():
    host = _host(cuda={"status": "UNKNOWN", "reason": "CUDA_COMPUTE_CAPABILITY_UNKNOWN"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_UNKNOWN
    assert "CUDA_UNKNOWN" in [row["code"] for row in verdict["unknowns"]]


def test_a_non_windows_host_is_blocked():
    host = _host(platform={"system": "Linux", "machine": "x86_64"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_BLOCKED
    assert "OS_UNSUPPORTED" in [row["code"] for row in verdict["blockers"]]


def test_a_non_x64_host_is_blocked():
    host = _host(platform={"system": "Windows", "machine": "ARM64"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_BLOCKED
    assert "ARCH_UNSUPPORTED" in [row["code"] for row in verdict["blockers"]]


@pytest.mark.parametrize("vram_mb,expected", [(12288, STATUS_BLOCKED), (15360, STATUS_SUPPORTED),
                                               (24576, STATUS_SUPPORTED)])
def test_the_vram_matrix_is_consistent_and_explainable(vram_mb, expected):
    host = _host()
    host["gpus"][0]["vram_total_mb"] = vram_mb
    host["gpu"]["vram_total_mb"] = vram_mb
    host["gpu"]["vram_mb"] = vram_mb
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == expected
    if expected == STATUS_BLOCKED:
        assert verdict["blockers"][0]["code"] == "VRAM_INSUFFICIENT"
        assert verdict["blockers"][0]["required"] == 15360
        assert verdict["blockers"][0]["actual"] == vram_mb
    else:
        assert verdict["blockers"] == []


def test_a_32gb_class_machine_is_not_rejected_by_a_round_gib_floor():
    host = _host(ram={"status": "OK", "ram_mb": 32418, "ram_available_mb": 20000})
    assert qualify(_requirements(), host)["status"] == STATUS_SUPPORTED
    host = _host(ram={"status": "OK", "ram_mb": 30000, "ram_available_mb": 20000})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_BLOCKED
    assert verdict["blockers"][0]["code"] == "RAM_INSUFFICIENT"
    assert verdict["blockers"][0]["required"] == 31744


def test_a_low_commit_limit_is_blocked_and_says_the_numbers():
    host = _host(commit={"status": "OK", "commit_total_mb": 20000, "commit_available_mb": 20000,
                         "page_file_status": "PAGE_FILE_DISABLED_OR_TINY"})
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_BLOCKED
    blocker = verdict["blockers"][0]
    assert blocker["code"] == "COMMIT_INSUFFICIENT"
    assert blocker["required"] == 40960
    assert blocker["actual"] == 20000


def test_two_cards_are_never_added_up():
    host = _host()
    host["gpus"] = [
        {"index": "0", "uuid": "GPU-ONE", "name": "Small", "driver_version": "1.0",
         "vram_total_mb": 8192, "vram_used_mb": 0, "vram_free_mb": 8192, "selected": True},
        {"index": "1", "uuid": "GPU-TWO", "name": "Big", "driver_version": "1.0",
         "vram_total_mb": 24576, "vram_used_mb": 0, "vram_free_mb": 24576, "selected": False},
    ]
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_BLOCKED
    assert verdict["blockers"][0]["code"] == "VRAM_INSUFFICIENT"
    assert verdict["blockers"][0]["actual"] == 8192
    assert verdict["gpus_considered"] == 1


def test_the_user_selected_card_is_the_one_that_counts():
    host = _host()
    host["gpus"] = [
        {"index": "0", "uuid": "GPU-ONE", "name": "Small", "driver_version": "1.0",
         "vram_total_mb": 8192, "vram_used_mb": 0, "vram_free_mb": 8192, "selected": False},
        {"index": "1", "uuid": "GPU-TWO", "name": "Big", "driver_version": "1.0",
         "vram_total_mb": 24576, "vram_used_mb": 0, "vram_free_mb": 24576, "selected": True},
    ]
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_SUPPORTED
    assert verdict["selected_gpu"]["uuid"] == "GPU-TWO"


def test_a_busy_card_is_a_warning_and_not_a_blocker():
    host = _host()
    host["gpus"][0]["vram_free_mb"] = 4096
    host["gpu"]["vram_free_mb"] = 4096
    verdict = qualify(_requirements(), host)
    assert verdict["status"] == STATUS_SUPPORTED
    assert "FREE_VRAM_BELOW_FLOOR" in [row["code"] for row in verdict["warnings"]]


def test_the_probe_reports_every_card_with_its_own_numbers():
    cards = [
        {"index": "0", "uuid": "GPU-ONE", "name": "A", "driver_version": "1",
         "vram_total_mb": 8192, "vram_used_mb": 100, "vram_free_mb": 8092},
        {"index": "1", "uuid": "GPU-TWO", "name": "B", "driver_version": "1",
         "vram_total_mb": 24576, "vram_used_mb": 200, "vram_free_mb": 24376},
    ]
    output = ("0, GPU-ONE, NVIDIA A, 1, 8192 MiB, 100 MiB, 8092 MiB\n"
              "1, GPU-TWO, NVIDIA B, 1, 24576 MiB, 200 MiB, 24376 MiB")
    probed = host_env.probe_gpus(runner=lambda probe: (True, output))
    assert len(probed) == 2
    assert [card["uuid"] for card in probed] == ["GPU-ONE", "GPU-TWO"]
    assert probed[1]["vram_total_mb"] == 24576
    assert probed[1]["vram_free_mb"] == 24376
    chosen = host_env.select_gpu(probed)
    assert chosen["uuid"] == "GPU-ONE"
    assert sum(card["vram_total_mb"] for card in probed) == 32768
    assert cards  # the two cards are reported separately, never as one number


def test_the_blockers_and_the_recommender_use_the_same_thresholds():
    requirements = host_env.default_requirements()
    assert requirements["min_vram_mb"] == 15360
    assert requirements["min_ram_mb"] == 32768
    assert requirements["min_commit_mb"] == 40960
    manifest = variants.load_variant_manifest(BASELINE)
    assert variants.variant_requirements(manifest)["min_vram_mb"] == requirements["min_vram_mb"]
    assert variants.variant_requirements(manifest)["min_commit_mb"] == requirements["min_commit_mb"]

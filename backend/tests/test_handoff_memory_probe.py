"""Memory admission requires measured RAM and the explicitly selected GPU."""

import pytest

from shine.comfyops.handoff_memory import evaluate_readiness, parse_nvidia_smi


def test_nvidia_probe_requires_unique_gpu_and_valid_memory():
    output = "0, GPU-abc, 16303, 15000\n1, GPU-def, 8000, 6000\n"
    assert parse_nvidia_smi(output, "GPU-abc") == {"uuid": "GPU-abc", "total_mb": 16303, "free_mb": 15000}
    with pytest.raises(ValueError, match="GPU_IDENTITY"):
        parse_nvidia_smi(output, "GPU-missing")
    with pytest.raises(ValueError, match="GPU_IDENTITY"):
        parse_nvidia_smi("0, GPU-abc, 100, 80\n1, GPU-abc, 100, 80\n", "GPU-abc")
    for invalid in ("0, GPU-abc, 100, 101", "0, GPU-abc, 100, -1", "0, GPU-abc, N/A, 1"):
        with pytest.raises(ValueError, match="GPU_MEMORY"):
            parse_nvidia_smi(invalid, "GPU-abc")


def test_admission_rejects_unknown_or_insufficient_physical_memory():
    gpu = {"uuid": "GPU-abc", "total_mb": 16303, "free_mb": 15000}
    result = evaluate_readiness(available_ram_bytes=12 * 1024**3, gpu=gpu,
                                min_ram_mb=8000, min_vram_mb=14000, expected_gpu="GPU-abc")
    assert result["ready"] is True
    for kwargs in (
        {"available_ram_bytes": None, "gpu": gpu},
        {"available_ram_bytes": 4 * 1024**3, "gpu": gpu},
        {"available_ram_bytes": 12 * 1024**3, "gpu": {**gpu, "free_mb": 500}},
        {"available_ram_bytes": 12 * 1024**3, "gpu": {**gpu, "uuid": "other"}},
    ):
        assert not evaluate_readiness(**kwargs, min_ram_mb=8000, min_vram_mb=14000,
                                      expected_gpu="GPU-abc")["ready"]

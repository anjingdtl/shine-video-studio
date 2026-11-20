"""Read-only hardware telemetry contract for CPU, memory and GPU sensors."""

from __future__ import annotations

from shine.hardware_telemetry import (
    parse_nvidia_smi_telemetry,
    parse_wmi_cpu_telemetry,
    read_hardware_telemetry,
    select_windows_energy_meter_power_w,
    select_windows_thermal_zone_celsius,
)


def test_parse_nvidia_smi_telemetry_handles_real_and_na_fields():
    sample = "\n".join([
        "0, NVIDIA GeForce RTX 5080, 12, 3491, 16303, 42.39, 76.64, 360.00",
        "1, NVIDIA GeForce RTX 4060, [N/A], 1024, 8192, 39, [Not Supported], [N/A]",
    ])
    parsed = parse_nvidia_smi_telemetry(sample)
    assert len(parsed) == 2
    assert parsed[0] == {
        "index": 0,
        "name": "NVIDIA GeForce RTX 5080",
        "utilization_pct": 12.0,
        "memory_used_mb": 3491,
        "memory_total_mb": 16303,
        "memory_pct": 21.4,
        "temperature_c": 42.4,
        "power_w": 76.6,
        "power_limit_w": 360.0,
    }
    assert parsed[1]["index"] == 1
    assert parsed[1]["utilization_pct"] is None
    assert parsed[1]["temperature_c"] == 39.0
    assert parsed[1]["power_w"] is None
    assert parsed[1]["power_limit_w"] is None
    assert parsed[1]["memory_pct"] == 12.5


def test_read_hardware_telemetry_degrades_gracefully_without_nvidia_smi():
    snap = read_hardware_telemetry(nvidia_smi="nonexistent-nvidia-smi-binary-xyz")
    assert snap["gpus"] == []
    assert snap["gpu_error"] == "NVIDIA_SMI_UNAVAILABLE"
    assert snap["memory"]["total_mb"] is None or snap["memory"]["total_mb"] > 0
    assert "utilization_pct" in snap["cpu"]
    assert "temperature_c" in snap["cpu"]
    assert "power_w" in snap["cpu"]


def test_hardware_telemetry_endpoint_returns_expected_shape(api, monkeypatch):
    client, _factory, _settings = api
    fake_payload = {
        "checked_at": "2026-11-20T16:00:00+00:00",
        "cpu": {"utilization_pct": 18.5, "temperature_c": None, "power_w": None},
        "memory": {"utilization_pct": 60.0, "used_mb": 19456, "total_mb": 32426},
        "gpus": [{
            "index": 0,
            "name": "NVIDIA GeForce RTX 5080",
            "utilization_pct": 24.0,
            "memory_used_mb": 4096,
            "memory_total_mb": 16303,
            "memory_pct": 25.1,
            "temperature_c": 45.0,
            "power_w": 88.5,
            "power_limit_w": 360.0,
        }],
        "gpu_error": None,
    }
    monkeypatch.setattr("shine.hardware_telemetry.read_hardware_telemetry", lambda: fake_payload)

    response = client.get("/api/v1/setup/hardware-telemetry")
    assert response.status_code == 200, response.text
    assert response.json() == fake_payload


def test_windows_energy_meter_prefers_pkg_over_cores():
    samples = [
        ("RAPL_Package0_Core0_CORE", 1800.0),
        ("RAPL_Package0_Core1_CORE", 900.0),
        ("RAPL_Package0_PKG", 42350.0),
        ("_Total", 0.0),
    ]
    assert select_windows_energy_meter_power_w(samples) == 42.4
    assert select_windows_energy_meter_power_w([("_Total", 0.0)]) is None


def test_windows_thermal_zone_and_wmi_parsers():
    assert select_windows_thermal_zone_celsius([("_TZ.TZ0", 3215.0)], high_precision=True) == 48.4
    assert select_windows_thermal_zone_celsius([("_TZ.TZ0", 315.0)], high_precision=False) == 41.9

    rows = [
        {"Name": "GPU Core", "SensorType": "Temperature", "Identifier": "/gpu-nvidia/0/temperature/0", "Value": 55.0},
        {
            "Name": "Core (Tctl/Tdie)",
            "SensorType": "Temperature",
            "Identifier": "/amdcpu/0/temperature/2",
            "Value": 61.3,
        },
        {"Name": "Package", "SensorType": "Power", "Identifier": "/amdcpu/0/power/0", "Value": 78.4},
    ]
    assert parse_wmi_cpu_telemetry(rows) == (61.3, 78.4)
    assert parse_wmi_cpu_telemetry([{"CurrentTemperature": 3182}]) == (45.1, None)

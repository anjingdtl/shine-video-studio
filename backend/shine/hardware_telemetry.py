"""Read-only host hardware telemetry (CPU, memory, GPU load/power/temperature).

Never fabricates sensor values: metrics genuinely unavailable from host OS
counters or open WMI sensor providers return ``None``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CPU_LOCK = threading.Lock()
_LAST_CPU_SAMPLE: tuple[int, int] | None = None
_LAST_CPU_PCT: float | None = None

_PDH_LOCK = threading.Lock()
_PDH_READER: _WindowsPdhReader | None = None

_WMI_LOCK = threading.Lock()
_WMI_LAST_MISS_MONO: float | None = None
_WMI_MISS_TTL_S = 60.0

_RAPL_LOCK = threading.Lock()
_LAST_RAPL_SAMPLE: tuple[float, int] | None = None
_LAST_RAPL_POWER_W: float | None = None

_MISSING_SENTINELS = {"", "n/a", "[n/a]", "na", "[not supported]", "not supported", "err!"}


def _parse_float(raw: str) -> float | None:
    text = raw.strip()
    if text.lower() in _MISSING_SENTINELS:
        return None
    try:
        return round(float(text), 1)
    except ValueError:
        return None


def _parse_int(raw: str) -> int | None:
    value = _parse_float(raw)
    return int(round(value)) if value is not None else None


def parse_nvidia_smi_telemetry(output: str) -> list[dict[str, Any]]:
    """Parse ``nvidia-smi`` CSV rows (index, name, gpu util, memory, temp, power)."""
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 8:
            continue
        idx = _parse_int(parts[0])
        if idx is None:
            continue
        mem_used = _parse_int(parts[3])
        mem_total = _parse_int(parts[4])
        mem_pct = (
            round(mem_used / mem_total * 100.0, 1)
            if mem_used is not None and mem_total is not None and mem_total > 0
            else None
        )
        gpus.append({
            "index": idx,
            "name": parts[1] or f"GPU {idx}",
            "utilization_pct": _parse_float(parts[2]),
            "memory_used_mb": mem_used,
            "memory_total_mb": mem_total,
            "memory_pct": mem_pct,
            "temperature_c": _parse_float(parts[5]),
            "power_w": _parse_float(parts[6]),
            "power_limit_w": _parse_float(parts[7]),
        })
    return gpus


def _read_windows_cpu_totals() -> tuple[int, int] | None:
    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    def _to_int(ft: _FileTime) -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    idle, kern, user = _FileTime(), _FileTime(), _FileTime()
    if not kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    # GetSystemTimes kernel includes idle time.
    total = _to_int(kern) + _to_int(user)
    return _to_int(idle), total


def _read_linux_cpu_totals() -> tuple[int, int] | None:
    try:
        first = Path("/proc/stat").read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    tokens = first.split()
    if len(tokens) < 5 or tokens[0] != "cpu":
        return None
    try:
        values = [int(item) for item in tokens[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _sample_cpu_counters() -> tuple[int, int] | None:
    return _read_windows_cpu_totals() if os.name == "nt" else _read_linux_cpu_totals()


def _read_cpu_utilization() -> float | None:
    global _LAST_CPU_SAMPLE, _LAST_CPU_PCT
    with _CPU_LOCK:
        prev = _LAST_CPU_SAMPLE
        now = _sample_cpu_counters()
        if now is None:
            return _LAST_CPU_PCT
        if prev is None:
            time.sleep(0.06)
            prev = now
            now = _sample_cpu_counters() or now
        delta_total = now[1] - prev[1]
        delta_idle = now[0] - prev[0]
        if delta_total > 0:
            pct = round(max(0.0, min(100.0, (delta_total - delta_idle) / delta_total * 100.0)), 1)
            _LAST_CPU_SAMPLE = now
            _LAST_CPU_PCT = pct
            return pct
        _LAST_CPU_SAMPLE = now
        return _LAST_CPU_PCT


def _read_linux_cpu_temperature() -> float | None:
    thermal_root = Path("/sys/class/thermal")
    if not thermal_root.is_dir():
        return None
    temps: list[float] = []
    for zone in sorted(thermal_root.glob("thermal_zone*/temp")):
        try:
            raw = int(zone.read_text(encoding="utf-8", errors="ignore").strip())
        except (OSError, ValueError):
            continue
        celsius = raw / 1000.0 if raw > 200 else float(raw)
        if 0.0 < celsius < 130.0:
            temps.append(celsius)
    return round(max(temps), 1) if temps else None


def _read_linux_cpu_power_w() -> float | None:
    global _LAST_RAPL_SAMPLE, _LAST_RAPL_POWER_W
    rapl_root = Path("/sys/class/powercap")
    if not rapl_root.is_dir():
        return None
    package_files = sorted(rapl_root.glob("intel-rapl:[0-9]*/energy_uj"))
    if not package_files:
        return None
    total_uj = 0
    for item in package_files:
        try:
            total_uj += int(item.read_text(encoding="utf-8", errors="ignore").strip())
        except (OSError, ValueError):
            return None
    now = time.monotonic()
    with _RAPL_LOCK:
        prev = _LAST_RAPL_SAMPLE
        _LAST_RAPL_SAMPLE = (now, total_uj)
        if prev is None:
            return _LAST_RAPL_POWER_W
        dt = now - prev[0]
        de = total_uj - prev[1]
        if dt > 0.05 and de >= 0:
            _LAST_RAPL_POWER_W = round((de / 1_000_000.0) / dt, 1)
        return _LAST_RAPL_POWER_W


def select_windows_energy_meter_power_w(samples: list[tuple[str, float]]) -> float | None:
    """Extract total CPU package power (Watts) from Windows Energy Meter samples (milliwatts)."""
    pkg_mw: list[float] = []
    core_mw: list[float] = []
    other_mw: list[float] = []
    for name, value_mw in samples:
        key = name.strip()
        lower = key.lower()
        if not key or lower == "_total" or value_mw < 0:
            continue
        if lower.endswith("_pkg") or ("package" in lower and "core" not in lower):
            pkg_mw.append(value_mw)
        elif lower.endswith("_core") or re.search(r"\bcore\d+\b", lower):
            core_mw.append(value_mw)
        else:
            other_mw.append(value_mw)
    chosen = pkg_mw or core_mw or other_mw
    if not chosen:
        return None
    return round(sum(chosen) / 1000.0, 1)


def select_windows_thermal_zone_celsius(
    samples: list[tuple[str, float]],
    *,
    high_precision: bool,
) -> float | None:
    """Convert Windows Thermal Zone Information PDH samples into max valid Celsius."""
    temps: list[float] = []
    for _name, raw in samples:
        celsius = (raw / 10.0 - 273.15) if high_precision else (raw - 273.15)
        if 0.0 < celsius < 130.0:
            temps.append(celsius)
    return round(max(temps), 1) if temps else None


def _pick_named_sensor_value(
    rows: list[dict[str, Any]],
    sensor_type: str,
    priorities: tuple[re.Pattern[str], ...],
    *,
    min_val: float,
    max_val: float,
) -> float | None:
    candidates: list[tuple[int, float]] = []
    for row in rows:
        if str(row.get("SensorType") or "").strip().lower() != sensor_type.lower():
            continue
        ident = str(row.get("Identifier") or "").lower()
        # Restrict to CPU sensor nodes (avoid matching GPU/motherboard/HDD sensors).
        if ident and not any(tok in ident for tok in ("cpu", "amdcpu", "intelcpu", "processor")):
            continue
        name = str(row.get("Name") or "").strip()
        try:
            val = float(row.get("Value"))
        except (TypeError, ValueError):
            continue
        if not (min_val < val < max_val):
            continue
        for rank, pat in enumerate(priorities):
            if pat.search(name):
                candidates.append((rank, val))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -item[1]))
    return round(candidates[0][1], 1)


_TEMP_PRIORITIES = (
    re.compile(r"tctl\s*/\s*tdie|cpu\s*package|package", re.IGNORECASE),
    re.compile(r"core\s*\(tctl|core\s*max|core\s*average|ccds?\s*max", re.IGNORECASE),
    re.compile(r"^cpu$|cpu\s*core", re.IGNORECASE),
)
_POWER_PRIORITIES = (
    re.compile(r"cpu\s*package|package", re.IGNORECASE),
    re.compile(r"cpu\s*cores?|cores?", re.IGNORECASE),
)


def parse_wmi_cpu_telemetry(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Parse LibreHardwareMonitor / OpenHardwareMonitor / ACPI WMI sensor rows into (temp_c, power_w)."""
    temp_c = _pick_named_sensor_value(
        rows, "Temperature", _TEMP_PRIORITIES, min_val=0.0, max_val=130.0
    )
    power_w = _pick_named_sensor_value(
        rows, "Power", _POWER_PRIORITIES, min_val=0.0, max_val=1500.0
    )
    if temp_c is None:
        acpi_temps: list[float] = []
        for row in rows:
            raw = row.get("CurrentTemperature")
            if raw is None:
                continue
            try:
                celsius = float(raw) / 10.0 - 273.15
            except (TypeError, ValueError):
                continue
            if 0.0 < celsius < 130.0:
                acpi_temps.append(celsius)
        if acpi_temps:
            temp_c = round(max(acpi_temps), 1)
    return temp_c, power_w


class _WindowsPdhReader:
    """Persistent PDH query reading Energy Meter (mW) and Thermal Zones without child processes."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes

        class _Union(ctypes.Union):
            _fields_ = [
                ("longValue", ctypes.c_long),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
            ]

        class _FmtVal(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("u", _Union)]

        class _FmtItem(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FmtVal)]

        self._item_type = _FmtItem
        self._pdh = ctypes.WinDLL("pdh")
        self._query = ctypes.c_void_p()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        self._energy_counter = self._add_counter(r"\Energy Meter(*)\Power")
        self._therm_hp_counter = self._add_counter(r"\Thermal Zone Information(*)\High Precision Temperature")
        self._therm_counter = self._add_counter(r"\Thermal Zone Information(*)\Temperature")
        if self._energy_counter is None and self._therm_hp_counter is None and self._therm_counter is None:
            self.close()
            raise OSError("No supported PDH hardware counters available")
        # Rate-based Energy Meter power counters need two collect calls before formatting cleanly.
        self._pdh.PdhCollectQueryData(self._query)
        time.sleep(0.06)

    def _add_counter(self, path: str) -> Any:
        handle = self._ctypes.c_void_p()
        status = self._pdh.PdhAddEnglishCounterW(self._query, path, 0, self._ctypes.byref(handle))
        return handle if status == 0 else None

    def _read_array(self, counter: Any) -> list[tuple[str, float]]:
        if counter is None:
            return []
        ctypes = self._ctypes
        wintypes = self._wintypes
        buf_size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        pdh_more_data = 0x800007D2
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, 0x00000200, ctypes.byref(buf_size), ctypes.byref(count), None
        )
        if (status & 0xFFFFFFFF) != pdh_more_data or buf_size.value == 0:
            return []
        buf = (ctypes.c_byte * buf_size.value)()
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter,
            0x00000200,
            ctypes.byref(buf_size),
            ctypes.byref(count),
            ctypes.cast(buf, ctypes.POINTER(self._item_type)),
        )
        if status != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(self._item_type))
        rows: list[tuple[str, float]] = []
        for idx in range(count.value):
            item = items[idx]
            if item.FmtValue.CStatus == 0 and item.szName:
                rows.append((str(item.szName), float(item.FmtValue.u.doubleValue)))
        return rows

    def sample(self) -> tuple[float | None, float | None]:
        status = self._pdh.PdhCollectQueryData(self._query)
        if status != 0:
            raise OSError(f"PdhCollectQueryData failed: {status:#x}")
        power_w = select_windows_energy_meter_power_w(self._read_array(self._energy_counter))
        temp_c = select_windows_thermal_zone_celsius(
            self._read_array(self._therm_hp_counter), high_precision=True
        )
        if temp_c is None:
            temp_c = select_windows_thermal_zone_celsius(
                self._read_array(self._therm_counter), high_precision=False
            )
        return temp_c, power_w

    def close(self) -> None:
        if getattr(self, "_query", None):
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
            self._query = None


def _read_windows_pdh_cpu_telemetry() -> tuple[float | None, float | None]:
    global _PDH_READER
    with _PDH_LOCK:
        for _attempt in range(2):
            try:
                if _PDH_READER is None:
                    _PDH_READER = _WindowsPdhReader()
                return _PDH_READER.sample()
            except Exception:
                if _PDH_READER is not None:
                    _PDH_READER.close()
                    _PDH_READER = None
        return None, None


_WMI_PROBE_SCRIPT = (
    "foreach ($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor') { "
    "  $rows = Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction SilentlyContinue | "
    "    Where-Object { $_.SensorType -in 'Temperature','Power' -and $null -ne $_.Value } | "
    "    Select-Object Name, SensorType, Identifier, Value; "
    "  if ($rows) { @($rows) | ConvertTo-Json -Compress; exit 0 } "
    "} "
    "$acpi = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
    "  -ErrorAction SilentlyContinue | Select-Object InstanceName, CurrentTemperature; "
    "if ($acpi) { @($acpi) | ConvertTo-Json -Compress }"
)


def _read_windows_wmi_cpu_telemetry() -> tuple[float | None, float | None]:
    global _WMI_LAST_MISS_MONO
    now = time.monotonic()
    with _WMI_LOCK:
        if _WMI_LAST_MISS_MONO is not None and (now - _WMI_LAST_MISS_MONO) < _WMI_MISS_TTL_S:
            return None, None
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WMI_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.SubprocessError):
        with _WMI_LOCK:
            _WMI_LAST_MISS_MONO = time.monotonic()
        return None, None

    stdout = result.stdout.strip()
    if not stdout:
        with _WMI_LOCK:
            _WMI_LAST_MISS_MONO = time.monotonic()
        return None, None
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return None, None
    rows = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
    temp_c, power_w = parse_wmi_cpu_telemetry(rows)
    if temp_c is None and power_w is None:
        with _WMI_LOCK:
            _WMI_LAST_MISS_MONO = time.monotonic()
    else:
        with _WMI_LOCK:
            _WMI_LAST_MISS_MONO = None
    return temp_c, power_w


def _read_memory_telemetry() -> dict[str, Any]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)) or status.ullTotalPhys <= 0:
            return {"utilization_pct": None, "used_mb": None, "total_mb": None}
        total_mb = round(status.ullTotalPhys / (1024 * 1024))
        avail_mb = round(status.ullAvailPhys / (1024 * 1024))
        used_mb = max(0, total_mb - avail_mb)
        pct = round((status.ullTotalPhys - status.ullAvailPhys) / status.ullTotalPhys * 100.0, 1)
        return {"utilization_pct": pct, "used_mb": used_mb, "total_mb": total_mb}

    meminfo: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, rest = line.partition(":")
            if not rest:
                continue
            num = rest.strip().split(" ")[0]
            if num.isdecimal():
                meminfo[key] = int(num)
    except OSError:
        return {"utilization_pct": None, "used_mb": None, "total_mb": None}
    total_kb = meminfo.get("MemTotal")
    avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree"))
    if not total_kb or avail_kb is None:
        return {"utilization_pct": None, "used_mb": None, "total_mb": None}
    total_mb = round(total_kb / 1024)
    used_mb = max(0, total_mb - round(avail_kb / 1024))
    pct = round((total_kb - avail_kb) / total_kb * 100.0, 1)
    return {"utilization_pct": pct, "used_mb": used_mb, "total_mb": total_mb}


def _read_gpu_telemetry(nvidia_smi: str = "nvidia-smi") -> tuple[list[dict[str, Any]], str | None]:
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return [], "NVIDIA_SMI_UNAVAILABLE"
    return parse_nvidia_smi_telemetry(result.stdout), None


def read_hardware_telemetry(*, nvidia_smi: str = "nvidia-smi") -> dict[str, Any]:
    """Return a single read-only snapshot of CPU, RAM and GPU metrics."""
    cpu_pct = _read_cpu_utilization()
    if os.name == "nt":
        cpu_temp, cpu_power = _read_windows_pdh_cpu_telemetry()
        if cpu_temp is None or cpu_power is None:
            wmi_temp, wmi_power = _read_windows_wmi_cpu_telemetry()
            if cpu_temp is None:
                cpu_temp = wmi_temp
            if cpu_power is None:
                cpu_power = wmi_power
    else:
        cpu_temp = _read_linux_cpu_temperature()
        cpu_power = _read_linux_cpu_power_w()
    gpus, gpu_error = _read_gpu_telemetry(nvidia_smi=nvidia_smi)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "cpu": {
            "utilization_pct": cpu_pct,
            "temperature_c": cpu_temp,
            "power_w": cpu_power,
        },
        "memory": _read_memory_telemetry(),
        "gpus": gpus,
        "gpu_error": gpu_error,
    }

"""Physical admission measurements; unknown values never mean available."""

import os
import subprocess


def parse_nvidia_smi(output, gpu_uuid):
    rows = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 4 and parts[1] == gpu_uuid:
            rows.append(parts)
    if not gpu_uuid or len(rows) != 1:
        raise ValueError("HANDOFF_GPU_IDENTITY_UNVERIFIED")
    try:
        index, _, total, free = rows[0]
        if not index.isdecimal() or not total.isdecimal() or not free.isdecimal():
            raise ValueError
        total_mb, free_mb = int(total), int(free)
        if total_mb <= 0 or not 0 <= free_mb <= total_mb:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("HANDOFF_GPU_MEMORY_UNVERIFIED") from exc
    return {"uuid": gpu_uuid, "total_mb": total_mb, "free_mb": free_mb}


def evaluate_readiness(*, available_ram_bytes, gpu, min_ram_mb, min_vram_mb, expected_gpu):
    """Evaluate measured capacity without equating a logical lease with memory release."""
    valid_ram = type(available_ram_bytes) is int and available_ram_bytes >= 0
    valid_budget = (type(min_ram_mb) is int and min_ram_mb > 0
                    and type(min_vram_mb) is int and min_vram_mb > 0)
    valid_gpu = (isinstance(gpu, dict) and isinstance(expected_gpu, str) and bool(expected_gpu)
                 and gpu.get("uuid") == expected_gpu and type(gpu.get("total_mb")) is int
                 and type(gpu.get("free_mb")) is int and gpu["total_mb"] > 0
                 and 0 <= gpu["free_mb"] <= gpu["total_mb"])
    ready = bool(valid_ram and valid_budget and valid_gpu
                 and available_ram_bytes >= min_ram_mb * 1024**2 and gpu["free_mb"] >= min_vram_mb)
    return {"ready": ready, "available_ram_bytes": available_ram_bytes, "gpu": gpu,
            "min_ram_mb": min_ram_mb, "min_vram_mb": min_vram_mb}


def windows_available_ram():
    if os.name != "nt":
        raise ValueError("HANDOFF_RAM_PROBE_REQUIRES_WINDOWS")
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)) or not 0 <= status.ullAvailPhys <= status.ullTotalPhys:
        raise ValueError("HANDOFF_RAM_PROBE_UNAVAILABLE")
    return int(status.ullAvailPhys)


def probe_memory(gpu_uuid, *, min_ram_mb, min_vram_mb, nvidia_smi="nvidia-smi"):
    result = subprocess.run(
        [nvidia_smi, "--query-gpu=index,uuid,memory.total,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True, creationflags=0x08000000 if os.name == "nt" else 0,
    )
    gpu = parse_nvidia_smi(result.stdout, gpu_uuid)
    return evaluate_readiness(available_ram_bytes=windows_available_ram(), gpu=gpu, min_ram_mb=min_ram_mb,
                              min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)

"""Read-only host environment probe for the new-machine entry script.

Every check reports an honest outcome: ``ok``, ``installable`` (the bootstrap
entry can install a pinned user-level copy), ``stale`` (present but below the
floor version), or ``blocked`` with the exact reason. Nothing here pretends a
missing prerequisite is present and no check mutates the host. Command runners
are injectable so tests exercise the same decision logic against synthetic
outputs.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

BOOTSTRAP_INSTALLABLE = {"uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe"}

FLOOR_VERSIONS = {
    "uv": "0.9",
    "node": "20",
    "npm": "10",
    "pwsh": "7.3",
    "ffmpeg": "6",
    "ffprobe": "6",
}

RAM_MB_MIN = 30 * 1024  # 32GB-class machines report ~31.7GB usable
VRAM_MB_MIN = 15360
DISK_GB_MIN = 120
DEFAULT_PORT = 8188


@dataclass
class CommandProbe:
    args: list[str]
    version_pattern: str = r"\d+\.\d+[\w.+]*"


def _run(probe: CommandProbe) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            probe.args, shell=False, capture_output=True, text=True, timeout=30, errors="replace"
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, (result.stdout or "") + (result.stderr or "")


def _version_of(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(0) if match else ""


def _at_floor(version: str, floor: str) -> bool:
    def key(value: str):
        return [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", value)[:3]]

    return key(version) >= key(floor)


def probe_command(name: str, *, runner=None) -> dict:
    """One CLI tool: present / version / at_floor."""
    runner = runner or _run
    npm = "npm.cmd" if os.name == "nt" else "npm"
    if name == "pwsh":
        probe = CommandProbe(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    elif name in {"uv", "node", "npm", "ffmpeg", "ffprobe"}:
        args = {
            "uv": ["uv", "--version"],
            "node": ["node", "--version"],
            "npm": [npm, "--version"],
            "ffmpeg": ["ffmpeg", "-version"],
            "ffprobe": ["ffprobe", "-version"],
        }[name]
        probe = CommandProbe(args)
    else:
        raise ValueError("UNKNOWN_TOOL " + name)
    present, output = runner(probe)
    version = _version_of(output, probe.version_pattern)
    floor = FLOOR_VERSIONS[name]
    return {"present": present, "version": version, "at_floor": _at_floor(version or "0", floor)}


def probe_gpu(runner=None) -> dict:
    """NVIDIA driver/GPU facts via nvidia-smi; absence is a hard blocker."""
    runner = runner or _run
    ok, output = runner(CommandProbe(
        ["nvidia-smi", "--query-gpu=index,uuid,name,driver_version,memory.total", "--format=csv,noheader"]
    ))
    if not ok or not output.strip():
        return {"status": "BLOCKED", "reason": "NVIDIA_DRIVER_UNAVAILABLE"}
    parts = [p.strip() for p in output.strip().splitlines()[0].split(",")]
    if len(parts) < 5:
        return {"status": "BLOCKED", "reason": "NVIDIA_SMI_OUTPUT_UNPARSEABLE"}
    index, uuid, name, driver, vram = parts[0], parts[1], parts[2], parts[3], parts[4]
    vram_mb = _parse_vram(vram)
    return {
        "status": "OK", "index": index, "uuid": uuid,
        "name": (name.split("NVIDIA ")[-1] or name),
        "driver_version": driver, "vram_mb": vram_mb,
        "at_floor": vram_mb >= VRAM_MB_MIN,
    }


def probe_ram() -> dict:
    """GlobalMemoryStatusEx via ctypes; no subprocess and no wmic dependency."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"status": "BLOCKED", "reason": "RAM_UNAVAILABLE"}
        ram_mb = int(status.ullTotalPhys) // (1024**2)
        return {"status": "OK", "ram_mb": ram_mb, "at_floor": ram_mb >= RAM_MB_MIN}
    except Exception:
        return {"status": "BLOCKED", "reason": "RAM_UNAVAILABLE"}


def _parse_vram(text: str) -> int:
    digits = re.findall(r"\d+", text)
    if not digits:
        return 0
    value = int(digits[0])
    if "g" in text.lower():
        return value * 1024
    return value


def probe_disk(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    return {"status": "OK", "free_gb": round(free_gb, 1), "at_floor": free_gb >= DISK_GB_MIN}


def probe_port(port: int) -> dict:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return {"status": "BLOCKED", "reason": "PORT_OCCUPIED", "port": port}
    return {"status": "OK", "port": port}


def probe_writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".shine-write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return {"status": "BLOCKED", "reason": "DATA_DIR_NOT_WRITABLE", "detail": type(exc).__name__}
    return {"status": "OK", "writable": True}


def probe_host_environment(data_dir: Path, *, port: int = DEFAULT_PORT) -> dict:
    """Full read-only host report for the setup entry point."""
    tools = {}
    for name in sorted(BOOTSTRAP_INSTALLABLE):
        tools[name] = probe_command(name)
    return {
        "platform": _platform(),
        "tools": tools,
        "gpu": probe_gpu(),
        "ram": probe_ram(),
        "disk": probe_disk(data_dir.parent),
        "port": probe_port(port),
        "data_dir": {"path": str(data_dir), **probe_writable(data_dir)},
        "path_issues": _path_issues(data_dir),
    }


def _platform() -> dict:
    import platform

    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine()}


def _path_issues(data_dir: Path) -> list:
    """Report (not reject) paths with spaces or non-ASCII characters: they are supported."""
    issues = []
    text = str(data_dir)
    if any(ord(ch) > 127 for ch in text):
        issues.append("NON_ASCII_PATH_SUPPORTED")
    if " " in text:
        issues.append("SPACES_IN_PATH_SUPPORTED")
    if len(text) > 200:
        issues.append("VERY_LONG_PATH")
    return issues


def setup_blockers(report: dict) -> list:
    """Honest blocker list: what stands between this host and the supported config."""
    blockers = []
    for name, info in report["tools"].items():
        if not info["present"]:
            blockers.append({"code": "TOOL_MISSING_INSTALLABLE", "tool": name})
        elif not info["at_floor"]:
            blockers.append({"code": "TOOL_STALE", "tool": name, "version": info["version"]})
    gpu = report["gpu"]
    if gpu["status"] != "OK":
        blockers.append({"code": gpu["reason"], "tool": "gpu"})
    elif not gpu["at_floor"]:
        blockers.append({"code": "GPU_VRAM_BELOW_FLOOR", "vram_mb": gpu["vram_mb"]})
    if report["ram"].get("status") != "OK" or not report["ram"].get("at_floor"):
        blockers.append({"code": "RAM_BELOW_FLOOR", "ram_mb": report["ram"].get("ram_mb", 0)})
    if not report["disk"]["at_floor"]:
        blockers.append({"code": "DISK_BELOW_FLOOR", "free_gb": report["disk"]["free_gb"]})
    if report["port"]["status"] != "OK":
        blockers.append({"code": "PORT_OCCUPIED", "port": report["port"]["port"]})
    if not report["data_dir"]["writable"]:
        blockers.append({"code": report["data_dir"].get("reason", "DATA_DIR_NOT_WRITABLE")})
    return blockers

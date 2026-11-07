"""Host environment probes for the new-machine entry script.

Every check reports an honest outcome: ``ok``, ``installable`` (the bootstrap
entry can install a pinned user-level copy), ``stale`` (present but below the
floor version), or ``blocked`` with the exact reason. Nothing here pretends a
missing prerequisite is present. Writable checks use temporary files; probes
never install tools or models. Command runners
are injectable so tests exercise the same decision logic against synthetic
outputs.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
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
DISK_GB_MIN = 5  # Runtime headroom; the installation plan separately reserves the complete deployment footprint.
DEFAULT_PORT = 8188

#: The Torch/CUDA wheel the deployment pins.  The wheel itself is a pinned
#: project fact; whether it is *installed* is read from the isolated runtime.
PINNED_TORCH_WHEEL = "torch 2.7.1+cu128"

_TORCH_PROBE_CACHE: dict[str, dict] = {}


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


def default_requirements() -> dict:
    """The admission thresholds of the default production variant.

    The variant manifest is the single place where these numbers are written
    down; this helper only reads them.
    """
    try:
        from shine.comfyops.variants import default_variant_id, load_variant_manifest, variant_requirements

        return variant_requirements(load_variant_manifest(default_variant_id()))
    except (OSError, ValueError, FileNotFoundError):
        return {
            "os": "windows-x64",
            "arch": "x64",
            "gpu_vendor": "nvidia",
            "min_vram_mb": VRAM_MB_MIN,
            "min_ram_mb": RAM_MB_MIN,
            "min_disk_gb": 180,
        }


def probe_gpus(runner=None) -> list:
    """Every NVIDIA card this machine has, with its own memory numbers.

    Each card is reported on its own.  The memory of several cards is never
    added up: the product renders on one card, and only that card counts.
    """
    runner = runner or _run
    ok, output = runner(CommandProbe([
        "nvidia-smi",
        "--query-gpu=index,uuid,name,driver_version,memory.total,memory.used,memory.free",
        "--format=csv,noheader",
    ]))
    if not ok or not output.strip():
        return []
    cards = []
    for line in output.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        cards.append({
            "index": parts[0],
            "uuid": parts[1],
            "name": (parts[2].split("NVIDIA ")[-1] or parts[2]),
            "driver_version": parts[3],
            "vram_total_mb": _parse_vram(parts[4]),
            "vram_used_mb": _parse_vram(parts[5]),
            "vram_free_mb": _parse_vram(parts[6]),
        })
    return cards


def select_gpu(cards: list, selected_uuid: str | None = None) -> dict | None:
    """The single card this machine will use.

    The user picks one card by UUID; until that choice exists the first card
    is used.  Either way only one card is ever selected.
    """
    if not cards:
        return None
    chosen = None
    if selected_uuid:
        chosen = next((card for card in cards if card.get("uuid") == selected_uuid), None)
    if chosen is None:
        chosen = cards[0]
    for card in cards:
        card["selected"] = card is chosen
    return chosen


def probe_gpu(runner=None, selected_uuid: str | None = None) -> dict:
    """The selected NVIDIA card; absence is a hard blocker."""
    cards = probe_gpus(runner)
    chosen = select_gpu(cards, selected_uuid)
    if chosen is None:
        return {"status": "BLOCKED", "reason": "NVIDIA_DRIVER_UNAVAILABLE"}
    requirements = default_requirements()
    floor = int(requirements.get("min_vram_mb") or VRAM_MB_MIN)
    return {
        "status": "OK",
        "index": chosen["index"],
        "uuid": chosen["uuid"],
        "name": chosen["name"],
        "driver_version": chosen["driver_version"],
        "vram_mb": chosen["vram_total_mb"],
        "vram_total_mb": chosen["vram_total_mb"],
        "vram_used_mb": chosen["vram_used_mb"],
        "vram_free_mb": chosen["vram_free_mb"],
        "gpus": cards,
        "at_floor": chosen["vram_total_mb"] >= floor,
    }


def _memory_status() -> dict:
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
        return {
            "status": "OK",
            "ram_mb": int(status.ullTotalPhys) // (1024 ** 2),
            "ram_available_mb": int(status.ullAvailPhys) // (1024 ** 2),
            "commit_total_mb": int(status.ullTotalPageFile) // (1024 ** 2),
            "commit_available_mb": int(status.ullAvailPageFile) // (1024 ** 2),
            "memory_load_percent": int(status.dwMemoryLoad),
        }
    except Exception:
        return {"status": "BLOCKED", "reason": "RAM_UNAVAILABLE"}


def probe_ram() -> dict:
    """Physical memory: the total and what is available right now."""
    status = _memory_status()
    if status.get("status") != "OK":
        return status
    from shine.comfyops.variants import effective_floor_mb

    requirements = default_requirements()
    floor = effective_floor_mb(int(requirements.get("min_ram_mb") or RAM_MB_MIN)) or RAM_MB_MIN
    return {
        "status": "OK",
        "ram_mb": status["ram_mb"],
        "ram_available_mb": status["ram_available_mb"],
        "at_floor": status["ram_mb"] >= floor,
    }


def probe_commit() -> dict:
    """The Windows commit limit and the page-file state behind it."""
    status = _memory_status()
    if status.get("status") != "OK":
        return {"status": "BLOCKED", "reason": "COMMIT_UNAVAILABLE"}
    total = status["commit_total_mb"]
    physical = status["ram_mb"]
    page_file = "PAGE_FILE_PRESENT"
    if total <= physical:
        page_file = "PAGE_FILE_DISABLED_OR_TINY"
    return {
        "status": "OK",
        "commit_total_mb": total,
        "commit_available_mb": status["commit_available_mb"],
        "page_file_status": page_file,
        "page_file_headroom_mb": max(0, total - physical),
    }


def _isolated_python(deployment_dir: Path | None) -> Path | None:
    """The interpreter of the isolated ComfyUI runtime, when it exists."""
    if deployment_dir is None:
        return None
    instances = Path(deployment_dir) / "instances"
    if not instances.is_dir():
        return None
    for candidate in sorted(instances.glob("*/.venv/Scripts/python.exe")):
        if candidate.is_file():
            return candidate
    return None


def probe_cuda(runner=None, deployment_dir: Path | None = None) -> dict:
    """CUDA compute capability and the state of the pinned Torch runtime.

    The compute capability comes from the driver and is available before any
    model is installed.  The Torch wheel and its architecture list can only be
    read from the isolated environment, so they stay explicitly unknown until
    that environment exists.
    """
    runner = runner or _run
    ok, output = runner(CommandProbe(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"]
    ))
    capability = ""
    if ok and output.strip():
        capability = output.strip().splitlines()[0].strip()
    torch_state = _probe_torch(deployment_dir)
    if not capability:
        return {
            "status": "UNKNOWN",
            "reason": "CUDA_COMPUTE_CAPABILITY_UNKNOWN",
            "compute_capability": None,
            "torch": torch_state,
        }
    return {
        "status": "OK",
        "compute_capability": capability,
        "compute_capability_source": "nvidia-smi",
        "torch": torch_state,
    }


def _probe_torch(deployment_dir: Path | None) -> dict:
    """Read the installed Torch build; unknown when the runtime is absent."""
    interpreter = _isolated_python(deployment_dir)
    if interpreter is None:
        return {
            "status": "UNKNOWN",
            "reason": "ISOLATED_RUNTIME_NOT_INSTALLED",
            "pinned_wheel": PINNED_TORCH_WHEEL,
            "version": None,
            "cuda_build": None,
            "arch_list": None,
        }
    key = str(interpreter)
    cached = _TORCH_PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    script = (
        "import json;import torch;"
        "print(json.dumps({'version': torch.__version__,"
        "'cuda_build': torch.version.cuda,"
        "'arch_list': list(torch.cuda.get_arch_list())}))"
    )
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", script], capture_output=True, text=True,
            timeout=120, errors="replace", check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        state = {"status": "UNKNOWN", "reason": "TORCH_PROBE_FAILED",
                 "pinned_wheel": PINNED_TORCH_WHEEL, "version": None,
                 "cuda_build": None, "arch_list": None}
        _TORCH_PROBE_CACHE[key] = state
        return state
    payload = {}
    if completed.returncode == 0:
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            payload = {}
    state = {
        "status": "OK" if completed.returncode == 0 else "UNKNOWN",
        "reason": None if completed.returncode == 0 else "TORCH_IMPORT_FAILED",
        "pinned_wheel": PINNED_TORCH_WHEEL,
        "version": payload.get("version"),
        "cuda_build": payload.get("cuda_build"),
        "arch_list": payload.get("arch_list"),
    }
    _TORCH_PROBE_CACHE[key] = state
    return state


def _parse_vram(text: str) -> int:
    digits = re.findall(r"\d+", text)
    if not digits:
        return 0
    value = int(digits[0])
    if "g" in text.lower():
        return value * 1024
    return value


def probe_disk(path: Path) -> dict:
    from shine.comfyops.storage import disk_info
    info = disk_info(path)
    return {"status": "OK", **info, "path": str(path),
            "at_floor": info['free_bytes'] >= DISK_GB_MIN * 1024**3,
            "runtime_floor_gb": DISK_GB_MIN}


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
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b'ok')
            probe.flush()
    except OSError as exc:
        return {"status": "BLOCKED", "reason": "DATA_DIR_NOT_WRITABLE", "detail": type(exc).__name__}
    return {"status": "OK", "writable": True}


def probe_host_environment(data_dir: Path, *, port: int = DEFAULT_PORT, deployment_dir: Path | None = None) -> dict:
    """Probe the selected deployment disk independently of the project database disk."""
    target = deployment_dir or data_dir
    tools = {}
    for name in sorted(BOOTSTRAP_INSTALLABLE):
        tools[name] = probe_command(name)
    facts = probe_host_facts(data_dir, deployment_dir=target)
    return {
        "platform": facts["platform"],
        "tools": tools,
        "gpu": facts["gpu"],
        "gpus": facts["gpus"],
        "cuda": facts["cuda"],
        "ram": facts["ram"],
        "commit": facts["commit"],
        "disk": facts["disk"],
        "port": probe_port(port),
        "data_dir": facts["paths"]["data_dir"],
        "deployment_dir": facts["paths"]["deployment_dir"],
        "path_issues": facts["paths"]["issues"],
        "evidence": facts["evidence"],
        "facts": facts,
    }


def _platform_facts() -> dict:
    """The platform facts a variant is admitted against."""
    import platform

    from shine.comfyops.variants import normalize_arch

    system = platform.system()
    version = platform.version()
    return {
        "system": system,
        "release": platform.release(),
        "version": version,
        "machine": platform.machine(),
        "arch": normalize_arch(platform.machine()),
        "is_windows": system == "Windows",
        "is_x64": normalize_arch(platform.machine()) == "x64",
        "windows_build": version if system == "Windows" else None,
    }


def probe_host_facts(data_dir: Path, *, deployment_dir: Path | None = None,
                     selected_gpu_uuid: str | None = None) -> dict:
    """The structured host facts every admission decision is made against.

    The report is read-only: it starts no renderer, loads no weight and
    downloads nothing.  Every section states what it could not read.
    """
    target = deployment_dir or data_dir
    cards = probe_gpus()
    chosen = select_gpu(cards, selected_gpu_uuid)
    ram = probe_ram()
    commit = probe_commit()
    cuda = probe_cuda(deployment_dir=target)
    disk = probe_disk(target)
    failures = []
    if chosen is None:
        failures.append({"section": "gpu", "code": "NVIDIA_DRIVER_UNAVAILABLE"})
    if ram.get("status") != "OK":
        failures.append({"section": "ram", "code": ram.get("reason", "RAM_UNAVAILABLE")})
    if commit.get("status") != "OK":
        failures.append({"section": "commit", "code": commit.get("reason", "COMMIT_UNAVAILABLE")})
    if cuda.get("status") != "OK":
        failures.append({"section": "cuda", "code": cuda.get("reason", "CUDA_UNKNOWN")})
    gpu_view = (
        {
            "status": "OK",
            "index": chosen["index"],
            "uuid": chosen["uuid"],
            "name": chosen["name"],
            "driver_version": chosen["driver_version"],
            "vram_mb": chosen["vram_total_mb"],
            "vram_total_mb": chosen["vram_total_mb"],
            "vram_used_mb": chosen["vram_used_mb"],
            "vram_free_mb": chosen["vram_free_mb"],
        }
        if chosen is not None
        else {"status": "BLOCKED", "reason": "NVIDIA_DRIVER_UNAVAILABLE"}
    )
    return {
        "platform": _platform_facts(),
        "gpus": cards,
        "gpu": gpu_view,
        "cuda": cuda,
        "ram": ram,
        "commit": commit,
        "disk": disk,
        "paths": {
            "data_dir": {"path": str(data_dir), **probe_writable(data_dir)},
            "deployment_dir": {"path": str(target), **probe_writable(target)},
            "issues": _path_issues(data_dir),
        },
        "evidence": {
            "probed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "failures": failures,
            "sources": {
                "gpu": "measured",
                "cuda": "measured" if cuda.get("status") == "OK" else "unknown",
                "ram": "measured" if ram.get("status") == "OK" else "unknown",
                "commit": "measured" if commit.get("status") == "OK" else "unknown",
                "disk": "measured",
                "platform": "measured",
            },
            "selected_gpu_uuid": chosen.get("uuid") if chosen else None,
            "multi_gpu_memory_is_summed": False,
        },
    }


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
    """Honest blocker list: what stands between this host and the supported config.

    The floors come from the default variant manifest, so this list, the
    recommender, the install plan and the production start cannot disagree
    about what a machine needs.
    """
    blockers = []
    for name, info in (report.get("tools") or {}).items():
        if not info["present"]:
            blockers.append({"code": "TOOL_MISSING_INSTALLABLE", "tool": name})
        elif not info["at_floor"]:
            blockers.append({"code": "TOOL_STALE", "tool": name, "version": info["version"]})
    facts = report.get("facts") or report
    requirements = default_requirements()
    gpu = facts.get("gpu") or {}
    if gpu.get("status") != "OK":
        blockers.append({"code": gpu.get("reason", "NVIDIA_DRIVER_UNAVAILABLE"), "tool": "gpu"})
    else:
        floor = int(requirements.get("min_vram_mb") or VRAM_MB_MIN)
        total = int(gpu.get("vram_total_mb") or gpu.get("vram_mb") or 0)
        if total < floor:
            blockers.append({"code": "GPU_VRAM_BELOW_FLOOR", "vram_mb": total})
    ram = facts.get("ram") or {}
    if ram.get("status") != "OK" or not ram.get("at_floor"):
        blockers.append({"code": "RAM_BELOW_FLOOR", "ram_mb": ram.get("ram_mb", 0)})
    commit = facts.get("commit") or {}
    min_commit = requirements.get("min_commit_mb")
    if isinstance(min_commit, int | float) and not isinstance(min_commit, bool):
        if commit.get("status") != "OK":
            blockers.append({"code": "COMMIT_UNAVAILABLE"})
        elif int(commit.get("commit_total_mb") or 0) < int(min_commit):
            blockers.append({"code": "COMMIT_BELOW_FLOOR", "commit_mb": commit.get("commit_total_mb")})
    disk = report.get("disk") or facts.get("disk") or {}
    if not disk.get("at_floor", True):
        blockers.append({"code": "DISK_BELOW_FLOOR", "free_gb": disk.get("free_gb")})
    port = report.get("port") or {}
    if port.get("status") != "OK":
        blockers.append({"code": "PORT_OCCUPIED", "port": port.get("port")})
    if not (report.get("data_dir") or {}).get("writable", True):
        blockers.append({"code": (report.get("data_dir") or {}).get("reason", "DATA_DIR_NOT_WRITABLE")})
    if not (report.get("deployment_dir") or {}).get("writable", True):
        blockers.append({"code": "DEPLOYMENT_DIR_NOT_WRITABLE"})
    return blockers

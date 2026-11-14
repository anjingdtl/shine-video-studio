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
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BOOTSTRAP_INSTALLABLE = {"uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe"}

#: The fixed command whitelist.  A probe may only ask for one of these names;
#: a caller-supplied executable path is never accepted.  The tuple is the
#: lookup order per platform and is a project fact, not a PATH guess.
TOOL_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "uv": ("uv.exe", "uv"),
    "node": ("node.exe", "node"),
    "npm": ("npm.cmd", "npm.exe", "npm"),
    "pwsh": ("pwsh.exe", "pwsh"),
    "ffmpeg": ("ffmpeg.exe", "ffmpeg"),
    "ffprobe": ("ffprobe.exe", "ffprobe"),
}

#: Windows scripts cannot be passed to CreateProcess as an application name.
#: They are executed through the system command processor with a fixed flag set.
WINDOWS_SCRIPT_SUFFIXES = (".cmd", ".bat")

RESOLUTION_MANAGED = "MANAGED_TOOL_DIR"
RESOLUTION_PROCESS_PATH = "PROCESS_PATH"
RESOLUTION_MISSING = "NOT_FOUND"

#: The ports a service may be moved to when the preferred one is not usable.
#: The range starts right after the historical 8188 and is only ever probed.
PORT_FALLBACK_WINDOW = 64

#: Port identities.  "Can this port be bound" is not the same question as
#: "which program holds it": a Shine-owned renderer is reusable, a foreign
#: process must never be killed and only needs a different port.
PORT_FREE = "FREE"
PORT_OWNED_COMFYUI = "OWNED_COMFYUI"
PORT_ADOPTABLE_COMFYUI = "ADOPTABLE_COMFYUI"
PORT_FOREIGN_PROCESS = "FOREIGN_PROCESS"
PORT_UNKNOWN_IDENTITY = "UNKNOWN_IDENTITY"

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


def _is_windows_host(windows: bool | None = None) -> bool:
    return os.name == "nt" if windows is None else bool(windows)


def _inherited_path_entries() -> list[str]:
    value = os.environ.get("PATH") or os.environ.get("Path") or ""
    return [entry for entry in value.split(os.pathsep) if entry]


def _candidate_names(name: str, windows: bool) -> tuple[str, ...]:
    names = TOOL_EXECUTABLES[name]
    if windows:
        return names
    return tuple(row for row in names if not row.lower().endswith((".exe", ".cmd", ".bat")))


def _looks_executable(path: Path, windows: bool) -> bool:
    try:
        if not path.is_file():
            return False
        if windows:
            return path.suffix.lower() in {".exe", ".cmd", ".bat"} or not path.suffix
        return os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_tool_executable(name: str, search_paths: list[str] | None = None, *,
                            windows: bool | None = None) -> dict:
    """Resolve one whitelisted tool to an absolute path without trusting input.

    This is the fix for the "installed but still reported missing" defect:
    adding a directory to a child process' PATH environment block does *not*
    make Windows' ``CreateProcess`` search it, because the application name is
    resolved against the *parent's* PATH.  The tool is therefore resolved here,
    against the managed tool directory first, and the absolute path is what
    gets executed.

    Only names from :data:`TOOL_EXECUTABLES` are accepted.  A caller cannot
    pass an executable path, a relative path or an unknown command.
    """
    if name not in TOOL_EXECUTABLES:
        raise ValueError("UNKNOWN_TOOL " + str(name))
    on_windows = _is_windows_host(windows)
    managed = [entry for entry in (search_paths or []) if entry]
    directories = [*managed, *_inherited_path_entries()]
    candidates = _candidate_names(name, on_windows)
    for directory in directories:
        try:
            base = Path(directory).expanduser()
        except (OSError, ValueError):
            continue
        if not base.is_absolute():
            # A relative PATH entry is resolved against the current directory,
            # which is not a stable identity for a tool.  Skip it.
            continue
        for candidate in candidates:
            path = base / candidate
            if not _looks_executable(path, on_windows):
                continue
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = str(path)
            return {
                "resolved": True,
                "tool": name,
                "path": resolved,
                "candidate": candidate,
                "source": (RESOLUTION_MANAGED if directory in managed
                           else RESOLUTION_PROCESS_PATH),
                "managed_root": directory if directory in managed else None,
                "is_script": candidate.lower().endswith(WINDOWS_SCRIPT_SUFFIXES),
                "searched": directories,
            }
    return {
        "resolved": False,
        "tool": name,
        "path": None,
        "candidate": None,
        "source": RESOLUTION_MISSING,
        "managed_root": None,
        "is_script": False,
        "searched": directories,
    }


def _windows_script_argv(executable: str, arguments: list[str]) -> list[str]:
    """The only legal way to run a ``.cmd``/``.bat`` shim from Python.

    ``subprocess`` with ``shell=False`` calls ``CreateProcess``, whose first
    argument must be a real executable image; a command script is not one.
    The script is therefore handed to the system command processor with fixed
    flags.  The extra outer quotes are required by ``cmd /s /c`` when the
    script path contains spaces (the managed directory usually does).
    """
    comspec = (os.environ.get("COMSPEC") or os.environ.get("ComSpec")
               or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"))
    command = subprocess.list2cmdline([executable, *arguments])
    return [comspec, "/d", "/s", "/c", f'"{command}"']


def executable_argv(resolution: dict, arguments: list[str], *,
                    windows: bool | None = None) -> list[str]:
    """The argv that runs a resolved tool, honouring the Windows script rule."""
    if not resolution.get("resolved") or not resolution.get("path"):
        raise FileNotFoundError("TOOL_NOT_RESOLVED")
    executable = str(resolution["path"])
    if _is_windows_host(windows) and executable.lower().endswith(WINDOWS_SCRIPT_SUFFIXES):
        return _windows_script_argv(executable, list(arguments))
    return [executable, *arguments]


def _environment_with_search_paths(search_paths: list[str] | None) -> dict | None:
    """A child environment whose PATH starts with the managed tool directory.

    The resolved absolute executable does not need this; the shim's own child
    processes (``npm`` starts ``node``) do.
    """
    if not search_paths:
        return None
    environment = dict(os.environ)
    existing = environment.get("PATH") or environment.get("Path") or ""
    environment["PATH"] = os.pathsep.join([*search_paths, existing])
    return environment


def _run_resolved(resolution: dict, name: str, search_paths: list[str] | None, *,
                  windows: bool | None = None) -> tuple[bool, str]:
    arguments = {
        "uv": ["--version"],
        "node": ["--version"],
        "npm": ["--version"],
        "pwsh": ["-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        "ffmpeg": ["-version"],
        "ffprobe": ["-version"],
    }[name]
    try:
        argv = executable_argv(resolution, arguments, windows=windows)
        result = subprocess.run(
            argv, shell=False, capture_output=True, text=True, timeout=30,
            errors="replace", env=_environment_with_search_paths(search_paths),
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, (result.stdout or "") + (result.stderr or "")


def _run_with_path(probe: CommandProbe, search_paths: list[str] | None) -> tuple[bool, str]:
    """Run a probe from the managed tool directory when the tool is one of ours.

    Kept for callers that build a :class:`CommandProbe` directly.  The command
    name is resolved to an absolute path first; see
    :func:`resolve_tool_executable` for why a PATH prefix alone is not enough.
    """
    if not probe.args:
        return False, ""
    name = Path(str(probe.args[0])).name
    if name.lower().endswith((".exe", ".cmd", ".bat")):
        name = Path(name).stem
    if name not in TOOL_EXECUTABLES:
        return _run(probe)
    resolution = resolve_tool_executable(name, search_paths)
    if not resolution["resolved"]:
        return False, ""
    return _run_resolved(resolution, name, search_paths)


def _version_of(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(0) if match else ""


def _at_floor(version: str, floor: str) -> bool:
    def key(value: str):
        return [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", value)[:3]]

    return key(version) >= key(floor)


def probe_command(name: str, *, runner=None, search_paths: list[str] | None = None,
                  windows: bool | None = None) -> dict:
    """One CLI tool: present / version / at_floor / how it was found.

    ``search_paths`` are the managed tool directories checked in front of the
    inherited PATH, so a tool installed a minute ago is detected without a
    workbench restart.  The tool is resolved to an absolute path and executed
    by that path; a Windows ``.cmd`` shim is run through the system command
    processor.  The returned ``command`` is the resolved executable identity.
    """
    if name not in TOOL_EXECUTABLES:
        raise ValueError("UNKNOWN_TOOL " + str(name))
    resolution = resolve_tool_executable(name, search_paths, windows=windows)
    npm = "npm.cmd" if _is_windows_host(windows) else "npm"
    if name == "pwsh":
        probe = CommandProbe(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    else:
        args = {
            "uv": ["uv", "--version"],
            "node": ["node", "--version"],
            "npm": [npm, "--version"],
            "ffmpeg": ["ffmpeg", "-version"],
            "ffprobe": ["ffprobe", "-version"],
        }[name]
        probe = CommandProbe(args)
    if runner is not None:
        present, output = runner(probe)
    elif resolution["resolved"]:
        present, output = _run_resolved(resolution, name, search_paths, windows=windows)
    else:
        present, output = False, ""
    version = _version_of(output, probe.version_pattern)
    floor = FLOOR_VERSIONS[name]
    return {
        "present": present,
        "version": version,
        "at_floor": _at_floor(version or "0", floor),
        "min_version": floor,
        "tool": name,
        "command": resolution["path"],
        "resolved_path": resolution["path"],
        "resolution": resolution["source"],
        "resolution_kind": "COMMAND_SCRIPT" if resolution["is_script"] else "EXECUTABLE",
        # The probe reports the scope it really searched.  It never claims a
        # process PATH answer when the tool came from the managed directory.
        "search_scope": ("MANAGED_THEN_PROCESS_PATH" if search_paths
                         else "PROCESS_PATH"),
        "requires_environment_refresh": bool(search_paths and present),
    }


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


def probe_installed_memory(runner=None) -> dict:
    """The physical memory the machine was built with, when Windows can say it.

    ``GlobalMemoryStatusEx`` reports the memory Windows may use; firmware and
    the iGPU reserve part of a retail 32 GB kit, so a healthy 32 GB machine can
    report ~31.0 GiB.  Summing ``Win32_PhysicalMemory.Capacity`` is a different
    fact from the same machine: what is physically installed.  Both facts are
    reported side by side and neither one is invented.  On a non-Windows host
    the installation size is explicitly unknown.
    """
    if os.name != "nt":
        return {"status": "UNKNOWN", "reason": "HARDWARE_INVENTORY_UNAVAILABLE",
                "installed_mb": None, "source": "not_windows"}
    runner = runner or _run
    ok, output = runner(CommandProbe([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "(Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum",
    ]))
    digits = re.findall(r"\d{6,}", output or "")
    if not ok or not digits:
        return {"status": "UNKNOWN", "reason": "HARDWARE_INVENTORY_UNAVAILABLE",
                "installed_mb": None, "source": "Win32_PhysicalMemory"}
    installed_mb = int(digits[-1]) // (1024 ** 2)
    if installed_mb <= 0:
        return {"status": "UNKNOWN", "reason": "HARDWARE_INVENTORY_UNPARSEABLE",
                "installed_mb": None, "source": "Win32_PhysicalMemory"}
    return {"status": "OK", "reason": None, "installed_mb": installed_mb,
            "source": "Win32_PhysicalMemory"}


def probe_ram(installed_reader=None, memory_reader=None) -> dict:
    """Physical memory as four separate facts.

    ``ram_mb``                  what the OS reports it can use (GlobalMemoryStatusEx)
    ``installed_mb``            what the firmware says is installed (may be unknown)
    ``floor_mb``                the admission floor expressed in reported units
    ``at_floor``                measured against the floor, with its basis named

    A host that reports less than the floor *and* has no readable hardware
    inventory is ``review_required`` - it is never rounded up to "meets the
    floor".  A host whose installed capacity really reaches the declared floor
    is reported with ``HARDWARE_RESERVED_MEMORY`` as the basis, which is a
    measured explanation, not a widened tolerance.
    """
    status = (memory_reader or _memory_status)()
    if status.get("status") != "OK":
        return {**status, "at_floor": False, "review_required": True,
                "declared_floor_mb": None, "floor_mb": None,
                "installed_mb": None, "at_floor_basis": "UNMEASURED",
                "detail": "无法读取本机物理内存"}
    from shine.comfyops.variants import effective_floor_mb

    requirements = default_requirements()
    declared = int(requirements.get("min_ram_mb") or RAM_MB_MIN)
    floor = effective_floor_mb(declared) or RAM_MB_MIN
    inventory = (installed_reader or probe_installed_memory)()
    installed = inventory.get("installed_mb")
    total = status["ram_mb"]
    at_floor = total >= floor
    basis = "OS_REPORTED_TOTAL" if at_floor else None
    note = None
    if not at_floor and isinstance(installed, int) and installed >= declared:
        at_floor = True
        basis = "HARDWARE_INSTALLED_MEMORY"
        note = "HARDWARE_RESERVED_MEMORY"
    elif not at_floor:
        basis = "OS_REPORTED_TOTAL"
        note = ("BELOW_FLOOR" if isinstance(installed, int)
                else "INSTALLED_CAPACITY_UNKNOWN")
    review_required = (not at_floor) and not isinstance(installed, int)
    return {
        "status": "OK",
        "ram_mb": total,
        "ram_available_mb": status["ram_available_mb"],
        "installed_mb": installed,
        "installed_source": inventory.get("source"),
        "installed_inventory_status": inventory.get("status"),
        "declared_floor_mb": declared,
        "floor_mb": floor,
        "deficit_mb": max(0, floor - total),
        "at_floor": at_floor,
        "at_floor_basis": basis,
        "review_required": review_required,
        "note": note,
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


def _port_bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _probe_comfy_http(port: int) -> dict:
    """Ask the listener whether it is ComfyUI; a 200 alone proves nothing.

    The contract checked here is ComfyUI's own ``/system_stats`` shape: a JSON
    object carrying both ``system`` and ``devices``.  Any other responder is a
    foreign process whose identity stays foreign, never "ComfyUI".
    """
    url = f"http://127.0.0.1:{port}/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - loopback only
            raw = response.read(262_144)
    except (urllib.error.URLError, OSError, ValueError):
        return {"reachable": False, "comfyui": False, "probe": "/system_stats"}
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"reachable": True, "comfyui": False, "probe": "/system_stats",
                "detail": "NON_JSON_RESPONSE"}
    is_comfy = isinstance(payload, dict) and "system" in payload and "devices" in payload
    return {
        "reachable": True,
        "comfyui": is_comfy,
        "probe": "/system_stats",
        "comfyui_version": ((payload.get("system") or {}).get("comfyui_version")
                            if isinstance(payload, dict) else None),
        "device_count": len(payload.get("devices") or []) if isinstance(payload, dict) else None,
    }


def probe_comfy_service(port: int) -> dict:
    """Read-only identity evidence of a ComfyUI service on loopback.

    A reachable HTTP server is not a ComfyUI identity; the contract requires
    the ``/system_stats`` shape (``system`` + ``devices``).  The reported
    version comes from that response and is labelled with its source.
    """
    probe = _probe_comfy_http(port)
    return {
        "port": port,
        "reachable": bool(probe.get("reachable")),
        "is_comfyui": bool(probe.get("comfyui")),
        "version": probe.get("comfyui_version"),
        "version_source": "/system_stats" if probe.get("comfyui_version") else None,
        "device_count": probe.get("device_count"),
        "probe": "/system_stats",
    }


def _first_free_port(preferred: int, *, owned_ports: set[int]) -> int | None:
    for candidate in range(preferred + 1, min(preferred + 1 + PORT_FALLBACK_WINDOW, 65536)):
        if candidate in owned_ports:
            return candidate
        if _port_bindable(candidate):
            return candidate
    return None


def probe_port(port: int, *, http=None, owned_ports: set[int] | None = None) -> dict:
    """Which program holds this port, and which port the service may use.

    Binding is only the first question.  A Shine-owned renderer or an existing
    ComfyUI is reusable; a foreign process is never killed and never
    mislabelled, it only moves the service to another verified-free port.
    When nothing can be established the identity is ``UNKNOWN_IDENTITY`` - the
    absence of evidence is not evidence of a free port.
    """
    owned = set(owned_ports or set())
    http_probe = http or _probe_comfy_http
    if _port_bindable(port):
        return {
            "status": "OK",
            "port": port,
            "selected": port,
            "identity": PORT_FREE,
            "preferred_available": True,
            "reusable": False,
            "evidence": {"bind": "FREE"},
        }
    if port in owned:
        return {
            "status": "OK",
            "port": port,
            "selected": port,
            "identity": PORT_OWNED_COMFYUI,
            "preferred_available": True,
            "reusable": True,
            "evidence": {"bind": "OCCUPIED", "source": "managed_instance_record",
                         "identity_rule": "owned_record"},
        }
    probe = http_probe(port) or {}
    if probe.get("comfyui"):
        identity = PORT_ADOPTABLE_COMFYUI
        reusable = True
    elif probe.get("reachable"):
        identity = PORT_FOREIGN_PROCESS
        reusable = False
    else:
        identity = PORT_UNKNOWN_IDENTITY
        reusable = False
    alternative = _first_free_port(port, owned_ports=owned)
    selected = port if reusable else alternative
    return {
        "status": "OK" if selected is not None else "BLOCKED",
        "port": port,
        "selected": selected,
        "identity": identity,
        "preferred_available": False,
        "reusable": reusable,
        "reason": None if selected is not None else "PORT_OCCUPIED",
        "next_action": None if selected is not None else "USER_ACTION_REQUIRED",
        "evidence": {"bind": "OCCUPIED", **probe,
                     "alternative_port": alternative,
                     "identity_rule": "owned_record" if identity == PORT_OWNED_COMFYUI
                     else ("comfy_system_stats" if identity == PORT_ADOPTABLE_COMFYUI
                           else ("http_responder" if identity == PORT_FOREIGN_PROCESS
                                 else "no_evidence"))},
    }


def probe_writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b'ok')
            probe.flush()
    except OSError as exc:
        return {"status": "BLOCKED", "reason": "DATA_DIR_NOT_WRITABLE", "detail": type(exc).__name__}
    return {"status": "OK", "writable": True}


def probe_host_environment(data_dir: Path, *, port: int = DEFAULT_PORT, deployment_dir: Path | None = None,
                           tool_paths: list[str] | None = None,
                           owned_ports: set[int] | None = None) -> dict:
    """Probe the selected deployment disk independently of the project database disk.

    ``tool_paths`` are managed tool directories searched before PATH, so a tool
    installed by the repair plan is visible without restarting the workbench.
    ``owned_ports`` are ports already recorded for Shine-managed instances;
    they make the port probe answer "owned" instead of guessing.
    """
    target = deployment_dir or data_dir
    tools = {}
    for name in sorted(BOOTSTRAP_INSTALLABLE):
        tools[name] = probe_command(name, search_paths=tool_paths)
    facts = probe_host_facts(data_dir, deployment_dir=target)
    port_state = probe_port(port, owned_ports=owned_ports)
    return {
        "platform": facts["platform"],
        "tools": tools,
        "tool_paths": list(tool_paths or []),
        "gpu": facts["gpu"],
        "gpus": facts["gpus"],
        "cuda": facts["cuda"],
        "ram": facts["ram"],
        "commit": facts["commit"],
        "disk": facts["disk"],
        "port": port_state,
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


REPAIRABILITY_AUTO = "AUTO_REPAIRABLE"
REPAIRABILITY_USER = "USER_ACTION_REQUIRED"
REPAIRABILITY_HARDWARE = "HARDWARE_INCOMPATIBLE"
REPAIRABILITY_UNKNOWN = "DETECTION_UNKNOWN"
REPAIRABILITY_AVAILABLE = "ALREADY_AVAILABLE"
REPAIRABILITIES = (
    REPAIRABILITY_AUTO,
    REPAIRABILITY_USER,
    REPAIRABILITY_HARDWARE,
    REPAIRABILITY_UNKNOWN,
    REPAIRABILITY_AVAILABLE,
)

SCOPE_BOOTSTRAP = "BOOTSTRAP"
SCOPE_RESOURCE = "RESOURCE_PREPARATION"
SCOPE_VARIANT = "VARIANT_QUALIFICATION"
SCOPE_SERVICE = "SERVICE_CONFIGURATION"
SCOPE_ACTIVATION = "PRODUCTION_ACTIVATION"
SCOPES = (SCOPE_BOOTSTRAP, SCOPE_RESOURCE, SCOPE_VARIANT, SCOPE_SERVICE, SCOPE_ACTIVATION)


def _finding(code: str, *, repairability: str, scopes: tuple[str, ...], message: str,
             next_action: str, actual=None, required=None, **extra) -> dict:
    return {
        "code": code,
        "repairability": repairability,
        "scopes": list(scopes),
        "scope": list(scopes),  # Kept for readers that expect one flat key.
        "message": message,
        "next_action": next_action,
        "actual": actual,
        "required": required,
        **extra,
    }


def environment_findings(report: dict, *, manifest: dict | None = None) -> list[dict]:
    """Every detected condition, classified by *what can fix it* and *what it blocks*.

    The old blocker list mixed three different questions and let any answer stop
    the whole chain.  Here each finding states:

    ``repairability``  AUTO_REPAIRABLE / USER_ACTION_REQUIRED /
                       HARDWARE_INCOMPATIBLE / DETECTION_UNKNOWN / ALREADY_AVAILABLE
    ``scopes``         which stages the condition really blocks

    A missing FFmpeg blocks the bootstrap scope only; it never blocks model
    candidate lookup or the creation of a repair plan.  Insufficient VRAM for
    the default INT8 variant blocks that variant's qualification, not resource
    preparation for a low-end candidate.
    """
    from shine.comfyops.tool_inventory import PACKAGE_ALREADY_AVAILABLE, inspect_packages, load_tools_manifest

    try:
        tools_manifest = manifest if manifest is not None else load_tools_manifest()
    except ValueError:
        tools_manifest = {"tools": {}}

    findings: list[dict] = []
    for package in inspect_packages(report.get("tools") or {}, tools_manifest):
        if package["status"] == PACKAGE_ALREADY_AVAILABLE:
            findings.append(_finding(
                "TOOL_PACKAGE_AVAILABLE", repairability=REPAIRABILITY_AVAILABLE,
                scopes=(SCOPE_BOOTSTRAP,), next_action="NONE",
                message=f"{package['label']} 已安装且版本达标，不会重复安装。",
                package_id=package["package_id"], tools=[row["tool"] for row in package["tools"]],
            ))
            continue
        missing = package["missing_tools"]
        stale = package["stale_tools"]
        state = "缺少" if missing else "版本过旧"
        detail = "、".join(missing or stale)
        if package["auto_repairable"]:
            findings.append(_finding(
                "TOOL_MISSING_INSTALLABLE" if missing else "TOOL_STALE",
                repairability=REPAIRABILITY_AUTO, scopes=(SCOPE_BOOTSTRAP,),
                next_action="INSTALL_PINNED_TOOL_PACKAGE",
                message=f"{state} {package['label']}（{detail}）：已固定下载源与 SHA-256，"
                        f"可自动补装 {package['download_bytes']} 字节。",
                actual={"tools": detail, "missing": missing, "stale": stale},
                required={"version": (package["download"] or {}).get("version")},
                package_id=package["package_id"], tool=detail,
                tools=[row["tool"] for row in package["tools"]],
                download=package["download"],
                requires_environment_refresh=True,
            ))
        else:
            findings.append(_finding(
                "TOOL_PACKAGE_UNREPAIRABLE", repairability=REPAIRABILITY_USER,
                scopes=(SCOPE_BOOTSTRAP,), next_action="INSTALL_TOOL_MANUALLY",
                message=f"{state} {package['label']}（{detail}），但没有可用的固定下载清单，需要手动安装。",
                actual={"tools": detail}, required=None,
                package_id=package["package_id"], tool=detail,
            ))

    facts = report.get("facts") or report
    requirements = default_requirements()
    gpu = facts.get("gpu") or {}
    if gpu.get("status") != "OK":
        findings.append(_finding(
            gpu.get("reason", "NVIDIA_DRIVER_UNAVAILABLE"), repairability=REPAIRABILITY_USER,
            scopes=(SCOPE_VARIANT, SCOPE_ACTIVATION), next_action="CHECK_NVIDIA_DRIVER",
            message="无法读取 NVIDIA 显卡与驱动信息；不会自动安装或升级显卡驱动。",
            actual=None, required="nvidia-smi 可读",
        ))
    else:
        floor = int(requirements.get("min_vram_mb") or VRAM_MB_MIN)
        total = int(gpu.get("vram_total_mb") or gpu.get("vram_mb") or 0)
        if total < floor:
            findings.append(_finding(
                "GPU_VRAM_BELOW_FLOOR", repairability=REPAIRABILITY_HARDWARE,
                scopes=(SCOPE_VARIANT, SCOPE_ACTIVATION), next_action="SELECT_LOWER_TIER_VARIANT",
                message=f"这张卡有 {total} MB 显存，低于标准档 {floor} MB 门槛；"
                        "默认 INT8 方案不能标记为生产就绪，但仍可查看并准备合法的低显存候选方案。",
                actual=total, required=floor,
            ))
        else:
            findings.append(_finding(
                "GPU_VRAM_MEETS_FLOOR", repairability=REPAIRABILITY_AVAILABLE,
                scopes=(SCOPE_VARIANT,), next_action="NONE",
                message=f"显存 {total} MB 达到标准档门槛 {floor} MB。",
                actual=total, required=floor,
            ))

    ram = facts.get("ram") or {}
    if ram.get("status") != "OK":
        findings.append(_finding(
            "RAM_UNREADABLE", repairability=REPAIRABILITY_UNKNOWN,
            scopes=(SCOPE_VARIANT,), next_action="REVIEW_MEMORY_INVENTORY",
            message="读不到本机物理内存总量；结论是未知，不会冒充满足。",
            actual=None, required=requirements.get("min_ram_mb"),
        ))
    elif ram.get("at_floor"):
        note = ram.get("note")
        if note == "HARDWARE_RESERVED_MEMORY":
            message = (f"系统报告可用 {ram.get('ram_mb')} MB，但硬件清单显示实装 "
                       f"{ram.get('installed_mb')} MB；差额是固件/核显保留内存，按实装容量判定达标。")
        else:
            message = f"物理内存 {ram.get('ram_mb')} MB 达到门槛 {ram.get('floor_mb')} MB。"
        findings.append(_finding(
            "RAM_MEETS_FLOOR", repairability=REPAIRABILITY_AVAILABLE,
            scopes=(SCOPE_VARIANT,), next_action="NONE", message=message,
            actual=ram.get("ram_mb"), required=ram.get("floor_mb"),
            evidence_basis=ram.get("at_floor_basis"),
        ))
    elif ram.get("review_required"):
        findings.append(_finding(
            "RAM_BELOW_FLOOR_UNCONFIRMED", repairability=REPAIRABILITY_UNKNOWN,
            scopes=(SCOPE_VARIANT,), next_action="REVIEW_MEMORY_INVENTORY",
            message=(f"系统报告 {ram.get('ram_mb')} MB，低于门槛 {ram.get('floor_mb')} MB；"
                     "硬件实装容量读不出来，因此不能确认这台机器属于 32GB 档，也不能伪造为达标。"),
            actual=ram.get("ram_mb"), required=ram.get("floor_mb"),
            deficit_mb=ram.get("deficit_mb"),
        ))
    else:
        findings.append(_finding(
            "RAM_BELOW_FLOOR", repairability=REPAIRABILITY_HARDWARE,
            scopes=(SCOPE_RESOURCE, SCOPE_VARIANT, SCOPE_ACTIVATION),
            next_action="ADD_PHYSICAL_MEMORY",
            message=f"实装内存 {ram.get('installed_mb') or ram.get('ram_mb')} MB，低于门槛 {ram.get('floor_mb')} MB。",
            actual=ram.get("installed_mb") or ram.get("ram_mb"), required=ram.get("floor_mb"),
            deficit_mb=ram.get("deficit_mb"),
        ))

    commit = facts.get("commit") or {}
    min_commit = requirements.get("min_commit_mb")
    if isinstance(min_commit, int | float) and not isinstance(min_commit, bool):
        if commit.get("status") != "OK":
            findings.append(_finding(
                "COMMIT_UNREADABLE", repairability=REPAIRABILITY_UNKNOWN,
                scopes=(SCOPE_VARIANT,), next_action="REVIEW_PAGE_FILE",
                message="读不到提交内存上限，结论未知。", actual=None, required=min_commit,
            ))
        elif int(commit.get("commit_total_mb") or 0) < int(min_commit):
            findings.append(_finding(
                "COMMIT_BELOW_FLOOR", repairability=REPAIRABILITY_USER,
                scopes=(SCOPE_VARIANT, SCOPE_ACTIVATION), next_action="ENLARGE_PAGE_FILE",
                message=f"提交内存上限 {commit.get('commit_total_mb')} MB 低于 {min_commit} MB；"
                        "页面文件过小或被关闭时会出现这种情况。",
                actual=commit.get("commit_total_mb"), required=min_commit,
            ))

    disk = report.get("disk") or facts.get("disk") or {}
    if not disk.get("at_floor", True):
        findings.append(_finding(
            "DISK_BELOW_FLOOR", repairability=REPAIRABILITY_USER,
            scopes=(SCOPE_RESOURCE, SCOPE_ACTIVATION), next_action="FREE_DISK_SPACE",
            message=f"安装盘只剩 {disk.get('free_gb')} GB，低于运行余量门槛。",
            actual=disk.get("free_gb"), required=disk.get("runtime_floor_gb"),
        ))

    port = report.get("port") or {}
    identity = port.get("identity")
    if identity == PORT_FREE:
        findings.append(_finding(
            "PORT_FREE", repairability=REPAIRABILITY_AVAILABLE, scopes=(SCOPE_SERVICE,),
            next_action="NONE", message=f"服务端口 {port.get('port')} 空闲。",
            actual=port.get("port"), required=port.get("port"),
        ))
    elif identity in {PORT_OWNED_COMFYUI, PORT_ADOPTABLE_COMFYUI}:
        findings.append(_finding(
            "PORT_HELD_BY_COMFYUI", repairability=REPAIRABILITY_AVAILABLE, scopes=(SCOPE_SERVICE,),
            next_action="REUSE_EXISTING_COMFYUI",
            message=(f"端口 {port.get('port')} 由"
                     + ("本机受管 ComfyUI" if identity == PORT_OWNED_COMFYUI else "可核验的已有 ComfyUI")
                     + "持有，按现有发现/核验流程复用，不杀进程、不重新绑定。"),
            actual=identity, required=PORT_FREE,
        ))
    elif port.get("selected"):
        findings.append(_finding(
            "PORT_OCCUPIED_SELECTED_ALTERNATIVE", repairability=REPAIRABILITY_AUTO,
            scopes=(SCOPE_SERVICE,), next_action="USE_SELECTED_PORT",
            message=(f"端口 {port.get('port')} 被"
                     f"{('其他进程' if identity == PORT_FOREIGN_PROCESS else '未知进程')}占用；"
                     f"已选择合法空闲端口 {port.get('selected')}，不会结束占用者。"),
            actual={"port": port.get("port"), "identity": identity},
            required={"port": PORT_FREE, "selected": port.get("selected")},
            selected_port=port.get("selected"),
        ))
    else:
        findings.append(_finding(
            "PORT_OCCUPIED", repairability=REPAIRABILITY_USER, scopes=(SCOPE_SERVICE,),
            next_action="FREE_PORT_MANUALLY",
            message=f"端口 {port.get('port')} 被占用，且在候选范围内没有可用端口。",
            actual=identity, required=PORT_FREE,
        ))

    if not (report.get("data_dir") or {}).get("writable", True):
        findings.append(_finding(
            (report.get("data_dir") or {}).get("reason", "DATA_DIR_NOT_WRITABLE"),
            repairability=REPAIRABILITY_USER, scopes=(SCOPE_BOOTSTRAP, SCOPE_RESOURCE),
            next_action="CHOOSE_WRITABLE_DIRECTORY",
            message="项目数据目录无法写入。", actual=None, required="可写目录",
        ))
    if not (report.get("deployment_dir") or {}).get("writable", True):
        findings.append(_finding(
            "DEPLOYMENT_DIR_NOT_WRITABLE", repairability=REPAIRABILITY_USER,
            scopes=(SCOPE_RESOURCE,), next_action="CHOOSE_WRITABLE_DIRECTORY",
            message="安装目录无法写入。", actual=None, required="可写目录",
        ))
    return findings


def setup_blockers(report: dict) -> list:
    """The legacy flat blocker list, derived from the classified findings.

    Each entry keeps its historic ``code`` (so existing consumers keep working)
    and carries the classification and the exact next action.  A condition that
    is already satisfied is not a blocker and is never counted as one.
    """
    blockers = []
    for finding in environment_findings(report):
        if finding["repairability"] == REPAIRABILITY_AVAILABLE:
            continue
        entry = {key: value for key, value in finding.items() if key not in {"scopes", "scope"}}
        entry["scope"] = finding["scopes"]
        blockers.append(entry)
    return blockers

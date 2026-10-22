from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from shine.comfyops.installer import safe_target
from shine.security import require_loopback_url


class WindowsHostProbe:
    """Read only explicit roots/ports. Never recursively discover user disks."""

    def __init__(self, roots: list[Path], ports: list[int], *, run=None, http=None):
        if any(p < 1024 or p > 65535 for p in ports) or len(ports) > 16:
            raise ValueError("PORT_SCOPE_INVALID")
        self.roots = [p.resolve() for p in roots]
        self.ports = ports
        self.run = run or subprocess.run
        self.http = http or httpx.Client(timeout=3, follow_redirects=False)

    def scan(self):
        if platform.system() != "Windows" and self.run is subprocess.run:
            raise ValueError("WINDOWS_HOST_REQUIRED")
        result = {
            "source": "WindowsHostProbe",
            "is_mock": False,
            "os": platform.platform(),
            "python": sys.version,
            "gpu": [],
            "instances": [],
            "roots": [],
            "coverage": [],
        }
        try:
            command = [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ]
            proc = self.run(command, capture_output=True, text=True, timeout=10, check=True)
            result["gpu"] = [x.strip().split(", ") for x in proc.stdout.splitlines()]
        except (OSError, subprocess.SubprocessError):
            result["coverage"].append("GPU_UNAVAILABLE")
        try:
            proc = self.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-CimInstance Win32_OperatingSystem | "
                    "Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            result["memory"] = json.loads(proc.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            result["coverage"].append("MEMORY_UNAVAILABLE")
        for root in self.roots:
            item = {"root": str(root), "exists": root.is_dir(), "models": [], "nodes": [], "workflows": []}
            if root.is_dir():
                item["disk_free"] = shutil.disk_usage(root).free
                for folder, key, suffixes in [
                    ("models", "models", {".safetensors", ".gguf", ".pt", ".bin"}),
                    ("custom_nodes", "nodes", {".py"}),
                    ("user/default/workflows", "workflows", {".json"}),
                ]:
                    directory = safe_target(root, folder)
                    if not directory.exists():
                        continue
                    for path in directory.rglob("*"):
                        # Symlinks/junctions may not expand the authorized scope.
                        if not path.resolve().is_relative_to(root) or path.is_symlink():
                            continue
                        if path.is_file() and path.suffix in suffixes:
                            entry = {
                                "path": str(path.relative_to(root)),
                                "bytes": path.stat().st_size,
                                "sha256": None,
                                "verification": "HASH_NOT_REQUESTED",
                            }
                            if key == "workflows":
                                data = path.read_bytes() if path.stat().st_size <= 5 * 1024**2 else b""
                                entry["sha256"] = hashlib.sha256(data).hexdigest() if data else None
                                try:
                                    graph = json.loads(data)
                                    entry["format"] = "UI" if "nodes" in graph else "API"
                                except (ValueError, TypeError):
                                    entry["format"] = "INVALID"
                            item[key].append(entry)
            result["roots"].append(item)
        for port in self.ports:
            base = f"http://127.0.0.1:{port}"
            require_loopback_url(base)
            instance = {"url": base, "health": "OFFLINE", "evidence": {}}
            for endpoint in ("system_stats", "object_info", "queue", "history"):
                try:
                    response = self.http.get(base + "/" + endpoint)
                    response.raise_for_status()
                    instance["evidence"][endpoint] = response.json()
                except (httpx.HTTPError, ValueError):
                    instance["evidence"][endpoint] = {"unavailable": True}
            if not instance["evidence"]["system_stats"].get("unavailable"):
                instance["health"] = "ONLINE"
            result["instances"].append(instance)
        return result


class FakeWindowsHostProbe:
    def scan(self):
        return {
            "source": "FakeWindowsHostProbe",
            "is_mock": True,
            "os": "Windows simulated",
            "gpu": [{"name": "Fake RTX 5080", "total_mb": 16384, "free_mb": 16384, "measurement": "SIMULATED"}],
            "memory_mb": 32768,
            "disk_free": 200 * 1024**3,
            "python": "3.12 simulated",
            "roots": [],
            "coverage": ["MOCK_VERIFIED"],
            "instances": [
                {"url": "http://127.0.0.1:8188", "health": "OFFLINE", "is_mock": True},
                {"url": "http://127.0.0.1:8189", "health": "SIMULATED_ONLINE", "is_mock": True},
            ],
        }


class ComfyModelLifecycle:
    """Ask an idle allowlisted local instance to unload; never terminate its process."""

    def __init__(self, base_url, *, client=None):
        self.base_url = require_loopback_url(base_url).rstrip("/")
        self.http = client or httpx.Client(timeout=10, follow_redirects=False)

    def release_models(self):
        from shine.comfy.client import ComfyError, ComfyUIClient

        client = ComfyUIClient(self.base_url, max_retries=0)
        from contextlib import nullcontext

        client._client = lambda: nullcontext(self.http)
        try:
            client.free_models()
        except ComfyError as exc:
            raise ValueError(str(exc)) from exc
        return {"status": "RELEASE_CONFIRMED", "measurement": "LOCAL_PROCESS_TORCH_ALLOCATION", "process_killed": False}

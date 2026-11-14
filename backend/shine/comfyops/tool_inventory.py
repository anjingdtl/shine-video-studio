"""The pinned bootstrap tool packages, as one honest inventory.

A *package* is the unit the installer downloads and hashes.  FFmpeg and
FFprobe are two commands of one archive; Node and npm are two commands of one
archive.  Reporting them as three independent "missing programs" is exactly the
defect the new-machine entry had: the user saw repeated blocks without a name,
while the installer would have installed the same zip again.

This module only reads.  It never downloads, never changes PATH and never
starts a process; the manifest is the same pinned file ``bootstrap.ps1`` uses,
so detection and installation cannot disagree about a version or a hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOLS_MANIFEST = REPO_ROOT / "config" / "deployment" / "tools-windows-x64.json"

#: The commands each pinned archive provides.  The order is the installer's
#: order and the display order; it is not a second source of truth.
TOOL_PACKAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("node", ("node", "npm")),
    ("uv", ("uv",)),
    ("ffmpeg", ("ffmpeg", "ffprobe")),
    ("pwsh", ("pwsh",)),
)

PACKAGE_LABELS = {
    "node": "Node.js + npm",
    "uv": "uv",
    "ffmpeg": "FFmpeg + FFprobe",
    "pwsh": "PowerShell 7",
}

TOOL_LABELS = {
    "node": "Node.js",
    "npm": "npm",
    "uv": "uv",
    "ffmpeg": "FFmpeg",
    "ffprobe": "FFprobe",
    "pwsh": "PowerShell 7",
}

PACKAGE_ALREADY_AVAILABLE = "ALREADY_AVAILABLE"
PACKAGE_MISSING = "MISSING"
PACKAGE_STALE = "STALE"
PACKAGE_UNREPAIRABLE = "UNREPAIRABLE"


class ToolManifestError(ValueError):
    """The pinned tool manifest is missing or malformed."""


def load_tools_manifest(path: Path | None = None) -> dict:
    target = Path(path or DEFAULT_TOOLS_MANIFEST)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolManifestError(f"TOOLS_MANIFEST_UNAVAILABLE: {target}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), dict):
        raise ToolManifestError(f"TOOLS_MANIFEST_INVALID: {target}")
    return raw


def manifest_sha256(path: Path | None = None) -> str:
    target = Path(path or DEFAULT_TOOLS_MANIFEST)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def package_of_tool(tool: str) -> str | None:
    for package_id, tools in TOOL_PACKAGES:
        if tool in tools:
            return package_id
    return None


def _tool_rows(tools_report: dict, tool_names: tuple[str, ...]) -> list[dict]:
    rows = []
    for name in tool_names:
        info = (tools_report or {}).get(name) or {}
        present = bool(info.get("present"))
        at_floor = bool(info.get("at_floor"))
        rows.append({
            "tool": name,
            "label": TOOL_LABELS.get(name, name),
            "present": present,
            "version": info.get("version") or "",
            "min_version": info.get("min_version"),
            "at_floor": at_floor,
            "ok": present and at_floor,
            "detectable": bool((tools_report or {}).get(name)),
        })
    return rows


def inspect_packages(tools_report: dict, manifest: dict | None = None) -> list[dict]:
    """Every pinned package, with per-command detail and one package verdict.

    ``installed`` is true only when every command of the package runs and meets
    its floor.  ``auto_repairable`` is true only when the pinned manifest
    really carries an archive for that package; a package without a pinned
    source is reported as unrepaired instead of being retried forever.
    """
    manifest = manifest if manifest is not None else load_tools_manifest()
    entries = manifest.get("tools") or {}
    rows = []
    for package_id, tool_names in TOOL_PACKAGES:
        tools = _tool_rows(tools_report, tool_names)
        missing = [row["tool"] for row in tools if not row["present"]]
        stale = [row["tool"] for row in tools if row["present"] and not row["at_floor"]]
        installed = not missing and not stale
        entry = entries.get(package_id)
        if installed:
            status = PACKAGE_ALREADY_AVAILABLE
        elif entry is None:
            status = PACKAGE_UNREPAIRABLE
        elif missing:
            status = PACKAGE_MISSING
        else:
            status = PACKAGE_STALE
        download = None
        if entry is not None:
            download = {
                "package_id": package_id,
                "version": str(entry.get("version") or ""),
                "url": str(entry.get("url") or ""),
                "sha256": str(entry.get("sha256") or ""),
                "size": int(entry.get("size") or 0),
                "entries": [str(item) for item in (entry.get("entries") or [])],
                "manifest_id": str(manifest.get("manifest_id") or ""),
            }
        rows.append({
            "package_id": package_id,
            "label": PACKAGE_LABELS.get(package_id, package_id),
            "tools": tools,
            "status": status,
            "installed": installed,
            "missing_tools": missing,
            "stale_tools": stale,
            "auto_repairable": status in {PACKAGE_MISSING, PACKAGE_STALE} and download is not None,
            "download": download,
            "download_bytes": int(download["size"]) if download else 0,
            # A successful install writes a shim outside the running API's
            # inherited PATH; detection must search the managed directory.
            "requires_environment_refresh": status in {PACKAGE_MISSING, PACKAGE_STALE},
            "identity": (
                hashlib.sha256(
                    f"{package_id}:{download['version']}:{download['sha256']}".encode()
                ).hexdigest()[:16] if download else None
            ),
        })
    return rows


def repairable_packages(tools_report: dict, manifest: dict | None = None) -> list[dict]:
    """The packages a repair plan may install, in installer order."""
    return [row for row in inspect_packages(tools_report, manifest) if row["auto_repairable"]]


def packages_download_bytes(packages: list[dict]) -> int:
    return sum(int(row.get("download_bytes") or 0) for row in packages)

"""Windows bootstrap entry: pinned-manifest download, hash/size verification, shims.

Uses a file:// zip with real bytes and real hashes so the exact production
algorithm runs without network. Skipped where PowerShell is unavailable.
"""

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap entry")

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "scripts" / "bootstrap.ps1"


def run_bootstrap(manifest_path, install_root):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BOOTSTRAP),
         "-ToolsManifest", str(manifest_path), "-InstallRoot", str(install_root), "-NoPathUpdate"],
        capture_output=True, text=True, timeout=300,
    )


def _report(stdout):
    return json.loads(stdout[stdout.index("{"): stdout.rindex("}") + 1])


def test_installs_pinned_tool_from_local_zip_and_creates_working_shim(tmp_path):
    zip_path = tmp_path / "cache" / "ffmpeg-1.0.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zipped:
        zipped.writestr("ffmpeg-1.0/ffmpeg.cmd", "@echo off\r\necho shim-test-ffmpeg ok\r\n")
    manifest = {
        "manifest_id": "test", "manifest_version": "0.0.1", "generated": "test",
        "tools": {"ffmpeg": {"version": "1.0", "url": "file:///" + zip_path.as_posix(),
                             "size": zip_path.stat().st_size,
                             "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                             "entries": ["ffmpeg.cmd"]}},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_bootstrap(manifest_path, tmp_path / "bin")
    assert result.returncode in {0, 1}  # blockers from the fixed tool list are allowed
    report = _report(result.stdout)
    assert "ffmpeg" in {row["name"] for row in report["installed"]}
    shim = tmp_path / "bin" / "ffmpeg.cmd"
    assert shim.exists()
    executed = subprocess.run([str(shim)], capture_output=True, text=True)
    assert "shim-test-ffmpeg ok" in executed.stdout


def test_hash_mismatch_blocks_install(tmp_path):
    zip_path = tmp_path / "cache" / "broken-1.0.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zipped:
        zipped.writestr("broken-1.0/tool.cmd", "@echo off\r\n")
    manifest = {
        "manifest_id": "test", "manifest_version": "0.0.1", "generated": "test",
        "tools": {"ffmpeg": {"version": "1.0", "url": "file:///" + zip_path.as_posix(),
                             "size": zip_path.stat().st_size, "sha256": "0" * 64,
                             "entries": ["tool.cmd"]}},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_bootstrap(manifest_path, tmp_path / "bin")
    report = _report(result.stdout)
    assert all(row["name"] != "ffmpeg" for row in report["installed"])
    assert any(b["name"] == "ffmpeg" and b["status"] == "install_failed"
               for b in report["blockers"])


def test_pinned_tools_manifest_matches_recorded_pins():
    """The committed tools manifest stays in sync with the upstream pins file."""
    pins = json.loads((REPO / "config" / "deployment" / "_upstream-pins" / "tools.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "config" / "deployment" / "tools-windows-x64.json").read_text(encoding="utf-8"))
    assert manifest["tools"] == pins

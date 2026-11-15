"""工具版本门槛只有一个来源：固定清单的 command_floors。

bootstrap.ps1 与 host_env 曾各自硬编码同一组门槛，改一处即漂移。
现在两侧都读 config/deployment/tools-windows-x64.json 的 command_floors：
探测报告的 min_version、安装器的判定口径与清单逐字一致。
"""

from __future__ import annotations

import json
from pathlib import Path

from shine.comfyops import host_env
from shine.comfyops.tool_inventory import TOOL_PACKAGES, load_tools_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "deployment" / "tools-windows-x64.json"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.ps1"


def test_host_floors_are_exactly_the_manifest_floors():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    floors = host_env._floor_versions()
    for _package_id, tools in TOOL_PACKAGES:
        for tool in tools:
            assert floors[tool] == manifest["command_floors"][tool]


def test_probe_reports_the_manifest_floor():
    manifest = load_tools_manifest()
    result = host_env.probe_command(
        "node", runner=lambda probe: (True, "v20.19.5"), windows=False)
    assert result["min_version"] == manifest["command_floors"]["node"]
    assert result["at_floor"] is True
    stale = host_env.probe_command(
        "node", runner=lambda probe: (True, "v18.20.0"), windows=False)
    assert stale["at_floor"] is False


def test_bootstrap_reads_floors_from_the_manifest():
    text = BOOTSTRAP.read_text(encoding="utf-8-sig")
    assert "command_floors" in text, "bootstrap must read the pinned floors"
    assert '"uv" = "0.9"' not in text, "hardcoded floor table must not return"
    assert '"node" = "20"' not in text
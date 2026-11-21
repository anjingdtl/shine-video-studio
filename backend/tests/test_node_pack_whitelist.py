"""节点白名单按磁盘目录解析 + 受管启动日志落盘与证据 surfaced。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from shine.comfyops.existing_installation import resolve_node_pack_dirs
from shine.comfyops.service_validation import (
    _validate_mode_workflows,
    renderer_log_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

CATALOG = {"components": [
    {"component_id": "ComfyUI-KJNodes", "kind": "node"},
    {"component_id": "comfyui-videohelpersuite", "kind": "node"},
    {"component_id": "ComfyUI-SolAttn_triton", "kind": "node"},
    {"component_id": "ComfyUI-MiniMax-H3-PDD-Acc", "kind": "node"},
    {"component_id": "ComfyUI-NotInstalled", "kind": "node"},
    {"component_id": "some-model", "kind": "model"},
]}


def _root_with(tmp_path, names):
    custom = tmp_path / "custom_nodes"
    custom.mkdir(parents=True, exist_ok=True)
    for name in names:
        (custom / name).mkdir()
    return tmp_path


def test_resolver_maps_upstream_directory_names(tmp_path):
    root = _root_with(tmp_path, ["KJNodes", "VideoHelperSuite", "SolAttn_triton",
                                 "ComfyUI-MiniMax-H3-PDD-Acc", "UnrelatedPack"])
    mapping, missing = resolve_node_pack_dirs(root, CATALOG)
    assert mapping == {
        "ComfyUI-KJNodes": "KJNodes",
        "comfyui-videohelpersuite": "VideoHelperSuite",
        "ComfyUI-SolAttn_triton": "SolAttn_triton",
        "ComfyUI-MiniMax-H3-PDD-Acc": "ComfyUI-MiniMax-H3-PDD-Acc",
    }
    assert missing == ["ComfyUI-NotInstalled"]


def test_resolver_exact_name_wins_and_ambiguity_is_missing(tmp_path):
    root = _root_with(tmp_path, ["ComfyUI-KJNodes", "KJNodes", "kjnodes-copy"])
    mapping, missing = resolve_node_pack_dirs(root, CATALOG)
    assert mapping["ComfyUI-KJNodes"] == "ComfyUI-KJNodes"
    root2 = _root_with(tmp_path / "two", ["KJNodes", "kj-nodes"])
    mapping2, missing2 = resolve_node_pack_dirs(root2, CATALOG)
    assert "ComfyUI-KJNodes" in missing2


def test_contract_failure_carries_renderer_log_evidence(tmp_path):
    log = tmp_path / "renderer.log"
    log.write_text("loading nodes\nCannot import /x/KJNodes module for custom nodes: "
                   "ModuleNotFoundError: triton\nTraceback line\n", encoding="utf-8")
    evidence = renderer_log_evidence(str(log))
    assert evidence["renderer_log"] == str(log)
    assert any("Cannot import" in line for line in evidence["import_errors"])
    with pytest.raises(HTTPException) as exc:
        _validate_mode_workflows({}, log_evidence=evidence)
    assert exc.value.detail["code"] == "CORE_MODE_NODES_MISSING"
    assert exc.value.detail["renderer_log"] == str(log)
    assert exc.value.detail["import_errors"]


def test_bootstrap_redirects_import_log_to_disk(tmp_path):
    log = tmp_path / "logs" / "renderer-test.log"
    env = {
        "SHINE_COMFY_LOG_FILE": str(log),
        "SHINE_COMFY_BOOTSTRAP_ROOT": "",  # 配置无效 → 启动即失败，失败也要落盘
        "SHINE_COMFY_ADAPTER_FILE": "",
        "SHINE_COMFY_EXPECTED_ADAPTER_SHA256": "",
        "SHINE_COMFY_START_DRAINED": "0",
        "SHINE_COMFY_HANDOFF_TOKEN": "",
    }
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "managed_comfy_bootstrap.py")],
        capture_output=True, text=True, env={**dict(__import__("os").environ), **env},
        timeout=60,
    )
    assert result.returncode != 0
    assert log.is_file()
    assert "COMFY_BOOTSTRAP_CONFIGURATION_INVALID" in log.read_text(encoding="utf-8")
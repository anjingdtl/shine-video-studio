"""Runtime loader for the build-generated copilot contracts (V2.1).

The running installation reads exactly three packaged files:

* ``shine/support/generated/copilot_contracts.json`` — knowledge index and
  capability registry produced by the build;
* ``shine/support/knowledge_manifest.json`` — the build identity binding;
* ``BUILD_INFO.json`` / ``SHINE_BUILD_COMMIT`` — the installed version.

It never scans the worktree, never regenerates contracts and never trusts a
contract that disagrees with the packaged declarations.  Disagreement degrades
*only the affected* action; a stale knowledge entry must not lock the whole
product, and an unavailable contract must never grant execution.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from shine.copilot import declarations as decl
from shine.copilot.contracts import canonical, digest

ARTIFACT_PATH = Path(__file__).with_name("generated") / "copilot_contracts.json"
MANIFEST_PATH = Path(__file__).with_name("knowledge_manifest.json")
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")

#: Execution states for one capability.  Only AVAILABLE may be dispatched.
EXECUTION_STATES = {
    "AVAILABLE": "受控合同与当前构建一致",
    "CONTRACT_STALE": "合同与当前构建不一致，拒绝执行并要求重新确认",
    "CONTRACT_REVIEW_REQUIRED": "已收录但未通过可执行合同审核",
    "CONTRACT_NOT_DECLARED": "当前构建没有这个受控动作",
    "CONTRACT_UNAVAILABLE": "构建合同产物缺失或损坏",
}

_NOTICES = {
    "AVAILABLE": "AI 管家知识已匹配当前版本。",
    "CONTRACT_STALE": "这个操作在当前版本已经调整，请重新确认。",
    "CONTRACT_REVIEW_REQUIRED": "当前版本暂不支持由 AI 代为执行，但可以在原页面操作。",
    "CONTRACT_NOT_DECLARED": "当前版本没有这个操作的受控合同，请在原页面操作。",
    "CONTRACT_UNAVAILABLE": "AI 管家的能力合同未随本版本打包，操作请在原页面完成。",
}

_BUILD_STATE_LABELS = {
    "FRESH": "AI 管家知识已匹配当前版本。",
    "STALE": "发现新版功能，正在使用新版说明；未重新确认的旧操作会被拒绝。",
    "UNKNOWN": "无法确认知识版本与当前构建一致，回答会明确标注 UNKNOWN。",
    False: "AI 管家的能力合同未随本版本打包，操作请在原页面完成。",
}


def build_commit_from_env() -> str | None:
    """The installed build's commit, when the package injected it.

    ``SHINE_BUILD_COMMIT`` wins; otherwise ``BUILD_INFO.json`` at the package or
    repository root is read.  A source checkout without either is UNKNOWN.
    """
    value = (os.environ.get("SHINE_BUILD_COMMIT") or "").strip()
    if COMMIT_RE.fullmatch(value):
        return value
    root = Path(__file__).resolve().parents[3]
    for candidate in (root / "BUILD_INFO.json", root.parent / "BUILD_INFO.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        commit = str(payload.get("source_commit") or "").strip()
        if COMMIT_RE.fullmatch(commit):
            return commit
    return None


@lru_cache(maxsize=8)
def _load(path_str: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path_str).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def load_artifact(path: Path | str | None = None) -> dict[str, Any] | None:
    return _load(str(path or ARTIFACT_PATH))


def load_manifest(path: Path | str | None = None) -> dict[str, Any]:
    try:
        return json.loads(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def entries(artifact: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    value = (artifact or load_artifact() or {}).get("knowledge") or {}
    rows = value.get("entries")
    return list(rows) if isinstance(rows, list) else []


def capabilities(artifact: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = (artifact or load_artifact() or {}).get("capabilities")
    return list(rows) if isinstance(rows, list) else []


def read_capabilities(artifact: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = (artifact or load_artifact() or {}).get("read_capabilities")
    return list(rows) if isinstance(rows, list) else []


def live_contract(action_id: str) -> dict[str, Any] | None:
    """The packaged declaration for this action (never a network/DB lookup)."""
    row = decl.write_action(action_id) or decl.read_action(action_id)
    if row is None:
        return None
    return {
        "action_id": action_id,
        "contract_digest": row.contract_digest(),
        "contract_revision": getattr(row, "contract_revision", 1),
        "availability": row.availability,
        "dispatch": getattr(row, "dispatch", None) or action_id,
        "api_route": row.api_route,
        "executor": row.executor,
    }


def live_contract_digest(action_id: str) -> str | None:
    row = decl.write_action(action_id) or decl.read_action(action_id)
    return row.contract_digest() if row is not None else None


def artifact_contract(action_id: str, artifact: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for row in capabilities(artifact):
        if row.get("action_id") == action_id:
            return row
    return None


def _artifact_build_matches(bundle: dict[str, Any], build_commit: str | None) -> bool:
    recorded = str(bundle.get("source_commit") or "")
    observed = build_commit if build_commit is not None else build_commit_from_env()
    if not recorded or not observed:
        # A development checkout is "unproven", not "wrong": execution stays
        # allowed only because the packaged contract itself is self-consistent
        # with the packaged declarations.
        return True
    return recorded == observed


def execution_state(
    action_id: str,
    *,
    artifact: dict[str, Any] | None = None,
    build_commit: str | None = None,
) -> dict[str, Any]:
    """Precise, per-action execution gate.  Never a global lock."""
    bundle = artifact if artifact is not None else load_artifact()
    if not bundle:
        return {
            "action_id": action_id,
            "state": "CONTRACT_UNAVAILABLE",
            "notice": _NOTICES["CONTRACT_UNAVAILABLE"],
            "executable": False,
        }
    packaged = artifact_contract(action_id, bundle)
    if packaged is None:
        return {
            "action_id": action_id,
            "state": "CONTRACT_NOT_DECLARED",
            "notice": _NOTICES["CONTRACT_NOT_DECLARED"],
            "executable": False,
        }
    if not _artifact_build_matches(bundle, build_commit):
        return {
            "action_id": action_id,
            "state": "CONTRACT_STALE",
            "notice": _NOTICES["CONTRACT_STALE"],
            "executable": False,
            "reason": f"产物来自 {str(bundle.get('source_commit'))[:8]}，当前构建不是同一版本。",
        }
    current = live_contract(action_id)
    if current is None:
        return {
            "action_id": action_id,
            "state": "CONTRACT_NOT_DECLARED",
            "notice": _NOTICES["CONTRACT_NOT_DECLARED"],
            "executable": False,
        }
    if packaged.get("contract_digest") != current["contract_digest"]:
        return {
            "action_id": action_id,
            "state": "CONTRACT_STALE",
            "notice": _NOTICES["CONTRACT_STALE"],
            "executable": False,
            "reason": "打包合同与当前声明不一致；拒绝把旧授权应用到新合同。",
        }
    if packaged.get("availability") != "AVAILABLE" or current["availability"] != "AVAILABLE":
        return {
            "action_id": action_id,
            "state": "CONTRACT_REVIEW_REQUIRED",
            "notice": _NOTICES["CONTRACT_REVIEW_REQUIRED"],
            "executable": False,
            "availability": packaged.get("availability"),
        }
    return {
        "action_id": action_id,
        "state": "AVAILABLE",
        "notice": _NOTICES["AVAILABLE"],
        "executable": True,
        "contract_digest": packaged.get("contract_digest"),
        "contract_revision": packaged.get("contract_revision"),
        "dispatch": packaged.get("dispatch") or action_id,
        "api_route": packaged.get("api_route"),
        "authority": packaged.get("authority"),
    }


def contract_binding(action_id: str) -> dict[str, Any]:
    """The block stored on a preview/authorization (durable, non-secret)."""
    state = execution_state(action_id)
    return {
        "action_id": action_id,
        "state": state["state"],
        "contract_digest": state.get("contract_digest"),
        "contract_revision": state.get("contract_revision"),
        "dispatch": state.get("dispatch") or action_id,
        "api_route": state.get("api_route"),
    }


def build_identity(
    *,
    artifact: dict[str, Any] | None = None,
    manifest_path: Path | str | None = None,
    build_commit: str | None = None,
) -> dict[str, Any]:
    """One build identity for knowledge + contracts + installed version."""
    bundle = artifact if artifact is not None else load_artifact()
    manifest = load_manifest(manifest_path)
    observed = build_commit if build_commit is not None else build_commit_from_env()
    reasons: list[str] = []

    if not bundle:
        state = "MISSING"
        reasons.append("能力/知识合同产物缺失，无法证明它们属于当前构建。")
    elif not manifest:
        state = "UNKNOWN"
        reasons.append("知识清单文件缺失，无法证明与当前构建一致。")
    else:
        recorded_knowledge = str(manifest.get("knowledge_version") or "")
        recorded_digest = str(manifest.get("content_sha256") or "")
        recorded_contracts = str(manifest.get("contracts_digest") or "")
        if recorded_knowledge != str(bundle.get("knowledge_version") or ""):
            state = "STALE"
            reasons.append("知识清单版本与打包产物不一致。")
        elif recorded_digest and recorded_digest != str(bundle.get("knowledge", {}).get("content_sha256") or ""):
            state = "STALE"
            reasons.append("知识清单内容指纹与打包产物不一致。")
        elif recorded_contracts and recorded_contracts != str(bundle.get("digests", {}).get("contracts") or ""):
            state = "STALE"
            reasons.append("能力合同指纹与打包产物不一致。")
        else:
            recorded_commit = str(manifest.get("source_commit") or "")
            if not recorded_commit:
                state = "UNKNOWN"
                reasons.append("知识清单未写入来源提交号（开发检出），无法证明属于当前代码。")
            elif not observed:
                state = "UNKNOWN"
                reasons.append("当前构建未注入提交号，无法证明知识版本与当前代码一致。")
            elif recorded_commit != observed:
                state = "STALE"
                reasons.append(
                    f"知识清单与当前构建不一致（清单来自 {recorded_commit[:8]}，当前构建为 {observed[:8]}）。"
                )
            else:
                state = "FRESH"

    notices = []
    if state == "FRESH":
        notices.append("AI 管家知识已匹配当前版本。")
    else:
        notices.extend(reasons)
    return {
        "state": state,
        "fresh": state == "FRESH",
        "app_version": (bundle or {}).get("app_version"),
        "contract_version": (bundle or {}).get("version"),
        "knowledge_version": (bundle or {}).get("knowledge_version"),
        "source_commit": str(manifest.get("source_commit") or "") or None,
        "build_commit": observed,
        "contracts_digest": (bundle or {}).get("digests", {}).get("contracts"),
        "api_schema_digest": (bundle or {}).get("digests", {}).get("api_schema"),
        "capability_count": len(capabilities(bundle)),
        "read_capability_count": len(read_capabilities(bundle)),
        "knowledge_entry_count": len(entries(bundle)),
        "reasons": reasons,
        "notices": notices,
        "notice": "；".join(notices) or _BUILD_STATE_LABELS[state],
        "user_notice": _BUILD_STATE_LABELS.get(state) or _BUILD_STATE_LABELS[False],
        "artifact_path": _display_path(ARTIFACT_PATH),
        "manifest_path": _display_path(MANIFEST_PATH),
    }


def _display_path(path: Path) -> str:
    """A stable, non-absolute label; never fails for a swapped temp artefact."""
    root = Path(__file__).resolve().parents[1]
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def live_api_digest(app_routes: list[tuple[str, str]]) -> str:
    """Fingerprint of the routes this process actually serves."""
    from shine.copilot.build import api_schema_digest

    return api_schema_digest(app_routes)


def bundle_fingerprint(bundle: dict[str, Any]) -> str:
    return digest(canonical(bundle))


def reset_cache() -> None:
    """Tests swap artefacts; the production process never calls this."""
    _load.cache_clear()


__all__ = [
    "ARTIFACT_PATH",
    "MANIFEST_PATH",
    "EXECUTION_STATES",
    "artifact_contract",
    "build_commit_from_env",
    "build_identity",
    "capabilities",
    "contract_binding",
    "entries",
    "execution_state",
    "live_api_digest",
    "live_contract",
    "live_contract_digest",
    "load_artifact",
    "read_capabilities",
    "reset_cache",
]

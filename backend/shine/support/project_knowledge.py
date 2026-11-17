# ruff: noqa: E501 - curated data tables keep one row per real operation
"""Versioned, offline project knowledge for the Shine copilot.

V2.1: the index is no longer a hand-kept list in this module.  It is generated
from ``shine/copilot/declarations.py`` by the build and loaded from
``shine/support/generated/copilot_contracts.json``; the build identity
(``knowledge_manifest.json`` + ``BUILD_INFO.json``) binds it to the installed
version.

Every entry names its source files, its public API and the automated evidence
behind it, and carries an explicit status that separates *implemented*,
*partially implemented*, *planned*, *blocked* and *not supported*.  A design
document never becomes "already shipped" because it is in the same repository.

When the running build cannot prove it matches, the answer is still produced but
is explicitly marked UNKNOWN/STALE - the copilot never presents outdated prose
as current fact.  A damaged index degrades to the packaged declarations with an
UNKNOWN notice instead of crashing the copilot.

Retrieval is a small deterministic BM25-like score over the catalogue.  It uses
no embedding model, no GPU and no network, so opening the copilot never steals
VRAM from a render and never scans the worktree.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shine.copilot import declarations as decl
from shine.copilot.build import KNOWLEDGE_VERSION, knowledge_content_digest, knowledge_entries
from shine.copilot.contracts import IMPLEMENTATION_STATUSES, VERIFICATION_STATUSES
from shine.support import contracts as contract_registry

MANIFEST_PATH = contract_registry.MANIFEST_PATH

#: Evidence levels, best first.  The answer shows this so a person can tell a
#: product fact from an official upstream reference.
EVIDENCE_LEVELS = (
    "PRODUCT_FACT",
    "VERSIONED_PROJECT_DOCS",
    "OFFICIAL_REFERENCE",
    "COMMUNITY_REFERENCE",
)

_STATUS_LABELS = dict(IMPLEMENTATION_STATUSES)
_VERIFICATION_LABELS = dict(VERIFICATION_STATUSES)


def _load_entries() -> list[dict[str, Any]]:
    artifact = contract_registry.load_artifact()
    rows = contract_registry.entries(artifact)
    if rows:
        return rows
    # Damaged/missing artefact: answering from the packaged declarations is
    # still safe (descriptive only) and is reported as UNKNOWN, never FRESH.
    return knowledge_entries(decl.FEATURES, source_commit="")


#: The runtime catalogue (kept as a module constant for callers/tests).
PROJECT_ENTRIES: list[dict[str, Any]] = _load_entries()


def _content_digest() -> str:
    return knowledge_content_digest(PROJECT_ENTRIES)


build_commit_from_env = contract_registry.build_commit_from_env


def index_metadata(
    build_commit: str | None = None, manifest_path: Path | None = None
) -> dict[str, Any]:
    """Version binding between this knowledge and the running build."""
    identity = contract_registry.build_identity(
        manifest_path=manifest_path or MANIFEST_PATH,
        build_commit=build_commit,
    )
    state = identity.get("state")
    if state == "MISSING":
        state = "UNKNOWN"
    reasons = list(identity.get("reasons") or [])
    if state == "UNKNOWN" and not reasons:
        reasons.append("知识清单文件缺失，无法证明与当前构建一致。")
    fresh = state == "FRESH"
    return {
        "knowledge_version": KNOWLEDGE_VERSION,
        "source_commit": identity.get("source_commit"),
        "build_commit": identity.get("build_commit"),
        "content_sha256": _content_digest(),
        "contracts_digest": identity.get("contracts_digest"),
        "api_schema_digest": identity.get("api_schema_digest"),
        "app_version": identity.get("app_version"),
        "state": state,
        "fresh": fresh,
        "reasons": reasons,
        "indexed_entry_count": len(PROJECT_ENTRIES),
        "manifest_path": "backend/shine/support/knowledge_manifest.json",
        "notice": (
            "知识索引与当前构建一致。"
            if fresh
            else "；".join(reasons) or "无法证明知识索引与当前构建一致。"
        ),
        "user_notice": identity.get("user_notice"),
        "evidence_level": "VERSIONED_PROJECT_DOCS",
    }


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}")


def _tokens(text: str) -> list[str]:
    lowered = (text or "").casefold()
    tokens = _TOKEN_RE.findall(lowered)
    # Chinese has no spaces: also match every 2-character window of a run.
    extra: list[str] = []
    for token in _TOKEN_RE.findall(text or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            extra.extend(token[i : i + 2] for i in range(0, len(token) - 1))
    return list(dict.fromkeys(tokens + extra))[:24]


def _score(entry: dict[str, Any], tokens: list[str]) -> int:
    fields = {
        "id": 4,
        "title": 6,
        "tags": 4,
        "summary": 2,
        "module": 3,
        "sources": 1,
        "apis": 1,
    }
    score = 0
    for token in tokens:
        for field, weight in fields.items():
            value = entry.get(field)
            haystack = " ".join(value) if isinstance(value, list) else str(value or "")
            if token and token in haystack.casefold():
                score += weight
    return score


def search(query: str, *, limit: int = 5, build_commit: str | None = None) -> dict[str, Any]:
    """Search the versioned catalogue; no network, no GPU, no worktree scan."""
    tokens = _tokens(query)
    scored = []
    for entry in PROJECT_ENTRIES:
        score = _score(entry, tokens) if tokens else 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda row: (-row[0], row[1]["id"]))
    metadata = index_metadata(build_commit)
    hits = [
        {
            **entry,
            "score": score,
            "citation": {
                "source_paths": entry["sources"],
                "source_commit": metadata.get("source_commit"),
                "knowledge_version": KNOWLEDGE_VERSION,
                "evidence_level": "VERSIONED_PROJECT_DOCS",
            },
        }
        for score, entry in scored[: max(1, min(limit, 10))]
    ]
    return {
        "query": (query or "")[:200],
        "metadata": metadata,
        "results": hits,
        "answered": bool(hits),
        "unknown_reason": None if hits else "项目知识索引中没有与问题匹配的条目。",
        "notice": metadata["notice"],
    }


def coverage() -> dict[str, Any]:
    """Counts per status for the acceptance report and the UI."""
    counts: dict[str, int] = {status: 0 for status in _STATUS_LABELS}
    for entry in PROJECT_ENTRIES:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "knowledge_version": KNOWLEDGE_VERSION,
        "total": len(PROJECT_ENTRIES),
        "by_status": counts,
        "status_labels": _STATUS_LABELS,
        "entries": [
            {
                "id": entry["id"],
                "title": entry["title"],
                "module": entry["module"],
                "status": entry["status"],
                "verification": entry["verification"],
                "sources": entry["sources"],
            }
            for entry in PROJECT_ENTRIES
        ],
    }


def answer(question: str, *, limit: int = 3, build_commit: str | None = None) -> dict[str, Any]:
    """A knowledge answer with citations and an explicit applicability note."""
    result = search(question, limit=limit, build_commit=build_commit)
    citations = [
        {
            "title": hit["title"],
            "status": hit["status_label"],
            "summary": hit["summary"],
            "sources": hit["sources"],
            "apis": hit["apis"],
            "verification": hit["verification_label"],
            "knowledge_version": hit["knowledge_version"],
        }
        for hit in result["results"]
    ]
    return {
        "intent": "KNOWLEDGE",
        "answered": bool(citations),
        "answer_lines": [
            f"【{row['status']}】{row['title']}：{row['summary']}" for row in citations
        ],
        "citations": citations,
        "metadata": result["metadata"],
        "unknown_reason": result["unknown_reason"],
        "notice": result["notice"],
        "next_step": (
            "如需操作，可以让管家先给出计划，再由你确认；需要真机验证的能力不会因为文档存在就被宣称可用。"
            if citations
            else "这个问题在当前版本的项目知识中没有依据；请换一种说法或生成工程报告。"
        ),
    }

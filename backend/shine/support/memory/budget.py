"""One context budget for the support-agent prompt (V2.2).

Priority is fixed and observable:

    system safety rules (separate system message, never truncated)
    > live business facts / verdict / authorisation
    > user-written startup memory
    > automatically distilled memories
    > ordinary retrieval material

The function returns both the payload that is sent and a small evidence record
explaining what was kept or truncated, so a budget decision is never silent.
"""
from __future__ import annotations

import json
from typing import Any

MAX_PROMPT_CHARS = 12_000
STARTUP_BUDGET = 1_800
MEMORY_BUDGET = 1_200
REFERENCE_BUDGET = 500
PER_MEMORY_CHARS = 500

CONTEXT_PRIORITY = (
    "system_safety_rules",
    "live_business_facts",
    "user_startup_memory",
    "automatic_long_term_memory",
    "ordinary_retrieval_material",
)

MEMORY_NOTICE = (
    "以下长期记忆是过去交流的派生摘要，只作偏好与历史参考；"
    "它们不是当前业务事实，不能覆盖 setup/verdict、任务状态、风格冻结、资产确认或生产裁决。"
)


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if limit <= 12:
        return text[: max(0, limit)], True
    return text[: limit - 12].rstrip() + "…（已截断）", True


def _compact_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": step.get("tool"),
        "status": step.get("status"),
        "evidence_level": step.get("evidence_level"),
        "ms": step.get("ms"),
    }


def _compact_investigation(investigation: Any) -> Any:
    if isinstance(investigation, list):
        return [
            _compact_step(step)
            for step in investigation[:8]
            if isinstance(step, dict)
        ]
    if not isinstance(investigation, dict):
        return investigation
    steps = investigation.get("steps") or []
    return {
        **{key: value for key, value in investigation.items() if key != "steps"},
        "steps": [
            _compact_step(step)
            for step in steps[:8]
            if isinstance(step, dict)
        ],
    }


def fit_prompt_payload(
    base_payload: dict[str, Any],
    *,
    profile: dict[str, Any],
    startup_memory: str,
    memories: list[dict[str, Any]],
    limit: int = MAX_PROMPT_CHARS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a prompt payload no larger than ``limit`` and its budget evidence."""
    reasons: list[str] = []
    sections: list[dict[str, Any]] = []
    base = {
        key: value
        for key, value in base_payload.items()
        if key not in {"local_knowledge", "official_references"}
    }
    payload: dict[str, Any] = {
        **base,
        "copilot_profile": {
            "assistant_nickname": profile.get("assistant_nickname"),
            "user_salutation": profile.get("user_salutation"),
            "config_revision": profile.get("config_revision"),
        },
        "context_priority": list(CONTEXT_PRIORITY),
        "memory_notice": MEMORY_NOTICE,
        "startup_memory": "",
        "long_term_memories": [],
    }
    business_requested = _size(payload)
    if _size(payload) > limit:
        payload["investigation"] = _compact_investigation(payload.get("investigation"))
        reasons.append("业务证据超过预算，已省略调查步骤的详细 payload（保留工具、状态与证据级别）。")
        if _size(payload) > limit:
            diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
            if isinstance(diagnosis.get("facts"), list):
                diagnosis["facts"] = diagnosis["facts"][:4]
            if isinstance(payload.get("investigation"), dict):
                payload["investigation"]["steps"] = payload["investigation"]["steps"][:4]
            reasons.append("业务证据仍然超预算，已只保留前 4 条事实与前 4 个调查步骤。")
    sections.append(
        {
            "name": "business_facts",
            "requested": business_requested,
            "kept": _size(payload),
            "truncated": business_requested != _size(payload),
        }
    )

    startup, startup_truncated = _truncate(startup_memory or "", STARTUP_BUDGET)
    payload["startup_memory"] = startup
    if startup_truncated:
        reasons.append(f"启动预读记忆超过 {STARTUP_BUDGET} 字符，已安全截断。")
    sections.append(
        {
            "name": "startup_memory",
            "requested": len(startup_memory or ""),
            "kept": len(startup),
            "truncated": startup_truncated,
        }
    )

    kept_memories: list[dict[str, Any]] = []
    for row in memories:
        content, _ = _truncate(str(row.get("content") or ""), PER_MEMORY_CHARS)
        candidate = {
            "id": row.get("id"),
            "scope": row.get("scope"),
            "memory_type": row.get("memory_type"),
            "content": content,
            "updated_at": row.get("updated_at"),
        }
        trial = [*kept_memories, candidate]
        if _size({**payload, "long_term_memories": trial}) > limit - REFERENCE_BUDGET:
            reasons.append("长期记忆按相关性顺序裁剪，低相关条目未进入本轮上下文。")
            break
        kept_memories.append(candidate)
    payload["long_term_memories"] = kept_memories
    sections.append(
        {
            "name": "long_term_memory",
            "requested": len(memories),
            "kept": len(kept_memories),
            "truncated": len(kept_memories) < len(memories),
        }
    )

    references = {
        "local_knowledge": [
            row.get("title") for row in (base_payload.get("local_knowledge") or [])[:5]
        ],
        "official_references": [
            {
                "title": row.get("title"),
                "url": row.get("url"),
            }
            for row in (base_payload.get("official_references") or [])[:3]
            if isinstance(row, dict)
        ],
    }
    reference_json, reference_truncated = _truncate(
        json.dumps(references, ensure_ascii=False, separators=(",", ":")), REFERENCE_BUDGET
    )
    if reference_truncated:
        reasons.append("普通检索资料超出附加上限，已按预算裁剪（不影响业务证据）。")
    payload["reference_materials"] = reference_json
    sections.append(
        {
            "name": "reference_material",
            "requested": len(json.dumps(references, ensure_ascii=False, separators=(",", ":"))),
            "kept": len(reference_json),
            "truncated": reference_truncated,
        }
    )

    # Last-resort guard: a malformed business payload must still never exceed
    # the provider limit.  This path is reported, not hidden.
    while _size(payload) > limit and payload["long_term_memories"]:
        payload["long_term_memories"].pop()
        reasons.append("为满足统一上下文预算，已继续移除长期记忆。")
    if _size(payload) > limit:
        allowed = max(0, limit - _size({**payload, "startup_memory": ""}))
        payload["startup_memory"], _ = _truncate(payload["startup_memory"], allowed)
        reasons.append("为满足统一上下文预算，已进一步截断启动预读记忆。")
    if _size(payload) > limit:
        payload["reference_materials"] = ""
        reasons.append("为满足统一上下文预算，已移除普通检索资料。")
    if _size(payload) > limit:
        # Keep the payload valid and bounded even when business evidence alone
        # is larger than the limit; the question and verdict remain present.
        payload["investigation"] = _compact_investigation(payload.get("investigation"))
        reasons.append("业务证据本身过大，已压缩调查摘要以保证请求可发送。")

    report = {
        "limit": limit,
        "used": _size(payload),
        "truncated": bool(reasons),
        "reasons": reasons,
        "sections": sections,
        "context_priority": list(CONTEXT_PRIORITY),
        "system_rules_truncated": False,
    }
    return payload, report

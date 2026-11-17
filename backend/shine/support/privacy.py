"""Privacy boundary for everything the copilot shows, stores or exports.

A support answer may quote logs and paths, so redaction is applied once here and
used by the evidence projection, the LLM prompt and the Markdown report.  Keys,
tokens, user home directories and the machine's private data roots never leave
the process.
"""
from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:hf|ghp|github_pat)_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*[\"']?[^\s\"',;}]{4,}"),
)

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "api_key_ref",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "operator_token",
        "operator_token_sha256",
    }
)

_HOME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+"),
    re.compile(r"(?i)(?<![\w.])/home/[^/\s\"']+"),
    re.compile(r"(?i)(?<![\w.])/Users/[^/\s\"']+"),
)

_MAX_TEXT = 4000


def register_private_roots(roots: list[str | None]) -> tuple[str, ...]:
    """Return the roots that must never appear verbatim in support output."""
    return tuple(str(root) for root in roots if root)


def redact_text(value: Any, *, private_roots: tuple[str, ...] = (), limit: int = _MAX_TEXT) -> str:
    text = "" if value is None else str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<已脱敏>", text)
    for pattern in _HOME_PATTERNS:
        text = pattern.sub("<用户目录>", text)
    for root in private_roots:
        if root and root in text:
            text = text.replace(root, "<本机路径>")
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def redact_payload(value: Any, *, private_roots: tuple[str, ...] = (), depth: int = 0) -> Any:
    """Recursively redact a JSON-compatible value, bounding its size."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            name = str(key)
            if name.casefold() in _SENSITIVE_KEYS:
                out[name] = "<已脱敏>"
            else:
                out[name] = redact_payload(item, private_roots=private_roots, depth=depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact_payload(item, private_roots=private_roots, depth=depth + 1) for item in list(value)[:40]]
    if isinstance(value, str):
        return redact_text(value, private_roots=private_roots)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact_text(value, private_roots=private_roots)


def private_roots_for(settings) -> tuple[str, ...]:
    return register_private_roots(
        [
            str(getattr(settings, "data_dir", "") or ""),
            str(getattr(settings, "comfyops_dir", "") or ""),
            str(getattr(settings, "secret_store_dir", "") or ""),
        ]
    )

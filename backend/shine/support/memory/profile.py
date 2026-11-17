"""The single AI-copilot user profile (V2.2).

``AppSetting`` remains the one authority for user configuration.  This module
only defines the contract written under one key, validates it once, and merges
the pre-existing ``support_agent_web_search`` setting into the same response.
There is deliberately no second configuration table.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from shine.models import AppSetting, utcnow
from shine.support.privacy import redact_text

PROFILE_KEY = "copilot_user_profile"
WEB_SEARCH_KEY = "support_agent_web_search"

DEFAULT_NICKNAME = "AI 管家"
DEFAULT_SALUTATION = ""
DEFAULT_STARTUP_MEMORY = ""

MAX_NICKNAME = 20
MAX_SALUTATION = 20
MAX_STARTUP_MEMORY = 4000
MAX_MEMORY_CONTENT = 2000

PROFILE_BOOL_FIELDS = (
    "memory_enabled",
    "auto_memory_enabled",
    "remote_memory_processing_consent",
    "remote_context_consent",
)
PROFILE_TEXT_FIELDS = ("assistant_nickname", "user_salutation", "startup_memory")

DEFAULTS: dict[str, Any] = {
    "assistant_nickname": DEFAULT_NICKNAME,
    "user_salutation": DEFAULT_SALUTATION,
    "startup_memory": DEFAULT_STARTUP_MEMORY,
    "memory_enabled": True,
    "auto_memory_enabled": False,
    "remote_memory_processing_consent": False,
    "remote_context_consent": False,
    "config_revision": 0,
    "updated_at": None,
    "updated_by": None,
}

LIMITS = {
    "assistant_nickname": MAX_NICKNAME,
    "user_salutation": MAX_SALUTATION,
    "startup_memory": MAX_STARTUP_MEMORY,
    "memory_content": MAX_MEMORY_CONTENT,
}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML = re.compile(r"[<>]")
_SCRIPT = re.compile(r"(?is)<\s*/?\s*(?:script|iframe|object|embed|svg|img|link|meta)\b")
_INSTRUCTION = re.compile(
    r"(?is)(?:忽略|无视|绕过|覆盖|跳过).{0,12}(?:规则|指令|系统|限制|权限|门禁)"
    r"|(?:ignore|disregard|override).{0,20}(?:previous|system|all).{0,12}(?:instruction|rule)"
    r"|system\s*prompt|developer\s*mode|jailbreak|越狱"
)


class ProfileInvalid(ValueError):
    """One named, user-readable validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(value: Any, *, limit: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProfileInvalid("INVALID_FIELD", f"{field} 必须是文本。")
    text = unicodedata.normalize("NFKC", value).strip()
    if text and _CONTROL.search(text):
        raise ProfileInvalid("CONTROL_CHARS", f"{field} 含有不可见控制字符。")
    if len(text) > limit:
        raise ProfileInvalid("TOO_LONG", f"{field} 最多 {limit} 个字符。")
    return text


def _reject_unsafe_identity(value: str, *, field: str) -> None:
    if _HTML.search(value):
        raise ProfileInvalid("HTML_NOT_ALLOWED", f"{field} 不能包含 HTML 标签或尖括号。")
    if _SCRIPT.search(value):
        raise ProfileInvalid("HTML_NOT_ALLOWED", f"{field} 不能包含网页标签。")
    if _INSTRUCTION.search(value):
        raise ProfileInvalid("INSTRUCTION_NOT_ALLOWED", f"{field} 不能用来改写系统规则或权限。")
    if redact_text(value, limit=10_000) != value:
        raise ProfileInvalid("SECRET_DETECTED", f"{field} 看起来包含密钥或本机敏感路径，已拒绝保存。")


def validate_nickname(value: Any) -> str:
    text = _text(value, limit=MAX_NICKNAME, field="管家昵称")
    if not text:
        raise ProfileInvalid("NICKNAME_REQUIRED", "管家昵称不能为空。")
    _reject_unsafe_identity(text, field="管家昵称")
    return text


def validate_salutation(value: Any) -> str:
    text = _text(value, limit=MAX_SALUTATION, field="对用户的称呼")
    if text:
        _reject_unsafe_identity(text, field="对用户的称呼")
    return text


def validate_startup_memory(value: Any) -> str:
    text = _text(value, limit=MAX_STARTUP_MEMORY, field="启动预读记忆")
    if _SCRIPT.search(text):
        raise ProfileInvalid("HTML_NOT_ALLOWED", "启动预读记忆不能包含可执行网页标签。")
    if redact_text(text, limit=10_000) != text:
        raise ProfileInvalid("SECRET_DETECTED", "启动预读记忆看起来包含密钥或本机敏感路径，已拒绝保存。")
    return text


def _coerce_profile(raw: Any) -> dict[str, Any]:
    """Read a stored profile without letting a corrupt row lock the copilot."""
    value = raw if isinstance(raw, dict) else {}
    profile = {**DEFAULTS}
    nickname = value.get("assistant_nickname")
    if isinstance(nickname, str) and nickname.strip() and len(nickname.strip()) <= MAX_NICKNAME:
        candidate = nickname.strip()
        if not _HTML.search(candidate) and not _INSTRUCTION.search(candidate):
            profile["assistant_nickname"] = candidate
    salutation = value.get("user_salutation")
    if isinstance(salutation, str) and len(salutation) <= MAX_SALUTATION:
        profile["user_salutation"] = salutation.strip()
    startup = value.get("startup_memory")
    if isinstance(startup, str):
        profile["startup_memory"] = startup[:MAX_STARTUP_MEMORY]
    for key in PROFILE_BOOL_FIELDS:
        if isinstance(value.get(key), bool):
            profile[key] = value[key]
    revision = value.get("config_revision")
    if isinstance(revision, int) and revision >= 0:
        profile["config_revision"] = revision
    profile["updated_at"] = value.get("updated_at") if isinstance(value.get("updated_at"), str) else None
    profile["updated_by"] = value.get("updated_by") if isinstance(value.get("updated_by"), str) else None
    return profile


def read_profile(db) -> dict[str, Any]:
    row = db.get(AppSetting, PROFILE_KEY)
    return _coerce_profile(row.value if row is not None else None)


def web_search_enabled(db) -> bool:
    """The pre-existing switch keeps its original key and meaning."""
    row = db.get(AppSetting, WEB_SEARCH_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return bool(value.get("enabled"))


def preferences_payload(db) -> dict[str, Any]:
    return {
        **read_profile(db),
        "web_search": web_search_enabled(db),
        "limits": LIMITS,
        "defaults": {
            "assistant_nickname": DEFAULT_NICKNAME,
            "user_salutation": DEFAULT_SALUTATION,
            "startup_memory": DEFAULT_STARTUP_MEMORY,
            "memory_enabled": True,
            "auto_memory_enabled": False,
            "remote_memory_processing_consent": False,
            "remote_context_consent": False,
        },
    }


def update_preferences(db, *, updates: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
    """Validate once, then write the profile and the legacy web switch together."""
    allowed = set(PROFILE_TEXT_FIELDS) | set(PROFILE_BOOL_FIELDS) | {"web_search"}
    unknown = set(updates) - allowed
    if unknown:
        raise ProfileInvalid("UNKNOWN_FIELD", f"不支持修改这些配置项：{', '.join(sorted(unknown))}")
    current = read_profile(db)
    next_profile = {**current}
    changed = False
    if "assistant_nickname" in updates:
        nickname = validate_nickname(updates["assistant_nickname"])
        if nickname != current["assistant_nickname"]:
            next_profile["assistant_nickname"] = nickname
            changed = True
    if "user_salutation" in updates:
        salutation = validate_salutation(updates["user_salutation"])
        if salutation != current["user_salutation"]:
            next_profile["user_salutation"] = salutation
            changed = True
    if "startup_memory" in updates:
        startup = validate_startup_memory(updates["startup_memory"])
        if startup != current["startup_memory"]:
            next_profile["startup_memory"] = startup
            changed = True
    for key in PROFILE_BOOL_FIELDS:
        if key in updates:
            value = updates[key]
            if not isinstance(value, bool):
                raise ProfileInvalid("INVALID_FIELD", f"{key} 必须是布尔值。")
            if value != current[key]:
                next_profile[key] = value
                changed = True
    web_changed = False
    if "web_search" in updates:
        value = updates["web_search"]
        if not isinstance(value, bool):
            raise ProfileInvalid("INVALID_FIELD", "web_search 必须是布尔值。")
        web_changed = value != web_search_enabled(db)
    if not changed and not web_changed:
        return preferences_payload(db)

    now = utcnow()
    revision = int(current.get("config_revision") or 0) + 1
    next_profile["config_revision"] = revision
    next_profile["updated_at"] = now.isoformat()
    next_profile["updated_by"] = (actor or "local-user")[:64]
    row = db.get(AppSetting, PROFILE_KEY)
    stored = {
        **next_profile,
        "updated_at": next_profile["updated_at"],
        "updated_by": next_profile["updated_by"],
    }
    if row is None:
        db.add(AppSetting(key=PROFILE_KEY, value=stored))
    else:
        row.value = stored
        row.updated_at = now
    if web_changed:
        web_row = db.get(AppSetting, WEB_SEARCH_KEY)
        web_value = {
            "enabled": bool(updates["web_search"]),
            "updated_at": now.isoformat(),
            "updated_by": (actor or "local-user")[:64],
        }
        if web_row is None:
            db.add(AppSetting(key=WEB_SEARCH_KEY, value=web_value))
        else:
            web_row.value = web_value
            web_row.updated_at = now
    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "SUPPORT_PREFERENCES_UPDATED",
        "support_preference",
        PROFILE_KEY,
        {
            "config_revision": revision,
            "fields": sorted(set(updates)),
            "web_search": web_search_enabled(db) if not web_changed else bool(updates["web_search"]),
            "memory_enabled": next_profile["memory_enabled"],
            "auto_memory_enabled": next_profile["auto_memory_enabled"],
            "remote_memory_processing_consent": next_profile["remote_memory_processing_consent"],
            "remote_context_consent": next_profile["remote_context_consent"],
        },
    )
    db.commit()
    return preferences_payload(db)

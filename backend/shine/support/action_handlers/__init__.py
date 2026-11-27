"""Modular domain registry for Copilot write actions (V4).

Each domain module registers handlers conforming to :class:`ActionHandler`.
Shared orchestration (preview, fingerprint staleness, contract validation,
single-use claim, grant budgeting, audit ledger) stays in :mod:`shine.support.actions`,
while domain facts, preflight, execution, and readback live with their domain.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from shine.support.occupancy import release_occupancy


class ActionBlocked(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ActionStale(ActionBlocked):
    def __init__(self, message: str = "环境事实已变化，请重新预览后再授权。"):
        super().__init__("PREVIEW_STALE", message)


class ActionUnknown(ActionBlocked):
    def __init__(self, message: str = "授权动作不存在。"):
        super().__init__("ACTION_NOT_FOUND", message)


class ActionInvalid(ActionBlocked):
    def __init__(self, message: str):
        super().__init__("INVALID_ACTION_PARAMS", message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def blocked_from_executor(exc: HTTPException) -> ActionBlocked:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("error_code") or f"HTTP_{exc.status_code}")
        message = str(detail.get("message") or detail.get("detail") or code)
        return ActionBlocked(code, message, details=detail)
    text = str(detail or f"HTTP_{exc.status_code}")
    return ActionBlocked(text[:64], text)


def action_occupancy(db, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    from shine.copilot.declarations import NO_GPU_GATE_KINDS

    if kind == "managed_release":
        return release_occupancy(db, only=set(params.get("only") or []) or None)
    if kind in NO_GPU_GATE_KINDS:
        occupancy = resource_occupancy_cached(db)
        return {
            **occupancy,
            "busy": False,
            "advisory_busy": bool(occupancy.get("busy")),
            "advisory_code": occupancy.get("code"),
            "label": "由原入口执行，资源占用仅作提示。",
        }
    return resource_occupancy_cached(db)


def resource_occupancy_cached(db) -> dict[str, Any]:
    from shine.support.occupancy import resource_occupancy

    return resource_occupancy(db)


@dataclass(frozen=True)
class ActionHandler:
    target_facts: Callable[[Any, Any, dict[str, Any], str | None], tuple[dict[str, Any], str | None]]
    preflight: Callable[[Any, Any, dict[str, Any], dict[str, Any]], dict[str, Any]]
    execute: Callable[[Any, Any, Any, Any, dict[str, Any], Any, Any, str, Any], None]
    readback: Callable[[Any, Any, Any], None] | None = None
    rollback: Callable[[Any, Any, Any, str], None] | None = None
    resolve_project: Callable[[Any, dict[str, Any], str | None], str | None] | None = None
    validate_scope: Callable[[Any, dict[str, Any], str | None], None] | None = None


REGISTRY: dict[str, ActionHandler] = {}


def register_handler(kind: str, handler: ActionHandler) -> None:
    REGISTRY[kind] = handler


def get_handler(kind: str) -> ActionHandler | None:
    return REGISTRY.get(kind)


# Populate registry from all five domain modules.
from shine.support.action_handlers import (  # noqa: E402,F401
    asset_center,
    materials,
    production,
    project_literary,
    setup,
)

__all__ = [
    "ActionBlocked",
    "ActionHandler",
    "ActionInvalid",
    "ActionStale",
    "ActionUnknown",
    "REGISTRY",
    "action_occupancy",
    "blocked_from_executor",
    "canonical_json",
    "get_handler",
    "json_sha256",
    "register_handler",
]

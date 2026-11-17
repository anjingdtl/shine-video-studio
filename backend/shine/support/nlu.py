"""Schema-bounded natural-language enrichment for an existing plan.

The model may only fill a *small, closed set* of text fields on actions that are
already registered.  It cannot choose a new action, produce an API path, invent
an id, rewrite the user's novel, or bypass any gate.  When the gateway is not
available the plan is returned unchanged: a missing field stays missing instead
of being guessed.

The fillable field set and its constraints are derived from the one capability
declaration + original request schema, so a new optional text parameter needs
no second hand-kept list here.
"""
from __future__ import annotations

import json
from typing import Any

_SYSTEM = (
    "你是 Shine AI 管家的参数抽取器。只允许为已经注册的动作补齐用户明确说过的文本参数。"
    "禁止选择动作、禁止生成 API 路径、禁止编造项目/集/工作流/素材 ID，禁止改写或补写用户原文。"
    "只返回 JSON 对象；不确定的字段不要返回。"
)


def fillable() -> dict[str, dict[str, Any]]:
    from shine.copilot.params import fillable_contract

    return fillable_contract()


def _allowed(kind: str) -> list[str]:
    return sorted((fillable().get(kind) or {}).keys())


def _schema_for(fields: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, contract in fields.items():
        entry: dict[str, Any] = {"type": "string"}
        if contract.get("enum"):
            entry["enum"] = list(contract["enum"])
        if contract.get("max_length"):
            entry["maxLength"] = int(contract["max_length"])
        if contract.get("min_length"):
            entry["minLength"] = int(contract["min_length"])
        properties[name] = entry
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


def enrich_plan(db, gateway, *, plan: dict[str, Any], text: str) -> dict[str, Any]:
    """Best-effort fill of missing *text* parameters; never invents anything."""
    if gateway is None or not text:
        return plan
    steps = list(plan.get("steps") or [])
    filled_any = False
    for step in steps:
        if step.get("state") != "HUMAN_REQUIRED":
            continue
        action = step.get("action") or {}
        kind = str(action.get("kind") or "")
        fields = fillable().get(kind) or {}
        if not fields:
            continue
        payload = {
            "request": text,
            "action": kind,
            "known_params": action.get("params") or {},
            "missing": plan.get("requires_input") or [],
            "allowed_fields": sorted(fields),
        }
        try:
            response = gateway.complete_for_role(
                db,
                "support_agent",
                system=_SYSTEM,
                user=json.dumps(payload, ensure_ascii=False),
                schema=_schema_for(fields),
            )
        except Exception:  # noqa: BLE001 - the rule-based plan remains authoritative
            return plan
        raw = getattr(response, "text", None) or getattr(response, "content", "")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        params = dict(action.get("params") or {})
        changed = False
        from shine.copilot.params import ParamError, validate_param_value

        for field in fields:
            value = parsed.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                clean = validate_param_value(kind, field, value.strip())
            except ParamError:
                continue
            if field in params and params[field]:
                continue
            params[field] = clean
            changed = True
        if not changed:
            continue
        step["action"] = {**action, "params": params}
        missing = [field for field in plan.get("requires_input") or [] if field not in params]
        plan["requires_input"] = missing
        if not missing and kind == "project_create":
            step["state"] = "READY"
            step["state_label"] = "可执行（仍需预览并确认）"
        filled_any = filled_any or changed
    if filled_any:
        plan["notice"] = (
            str(plan.get("notice") or "")
            + " 已按你的原话补齐文本参数；仍需预览并确认，缺失的 ID 不会被猜测。"
        ).strip()
    return plan

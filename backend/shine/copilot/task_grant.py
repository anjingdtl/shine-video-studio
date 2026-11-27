"""The task-level grant contract ``shine.copilot.task-grant.v1`` (V3.1).

One person, one objective, one click.  After that click the copilot may keep
calling *already reviewed* Shine capabilities inside a frozen boundary without
asking again.  This module is the boundary itself: it holds no database handle,
starts no work and never decides a business outcome.  It answers exactly four
questions, and every answer is a pure function of the grant payload:

* is this grant still usable (status, validity, revocation, digest)?
* does this actor / session / project / installation belong to its scope?
* is this permission, and this capability family, granted?
* does the planned cost still fit the frozen budget?

The single write path lives in :mod:`shine.support.grants`; the single
execution path lives in :func:`shine.support.actions.execute_under_grant`.
A grant is therefore *not* a second task system: it is the frozen envelope the
existing ``SupportAction`` envelope is executed inside.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from shine.copilot.contracts import digest

SCHEMA = "shine.copilot.task-grant.v1"
TOOL_INTENT_SCHEMA = "shine.copilot.tool-intent.v1"

#: Permission names are the contract.  A capability maps onto exactly one of
#: them, so "may this tool run" is a table lookup, never a model judgement.
PERMISSION_KEYS: tuple[str, ...] = (
    "read_project",
    "deep_scan",
    "verify_existing_files",
    "download_models",
    "install_reviewed_node_packages",
    "manage_user_workflow_profiles",
    "configure_managed_runtime",
    "control_owned_processes",
    "manage_project_literary",
    "manage_project_materials",
    "manage_project_production",
    "run_test_inference",
    "delete_user_assets",
    "modify_source_code",
    "arbitrary_shell",
    "arbitrary_http",
)

#: Permissions that can never be granted to the runtime copilot, whatever the
#: person ticks.  They are listed so a request for them is refused by name
#: instead of silently dropped.
PERMANENTLY_FORBIDDEN: frozenset[str] = frozenset(
    {"modify_source_code", "arbitrary_shell", "arbitrary_http", "delete_user_assets"}
)

#: Capability id -> the one permission it needs.  Only capabilities that
#: ``copilot/declarations.py`` actually declares may appear here: a name without
#: a declaration has no reviewed contract and must never be offered.
#:
#: ``setup_repair`` owns *both* missing weights and reviewed node packages (they
#: are two component kinds of the same install plan), so it requires
#: ``download_models``.  The separate ``install_reviewed_node_packages`` flag
#: records that the person accepted third-party executable code; the ``nodes``
#: group grants both.
CAPABILITY_PERMISSIONS: dict[str, str] = {
    # Setup & runtime
    "setup_repair": "download_models",
    "setup_reconcile": "verify_existing_files",
    "resource_install_plan": "download_models",
    "managed_release": "control_owned_processes",
    "support_web_search_update": "read_project",
    "image_runtime_validate": "run_test_inference",
    # Workflows
    "workflow_user_copy": "manage_user_workflow_profiles",
    "workflow_graph_register": "manage_user_workflow_profiles",
    # Project, documents & literary
    "project_create": "manage_project_literary",
    "project_settings_update": "manage_project_literary",
    "document_import_text": "manage_project_literary",
    "document_switch_basis": "manage_project_literary",
    "story_outline_generate": "manage_project_literary",
    "story_outline_confirm": "manage_project_literary",
    "storyboard_book_confirm": "manage_project_literary",
    "literary_scripts_generate": "manage_project_literary",
    "storyboard_generate": "manage_project_literary",
    "storyboard_confirm": "manage_project_literary",
    "shot_revise": "manage_project_literary",
    # Materials & Asset Center & LoRAs
    "material_derive": "manage_project_materials",
    "material_preferences_update": "manage_project_materials",
    "material_generate": "run_test_inference",
    "material_regenerate": "run_test_inference",
    "material_promote": "manage_project_materials",
    "shot_swap_material_binding": "manage_project_materials",
    "asset_center_image_generate": "run_test_inference",
    "asset_center_character_sheet_generate": "run_test_inference",
    "asset_center_version_promote": "manage_project_materials",
    "asset_center_assign_to_project": "manage_project_materials",
    "lora_import_local": "manage_project_materials",
    "lora_update": "manage_project_materials",
    "lora_delete": "manage_project_materials",
    # Package, production, retake, delivery & recovery
    "director_package_create": "manage_project_production",
    "production_start": "run_test_inference",
    "production_control": "manage_project_production",
    "shot_rerender": "run_test_inference",
    "episode_delivery_assemble": "manage_project_production",
    "task_reconcile": "manage_project_production",
    "stale_run_recover": "manage_project_production",
}

#: Capability -> capability group the preview asks the person to confirm.  The
#: UI shows groups, the server checks atomic permissions; the mapping keeps the
#: two from drifting.
GROUP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "inspect": ("read_project", "deep_scan", "verify_existing_files"),
    "resources": ("download_models",),
    "nodes": ("install_reviewed_node_packages", "download_models"),
    "workflows": ("manage_user_workflow_profiles",),
    "runtime": ("configure_managed_runtime", "control_owned_processes"),
    "literary": ("manage_project_literary",),
    "materials": ("manage_project_materials",),
    "production": ("manage_project_production",),
    "test": ("run_test_inference",),
}

DEFAULT_GROUPS: tuple[str, ...] = (
    "inspect",
    "resources",
    "nodes",
    "workflows",
    "runtime",
    "test",
)

PROJECT_AUTONOMOUS_GROUPS: tuple[str, ...] = (
    "inspect",
    "literary",
    "materials",
    "production",
    "workflows",
    "runtime",
    "test",
)

#: Budget keys, their default ceiling and whether raising it needs a reason.
DEFAULT_LIMITS: dict[str, int] = {
    "max_download_bytes": 200_000_000_000,
    "max_disk_write_bytes": 300_000_000_000,
    "max_installed_node_packages": 8,
    "max_new_workflow_profiles": 6,
    "max_test_inference_jobs": 3,
    "max_gpu_minutes": 90,
    "max_paid_api_cost_minor_units": 0,
    "max_llm_tool_calls": 60,
    "max_total_runtime_seconds": 43_200,
}

#: Upper bounds a person may raise a budget to.  Beyond these the request is a
#: configuration error, not a generous authorization.
LIMIT_CEILINGS: dict[str, int] = {
    "max_download_bytes": 2_000_000_000_000,
    "max_disk_write_bytes": 3_000_000_000_000,
    "max_installed_node_packages": 32,
    "max_new_workflow_profiles": 32,
    "max_test_inference_jobs": 16,
    "max_gpu_minutes": 720,
    "max_paid_api_cost_minor_units": 100_000,
    "max_llm_tool_calls": 400,
    "max_total_runtime_seconds": 604_800,
}

DEFAULT_VALIDITY = timedelta(hours=24)
MAX_VALIDITY = timedelta(days=7)
PREVIEW_VALIDITY = timedelta(minutes=30)

#: Statuses that may start a new write action.
AUTHORIZED_STATUSES: frozenset[str] = frozenset({"AUTHORIZED", "RUNNING"})
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "REVOKED"}
)
PAUSED_STATUSES: frozenset[str] = frozenset(
    {
        "PAUSED_SCOPE",
        "PAUSED_UNKNOWN",
        "PAUSED_CONTRACT",
        "PAUSED_USER_DECISION",
        "PAUSED_BUDGET",
    }
)

#: Supply-chain admission.  These are not settings: an authorization cannot
#: widen them, and a payload that tries is rejected as tampered.
SUPPLY_CHAIN = {
    "require_pinned_node_commit": True,
    "require_verified_sha256_and_size": True,
    "allow_unreviewed_executable_packages": False,
    "allow_install_scripts_from_llm": False,
}

STATUS_LABELS: dict[str, str] = {
    "DRAFT": "准备中",
    "PREVIEWED": "等待一次授权",
    "AUTHORIZED": "已授权",
    "RUNNING": "自动执行中",
    "WAITING_EXECUTOR": "等待原任务",
    "VERIFYING": "正在复核",
    "SUCCEEDED": "已成功",
    "PAUSED_SCOPE": "超出本次授权，已停止",
    "PAUSED_UNKNOWN": "原任务结果不明，已停止",
    "PAUSED_CONTRACT": "能力合同已变化，已停止",
    "PAUSED_USER_DECISION": "需要你确认下一步",
    "PAUSED_BUDGET": "本次授权预算已用尽",
    "FAILED": "存在阻滞",
    "CANCELLED": "已取消",
    "EXPIRED": "授权已过期",
    "REVOKED": "授权已撤销",
}


class GrantError(RuntimeError):
    """A named refusal.  ``code`` is what the UI and the model both read."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def normalize_objective(text: str) -> str:
    """One canonical spelling of the goal, so its digest is stable."""
    return " ".join(str(text or "").split())


def objective_digest(objective: str) -> str:
    return hashlib.sha256(normalize_objective(objective).encode("utf-8")).hexdigest()


def normalize_groups(groups: Iterable[str] | None) -> list[str]:
    """Requested capability groups, in canonical order, unknown ones refused."""
    requested = {str(item) for item in (groups or DEFAULT_GROUPS)}
    unknown = sorted(requested - set(GROUP_PERMISSIONS))
    if unknown:
        raise GrantError(
            "UNKNOWN_CAPABILITY_GROUP",
            "授权范围里有无法识别的能力分组。",
            details={"unknown": unknown},
        )
    return [name for name in GROUP_PERMISSIONS if name in requested]


def permissions_for_groups(groups: Iterable[str]) -> dict[str, bool]:
    """The permission map one set of capability groups implies."""
    granted = {key: False for key in PERMISSION_KEYS}
    for group in groups:
        for permission in GROUP_PERMISSIONS.get(group, ()):
            if permission in PERMANENTLY_FORBIDDEN:
                continue
            granted[permission] = True
    # Permanently forbidden stays false even if a group ever listed it.
    for permission in PERMANENTLY_FORBIDDEN:
        granted[permission] = False
    return granted


def normalize_limits(raw: dict[str, Any] | None) -> dict[str, int]:
    """Accept only known keys, clamp to the ceiling, refuse nonsense."""
    merged = dict(DEFAULT_LIMITS)
    for key, value in (raw or {}).items():
        if key not in DEFAULT_LIMITS:
            raise GrantError(
                "UNKNOWN_LIMIT",
                f"授权范围里有无法识别的预算项：{key}",
                details={"limit": key},
            )
        if isinstance(value, bool) or not isinstance(value, int):
            raise GrantError(
                "INVALID_LIMIT", f"预算项 {key} 必须是整数。", details={"limit": key}
            )
        if value < 0:
            raise GrantError(
                "INVALID_LIMIT", f"预算项 {key} 不能为负数。", details={"limit": key}
            )
        if value > LIMIT_CEILINGS[key]:
            raise GrantError(
                "LIMIT_ABOVE_CEILING",
                f"预算项 {key} 超过系统允许的上限。",
                details={"limit": key, "ceiling": LIMIT_CEILINGS[key]},
            )
        merged[key] = value
    return merged


def permission_for_capability(capability_id: str) -> str:
    permission = CAPABILITY_PERMISSIONS.get(str(capability_id))
    if not permission:
        raise GrantError(
            "CAPABILITY_NOT_IN_GRANT_CATALOG",
            "这个操作不在任务授权的能力目录内，已拒绝。",
            details={"capability_id": capability_id},
        )
    return permission


def capability_allowed(grant: dict[str, Any], capability_id: str) -> bool:
    permission = permission_for_capability(capability_id)
    return bool((grant.get("permissions") or {}).get(permission))


def compute_grant_digest(grant: dict[str, Any]) -> str:
    """The digest covers everything that authorizes, never the mutable ledger.

    ``status``, ``usage``, ``tool_calls`` and the timestamps of the running task
    are deliberately excluded: they change while the same authorization stays
    the same authorization.  Everything that grants power is included, so a
    changed scope or budget is a different digest and a stale preview cannot be
    replayed.
    """
    material = {
        "schema": grant.get("schema"),
        # ``grant_id`` is the row identity, not part of the boundary: a preview
        # and the click that follows it must describe the same authority even
        # though the click mints the real id.
        "session_id": grant.get("session_id"),
        "actor_id": grant.get("actor_id"),
        "objective_digest": grant.get("objective_digest"),
        "scope": grant.get("scope"),
        "permissions": grant.get("permissions"),
        "supply_chain": grant.get("supply_chain"),
        "limits": grant.get("limits"),
        # The expiry is frozen separately: a preview and the click that follows
        # it happen at different seconds but must describe the same authority.
        "validity": {
            "single_task_only": (grant.get("validity") or {}).get("single_task_only"),
            "revocable": (grant.get("validity") or {}).get("revocable"),
        },
        "frozen_capability_contracts": grant.get("frozen_capability_contracts"),
    }
    return digest(material)


def _contract_digest(capability_id: str) -> str | None:
    from shine.support import contracts as contract_registry

    return contract_registry.live_contract_digest(capability_id)


def freeze_capability_contracts(capability_ids: Iterable[str]) -> dict[str, str]:
    """Digest every capability the objective can reach, at preview time.

    A later upgrade that changes one of those contracts is a *named* pause
    (``PAUSED_CONTRACT``) rather than an old authorization silently reused
    against new code.
    """
    frozen: dict[str, str] = {}
    for capability_id in sorted({str(item) for item in capability_ids}):
        value = _contract_digest(capability_id)
        if value:
            frozen[capability_id] = value
    return frozen


def capabilities_for_groups(groups: Iterable[str]) -> list[str]:
    """Every capability the chosen groups can reach, in canonical order."""
    allowed = {
        permission
        for group in groups
        for permission in GROUP_PERMISSIONS.get(group, ())
    }
    return sorted(
        capability_id
        for capability_id, permission in CAPABILITY_PERMISSIONS.items()
        if permission in allowed
    )


def build_proposed_grant(
    *,
    grant_id: str,
    session_id: str,
    actor_id: str,
    objective: str,
    project_ids: list[str],
    installation_ids: list[str],
    allowed_resource_roots: list[str],
    groups: Iterable[str],
    limits: dict[str, Any] | None,
    created_at: datetime,
    validity: timedelta | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the server-generated grant body.  No field is client-supplied."""
    normalized_groups = normalize_groups(groups)
    if not project_ids:
        # A machine-level objective (environment preparation before any work
        # exists) still declares "no project", explicitly, rather than leaving
        # the field absent and ambiguous.
        project_ids = []
    window = validity or DEFAULT_VALIDITY
    if window > MAX_VALIDITY:
        raise GrantError("VALIDITY_TOO_LONG", "一次授权的有效期不能超过 7 天。")
    frozen_expiry = expires_at or (created_at + window)
    if (frozen_expiry - created_at) > MAX_VALIDITY:
        raise GrantError("VALIDITY_TOO_LONG", "一次授权的有效期不能超过 7 天。")
    grant: dict[str, Any] = {
        "schema": SCHEMA,
        "grant_id": grant_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "objective": normalize_objective(objective),
        "objective_digest": objective_digest(objective),
        "scope": {
            "project_ids": list(project_ids),
            "installation_ids": list(installation_ids),
            "allowed_resource_roots": list(allowed_resource_roots),
            "capability_groups": normalized_groups,
            "allow_other_project_access": False,
            "allow_external_comfyui_mutation": False,
        },
        "permissions": permissions_for_groups(normalized_groups),
        "supply_chain": dict(SUPPLY_CHAIN),
        "limits": normalize_limits(limits),
        "validity": {
            "expires_at": frozen_expiry.replace(microsecond=0).isoformat(),
            "single_task_only": True,
            "revocable": True,
        },
        "frozen_capability_contracts": freeze_capability_contracts(
            capabilities_for_groups(normalized_groups)
        ),
    }
    grant["grant_digest"] = compute_grant_digest(grant)
    return grant


def grant_status(grant: dict[str, Any], *, now: datetime) -> str:
    """The status the *server* reports, never the status the model claims."""
    status = str(grant.get("status") or "AUTHORIZED")
    if status in TERMINAL_STATUSES:
        return status
    expires = (grant.get("validity") or {}).get("expires_at")
    if expires:
        try:
            deadline = datetime.fromisoformat(str(expires))
        except ValueError:
            return "PAUSED_CONTRACT"
        if deadline.tzinfo is not None:
            deadline = deadline.replace(tzinfo=None)
        if now > deadline:
            return "EXPIRED"
    if grant.get("revoked_at"):
        return "REVOKED"
    return status


def assert_usable(grant: dict[str, Any], *, now: datetime) -> None:
    """Refuse a grant that may not start new work, by name."""
    status = grant_status(grant, now=now)
    if status in AUTHORIZED_STATUSES:
        return
    code = {
        "EXPIRED": "GRANT_EXPIRED",
        "REVOKED": "GRANT_REVOKED",
        "CANCELLED": "GRANT_CANCELLED",
    }.get(status, "GRANT_NOT_RUNNING")
    raise GrantError(code, f"本次任务授权当前状态是 {STATUS_LABELS.get(status, status)}。",
                     details={"status": status})


def assert_identity(
    grant: dict[str, Any],
    *,
    actor: str,
    session_id: str | None,
    grant_digest: str | None = None,
) -> None:
    """The grant belongs to one operator and one conversation."""
    if str(grant.get("actor_id") or "") != str(actor):
        raise GrantError("GRANT_ACTOR_MISMATCH", "这次授权属于另一位操作者，不能转用。")
    if session_id and str(grant.get("session_id") or "") != str(session_id):
        raise GrantError("GRANT_SESSION_MISMATCH", "这次授权不属于当前会话。")
    if grant_digest is not None and grant_digest != grant.get("grant_digest"):
        raise GrantError("GRANT_DIGEST_MISMATCH", "授权内容与预览时不一致，请重新确认。")


def assert_project_scope(
    grant: dict[str, Any], *, project_id: str | None, capability_id: str
) -> None:
    """A project-scoped action may only touch a project inside the envelope."""
    scope = grant.get("scope") or {}
    allowed = {str(item) for item in scope.get("project_ids") or []}
    if not allowed:
        # A machine-level grant has no projects.  A project-scoped capability
        # still needs a concrete, declared project, so refuse instead of
        # guessing which project the person meant.
        if project_id:
            raise GrantError(
                "SCOPE_EXCEEDED",
                "本次授权没有覆盖任何项目，不能改动项目数据。",
                details={"capability_id": capability_id, "project_id": project_id},
            )
        return
    if project_id and project_id not in allowed:
        raise GrantError(
            "SCOPE_EXCEEDED",
            "这个操作针对的项目不在本次授权范围内，已停止。",
            details={"capability_id": capability_id, "project_id": project_id},
        )


def assert_installation_scope(
    grant: dict[str, Any], *, installation_id: str | None
) -> None:
    """An adopted external installation is never mutated by a broad grant."""
    scope = grant.get("scope") or {}
    allowed = {str(item) for item in scope.get("installation_ids") or []}
    if installation_id and allowed and str(installation_id) not in allowed:
        raise GrantError(
            "SCOPE_EXCEEDED",
            "这个 ComfyUI 实例不在本次授权范围内。",
            details={"installation_id": installation_id},
        )


def assert_contract_frozen(grant: dict[str, Any], capability_id: str) -> None:
    """A capability that changed since the preview needs a new authorization."""
    frozen = (grant.get("frozen_capability_contracts") or {}).get(str(capability_id))
    if not frozen:
        return
    current = _contract_digest(str(capability_id))
    if current and current != frozen:
        raise GrantError(
            "GRANT_CONTRACT_CHANGED",
            "这个操作的能力合同在授权后发生了变化，已停止并要求重新确认。",
            details={"capability_id": capability_id},
        )


def usage_of(grant: dict[str, Any]) -> dict[str, int]:
    raw = grant.get("usage") or {}
    usage: dict[str, int] = {}
    for key in DEFAULT_LIMITS:
        value = raw.get(key)
        usage[key] = int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
    return usage


def remaining(grant: dict[str, Any]) -> dict[str, int]:
    limits = {**DEFAULT_LIMITS, **(grant.get("limits") or {})}
    usage = usage_of(grant)
    return {key: max(0, int(limits.get(key, 0)) - usage[key]) for key in DEFAULT_LIMITS}


def assert_budget(
    grant: dict[str, Any],
    *,
    cost: dict[str, int] | None = None,
    counters: dict[str, int] | None = None,
) -> None:
    """Refuse a step whose *declared* cost no longer fits the frozen budget."""
    left = remaining(grant)
    for key, amount in (cost or {}).items():
        if key not in DEFAULT_LIMITS:
            continue
        if int(amount) > left[key]:
            raise GrantError(
                "GRANT_BUDGET_EXCEEDED",
                "这一步的成本超出本次授权剩余预算，已停止。",
                details={"limit": key, "requested": int(amount), "remaining": left[key]},
            )
    for key, amount in (counters or {}).items():
        if key not in DEFAULT_LIMITS:
            continue
        if int(amount) > left[key]:
            raise GrantError(
                "GRANT_BUDGET_EXCEEDED",
                "这一步计入的次数超出本次授权剩余额度，已停止。",
                details={"limit": key, "requested": int(amount), "remaining": left[key]},
            )


def child_key(grant_id: str, tool_call_id: str, capability_id: str,
              params: dict[str, Any], target_identity: str) -> str:
    """The idempotency key of one sub-action inside one grant.

    Two identical tool calls (a replay, a retried HTTP request, a resumed
    worker) collapse onto the same key.  A model that invents a fresh
    ``tool_call_id`` for the same physical target is caught by the *business*
    identity (``SupportAction.request_key``) underneath, so neither layer can
    be sidestepped alone.
    """
    return digest(
        {
            "grant_id": grant_id,
            "tool_call_id": tool_call_id,
            "capability_id": capability_id,
            "params": params,
            "target_identity": target_identity,
        }
    )[:96]


def intent_payload(
    *,
    grant_id: str,
    tool_call_id: str,
    capability_id: str,
    params: dict[str, Any],
    expected: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """The exact object a model is allowed to produce."""
    return {
        "schema": TOOL_INTENT_SCHEMA,
        "grant_id": grant_id,
        "tool_call_id": tool_call_id,
        "capability_id": capability_id,
        "params": dict(params or {}),
        "expected": dict(expected or {}),
        "reason": str(reason or "")[:400],
    }


def validate_intent(value: Any) -> dict[str, Any]:
    """Strict validation.  Power is never taken from the model's text."""
    if not isinstance(value, dict):
        raise GrantError("INTENT_MALFORMED", "工具意图必须是一个对象。")
    if value.get("schema") != TOOL_INTENT_SCHEMA:
        raise GrantError(
            "INTENT_SCHEMA_MISMATCH",
            "工具意图的 schema 字段不正确。",
            details={"expected": TOOL_INTENT_SCHEMA},
        )
    capability_id = str(value.get("capability_id") or "")
    if not capability_id:
        raise GrantError("INTENT_CAPABILITY_REQUIRED", "工具意图缺少 capability_id。")
    params = value.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise GrantError("INTENT_PARAMS_MALFORMED", "工具意图的 params 必须是对象。")
    tool_call_id = str(value.get("tool_call_id") or "")
    if not tool_call_id or len(tool_call_id) > 96:
        raise GrantError("INTENT_TOOL_CALL_ID_REQUIRED", "工具意图缺少合法的 tool_call_id。")
    expected = value.get("expected")
    if expected is not None and not isinstance(expected, dict):
        raise GrantError("INTENT_EXPECTED_MALFORMED", "工具意图的 expected 必须是对象。")
    return intent_payload(
        grant_id=str(value.get("grant_id") or ""),
        tool_call_id=tool_call_id,
        capability_id=capability_id,
        params=params,
        expected=expected or {},
        reason=str(value.get("reason") or ""),
    )


def grant_public(grant: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """The projection the UI and the tool feedback both read."""
    return {
        "schema": SCHEMA,
        "grant_id": grant.get("grant_id"),
        "objective": grant.get("objective"),
        "objective_digest": grant.get("objective_digest"),
        "status": grant_status(grant, now=now),
        "status_label": STATUS_LABELS.get(
            grant_status(grant, now=now), str(grant.get("status") or "")
        ),
        "scope": grant.get("scope"),
        "permissions": grant.get("permissions"),
        "limits": grant.get("limits"),
        "remaining": remaining(grant),
        "validity": grant.get("validity"),
        "grant_digest": grant.get("grant_digest"),
        "paused_reason": grant.get("paused_reason"),
    }


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed

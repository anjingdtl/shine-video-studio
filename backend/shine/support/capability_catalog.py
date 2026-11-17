"""Canonical capability registry served to the UI and the planner.

V2.1: the registry is no longer hand-maintained here.  It is *derived* from
``shine/copilot/declarations.py`` by the build generator and loaded from the
packaged artefact, so a new product capability appears once the build runs -
and still needs a reviewed contract before it can execute.

The second half of this module is the audited UI operation matrix.  It remains
the acceptance artefact: one row per real user operation, mapped to the real
route, the real executor and the honest AI status.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from shine.copilot import declarations as decl
from shine.copilot.build import CONTRACT_VERSION
from shine.copilot.contracts import AI_STATUSES, AVAILABILITY, PERMISSION_TIERS
from shine.support import contracts as contract_registry
from shine.support.tools import READ_TOOLS

CATALOG_VERSION = CONTRACT_VERSION

#: Permission tiers, enforced server-side by ``support.actions``.
TIERS = dict(PERMISSION_TIERS)

#: AI status of a real user operation.
STATUSES = dict(AI_STATUSES)


@dataclass(frozen=True)
class Capability:
    action_id: str
    label: str
    module: str
    page: str
    tier: str
    executor: str | None
    params: tuple[str, ...] = ()
    param_contract: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    preconditions: tuple[str, ...] = ()
    readback: str | None = None
    reversible: str = "NO"
    gpu: bool = False
    paid: bool = False
    scope: str = "PROJECT"
    status: str = "COVERED"
    examples: tuple[str, ...] = ()
    note: str = ""
    gates: tuple[str, ...] = field(default_factory=tuple)
    #: V2.1 contract metadata (displayed in diagnostics, not as raw hashes).
    feature_id: str = ""
    availability: str = "AVAILABLE"
    contract_revision: int = 1
    contract_digest: str = ""
    api_route: str | None = None
    request_schema: str | None = None
    confirmation_policy: str = ""
    resource_cost: dict[str, Any] = field(default_factory=dict)


def _row(payload: dict[str, Any], *, status: str | None = None) -> Capability:
    return Capability(
        action_id=str(payload.get("action_id") or ""),
        label=str(payload.get("label") or ""),
        module=str(payload.get("module") or ""),
        page=str(payload.get("page") or ""),
        tier=str(payload.get("tier") or "PREVIEW"),
        executor=payload.get("executor"),
        params=tuple(payload.get("params") or ()),
        param_contract=tuple(payload.get("param_contract") or ()),
        preconditions=tuple(payload.get("preconditions") or ()),
        readback=payload.get("readback") or payload.get("authority"),
        reversible=str(payload.get("reversible") or "NO"),
        gpu=bool(payload.get("gpu")),
        paid=bool(payload.get("paid")),
        scope=str(payload.get("scope") or "PROJECT"),
        status=status or str(payload.get("status") or "GUARDED"),
        examples=tuple(payload.get("examples") or ()),
        note=str(payload.get("note") or ""),
        gates=tuple(payload.get("gates") or ()),
        feature_id=str(payload.get("feature_id") or ""),
        availability=str(payload.get("availability") or "AVAILABLE"),
        contract_revision=int(payload.get("contract_revision") or 1),
        contract_digest=str(payload.get("contract_digest") or ""),
        api_route=payload.get("api_route"),
        request_schema=payload.get("request_schema"),
        confirmation_policy=str(payload.get("confirmation_policy") or ""),
        resource_cost=dict(payload.get("resource_cost") or {}),
    )


def _fallback_write_payload(row) -> dict[str, Any]:
    return {
        "action_id": row.action_id,
        "feature_id": row.feature_id,
        "label": row.label,
        "module": row.module,
        "page": row.page,
        "tier": row.permission_tier,
        "executor": row.executor,
        "params": list(row.param_names()),
        "param_contract": [],
        "preconditions": list(row.preconditions),
        "gates": list(row.gates),
        "readback": row.authority,
        "reversible": row.reversible,
        "gpu": row.gpu,
        "paid": row.paid,
        "scope": row.project_scope,
        "status": row.status,
        "examples": list(row.examples),
        "note": row.note or row.description,
        "availability": "CONTRACT_UNAVAILABLE",
        "contract_revision": row.contract_revision,
        "contract_digest": "",
        "api_route": row.api_route,
        "request_schema": row.request_schema,
        "resource_cost": {"gpu": row.gpu, "paid": row.paid, "note": row.cost_note},
    }


def _rows() -> tuple[tuple[Capability, ...], tuple[Capability, ...], dict[str, Any] | None]:
    artifact = contract_registry.load_artifact()
    if artifact:
        writes = tuple(_row(row) for row in contract_registry.capabilities(artifact))
        reads = tuple(
            _row(row, status="READ_ONLY") for row in contract_registry.read_capabilities(artifact)
        )
        return writes, reads, artifact
    writes = tuple(_row(_fallback_write_payload(row)) for row in decl.WRITE_ACTIONS)
    reads = tuple(_row(_fallback_write_payload(row), status="READ_ONLY") for row in decl.READ_ACTIONS)
    return writes, reads, None


CAPABILITIES, READ_CAPABILITIES, _ARTIFACT = _rows()

MATRIX: tuple[dict[str, str], ...] = tuple(
    (_ARTIFACT or {}).get("matrix") or [dict(row) for row in decl.MATRIX]
)

_HUMAN: tuple[tuple[str, str, str, str], ...] = tuple(
    (row.operation, row.api, row.reason, row.status) for row in decl.HUMAN_OPERATIONS
)

_NOT_SUPPORTED: tuple[tuple[str, str, str], ...] = tuple(
    (row.operation, row.api, row.reason) for row in decl.NOT_SUPPORTED_OPERATIONS
)


def _capability(row: Capability) -> dict[str, Any]:
    payload = asdict(row)
    payload["tier_label"] = TIERS.get(row.tier, row.tier)
    payload["status_label"] = STATUSES.get(row.status, row.status)
    payload["availability_label"] = AVAILABILITY.get(row.availability, row.availability)
    payload["requires_confirmation"] = row.tier in {"PREVIEW", "EXECUTE_GUARDED"}
    return payload


def catalog_payload() -> dict[str, Any]:
    """Everything the UI/API needs, with the honest matrix alongside."""
    identity = contract_registry.build_identity()
    matrix_counts = {status: 0 for status in STATUSES}
    for row in MATRIX:
        matrix_counts[row["ai_status"]] = matrix_counts.get(row["ai_status"], 0) + 1
    human = (
        list((_ARTIFACT or {}).get("human_required") or [])
        if _ARTIFACT
        else [
            {"operation": operation, "api": api, "reason": reason, "status": status}
            for operation, api, reason, status in _HUMAN
        ]
    )
    not_supported = (
        list((_ARTIFACT or {}).get("not_supported") or [])
        if _ARTIFACT
        else [
            {"operation": operation, "api": api, "reason": reason}
            for operation, api, reason in _NOT_SUPPORTED
        ]
    )
    forbidden = dict((_ARTIFACT or {}).get("forbidden") or {
        name: "运行时管家不具备" for name in decl.FORBIDDEN
    })
    discovered_all = list((_ARTIFACT or {}).get("discovered") or [])
    return {
        "version": CATALOG_VERSION,
        "app_version": identity.get("app_version"),
        "principles": {
            "facts": "所有状态读取既有权威台账，不新建第二套事实。",
            "verdict": "环境/生产/冻结/交付仍由原业务门禁裁决，AI 不宣布 READY。",
            "executor": "每个写动作只有一个既有执行者。",
            "source_write": "运行时管家没有源码/Shell/Git/任意文件工具。",
            "auto_sync": "知识索引与能力合同由构建从正式声明生成，运行时只加载。",
        },
        "build": identity,
        "tiers": TIERS,
        "statuses": STATUSES,
        "availability": AVAILABILITY,
        "capabilities": [_capability(row) for row in CAPABILITIES],
        "read_capabilities": [_capability(row) for row in READ_CAPABILITIES],
        "matrix": list(MATRIX),
        "matrix_totals": {
            "operations": len(MATRIX),
            "by_status": matrix_counts,
        },
        "discovered": discovered_all[:100],
        "discovered_totals": {
            "routes": len(discovered_all),
            "executable": 0,
            "note": "构建期发现但未声明合同的 API 只作说明，默认不可执行；"
            "完整清单见打包产物 generated/copilot_contracts.json。",
        },
        "human_required": human,
        "not_supported": not_supported,
        "forbidden": forbidden,
        "read_tools": [spec.name for spec in READ_TOOLS],
        "note": "未列入矩阵的操作不计入覆盖率；不得把 NOT_SUPPORTED 表述为可用。",
    }


def action_ids() -> list[str]:
    return [row.action_id for row in CAPABILITIES]


def capability(action_id: str) -> Capability | None:
    for row in CAPABILITIES:
        if row.action_id == action_id:
            return row
    return None


def read_capability(action_id: str) -> Capability | None:
    for row in READ_CAPABILITIES:
        if row.action_id == action_id:
            return row
    return None


def plan_step_for(next_action_kind: str) -> dict[str, str]:
    """Map a director overview ``next_action`` to an honest plan step.

    Used by the bounded planner; it never invents a capability.
    """
    mapping = {
        "RUN_STORYBOARD": {"ai_action": "storyboard_generate", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "RETRY_STORYBOARD": {"ai_action": "storyboard_generate", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "START_PRODUCTION": {"ai_action": "production_start", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "BUILD_PACKAGE": {"ai_action": "production_start", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "PREPARE_MATERIALS": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "CONFIRM_EPISODE_SHOTS": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "CONFIRM_BOOK": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "IMPORT_SCRIPT": {"ai_action": "document_import_text", "status": "COVERED", "tier": "PREVIEW"},
        "REVIEW_EPISODE": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "WAIT": {"ai_action": None, "status": "READ_ONLY", "tier": "READ"},
        "COMPLETE": {"ai_action": None, "status": "READ_ONLY", "tier": "READ"},
    }
    return mapping.get(
        next_action_kind,
        {"ai_action": None, "status": "NOT_SUPPORTED", "tier": "HUMAN_REQUIRED"},
    )

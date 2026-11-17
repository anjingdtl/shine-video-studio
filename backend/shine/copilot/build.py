"""Build the copilot's version-bound artefacts from declarations.

This module is the *only* generator.  It is used by the build script, by CI,
and by tests; the runtime loader never calls it, so a running installation
cannot invent a capability for itself.

Determinism: every row is derived from declarations and the real route table.
No timestamps are written.  ``source_commit`` is provenance and is excluded
from every content hash, so the same code always yields the same fingerprint.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from shine.copilot.contracts import (
    AI_STATUSES,
    APP_VERSION,
    AVAILABILITY,
    CONTRACT_VERSION,
    IMPLEMENTATION_STATUSES,
    PERMISSION_TIERS,
    VERIFICATION_STATUSES,
    CapabilityDeclaration,
    FeatureDeclaration,
    HumanOperation,
    NotSupportedOperation,
    canonical,
    digest,
)

KNOWLEDGE_VERSION = f"shine-project-kb-{APP_VERSION}"
ARTIFACT_RELATIVE_PATH = "shine/support/generated/copilot_contracts.json"
MANIFEST_RELATIVE_PATH = "shine/support/knowledge_manifest.json"

_PARAM_RE = re.compile(r"{[^}]+}")


def normalize_path(path: str) -> str:
    """``/projects/{project_id}/x`` and ``/projects/{id}/x`` are one route."""
    return _PARAM_RE.sub("{}", path.rstrip("/") or "/")


def route_key(method: str, path: str) -> str:
    return f"{method.upper()} {normalize_path(path)}"


def route_inventory(routes: Iterable[tuple[str, str]]) -> list[str]:
    keys = {route_key(method, path) for method, path in routes}
    return sorted(keys)


def api_schema_digest(routes: Iterable[tuple[str, str]]) -> str:
    return digest(route_inventory(routes))


def knowledge_entry(feature: FeatureDeclaration, *, source_commit: str) -> dict[str, Any]:
    content_hash = feature.content_hash()
    entry = {
        "knowledge_id": f"kb-{feature.feature_id}",
        # Compatibility alias: V2 retrieval and tests use the bare feature id.
        "id": feature.feature_id,
        "feature_id": feature.feature_id,
        "module": feature.module,
        "title": feature.title,
        "description": feature.description,
        "summary": feature.description,
        "status": feature.status,
        "status_label": IMPLEMENTATION_STATUSES[feature.status],
        "implementation_status": feature.status,
        "verification": feature.verification,
        "verification_status": feature.verification,
        "verification_label": VERIFICATION_STATUSES[feature.verification],
        "sources": list(feature.sources),
        "source_path": feature.sources[0] if feature.sources else "",
        "source_paths": list(feature.sources),
        "source_revision": source_commit,
        "apis": list(feature.apis),
        "api_contract": {"apis": list(feature.apis)},
        "evidence": list(feature.evidence),
        "docs": list(feature.docs),
        "tags": list(feature.tags),
        "not_supported": feature.not_supported,
        "app_version": APP_VERSION,
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge_revision": f"{APP_VERSION}-{content_hash[:8]}",
        "content_hash": content_hash,
        "evidence_level": "VERSIONED_PROJECT_DOCS",
    }
    return entry


def knowledge_entries(
    features: tuple[FeatureDeclaration, ...], *, source_commit: str = ""
) -> list[dict[str, Any]]:
    return [knowledge_entry(feature, source_commit=source_commit) for feature in features]


def knowledge_content_digest(entries: list[dict[str, Any]]) -> str:
    """Hash only content, never provenance: no false staleness per build."""
    stripped = [
        {key: value for key, value in entry.items() if key != "source_revision"}
        for entry in entries
    ]
    return digest(stripped)


def capability_row(row: CapabilityDeclaration, *, status: str | None = None) -> dict[str, Any]:
    return {
        "action_id": row.action_id,
        "feature_id": row.feature_id,
        "label": row.label,
        "module": row.module,
        "page": row.page,
        "tier": row.permission_tier,
        "executor": row.executor,
        "params": [param.name for param in row.params],
        "param_contract": [param.contract() for param in row.params],
        "preconditions": list(row.preconditions),
        "gates": list(row.gates),
        "readback": row.authority,
        "reversible": row.reversible,
        "gpu": row.gpu,
        "paid": row.paid,
        "scope": row.project_scope,
        "status": status or row.status,
        "examples": list(row.examples),
        "note": row.note or row.description,
        "description": row.description,
        "availability": row.availability,
        "dispatch": row.dispatch or row.action_id,
        "executor_bound": row.executor_bound,
        "api_route": row.api_route,
        "request_schema": row.request_schema,
        "response_schema": row.response_schema,
        "authority": row.authority,
        "confirmation_policy": row.confirmation_policy,
        "occupancy_gate": row.occupancy_gate,
        "resource_cost": {"gpu": row.gpu, "paid": row.paid, "note": row.cost_note},
        "idempotency_policy": row.idempotency_policy,
        "readback_contract": row.readback_contract,
        "rollback_policy": row.rollback_policy,
        "introduced_version": row.introduced_version,
        "contract_revision": row.contract_revision,
        "contract_digest": row.contract_digest(),
    }


def contracts_digest(
    actions: tuple[CapabilityDeclaration, ...], features: tuple[FeatureDeclaration, ...]
) -> str:
    payload = {
        "actions": {
            row.action_id: {
                "digest": row.contract_digest(),
                "revision": row.contract_revision,
                "availability": row.availability,
            }
            for row in actions
        },
        "features": {row.feature_id: row.content_hash() for row in features},
    }
    return digest(payload)


def build_bundle(
    *,
    features: tuple[FeatureDeclaration, ...],
    write_actions: tuple[CapabilityDeclaration, ...],
    read_actions: tuple[CapabilityDeclaration, ...],
    human: tuple[HumanOperation, ...],
    not_supported: tuple[NotSupportedOperation, ...],
    matrix: tuple[dict[str, str], ...],
    forbidden: tuple[str, ...],
    source_commit: str = "",
    routes: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    entries = knowledge_entries(features, source_commit=source_commit)
    capabilities = [capability_row(row) for row in write_actions]
    reads = [capability_row(row, status="READ_ONLY") for row in read_actions]
    declared = {
        route_key(*row.api_route.split(" ", 1))
        for row in (*write_actions, *read_actions)
        if row.api_route and " " in row.api_route and "|" not in row.api_route
    }
    declared_actions = {
        "POST /api/v1/support/actions/preview",
        "POST /api/v1/support/plans/delegate",
    }
    discovered = [
        {
            "route": key,
            "status": "DISCOVERED",
            "executable": False,
            "reason": "构建期发现但未声明受控合同；只作说明，不获得执行权。",
        }
        for key in route_inventory(routes)
        if key not in declared
        and key not in declared_actions
        and not key.split(" ", 1)[1].startswith("/api/v1/support/")
    ]
    matrix_counts = {status: 0 for status in AI_STATUSES}
    for row in matrix:
        matrix_counts[row["ai_status"]] = matrix_counts.get(row["ai_status"], 0) + 1
    return {
        "version": CONTRACT_VERSION,
        "app_version": APP_VERSION,
        "source_commit": source_commit,
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge": {
            "entries": entries,
            "content_sha256": knowledge_content_digest(entries),
            "entry_count": len(entries),
        },
        "capabilities": capabilities,
        "read_capabilities": reads,
        "human_required": [
            {"operation": row.operation, "api": row.api, "reason": row.reason, "status": row.status}
            for row in human
        ],
        "not_supported": [
            {"operation": row.operation, "api": row.api, "reason": row.reason}
            for row in not_supported
        ],
        "forbidden": {name: "运行时管家不具备" for name in forbidden},
        "matrix": list(matrix),
        "matrix_totals": {"operations": len(matrix), "by_status": matrix_counts},
        "discovered": discovered,
        "digests": {
            "contracts": contracts_digest(write_actions, features),
            "api_schema": api_schema_digest(routes),
        },
        "vocabulary": {
            "permission_tiers": PERMISSION_TIERS,
            "availability": AVAILABILITY,
            "ai_statuses": AI_STATUSES,
            "implementation_statuses": IMPLEMENTATION_STATUSES,
            "verification_statuses": VERIFICATION_STATUSES,
        },
    }


def manifest_payload(bundle: dict[str, Any], *, source_commit: str) -> dict[str, Any]:
    return {
        "knowledge_version": bundle["knowledge_version"],
        "source_commit": source_commit,
        "content_sha256": bundle["knowledge"]["content_sha256"],
        "entry_count": bundle["knowledge"]["entry_count"],
        "contracts_digest": bundle["digests"]["contracts"],
        "capability_count": len(bundle["capabilities"]),
        "read_capability_count": len(bundle["read_capabilities"]),
        "api_schema_digest": bundle["digests"]["api_schema"],
        "app_version": bundle["app_version"],
        "generator": "backend/scripts/generate_shine_copilot_contracts.py",
    }


def serialized(bundle: dict[str, Any]) -> str:
    """Stable file bytes: sorted keys, one trailing newline, no timestamp."""
    import json

    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def canonical_bundle_digest(bundle: dict[str, Any]) -> str:
    return digest(bundle)


__all__ = [
    "ARTIFACT_RELATIVE_PATH",
    "KNOWLEDGE_VERSION",
    "MANIFEST_RELATIVE_PATH",
    "build_bundle",
    "capability_row",
    "contracts_digest",
    "api_schema_digest",
    "knowledge_content_digest",
    "knowledge_entries",
    "manifest_payload",
    "normalize_path",
    "route_inventory",
    "route_key",
    "serialized",
    "canonical",
]

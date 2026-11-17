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


API_CONTRACT_VERSION = "openapi-normalized-2026.11-v1"

_SCHEMA_KEEP = {
    "type", "format", "enum", "const", "default", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern",
    "minItems", "maxItems", "uniqueItems", "multipleOf", "nullable", "required",
    "properties", "items", "additionalProperties", "prefixItems", "anyOf", "allOf",
    "oneOf", "propertyNames", "minProperties", "maxProperties", "dependentRequired",
}
_SCHEMA_DROP = {
    "title", "description", "example", "examples", "deprecated", "xml",
    "externalDocs", "readOnly", "writeOnly", "discriminator",
}
_PERMISSION_HEADERS = {"authorization", "x-shine-local-action"}


def _openapi_doc(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    openapi = getattr(value, "openapi", None)
    if callable(openapi):
        return openapi()
    return {}


def _normalize_schema(schema: Any, components: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        if name in seen:
            return {"$ref": name}
        target = components.get(name)
        if target is None:
            return {"$ref": name}
        return _normalize_schema(target, components, seen | {name})
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _SCHEMA_DROP or key not in _SCHEMA_KEEP:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {
                name: _normalize_schema(item, components, seen)
                for name, item in sorted(value.items())
            }
        elif key in {"items", "additionalProperties", "propertyNames"}:
            out[key] = _normalize_schema(value, components, seen)
        elif key in {"anyOf", "allOf", "oneOf", "prefixItems"} and isinstance(value, list):
            out[key] = [_normalize_schema(item, components, seen) for item in value]
        else:
            out[key] = value
    return out


def _compact_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    if "anyOf" in schema:
        inner = [
            _compact_type(item)
            for item in schema["anyOf"]
            if not (isinstance(item, dict) and item.get("type") == "null")
        ]
        return "|".join(item for item in inner if item != "null") or "null"
    if "type" in schema:
        value = schema["type"]
        return value if isinstance(value, str) else "|".join(str(item) for item in value)
    if "enum" in schema:
        return "enum"
    if "const" in schema:
        return "const"
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "unknown"


def _summary(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    keep = (
        "type", "format", "enum", "const", "default", "minimum", "maximum",
        "minLength", "maxLength", "pattern", "minItems", "maxItems", "nullable",
        "required",
    )
    return {key: schema[key] for key in keep if key in schema}


def api_schema_contract(
    target: Any,
    *,
    operations: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """A normalized, deterministic contract for every real request route.

    Only request semantics are kept: path/query/header parameters, request body
    fields/constraints/defaults/enums and security.  Documentation order, prose,
    examples and responses are excluded so a comment edit is not a fake change.
    """
    doc = _openapi_doc(target)
    components = (doc.get("components") or {}).get("schemas") or {}
    wanted = set(operations) if operations is not None else None
    global_security = doc.get("security") or []
    out: list[dict[str, Any]] = []
    for path, item in sorted((doc.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method, operation in sorted(item.items()):
            if str(method).lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            key = route_key(str(method), path)
            if wanted is not None and key not in wanted:
                continue
            parameters = []
            for parameter in sorted(
                operation.get("parameters") or [],
                key=lambda row: (str(row.get("in") or ""), str(row.get("name") or "")),
            ):
                schema = _normalize_schema(parameter.get("schema") or {}, components)
                parameters.append(
                    {
                        "name": parameter.get("name"),
                        "in": parameter.get("in"),
                        "required": bool(parameter.get("required")),
                        "schema": schema,
                        "summary": _summary(schema),
                    }
                )
            body = operation.get("requestBody")
            body_contract = None
            if body:
                content = body.get("content") or {}
                schema = _normalize_schema(
                    (content.get("application/json") or {}).get("schema") or {}, components
                )
                properties = schema.get("properties") or {}
                body_contract = {
                    "required": bool(body.get("required")),
                    "schema": schema,
                    "schema_digest": digest(schema),
                    "fields": {
                        name: {"type": _compact_type(field), **_summary(field)}
                        for name, field in sorted(properties.items())
                    },
                    "required_fields": sorted(schema.get("required") or []),
                }
            permission_headers = [
                row for row in parameters if row["in"] == "header"
                and str(row["name"]).lower() in _PERMISSION_HEADERS
            ]
            out.append(
                {
                    "operation": key,
                    "parameters": parameters,
                    "body": body_contract,
                    "security": operation.get("security") or global_security,
                    "permission": {
                        "security": sorted(
                            str(item) for item in (operation.get("security") or global_security)
                        ),
                        "auth_headers": sorted(
                            row["name"] for row in permission_headers if row.get("required")
                        ),
                    },
                }
            )
    return out


def api_schema_digest(target: Any) -> str:
    """Full request-contract fingerprint (all real routes, no prose/order)."""
    if isinstance(target, list | tuple) and all(
        isinstance(item, list | tuple) and len(item) == 2 for item in target
    ):
        # Backward-compatible route inventory when a caller only has routes.
        return digest(route_inventory(target))
    return digest(api_schema_contract(target))


def _operations_for(row: CapabilityDeclaration) -> list[str]:
    keys: list[str] = []
    for value in (row.api_route, row.authority):
        if not value or " " not in value:
            continue
        method, path = value.split(" ", 1)
        if method.count("/") or not method.isupper():
            continue
        if "|" in path:
            parts = path.split("|")
            prefix, _, last = parts[0].rpartition("/")
            paths = [f"{prefix}/{name.strip()}" for name in [last, *parts[1:]]]
        else:
            paths = [path]
        keys.extend(route_key(method, part) for part in paths)
    return keys


def api_contract_digest_for(contract: list[dict[str, Any]], operations: Iterable[str]) -> str:
    wanted = set(operations)
    rows = [row for row in contract if row.get("operation") in wanted]
    present = {row.get("operation") for row in rows}
    missing = sorted(wanted - present)
    payload = {"operations": rows, "missing": missing}
    return digest(payload)


def diff_api_contract(
    previous: list[dict[str, Any]] | None,
    current: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Classify request-contract drift into COMPATIBLE / BREAKING changes."""
    old = {row.get("operation"): row for row in previous or []}
    new = {row.get("operation"): row for row in current or []}
    changes: list[dict[str, str]] = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before is None:
            changes.append({"operation": key, "reason": "route added", "severity": "COMPATIBLE"})
            continue
        if after is None:
            changes.append({"operation": key, "reason": "route removed", "severity": "BREAKING"})
            continue
        changes.extend(_diff_operation(key, before, after))
    return changes


def _diff_operation(key: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []

    def add(reason: str, severity: str) -> None:
        changes.append({"operation": key, "reason": reason, "severity": severity})

    if before.get("security") != after.get("security") or before.get("permission") != after.get(
        "permission"
    ):
        old_permission = before.get("permission") or {}
        new_permission = after.get("permission") or {}
        stricter = (
            len(new_permission.get("security") or []) > len(old_permission.get("security") or [])
            or len(new_permission.get("auth_headers") or [])
            > len(old_permission.get("auth_headers") or [])
        )
        add("permission changed", "BREAKING" if stricter else "COMPATIBLE")

    old_params = {
        (row.get("in"), row.get("name")): row for row in before.get("parameters") or []
    }
    new_params = {
        (row.get("in"), row.get("name")): row for row in after.get("parameters") or []
    }
    for param_key in sorted(set(old_params) | set(new_params), key=lambda item: str(item)):
        old_param, new_param = old_params.get(param_key), new_params.get(param_key)
        if old_param is None:
            add(f"parameter added: {param_key[1]}", "COMPATIBLE")
            continue
        if new_param is None:
            add(f"parameter removed: {param_key[1]}", "BREAKING")
            continue
        if not old_param.get("required") and new_param.get("required"):
            add(f"optional became required: {param_key[1]}", "BREAKING")
        changes.extend(_diff_schema(str(param_key[1]), old_param.get("schema"), new_param.get("schema")))

    old_body, new_body = before.get("body"), after.get("body")
    if old_body is None and new_body is not None:
        add("request body added", "COMPATIBLE")
    elif old_body is not None and new_body is None:
        add("request body removed", "BREAKING")
    elif old_body is not None and new_body is not None:
        if not old_body.get("required") and new_body.get("required"):
            add("request body became required", "BREAKING")
        old_fields = old_body.get("fields") or {}
        new_fields = new_body.get("fields") or {}
        old_required = set(old_body.get("required_fields") or [])
        new_required = set(new_body.get("required_fields") or [])
        for name in sorted(set(old_fields) | set(new_fields)):
            if name not in new_fields:
                add(f"body field removed: {name}", "BREAKING")
                continue
            if name not in old_fields:
                severity = "BREAKING" if name in new_required else "COMPATIBLE"
                add(f"body field added: {name}", severity)
                continue
            if name in new_required and name not in old_required:
                add(f"optional body field became required: {name}", "BREAKING")
            changes.extend(_diff_field(name, old_fields[name], new_fields[name]))
        if old_body.get("schema_digest") != new_body.get("schema_digest") and not changes:
            # The digest changed somewhere the compact field summary cannot see
            # (nested object/array constraints).  Treat it as incompatible.
            add("nested request schema changed", "BREAKING")
    return changes


def _schema_changes(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, str]]:
    reasons: list[tuple[str, str]] = []
    if before.get("type") != after.get("type"):
        reasons.append(("type changed", "BREAKING"))
    old_enum = before.get("enum")
    new_enum = after.get("enum")
    if old_enum != new_enum and (old_enum or new_enum):
        narrowed = (
            (not old_enum and bool(new_enum))
            or (bool(old_enum) and not new_enum)
            or (bool(old_enum) and any(value not in (new_enum or []) for value in old_enum))
        )
        reasons.append(("enum changed", "BREAKING" if narrowed else "COMPATIBLE"))
    if before.get("default", "<none>") != after.get("default", "<none>"):
        reasons.append(("default changed", "BREAKING"))
    for key_name, mode in (
        ("maxLength", "shrink"),
        ("maximum", "shrink"),
        ("minLength", "grow"),
        ("minimum", "grow"),
    ):
        old_value, new_value = before.get(key_name), after.get(key_name)
        if old_value is None or new_value is None or old_value == new_value:
            continue
        stricter = new_value < old_value if mode == "shrink" else new_value > old_value
        reasons.append((f"{key_name} changed", "BREAKING" if stricter else "COMPATIBLE"))
    if before.get("nullable") and not after.get("nullable"):
        reasons.append(("nullable removed", "BREAKING"))
    if not reasons and digest(before) != digest(after):
        reasons.append(("nested schema changed", "BREAKING"))
    return reasons


def _diff_schema(label: str, before: Any, after: Any) -> list[dict[str, str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        if before != after:
            return [{"operation": label, "reason": "schema changed", "severity": "BREAKING"}]
        return []
    return [
        {"operation": label, "reason": reason, "severity": severity}
        for reason, severity in _schema_changes(before, after)
    ]


def _diff_field(name: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"operation": f"body field {name}", "reason": reason, "severity": severity}
        for reason, severity in _schema_changes(before, after)
    ]


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


def capability_row(
    row: CapabilityDeclaration,
    *,
    status: str | None = None,
    api_contract: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from shine.copilot.params import resolved_contract_digest, resolved_params

    operations = _operations_for(row)
    return {
        "action_id": row.action_id,
        "feature_id": row.feature_id,
        "label": row.label,
        "module": row.module,
        "page": row.page,
        "tier": row.permission_tier,
        "executor": row.executor,
        "params": [param.name for param in row.params],
        "param_contract": resolved_params(row),
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
        "api_operations": operations,
        "api_contract_digest": api_contract_digest_for(api_contract or [], operations),
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
        "contract_digest": resolved_contract_digest(row),
    }


def contracts_digest(
    actions: tuple[CapabilityDeclaration, ...], features: tuple[FeatureDeclaration, ...]
) -> str:
    from shine.copilot.params import resolved_contract_digest

    payload = {
        "actions": {
            row.action_id: {
                "digest": resolved_contract_digest(row),
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
    openapi: Any = None,
) -> dict[str, Any]:
    entries = knowledge_entries(features, source_commit=source_commit)
    declared_operations = sorted(
        {
            operation
            for row in (*write_actions, *read_actions)
            for operation in _operations_for(row)
        }
    )
    api_contract = (
        api_schema_contract(openapi, operations=declared_operations) if openapi else []
    )
    capabilities = [
        capability_row(row, api_contract=api_contract) for row in write_actions
    ]
    reads = [
        capability_row(row, status="READ_ONLY", api_contract=api_contract)
        for row in read_actions
    ]
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
        "api_contract_version": API_CONTRACT_VERSION,
        "api_contract": api_contract,
        "digests": {
            "contracts": contracts_digest(write_actions, features),
            "api_schema": (
                api_schema_digest(openapi) if openapi else api_schema_digest(routes)
            ),
            "api_contract": digest(api_contract),
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
        "api_contract_version": bundle.get("api_contract_version"),
        "api_contract_digest": bundle["digests"].get("api_contract"),
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
    "API_CONTRACT_VERSION",
    "ARTIFACT_RELATIVE_PATH",
    "KNOWLEDGE_VERSION",
    "MANIFEST_RELATIVE_PATH",
    "api_contract_digest_for",
    "api_schema_contract",
    "diff_api_contract",
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

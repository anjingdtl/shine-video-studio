"""Generate (or verify) the copilot's version-bound knowledge and contracts.

This is the single build entry point for V2.1:

    python -m scripts.generate_shine_copilot_contracts --commit "$(git rev-parse HEAD)"
    python -m scripts.generate_shine_copilot_contracts --check

``--check`` is what CI runs.  It verifies, against the real FastAPI app:

1. every declared route exists;
2. every declared ``body`` parameter exists on that route's request model;
3. every declared executor is importable;
4. every read-back route exists;
5. every capability points at a declared feature;
6. ``AVAILABLE`` actions are bound to a reviewed dispatcher;
7. the committed artefact matches the current declarations (digests);
8. the manifest matches the artefact;
9. contract changes bump ``contract_revision`` and breaking changes are
   explicit;
10. undeclared routes are reported as DISCOVERED and never executable.

Exit code 0 = consistent; 1 = the build must not ship this contract.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ARTIFACT = BACKEND / "shine" / "support" / "generated" / "copilot_contracts.json"
MANIFEST = BACKEND / "shine" / "support" / "knowledge_manifest.json"


def _source_commit(explicit: str | None) -> str | None:
    """Explicit build commit only.

    Deliberately *no* ``git rev-parse HEAD`` fallback: a committed artefact that
    names the commit which contains it can never match itself.  Packaging passes
    the real commit; a source checkout stays unproven (UNKNOWN), which is honest.
    """
    if explicit:
        return explicit.strip()
    injected = (os.environ.get("SHINE_KNOWLEDGE_SOURCE_COMMIT") or "").strip()
    if injected:
        return injected
    return None


def _import_object(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _import_module_or_object(spec: str) -> Any:
    """Accept ``module.attr`` or a whole module (``shine.director.run_engine``)."""
    if ":" in spec:
        return _import_object(spec)
    try:
        return importlib.import_module(spec)
    except ImportError:
        pass
    module_name, _, attr = spec.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def app_routes() -> list[tuple[str, str]]:
    from shine.main import app

    out: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                out.append((method, path))
    return out


def load_declarations(extra: str | None):
    from shine.copilot import declarations as base
    from shine.copilot.contracts import CapabilityDeclaration, FeatureDeclaration

    features = tuple(base.FEATURES)
    writes = tuple(base.WRITE_ACTIONS)
    reads = tuple(base.READ_ACTIONS)
    if not extra:
        return base, features, writes, reads
    spec = importlib.util.spec_from_file_location("shine_copilot_extra_declarations", extra)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load extra declarations: {extra}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extra_features = tuple(getattr(module, "FEATURES", ()) or ())
    extra_writes = tuple(getattr(module, "ACTIONS", ()) or ())
    extra_reads = tuple(getattr(module, "READ_ACTIONS", ()) or ())
    for row in (*extra_features,):
        if not isinstance(row, FeatureDeclaration):
            raise SystemExit("extra declarations: FEATURES must be FeatureDeclaration rows")
    for row in (*extra_writes, *extra_reads):
        if not isinstance(row, CapabilityDeclaration):
            raise SystemExit("extra declarations: ACTIONS must be CapabilityDeclaration rows")
    return base, features + extra_features, writes + extra_writes, reads + extra_reads


def _route_paths(path: str) -> list[str]:
    """``/x/pause|resume|cancel`` means three real routes, not a literal pipe."""
    if "|" not in path:
        return [path]
    parts = path.split("|")
    prefix, _, last = parts[0].rpartition("/")
    return [f"{prefix}/{name.strip()}" for name in [last, *parts[1:]]]


def _declared_route_keys(row) -> list[str]:
    from shine.copilot.build import route_key

    if not row.api_route or " " not in row.api_route:
        return []
    method, path = row.api_route.split(" ", 1)
    return [route_key(method, part) for part in _route_paths(path)]


def validate_declarations(
    features, writes, reads, routes: list[tuple[str, str]]
) -> list[str]:
    from shine.copilot.build import route_key
    from shine.copilot.contracts import feature_index

    issues: list[str] = []
    known_routes = {route_key(method, path) for method, path in routes}
    feature_ids = feature_index(features)
    write_ids = {row.action_id for row in writes}
    seen: set[str] = set()

    for row in writes:
        prefix = f"action {row.action_id}"
        if row.action_id in seen:
            issues.append(f"{prefix}: duplicate action_id")
        seen.add(row.action_id)
        if row.feature_id not in feature_ids:
            issues.append(f"{prefix}: unknown feature_id {row.feature_id}")
        if row.permission_tier not in {"PREVIEW", "EXECUTE_GUARDED"}:
            issues.append(f"{prefix}: write action must be PREVIEW/EXECUTE_GUARDED")
        if not row.executor:
            issues.append(f"{prefix}: missing executor")
        else:
            try:
                _import_module_or_object(row.executor)
            except (ImportError, AttributeError) as exc:
                issues.append(f"{prefix}: executor not importable ({row.executor}: {exc})")
        keys = _declared_route_keys(row)
        if not keys:
            issues.append(f"{prefix}: missing api_route")
        for key in keys:
            if key not in known_routes:
                issues.append(f"{prefix}: route not served ({key})")
        if row.request_schema:
            try:
                model = _import_object(row.request_schema)
            except (ImportError, AttributeError) as exc:
                issues.append(f"{prefix}: request schema not importable ({row.request_schema}: {exc})")
                model = None
            if model is not None:
                fields = set(getattr(model, "model_fields", {}) or {})
                for param in row.params:
                    if param.binding == "body" and param.name not in fields:
                        issues.append(
                            f"{prefix}: param {param.name} is not a field of {row.request_schema}"
                        )
        for param in row.params:
            if param.binding == "path" and keys:
                if "{" + param.name + "}" not in row.api_route:
                    issues.append(f"{prefix}: path param {param.name} is not in the route")
        if row.authority.startswith(("GET ", "POST ", "PATCH ", "PUT ")):
            for key in _readback_keys(row.authority):
                if key not in known_routes:
                    issues.append(f"{prefix}: read-back route not served ({key})")
        if row.availability == "AVAILABLE":
            if not row.executor_bound:
                issues.append(f"{prefix}: AVAILABLE without executor_bound")
            dispatch = row.dispatch or row.action_id
            if dispatch not in write_ids:
                issues.append(
                    f"{prefix}: AVAILABLE without a reviewed dispatcher "
                    f"(dispatch={dispatch!r})"
                )
            if not keys:
                issues.append(f"{prefix}: AVAILABLE without a real route")

    for row in reads:
        prefix = f"read {row.action_id}"
        if row.action_id in seen:
            issues.append(f"{prefix}: duplicate action_id")
        seen.add(row.action_id)
        if row.feature_id not in feature_ids:
            issues.append(f"{prefix}: unknown feature_id {row.feature_id}")
        if row.permission_tier != "READ":
            issues.append(f"{prefix}: read action must be READ")
        if row.executor:
            try:
                _import_module_or_object(row.executor)
            except (ImportError, AttributeError) as exc:
                issues.append(f"{prefix}: executor not importable ({row.executor}: {exc})")
        for key in _declared_route_keys(row):
            if key not in known_routes:
                issues.append(f"{prefix}: route not served ({key})")

    for feature in features:
        prefix = f"feature {feature.feature_id}"
        if feature.status not in {"IMPLEMENTED", "PARTIAL", "PLANNED", "BLOCKED", "NOT_SUPPORTED"}:
            issues.append(f"{prefix}: unknown implementation status {feature.status}")
        if feature.verification not in {"AUTOMATED", "HARDWARE_REQUIRED", "NOT_RUN", "BLOCKED"}:
            issues.append(f"{prefix}: unknown verification status {feature.verification}")
        if not feature.description.strip():
            issues.append(f"{prefix}: missing description")
        for source in feature.sources:
            if not (REPO / source).exists():
                issues.append(f"{prefix}: source path missing ({source})")
        for api in feature.apis:
            for part in api.split(", "):
                if " " not in part:
                    continue
                method, path = part.split(" ", 1)
                if method.count("/") or not method.isupper():
                    continue
                key = route_key(method, path)
                if key not in known_routes:
                    issues.append(f"{prefix}: declared API not served ({key})")
    return issues


def _readback_keys(value: str) -> list[str]:
    from shine.copilot.build import route_key

    method, path = value.split(" ", 1)
    return [route_key(method, part) for part in _route_paths(path)]


_TIER_ORDER = {"READ": 0, "PREVIEW": 1, "EXECUTE_GUARDED": 2}


def compare_with_previous(bundle: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """Contract drift: an upgrade must never silently reuse old authorizations."""
    if not previous:
        return []
    issues: list[str] = []
    old = {row["action_id"]: row for row in previous.get("capabilities") or []}
    new = {row["action_id"]: row for row in bundle.get("capabilities") or []}
    breaking: list[str] = []

    for action_id, row in new.items():
        before = old.get(action_id)
        if before is None:
            continue
        if before.get("contract_digest") == row.get("contract_digest"):
            continue
        revision = int(row.get("contract_revision") or 0)
        old_revision = int(before.get("contract_revision") or 0)
        if revision <= old_revision:
            issues.append(
                f"action {action_id}: contract changed but contract_revision not bumped "
                f"({old_revision} -> {revision})"
            )
        changes: list[str] = []
        if set(before.get("params") or []) - set(row.get("params") or []):
            changes.append("parameters removed")
        if _TIER_ORDER.get(str(row.get("tier")), 0) > _TIER_ORDER.get(str(before.get("tier")), 0):
            changes.append("permission tier raised")
        if bool(row.get("gpu")) and not bool(before.get("gpu")):
            changes.append("GPU cost added")
        if bool(row.get("paid")) and not bool(before.get("paid")):
            changes.append("paid cost added")
        if before.get("executor") != row.get("executor"):
            changes.append("executor changed")
        if before.get("api_route") != row.get("api_route"):
            changes.append("API route changed")
        if before.get("availability") == "AVAILABLE" and row.get("availability") != "AVAILABLE":
            changes.append("execution withdrawn")
        if before.get("scope") != row.get("scope"):
            changes.append("project scope changed")
        if changes:
            breaking.append(f"action {action_id}: " + ", ".join(changes))

    for action_id in old:
        if action_id not in new:
            breaking.append(f"action {action_id}: removed from the registry")

    for message in breaking:
        issues.append(f"BREAKING {message} (pass --allow-breaking after review)")
    return issues


def build(args) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    from shine.copilot.build import build_bundle, manifest_payload

    base, features, writes, reads = load_declarations(getattr(args, "extra_declarations", None))
    routes = app_routes()
    issues = validate_declarations(features, writes, reads, routes)
    bundle = build_bundle(
        features=features,
        write_actions=writes,
        read_actions=reads,
        human=base.HUMAN_OPERATIONS,
        not_supported=base.NOT_SUPPORTED_OPERATIONS,
        matrix=base.MATRIX,
        forbidden=base.FORBIDDEN,
        source_commit=_source_commit(getattr(args, "commit", None)) or "",
        routes=routes,
    )
    previous = None if getattr(args, "output_dir", None) else _read_json(ARTIFACT)
    issues.extend(compare_with_previous(bundle, previous))
    return bundle, manifest_payload(bundle, source_commit=bundle["source_commit"]), issues


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def compare_artifacts(bundle: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """Cheap, deterministic comparison used by --check."""
    if not previous:
        return ["artefact missing; run the generator"]
    issues: list[str] = []
    for key in ("version", "app_version", "knowledge_version"):
        if previous.get(key) != bundle.get(key):
            issues.append(f"artefact {key} differs ({previous.get(key)!r} != {bundle.get(key)!r})")
    if (previous.get("knowledge") or {}).get("content_sha256") != bundle["knowledge"]["content_sha256"]:
        issues.append("knowledge content fingerprint differs; regenerate the artefact")
    for key in ("contracts", "api_schema"):
        if (previous.get("digests") or {}).get(key) != bundle["digests"][key]:
            issues.append(f"{key} digest differs; regenerate the artefact")
    old = {row.get("action_id"): row for row in previous.get("capabilities") or []}
    new = {row.get("action_id"): row for row in bundle.get("capabilities") or []}
    if set(old) != set(new):
        issues.append("capability set differs; regenerate the artefact")
    for action_id in new:
        if action_id in old and old[action_id].get("contract_digest") != new[action_id].get("contract_digest"):
            issues.append(f"action {action_id}: contract differs; regenerate the artefact")
    old_discovered = {row.get("route") for row in previous.get("discovered") or []}
    new_discovered = {row.get("route") for row in bundle.get("discovered") or []}
    added = new_discovered - old_discovered
    removed = old_discovered - new_discovered
    if added or removed:
        issues.append(
            "undeclared route set changed "
            f"(+{len(added)}/-{len(removed)}); regenerate and review DISCOVERED routes"
        )
    return issues


def compare_manifest(bundle: dict[str, Any], manifest: dict[str, Any] | None, *, check_commit: bool) -> list[str]:
    if not manifest:
        return ["knowledge manifest missing; run the generator"]
    expected = {
        "knowledge_version": bundle["knowledge_version"],
        "content_sha256": bundle["knowledge"]["content_sha256"],
        "entry_count": bundle["knowledge"]["entry_count"],
        "contracts_digest": bundle["digests"]["contracts"],
        "capability_count": len(bundle["capabilities"]),
        "app_version": bundle["app_version"],
    }
    issues = [
        f"manifest {key} differs ({manifest.get(key)!r} != {value!r})"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    recorded_commit = str(manifest.get("source_commit") or "")
    if check_commit and recorded_commit and recorded_commit != bundle["source_commit"]:
        issues.append(
            f"manifest source_commit differs ({recorded_commit!r} != {bundle['source_commit']!r})"
        )
    return issues


def write_outputs(bundle: dict[str, Any], manifest: dict[str, Any], *, output_dir: Path | None = None) -> None:
    from shine.copilot.build import serialized

    artifact_path = (output_dir / "copilot_contracts.json") if output_dir else ARTIFACT
    manifest_path = (output_dir / "knowledge_manifest.json") if output_dir else MANIFEST
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(serialized(bundle), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {artifact_path} app={bundle['app_version']} actions={len(bundle['capabilities'])} "
        f"knowledge={bundle['knowledge']['entry_count']} discovered={len(bundle['discovered'])}"
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None, help="build source commit")
    parser.add_argument("--output-dir", default=None, help="write artefacts here instead of the repo")
    parser.add_argument("--extra-declarations", default=None, help="file with extra declarations (tests)")
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    parser.add_argument("--allow-breaking", action="store_true", help="accept reviewed breaking changes")
    parser.add_argument(
        "--check-commit",
        action="store_true",
        help="also require the manifest's source commit to match the build",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    bundle, manifest, issues = build(args)
    blocking = [issue for issue in issues if "BREAKING" in issue or "not bumped" in issue]
    rest = [issue for issue in issues if issue not in blocking]
    if blocking and not args.allow_breaking:
        for issue in blocking:
            print(f"FAIL {issue}", file=sys.stderr)
        return 1
    if blocking:
        print("NOTE reviewed breaking changes accepted via --allow-breaking", file=sys.stderr)
    if rest:
        for issue in rest:
            print(f"FAIL {issue}", file=sys.stderr)
        return 1

    if args.check:
        issues = compare_artifacts(bundle, _read_json(ARTIFACT))
        issues.extend(compare_manifest(bundle, _read_json(MANIFEST), check_commit=args.check_commit))
        if issues:
            for issue in issues:
                print(f"FAIL {issue}", file=sys.stderr)
            return 1
        print(
            f"contracts ok: app={bundle['app_version']} actions={len(bundle['capabilities'])} "
            f"knowledge={bundle['knowledge']['entry_count']} discovered={len(bundle['discovered'])}"
        )
        return 0

    output_dir = Path(args.output_dir) if args.output_dir else None
    write_outputs(bundle, manifest, output_dir=output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Idempotent reconciliation of what is already on this machine.

The setup page must never ask a person to "register" a fact the system can
prove itself.  This module is the one place that turns read-only discovery into
durable evidence:

``discover`` (bounded, read-only) → ``inspect`` (sizes only) → ``verify``
(SHA-256, receipt-cached) → ``register`` (package rows + receipts) → ``adopt``
(the one confirmation that changes how the instance is represented).

It is deliberately *not* a second installer, a second scheduler or a second
state machine.  It calls the existing verifier and the existing registry; every
long download still belongs to the existing installation Worker, and every
process decision still belongs to ``runtime_orchestrator``.

Two properties matter more than speed:

* **Idempotent.** Running it twice registers nothing twice and hashes nothing
  twice (the receipt sidecar is keyed by path, size and modification time).
* **Single-flight per target.** Two tabs (or the page and the Worker) ask the
  same question once; the second caller gets ``IN_PROGRESS`` instead of a
  duplicate walk over tens of gigabytes.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from shine.comfyops import deployment, verification_receipts

logger = logging.getLogger(__name__)

_active: set[tuple[str, str]] = set()
_active_lock = threading.Lock()

#: The image capability's catalogue.  Kept here as a name, not as a second
#: deployment model: the catalogue itself lives in ``config/``.
IMAGE_CAPABILITY_CATALOG = deployment.IMAGE_CAPABILITY_CATALOG


def _capability_catalog(capability: str) -> dict:
    if capability == "image":
        return deployment.load_deployment_catalog(IMAGE_CAPABILITY_CATALOG)
    if capability == "relayvsr":
        return deployment.load_deployment_catalog(deployment.RELAYVSR_CATALOG)
    return deployment.load_default_catalog()


def _required_keys(catalog: dict, capability: str) -> set[tuple[str, str, str, int]]:
    """The bytes this capability's catalog declares.

    ``kind`` is the only filter: a review model is not a video weight, but both
    are real files of the same catalog and both may be imported by hand.
    """
    if capability in {"h3", "image"}:
        from shine.comfyops.adoption import _required_capability_files

        return _required_capability_files(catalog, capability)
    keys = set()
    for component in catalog.get("components") or []:
        if component.get("kind") not in {"model", "review-model"}:
            continue
        for item in component.get("files") or []:
            keys.add((component["component_id"], item["relative_path"], item["sha256"], item["size"]))
    return keys


def _claim(key: tuple[str, str]) -> bool:
    with _active_lock:
        if key in _active:
            return False
        _active.add(key)
        return True


def _release(key: tuple[str, str]) -> None:
    with _active_lock:
        _active.discard(key)


def _known_port(db, root: str) -> int | None:
    """The port this installation is already registered under, if any.

    Reading it back keeps an adoption on a non-default port (8189) on *that*
    port instead of silently re-registering the installation as 8188.  The
    row is found by the same single resolver adoption uses (R5 B1), so a
    legacy duplicate can never answer with an arbitrary iteration order.
    """
    from shine.comfyops.instances import find_adopted_instance

    try:
        row = find_adopted_instance(db, root)
        port = (row.payload if row is not None else {}).get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            return port
    except Exception:  # noqa: BLE001 - an unreadable row is not a port
        return None
    return None


def _with_scan_candidates(report: dict, candidates: list[dict]) -> dict:
    """Offer the search index's candidates to the same verifier.

    A renamed weight in a directory the person added is a real file on this
    machine: the reconciler reads it, hashes it and — if it is the pinned bytes —
    uses it instead of downloading it again.  The report keeps its shape, so the
    one verifier (``verify_models``) stays the only judge of "these are the
    pinned bytes"; the snapshot is recomputed because the candidate list is part
    of what was measured.
    """
    from shine.comfy.workflow import graph_hash

    if not candidates:
        return report
    by_key = {(row["component_id"], row["relative_path"]): row for row in report["matches"]}
    for row in candidates:
        key = (row["component_id"], row["relative_path"])
        entry = by_key.get(key)
        if entry is None:
            entry = {
                "component_id": row["component_id"], "relative_path": row["relative_path"],
                "sha256": row["sha256"], "size": row["size"],
                "sources": [], "source_stats": {}, "status": "PENDING_HASH",
            }
            report["matches"].append(entry)
            by_key[key] = entry
        if row["path"] not in entry["sources"]:
            entry["sources"].append(row["path"])
            entry.setdefault("source_stats", {})[row["path"]] = row["stat"]
    payload = {key: value for key, value in report.items() if key != "snapshot"}
    report["snapshot"] = graph_hash(payload)
    return report


def _is_managed_non_adopted_instance(db, settings, path: str | None) -> bool:
    """Whether *path* is already this workbench's own non-adopted managed instance."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        if resolved.is_relative_to((Path(settings.comfyops_dir) / "instances").resolve()):
            return True
    except OSError:
        return False
    from sqlalchemy import select

    from shine.comfyops.models import ComfyInstance

    for row in db.scalars(select(ComfyInstance)):
        payload = row.payload if isinstance(row.payload, dict) else {}
        if not payload.get("managed") or payload.get("adopted") or not payload.get("root"):
            continue
        try:
            if Path(payload["root"]).resolve() == resolved:
                return True
        except OSError:
            continue
    return False


def reconcile_installation(db, settings, *, path: str | None, project_id: str | None = None,
                           capability: str = "h3", adopt: bool = False, port: int | None = None,
                           actor: str = "local-user", variant_id: str | None = None,
                           claim_guard=None) -> dict[str, Any]:
    """Prove what is there, register it, and optionally confirm the instance.

    ``adopt`` is the single authorization this function can consume.  Without
    it the installation is only *read*: discovered, hashed, registered.  Nothing
    about the user's own ComfyUI tree is written, moved or terminated — the one
    additive exception is RelayVSR's pinned weights (user ruling 2026-11-17):
    the upscale runs on that core, so they are placed into its models directory;
    existing files are never overwritten or deleted.

    ``variant_id`` names the H3 stack the person chose: the bytes are then
    verified against that variant's own catalog, never the default one.

    The reconciler also consumes the resource scan's index: a byte-identical
    file in a directory the person added is verified, registered and copied into
    the existing content-addressed cache, so the installer places those bytes
    locally and never downloads them a second time.
    """
    from shine.comfy.workflow import graph_hash
    from shine.comfyops import resource_discovery
    from shine.comfyops.adoption import _verified_key, adopt_existing_installation
    from shine.comfyops.existing_installation import (
        import_models,
        inspect,
        register_verified_packages,
        verify_models,
    )
    from shine.comfyops.verification_receipts import cache_candidates, installed_directory
    from shine.services import add_audit
    key = (str(Path(path or settings.comfyops_dir).resolve()), capability)
    if not _claim(key):
        return {"status": "IN_PROGRESS", "path": str(key[0]), "capability": capability,
                "registered": [], "verified": 0, "downloaded_bytes": 0}
    try:
        if capability == "h3" and variant_id:
            from shine.comfyops.variant_binding import load_binding

            catalog = load_binding(variant_id).catalog
        else:
            catalog = _capability_catalog(capability)
        served_placed: list[str] = []
        if capability == "relayvsr" and path:
            # Place verified bytes before inspection so the receipt binds the
            # exact file the renderer will load, including freshly downloaded weights.
            if claim_guard is not None:
                claim_guard(db)
            served_placed = _place_relayvsr_served(db, settings, catalog, Path(path) / "models", actor=actor)
        if path:
            report = inspect(path, catalog)
        else:
            report = {"root": None, "catalog_id": catalog["catalog_id"], "matches": [], "software": {}}
            report["snapshot"] = graph_hash(report)
        candidates = resource_discovery.verification_candidates(db, catalog)
        for component in catalog.get("components") or []:
            if component.get("kind") not in {"model", "review-model"}:
                continue
            directory = installed_directory(settings, component)
            for item in component.get("files") or []:
                source = directory / item["relative_path"] if directory else None
                if source is not None and source.is_file():
                    from shine.comfyops.existing_installation import _stat_identity

                    candidates.append({**item, "component_id": component["component_id"],
                                       "path": str(source), "stat": _stat_identity(source.stat())})
        report = _with_scan_candidates(report, candidates)
        cache_root = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
        result = verify_models(report, report["snapshot"], cache_dir=cache_root)
        verified = list(result.get("verified") or [])
        rejected = list(result.get("rejected") or [])
        # 手动收料目录属于同一条收料链：整备链与独立入口都经这个执行者，
        # 不再只有单独 API 认识 manual-downloads（否则一键整备仍会重排下载）。
        manual = manual_files_reconcile(db, settings, capability=capability, actor=actor, catalog=catalog)
        # 已入缓存的字节本身就是核验证据（与 capability_report 同一读法）：
        # 收料函数跳过已缓存文件，重复核验不得因此把 complete 翻回 false。
        seen = {_verified_key(row) for row in verified}
        for component in catalog.get("components") or []:
            if component.get("kind") not in {"model", "review-model"}:
                continue
            candidates_in_cache = [item for item in component.get("files") or []
                                   if (component["component_id"], item["relative_path"],
                                       item["sha256"], item["size"]) not in seen
                                   and (cache_root / item["sha256"]).is_file()]
            checked = cache_candidates(cache_root, {**component, "files": candidates_in_cache})
            for item, evidence in zip(candidates_in_cache, checked["files"], strict=True):
                if evidence["verified"]:
                    verified.append({**item, "component_id": component["component_id"],
                                     "source": str(cache_root / item["sha256"])})
        # RelayVSR 精修跑在既有 ComfyUI 核心上（2026-11-17 用户裁定）：已核验字节
        # 必须加性落位到该安装的 models 目录，readiness 与执行读同一个根。
        # 只加钉住文件：同名不同字节拒绝覆盖，已存在则跳过。
        if claim_guard is not None:
            claim_guard(db)
        from shine.comfyops.local import reconcile_stopped_isolated_attempts

        reconciled_attempts = reconcile_stopped_isolated_attempts(
            db, settings, actor=actor, evidence=f"setup-reconcile:{capability}"
        )
        if capability == "relayvsr" and path:
            # Manual intake may have filled the cache during this reconciliation.
            served_placed.extend(_place_relayvsr_served(db, settings, catalog, Path(path) / "models", actor=actor))
        candidate_paths = {row["path"] for row in candidates}
        candidate_dirs = [cache_root, *(Path(row["path"]).parent for row in candidates)]
        registered = register_verified_packages(db, verified, catalog, root=report["root"],
                                                extra_roots=candidate_dirs)
        verified_keys = {_verified_key(row) for row in verified}
        required = _required_keys(catalog, capability)
        complete = bool(required) and required <= verified_keys
        adoption = None
        managed_own_instance = _is_managed_non_adopted_instance(db, settings, path)
        if adopt and path and not managed_own_instance and capability in {"h3", "image"}:
            if complete:
                try:
                    adoption = adopt_existing_installation(
                        db, settings, project_id=project_id, path=report["root"],
                        snapshot=report["snapshot"], port=int(port or _known_port(db, report["root"]) or 8188),
                        actor=actor, capability=capability, catalog=catalog, inspected=report, verified=verified,
                        claim_guard=claim_guard,
                    )
                except HTTPException as exc:
                    detail = exc.detail
                    adoption = {"status": "REFUSED",
                                "code": detail.get("code") if isinstance(detail, dict) else detail}
            else:
                adoption = {"status": "FILES_INCOMPLETE",
                            "missing_components": sorted({row[0] for row in required - verified_keys})}
        if claim_guard is not None:
            claim_guard(db)
        db.commit()
        # Stage verified bytes for the existing installer when they are outside
        # the installation or the installation cannot be adopted.
        adopted = bool((adoption or {}).get("instance_id"))
        relay_models_root = Path(path) / "models" if capability == "relayvsr" and path else None
        to_cache = [row for row in verified if Path(row["source"]).parent != cache_root
                    and (relay_models_root is None
                         or Path(row["source"]) != (relay_models_root / row["relative_path"]).resolve())
                    and ((not adopted and row["source"] in candidate_paths)
                         or (adoption is not None and not adopted and _verified_key(row) in required))]
        imported = import_models(settings, {"matches": to_cache, "snapshot": report["snapshot"]},
                                 report["snapshot"]).get("imported", []) if to_cache else []
        add_audit(db, actor, "SETUP_FACTS_RECONCILED", "deployment", report["catalog_id"], {
            "root": report["root"], "snapshot": report["snapshot"], "capability": capability,
            "verified_components": sorted({row["component_id"] for row in verified}),
            "rejected_components": sorted({row["component_id"] for row in rejected}),
            "registered": sorted(registered), "adopted": adopted,
            "scan_candidates": len(candidates), "cached_from_scan": len(imported),
            "reconciled_attempts": [row["attempt_id"] for row in reconciled_attempts],
        }, project_id=project_id)
        payload: dict[str, Any] = {
            "status": "RECONCILED",
            "path": report["root"],
            "snapshot": report["snapshot"],
            "capability": capability,
            "catalog": catalog.get("catalog_id"),
            "verified": len(verified),
            "rejected": len(rejected),
            "verified_bytes": result.get("verified_bytes", 0),
            "registered": sorted(registered),
            "complete": complete,
            "missing_components": sorted({row[0] for row in required - verified_keys}),
            "scan_candidates": len(candidates),
            "cached_from_scan": [row["sha256"] for row in imported],
            "manual_imported_bytes": manual.get("imported_bytes", 0),
            "served_root_placed": served_placed,
            "reconciled_attempts": reconciled_attempts,
            "downloaded_bytes": 0,
            "copied_bytes": sum(int(row.get("size") or 0) for row in imported),
            "user_install_modified": False,
        }
        if adoption is not None:
            payload["adoption"] = adoption
            if adopted:
                add_audit(db, actor, "SETUP_INSTALLATION_ADOPTED_BY_RECONCILE",
                          "deployment", report["catalog_id"],
                          {"status": adoption["status"], "capability": capability}, project_id=project_id)
        db.commit()
        return payload
    finally:
        _release(key)


# --- manual downloads -------------------------------------------------------


def _safe_manual_root(settings) -> Path:
    return (Path(settings.comfyops_dir) / "manual-downloads").resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root) and not path.is_symlink()
    except (OSError, ValueError):
        return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manual_files_reconcile(db, settings, *, capability: str = "h3",
                           actor: str = "local-user", catalog: dict | None = None) -> dict[str, Any]:
    """Adopt files a person downloaded by hand, without a second downloader.

    The staging directory is fixed (``<deployment_root>/manual-downloads``); the
    request never carries a path, so no browser can name an arbitrary directory.
    A file is imported only when its full SHA-256 matches the pinned value, and
    it is imported *into the existing content-addressed cache* - the Worker
    still performs the install.  The original file is left where it is.
    """
    from shine.services import add_audit

    manual_root = _safe_manual_root(settings)
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    catalog = catalog or _capability_catalog(capability)
    imported, rejected, absent = [], [], []
    if not manual_root.is_dir():
        return {"status": "NO_STAGING_DIRECTORY", "manual_root": str(manual_root),
                "imported": [], "rejected": [], "absent": [], "imported_bytes": 0}
    by_size: dict[int, list[Path]] = {}
    for candidate in manual_root.rglob("*"):
        if not candidate.is_file():
            continue
        if not _is_inside(candidate, manual_root):
            rejected.append({"path": str(candidate), "code": "MANUAL_FILE_OUTSIDE_STAGING_ROOT"})
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if stat.st_size > 200 * 1024 ** 3:
            rejected.append({"path": str(candidate), "code": "MANUAL_FILE_TOO_LARGE"})
            continue
        by_size.setdefault(stat.st_size, []).append(candidate)

    for component in catalog.get("components") or []:
        if component.get("kind") not in {"model", "review-model"}:
            continue
        for item in component.get("files") or []:
            expected_sha = str(item.get("sha256") or "").lower()
            size = int(item.get("size") or 0)
            target = cache / expected_sha if expected_sha else None
            # R5 B3：判同走同一回执执行者（stat+头尾见证复用）。已缓存字节
            # 不得每轮全量重读；字节真变了仍会被见证/哈希揪出，威胁模型不变。
            if target is not None and target.is_file() and verification_receipts.file_digest(
                    target, expected_sha, size, cache_dir=cache)["verified"]:
                continue  # already cached and verified - never copy twice
            match = None
            for candidate in by_size.get(size, []):
                if verification_receipts.file_digest(
                        candidate, expected_sha, size, cache_dir=cache)["verified"]:
                    match = candidate
                    break
            if match is None:
                absent.append({"component_id": component["component_id"],
                               "relative_path": item["relative_path"], "size": size})
                continue
            try:
                cache.mkdir(parents=True, exist_ok=True)
                partial = cache / (expected_sha + ".manual-partial")
                shutil.copyfile(match, partial)
                if _hash_file(partial) != expected_sha:
                    partial.unlink(missing_ok=True)
                    rejected.append({"path": str(match), "code": "MANUAL_FILE_CHANGED_WHILE_COPYING"})
                    continue
                partial.replace(target)
                imported.append({"component_id": component["component_id"],
                                 "relative_path": item["relative_path"],
                                 "sha256": expected_sha, "size": size, "source": str(match)})
            except OSError as exc:
                rejected.append({"path": str(match), "code": type(exc).__name__})
    add_audit(db, actor, "SETUP_MANUAL_FILES_RECONCILED", "deployment", capability, {
        "manual_root": str(manual_root),
        "imported": [row["sha256"] for row in imported],
        "rejected": [row["code"] for row in rejected],
        "absent": len(absent),
        "copied_into_cache_only": True,
    })
    db.commit()
    return {
        "status": "RECONCILED",
        "manual_root": str(manual_root),
        "cache_root": str(cache),
        "capability": capability,
        "imported": imported,
        "rejected": rejected,
        "absent": absent,
        "imported_bytes": sum(row["size"] for row in imported),
    }


def _place_relayvsr_served(db, settings, catalog: dict, served_root: Path, *, actor: str) -> list[str]:
    """Additively place verified RelayVSR bytes where the executing ComfyUI loads.

    User ruling 2026-11-17: RelayVSR post-production runs on the existing ComfyUI
    core, so its install target is that core's models directory — the same root
    ``relayvsr_readiness`` and the upscale graph read.  Only pinned files are
    added: identical bytes skip, different bytes are refused by name and never
    overwritten or deleted.
    """
    from shine.services import add_audit

    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    placed: list[str] = []
    refused: list[dict] = []
    for component in catalog.get("components") or []:
        if component.get("kind") != "model":
            continue
        for item in component.get("files") or []:
            sha = str(item.get("sha256") or "")
            source = cache / sha
            target = served_root / str(item["relative_path"])
            if not source.is_file():
                continue
            if target.is_file():
                # R5 B3：已落位文件判同同样走回执复用，不每轮全量重读。
                same = verification_receipts.file_digest(
                    target, sha, int(item["size"]), cache_dir=cache)["verified"]
                if not same:
                    refused.append({"path": str(target), "code": "SERVED_TARGET_BYTES_DIFFER"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".shine-partial")
            shutil.copyfile(source, partial)
            if _hash_file(partial) != sha:
                partial.unlink(missing_ok=True)
                refused.append({"path": str(target), "code": "SERVED_COPY_HASH_MISMATCH"})
                continue
            partial.replace(target)
            placed.append(str(item["relative_path"]))
    if placed or refused:
        add_audit(db, actor, "SETUP_RELAYVSR_SERVED_ROOT_PLACED", "deployment",
                  catalog.get("catalog_id") or "relayvsr",
                  {"served_root": str(served_root), "placed": placed, "refused": refused})
        db.commit()
    return placed

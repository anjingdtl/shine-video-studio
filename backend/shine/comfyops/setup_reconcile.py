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

from shine.comfyops import deployment

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
        return deployment.load_deployment_catalog("relayvsr-x2-windows-x64")
    return deployment.load_default_catalog()


def _required_keys(catalog: dict, capability: str) -> set[tuple[str, str, str, int]]:
    """The bytes this capability's catalog declares.

    ``kind`` is the only filter: a review model is not a video weight, but both
    are real files of the same catalog and both may be imported by hand.
    """
    del capability
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
    port instead of silently re-registering the installation as 8188.
    """
    from sqlalchemy import select

    from shine.comfyops.models import ComfyInstance

    try:
        resolved = str(Path(root).resolve())
        for row in db.scalars(select(ComfyInstance)):
            payload = row.payload if isinstance(row.payload, dict) else {}
            if payload.get("root") and str(Path(payload["root"]).resolve()) == resolved:
                port = payload.get("port")
                if isinstance(port, int) and not isinstance(port, bool):
                    return port
    except Exception:  # noqa: BLE001 - an unreadable row is not a port
        return None
    return None


def reconcile_installation(db, settings, *, path: str, project_id: str | None = None,
                           capability: str = "h3", adopt: bool = False, port: int | None = None,
                           actor: str = "local-user", variant_id: str | None = None) -> dict[str, Any]:
    """Prove what is there, register it, and optionally confirm the instance.

    ``adopt`` is the single authorization this function can consume.  Without
    it the installation is only *read*: discovered, hashed, registered.  Nothing
    about the user's own ComfyUI tree is written, moved or terminated.

    ``variant_id`` names the H3 stack the person chose: the bytes are then
    verified against that variant's own catalog, never the default one.
    """
    from shine.comfyops.existing_installation import inspect, register_verified_packages, verify_models
    from shine.services import add_audit
    key = (str(Path(path).resolve()), capability)
    if not _claim(key):
        return {"status": "IN_PROGRESS", "path": str(key[0]), "capability": capability,
                "registered": [], "verified": 0, "downloaded_bytes": 0}
    try:
        required_component_ids: set[str] | None = None
        if capability == "h3" and variant_id:
            from shine.comfyops.variant_binding import load_binding
            from shine.comfyops.variant_readiness import _required_components

            binding = load_binding(str(variant_id), fresh=False)
            catalog = binding.catalog
            required_component_ids = {str(row.get("component_id"))
                                      for row in _required_components(catalog, binding.profiles)}
        else:
            catalog = _capability_catalog(capability)
        report = inspect(path, catalog)
        result = verify_models(report, report["snapshot"])
        verified = list(result.get("verified") or [])
        rejected = list(result.get("rejected") or [])
        registered = register_verified_packages(db, verified, catalog, root=report["root"])
        verified_keys = {(row["component_id"], row["relative_path"], row["sha256"], row["size"])
                         for row in verified}
        required = _required_keys(catalog, capability)
        if required_component_ids is not None:
            required = {row for row in required if row[0] in required_component_ids}
        complete = bool(required) and required <= verified_keys
        add_audit(db, actor, "SETUP_FACTS_RECONCILED", "deployment", report["catalog_id"], {
            "root": report["root"], "snapshot": report["snapshot"], "capability": capability,
            "verified_components": sorted({row["component_id"] for row in verified}),
            "rejected_components": sorted({row["component_id"] for row in rejected}),
            "registered": sorted(registered), "adopted": False,
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
            "downloaded_bytes": 0,
            "copied_bytes": 0,
            "user_install_modified": False,
        }
        if adopt:
            if not project_id:
                payload["adoption"] = {"status": "PROJECT_REQUIRED"}
            elif not complete:
                payload["adoption"] = {"status": "FILES_INCOMPLETE",
                                       "missing_components": payload["missing_components"]}
            else:
                from shine.comfyops.adoption import adopt_existing_installation

                try:
                    resolved_port = int(port or _known_port(db, report["root"]) or 8188)
                    adoption = adopt_existing_installation(
                        db, settings, project_id=project_id, path=report["root"],
                        snapshot=report["snapshot"], port=resolved_port,
                        actor=actor, capability=capability, catalog=catalog,
                    )
                    payload["adoption"] = adoption
                    add_audit(db, actor, "SETUP_INSTALLATION_ADOPTED_BY_RECONCILE",
                              "deployment", report["catalog_id"],
                              {"status": adoption.get("status"), "capability": capability},
                              project_id=project_id)
                except HTTPException as exc:
                    detail = exc.detail
                    code = detail.get("code") if isinstance(detail, dict) else detail
                    payload["adoption"] = {"status": "REFUSED", "code": str(code)}
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
                           actor: str = "local-user") -> dict[str, Any]:
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
    catalog = _capability_catalog(capability)
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
            if target is not None and target.is_file():
                continue  # already cached and verified - never copy twice
            match = None
            for candidate in by_size.get(size, []):
                try:
                    if _hash_file(candidate) == expected_sha:
                        match = candidate
                        break
                except OSError:
                    continue
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

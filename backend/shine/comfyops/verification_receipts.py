"""Durable receipts for bytes that were really hash-verified on this machine.

A registry status is a claim about the past; it is not evidence that the bytes
under the current deployment directory still match the pinned revision.  J04
was exactly that gap: an old ``FILES_VERIFIED_PENDING_RUNTIME`` row (or a cache
file whose *name* was the digest) made a weight layer green although the files
belonged to another revision, or had the right length and the wrong bytes.

This module is the one place that answers "are these exact bytes here, now?".
It never downloads, never copies and never promotes a package: it records what
it read and reuses that reading only while the file's size and modification
time are unchanged.  A changed file is hashed again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

RECEIPT_SCHEMA = 1
#: AppSetting.key is String(80); the digest keeps the key bounded and scoped.
_KEY_PREFIX = "verify-receipt:"


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def component_identity(component: dict) -> str:
    """The content identity of one component: revision plus every pinned file.

    The variant and the catalog are recorded next to the receipt for audit, but
    the identity is the bytes.  Two catalogs that pin the same runtime files
    therefore reuse one verification, while a different checkpoint - even with
    a similar name - can never inherit it.
    """
    files = [
        {
            "relative_path": str(entry.get("relative_path") or ""),
            "sha256": str(entry.get("sha256") or "").lower(),
            "size": int(entry.get("size") or 0),
        }
        for entry in (component.get("files") or [])
    ]
    files.sort(key=lambda row: row["relative_path"])
    return hashlib.sha256(_canonical({
        "component_id": str(component.get("component_id") or ""),
        "revision": str(component.get("revision") or ""),
        "files": files,
    }).encode("utf-8")).hexdigest()


def _settings_key(variant_id: str, component_id: str) -> str:
    digest = hashlib.sha256(f"{variant_id}:{component_id}".encode()).hexdigest()
    return _KEY_PREFIX + digest[:48]


def _receipts_dir(cache_dir: Path | None) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / ".receipts"


def file_digest(path: Path, expected_sha256: str, expected_size: int,
                cache_dir: Path | None = None) -> dict:
    """Read one file and return whether it is exactly the pinned bytes.

    The sidecar stores the size and the modification time of the reading, not
    the file contents: a file that changed - even to the same length - gets a
    new stat and is hashed again.  A file whose stat is unchanged is not read
    twice; that is what makes "existing verification reuse" cost zero copies.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        return {"present": False, "verified": False, "reason": type(exc).__name__}
    expected_size = int(expected_size or 0)
    expected_sha256 = str(expected_sha256 or "").lower()
    if stat.st_size != expected_size:
        return {"present": True, "verified": False, "reason": "SIZE_MISMATCH",
                "size": stat.st_size, "expected_size": expected_size}
    sidecar = None
    directory = _receipts_dir(cache_dir)
    if directory is not None:
        sidecar = directory / f"{expected_sha256}.json"
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if (isinstance(cached, dict)
                and cached.get("path") == str(path.resolve())
                and int(cached.get("size") or -1) == stat.st_size
                and int(cached.get("mtime_ns") or -1) == stat.st_mtime_ns
                and cached.get("sha256") == expected_sha256):
            return {"present": True, "verified": True, "size": stat.st_size,
                    "reused": True}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return {"present": True, "verified": False, "reason": type(exc).__name__}
    actual = digest.hexdigest()
    if actual != expected_sha256:
        return {"present": True, "verified": False, "reason": "SHA256_MISMATCH",
                "sha256": actual, "size": stat.st_size}
    if sidecar is not None:
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps({
                "path": str(path.resolve()),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": expected_sha256,
                "verified_at": datetime.now(UTC).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return {"present": True, "verified": True, "size": stat.st_size, "reused": False}


def directory_files(directory: Path | None, component: dict,
                    recorded: dict | None = None) -> dict:
    """Check every pinned file below one installed package directory.

    ``recorded`` is the receipt's own per-file stat (relative path -> size and
    mtime).  When a file still has that exact stat it was already read by the
    verification that wrote the receipt, so it is not read again.  Any change
    invalidates the shortcut and the file is hashed.
    """
    rows = []
    if directory is None:
        return {"verified": False, "present": False, "files": rows,
                "reason": "DIRECTORY_UNKNOWN"}
    recorded = recorded or {}
    for entry in component.get("files") or []:
        relative = str(entry.get("relative_path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            rows.append({"relative_path": relative, "present": False, "verified": False,
                         "reason": "INVALID_RELATIVE_PATH"})
            continue
        target = directory / relative
        pinned = recorded.get(relative) or {}
        try:
            stat = target.stat()
        except OSError:
            stat = None
        if (stat is not None and pinned.get("size") == stat.st_size
                and pinned.get("mtime_ns") == stat.st_mtime_ns
                and pinned.get("sha256") == str(entry.get("sha256") or "").lower()):
            rows.append({"relative_path": relative, "present": True, "verified": True,
                         "size": stat.st_size, "reused": True})
            continue
        result = file_digest(target, str(entry.get("sha256") or ""),
                             int(entry.get("size") or 0))
        rows.append({"relative_path": relative, **result})
    present = all(row.get("present") for row in rows) if rows else False
    verified = bool(rows) and all(row.get("verified") for row in rows)
    return {"verified": verified, "present": present, "files": rows,
            "reason": None if verified else ("MISSING_FILES" if not present else "BYTES_CHANGED")}


def _recorded_file(directory: Path | None, entry: dict) -> dict:
    relative = str(entry.get("relative_path") or "")
    record = {"relative_path": relative,
              "sha256": str(entry.get("sha256") or "").lower(),
              "size": int(entry.get("size") or 0)}
    if directory is not None:
        try:
            stat = (directory / relative).stat()
            record["mtime_ns"] = stat.st_mtime_ns
        except OSError:
            record["mtime_ns"] = None
    return record


def save_receipt(db, *, variant_id: str | None, catalog_id: str | None,
                 catalog_version: str | None, component: dict,
                 root: Path | None, directory: Path | None) -> dict:
    """Record a complete, verified component under its content identity.

    Only a full set of matching files produces a receipt.  A receipt is a fact
    about this directory at this moment, so a later byte change is caught by
    :func:`verify_receipt`, not hidden by the row.
    """
    from shine.models import AppSetting

    component_id = str(component.get("component_id") or "")
    identity = component_identity(component)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "variant_id": variant_id,
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "component_id": component_id,
        "revision": str(component.get("revision") or ""),
        "identity": identity,
        "root": str(root) if root is not None else None,
        "directory": str(directory) if directory is not None else None,
        "files": [
            _recorded_file(directory, entry)
            for entry in (component.get("files") or [])
        ],
        "verified_at": datetime.now(UTC).isoformat(),
    }
    key = _settings_key(variant_id or "", component_id)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=receipt))
    else:
        row.value = receipt
    return receipt


def load_receipt(db, *, variant_id: str | None, component_id: str) -> dict | None:
    from shine.models import AppSetting

    row = db.get(AppSetting, _settings_key(variant_id or "", component_id))
    value = row.value if row is not None else None
    return value if isinstance(value, dict) else None


def find_any_receipt(db, component_id: str) -> dict | None:
    """The newest receipt for this component, whichever variant recorded it.

    A runtime node verified while installing the baseline is still the same
    pinned bytes when a candidate profile needs it.  The content identity is
    checked by the caller, so looking across variants does not widen evidence:
    it only finds a receipt that must still describe these exact files.
    """
    from sqlalchemy import select

    from shine.models import AppSetting

    rows = db.scalars(
        select(AppSetting).where(AppSetting.key.like(_KEY_PREFIX + "%"))
    ).all()
    matches = [row for row in rows
               if isinstance(row.value, dict) and row.value.get("component_id") == component_id]
    if not matches:
        return None
    # AppSetting has no timestamp; the key is stable per (variant, component),
    # so prefer a receipt that names a catalog over one that names nothing.
    matches.sort(key=lambda row: (row.value.get("catalog_id") or "", row.value.get("verified_at") or ""))
    return matches[-1].value


def verify_receipt(db, *, component: dict, variant_id: str | None,
                   catalog_id: str | None, catalog_version: str | None,
                   cache_dir: Path | None = None) -> dict:
    """Does a receipt still describe these bytes on this machine?

    A registry row, a same-named cache file or a receipt for another revision
    is not evidence.  The receipt must carry the same content identity and its
    recorded directory must still contain the exact pinned files.
    """
    component_id = str(component.get("component_id") or "")
    expected = component_identity(component)
    receipt = load_receipt(db, variant_id=variant_id, component_id=component_id)
    if receipt is None:
        # A component verified while installing another variant of the same
        # catalog still proves these bytes; try the catalog-level key too.
        receipt = load_receipt(db, variant_id=None, component_id=component_id)
    if receipt is None:
        receipt = find_any_receipt(db, component_id)
    if not isinstance(receipt, dict):
        return {"verified": False, "present": False, "reason": "NO_RECEIPT",
                "identity": expected, "receipt": None}
    if receipt.get("identity") != expected:
        return {"verified": False, "present": False, "reason": "RECEIPT_IDENTITY_CHANGED",
                "identity": expected, "receipt": receipt}
    if catalog_id and receipt.get("catalog_id") not in {None, catalog_id}:
        return {"verified": False, "present": False, "reason": "RECEIPT_CATALOG_CHANGED",
                "identity": expected, "receipt": receipt}
    directory = receipt.get("directory")
    recorded = {
        str(row.get("relative_path")): row
        for row in (receipt.get("files") or [])
        if isinstance(row, dict)
    }
    check = directory_files(Path(directory) if directory else None, component,
                            recorded=recorded)
    return {
        "verified": check["verified"],
        "present": check["present"],
        "reason": check["reason"],
        "identity": expected,
        "receipt": receipt,
        "files": check["files"],
    }


def cache_candidates(cache_dir: Path | None, component: dict) -> dict:
    """Check the verified-bytes cache by content, not by file name.

    A cache file is named after its digest, but a name is not a proof: the file
    is read (or its unchanged-stat receipt reused) before it counts.
    """
    rows = []
    if cache_dir is None:
        return {"verified": False, "present": False, "files": rows,
                "reason": "NO_CACHE_DIRECTORY"}
    for entry in component.get("files") or []:
        digest = str(entry.get("sha256") or "").lower()
        target = cache_dir / digest
        result = file_digest(target, digest, int(entry.get("size") or 0), cache_dir=cache_dir)
        rows.append({"relative_path": str(entry.get("relative_path") or ""), **result})
    present = all(row.get("present") for row in rows) if rows else False
    verified = bool(rows) and all(row.get("verified") for row in rows)
    return {"verified": verified, "present": present, "files": rows,
            "reason": None if verified else ("MISSING_FILES" if not present else "BYTES_CHANGED")}


def installed_directory(settings, component: dict) -> Path | None:
    """The isolated package directory this component would occupy, if any."""
    if settings is None:
        return None
    name = str(component.get("component_id") or "")
    if not name:
        return None
    return settings.comfyops_dir / "isolated-packages" / name

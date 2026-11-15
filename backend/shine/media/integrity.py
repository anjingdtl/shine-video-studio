"""Shared media integrity verification and conditional-request helpers.

A produced artifact is content-addressed: its SHA-256 is its identity.  The
on-disk check exists to refuse a file that changed after it was registered.
Re-hashing the whole file on *every* request made video playback read hundreds
of megabytes again and again — the browser player issues several HTTP Range
requests per clip, and each one paid a full read plus a full digest.

This module keeps the guarantee without that cost:

* the digest is computed in chunks, never by loading the file into memory;
* a verified ``(path, size, mtime_ns)`` identity is remembered for the process,
  so repeated requests and Range segments cost a stat, not a re-read;
* any change to the size or the modification time invalidates that memory and
  forces a re-hash, so a tampered or swapped file is still refused.

Critical one-shot decisions (operator review, persisted-artifact adoption,
storage migration) keep their own explicit full-file verification; this cache
only serves the high-frequency preview/playback paths.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from fastapi import HTTPException, Request

_CHUNK_BYTES = 1024 * 1024
_CACHE_LIMIT = 512
_lock = threading.Lock()
_verified: dict[tuple[str, int, int], str] = {}


def file_sha256(path: Path) -> str:
    """The digest of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_media_file(
    path: Path,
    expected_sha256: str,
    expected_size: int | None = None,
    *,
    mismatch_code: str = "ARTIFACT_FILE_HASH_MISMATCH",
    missing_detail: str = "MEDIA_FILE_MISSING",
) -> None:
    """Refuse a missing or changed file; remember a verified identity.

    The caller keeps its own named refusal codes so the workbench contract
    does not change: a shot still answers ``ARTIFACT_FILE_HASH_MISMATCH`` and
    a delivery still answers ``DELIVERY_FILE_HASH_MISMATCH``.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=missing_detail) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=missing_detail)
    if expected_size is not None and stat.st_size != expected_size:
        raise HTTPException(status_code=409, detail=mismatch_code)
    try:
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _lock:
        cached = _verified.get(key)
    if cached is not None:
        if cached != expected_sha256:
            raise HTTPException(status_code=409, detail=mismatch_code)
        return
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise HTTPException(status_code=409, detail=mismatch_code)
    with _lock:
        if len(_verified) >= _CACHE_LIMIT:
            _verified.clear()
        _verified[key] = actual


def media_etag(sha256: str) -> str:
    """The strong ETag of content-addressed media: its own digest."""
    return f'"{sha256}"'


def not_modified(request: Request, etag: str) -> bool:
    """Whether the caller already holds exactly this content."""
    header = request.headers.get("if-none-match")
    if not header:
        return False
    return etag in {part.strip() for part in header.split(",")}


def clear_verified_cache() -> None:
    """Test hook: forget every remembered identity."""
    with _lock:
        _verified.clear()
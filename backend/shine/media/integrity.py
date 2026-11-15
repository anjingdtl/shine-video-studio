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
_verified: dict[tuple[str, int, int], dict] = {}

#: Windows file timestamps derive from the system clock (~15.6ms ticks): two
#: writes within one tick can share an mtime, so a same-length rewrite right
#: after a verification could fake an "unchanged stat".  A head/tail content
#: witness closes that hole: for files up to 128 KiB the witness IS the whole
#: content; for larger files it detects any change outside the untouched
#: middle.  Critical one-shot decisions still hash the full file.
_SAMPLE_BYTES = 64 * 1024


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


def content_sample(path: Path, size: int) -> dict | None:
    """Head/tail witness digests of a file, read without loading it whole."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_SAMPLE_BYTES)
            if size > _SAMPLE_BYTES:
                handle.seek(max(0, size - _SAMPLE_BYTES))
                tail = handle.read(_SAMPLE_BYTES)
            else:
                tail = b""
    except OSError:
        return None
    return {
        "head_sha256": hashlib.sha256(head).hexdigest(),
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
    }


def sample_matches(path: Path, size: int, recorded: dict) -> bool:
    """Whether the file still shows the recorded content witness.

    A record without a witness (older caches/receipts) never matches: the
    caller falls back to a full hash instead of guessing.
    """
    if not recorded.get("head_sha256"):
        return False
    sample = content_sample(path, size)
    if sample is None:
        return False
    return (sample["head_sha256"] == recorded.get("head_sha256")
            and sample["tail_sha256"] == recorded.get("tail_sha256"))


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
        if cached.get("sha256") != expected_sha256 or not sample_matches(path, stat.st_size, cached):
            raise HTTPException(status_code=409, detail=mismatch_code)
        return
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise HTTPException(status_code=409, detail=mismatch_code)
    with _lock:
        if len(_verified) >= _CACHE_LIMIT:
            _verified.clear()
        _verified[key] = {"sha256": actual, **(content_sample(path, stat.st_size) or {})}


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
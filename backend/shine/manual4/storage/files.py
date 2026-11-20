"""Isolated file storage, path traversal guard and SHA-256 helpers for Manual4."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException

_SENSITIVE_KEY = re.compile(r"(api[_-]?key|token|secret|password|credential|authorization)", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})")

ALLOWED_M4_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "audio": {".wav", ".mp3", ".flac"},
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_m4_filename(filename: str | None) -> str:
    leaf = re.split(r"[\\/]", filename or "")[-1].strip().replace("\x00", "")
    if not leaf or leaf in {".", ".."}:
        raise HTTPException(status_code=400, detail="M4_FILENAME_INVALID")
    if len(leaf) > 200:
        raise HTTPException(status_code=400, detail="M4_FILENAME_TOO_LONG")
    if any(char in leaf for char in '<>:"|?*'):
        raise HTTPException(status_code=400, detail="M4_FILENAME_UNSUPPORTED_CHARS")
    return leaf


def safe_m4_path(m4_root: Path, relative_path: str) -> Path:
    root = Path(m4_root).resolve()
    rel = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="M4_STORAGE_PATH_INVALID")
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=403, detail="M4_PATH_TRAVERSAL_BLOCKED")
    return candidate


def verify_m4_media_bytes(kind: str, filename: str, content: bytes, max_bytes: int = 100 * 1024 * 1024) -> str:
    if kind not in ALLOWED_M4_EXTENSIONS:
        raise HTTPException(status_code=422, detail="M4_MEDIA_KIND_INVALID")
    if not content:
        raise HTTPException(status_code=400, detail="M4_FILE_EMPTY")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="M4_FILE_TOO_LARGE")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in ALLOWED_M4_EXTENSIONS[kind]:
        raise HTTPException(status_code=415, detail="M4_EXTENSION_MISMATCH")
    if kind == "image":
        if content.startswith(b"\x89PNG\r\n\x1a\n") and suffix == ".png":
            return "image/png"
        if content.startswith(b"\xff\xd8\xff") and suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP" and suffix == ".webp":
            return "image/webp"
    elif kind == "audio":
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE" and suffix == ".wav":
            return "audio/wav"
        if (content.startswith(b"ID3") or content[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}) and suffix == ".mp3":
            return "audio/mpeg"
        if content.startswith(b"fLaC") and suffix == ".flac":
            return "audio/flac"
    else:
        if len(content) >= 12 and content[4:8] == b"ftyp" and suffix in {".mp4", ".mov"}:
            return "video/mp4" if suffix == ".mp4" else "video/quicktime"
        if content.startswith(b"\x1aE\xdf\xa3") and suffix in {".mkv", ".webm"}:
            return "video/x-matroska" if suffix == ".mkv" else "video/webm"
    raise HTTPException(status_code=415, detail="M4_SIGNATURE_MISMATCH")


def store_m4_bytes(m4_root: Path, project_id: str, subdir: str, filename: str, content: bytes) -> tuple[str, str, Path]:
    digest = sha256_bytes(content)
    safe_name = safe_m4_filename(filename)
    ext = PurePosixPath(safe_name).suffix.lower() or ".bin"
    rel = f"projects/{project_id}/{subdir}/{digest[:2]}/{digest}{ext}"
    target = safe_m4_path(m4_root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_name(f"{target.name}.partial")
        tmp.write_bytes(content)
        tmp.replace(target)
    return rel, digest, target


def sanitize_m4_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if _SENSITIVE_KEY.search(str(k)):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_m4_payload(v)
        return cleaned
    if isinstance(value, list):
        return [sanitize_m4_payload(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub("[REDACTED]", value)
    return value

from __future__ import annotations

import errno
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from shine.security import sha256_bytes


def artifact_suffix(content_type: str) -> str:
    return {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }.get(content_type, ".bin")


def store_artifact(root: Path, project_id: str, job_id: str, content: bytes, content_type: str) -> tuple[str, str, int]:
    digest = sha256_bytes(content)
    relative = Path("artifacts") / project_id / job_id / f"{digest}{artifact_suffix(content_type)}"
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f"{target.name}.{uuid4().hex}.partial")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
            raise HTTPException(status_code=507, detail="Local storage is full while storing the artifact") from exc
        raise HTTPException(status_code=500, detail="Unable to store the rendered artifact") from exc
    return relative.as_posix(), digest, len(content)


def safe_asset_path(root: Path, storage_key: str) -> Path:
    candidate = (root / storage_key).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(status_code=500, detail="Asset storage key escapes the configured data directory")
    return candidate


def content_type_for_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
    }.get(suffix, "application/octet-stream")

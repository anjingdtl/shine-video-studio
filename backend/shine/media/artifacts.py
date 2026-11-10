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


def store_artifact(root: Path, project_id: str, job_id: str, content: bytes, content_type: str,
                   *, filename: str | None = None, subdir: str = "shots") -> tuple[str, str, int]:
    """Write the bytes once and return their content-addressed identity.

    ``filename`` is only the on-disk name of new videos: the ledger identity is
    still the SHA-256, and an existing different file with the same name gets a
    short ``_vN`` suffix instead of being overwritten.  Without a filename the
    historical content-addressed layout is kept (continuity derivatives etc.).
    """
    digest = sha256_bytes(content)
    suffix = artifact_suffix(content_type)
    if filename:
        safe_name = Path(str(filename).replace("\\", "/")).name.strip(" .")
        if not safe_name:
            raise HTTPException(status_code=500, detail="A named artifact needs a file name")
        if Path(safe_name).suffix.lower() != suffix:
            safe_name = Path(safe_name).stem + suffix
        relative = Path("artifacts") / project_id / subdir / safe_name
    else:
        relative = Path("artifacts") / project_id / job_id / f"{digest}{suffix}"
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if filename and target.exists():
            if _same_bytes(target, digest, len(content)):
                return relative.as_posix(), digest, len(content)
            relative = _free_name(root, relative, digest, len(content))
            target = root / relative
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


def _same_bytes(path: Path, digest: str, byte_size: int) -> bool:
    try:
        if path.stat().st_size != byte_size:
            return False
        return sha256_bytes(path.read_bytes()) == digest
    except OSError:
        return False


def _free_name(root: Path, relative: Path, digest: str, byte_size: int) -> Path:
    """Resolve a name collision without overwriting a different version.

    The first take keeps the readable name; a later different take becomes
    ``…_v2``.  Identical bytes reuse the existing file.
    """
    stem, suffix, parent = relative.stem, relative.suffix, relative.parent
    index = 2
    while True:
        candidate = root / parent / f"{stem}_v{index}{suffix}"
        if not candidate.exists():
            return parent / f"{stem}_v{index}{suffix}"
        if _same_bytes(candidate, digest, byte_size):
            return parent / f"{stem}_v{index}{suffix}"
        index += 1


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

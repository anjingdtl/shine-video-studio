from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile

ALLOWED_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "audio": {".wav", ".mp3", ".flac"},
}
MAX_REFERENCE_COUNTS = {"image": 9, "video": 3, "audio": 3}


def safe_filename(filename: str | None) -> str:
    # Windows and POSIX separators are both treated as path separators.
    leaf = re.split(r"[\\/]", filename or "")[-1].strip().replace("\x00", "")
    if not leaf or leaf in {".", ".."}:
        raise HTTPException(status_code=400, detail="A valid filename is required")
    if len(leaf) > 255:
        raise HTTPException(status_code=400, detail="Filename is too long")
    return leaf


def verify_upload(kind: str, filename: str, content: bytes, max_bytes: int) -> str:
    if kind not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Asset kind must be image, video, or audio")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Asset exceeds the 25 MiB upload limit")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS[kind]:
        raise HTTPException(status_code=415, detail="File extension does not match asset kind")

    if kind == "image":
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
    elif kind == "audio":
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE":
            return "audio/wav"
        if content.startswith(b"ID3") or content[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return "audio/mpeg"
        if content.startswith(b"fLaC"):
            return "audio/flac"
    else:
        if len(content) >= 12 and content[4:8] == b"ftyp":
            return "video/mp4"
        if content.startswith(b"\x1aE\xdf\xa3"):
            return "video/webm"
    raise HTTPException(status_code=415, detail="File signature does not match a supported media type")


async def read_verified_upload(upload: UploadFile, kind: str, max_bytes: int) -> tuple[str, bytes, str]:
    filename = safe_filename(upload.filename)
    content = await upload.read(max_bytes + 1)
    content_type = verify_upload(kind, filename, content, max_bytes)
    return filename, content, content_type


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

from __future__ import annotations

import hashlib
import re
from ipaddress import ip_address
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile

from shine.media.probe import MediaInfo, parse_bytes

ALLOWED_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "audio": {".wav", ".mp3", ".flac"},
}
MAX_REFERENCE_COUNTS = {"image": 9, "video": 3, "audio": 3}
MAX_TOTAL_REFERENCES = 12
MAX_REFERENCE_DURATION_S = 15.0
ALLOWED_ASSET_ROLES = {"character", "costume", "scene", "prop", "voice", "style_reference", "generic"}

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_SENSITIVE_KEY = re.compile(r"(api[_-]?key|token|secret|password|credential|authorization)", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})")
_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+|/(?:home|Users|mnt|private)/[^\s\"']+")


def safe_filename(filename: str | None) -> str:
    # Windows and POSIX separators are both treated as path separators.
    leaf = re.split(r"[\\/]", filename or "")[-1].strip().replace("\x00", "")
    if not leaf or leaf in {".", ".."}:
        raise HTTPException(status_code=400, detail="A valid filename is required")
    if len(leaf) > 200:
        raise HTTPException(status_code=400, detail="Filename is too long")
    if any(char in leaf for char in '<>:"|?*'):
        raise HTTPException(status_code=400, detail="Filename contains characters that are invalid on Windows")
    return leaf


def verify_upload(kind: str, filename: str, content: bytes, max_bytes: int) -> str:
    if kind not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Asset kind must be image, video, or audio")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Asset exceeds the configured upload limit of {max_bytes // (1024 * 1024)} MiB",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
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


def probe_upload(kind: str, content: bytes, content_type: str) -> MediaInfo:
    info = parse_bytes(content, content_type)
    if kind == "video" and info.container == "unknown":
        raise HTTPException(status_code=415, detail="Unsupported video container")
    return info


def is_loopback_url(url: str) -> bool:
    """True when the URL points at the local machine."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback_url(url: str, *, allow_remote: bool = False) -> str:
    """Reject any renderer URL that is not on this machine.

    Reference bytes are uploaded to the renderer. Sending master assets to a
    remote host is a cloud transmission of user media and therefore requires an
    explicit opt-in.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="The renderer URL must start with http:// or https://")
    if allow_remote:
        return url
    if not is_loopback_url(url):
        raise HTTPException(
            status_code=422,
            detail=(
                "The renderer URL must point at this machine. Uploading master assets to a remote "
                "renderer requires the explicit SHINE_ALLOW_REMOTE_COMFYUI=1 opt-in."
            ),
        )
    return url


def validate_asset_role(role: str | None) -> str | None:
    if role is None or role == "":
        return None
    if role not in ALLOWED_ASSET_ROLES:
        raise HTTPException(status_code=422, detail="Unknown asset role")
    return role


def validate_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags[:40]:
        value = tag.strip()
        if not value:
            continue
        if len(value) > 40:
            raise HTTPException(status_code=422, detail="Asset tag is too long")
        cleaned.append(value)
    return cleaned


def sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    """Remove credentials and absolute local paths before persistence or logging."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _SENSITIVE_KEY.search(text_key):
                cleaned[text_key] = "[redacted]"
            else:
                cleaned[text_key] = sanitize_payload(item, depth=depth + 1)
        return cleaned
    if isinstance(value, list | tuple):
        return [sanitize_payload(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        redacted = _SENSITIVE_VALUE.sub("[redacted]", value)
        redacted = _ABSOLUTE_PATH.sub("[local-path]", redacted)
        if len(redacted) > 4000:
            return redacted[:4000] + "…"
        return redacted
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:500]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def storage_relative_path(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(status_code=500, detail="Invalid content digest")
    return (PurePosixPath("assets") / digest[:2] / digest).as_posix()

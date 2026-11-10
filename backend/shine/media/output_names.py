"""Readable, project-scoped output names.

Every file this workbench produces is stored as ``项目_类型_名称``: the project
it belongs to, what kind of output it is (素材 / 视频 / 音色), and a name a
person can read in a file list.  The ledger identity stays the SHA-256; the
name is only how a person finds the bytes.
"""

from __future__ import annotations

import re
from pathlib import Path

TYPE_MATERIAL = "素材"
TYPE_VIDEO = "视频"
TYPE_VOICE = "音色"
#: Outputs produced by the global material centre carry this pseudo-project.
MATERIAL_CENTER = "素材中心"

# Windows rejects these names regardless of extension.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_INVALID = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")
# Leaves room for the project/type prefix and a _vN disambiguator inside the
# 255-character component limit, without depending on a long root path.
MAX_TITLE_LENGTH = 60
MAX_COMPONENT_LENGTH = 96
_DIGEST = re.compile(r"[0-9a-fA-F]{16,}")
_MEDIA_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".wav", ".mp3", ".flac", ".m4a", ".ogg",
    ".mp4", ".mov", ".mkv", ".webm",
    ".srt", ".ass", ".bin",
)


def _without_media_suffix(text: str) -> str:
    """Strip a real media suffix, but keep dots that are part of a name."""
    lowered = text.lower()
    for suffix in _MEDIA_SUFFIXES:
        if lowered.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def sanitize_component(text: str | None, *, fallback: str = "未命名", limit: int = MAX_TITLE_LENGTH) -> str:
    """A legal, non-reserved Windows path component that keeps its meaning."""
    cleaned = _INVALID.sub("-", text or "")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    cleaned = cleaned[:limit].strip(" .")
    return cleaned or fallback


def bounded_name(name: str) -> str:
    """Keep a whole file-name component short without cutting the extension."""
    stem, suffix = Path(name).stem, Path(name).suffix
    if len(stem) > MAX_COMPONENT_LENGTH:
        stem = stem[:MAX_COMPONENT_LENGTH].rstrip(" .")
    return stem + suffix


def typed_filename(project: str, type_label: str, base: str, extension: str) -> str:
    """``项目_类型_名称.ext`` with one prefix only, even after a re-migration."""
    project_part = sanitize_component(project)
    type_part = sanitize_component(type_label, fallback=TYPE_MATERIAL)
    stem = sanitize_component(
        _without_media_suffix(str(base or "").strip()), fallback="未命名"
    )
    prefix = f"{project_part}_{type_part}_"
    if stem.startswith(prefix):
        stem = stem[len(prefix):]
    ext = extension if extension.startswith(".") else f".{extension}"
    return bounded_name(f"{prefix}{stem}{ext}")


def is_readable_ledger_name(filename: str | None) -> bool:
    """True when the name came from a person rather than a generator."""
    stem = _without_media_suffix(str(filename or "").strip())
    return bool(stem) and not _DIGEST.fullmatch(stem) and not stem.lower().startswith("shine-assets")


def readable_base(filename: str | None, fallback: str) -> str:
    """A ledger file name with its extension removed, unless it is a digest."""
    stem = _without_media_suffix(str(filename or "").strip())
    if not is_readable_ledger_name(filename):
        return fallback
    return sanitize_component(stem, fallback=fallback)

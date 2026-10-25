"""Derived evidence frames for remote semantic review.

Only derived frames ever leave the machine. Master assets are never uploaded
to a remote reviewer.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from shine.media.artifacts import safe_asset_path


def reference_thumbnails(root, manifest, output_dir, *, ffmpeg_path="ffmpeg"):
    """Local-only derived references pinned to the request manifest, never masters."""
    executable = ffmpeg_executable(ffmpeg_path)
    if not executable:
        raise ValueError("REFERENCE_EVIDENCE_TOOL_UNAVAILABLE")
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for entry in manifest:
        if not entry.content_type.startswith("image/"):
            continue
        source = safe_asset_path(root, entry.storage_key)
        with source.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry.sha256:
                raise ValueError("REFERENCE_EVIDENCE_HASH_CHANGED")
        target = output_dir / f"{entry.sha256}.jpg"
        subprocess.run(
            [
                executable,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=768:768:force_original_aspect_ratio=decrease",
                str(target),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        frames.append((f"{entry.label} / {entry.entity_id or entry.role or 'reference'}", target))
    return tuple(frames)


def ffmpeg_executable(ffmpeg_path: str = "ffmpeg") -> str | None:
    """Resolve the configured FFmpeg binary.

    The configured name is authoritative: there is no hidden fallback to a
    different binary, so "FFmpeg is not installed" is honoured exactly.
    """
    return shutil.which(ffmpeg_path)


def extract_frames(
    path: Path,
    out_dir: Path,
    *,
    count: int = 4,
    ffmpeg_path: str = "ffmpeg",
    timeout_s: float = 60,
    duration_s: float | None = None,
) -> list[Path]:
    """Extract up to ``count`` evenly spaced JPEG frames.

    Returns an empty list when FFmpeg is unavailable or the extraction fails;
    a remote reviewer must never receive fabricated evidence.
    """
    executable = ffmpeg_executable(ffmpeg_path)
    if not executable or not path.is_file():
        return []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    if count <= 0:
        return []
    frames: list[Path] = []
    for index in range(count):
        offset = (index + 0.5) * duration_s / count if duration_s and duration_s > 0 else 0.5 + index
        target = out_dir / f"frame_{index:02d}.jpg"
        command = [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{offset}",
            "-i",
            str(path),
            "-vf",
            "scale=768:768:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-y",
            str(target),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired):
            break
        if completed.returncode != 0 or not target.is_file():
            break
        frames.append(target)
    return frames


__all__ = ["extract_frames", "ffmpeg_executable"]

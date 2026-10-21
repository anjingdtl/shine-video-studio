"""Derived evidence frames for remote semantic review.

Only derived frames ever leave the machine. Master assets are never uploaded
to a remote reviewer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_executable(ffmpeg_path: str) -> str | None:
    return shutil.which(ffmpeg_path) or shutil.which("ffmpeg")


def extract_frames(
    path: Path,
    out_dir: Path,
    *,
    count: int = 4,
    ffmpeg_path: str = "ffmpeg",
    timeout_s: float = 60,
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
    frames: list[Path] = []
    for index in range(count):
        offset = 0.5 + index
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

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shine.media.probe import MediaInfo, probe_file
from shine.schemas import TechnicalCheck, TechnicalQCResult
from shine.security import sha256_bytes


@dataclass(frozen=True)
class TechnicalExpectations:
    duration_s: float | None = None
    duration_tolerance_s: float = 0.75
    expect_audio: bool = True
    expected_fps: float = 24.0
    fps_tolerance: float = 1.0
    require_decodable: bool = True


def _check(name: str, status: str, detail: str = "", measured=None) -> TechnicalCheck:
    return TechnicalCheck(name=name, status=status, detail=detail, measured=measured)


def _silence_check(path: Path, ffmpeg_path: str) -> TechnicalCheck:
    executable = shutil.which(ffmpeg_path) or shutil.which("ffmpeg")
    if not executable:
        return _check("audio_silence", "unavailable", "FFmpeg is not installed; silence detection was not run")
    command = [
        executable,
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        "silencedetect=noise=-50dB:d=2",
        "-f",
        "null",
        "--",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _check("audio_silence", "unavailable", "FFmpeg silence detection could not run")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    if starts:
        return _check(
            "audio_silence",
            "fail",
            f"Detected {len(starts)} silent span(s) longer than 2 seconds",
            measured=len(starts),
        )
    return _check("audio_silence", "pass", "No silent span longer than 2 seconds was detected")


def _full_decode_check(path: Path, ffmpeg_path: str) -> TechnicalCheck:
    """Decode every frame of the file.

    FFprobe only parses container metadata; it never proves that the coded
    frames are decodable. This check is the only one that may claim a full
    decode, and it is honestly ``unavailable`` when FFmpeg is not installed.
    """
    executable = shutil.which(ffmpeg_path) or shutil.which("ffmpeg")
    if not executable:
        return _check(
            "decode",
            "unavailable",
            "FFmpeg is not installed; the coded frames were not decoded",
        )
    command = [
        executable,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _check("decode", "unavailable", "The full decode check could not run")
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        return _check(
            "decode",
            "fail",
            f"The media could not be decoded end to end: {stderr[:300] or 'ffmpeg reported an error'}",
        )
    return _check("decode", "pass", "Every frame was decoded without error")


class TechnicalQC:
    """Deterministic checks. A fallback container parse never claims codec decoding."""

    def __init__(self, *, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg"):
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_path = ffmpeg_path

    def check(self, path: Path, *, expectations: TechnicalExpectations | None = None) -> TechnicalQCResult:
        expectations = expectations or TechnicalExpectations()
        checks: list[TechnicalCheck] = []
        if not path.exists() or not path.is_file():
            return TechnicalQCResult(
                result="failed",
                checks=[_check("file_exists", "fail", "The rendered artifact is missing")],
                tool="none",
                artifact_sha256="",
                byte_size=0,
            )
        content = path.read_bytes()
        digest = sha256_bytes(content)
        checks.append(_check("file_exists", "pass", "Artifact exists", measured=len(content)))
        checks.append(
            _check("file_nonempty", "pass" if content else "fail", "Artifact byte size", measured=len(content))
        )
        info: MediaInfo = probe_file(path, ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)
        checks.append(
            _check(
                "container",
                "pass" if info.container not in {"unknown", "missing"} else "fail",
                f"Detected container: {info.container}",
                measured=info.container,
            )
        )
        if info.decoded:
            checks.append(
                _check(
                    "stream_metadata",
                    "pass",
                    "FFprobe parsed the container metadata and the stream list",
                )
            )
        else:
            checks.append(
                _check(
                    "stream_metadata",
                    "unavailable" if expectations.require_decodable else "skipped",
                    "FFprobe is unavailable; only container structure was inspected",
                )
            )
        if expectations.duration_s is not None:
            if info.duration_s is None:
                checks.append(_check("duration", "unavailable", "Duration could not be measured"))
            else:
                delta = abs(info.duration_s - expectations.duration_s)
                checks.append(
                    _check(
                        "duration",
                        "pass" if delta <= expectations.duration_tolerance_s else "fail",
                        f"Measured {info.duration_s:.2f}s, expected "
                        f"{expectations.duration_s:.2f}s ±{expectations.duration_tolerance_s:.2f}s",
                        measured=round(info.duration_s, 3),
                    )
                )
        if info.width and info.height:
            checks.append(
                _check("resolution", "pass", f"{info.width}x{info.height}", measured=f"{info.width}x{info.height}")
            )
        else:
            checks.append(_check("resolution", "unavailable", "Resolution could not be measured"))
        if info.fps is None:
            checks.append(_check("frame_rate", "unavailable", "Frame rate could not be measured"))
        else:
            delta = abs(info.fps - expectations.expected_fps)
            checks.append(
                _check(
                    "frame_rate",
                    "pass" if delta <= expectations.fps_tolerance else "fail",
                    f"Measured {info.fps:.2f} fps, expected {expectations.expected_fps:.2f}",
                    measured=round(info.fps, 3),
                )
            )
        if info.has_audio is None:
            checks.append(_check("audio_track", "unavailable", "Audio stream presence could not be determined"))
        else:
            if expectations.expect_audio:
                checks.append(
                    _check(
                        "audio_track",
                        "pass" if info.has_audio else "fail",
                        "Audio stream present" if info.has_audio else "Audio stream is missing",
                    )
                )
            else:
                checks.append(_check("audio_track", "pass", "Audio is not required for this shot"))
        checks.append(_silence_check(path, self.ffmpeg_path))
        checks.append(_full_decode_check(path, self.ffmpeg_path))

        if any(check.status == "fail" for check in checks):
            result = "failed"
        elif any(check.status == "unavailable" for check in checks):
            result = "needs_review"
        else:
            result = "passed"
        return TechnicalQCResult(
            result=result,
            checks=checks,
            tool=info.tool,
            artifact_sha256=digest,
            byte_size=len(content),
            duration_s=info.duration_s,
            width=info.width,
            height=info.height,
            fps=info.fps,
            has_audio=info.has_audio,
        )

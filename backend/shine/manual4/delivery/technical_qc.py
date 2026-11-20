"""Mirrored deterministic technical QC for Manual4 (from shine/quality/technical.py)."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shine.manual4.materials.probe import MediaInfo, probe_file
from shine.manual4.storage.files import sha256_bytes


@dataclass(frozen=True)
class M4TechnicalExpectations:
    duration_s: float | None = None
    duration_tolerance_s: float = 0.85
    expect_audio: bool = False
    expected_fps: float = 24.0
    fps_tolerance: float = 1.0
    require_decodable: bool = True


def _check(name: str, status: str, detail: str = "", measured: Any = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "measured": measured}


def _full_decode_check(path: Path, ffmpeg_path: str) -> dict[str, Any]:
    executable = shutil.which(ffmpeg_path)
    if not executable:
        return _check("decode", "unavailable", "未检测到 FFmpeg，无法全帧解码验证")
    command = [executable, "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _check("decode", "unavailable", "FFmpeg 全帧解码校验超时或无法运行")
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        return _check("decode", "fail", f"视频帧解码失败：{stderr[:300] or 'ffmpeg reported an error'}")
    return _check("decode", "pass", "全帧解码无错误")


def _silence_check(path: Path, ffmpeg_path: str) -> dict[str, Any]:
    executable = shutil.which(ffmpeg_path)
    if not executable:
        return _check("audio_silence", "unavailable", "未检测到 FFmpeg，未检查静音区间")
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
        return _check("audio_silence", "unavailable", "静音检测无法运行")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    starts = [float(v) for v in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    if starts:
        return _check("audio_silence", "fail", f"检测到 {len(starts)} 段超过 2 秒静音", measured=len(starts))
    return _check("audio_silence", "pass", "无超 2 秒异常静音")


def run_m4_technical_qc(
    path: Path,
    *,
    expectations: M4TechnicalExpectations | None = None,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> dict[str, Any]:
    exp = expectations or M4TechnicalExpectations()
    checks: list[dict[str, Any]] = []
    if not path.is_file():
        return {
            "result": "failed",
            "checks": [_check("file_exists", "fail", "输出视频文件不存在")],
            "tool": "none",
            "artifact_sha256": "",
            "byte_size": 0,
            "duration_s": None,
            "width": None,
            "height": None,
            "fps": None,
            "has_audio": None,
        }

    content = path.read_bytes()
    digest = sha256_bytes(content)
    checks.append(_check("file_exists", "pass", "文件存在", measured=len(content)))
    checks.append(_check("file_nonempty", "pass" if content else "fail", "文件大小校验", measured=len(content)))

    info: MediaInfo = probe_file(path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
    checks.append(
        _check(
            "container",
            "pass" if info.container not in {"unknown", "missing"} else "fail",
            f"容器格式：{info.container}",
            measured=info.container,
        )
    )
    if info.decoded:
        checks.append(_check("stream_metadata", "pass", f"使用 {info.tool} 解析流元数据成功"))
    else:
        checks.append(
            _check(
                "stream_metadata",
                "unavailable" if exp.require_decodable else "skipped",
                "FFprobe/FFmpeg 不可用，仅完成容器结构读取",
            )
        )

    if exp.duration_s is not None:
        if info.duration_s is None:
            checks.append(_check("duration", "unavailable", "未能测得视频时长"))
        else:
            delta = abs(info.duration_s - exp.duration_s)
            checks.append(
                _check(
                    "duration",
                    "pass" if delta <= exp.duration_tolerance_s else "fail",
                    f"实测 {info.duration_s:.2f}s，目标 {exp.duration_s:.2f}s ±{exp.duration_tolerance_s:.2f}s",
                    measured=round(info.duration_s, 3),
                )
            )

    if info.width and info.height:
        dims = f"{info.width}x{info.height}"
        checks.append(_check("resolution", "pass", dims, measured=dims))
    else:
        checks.append(_check("resolution", "unavailable", "未能测得分辨率"))

    if info.fps is None:
        checks.append(_check("frame_rate", "unavailable", "未能测得帧率"))
    else:
        delta = abs(info.fps - exp.expected_fps)
        checks.append(
            _check(
                "frame_rate",
                "pass" if delta <= exp.fps_tolerance else "fail",
                f"实测 {info.fps:.2f} fps，预期 {exp.expected_fps:.2f} fps",
                measured=round(info.fps, 3),
            )
        )

    if info.has_audio is None:
        checks.append(_check("audio_track", "unavailable", "未能判断音轨是否存在"))
    elif exp.expect_audio:
        checks.append(_check("audio_track", "pass" if info.has_audio else "fail", "音频流检查"))
        if info.has_audio:
            checks.append(_silence_check(path, ffmpeg_path))
    else:
        checks.append(_check("audio_track", "pass", "本镜不强制音轨检查"))

    if exp.require_decodable:
        checks.append(_full_decode_check(path, ffmpeg_path))

    if any(c["status"] == "fail" for c in checks):
        result = "failed"
    elif any(c["status"] == "unavailable" for c in checks):
        result = "needs_review"
    else:
        result = "passed"

    return {
        "result": result,
        "checks": checks,
        "tool": info.tool,
        "artifact_sha256": digest,
        "byte_size": len(content),
        "duration_s": info.duration_s,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "has_audio": info.has_audio,
    }

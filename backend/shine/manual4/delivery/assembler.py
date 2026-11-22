"""Mirrored FFmpeg delivery assembly & SRT subtitle generation for Manual4.

Mirrors ``shine/media/assembly.py`` and ``shine/media/subtitles.py`` without
importing any seven-stage modules.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from shine.manual4.materials.probe import probe_file
from shine.manual4.storage.files import safe_m4_path, sha256_file, store_m4_bytes

SUBTITLE_FONT_SIZE_DEFAULT = 40
SUBTITLE_FONT_SIZE_MIN = 12
SUBTITLE_FONT_SIZE_MAX = 96

_SRT_TIMING_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def clamp_m4_subtitle_font_size(raw: int | None) -> int:
    try:
        val = int(raw) if raw is not None else SUBTITLE_FONT_SIZE_DEFAULT
    except (TypeError, ValueError):
        val = SUBTITLE_FONT_SIZE_DEFAULT
    return max(SUBTITLE_FONT_SIZE_MIN, min(SUBTITLE_FONT_SIZE_MAX, val))


def _ass_stamp(seconds: float) -> str:
    units = max(0, round(seconds * 100))
    hours, units = divmod(units, 360000)
    minutes, units = divmod(units, 6000)
    secs, centis = divmod(units, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def srt_to_ass(subtitle_srt: str, *, font_size: int = SUBTITLE_FONT_SIZE_DEFAULT) -> str:
    """Convert SRT text into 720p-referenced ASS so burn-in honours font_size."""
    size = clamp_m4_subtitle_font_size(font_size)
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1280",
        "PlayResY: 720",
        "ScaledBorderAndShadow: yes",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{size},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,"
        "2,1,2,60,60,60,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    normalized = (subtitle_srt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized:
        for block in re.split(r"\n\s*\n", normalized):
            lines = [ln for ln in block.split("\n") if ln.strip() != ""]
            if len(lines) < 2:
                continue
            timing_idx = 1 if lines[0].strip().isdigit() else 0
            if timing_idx >= len(lines):
                continue
            match = _SRT_TIMING_RE.match(lines[timing_idx].strip())
            if not match:
                continue
            vals = [int(v) for v in match.groups()]
            start_s = vals[0] * 3600 + vals[1] * 60 + vals[2] + vals[3] / 1000.0
            end_s = vals[4] * 3600 + vals[5] * 60 + vals[6] + vals[7] / 1000.0
            body = "\n".join(lines[timing_idx + 1 :]).strip()
            if not body:
                continue
            escaped = body.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")
            events.append(f"Dialogue: 0,{_ass_stamp(start_s)},{_ass_stamp(end_s)},Default,,0,0,0,,{escaped}")
    return "\n".join(head + events)


@dataclass(frozen=True)
class M4ClipSpec:
    job_id: str
    shot_id: str
    shot_code: str
    storage_path: str
    expected_sha256: str
    in_s: float
    out_s: float
    target_width: int
    target_height: int

    @property
    def duration_s(self) -> float:
        return max(0.1, self.out_s - self.in_s)


def _srt_stamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, millis = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def build_episode_srt(clips: list[tuple[M4ClipSpec, list[dict[str, Any]]]]) -> str:
    blocks: list[str] = []
    cursor_s = 0.0
    cue_idx = 1
    for clip, dialogue in clips:
        dur = clip.duration_s
        lines = [d for d in (dialogue or []) if isinstance(d, dict) and str(d.get("text") or "").strip()]
        if lines:
            slice_dur = dur / len(lines)
            for idx, line in enumerate(lines):
                start = cursor_s + idx * slice_dur
                end = min(cursor_s + dur, start + slice_dur)
                speaker = str(line.get("speaker") or "").strip()
                text = str(line.get("text") or "").strip()
                rendered = f"{speaker}：{text}" if speaker else text
                blocks.append(f"{cue_idx}\n{_srt_stamp(start)} --> {_srt_stamp(end)}\n{rendered}\n")
                cue_idx += 1
        cursor_s += dur
    return "\n".join(blocks)


def _run_ffmpeg(cmd: list[str], *, cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, capture_output=True, timeout=1800, check=False, cwd=cwd)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", errors="replace")[-800:]
        raise HTTPException(
            500,
            {"code": "M4_DELIVERY_FFMPEG_FAILED", "message": f"FFmpeg 合成失败：{tail}"},
        )


def assemble_m4_episode_reel(
    m4_root: Path,
    project_id: str,
    clips: list[M4ClipSpec],
    *,
    out_filename: str,
    fps: float = 24.0,
    crf: int = 18,
    subtitle_srt: str = "",
    burn_subtitles: bool = False,
    subtitle_font_size: int = SUBTITLE_FONT_SIZE_DEFAULT,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> dict[str, Any]:
    if not clips:
        raise HTTPException(400, {"code": "M4_DELIVERY_EMPTY_EDIT", "message": "成片 EDL 不能为空。"})
    ffmpeg_bin = shutil.which(ffmpeg_path)
    if not ffmpeg_bin:
        raise HTTPException(
            409,
            {"code": "M4_DELIVERY_FFMPEG_UNAVAILABLE", "message": "未检测到 FFmpeg，无法合成可播放成片。"},
        )

    verified_sources: list[tuple[M4ClipSpec, Path]] = []
    for clip in clips:
        src = safe_m4_path(m4_root, clip.storage_path)
        if not src.is_file():
            raise HTTPException(
                409,
                {"code": "M4_SOURCE_CLIP_MISSING", "message": f"镜头 {clip.shot_code} 的视频产物不存在。"},
            )
        if sha256_file(src) != clip.expected_sha256:
            raise HTTPException(
                409,
                {"code": "M4_SOURCE_CLIP_SHA_MISMATCH", "message": f"镜头 {clip.shot_code} 文件哈希与记录不符。"},
            )
        verified_sources.append((clip, src))

    first_info = probe_file(verified_sources[0][1], ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_bin)
    width = clips[0].target_width or first_info.width or 1344
    height = clips[0].target_height or first_info.height or 768
    width = max(16, int(width) // 2 * 2)
    height = max(16, int(height) // 2 * 2)

    with tempfile.TemporaryDirectory(prefix="shine-m4-delivery-") as tmpdir:
        work = Path(tmpdir)
        segments: list[Path] = []
        for idx, (clip, src) in enumerate(verified_sources):
            info = probe_file(src, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_bin)
            seg_path = work / f"seg_{idx:03d}.mp4"
            cmd = [ffmpeg_bin, "-nostdin", "-y", "-ss", f"{clip.in_s:.3f}", "-i", str(src)]
            has_audio = bool(info.has_audio)
            if not has_audio:
                cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            cmd += [
                "-t",
                f"{clip.duration_s:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if has_audio else "1:a:0",
                "-vf",
                (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},format=yuv420p"
                ),
                "-af",
                "aresample=async=1:first_pts=0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(crf),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(seg_path),
            ]
            _run_ffmpeg(cmd)
            segments.append(seg_path)

        concat_list = work / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{seg.resolve().as_posix()}'" for seg in segments) + "\n",
            encoding="utf-8",
        )
        raw_concat = work / "concat.mp4"
        _run_ffmpeg(
            [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(raw_concat),
            ]
        )

        final_local = raw_concat
        if burn_subtitles and subtitle_srt.strip():
            subs_file = work / "subs.ass"
            subs_file.write_text(srt_to_ass(subtitle_srt, font_size=subtitle_font_size), encoding="utf-8")
            burned = work / "burned.mp4"
            _run_ffmpeg(
                [
                    ffmpeg_bin,
                    "-nostdin",
                    "-y",
                    "-i",
                    raw_concat.name,
                    "-vf",
                    f"ass={subs_file.name}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    str(crf),
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    burned.name,
                ],
                cwd=work,
            )
            final_local = burned

        final_bytes = final_local.read_bytes()
        rel_path, digest, abs_path = store_m4_bytes(m4_root, project_id, "deliveries", out_filename, final_bytes)
        out_info = probe_file(abs_path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_bin)

    return {
        "storage_path": rel_path,
        "sha256": digest,
        "byte_size": len(final_bytes),
        "duration_s": out_info.duration_s,
        "width": out_info.width or width,
        "height": out_info.height or height,
        "fps": out_info.fps or fps,
    }

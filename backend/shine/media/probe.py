"""Deterministic local media probing.

FFprobe is used when it is installed; otherwise a conservative pure-Python
parser extracts container-level facts. The fallback never claims to have
decoded a codec.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaInfo:
    container: str
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_video: bool | None = None
    has_audio: bool | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    tool: str = "python-fallback"
    decoded: bool = False
    notes: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _boxes(data: bytes, start: int, end: int):
    index = start
    while index + 8 <= end:
        size = int.from_bytes(data[index : index + 4], "big")
        box_type = data[index + 4 : index + 8].decode("latin-1")
        header = 8
        if size == 1:
            if index + 16 > end:
                return
            size = int.from_bytes(data[index + 8 : index + 16], "big")
            header = 16
        elif size == 0:
            size = end - index
        if size < header or index + size > end:
            return
        yield box_type, index + header, index + size
        index += size


def _find(data: bytes, start: int, end: int, names: tuple[str, ...]):
    for box_type, body_start, body_end in _boxes(data, start, end):
        if box_type in names:
            yield box_type, body_start, body_end


def _first(data: bytes, start: int, end: int, name: str):
    for box_type, body_start, body_end in _boxes(data, start, end):
        if box_type == name:
            return body_start, body_end
    return None


def _read_full_box(data: bytes, start: int) -> tuple[int, bytes]:
    version = data[start]
    return version, data[start + 4 :]


def _parse_mvhd(data: bytes, start: int, end: int) -> tuple[float | None, int]:
    if end - start < 20:
        return None, 0
    version = data[start]
    if version == 1:
        timescale = int.from_bytes(data[start + 20 : start + 24], "big")
        duration = int.from_bytes(data[start + 24 : start + 32], "big")
    else:
        timescale = int.from_bytes(data[start + 12 : start + 16], "big")
        duration = int.from_bytes(data[start + 16 : start + 20], "big")
    seconds = duration / timescale if timescale else None
    return seconds, timescale


def _parse_tkhd(data: bytes, start: int, end: int) -> tuple[int | None, int | None]:
    if end - start < 84:
        return None, None
    version = data[start]
    width_offset = 88 if version == 1 else 76
    if end - start < width_offset + 8:
        return None, None
    width = int.from_bytes(data[start + width_offset : start + width_offset + 4], "big") / 65536
    height = int.from_bytes(data[start + width_offset + 4 : start + width_offset + 8], "big") / 65536
    return int(round(width)) or None, int(round(height)) or None


def _parse_mdhd_timescale(data: bytes, start: int) -> int:
    version = data[start]
    offset = 20 if version == 1 else 12
    if len(data) < start + offset + 4:
        return 0
    return int.from_bytes(data[start + offset : start + offset + 4], "big")


def _parse_stts_fps(data: bytes, start: int, end: int, timescale: int) -> float | None:
    if end - start < 16 or not timescale:
        return None
    count = int.from_bytes(data[start + 4 : start + 8], "big")
    cursor = start + 8
    samples = 0
    ticks = 0
    for _ in range(count):
        if cursor + 8 > end:
            break
        sample_count = int.from_bytes(data[cursor : cursor + 4], "big")
        delta = int.from_bytes(data[cursor + 4 : cursor + 8], "big")
        samples += sample_count
        ticks += sample_count * delta
        cursor += 8
    if ticks <= 0 or samples <= 0:
        return None
    seconds = ticks / timescale
    return samples / seconds if seconds > 0 else None


def _parse_handler(data: bytes, start: int) -> str:
    if len(data) < start + 12:
        return ""
    return data[start + 8 : start + 12].decode("latin-1", errors="replace")


def parse_mp4(data: bytes) -> MediaInfo:
    notes: list[str] = []
    moov = _first(data, 0, len(data), "moov")
    if not moov:
        return MediaInfo(container="mp4", has_video=False, has_audio=False, notes=("missing_moov",))
    duration = None
    movie_timescale = 0
    mvhd = _first(data, moov[0], moov[1], "mvhd")
    if mvhd:
        duration, movie_timescale = _parse_mvhd(data, mvhd[0], mvhd[1])
    width = height = None
    fps = None
    has_video = False
    has_audio = False
    video_codec = None
    audio_codec = None
    for _trak_type, trak_start, trak_end in _find(data, moov[0], moov[1], ("trak",)):
        tkhd = _first(data, trak_start, trak_end, "tkhd")
        if tkhd:
            w, h = _parse_tkhd(data, tkhd[0], tkhd[1])
            if w and h:
                width, height = width or w, height or h
        mdia = _first(data, trak_start, trak_end, "mdia")
        if not mdia:
            continue
        hdlr = _first(data, mdia[0], mdia[1], "hdlr")
        handler = _parse_handler(data, hdlr[0]) if hdlr else ""
        mdhd = _first(data, mdia[0], mdia[1], "mdhd")
        track_timescale = _parse_mdhd_timescale(data, mdhd[0]) if mdhd else 0
        minf = _first(data, mdia[0], mdia[1], "minf")
        stbl = _first(data, minf[0], minf[1], "stbl") if minf else None
        if handler == "vide":
            has_video = True
            if stbl:
                stts = _first(data, stbl[0], stbl[1], "stts")
                if stts:
                    fps = _parse_stts_fps(data, stts[0], stts[1], track_timescale)
                stsd = _first(data, stbl[0], stbl[1], "stsd")
                if stsd and stsd[1] - stsd[0] > 16:
                    video_codec = data[stsd[0] + 12 : stsd[0] + 16].decode("latin-1", errors="replace")
        elif handler == "soun":
            has_audio = True
            if stbl:
                stsd = _first(data, stbl[0], stbl[1], "stsd")
                if stsd and stsd[1] - stsd[0] > 16:
                    audio_codec = data[stsd[0] + 12 : stsd[0] + 16].decode("latin-1", errors="replace")
    if duration is not None:
        notes.append("duration_from_mvhd")
    return MediaInfo(
        container="mp4",
        duration_s=duration,
        width=width,
        height=height,
        fps=fps,
        has_video=has_video,
        has_audio=has_audio,
        video_codec=video_codec,
        audio_codec=audio_codec,
        notes=tuple(notes),
    )


def parse_wav(data: bytes) -> MediaInfo:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return MediaInfo(container="wav", has_audio=False, notes=("not_wav",))
    index = 12
    byte_rate = 0
    data_size = 0
    channels = 0
    sample_rate = 0
    while index + 8 <= len(data):
        chunk = data[index : index + 4]
        size = int.from_bytes(data[index + 4 : index + 8], "little")
        body = index + 8
        if chunk == b"fmt " and size >= 16:
            channels = int.from_bytes(data[body + 2 : body + 4], "little")
            sample_rate = int.from_bytes(data[body + 4 : body + 8], "little")
            byte_rate = int.from_bytes(data[body + 8 : body + 12], "little")
        elif chunk == b"data":
            data_size = min(size, len(data) - body)
        index = body + size + (size & 1)
    duration = data_size / byte_rate if byte_rate else None
    return MediaInfo(
        container="wav",
        duration_s=duration,
        has_video=False,
        has_audio=True,
        audio_codec="pcm",
        notes=(f"channels={channels}", f"sample_rate={sample_rate}"),
    )


def parse_png(data: bytes) -> MediaInfo:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaInfo(container="png", notes=("not_png",))
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return MediaInfo(container="png", width=width, height=height, has_video=False, has_audio=False)


def parse_jpeg(data: bytes) -> MediaInfo:
    if not data.startswith(b"\xff\xd8\xff"):
        return MediaInfo(container="jpeg", notes=("not_jpeg",))
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xD8, 0xD9}:
            index += 2
            continue
        size = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return MediaInfo(container="jpeg", width=width, height=height, has_video=False, has_audio=False)
        index += 2 + size
    return MediaInfo(container="jpeg", notes=("dimensions_unknown",))


def parse_webp(data: bytes) -> MediaInfo:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return MediaInfo(container="webp", notes=("not_webp",))
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return MediaInfo(container="webp", width=width, height=height, has_video=False, has_audio=False)
    if chunk == b"VP8 " and len(data) >= 30:
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return MediaInfo(container="webp", width=width, height=height, has_video=False, has_audio=False)
    return MediaInfo(container="webp", notes=("dimensions_unknown",))


def parse_bytes(data: bytes, content_type: str = "") -> MediaInfo:
    if data.startswith(b"\x89PNG\r\n\x1a\n") or content_type == "image/png":
        return parse_png(data)
    if data.startswith(b"\xff\xd8\xff") or content_type == "image/jpeg":
        return parse_jpeg(data)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return parse_webp(data)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return parse_wav(data)
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return parse_mp4(data)
    if data.startswith(b"fLaC"):
        return MediaInfo(container="flac", has_audio=True, audio_codec="flac", notes=("duration_unknown",))
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return MediaInfo(container="mp3", has_audio=True, audio_codec="mp3", notes=("duration_unknown",))
    if data.startswith(b"\x1aE\xdf\xa3"):
        return MediaInfo(container="webm", notes=("duration_unknown",))
    return MediaInfo(container="unknown", notes=("unsupported_container",))


def _ffprobe_json(path: Path, ffprobe_path: str, timeout_s: float = 30) -> dict[str, Any] | None:
    # The configured executable is authoritative. There is no hidden fallback to
    # a different binary, so "the tool is not installed" can be simulated and
    # honoured exactly as configured.
    executable = shutil.which(ffprobe_path)
    if not executable:
        return None
    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}\.\d+)")
# The resolution is a "WIDTHxHEIGHT" pair. At least two digits on each side
# keeps the pattern away from hexadecimal literals such as "0x31637661".
_VIDEO_PATTERN = re.compile(
    r"Stream #\d+:\d+.*?:\s*Video:\s*(?P<codec>[A-Za-z0-9_]+).*?(?P<width>\d{2,5})x(?P<height>\d{2,5})(?![0-9])",
    re.DOTALL,
)
_FPS_PATTERN = re.compile(r"(?P<fps>\d+(?:\.\d+)?)\s*fps")
_AUDIO_PATTERN = re.compile(r"Stream #\d+:\d+.*?:\s*Audio:\s*(?P<codec>[A-Za-z0-9_]+)", re.DOTALL)


def _ffmpeg_metadata(path: Path, ffmpeg_path: str, timeout_s: float = 30) -> dict[str, Any] | None:
    """Read stream metadata with FFmpeg when FFprobe is not installed.

    FFmpeg prints the same container facts to stderr. This keeps the technical
    QC honest on installations that ship only ``ffmpeg``.
    """
    executable = shutil.which(ffmpeg_path)
    if not executable:
        return None
    command = [executable, "-hide_banner", "-i", str(path)]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if "Invalid data found" in stderr or "No such file" in stderr:
        return None
    streams: list[dict[str, Any]] = []
    duration_s: float | None = None
    match = _DURATION_PATTERN.search(stderr)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        duration_s = hours * 3600 + minutes * 60 + seconds
    video = _VIDEO_PATTERN.search(stderr)
    if video:
        stream: dict[str, Any] = {
            "codec_type": "video",
            "codec_name": video.group("codec"),
            "width": int(video.group("width")),
            "height": int(video.group("height")),
        }
        fps_match = _FPS_PATTERN.search(stderr[video.end() : video.end() + 200])
        if fps_match:
            stream["avg_frame_rate"] = f"{fps_match.group('fps')}/1"
        streams.append(stream)
    audio = _AUDIO_PATTERN.search(stderr)
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio.group("codec")})
    if not streams:
        return None
    return {
        "streams": streams,
        "format": {
            "duration": str(duration_s) if duration_s else "",
            "format_name": Path(path).suffix.lstrip(".").lower() or "media",
        },
    }


def probe_file(
    path: Path,
    *,
    ffprobe_path: str = "ffprobe",
    content_type: str = "",
    ffmpeg_path: str = "ffmpeg",
) -> MediaInfo:
    if not path.exists() or not path.is_file():
        return MediaInfo(container="missing", notes=("file_missing",))
    probe = _ffprobe_json(path, ffprobe_path)
    tool = "ffprobe"
    if probe is None:
        probe = _ffmpeg_metadata(path, ffmpeg_path)
        tool = "ffmpeg"
    size = path.stat().st_size
    if probe is None:
        data = path.read_bytes() if size <= 32 * 1024 * 1024 else path.read_bytes()[: 32 * 1024 * 1024]
        return parse_bytes(data, content_type)
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = fmt.get("duration")
    fps = None
    if video and video.get("avg_frame_rate"):
        try:
            numerator, denominator = video["avg_frame_rate"].split("/")
            fps = float(numerator) / float(denominator) if float(denominator) else None
        except (ValueError, ZeroDivisionError):
            fps = None
    return MediaInfo(
        container=str(fmt.get("format_name", "")),
        duration_s=float(duration) if duration else None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=fps,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        tool=tool,
        decoded=True,
        raw={"streams": streams, "format": fmt},
    )


def read_box_sizes(data: bytes) -> list[tuple[str, int]]:
    return [(box_type, end - start) for box_type, start, end in _boxes(data, 0, len(data))]


def make_mp4_fixture(
    *, duration_s: float = 6.0, width: int = 768, height: int = 1344, fps: int = 24, audio: bool = True
) -> bytes:
    """Build a tiny structurally valid MP4 container for offline tests.

    The payload has no decodable frames; tests use it to exercise container
    inspection only and must never present it as a playable video.
    """
    timescale = 1000
    duration = int(duration_s * timescale)

    def box(kind: str, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind.encode("latin-1") + payload

    def full_box(kind: str, version: int, payload: bytes) -> bytes:
        return box(kind, bytes([version, 0, 0, 0]) + payload)

    mvhd = full_box("mvhd", 0, struct.pack(">IIII", 0, 0, timescale, duration) + b"\x00" * 80)
    tkhd_video = full_box("tkhd", 0, b"\x00" * 72 + struct.pack(">II", width << 16, height << 16))
    hdlr_video = full_box("hdlr", 0, b"\x00" * 4 + b"vide" + b"\x00" * 13)
    mdhd_video = full_box("mdhd", 0, struct.pack(">IIII", 0, 0, timescale, duration) + b"\x00\x00\x00\x00")
    stts = full_box("stts", 0, struct.pack(">III", 1, int(duration_s * fps), timescale // fps))
    stsd_video = full_box("stsd", 0, struct.pack(">I", 1) + box("avc1", b"\x00" * 16))
    stbl_video = box("stbl", stsd_video + stts)
    minf_video = box("minf", stbl_video)
    mdia_video = box("mdia", hdlr_video + mdhd_video + minf_video)
    trak_video = box("trak", tkhd_video + mdia_video)
    traks = trak_video
    if audio:
        tkhd_audio = full_box("tkhd", 0, b"\x00" * 76)
        hdlr_audio = full_box("hdlr", 0, b"\x00" * 4 + b"soun" + b"\x00" * 13)
        mdhd_audio = full_box(
            "mdhd", 0, struct.pack(">IIII", 0, 0, 48000, 48000 * int(duration_s)) + b"\x00\x00\x00\x00"
        )
        stsd_audio = full_box("stsd", 0, struct.pack(">I", 1) + box("mp4a", b"\x00" * 16))
        stbl_audio = box("stbl", stsd_audio)
        minf_audio = box("minf", stbl_audio)
        mdia_audio = box("mdia", hdlr_audio + mdhd_audio + minf_audio)
        traks += box("trak", tkhd_audio + mdia_audio)
    moov = box("moov", mvhd + traks)
    ftyp = box("ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2avc1mp41")
    mdat = box("mdat", b"\x00" * 64)
    return ftyp + moov + mdat

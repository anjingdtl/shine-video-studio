"""Subtitles as an external, revisable layer that is composited at the end.

Burning subtitles into a rendered shot would freeze them at render time: a
line that the recognizer misread, or a timing that a human wants to change,
could then only be fixed by rendering the shot again.  The subtitle layer is
therefore kept outside the video:

* the alignment of the real audio produces a subtitle file;
* a human may revise that file, and every revision is kept;
* the delivery step composites the approved revision into the reel.

The subtitles never change the picture and never change the audio.  They are
produced from the real alignment of the real artifact, so a subtitle that
does not match the audio is a visible defect rather than a silent one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

MAX_CUES = 2000
MAX_TEXT_LENGTH = 200


class SubtitleError(HTTPException):
    """A refusal with a stable machine-readable detail."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)


@dataclass(frozen=True)
class SubtitleCue:
    """One subtitle line with its time range."""

    index: int
    start_s: float
    end_s: float
    text: str

    def validate(self, *, duration_s: float | None = None) -> None:
        if not self.text.strip():
            raise SubtitleError(422, "SUBTITLE_TEXT_EMPTY")
        if len(self.text) > MAX_TEXT_LENGTH:
            raise SubtitleError(422, "SUBTITLE_TEXT_TOO_LONG")
        if self.start_s < 0 or self.end_s <= self.start_s:
            raise SubtitleError(422, "SUBTITLE_TIME_INVALID")
        if duration_s is not None and self.end_s > duration_s + 0.05:
            raise SubtitleError(422, "SUBTITLE_TIME_OUTSIDE_MEDIA")


def _stamp(seconds: float, *, ass: bool) -> str:
    units = round(seconds * (100 if ass else 1000))
    hours, units = divmod(units, 360000 if ass else 3600000)
    minutes, units = divmod(units, 6000 if ass else 60000)
    secs, fraction = divmod(units, 100 if ass else 1000)
    if ass:
        return f"{hours}:{minutes:02}:{secs:02}.{fraction:02}"
    return f"{hours:02}:{minutes:02}:{secs:02},{fraction:03}"


def render_srt(cues: list[SubtitleCue]) -> str:
    """The SRT text of the cues."""
    blocks = []
    for cue in cues:
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n")
        blocks.append(
            f"{cue.index}\n{_stamp(cue.start_s, ass=False)} --> {_stamp(cue.end_s, ass=False)}\n{text}\n"
        )
    return "\n".join(blocks)


def render_ass(cues: list[SubtitleCue], *, font_size: int = 48) -> str:
    """The ASS text of the cues.

    ASS is what FFmpeg composites, so the burned-in result keeps the exact
    timing the human approved.
    """
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,"
        "2,1,2,60,60,60,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = []
    for cue in cues:
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n").replace("\\", "＼")
        text = text.replace("{", "｛").replace("}", "｝").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_stamp(cue.start_s, ass=True)},{_stamp(cue.end_s, ass=True)},"
            f"Default,,0,0,0,,{text}"
        )
    return "\n".join(head + lines)


def parse_srt(text: str) -> list[SubtitleCue]:
    """Read an SRT file that a human revised.

    The revision is the authority here: a human may correct a word or a
    timing, and the file is the thing that is composited.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise SubtitleError(422, "SUBTITLE_EMPTY")
    cues: list[SubtitleCue] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if len(lines) < 2:
            continue
        timing_index = 1 if lines[0].strip().isdigit() else 0
        if timing_index >= len(lines):
            continue
        match = re.match(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            lines[timing_index].strip(),
        )
        if not match:
            raise SubtitleError(422, "SUBTITLE_TIMING_INVALID")
        values = [int(value) for value in match.groups()]
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        body = "\n".join(lines[timing_index + 1:]).strip()
        cues.append(SubtitleCue(index=len(cues) + 1, start_s=start, end_s=end, text=body))
    if not cues:
        raise SubtitleError(422, "SUBTITLE_NO_CUES")
    if len(cues) > MAX_CUES:
        raise SubtitleError(422, "SUBTITLE_TOO_MANY_CUES")
    return cues


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cues_from_alignment(words: list[dict], *, max_chars: int = 18) -> list[SubtitleCue]:
    """Group an alignment into subtitle lines.

    The alignment of the real audio provides the words and their times; the
    grouping only decides where a line breaks, and never changes a word.
    """
    cues: list[SubtitleCue] = []
    buffer: list[dict] = []

    def flush() -> None:
        if not buffer:
            return
        text = "".join(str(word.get("text", "")) for word in buffer).strip()
        if text:
            cues.append(
                SubtitleCue(
                    index=len(cues) + 1,
                    start_s=float(buffer[0]["start_s"]),
                    end_s=float(buffer[-1]["end_s"]),
                    text=text,
                )
            )
        buffer.clear()

    for word in words:
        buffer.append(word)
        length = sum(len(str(item.get("text", ""))) for item in buffer)
        if length >= max_chars:
            flush()
    flush()
    return cues


def write_subtitle_files(directory: Path, cues: list[SubtitleCue]) -> dict[str, str]:
    """Write the SRT and the ASS file of the cues."""
    directory.mkdir(parents=True, exist_ok=True)
    srt = render_srt(cues)
    ass = render_ass(cues)
    (directory / "subtitles.srt").write_text(srt, encoding="utf-8")
    (directory / "subtitles.ass").write_text(ass, encoding="utf-8")
    return {"srt": srt, "ass": ass}


__all__ = [
    "MAX_CUES",
    "SubtitleCue",
    "SubtitleError",
    "cues_from_alignment",
    "parse_srt",
    "render_ass",
    "render_srt",
    "sha256_text",
    "write_subtitle_files",
]

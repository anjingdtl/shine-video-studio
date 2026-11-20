"""Mirrored advisory review of a hand-written H3 prompt (from shine/h3/spec_review.py).

Every finding is advisory-only (non-blocking). Manual prompt text and
reference bindings remain authoritative.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

BASE_SECTION_ORDER = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_SECTION_ORDER = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
LABEL_PATTERN = re.compile(r"<(Subject|Picture|Video|Audio) (\d+)>")
NARRATOR_SPEAKER_NAMES = {"旁白", "画外音", "narrator", "voiceover", "voice-over", "vo"}

_CJK = re.compile(r"[\u3400-\u9fff]")
_QUOTED_OR_DIALOGUE = re.compile(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」|『[^』]*』", re.S)
_TIMESTAMP = re.compile(r"At (\d{2}):(\d{2}\.\d{3})")
MAX_PROMPT_CHARS = 12000


def _finding(code: str, message: str, *, field: str = "prompt") -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "field": field,
        "message": message,
    }


def _is_narrator(speaker: str) -> bool:
    return (speaker or "").strip().lower() in NARRATOR_SPEAKER_NAMES


def section_order_for(mode: str = "Ref2VA") -> tuple[str, ...]:
    return REF_SECTION_ORDER if str(mode).lower() == "ref2va" else BASE_SECTION_ORDER


def split_h3_sections(prompt: str, mode: str = "Ref2VA") -> dict[str, str]:
    order = section_order_for(mode)
    text = (prompt or "").strip()
    if not text:
        return {}
    pattern = re.compile(r"(?m)^\s*(" + "|".join(re.escape(name) for name in order) + r")\s*:\s*$")
    matches = list(pattern.finditer(text))
    if not matches:
        primary = order[3] if order is REF_SECTION_ORDER else order[0]
        return {primary: text}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[start:end].strip()
    return sections


def review_h3_prompt(
    prompt: str,
    *,
    mode: str = "Ref2VA",
    dialogue: Iterable[Any] = (),
    duration_s: float | None = None,
    defined_labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    text = (prompt or "").strip()
    if not text:
        return [_finding("H3_EMPTY", "提示词为空；冻结前请填写这一镜的 H3 描述。")]

    if len(text) > MAX_PROMPT_CHARS:
        findings.append(
            _finding(
                "H3_LIMITS",
                f"提示词长度 {len(text)} 字符超过建议上限 {MAX_PROMPT_CHARS}，过长部分可能被忽略。",
            )
        )

    order = section_order_for(mode)
    sections = split_h3_sections(text, mode)
    if len(sections) <= 1 and order[0] not in sections:
        findings.append(
            _finding(
                "H3_SECTIONS",
                "未检测到完整 H3 官方 section 标题（如 subject_definitions:），仅供格式参考。",
            )
        )
    else:
        missing = [name for name in order if not str(sections.get(name) or "").strip()]
        if missing:
            findings.append(_finding("H3_SECTIONS", "缺少建议 section：" + "、".join(missing)))

    descriptive = _QUOTED_OR_DIALOGUE.sub("", text)
    if _CJK.search(descriptive):
        findings.append(
            _finding(
                "H3_ENGLISH",
                "对白/引号外出现中文描述；H3 官方建议主体与镜头描述用英文，台词保留原语种。",
            )
        )

    rows = [dict(row) for row in (dialogue or ()) if isinstance(row, dict)]
    missing_lines = [str(row.get("text") or "") for row in rows if row.get("text") and str(row["text"]) not in text]
    if missing_lines:
        findings.append(
            _finding(
                "H3_DIALOGUE",
                f"分镜中有 {len(missing_lines)} 句台词未包含在提示词内。",
                field="dialogue",
            )
        )
    if rows:
        tag_count = len(re.findall(r"</?d\s*>", text, flags=re.I))
        if tag_count != 2 * len(rows):
            findings.append(
                _finding(
                    "H3_DIALOGUE",
                    f"对白标签 <d></d> 数量（{tag_count}）与台词数（{len(rows)}）不一致。",
                    field="dialogue",
                )
            )
        if any(_is_narrator(str(row.get("speaker") or "")) for row in rows) and (
            "off-screen narrator" not in text.casefold()
        ):
            findings.append(
                _finding(
                    "H3_NARRATOR",
                    "包含旁白台词，建议注明 off-screen narrator 不入镜。",
                    field="dialogue",
                )
            )

    if duration_s is not None:
        if not 4.0 <= float(duration_s) <= 15.0:
            findings.append(_finding("H3_LIMITS", "H3 单镜时长通常为 4–15 秒。"))
        visual = re.sub(r"At \d{2}:\d{2}\.\d{3}, [^\n]*?\([^)]*\) says: <d>.*?</d>", "", text, flags=re.S)
        times = [int(m) * 60 + float(s) for m, s in _TIMESTAMP.findall(visual)]
        if times:
            if times != sorted(times) or len(set(times)) != len(times):
                findings.append(_finding("H3_TIMING", "镜内时间戳未严格递增。"))
            if any(t <= 0 or t >= float(duration_s) for t in times):
                findings.append(_finding("H3_TIMING", "有时间戳超出了当前镜时长。"))

    if defined_labels is not None:
        used = {match.group(0) for match in LABEL_PATTERN.finditer(text)}
        unresolved = sorted(used - set(defined_labels))
        if unresolved:
            findings.append(_finding("H3_LABELS", "引用了未绑定参考标签：" + "、".join(unresolved)))

    return findings

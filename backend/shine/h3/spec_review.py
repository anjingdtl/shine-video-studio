"""Advisory review of a hand-written H3 prompt.

The official prompt-writing rules are guidance, not a gate.  A person may write
a shot prompt themselves and freeze it; this module answers "how far is this
from the published shape" so the workbench can show concrete suggestions while
still saving, freezing and rendering exactly what the person wrote.

Every finding is a warning.  Hard integrity (the prompt is what was frozen, the
style and duration still belong to this shot, references still fit) stays in the
callers, because those protect identity rather than style.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from shine.h3.compiler import BASE_SECTION_ORDER, LABEL_PATTERN, REF_SECTION_ORDER
from shine.h3.narrator import is_narrator_speaker

_CJK = re.compile(r"[\u3400-\u9fff]")
_QUOTED_OR_DIALOGUE = re.compile(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」|『[^』]*』", re.S)
_TIMESTAMP = re.compile(r"At (\d{2}):(\d{2}\.\d{3})")
MAX_PROMPT_CHARS = 12000
"""A soft ceiling: past this the shot description usually repeats itself."""

_RULE_BY_CODE = {
    "H3_SECTIONS": "H3_SECTIONS",
    "H3_ENGLISH": "H3_ENGLISH",
    "H3_DIALOGUE": "H3_DIALOGUE",
    "H3_TIMING": "H3_TIMING",
    "H3_LABELS": "H3_LABELS",
    "H3_LIMITS": "H3_LIMITS",
    "H3_DURATION": "H3_LIMITS",
}


def _finding(code: str, message: str, *, field: str = "prompt") -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "rule_id": _RULE_BY_CODE.get(code, "H3_SECTIONS"),
        "field": field,
        "message": message,
    }


def _dialogue_fields(dialogue: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in dialogue or ():
        if isinstance(line, dict):
            rows.append(line)
        elif hasattr(line, "model_dump"):
            rows.append(line.model_dump(mode="json"))
        else:  # pragma: no cover - a caller passing something unexpected is a bug
            continue
    return rows


def section_order_for(mode: str) -> tuple[str, ...]:
    return REF_SECTION_ORDER if str(mode).lower() == "ref2va" else BASE_SECTION_ORDER


def split_h3_sections(prompt: str, mode: str) -> dict[str, str]:
    """Best-effort split of a free-form prompt into the official sections.

    The editor lets a person edit one text block.  When the block still carries
    the official headings the split is exact; otherwise the whole text becomes
    the primary description section and the caller records that the structure
    was not recognised.
    """
    order = section_order_for(mode)
    text = (prompt or "").strip()
    if not text:
        return {}
    pattern = re.compile(
        r"(?m)^\s*(" + "|".join(re.escape(name) for name in order) + r")\s*:\s*$"
    )
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
    sections: dict[str, str] | None,
    *,
    mode: str,
    dialogue: Iterable[Any] = (),
    duration_s: float | None = None,
    defined_labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return every advisory finding about one shot prompt, in a stable order."""
    findings: list[dict[str, Any]] = []
    text = (prompt or "").strip()
    if not text:
        return [_finding("H3_SECTIONS", "提示词是空的；冻结前至少写一段这一镜的画面描述")]

    if len(text) > MAX_PROMPT_CHARS:
        findings.append(_finding(
            "H3_LIMITS",
            f"提示词有 {len(text)} 个字符，超过建议上限 {MAX_PROMPT_CHARS}；过长的描述通常会被模型忽略一部分",
        ))

    order = section_order_for(mode)
    present = dict(sections or {})
    if not present:
        findings.append(_finding(
            "H3_SECTIONS",
            "没有识别到官方 section 标题（如 detailed_description:）；整段将作为主描述提交",
        ))
    else:
        missing = [name for name in order if not str(present.get(name) or "").strip()]
        if missing:
            findings.append(_finding(
                "H3_SECTIONS",
                "缺少官方 section：" + "、".join(missing),
            ))
        actual = [name for name in present if name in order]
        expected = [name for name in order if name in present]
        if actual != expected:
            findings.append(_finding(
                "H3_SECTIONS",
                "section 顺序与官方顺序不一致：" + " → ".join(actual),
            ))

    descriptive = _QUOTED_OR_DIALOGUE.sub("", text)
    if _CJK.search(descriptive):
        findings.append(_finding(
            "H3_ENGLISH",
            "对白与引号可见文字之外出现了中文；H3 官方要求画面描述用英文，台词和画面文字保留原文",
        ))

    rows = _dialogue_fields(dialogue)
    missing_lines = [str(row.get("text") or "") for row in rows
                     if row.get("text") and str(row["text"]) not in text]
    if missing_lines:
        findings.append(_finding(
            "H3_DIALOGUE",
            f"有 {len(missing_lines)} 句台词没有出现在提示词里，H3 不会把它们说出来",
            field="dialogue",
        ))
    tag_count = len(re.findall(r"</?d\s*>", text, flags=re.I))
    if tag_count != 2 * len(rows):
        findings.append(_finding(
            "H3_DIALOGUE",
            f"对白标签数量（{tag_count}）与台词行数（{len(rows)}）不匹配；每句台词应各有一对 <d></d>",
            field="dialogue",
        ))

    if duration_s is not None:
        if not 4 <= float(duration_s) <= 15:
            findings.append(_finding("H3_LIMITS", "H3 单段生成只支持 4–15 秒"))
        visual = re.sub(
            r"At \d{2}:\d{2}\.\d{3}, [^\n]*?\([^)]*\) says: <d>.*?</d>", "", text, flags=re.S
        )
        times = [int(m) * 60 + float(s) for m, s in _TIMESTAMP.findall(visual)]
        if times:
            if times != sorted(times) or len(set(times)) != len(times):
                findings.append(_finding("H3_TIMING", "镜内切点时间不是递增的"))
            if any(t <= 0 or t >= float(duration_s) for t in times):
                findings.append(_finding("H3_TIMING", "有切点时间落在这一镜的时长之外"))

    if defined_labels is not None:
        used = {match.group(0) for match in LABEL_PATTERN.finditer(text)}
        unresolved = sorted(used - set(defined_labels))
        if unresolved:
            findings.append(_finding(
                "H3_LABELS",
                "引用了本次请求没有定义的参考标签：" + "、".join(unresolved),
            ))

    if any(is_narrator_speaker(str(row.get("speaker_id") or "")) for row in rows):
        if "off-screen narrator" not in text.casefold():
            findings.append(_finding(
                "H3_LABELS",
                "写了旁白台词，但提示词没有声明旁白只出声、不出画面",
                field="dialogue",
            ))
    return findings


def serialise(findings: list[dict[str, Any]]) -> list[str]:
    """The message-only projection carried on render requests and job warnings."""
    return [str(row.get("message") or "") for row in findings if row.get("message")]


__all__ = [
    "MAX_PROMPT_CHARS",
    "review_h3_prompt",
    "section_order_for",
    "serialise",
    "split_h3_sections",
]

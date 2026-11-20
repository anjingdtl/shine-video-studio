"""Mirrored document parser, long-text chunker and deterministic storyboard extractor.

Mirrored from:
* ``shine/agents/script.py`` (document decoding, DOCX/PDF extraction, format checks)
* ``shine/materialprep/source_spans.py`` (deterministic offset-preserving chunking)

Differences for Manual4:
* Scanned/empty PDFs are explicitly refused with ``M4_SCANNED_PDF_OCR_UNSUPPORTED``
  instead of guessing OCR.
* Deterministic parsing preserves exact user episode/shot numbers, target
  seconds, characters (including costume/appearance variants), scenes, props,
  dialogue and character offsets.
* Missing durations or duplicate/ambiguous shots are emitted as explicit
  ``ambiguities`` for human resolution — never guessed or silently rewritten.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from shine.manual4.storage.files import sha256_bytes

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
STRUCTURED_EXTENSIONS = {".json"}
OFFICE_EXTENSIONS = {".docx", ".pdf"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_CHUNK_CHARS = 4000


class M4ParseError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class M4ParsedDocument:
    filename: str
    source_format: str
    text: str
    sha256: str
    byte_size: int
    title: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class M4TextChunk:
    chunk_index: int
    start_offset: int
    end_offset: int
    text: str


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _parse_cn_or_int(token: str) -> int | None:
    raw = (token or "").strip()
    if raw.isdigit():
        return int(raw)
    if raw in _CN_DIGITS:
        return _CN_DIGITS[raw]
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _docx_text(content: bytes) -> str:
    try:
        with zipfile_open(content) as archive:
            xml_bytes = archive.read("word/document.xml")
    except Exception as exc:
        raise M4ParseError("M4_DOCX_INVALID", "DOCX 文件无法读取或格式损坏") from exc
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise M4ParseError("M4_DOCX_INVALID", "DOCX 文档 XML 格式损坏") from exc
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for p in root.iter(f"{ns}p"):
        line = "".join(t.text or "" for t in p.iter(f"{ns}t")).strip()
        if line:
            paragraphs.append(line)
    text = "\n".join(paragraphs).strip()
    if not text:
        raise M4ParseError("M4_DOCUMENT_EMPTY", "DOCX 文件没有可提取的文本内容")
    return text


def zipfile_open(content: bytes):
    import zipfile

    return zipfile.ZipFile(io.BytesIO(content))


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise M4ParseError("M4_PDF_UNAVAILABLE", "缺少 pypdf 依赖，无法解析 PDF") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise M4ParseError("M4_PDF_INVALID", "PDF 文件无法打开或已损坏") from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception:
            pages.append("")
    text = "\n".join(p for p in pages if p).strip()
    if not text:
        raise M4ParseError(
            "M4_SCANNED_PDF_OCR_UNSUPPORTED",
            "该 PDF 未提取到可用文字（疑似扫描图或纯图片 PDF）；V1 暂不支持图片 OCR，"
            "请直接粘贴文本或导入 TXT/MD/DOCX。",
        )
    return text


def parse_m4_document(filename: str, content: bytes) -> M4ParsedDocument:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise M4ParseError("M4_DOCUMENT_TOO_LARGE", "分镜文档大小不能超过 20 MiB")
    if not content:
        raise M4ParseError("M4_DOCUMENT_EMPTY", "上传的分镜文档为空")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    title = ""
    parsed: dict[str, Any] = {}
    if suffix in TEXT_EXTENSIONS:
        text = _decode_text(content)
        for line in text.splitlines():
            s = line.strip().lstrip("#").strip()
            if s:
                title = s[:160]
                break
        source_format = "markdown" if suffix in {".md", ".markdown"} else "text"
    elif suffix in STRUCTURED_EXTENSIONS:
        try:
            value = json.loads(_decode_text(content))
        except json.JSONDecodeError as exc:
            raise M4ParseError("M4_JSON_INVALID", "JSON 分镜文件格式不正确") from exc
        if not isinstance(value, dict):
            raise M4ParseError("M4_JSON_INVALID", "JSON 分镜文件必须是对象")
        parsed = value
        title = str(value.get("title", ""))[:160]
        for key in ("text", "storyboard", "script", "content", "body"):
            if isinstance(value.get(key), str) and value[key].strip():
                text = value[key].strip()
                break
        else:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        source_format = "json"
    elif suffix == ".docx":
        text = _docx_text(content)
        source_format = "docx"
    elif suffix == ".pdf":
        text = _pdf_text(content)
        source_format = "pdf"
    else:
        raise M4ParseError(
            "M4_FORMAT_UNSUPPORTED",
            "仅支持 TXT、Markdown、JSON、DOCX 与含可提取文本的 PDF（不支持图片/扫描 OCR）。",
        )
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        raise M4ParseError("M4_DOCUMENT_EMPTY", "分镜文档内容为空")
    if not title:
        title = normalized.splitlines()[0].strip()[:160]
    return M4ParsedDocument(
        filename=filename,
        source_format=source_format,
        text=normalized,
        sha256=sha256_bytes(normalized.encode("utf-8")),
        byte_size=len(content),
        title=title,
        parsed=parsed,
    )


_EPISODE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:第\s*([0-9零一二三四五六七八九十两]+)\s*集|EP(?:ISODE)?\s*0*(\d+))"
    r"(?:\s*[:：\-—·|]\s*(.+?))?"
    r"(?:\s*[（(]\s*(?:(?:目标总时长|总时长|target)\s*[:：]?\s*)?(\d+(?:\.\d+)?)\s*(?:s|秒)\s*[)）])?\s*$",
    re.IGNORECASE,
)

_SHOT_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:第\s*([0-9零一二三四五六七八九十两]+)\s*[镜场]|(?:SHOT|S)\s*0*(\d+))"
    r"(?:\s*[（(]\s*(?:时长\s*[:：]?\s*)?(\d+(?:\.\d+)?)\s*(?:s|sec|秒)\s*[)）])?"
    r"(?:\s*[:：\-—]\s*(.*))?$",
    re.IGNORECASE,
)

_DURATION_FIELD_RE = re.compile(r"(?:时长|秒数|duration)\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:s|sec|秒)?", re.IGNORECASE)
_INLINE_DURATION_RE = re.compile(r"[（(\[]\s*(\d+(?:\.\d+)?)\s*(?:s|sec|秒)\s*[)）\]]", re.IGNORECASE)
_CAST_RE = re.compile(r"^(?:人物|角色|出场人物)\s*[:：]\s*(.+)$")
_SCENE_RE = re.compile(r"^(?:场景|地点)\s*[:：]\s*(.+)$")
_PROP_RE = re.compile(r"^(?:道具|物品)\s*[:：]\s*(.+)$")
_DIALOGUE_RE = re.compile(r"^(?:对白|台词)\s*[:：]\s*(.+)$")
_QUOTE_LINE_RE = re.compile(r'^([^\s:：]{1,24})\s*[:：]\s*[「『"](.+?)[」』"]\s*$')
_VARIANT_RE = re.compile(r"^(.+?)\s*[（(\[【]([^）)\]】]+)[）)\]】]$")


def chunk_storyboard_text(text: str, *, max_chunk_chars: int = MAX_CHUNK_CHARS) -> list[M4TextChunk]:
    """Deterministically slice long storyboard text on line/episode/shot boundaries."""
    if not text or not text.strip():
        return []
    if len(text) <= max_chunk_chars:
        return [M4TextChunk(chunk_index=0, start_offset=0, end_offset=len(text), text=text)]

    chunks: list[M4TextChunk] = []
    lines = list(re.finditer(r"[^\n]*\n|[^\n]+$", text))
    chunk_start = 0
    chunk_end = 0
    last_safe_break = 0
    idx = 0
    for match in lines:
        line_str = match.group()
        if _EPISODE_RE.match(line_str.strip()) or _SHOT_RE.match(line_str.strip()):
            if match.start() > chunk_start:
                last_safe_break = match.start()
        if match.end() - chunk_start > max_chunk_chars and chunk_end > chunk_start:
            split_at = last_safe_break if last_safe_break > chunk_start else chunk_end
            chunks.append(
                M4TextChunk(
                    chunk_index=idx,
                    start_offset=chunk_start,
                    end_offset=split_at,
                    text=text[chunk_start:split_at],
                )
            )
            idx += 1
            chunk_start = split_at
            last_safe_break = split_at
        chunk_end = match.end()
    if chunk_end > chunk_start:
        chunks.append(
            M4TextChunk(
                chunk_index=idx,
                start_offset=chunk_start,
                end_offset=chunk_end,
                text=text[chunk_start:chunk_end],
            )
        )
    return chunks


def _split_entity_tokens(raw: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for token in re.split(r"[,，、;；]+", raw.strip()):
        name = token.strip()
        if not name or name in {"无", "暂无", "none", "None", "-"}:
            continue
        m = _VARIANT_RE.match(name)
        if m:
            items.append({"name": m.group(1).strip(), "variant": m.group(2).strip()})
        else:
            items.append({"name": name, "variant": ""})
    return items


def extract_storyboard_from_text(
    text: str,
    *,
    source_sha256: str = "",
    document_id: str | None = None,
    max_chunk_chars: int = MAX_CHUNK_CHARS,
) -> dict[str, Any]:
    """Deterministically extract episodes, shots, durations, assets and ambiguities.

    Never invents missing durations, never drops shots across long-text chunks,
    and never rewrites user shot descriptions.
    """
    normalized = re.sub(r"\r\n?", "\n", text or "")
    if not normalized.strip():
        return {
            "episodes": [],
            "ambiguities": [
                {
                    "code": "M4_STORYBOARD_TEXT_EMPTY",
                    "message": "输入内容为空，请粘贴分镜或手工添加镜头。",
                }
            ],
            "chunks": [],
            "extractor": "deterministic",
        }

    chunks = chunk_storyboard_text(normalized, max_chunk_chars=max_chunk_chars)
    episodes_map: dict[int, dict[str, Any]] = {}
    episode_order: list[int] = []
    seen_shot_codes: set[str] = set()
    ambiguities: list[dict[str, Any]] = []

    current_ep_no = 1
    current_shot: dict[str, Any] | None = None

    def _ensure_episode(ep_no: int, title: str = "", target_total_s: float | None = None) -> dict[str, Any]:
        if ep_no not in episodes_map:
            episodes_map[ep_no] = {
                "episode_no": ep_no,
                "episode_code": f"ep{ep_no:02d}",
                "title": title or f"第 {ep_no} 集",
                "target_total_seconds": target_total_s,
                "shots": [],
            }
            episode_order.append(ep_no)
        else:
            if title and episodes_map[ep_no]["title"] == f"第 {ep_no} 集":
                episodes_map[ep_no]["title"] = title
            if target_total_s is not None:
                episodes_map[ep_no]["target_total_seconds"] = target_total_s
        return episodes_map[ep_no]

    def _finalize_shot(shot: dict[str, Any] | None) -> None:
        if shot is None:
            return
        shot["summary"] = "\n".join(shot.pop("_summary_lines", [])).strip()
        if shot["target_seconds"] is None:
            ambiguities.append(
                {
                    "code": "M4_AMBIGUITY_MISSING_DURATION",
                    "episode_code": shot["episode_code"],
                    "shot_code": shot["shot_code"],
                    "message": f"镜头 {shot['shot_code']} 未标注时长（秒），请在分镜表中手工补充后再冻结。",
                }
            )
        if not shot["requested_assets"]:
            ambiguities.append(
                {
                    "code": "M4_AMBIGUITY_NO_ASSETS_MENTIONED",
                    "episode_code": shot["episode_code"],
                    "shot_code": shot["shot_code"],
                    "message": f"镜头 {shot['shot_code']} 未写明人物/场景/道具，Ref2VA 仍需在②素材生成补充参考图。",
                }
            )

    for chunk in chunks:
        line_matches = list(re.finditer(r"[^\n]+", chunk.text))
        for m in line_matches:
            line = m.group().strip()
            abs_start = chunk.start_offset + m.start()
            abs_end = chunk.start_offset + m.end()
            if not line:
                continue

            ep_match = _EPISODE_RE.match(line)
            if ep_match:
                _finalize_shot(current_shot)
                current_shot = None
                ep_num_raw = ep_match.group(1) or ep_match.group(2)
                ep_no = _parse_cn_or_int(ep_num_raw) or 1
                ep_title = (ep_match.group(3) or "").strip()
                target_total = float(ep_match.group(4)) if ep_match.group(4) else None
                current_ep_no = ep_no
                _ensure_episode(ep_no, ep_title, target_total)
                continue

            shot_match = _SHOT_RE.match(line)
            if shot_match:
                _finalize_shot(current_shot)
                ep_obj = _ensure_episode(current_ep_no)
                sh_num_raw = shot_match.group(1) or shot_match.group(2)
                shot_no = _parse_cn_or_int(sh_num_raw) or (len(ep_obj["shots"]) + 1)
                shot_code = f"ep{current_ep_no:02d}-s{shot_no:02d}"
                if shot_code in seen_shot_codes:
                    ambiguities.append(
                        {
                            "code": "M4_AMBIGUITY_DUPLICATE_SHOT",
                            "episode_code": ep_obj["episode_code"],
                            "shot_code": shot_code,
                            "message": f"检测到重复镜号 {shot_code}，请确认是否为不同镜次并调整编号。",
                        }
                    )
                    shot_code = f"ep{current_ep_no:02d}-s{shot_no:02d}-dup{len(ep_obj['shots']) + 1}"
                seen_shot_codes.add(shot_code)
                duration_s = float(shot_match.group(3)) if shot_match.group(3) else None
                tail = (shot_match.group(4) or "").strip()
                if duration_s is None and tail:
                    dur_inline = _INLINE_DURATION_RE.search(tail) or _DURATION_FIELD_RE.search(tail)
                    if dur_inline:
                        duration_s = float(dur_inline.group(1))
                        tail = (tail[: dur_inline.start()] + tail[dur_inline.end() :]).strip(" -—:：")
                current_shot = {
                    "episode_code": ep_obj["episode_code"],
                    "shot_no": shot_no,
                    "shot_code": shot_code,
                    "target_seconds": duration_s,
                    "summary": "",
                    "_summary_lines": [tail] if tail else [],
                    "dialogue": [],
                    "requested_assets": [],
                    "source_span": {
                        "document_id": document_id,
                        "source_sha256": source_sha256,
                        "start_offset": abs_start,
                        "end_offset": abs_end,
                        "excerpt": line[:240],
                    },
                    "aspect_ratio": "16:9",
                    "continuity_mode": "cut",
                }
                ep_obj["shots"].append(current_shot)
                continue

            if current_shot is None:
                continue

            current_shot["source_span"]["end_offset"] = abs_end
            dur_field = _DURATION_FIELD_RE.search(line)
            if dur_field and current_shot["target_seconds"] is None:
                current_shot["target_seconds"] = float(dur_field.group(1))
                continue

            cast_m = _CAST_RE.match(line)
            if cast_m:
                for ent in _split_entity_tokens(cast_m.group(1)):
                    current_shot["requested_assets"].append(
                        {"family": "character", "name": ent["name"], "variant": ent["variant"]}
                    )
                continue

            scene_m = _SCENE_RE.match(line)
            if scene_m:
                for ent in _split_entity_tokens(scene_m.group(1)):
                    current_shot["requested_assets"].append(
                        {"family": "scene", "name": ent["name"], "variant": ent["variant"]}
                    )
                continue

            prop_m = _PROP_RE.match(line)
            if prop_m:
                for ent in _split_entity_tokens(prop_m.group(1)):
                    current_shot["requested_assets"].append(
                        {"family": "prop", "name": ent["name"], "variant": ent["variant"]}
                    )
                continue

            dlg_m = _DIALOGUE_RE.match(line)
            if dlg_m:
                raw_dlg = dlg_m.group(1).strip()
                qm = _QUOTE_LINE_RE.match(raw_dlg)
                if qm:
                    current_shot["dialogue"].append({"speaker": qm.group(1), "text": qm.group(2)})
                else:
                    current_shot["dialogue"].append({"speaker": "", "text": raw_dlg})
                continue

            qm = _QUOTE_LINE_RE.match(line)
            if qm:
                current_shot["dialogue"].append({"speaker": qm.group(1), "text": qm.group(2)})
                continue

            current_shot["_summary_lines"].append(line)

    _finalize_shot(current_shot)
    episodes = [episodes_map[no] for no in sorted(episode_order)]
    if not episodes or all(not ep["shots"] for ep in episodes):
        ambiguities.append(
            {
                "code": "M4_AMBIGUITY_UNSTRUCTURED_TEXT",
                "message": (
                    "未能从原文确定性识别标准「第X集 / 第Y镜」结构；"
                    "可在下方集镜表直接手工编辑，或选用已配置的 LLM 抽取结构。"
                ),
            }
        )
    return {
        "episodes": episodes,
        "ambiguities": ambiguities,
        "chunks": [
            {"chunk_index": c.chunk_index, "start_offset": c.start_offset, "end_offset": c.end_offset}
            for c in chunks
        ],
        "extractor": "deterministic",
    }

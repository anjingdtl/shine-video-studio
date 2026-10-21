from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree


class ScriptParseError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ParsedDocument:
    filename: str
    source_format: str
    text: str
    title: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)


TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
STRUCTURED_EXTENSIONS = {".json"}
OFFICE_EXTENSIONS = {".docx", ".pdf"}


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml_bytes = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ScriptParseError("SCRIPT_DOCX_INVALID", "The DOCX file does not contain a readable document") from exc
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ScriptParseError("SCRIPT_DOCX_INVALID", "The DOCX document XML is malformed") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    text = "\n".join(paragraphs)
    if not text:
        raise ScriptParseError("SCRIPT_EMPTY", "No text could be extracted from the DOCX document")
    return text


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ScriptParseError("SCRIPT_PDF_UNAVAILABLE", "PDF parsing requires the pypdf package") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise ScriptParseError("SCRIPT_PDF_INVALID", "The PDF file could not be opened") from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages).strip()
    if not text:
        raise ScriptParseError("SCRIPT_EMPTY", "No text could be extracted from the PDF document")
    return text


def parse_document(filename: str, content: bytes) -> ParsedDocument:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if len(content) > 20 * 1024 * 1024:
        raise ScriptParseError("SCRIPT_TOO_LARGE", "Script documents are limited to 20 MiB")
    title = ""
    parsed: dict[str, Any] = {}
    if suffix in TEXT_EXTENSIONS:
        text = _decode_text(content)
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped[:200]
                break
        source_format = "markdown" if suffix in {".md", ".markdown"} else "text"
    elif suffix in STRUCTURED_EXTENSIONS:
        try:
            value = json.loads(_decode_text(content))
        except json.JSONDecodeError as exc:
            raise ScriptParseError("SCRIPT_JSON_INVALID", "The JSON script is malformed") from exc
        if not isinstance(value, dict):
            raise ScriptParseError("SCRIPT_JSON_INVALID", "The JSON script must be an object")
        parsed = value
        title = str(value.get("title", ""))[:200]
        for key in ("text", "script", "content", "body"):
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
        raise ScriptParseError(
            "SCRIPT_FORMAT_UNSUPPORTED",
            "Supported script formats are TXT, Markdown, JSON, DOCX, and PDF",
        )
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ScriptParseError("SCRIPT_EMPTY", "The script document is empty")
    if not title:
        title = text.splitlines()[0][:200] if text else ""
    return ParsedDocument(filename=filename, source_format=source_format, text=text, title=title, parsed=parsed)

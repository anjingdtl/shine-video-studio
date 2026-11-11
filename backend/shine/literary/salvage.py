"""Local, lossless recovery of a model answer.

A model that answers with a complete scene inside a slightly wrong envelope is
not a failed scene.  The old path raised before the text left the transport, so
the text was gone before anything could keep it.  This module is the recovery
step: it reads the *raw* answer, tolerates the shapes real providers produce
(code fences, prose before the object, a trailing comma, the script under
``content`` instead of ``text``) and returns either a usable payload or the
plain literary text.

Hard rules:

* nothing here edits literary prose - no truncation, no paraphrase, no
  de-duplication of deliberate repeated dialogue;
* a repair is recorded as a note, so the artifact can say how it was read;
* when no object can be recovered, the raw text is still returned as the
  content, because the chapter exists even if the JSON does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

#: Envelope keys real models use for the actual literary body.  Ordered: the
#: first present key wins, so a payload that already has ``text`` is untouched.
TEXT_KEYS = ("text", "content", "script", "script_text", "scene_text", "screenplay", "body", "markdown")

#: Keys that identify the unit rather than describe it.
UNIT_KEY_ALIASES = {
    "scene_id": "scene_key",
    "section_key": "scene_key",
    "scene": "scene_key",
    "paragraph_id": "section_id",
}


@dataclass
class Salvage:
    """What could be read out of one raw answer."""

    payload: dict[str, Any] | None = None
    text: str = ""
    notes: list[str] = field(default_factory=list)
    raw_chars: int = 0

    @property
    def recovered(self) -> bool:
        return bool(self.text.strip()) or self.payload is not None

    def as_dict(self) -> dict[str, Any]:
        return {"recovered": self.recovered, "text_chars": len(self.text),
                "raw_chars": self.raw_chars, "notes": list(self.notes)}


def strip_code_fence(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped)
    return stripped


def _balanced_object(text: str) -> str | None:
    """The first balanced ``{...}`` block, string- and escape-aware."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _loads(candidate: str) -> dict[str, Any] | None:
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_payload(raw: str, *, notes: list[str] | None = None) -> dict[str, Any] | None:
    """Read one JSON object out of a raw answer, tolerating common envelopes."""
    notes = notes if notes is not None else []
    text = strip_code_fence(raw or "")
    if not text:
        return None
    direct = _loads(text)
    if direct is not None:
        return direct
    block = _balanced_object(text)
    if block is None:
        return None
    notes.append("object_extracted_from_envelope")
    parsed = _loads(block)
    if parsed is not None:
        return parsed
    repaired = _TRAILING_COMMA_RE.sub(r"\1", block)
    parsed = _loads(repaired)
    if parsed is not None:
        notes.append("trailing_comma_removed")
        return parsed
    return None


def _first_text(payload: dict[str, Any]) -> tuple[str, str | None]:
    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value, key
    return "", None


def normalize_unit_keys(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy unit identifiers to their canonical names without dropping originals."""
    normalized = dict(payload)
    for source, target in UNIT_KEY_ALIASES.items():
        if target not in normalized and isinstance(normalized.get(source), str):
            normalized[target] = normalized[source]
    return normalized


def apply_aliases(payload: dict[str, Any], aliases: dict[str, str] | None) -> tuple[dict[str, Any], list[str]]:
    """Fill canonical fields from their accepted aliases, locally and losslessly.

    Only a *missing* canonical field is filled, and the original key stays in
    the payload: nothing is moved, renamed or rewritten.  Returns the payload
    and the notes describing what was read from where.
    """
    normalized = normalize_unit_keys(payload)
    notes: list[str] = []
    for source, target in (aliases or {}).items():
        if target in normalized:
            continue
        value = normalized.get(source)
        if isinstance(value, str) and value.strip():
            normalized[target] = value
            notes.append(f"field_aliased:{source}->{target}")
    return normalized, notes


def salvage_answer(raw: str, *, aliases: dict[str, str] | None = None) -> Salvage:
    """Recover the literary body from one raw provider answer.

    ``aliases`` maps an alternative field name to a canonical one (for example
    ``{"content": "text"}``); the canonical field is only filled when it is
    really absent, so an answer that already satisfies the contract is never
    rewritten.
    """
    notes: list[str] = []
    raw = raw or ""
    payload = parse_payload(raw, notes=notes)
    if payload is None:
        body = strip_code_fence(raw)
        return Salvage(payload=None, text=body, notes=[*notes, "no_json_object_raw_text_kept"],
                       raw_chars=len(raw))
    payload = normalize_unit_keys(payload)
    for source, target in (aliases or {}).items():
        if target not in payload and isinstance(payload.get(source), str) and payload[source].strip():
            payload[target] = payload[source]
            notes.append(f"field_aliased:{source}->{target}")
    body, key = _first_text(payload)
    if key and key != "text":
        notes.append(f"text_read_from:{key}")
    return Salvage(payload=payload, text=body, notes=notes, raw_chars=len(raw))

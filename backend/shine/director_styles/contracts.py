"""Strict wire and storage contracts for director-style/1 profiles."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from shine.schemas import APIModel, StyleBible

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SUPPORTED_SCENE_TAGS = {
    "action", "architecture", "day", "dialogue", "drama", "emotion", "exterior",
    "interior", "mystery", "night", "urban",
}
SUPPORTED_CAPABILITIES = {
    "ffmpeg-cut", "ffmpeg-crossfade", "ffmpeg-speed-ramp", "h3-camera-motion-prompt", "h3-lighting-prompt",
}
RECOGNIZED_LOCKED_CONSTRAINTS = {
    "approved-asset-only", "axis-locked", "dialogue-locked", "duration-locked",
    "neutral-daylight-only", "strict-no-pause", "unobstructed-product-closeup",
}
_INJECTION = re.compile(
    r"(?:ignore|forget|disregard)\s+(?:all\s+)?(?:previous|prior|above)|system\s+prompt|developer\s+message|"
    r"jailbreak|act\s+as\s+(?:an?\s+)?(?:assistant|system)|as\s+an\s+ai|"
    r"https?://|\bwww\.|(?:^|\s)(?:\.\.?/|/)[\w.-]+|"
    r"\b[\w.-]+(?:/[\w.-]+)+\b|(?:^|\s)[A-Za-z]:[\\/][^\s]+|\\\\|"
    r"\b(?:change|alter|retime|shorten|lengthen|rewrite|replace)\s+(?:the\s+)?"
    r"(?:dialogue|spoken dialogue|script|story|character identity|shot duration|duration)\b|"
    r"忽略(?:之前|上文|系统|开发者|规则)|无视(?:之前|上文|系统|开发者|规则)|"
    r"(?:改写|擅自改|替换)(?:剧情|对白|人物身份|镜头时长)|执行(?:命令|脚本|工具)",
    re.IGNORECASE,
)
_HTML_OR_PROMPT_TAG = re.compile(r"</?(?:system|assistant|user|shot|subject|picture|audio|video)\b", re.I)
_CJK = re.compile(r"[\u3400-\u9fff]")


def _safe_text(value: str, *, english: bool = False) -> str:
    if _INJECTION.search(value) or _HTML_OR_PROMPT_TAG.search(value):
        raise ValueError("STYLE_PROFILE_UNSAFE_TEXT")
    if english and _CJK.search(value):
        raise ValueError("STYLE_TECHNIQUE_MUST_BE_ENGLISH")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("STYLE_PROFILE_CONTROL_CHARACTER")
    return value


class TechniqueCondition(APIModel):
    scene_tags_any: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("scene_tags_any")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(value not in SUPPORTED_SCENE_TAGS for value in values):
            raise ValueError("STYLE_PROFILE_INVALID_SCENE_TAG")
        return values


class StyleTechnique(APIModel):
    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    target: Literal["camera", "lighting", "color", "texture", "editing", "sound"]
    priority: int = Field(ge=0, le=100)
    when: TechniqueCondition = Field(default_factory=TechniqueCondition)
    instruction_en: str = Field(min_length=12, max_length=700)
    requires_capabilities: list[str] = Field(default_factory=list, max_length=8)
    conflicts_with: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("id", "requires_capabilities", "conflicts_with")
    @classmethod
    def valid_identifiers(cls, value):
        values = [value] if isinstance(value, str) else value
        if any(not _ID.fullmatch(item) for item in values):
            raise ValueError("STYLE_PROFILE_INVALID_IDENTIFIER")
        return value

    @field_validator("requires_capabilities")
    @classmethod
    def known_capabilities(cls, values: list[str]) -> list[str]:
        if not set(values).issubset(SUPPORTED_CAPABILITIES):
            raise ValueError("STYLE_PROFILE_UNKNOWN_CAPABILITY")
        return values

    @field_validator("instruction_en")
    @classmethod
    def safe_english_instruction(cls, value: str) -> str:
        return _safe_text(value, english=True)

    @model_validator(mode="after")
    def no_self_conflict(self):
        if self.id in self.conflicts_with or len(set(self.conflicts_with)) != len(self.conflicts_with):
            raise ValueError("STYLE_PROFILE_INVALID_CONFLICT")
        if len(set(self.requires_capabilities)) != len(self.requires_capabilities):
            raise ValueError("STYLE_PROFILE_DUPLICATE_CAPABILITY")
        return self


class DirectorStyleProfile(APIModel):
    schema_version: Literal["director-style/1"]
    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name_zh: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    summary_zh: str = Field(min_length=1, max_length=240)
    categories: list[str] = Field(min_length=1, max_length=12)
    style_bible: StyleBible
    techniques: list[StyleTechnique] = Field(min_length=1, max_length=40)
    source_notes: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("style_bible")
    @classmethod
    def safe_style_bible(cls, value: StyleBible) -> StyleBible:
        for item in value.model_dump(mode="json").values():
            _safe_text(item)
        return value

    @field_validator("name_zh", "summary_zh")
    @classmethod
    def safe_display_text(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, values: list[str]) -> list[str]:
        if (len(values) != len(set(values))
                or any(not _TAG.fullmatch(value) for value in values)):
            raise ValueError("STYLE_PROFILE_INVALID_CATEGORY")
        return values

    @field_validator("source_notes")
    @classmethod
    def safe_notes(cls, values: list[str]) -> list[str]:
        if any(not note.strip() or len(note) > 500 for note in values):
            raise ValueError("STYLE_PROFILE_INVALID_SOURCE_NOTE")
        return [_safe_text(note) for note in values]

    @model_validator(mode="after")
    def unique_techniques(self):
        identities = [item.id for item in self.techniques]
        if len(identities) != len(set(identities)):
            raise ValueError("STYLE_PROFILE_DUPLICATE_TECHNIQUE")
        known = set(identities)
        for technique in self.techniques:
            unknown = set(technique.conflicts_with) - known - RECOGNIZED_LOCKED_CONSTRAINTS
            if unknown:
                raise ValueError("STYLE_PROFILE_UNKNOWN_CONFLICT")
        return self


class DirectorStyleSelection(APIModel):
    profile_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    profile_version: str = Field(pattern=_VERSION.pattern)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strength: Literal["subtle", "balanced", "strong"] = "balanced"
    style_overrides: StyleBible | None = None


class DirectorStylePreviewRequest(DirectorStyleSelection):
    scene_tags: list[str] = Field(default_factory=list, max_length=16)
    compare_style: StyleBible | None = None

    @field_validator("scene_tags")
    @classmethod
    def valid_scene_tags(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(value not in SUPPORTED_SCENE_TAGS for value in values):
            raise ValueError("STYLE_PROFILE_INVALID_SCENE_TAG")
        return values


class DirectorStyleApplyRequest(DirectorStyleSelection):
    expected_draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StyleResolution(APIModel):
    schema_version: Literal["director-style-resolution/1"] = "director-style-resolution/1"
    profile_id: str
    profile_version: str
    profile_sha256: str
    strength: Literal["subtle", "balanced", "strong"]
    scene_tags: list[str]
    selected_technique_ids: list[str]
    constraints: dict[str, list[str]]
    warnings: list[str]
    rejected: list[dict[str, str]]
    input_sha256: str
    resolution_sha256: str

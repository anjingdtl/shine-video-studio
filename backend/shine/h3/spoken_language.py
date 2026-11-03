"""The spoken language of a project, in the one form the H3 prompt may use.

The whole H3 prompt is written in English, so the language of the voice is
stated in English as well.  A project setting written in another script would
put CJK outside the dialogue tags, which the official rule pack rejects, so the
language is normalised here instead of being copied into the prompt verbatim.
"""

from __future__ import annotations

#: Canonical English names of the languages a shot may be spoken in.
SPOKEN_LANGUAGES: tuple[str, ...] = (
    "Chinese",
    "English",
    "Japanese",
    "Korean",
    "Cantonese",
    "French",
    "German",
    "Spanish",
)

DEFAULT_SPOKEN_LANGUAGE = "Chinese"

#: Accepted spellings and native names, mapped to their canonical English name.
_ALIASES: dict[str, str] = {
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "putonghua": "Chinese",
    "中文": "Chinese",
    "汉语": "Chinese",
    "普通话": "Chinese",
    "english": "English",
    "英文": "English",
    "英语": "English",
    "japanese": "Japanese",
    "日语": "Japanese",
    "日本語": "Japanese",
    "korean": "Korean",
    "韩语": "Korean",
    "한국어": "Korean",
    "cantonese": "Cantonese",
    "粤语": "Cantonese",
    "廣東話": "Cantonese",
    "广东话": "Cantonese",
    "french": "French",
    "法语": "French",
    "français": "French",
    "german": "German",
    "德语": "German",
    "deutsch": "German",
    "spanish": "Spanish",
    "西班牙语": "Spanish",
    "español": "Spanish",
}


def canonical_spoken_language(value: str | None) -> str | None:
    """The canonical English name of a spoken language, or None when unknown.

    A known spelling or native name is mapped to its canonical English name.
    Any other ASCII-only name is kept as it is, so a project may still choose a
    language this build does not know.  A name written in a non-Latin script is
    rejected: it cannot be stated inside an English prompt.
    """
    text = (value or "").strip()
    if not text:
        return None
    canonical = _ALIASES.get(text.casefold()) or _ALIASES.get(text)
    if canonical:
        return canonical
    if text.isascii():
        return text
    return None


def spoken_language_is_prompt_safe(value: str | None) -> bool:
    """Whether a language name may be written into the English prompt."""
    text = (value or "").strip()
    return bool(text) and text.isascii()

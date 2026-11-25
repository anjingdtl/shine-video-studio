"""The Qwen-Image 2.1 prompt standard, owned by the material centre.

The material centre renders reference stills on a Qwen-Image-2.1 workflow, so
its prompts have to be written the way that model reads them. The published
guidance is specific and this module is the single place that encodes it:

* natural prose sentences, not tag soup - the model reads the prompt as a
  sentence, so ``appearance: ... must keep ... era: ...`` degrades it;
* a first sentence that anchors what the picture *is* (its medium, subject and
  palette), before any detail;
* a fixed layer order - subject, environment, lighting, material and texture,
  framing, then overall style - because earlier layers weigh more;
* lighting stated as its own sentence, never folded into a label;
* concrete nouns instead of bare colours and bare materials
  (``deep navy`` not ``blue``, ``brushed metal`` not ``metal``);
* no quality slogans (``masterpiece``, ``8k``, ``highly detailed`` and friends
  carry no picture information and the provider guidance rules them out);
* a closing sentence that summarises composition and mood.

Everything here is deterministic: same inputs, byte-identical prompt, because
the prompt hash is part of the attempt's submit intent.
"""

from __future__ import annotations

import re

from shine.materialprep.contracts import MaterialFamily, MaterialVisualDirection

STANDARD_ID = "qwen-image-2.1-prompt-standard"
STANDARD_VERSION = "1.1"

LAYER_ORDER: tuple[str, ...] = (
    "subject", "must_keep", "composition", "medium", "era", "lighting", "materials", "exclusions",
)
"""The layer order the standard prescribes, kept as data so evidence can name it."""

QUALITY_SLOGANS = (
    "masterpiece", "best quality", "highest quality", "ultra detailed", "ultra-detailed",
    "highly detailed", "extremely detailed", "hyper detailed", "8k", "4k", "16k",
    "award winning", "award-winning", "professional photography", "trending on artstation",
    "hdr", "ray tracing", "octane render", "unreal engine",
)
"""Slogans that add no picture information; the provider guidance rules them out."""

_QUALITY_PATTERN = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(item) for item in QUALITY_SLOGANS) + r")\b[,\s]*"
)

_CJK = re.compile(r"[\u4e00-\u9fff]")

NEUTRAL_STUDIO_LIGHTING = "Even soft diffused lighting."


def strip_quality_slogans(text: str) -> str:
    """Remove quality slogans a producer may have copied from a prompt habit."""
    if not text:
        return ""
    cleaned = _QUALITY_PATTERN.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;])", r"\1", cleaned)
    return cleaned.strip(" ,;")


def is_prompt_language(text: str) -> bool:
    """The provider reads English prompt prose; a CJK label is source identity."""
    return bool(text) and not _CJK.search(text)


def environment_sentence(family: MaterialFamily, direction: MaterialVisualDirection | None) -> str:
    """Where the subject sits. A scene carries the world; a studio study does not.

    The confirmed visual direction is a project-wide look, and its ``notes`` are
    written for whatever family the operator had in mind. Attaching the character
    note ("plain grey-white background") to a location plate is how a scene ends
    up with a studio backdrop instead of its own weather and architecture.
    """
    if direction is None:
        return ""
    era = strip_quality_slogans(direction.era).strip(" .")
    notes = strip_quality_slogans(direction.notes) if family == "scene" else ""
    return join_layers([f"The setting belongs to {era}" if era else "", notes])


def lighting_sentence(family: MaterialFamily, direction: MaterialVisualDirection | None, *, styled: bool) -> str:
    """Use the brief and LoRA for light when the request has a style selection."""
    if styled:
        return ""
    lighting = strip_quality_slogans(direction.lighting) if direction else ""
    if not lighting:
        return NEUTRAL_STUDIO_LIGHTING if family in {"character", "prop"} else ""
    return f"The lighting is {lighting[0].lower() + lighting[1:]}."


def material_sentence(direction: MaterialVisualDirection | None) -> str:
    """Surface and texture, named concretely."""
    if direction is None:
        return ""
    texture = strip_quality_slogans(direction.texture)
    palette = strip_quality_slogans(direction.palette)
    parts = []
    if texture:
        parts.append(texture[0].lower() + texture[1:])
    if palette:
        parts.append(f"in a palette of {palette[0].lower() + palette[1:]}")
    if not parts:
        return ""
    return f"Materials and colour read as {' , '.join(parts)}."


def join_layers(parts: list[str]) -> str:
    """Join finished sentences, keeping one space and one full stop between them."""
    cleaned = []
    for part in parts:
        text = (part or "").strip()
        if not text:
            continue
        text = text[0].upper() + text[1:] if text[0].islower() else text
        if not text.endswith((".", "!", "?")):
            text += "."
        if text not in cleaned:
            cleaned.append(text)
    return " ".join(cleaned)


def standard_evidence() -> dict:
    """The pinned identity of this standard, recorded with every readiness check."""
    return {
        "standard_id": STANDARD_ID,
        "standard_version": STANDARD_VERSION,
        "layer_order": list(LAYER_ORDER),
        "quality_slogans": list(QUALITY_SLOGANS),
        "neutral_studio_lighting": NEUTRAL_STUDIO_LIGHTING,
    }

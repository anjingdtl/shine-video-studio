"""One place that decides what a material's image prompt is.

The material centre shows a person what the prompt *will* be before the picture
exists, and the worker later submits exactly that text.  When those two answers
come from two implementations, the person edits one text and the renderer sends
another.  So the preview and the render both call this module.

The rules it applies are the same in both places:

* a frozen, hand-written prompt replaces the composed one verbatim;
* an empty appearance falls back to the requirement's own description, because
  a producer may answer with a question instead of a look and that question
  must not make the item unrenderable;
* the film style of the frozen Style Bible travels with every prompt, so a
  preview and the render cannot differ on the medium alone.
"""

from __future__ import annotations

import hashlib
from typing import Any

from shine.materialprep import image_prompts
from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection

MANUAL_TEMPLATE_VERSION = "manual-frozen-v1"


class PromptSourceError(ValueError):
    """A prompt that cannot be composed, with a stable reason code."""

    def __init__(self, code: str, message: str = "", **details: Any):
        super().__init__(message or code)
        self.code = code
        self.details = details


def frozen_prompt(requirement_payload: dict[str, Any]) -> dict[str, Any] | None:
    """The prompt a person froze for this requirement, when there is one."""
    frozen = (requirement_payload or {}).get("manual_prompt")
    if isinstance(frozen, dict) and str(frozen.get("prompt") or "").strip():
        return frozen
    return None


def compose_requirement_prompt(
    *,
    family: str,
    brief: MaterialImageBrief | None,
    visual_direction: MaterialVisualDirection | None,
    requirement_name: str = "",
    carrier: str | None = None,
    film_style: str = "",
    description: str = "",
    manual: dict[str, Any] | None = None,
) -> image_prompts.ImagePrompt:
    """The prompt pair for one requirement.

    ``manual`` is the frozen text when there is one; otherwise the prompt is
    composed from the producer's brief.
    """
    if manual is not None:
        prompt = str(manual["prompt"]).strip()
        return image_prompts.ImagePrompt(
            prompt=prompt,
            negative_prompt=str(manual.get("negative_prompt") or "").strip(),
            template_version=MANUAL_TEMPLATE_VERSION,
            sha256=str(manual.get("sha256") or hashlib.sha256(
                prompt.encode("utf-8")).hexdigest()),
        )
    if brief is None:
        raise PromptSourceError("MATERIAL_BRIEF_MISSING")
    if not (brief.appearance or "").strip():
        # The producer may answer with a question instead of a look when a fact
        # is missing. That question is worth showing the person, but it must not
        # stop the render: the first confirmed description of the requirement is
        # enough to make one candidate, and a candidate is only a proposal the
        # person still has to promote.
        fallback = str(description or "").strip()
        if not fallback:
            raise PromptSourceError("MATERIAL_BRIEF_EMPTY_APPEARANCE",
                                    questions=list(brief.questions or []))
        brief = brief.model_copy(update={"appearance": fallback})
    if family not in image_prompts.COMPOSITION_RULES:
        raise PromptSourceError("UNKNOWN_MATERIAL_FAMILY", family=str(family))
    try:
        return image_prompts.compose(
            family,
            brief,
            visual_direction=visual_direction or MaterialVisualDirection(),
            requirement_name=requirement_name,
            carrier=carrier,
            film_style=film_style,
        )
    except image_prompts.ImagePromptError as exc:
        raise PromptSourceError(exc.code, **exc.details) from exc


def film_style_for(snapshot: dict[str, Any] | None) -> str:
    """The frozen film medium sentence every material prompt carries."""
    from shine.h3.film_type import film_type_sentence
    from shine.schemas import FilmType

    if not snapshot:
        return ""
    try:
        return film_type_sentence(FilmType(snapshot.get("film_type")))
    except ValueError:
        return ""


__all__ = [
    "MANUAL_TEMPLATE_VERSION",
    "PromptSourceError",
    "compose_requirement_prompt",
    "film_style_for",
    "frozen_prompt",
]

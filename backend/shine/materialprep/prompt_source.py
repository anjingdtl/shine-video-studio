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


def world_note_for_family(
    family: str,
    world: str,
    *,
    world_source: str = "",
    user_text: bool = False,
) -> str:
    """Single verdict on whether a project world note may enter an image prompt.

    An outline synopsis (``outline_summary``) describes characters and their
    actions.  Pasting it into a scene or prop positive prompt made image models
    paint the whole cast into an empty location plate.  Scene and prop prompts
    therefore never receive an outline summary; producer-composed scene/prop
    briefs already translate era and architecture into English ``appearance``
    prose, so only character stills (and user-written stills backed by a
    confirmed global story bible) inherit the raw world note.
    """
    text = str(world or "").strip()
    if not text:
        return ""
    if family == "character":
        return text
    if family in {"scene", "prop"}:
        if world_source == "outline_summary":
            return ""
        if not user_text:
            return ""
    return text


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
    world_note: str = "",
    world_source: str = "",
    style_snapshot: dict[str, Any] | None = None,
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
    resolved_film_style = film_style_for(style_snapshot, family=family) if style_snapshot else film_style
    resolved_world = world_note_for_family(
        family, world_note, world_source=world_source, user_text=False
    )
    try:
        return image_prompts.compose(
            family,
            brief,
            visual_direction=visual_direction or MaterialVisualDirection(),
            requirement_name=requirement_name,
            carrier=carrier,
            film_style=resolved_film_style,
            world_note=resolved_world,
        )
    except image_prompts.ImagePromptError as exc:
        raise PromptSourceError(exc.code, **exc.details) from exc


def film_style_for(snapshot: dict[str, Any] | None, *, family: str | None = None) -> str:
    """The frozen film medium sentence every material prompt carries, scoped by family."""
    from shine.h3.film_type import material_film_type_sentence
    from shine.schemas import FilmType

    if not snapshot:
        return ""
    try:
        return material_film_type_sentence(FilmType(snapshot.get("film_type")), family=family)
    except ValueError:
        return ""


def project_visual_facts(db: Any, project_id: str) -> dict[str, Any]:
    """The single read of the facts that govern every material image.

    The frozen Style Bible supplies the film medium; the confirmed global story
    bible supplies the world / era.  A streamline work has no global bible: its
    confirmed outline synopsis is then the user-confirmed world fact, so a
    period script cannot render as modern people in either path.  Both the
    brief producer and the asset centre compose from this one reader.
    """
    from sqlalchemy import select

    from shine.models import AgentArtifact, StyleVersion

    style = db.scalar(
        select(StyleVersion)
        .where(StyleVersion.project_id == project_id, StyleVersion.frozen_at.is_not(None))
        .order_by(StyleVersion.version.desc())
    )
    snapshot = dict((style.snapshot or {}) if style else {})
    bible = db.scalar(
        select(AgentArtifact)
        .where(AgentArtifact.project_id == project_id,
               AgentArtifact.kind == "FourStageGlobalStoryBible")
        .order_by(AgentArtifact.created_at.desc())
    )
    world_raw = (bible.payload_json or {}).get("world") if bible else None
    if isinstance(world_raw, list):
        world = " ".join(str(item).strip() for item in world_raw if str(item).strip())
    else:
        world = str(world_raw or "").strip()
    world_source = "story_bible" if world else ""
    if not world:
        outline = db.scalar(
            select(AgentArtifact)
            .where(AgentArtifact.project_id == project_id,
                   AgentArtifact.kind == "StoryOutline",
                   AgentArtifact.stale.is_(False),
                   AgentArtifact.approved_at.is_not(None))
            .order_by(AgentArtifact.created_at.desc())
        )
        if outline is not None:
            world = str((outline.payload_json or {}).get("summary") or "").strip()
            if world:
                world_source = "outline_summary"
    return {
        "project_id": project_id,
        "film_style": film_style_for(snapshot),
        "film_type": snapshot.get("film_type"),
        "style_version_id": style.id if style else None,
        "style_version": style.version if style else None,
        "snapshot": snapshot,
        "world": world,
        "world_source": world_source,
    }


def compose_user_image_prompt(
    *,
    user_prompt: str,
    family: str,
    facts: dict[str, Any],
    carrier: str | None = None,
) -> image_prompts.ImagePrompt:
    """Compose an asset-centre prompt from the user's own text plus project facts.

    The user's words are boundary input carried verbatim (any script language);
    the server still applies the film medium, the world / era note, the family
    framing and the medium negatives through the one composer.
    """
    brief = MaterialImageBrief.model_construct(
        appearance=user_prompt, must_keep=[], avoid=[], questions=[])
    snapshot = facts.get("snapshot") if isinstance(facts.get("snapshot"), dict) else None
    resolved_film_style = (
        film_style_for(snapshot, family=family)
        if snapshot
        else str(facts.get("film_style") or "")
    )
    resolved_world = world_note_for_family(
        family,
        str(facts.get("world") or ""),
        world_source=str(facts.get("world_source") or ""),
        user_text=True,
    )
    try:
        return image_prompts.compose(
            family,
            brief,
            visual_direction=None,
            requirement_name="",
            carrier=carrier,
            film_style=resolved_film_style,
            user_text=True,
            world_note=resolved_world,
        )
    except image_prompts.ImagePromptError as exc:
        raise PromptSourceError(exc.code, **exc.details) from exc


__all__ = [
    "MANUAL_TEMPLATE_VERSION",
    "PromptSourceError",
    "compose_requirement_prompt",
    "compose_user_image_prompt",
    "film_style_for",
    "frozen_prompt",
    "project_visual_facts",
    "world_note_for_family",
]

"""The deterministic material-image prompt template.

This module is deliberately **not** part of ``H3PromptCompiler``.  An image
prompt carries no H3 section order, no ``<Subject n>`` labels, no dialogue tags
and no reference slots; the H3 compiler stays the only path that produces a
video prompt, and it keeps checking Style/Rule hashes, per-shot masters, request
-local labels, real reference slots and ``media_transfers``.

What lives here is the fixed composition rule per family, which the product owns
and the model may not change:

* character - single-person half-body study, complete head, clear face, neutral
  grey-white background;
* scene - empty environment plate, no people, honouring the confirmed space,
  era, weather and lighting;
* prop - one complete object, no holder, neutral grey-white background.

The producer supplies appearance text only.  Composition, ordering, negative
prompt assembly and every bound below are server-side and deterministic: the same
inputs always produce byte-identical prompts, which is what makes the prompt hash
part of the attempt's submit intent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from shine.materialprep.contracts import MaterialFamily, MaterialImageBrief, MaterialVisualDirection

PROMPT_TEMPLATE_VERSION = "material-image-v1"
"""Bump when a composition rule changes; readiness evidence is bound to it."""

_CJK = re.compile(r"[\u4e00-\u9fff]")
"""Same deterministic script test the H3 rule pack uses for ``H3_ENGLISH``."""

MAX_COMPOSED_PROMPT_CHARS = 4000
MAX_COMPOSED_NEGATIVE_CHARS = 1200

_NO_TEXT_WATERMARK = "no text, no typography, no letters, no words, no numbers, no captions, no logo, no watermark"
_NO_COLLAGE = "no collage, no split panel, no multiple views, no character sheet, no border, no frame overlay"

COMPOSITION_RULES: dict[MaterialFamily, str] = {
    "character": (
        "Single-person half-body portrait study of exactly one character, complete head fully inside the frame, "
        "face clearly visible and unobstructed, calm neutral expression, shoulders and upper body squared to the "
        "camera, plain neutral grey-white seamless studio background, even soft diffused lighting, sharp focus on "
        "the face, no other people, no cropped head, no back view, no profile-only view, no costume reference sheet"
    ),
    "scene": (
        "Empty environment plate of one location, absolutely no people and no characters present, no silhouettes and "
        "no figures in the background, wide establishing framing that shows the whole space clearly, consistent "
        "perspective and horizon, no held or worn costume on display"
    ),
    "prop": (
        "Single complete object shown as one whole item, entirely inside the frame with its full outline visible, "
        "no person holding it, no person wearing it, no hands, no body parts, plain neutral grey-white seamless "
        "studio background, even soft diffused lighting, product-study framing, no second copy of the object"
    ),
}


class ImagePromptError(ValueError):
    """A composition refusal carrying a stable reason code."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class ImagePrompt:
    """The finished prompt pair plus the identity that makes it auditable."""

    prompt: str
    negative_prompt: str
    template_version: str
    sha256: str
    subject_omitted: str | None = None

    def as_evidence(self) -> dict[str, Any]:
        return {
            "template_version": self.template_version,
            "prompt_sha256": self.sha256,
            "prompt_chars": len(self.prompt),
            "negative_chars": len(self.negative_prompt),
            **({"subject_omitted": self.subject_omitted} if self.subject_omitted else {}),
        }


def _require_prompt_language(text: str, *, field: str) -> str:
    """Refuse CJK instead of translating or silently passing it through.

    The composition rules and the audited template prompt are English, so a
    mixed-script prompt is a contract failure the operator must fix at the
    brief, not something to paper over here.
    """
    if _CJK.search(text):
        raise ImagePromptError(
            "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE",
            field=field,
            remediation=(
                "Write the appearance text in English visual prose; "
                "the Chinese description stays on the requirement."
            ),
        )
    return text.strip()


def _join(parts: list[str]) -> str:
    return ", ".join(part for part in (p.strip().rstrip(",") for p in parts) if part)


def compose(
    family: MaterialFamily,
    brief: MaterialImageBrief,
    *,
    visual_direction: MaterialVisualDirection | None = None,
    requirement_name: str = "",
) -> ImagePrompt:
    """Build the deterministic prompt pair for one confirmed requirement.

    Order is fixed: composition rule, subject, producer appearance, must-keep
    facts, then the confirmed visual direction.  The negative prompt is the
    producer's ``avoid`` list followed by the fixed exclusions, because the
    audited graph exposes a real ``negative_prompt`` input on
    ``TextEncodeQwenImage21``.
    """
    if family not in COMPOSITION_RULES:
        raise ImagePromptError("UNKNOWN_MATERIAL_FAMILY", family=str(family))

    appearance = _require_prompt_language(brief.appearance, field="appearance")
    if not appearance:
        raise ImagePromptError("MATERIAL_BRIEF_EMPTY", field="appearance")
    must_keep = [_require_prompt_language(item, field="must_keep") for item in brief.must_keep]
    avoid = [_require_prompt_language(item, field="avoid") for item in brief.avoid]

    direction = visual_direction or MaterialVisualDirection()
    direction_parts = []
    for label, value in (
        ("era", direction.era),
        ("palette", direction.palette),
        ("lighting", direction.lighting),
        ("texture", direction.texture),
        ("notes", direction.notes),
    ):
        cleaned = _require_prompt_language(value, field=f"visual_direction.{label}") if value else ""
        if cleaned:
            direction_parts.append(f"{label}: {cleaned}")

    subject = ""
    subject_omitted: str | None = None
    if requirement_name:
        if _CJK.search(requirement_name):
            # The script name is a source identity, not prompt prose.  It stays
            # on the requirement the user reads, and the producer's appearance
            # text carries the identity into the picture.  The omission is
            # recorded in the evidence, never applied silently: a prompt that
            # refused a Chinese name would fail every Chinese script.
            subject_omitted = "not_prompt_language"
        else:
            subject = requirement_name.strip()
    positive = _join(
        [
            COMPOSITION_RULES[family],
            f"subject: {subject}" if subject else "",
            f"appearance: {appearance}",
            "; ".join(f"must keep {item}" for item in must_keep),
            "; ".join(direction_parts),
            _NO_TEXT_WATERMARK,
            _NO_COLLAGE,
        ]
    )
    negative = _join([*avoid, _NO_TEXT_WATERMARK, _NO_COLLAGE])

    if len(positive) > MAX_COMPOSED_PROMPT_CHARS:
        # Never truncate: a cut composition rule or must-keep fact would render
        # something the user did not confirm.
        raise ImagePromptError(
            "MATERIAL_PROMPT_TOO_LONG",
            chars=len(positive),
            limit=MAX_COMPOSED_PROMPT_CHARS,
            remediation="Shorten the appearance text or the visual direction, then generate a new brief version.",
        )
    if len(negative) > MAX_COMPOSED_NEGATIVE_CHARS:
        raise ImagePromptError(
            "MATERIAL_NEGATIVE_PROMPT_TOO_LONG",
            chars=len(negative),
            limit=MAX_COMPOSED_NEGATIVE_CHARS,
        )

    digest = hashlib.sha256(
        "\n".join([PROMPT_TEMPLATE_VERSION, family, positive, negative]).encode("utf-8")
    ).hexdigest()
    return ImagePrompt(
        prompt=positive,
        negative_prompt=negative,
        template_version=PROMPT_TEMPLATE_VERSION,
        sha256=digest,
        subject_omitted=subject_omitted,
    )


def template_evidence() -> dict[str, Any]:
    """The pinned template identity recorded in readiness and attempt rows."""
    rules_hash = hashlib.sha256(
        "\n".join(f"{family}={rule}" for family, rule in sorted(COMPOSITION_RULES.items())).encode("utf-8")
    ).hexdigest()
    return {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "composition_rules_sha256": rules_hash,
        "families": sorted(COMPOSITION_RULES),
        "max_prompt_chars": MAX_COMPOSED_PROMPT_CHARS,
        "max_negative_chars": MAX_COMPOSED_NEGATIVE_CHARS,
    }

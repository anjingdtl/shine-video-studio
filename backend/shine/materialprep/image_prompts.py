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
* prop - one complete object, no holder, neutral grey-white background.  A prop
  requirement additionally carries a typed carrier: ``object`` keeps the
  still-life rule, while a ``paper`` or ``screen`` carrier is shown face-on
  with its written/printed/on-screen content legible, because for those the
  content IS the material.

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

from shine.materialprep import qwen_image_standard as standard
from shine.materialprep.contracts import (
    MaterialCarrier,
    MaterialFamily,
    MaterialImageBrief,
    MaterialVisualDirection,
)

PROMPT_TEMPLATE_VERSION = "material-image-v6-qwen21"
"""Bump when a composition rule changes; readiness evidence is bound to it.

V6 places the subject and immutable script traits before composition and
medium. Producer prose carries translated era details. Scene and prop
plates keep their family-scoped medium and no people/hands rules.

v5 carries the frozen film type as a server-owned English sentence (the same
clause the H3 compiler uses): an animation project's reference stills must not
come back photoreal because the brief producer omitted the medium.

v4 states the no-overlay rule as an explicit list - watermark, logo, signature, platform
badge, timestamp, user-interface overlay - because a single "no watermark" phrase left the
generator free to add a service mark, and post-processing the picture afterwards is not a
fix: a marked picture must never be produced, and if one arrives it is refused at QC.

v3 composes the prompt as layered prose under the Qwen-Image 2.1 standard
(``qwen_image_standard``) instead of joining ``appearance:``/``must keep``/``era:``
labels: that model reads the prompt as a sentence, and the label form diluted the
script description and the confirmed film style it was supposed to carry.
"""

_CJK = re.compile(r"[\u4e00-\u9fff]")
"""Same deterministic script test the H3 rule pack uses for ``H3_ENGLISH``."""

MAX_COMPOSED_PROMPT_CHARS = 4000
MAX_COMPOSED_NEGATIVE_CHARS = 1200

COMPOSITION_RULES: dict[MaterialFamily, str] = {
    "character": (
        "One person framed from the waist up with the complete head in frame, a clear unobstructed face "
        "turned towards the camera, a calm expression and a plain neutral grey-white background."
    ),
    "scene": (
        "A wide establishing view of the whole space with a consistent perspective and horizon, "
        "with no people, figures or silhouettes anywhere in it."
    ),
    "prop": (
        "One complete object against a plain neutral grey-white background, with its entire outline in frame, "
        "nothing holding or wearing it and no hands, body parts or second copy."
    ),
}
"""The family's fixed composition, written as the sentence the standard asks for."""

PROP_CARRIER_RULES: dict[MaterialCarrier, str] = {
    "object": COMPOSITION_RULES["prop"],
    "paper": (
        "One flat paper carrier shown complete and face-on, its written or printed content legible as the brief "
        "describes it, against a plain neutral grey-white background with no person, hands or second copy."
    ),
    "screen": (
        "The display is shown complete and face-on, its on-screen content legible as the brief describes it, "
        "against a plain neutral grey-white background with no person, hands or second copy."
    ),
}

_NO_WATERMARK_TERMS = ("watermark, logo, signature, platform badge, timestamp, service mark "
                       "or user-interface overlay")
"""Every mark a generator or a service may stamp onto a picture, named explicitly.

A single "no watermark" phrase is not enough: a generator can honour the letter
of it and still add a badge, and the picture then has to be cropped or retouched
by hand - which is exactly the manual work the product must not depend on.
"""

_NO_TEXT_SENTENCE = (
    "Nothing in the frame is written text, lettering, numbering, a caption, a logo, a watermark, "
    "a signature, a platform badge, a timestamp or any user-interface overlay"
)
_NO_COLLAGE_SENTENCE = "The picture is a single view, never a collage, split panel, character sheet or bordered sheet"
_NO_WATERMARK_SHORT = f"no {_NO_WATERMARK_TERMS}"
_NO_TEXT_SHORT = f"no text, no lettering, no captions, no {_NO_WATERMARK_TERMS}"
_NO_COLLAGE_SHORT = "no collage, no split panel, no character sheet, no border overlay"
_NO_PEOPLE_SHORT = (
    "people, person, human, man, woman, character, human figure, silhouette, crowd, portrait, face, hands"
)
_NO_HOLDER_SHORT = "person, human, hands, fingers, holding, wearing, portrait"

_TEXT_EXCLUSION_SENTENCES = (_NO_TEXT_SENTENCE, _NO_COLLAGE_SENTENCE)
"""Composition rules that must survive even after the studio sentences are joined."""


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
    carrier: MaterialCarrier | None = None,
    film_style: str = "",
    user_text: bool = False,
    world_note: str = "",
    styled: bool = False,
) -> ImagePrompt:
    """Build the deterministic prompt pair for one confirmed requirement.

    Order is fixed: subject, immutable facts, composition, medium and era,
    visual treatment, then exclusions. The negative prompt is the
    producer's ``avoid`` list followed by the fixed exclusions, because the
    audited graph exposes a real ``negative_prompt`` input on
    ``TextEncodeQwenImage21``.

    A prop requirement may declare a typed carrier.  ``object`` keeps the
    still-life rule and the blanket text ban; a ``paper`` or ``screen`` carrier
    is shown face-on with its content legible, so the blanket text ban is
    replaced by a watermark-only ban — the written or on-screen content IS the
    material the script asks for.

    ``user_text`` marks the appearance as the user's own boundary input (asset
    centre): it is carried verbatim in the script language instead of being
    refused for CJK, while the server layers (film medium, world note, framing,
    negatives) still apply. Producer prose carries translated story facts.
    """
    if family not in COMPOSITION_RULES:
        raise ImagePromptError("UNKNOWN_MATERIAL_FAMILY", family=str(family))

    framing = COMPOSITION_RULES[family]
    text_sentences = list(_TEXT_EXCLUSION_SENTENCES)
    if family == "prop" and carrier in ("paper", "screen"):
        framing = PROP_CARRIER_RULES[carrier]
        text_sentences = [f"No {_NO_WATERMARK_TERMS}", _NO_COLLAGE_SENTENCE]

    # Producer-authored prose is a server contract and must be English; a
    # user's own prompt (asset centre) is boundary input in the script language
    # and is carried verbatim, with the server layers still applied around it.
    appearance = standard.strip_quality_slogans(
        brief.appearance if user_text else _require_prompt_language(brief.appearance, field="appearance"))
    if not appearance:
        raise ImagePromptError("MATERIAL_BRIEF_EMPTY", field="appearance")
    must_keep = [standard.strip_quality_slogans(
        item if user_text else _require_prompt_language(item, field="must_keep"))
        for item in brief.must_keep]
    avoid = [standard.strip_quality_slogans(
        item if user_text else _require_prompt_language(item, field="avoid"))
        for item in brief.avoid]

    direction = visual_direction or MaterialVisualDirection()
    for label, value in (
        ("era", direction.era),
        ("palette", direction.palette),
        ("lighting", direction.lighting),
        ("texture", direction.texture),
        ("notes", direction.notes),
    ):
        if value:
            _require_prompt_language(value, field=f"visual_direction.{label}")

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

    # Keep every distinct hard trait next to the appearance.
    lowered = appearance.casefold()
    extra: dict[str, str] = {}
    for item in must_keep:
        key = item.rstrip(".").casefold()
        if key and not re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", lowered):
            extra.setdefault(key, item.rstrip("."))
    extra_sentence = "Fixed identity and design details include " + ", ".join(extra.values()) if extra else ""

    # Enforce family-safe film medium prose even if a caller passes the video
    # shot sentence ("real human performers...") for an empty scene or prop.
    effective_film_style = film_style.strip()
    if family in ("scene", "prop") and effective_film_style:
        from shine.h3.film_type import material_film_type_sentence
        from shine.schemas import FilmType

        lowered_style = effective_film_style.casefold()
        if "real human performers" in lowered_style or "live-action footage" in lowered_style:
            effective_film_style = material_film_type_sentence(FilmType.LIVE_ACTION, family)
        elif "2d cel-shaded animation" in lowered_style and "photorealistic skin" in lowered_style:
            effective_film_style = material_film_type_sentence(FilmType.ANIMATION, family)

    # Producer-composed scene/prop stills already translate era and architecture
    # into English ``appearance`` prose; injecting a raw CJK plot summary here
    # caused image models to paint characters and plot actions into empty sets.
    effective_world_note = world_note.strip()
    if not user_text and not standard.is_prompt_language(effective_world_note):
        effective_world_note = ""

    layers = [
        f"{subject + ', ' if subject else ''}{appearance}",
        extra_sentence,
        framing,
        effective_film_style,
        effective_world_note,
        standard.environment_sentence(family, direction),
        standard.lighting_sentence(family, direction, styled=styled),
        standard.material_sentence(direction),
        *text_sentences,
    ]
    positive = standard.join_layers(layers)
    # A paper or screen carrier IS its legible content, so the text ban must not
    # reach its negative prompt; only the watermark and collage bans apply.
    negative_exclusions = [_NO_WATERMARK_SHORT, _NO_COLLAGE_SHORT]
    if not (family == "prop" and carrier in ("paper", "screen")):
        negative_exclusions.insert(0, _NO_TEXT_SHORT)
    if family == "scene":
        negative_exclusions.append(_NO_PEOPLE_SHORT)
    elif family == "prop":
        negative_exclusions.append(_NO_HOLDER_SHORT)
    if "cel-shaded" in effective_film_style:
        negative_exclusions.append("photorealistic, live-action footage, 3D CGI rendering")
    elif "live-action" in effective_film_style:
        negative_exclusions.append("illustration, cel shading, cartoon, anime rendering")
    negative = _join([*avoid, *negative_exclusions])

    if len(positive) > MAX_COMPOSED_PROMPT_CHARS and not user_text:
        # Never truncate: a cut composition rule or must-keep fact would render
        # something the user did not confirm.  A user's own prompt is carried
        # verbatim (the asset centre never imposed this cap), so it is exempt.
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
        "\n".join([PROMPT_TEMPLATE_VERSION, family, carrier or "", positive, negative]).encode("utf-8")
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
    carrier_hash = hashlib.sha256(
        "\n".join(f"{carrier}={rule}" for carrier, rule in sorted(PROP_CARRIER_RULES.items())).encode("utf-8")
    ).hexdigest()
    return {
        "template_version": PROMPT_TEMPLATE_VERSION,
        "composition_rules_sha256": rules_hash,
        "prop_carrier_rules_sha256": carrier_hash,
        "families": sorted(COMPOSITION_RULES),
        "prop_carriers": sorted(PROP_CARRIER_RULES),
        "max_prompt_chars": MAX_COMPOSED_PROMPT_CHARS,
        "max_negative_chars": MAX_COMPOSED_NEGATIVE_CHARS,
        # The provider's prompt standard travels with the template identity, so
        # readiness evidence names both the rules and how they are written.
        "prompt_standard": standard.standard_evidence(),
    }

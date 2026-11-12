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

PROMPT_TEMPLATE_VERSION = "material-image-v5-qwen21"
"""Bump when a composition rule changes; readiness evidence is bound to it.

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

FRAMING_SENTENCES: dict[MaterialFamily, str] = {
    "character": (
        "The subject is framed from the waist up, the whole head inside the frame, the face unobstructed and "
        "turned towards the camera with a calm neutral expression, and no other people appear."
    ),
    "scene": (
        "The framing is a wide establishing view of the whole space with a consistent perspective and horizon, "
        "and no people, figures or silhouettes appear anywhere in it."
    ),
    "prop": (
        "The object is shown whole, its entire outline inside the frame, with nothing holding or wearing it and "
        "no second copy of it in the picture."
    ),
}
"""The family's fixed composition, written as the sentence the standard asks for."""

CARRIER_FRAMING: dict[MaterialCarrier, str] = {
    "paper": (
        "The carrier is shown complete and face-on, its written or printed content legible as the brief "
        "describes it, with no person, hands or body parts in the frame and no second copy."
    ),
    "screen": (
        "The display is shown complete and face-on, its on-screen content legible as the brief describes it, "
        "with no person, hands or body parts in the frame and no second copy."
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

_TEXT_EXCLUSION_SENTENCES = (_NO_TEXT_SENTENCE, _NO_COLLAGE_SENTENCE)
"""Composition rules that must survive even after the studio sentences are joined."""

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

PROP_CARRIER_RULES: dict[MaterialCarrier, str] = {
    "object": COMPOSITION_RULES["prop"],
    "paper": (
        "Single flat paper content carrier (a letter, a notice, a sign, a plaque, a map or a book page) shown "
        "complete and face-on, entirely inside the frame, its written or printed content visible and legible as "
        "the brief describes it, text on the carrier is legitimate content, no person, no hands, no body parts, "
        "plain neutral grey-white seamless studio background, even soft diffused lighting, no second copy"
    ),
    "screen": (
        "Single screen display carrier (a monitor, a phone, a television or a projection surface) shown complete "
        "and face-on, entirely inside the frame, its on-screen content visible and legible as the brief describes "
        "it, on-screen text and imagery is legitimate content, no person, no hands, no body parts, plain neutral "
        "grey-white seamless studio background, even soft diffused lighting, no second copy"
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
    carrier: MaterialCarrier | None = None,
    film_style: str = "",
) -> ImagePrompt:
    """Build the deterministic prompt pair for one confirmed requirement.

    Order is fixed: composition rule, subject, producer appearance, must-keep
    facts, then the confirmed visual direction.  The negative prompt is the
    producer's ``avoid`` list followed by the fixed exclusions, because the
    audited graph exposes a real ``negative_prompt`` input on
    ``TextEncodeQwenImage21``.

    A prop requirement may declare a typed carrier.  ``object`` keeps the
    still-life rule and the blanket text ban; a ``paper`` or ``screen`` carrier
    is shown face-on with its content legible, so the blanket text ban is
    replaced by a watermark-only ban — the written or on-screen content IS the
    material the script asks for.
    """
    if family not in COMPOSITION_RULES:
        raise ImagePromptError("UNKNOWN_MATERIAL_FAMILY", family=str(family))

    framing = FRAMING_SENTENCES[family]
    text_sentences = list(_TEXT_EXCLUSION_SENTENCES)
    if family == "prop" and carrier in CARRIER_FRAMING:
        framing = CARRIER_FRAMING[carrier]
        if carrier in ("paper", "screen"):
            # For these carriers the written or on-screen content IS the
            # material, so only the watermark and collage bans still apply.
            text_sentences = [_NO_COLLAGE_SENTENCE]

    appearance = standard.strip_quality_slogans(
        _require_prompt_language(brief.appearance, field="appearance"))
    if not appearance:
        raise ImagePromptError("MATERIAL_BRIEF_EMPTY", field="appearance")
    must_keep = [standard.strip_quality_slogans(_require_prompt_language(item, field="must_keep"))
                 for item in brief.must_keep]
    avoid = [standard.strip_quality_slogans(_require_prompt_language(item, field="avoid"))
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

    # The standard's order: anchor, subject, environment, lighting, material,
    # framing, then a closing line.  A must-keep fact already carried by the
    # appearance prose is not repeated - repetition pushed the script
    # description out of the model's attention instead of reinforcing it.
    lowered = appearance.casefold()
    extra = [item for item in must_keep if item and item.casefold()[:40] not in lowered]
    if extra:
        extra_sentence = "Fixed details that must not drift: " + "; ".join(extra)
    else:
        extra_sentence = ""

    layers = [
        standard.anchor_sentence(family, carrier, direction),
        film_style.strip(),
        f"{subject + ', ' if subject else ''}{appearance}",
        standard.environment_sentence(family, direction),
        standard.lighting_sentence(direction),
        standard.material_sentence(direction),
        framing,
        extra_sentence,
        *text_sentences,
        standard.closing_sentence(family),
    ]
    positive = standard.join_layers(layers)
    # A paper or screen carrier IS its legible content, so the text ban must not
    # reach its negative prompt; only the watermark and collage bans apply.
    negative_exclusions = [_NO_WATERMARK_SHORT, _NO_COLLAGE_SHORT]
    if not (family == "prop" and carrier in ("paper", "screen")):
        negative_exclusions.insert(0, _NO_TEXT_SHORT)
    if "cel-shaded" in film_style:
        negative_exclusions.append("photorealistic, live-action footage, 3D CGI rendering")
    elif "live-action" in film_style:
        negative_exclusions.append("illustration, cel shading, cartoon, anime rendering")
    negative = _join([*avoid, *negative_exclusions])

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

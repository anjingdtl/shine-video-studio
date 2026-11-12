"""The no-overlay rule, from the prompt that forbids the mark to the QC that refuses it.

A picture carrying a watermark is not a small blemish to be cropped away later:
it is a reference master that would put the mark into every shot it influences.
The product therefore states the rule twice - in the prompt that generated the
picture, and in the check that decides whether it may become a master.
"""

from __future__ import annotations

from shine.comfyops.assets import MEDIA_OVERLAY_CODES, overlay_findings
from shine.comfyops.candidate_quality import CandidateFinding
from shine.materialprep import image_prompts
from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection

BRIEF = MaterialImageBrief(
    appearance="A weathered fisherman in a dark blue cotton jacket, short grey hair, calm expression",
    must_keep=["a thin scar across the left hand"],
    avoid=["modern clothing"],
)


def _compose(family: str = "character"):
    return image_prompts.compose(
        family,
        BRIEF,
        visual_direction=MaterialVisualDirection(era="1990s Chinese fishing village"),
    )


def test_every_mark_a_service_may_stamp_is_named_in_the_prompt():
    """One "no watermark" phrase is what let a service badge through.

    The prompt has to name the whole family of overlays, because a generator can
    honour the letter of a single ban and still add a logo or a timestamp.
    """
    composed = _compose()
    lowered = composed.prompt.casefold()
    for term in ("watermark", "logo", "signature", "badge", "timestamp", "overlay"):
        assert term in lowered, term
    assert "watermark" in composed.negative_prompt.casefold()
    assert "logo" in composed.negative_prompt.casefold()


def test_the_negative_prompt_also_forbids_the_marks():
    """A negative prompt is what most local graphs actually read."""
    negative = _compose().negative_prompt.casefold()
    for term in ("watermark", "logo", "signature", "timestamp", "overlay"):
        assert term in negative, term


def test_a_watermark_finding_disqualifies_a_picture():
    """QC refuses the marked picture instead of asking a person to crop it."""
    findings = [
        CandidateFinding(code="WATERMARK_PRESENT", result="FAIL", confidence=0.97,
                         evidence="a translucent service mark sits in the bottom-right corner"),
        CandidateFinding(code="IDENTITY", result="PASS", confidence=0.95,
                         evidence="the face matches the brief"),
    ]
    assert overlay_findings(findings) == ["WATERMARK_PRESENT"]


def test_a_passing_text_finding_is_not_a_refusal():
    """A paper prop legitimately carries text; only a FAIL refuses it."""
    findings = [CandidateFinding(code="TEXT_IN_FRAME", result="PASS", confidence=0.9,
                                 evidence="the letter's content is the material")]
    assert overlay_findings(findings) == []


def test_the_overlay_code_set_covers_the_prompt_terms():
    """The two halves of the rule name the same defects."""
    assert {"WATERMARK_PRESENT", "LOGO_PRESENT", "SIGNATURE_PRESENT"} <= MEDIA_OVERLAY_CODES
    assert {"UI_OVERLAY_PRESENT", "TIMESTAMP_PRESENT"} <= MEDIA_OVERLAY_CODES


def test_the_template_version_moved_with_the_rule():
    """Readiness evidence is bound to the template version, so it must change."""
    assert image_prompts.PROMPT_TEMPLATE_VERSION == "material-image-v5-qwen21"


def test_the_frozen_film_type_travels_into_the_image_prompt():
    """An animation film's reference stills must not come back photoreal."""
    from shine.h3.film_type import film_type_sentence
    from shine.materialprep.contracts import MaterialImageBrief
    from shine.schemas import FilmType

    brief = MaterialImageBrief(appearance="A young woman with long black hair.",
                               must_keep=[], avoid=[], questions=[])
    animated = image_prompts.compose("character", brief,
                                     film_style=film_type_sentence(FilmType.ANIMATION))
    assert "2D cel-shaded animation" in animated.prompt
    assert "photorealistic" in animated.negative_prompt
    live = image_prompts.compose("character", brief,
                                 film_style=film_type_sentence(FilmType.LIVE_ACTION))
    assert "photorealistic live-action" in live.prompt
    assert "cel shading" in live.negative_prompt
    neutral = image_prompts.compose("character", brief)
    assert "cel-shaded" not in neutral.prompt

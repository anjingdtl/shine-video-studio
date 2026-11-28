from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from shine.asset_identity import character_reference_matches
from shine.foundation.h3_audio import sanitize_h3_dialogue_text, sanitize_non_vocal_style_text
from shine.h3.film_type import film_type_sentence
from shine.h3.narrator import is_narrator_speaker, narrator_sentence
from shine.h3.spoken_language import (
    DEFAULT_SPOKEN_LANGUAGE,
    canonical_spoken_language,
)
from shine.schemas import (
    CompiledPrompt,
    ContinuityMode,
    DialogueLine,
    Facing,
    FrameRole,
    FrameZone,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)

# Pinned official skill material from MiniMax-AI/MiniMax-H3 commit
# d21241f0a4b3acbb34c97dae47fa417b7065e438 (skills/h3-prompt-writing).
LEGACY_AUDIO_COMPILER_VERSION = "shine-h3-compiler/1.2.1"
LEGACY_STYLE_COMPILER_VERSION = "shine-h3-compiler/1.2.2"
LEGACY_SOUND_COMPILER_VERSION = "shine-h3-compiler/1.2.3"
# 1.3.0 rewrites the audio contracts as positive sound descriptions.  The older
# fixed sentences enumerated forbidden vocal acts ("narration, voice-over,
# whispering, mumbling, singing, humming..."); a video model reads those as
# concepts to render, not as prohibitions, and the enumeration primed exactly
# the unscripted foreign-language narration it tried to forbid.  The new
# sentences state what the soundtrack contains and anchor every voice to the
# project language instead.  Historical versions stay byte-identical so stored
# requests remain verifiable.
#
# Version-drift reconciliation (2026-11-21, plan R6b): the silent-shot ambience
# sanitizer, the silent language-world anchor and the subject appearance wording
# were added to 1.3.0 *without* a version bump, so the ledger holds two different
# byte variants both pinned as ``1.3.0``.  ``1.3.0`` keeps the current (anchored)
# bytes; ``1.3.0-original`` is a replay-only identity that reproduces the exact
# pre-anchoring bytes.  The single provenance verifier falls back to it for stored
# ``1.3.0`` requests whose bytes predate the anchor.  Neither historical variant is
# ever rewritten.  New prompt behaviour never lands in a released version
# again: it goes into the next one, so a stored request always replays.
LEGACY_ANCHORED_COMPILER_VERSION = "shine-h3-compiler/1.3.0"
LEGACY_PRE_ANCHOR_COMPILER_VERSION = "shine-h3-compiler/1.3.0-original"
# 1.4.0 is the native in-shot timeline: each internal cut becomes its own
# "[Shot N] At MM:SS.mmm" block, the summary names the real use of every
# reference, and the camera is written as prose.
LEGACY_TIMELINE_COMPILER_VERSION = "shine-h3-compiler/1.4.0"
# The same drift happened again under 1.4.0: commit 40042d2 (2026-11-22) added
# the dialogue sanitizer, the silent-shot style sanitizer and the silent-shot
# environment sanitizer to 1.4.0 *without* a version bump.  Requests frozen
# before it (16 real 1.4.0 requests in the ledger, 6 of them touched by the
# sanitizers) no longer replayed.  As with 1.3.0, ``1.4.0`` keeps the current
# (sanitizing) bytes and ``1.4.0-original`` is a replay-only identity that
# reproduces the pre-sanitizing bytes; neither is ever rewritten.
LEGACY_PRE_SANITIZE_COMPILER_VERSION = "shine-h3-compiler/1.4.0-original"
# 2.0.0 is the thin shell over a rich timeline: the reference sections bind
# each label in one sentence, the frozen-style header and the other meta
# instructions are gone, the second-by-second performance is prose, and every
# spoken line sits inside the [Shot N] block that performs it.
LEGACY_THIN_SHELL_COMPILER_VERSION = "shine-h3-compiler/2.0.0"
# 2.1.0 anchors every character reference token (``{{Ck}}``) inside shot prose
# directly to its request-local ``<Subject n>`` label, strengthens explicit
# outfit/hairstyle preservation, and distinguishes scene, prop and continuity
# ``<Picture k>`` roles so H3 never confuses an environment plate with a prop or
# preceding final frame.
LEGACY_ANCHORED_TOKENS_COMPILER_VERSION = "shine-h3-compiler/2.1.0"
# 2.2.0 scopes the project-wide locked story facts to what this shot can show.
# 2.1.0 emitted every one of them into every shot, so a solo shot of one
# character also carried the other character's appearance bio, the props the
# shot never uses and the plan's own structure note ("two scenes, split into 8
# shots"), and H3 rendered that off-screen cast into frame.  The scoping lands
# here, in the next version: 2.1.0 keeps its released bytes, so every stored
# 2.1.0 request still replays through the single provenance verifier.
LEGACY_SCOPED_FACTS_COMPILER_VERSION = "shine-h3-compiler/2.2.0"
# 2.3.0 puts soft last_frame opening continuity at the top of [Shot 1] (anchoring
# 0.0s posture and held props before the new beat so actions do not repeat) and
# tightens per-shot story-fact scoping for appearance-only off-cast bios and
# English ``and``-joined prop inventories.  All pre-2.3.0 versions keep their
# exact released bytes.
LEGACY_HANDOFF_V23_COMPILER_VERSION = "shine-h3-compiler/2.3.0"
# 2.4.0 aligns last_frame shots with physical MiniMaxH3AddGuide(frame_idx=0):
# downgrades the empty scene plate to architectural/spatial geometry only so it
# never overrides the preceding final frame's lighting, exposure, or color
# temperature, and instructs strict forward motion without rewinding.  CUT shots
# remain byte-identical to 2.3.0, and <=2.3.0 historical requests keep their bytes.
LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION = "shine-h3-compiler/2.4.0"
# 2.5.0 introduces acting-driven compilation (Plan 20261127): fronts static set
# environment (Space/Light/Air) and scoped story facts before performance,
# interleaves timeline windows with timed dialogue in chronological order inside
# each [Shot N] block so pre-speech micro-expression ramps sit immediately before
# their spoken line, and renders controlled 4D vocal delivery cues outside <d>.
# All <=2.4.0 historical versions stay byte-identical for exact provenance replay.
LEGACY_ACTING_V25_COMPILER_VERSION = "shine-h3-compiler/2.5.0"
# 2.6.0 fixes three real-render continuity/repetition defects found in 2.5.0
# sample verification while keeping <=2.5.0 byte-identical for provenance replay:
# (1) resolves {{Ck}} tokens inside blocking.placements[].posture/props so raw
#     tokens never leak into "In frame:",
# (2) avoids duplicating the full-shot story_beat spoiler before a populated
#     second-by-second timeline (preventing H3 from executing one-shot prop
#     actions twice, e.g. placing a second key over the first), and
# (3) reinforces spatial landmark-side and continuous visible-motion anchoring
#     on last_frame joins so performers and hidden props never teleport.
COMPILER_VERSION = "shine-h3-compiler/2.6.0"
KNOWN_COMPILER_VERSIONS = (
    LEGACY_AUDIO_COMPILER_VERSION,
    LEGACY_STYLE_COMPILER_VERSION,
    LEGACY_SOUND_COMPILER_VERSION,
    LEGACY_ANCHORED_COMPILER_VERSION,
    LEGACY_PRE_ANCHOR_COMPILER_VERSION,
    LEGACY_TIMELINE_COMPILER_VERSION,
    LEGACY_PRE_SANITIZE_COMPILER_VERSION,
    LEGACY_THIN_SHELL_COMPILER_VERSION,
    LEGACY_ANCHORED_TOKENS_COMPILER_VERSION,
    LEGACY_SCOPED_FACTS_COMPILER_VERSION,
    LEGACY_HANDOFF_V23_COMPILER_VERSION,
    LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION,
    LEGACY_ACTING_V25_COMPILER_VERSION,
    COMPILER_VERSION,
)
#: Versions that keep the pre-1.4.0 summary/retention/camera/shot-text behaviour.
LEGACY_SEMANTIC_VERSIONS = (
    LEGACY_AUDIO_COMPILER_VERSION,
    LEGACY_STYLE_COMPILER_VERSION,
    LEGACY_SOUND_COMPILER_VERSION,
    LEGACY_ANCHORED_COMPILER_VERSION,
    LEGACY_PRE_ANCHOR_COMPILER_VERSION,
)
#: Versions that keep the pre-2.0.0 shell (style-bible header, legal-form
#: reference sections, all spoken lines inside the [Shot 1] block).
LEGACY_SHELL_VERSIONS = (
    *LEGACY_SEMANTIC_VERSIONS,
    LEGACY_TIMELINE_COMPILER_VERSION,
    LEGACY_PRE_SANITIZE_COMPILER_VERSION,
)
#: Versions that keep the pre-2.1.0 prose/picture-role behaviour.
LEGACY_200_VERSIONS = (
    *LEGACY_SHELL_VERSIONS,
    LEGACY_THIN_SHELL_COMPILER_VERSION,
)
#: Versions that keep the pre-2.2.0 behaviour of emitting every locked story fact
#: into every shot.
LEGACY_210_VERSIONS = (
    *LEGACY_200_VERSIONS,
    LEGACY_ANCHORED_TOKENS_COMPILER_VERSION,
)
#: Versions that keep the 2.2.0 and earlier continuity-footer and scoping bytes.
LEGACY_220_VERSIONS = (
    *LEGACY_210_VERSIONS,
    LEGACY_SCOPED_FACTS_COMPILER_VERSION,
)
#: Versions that keep the 2.3.0 soft scene-lighting & continuity wording.
LEGACY_230_VERSIONS = (
    *LEGACY_220_VERSIONS,
    LEGACY_HANDOFF_V23_COMPILER_VERSION,
)
#: Versions that keep the 2.4.0 layout (timeline before environment before dialogue,
#: no 4D vocal delivery phrase).
LEGACY_240_VERSIONS = (
    *LEGACY_230_VERSIONS,
    LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION,
)
#: Versions that keep the 2.5.0 behaviour (pre-timeline story_beat spoiler,
#: unresolved blocking posture tokens, pre-2.6.0 last_frame wording).
LEGACY_250_VERSIONS = (
    *LEGACY_240_VERSIONS,
    LEGACY_ACTING_V25_COMPILER_VERSION,
)
RULE_VERSION = "h3-prompt-writing@d21241f0a4b3"
SKILL_SHA256 = "a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0"
BASE_RULE_SHA256 = "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc"
REF_RULE_SHA256 = "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7"

MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_TOTAL = 12
MAX_MEDIA_DURATION_S = 15.0

BASE_SECTION_ORDER = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_SECTION_ORDER = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
LABEL_PATTERN = re.compile(r"<(Subject|Picture|Video|Audio) (\d+)>")
CHARACTER_TOKEN_PATTERN = re.compile(r"\{\{C\d+\}\}")


class PromptCompilationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass
class AlignInstruction:
    mode: GenerationMode
    duration_s: float
    last_shot: int = 1


@dataclass
class _Context:
    spec: ShotSpec
    style: StyleBible
    manifest: list[ReferenceManifestItem]
    label_to_asset: dict[str, str]
    asset_roles: dict[str, str]
    speaker_labels: dict[str, str]
    speaker_order: list[str]
    project_language: str = "Chinese"
    legacy_audio: bool = False
    legacy_style: bool = False
    legacy_sound: bool = False
    #: Replay-only: the pre-anchoring 1.3.0 bytes (no silent ambience sanitizer,
    #: no silent language-world anchor, original subject appearance wording).
    pre_anchor: bool = False
    #: Keeps the pre-1.4.0 summary/retention/camera/shot-text behaviour.
    legacy_semantics: bool = False
    #: Replay-only: the pre-sanitizing 1.4.0 bytes (dialogue text, the silent
    #: shot's style dimensions and its environment are emitted unsanitized).
    pre_sanitize: bool = False
    #: 2.0.0+: thin reference shell, performance timeline, dialogue inside the
    #: [Shot N] block that speaks it.
    thin_shell: bool = False
    #: 2.1.0+: resolve ``{{Ck}}`` tokens into ``<Subject n>``, state explicit
    #: outfit/hairstyle retention, and distinguish scene/prop/continuity pictures.
    anchored_v21: bool = False
    #: 2.2.0+: locked story facts are scoped to the shot's visible cast and props.
    scoped_facts: bool = False
    #: 2.3.0+: opening last_frame continuity sits at top of [Shot 1] and fact scoping
    #: catches appearance-only off-cast bios and English ``and``-joined props.
    handoff_v23: bool = False
    #: 2.4.0+: on last_frame shots, downgrade scene plate to architecture-only
    #: and anchor color/exposure/forward-time progression on continuity_derivative.
    continuity_color_v24: bool = False
    #: 2.5.0+: static environment fronted, timeline/dialogue interleaved chronologically,
    #: and controlled 4D vocal delivery phrases rendered before <d> tags.
    acting_v25: bool = False
    #: 2.6.0+: resolve {{Ck}} tokens inside blocking posture/props, omit redundant
    #: pre-timeline story_beat spoiler when timeline is populated, and anchor
    #: landmark-side & visible prop motion on last_frame joins.
    continuity_clean_v26: bool = False
    warnings: list[str] = field(default_factory=list)


def _language_tag(text: str, project_language: str) -> str:
    if project_language:
        return project_language
    if re.search(r"[\u4e00-\u9fff]", text):
        return "Chinese"
    return "English"


def _clause(text: str) -> str:
    """One prose clause as a sentence: no doubled full stop when the value has one."""
    stripped = str(text or "").strip().rstrip(".")
    return stripped + "." if stripped else ""


def _style_sentences(
    style: StyleBible,
    style_version_id: str,
    include_sound: bool = True,
    *,
    legacy_sound: bool = False,
    legacy_semantics: bool = False,
    pre_sanitize: bool = False,
    thin_shell: bool = False,
) -> str:
    if include_sound:
        sound_clause = f"Performance and sound: {style.sound_performance}."
    elif legacy_sound:
        sound_clause = "Performance and sound: no spoken dialogue in this shot."
    else:
        sound_clause = (
            "Performance and sound: this shot is performed without dialogue; "
            "its soundtrack is entirely non-vocal."
        )
    unsanitized_style = include_sound or legacy_semantics or pre_sanitize
    blocking = (
        style.shot_blocking
        if unsanitized_style
        else (sanitize_non_vocal_style_text(style.shot_blocking) or style.shot_blocking)
    )
    rhythm = (
        style.editing_rhythm
        if unsanitized_style
        else (sanitize_non_vocal_style_text(style.editing_rhythm) or style.editing_rhythm)
    )
    film_clause = film_type_sentence(style.film_type)
    film_prefix = f"{film_clause} " if film_clause else ""
    if thin_shell:
        # The seven dimensions are the creative content and stay.  The header
        # that named the Style Bible version is a meta instruction: the request
        # already pins ``style_version_id`` and the shot already carries it, so
        # the UUID only spent the text encoder's attention.
        if include_sound:
            sound_clause = f"Performance and sound: {_clause(style.sound_performance)}"
        return (
            f"{film_prefix}Photography language: {_clause(style.photography_language)} "
            f"Blocking: {_clause(blocking)} Color palette: {_clause(style.color_palette)} "
            f"Lighting and tone: {_clause(style.lighting_tone)} Lens and texture: {_clause(style.lens_texture)} "
            f"Editing rhythm: {_clause(rhythm)} {sound_clause}"
        )
    return (
        "Visual style constraints from the frozen Style Bible "
        f"(version {style_version_id}). {film_prefix}Photography language: {style.photography_language}. "
        f"Blocking: {blocking}. Color palette: {style.color_palette}. "
        f"Lighting and tone: {style.lighting_tone}. Lens and texture: {style.lens_texture}. "
        f"Editing rhythm: {rhythm}. {sound_clause}"
    )


def _prompt_style(spec: ShotSpec, style: StyleBible, *, legacy_style: bool = False) -> StyleBible:
    # The translated draft is part of the shot revision; the original frozen
    # style and its hash remain authoritative and unchanged.  The film type is
    # a project-wide contract, so a per-shot English style that predates it
    # still inherits the frozen selection.
    prompt_style = spec.prompt_style
    if prompt_style is None:
        return style
    if not legacy_style and prompt_style.film_type != style.film_type:
        return prompt_style.model_copy(update={"film_type": style.film_type})
    if legacy_style and prompt_style.film_type is None and style.film_type is not None:
        return prompt_style.model_copy(update={"film_type": style.film_type})
    return prompt_style


def _format_timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def validate_reference_manifest(manifest: list[ReferenceManifestItem]) -> None:
    """Public entry to the request-local reference limits.

    A hand-written prompt skips compilation but not the transport contract: the
    renderer still has hard slot caps, and exceeding them must be named before a
    job exists rather than failing inside the submitted graph.
    """
    _validate_manifest(manifest)


def _count_references(manifest: list[ReferenceManifestItem]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "audio": 0}
    for item in manifest:
        if item.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}:
            counts["image"] += 1
        elif item.use_as == ReferenceUse.VIDEO:
            counts["video"] += 1
        else:
            counts["audio"] += 1
    return counts


def _validate_manifest(manifest: list[ReferenceManifestItem]) -> None:
    if len(manifest) > MAX_TOTAL:
        raise PromptCompilationError("H3_REFERENCE_LIMIT", "Ref2VA accepts at most 12 reference files")
    labels = [item.label for item in manifest]
    if len(set(labels)) != len(labels):
        raise PromptCompilationError("H3_DUPLICATE_LABEL", "Each reference label must be unique within a request")
    counts = _count_references(manifest)
    if counts["image"] > MAX_IMAGES:
        raise PromptCompilationError("H3_REFERENCE_LIMIT", "Ref2VA accepts at most 9 image references")
    if counts["video"] > MAX_VIDEOS:
        raise PromptCompilationError("H3_REFERENCE_LIMIT", "Ref2VA accepts at most 3 video references")
    if counts["audio"] > MAX_AUDIOS:
        raise PromptCompilationError("H3_REFERENCE_LIMIT", "Ref2VA accepts at most 3 audio references")
    totals = {"video": 0.0, "audio": 0.0}
    for item in manifest:
        expected_family = {
            ReferenceUse.SUBJECT: "Subject",
            ReferenceUse.PICTURE: "Picture",
            ReferenceUse.VIDEO: "Video",
            ReferenceUse.AUDIO: "Audio",
        }[item.use_as]
        if not item.label.startswith(f"<{expected_family} "):
            raise PromptCompilationError(
                "H3_LABEL_MISMATCH",
                f"Reference label {item.label} does not match its declared use_as={item.use_as.value}",
            )
        if item.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE} and not item.content_type.startswith("image/"):
            raise PromptCompilationError("H3_REFERENCE_TYPE", f"{item.label} must be an image")
        if item.use_as == ReferenceUse.VIDEO and not item.content_type.startswith("video/"):
            raise PromptCompilationError("H3_REFERENCE_TYPE", f"{item.label} must be a video")
        if item.use_as == ReferenceUse.AUDIO and not item.content_type.startswith("audio/"):
            raise PromptCompilationError("H3_REFERENCE_TYPE", f"{item.label} must be audio")
        if item.use_as in {ReferenceUse.VIDEO, ReferenceUse.AUDIO}:
            if item.duration_s is None:
                raise PromptCompilationError(
                    "H3_REFERENCE_DURATION_UNKNOWN",
                    f"{item.label} has no probed duration; H3 video/audio references must be measured "
                    "before submission",
                )
            if item.duration_s < 2 or item.duration_s > MAX_MEDIA_DURATION_S:
                raise PromptCompilationError(
                    "H3_REFERENCE_DURATION",
                    f"{item.label} must be between 2 and 15 seconds",
                )
            totals[item.use_as.value] += item.duration_s
    for kind, total in totals.items():
        if total > MAX_MEDIA_DURATION_S:
            raise PromptCompilationError(
                "H3_REFERENCE_DURATION",
                f"Total {kind} reference duration exceeds the official 15 second limit",
            )


def _infer_mode(manifest: list[ReferenceManifestItem], hint: GenerationMode | None) -> GenerationMode:
    pictures = [item for item in manifest if item.use_as == ReferenceUse.PICTURE]
    other = [item for item in manifest if item.use_as != ReferenceUse.PICTURE]
    if hint:
        _check_mode_hint(hint, manifest)
        return hint
    if not manifest:
        return GenerationMode.T2VA
    if other:
        return GenerationMode.REF2VA
    if len(pictures) > 3:
        return GenerationMode.REF2VA
    roles = {item.frame_role for item in pictures}
    if len(pictures) == 1:
        if FrameRole.LAST in roles:
            return GenerationMode.L2VA
        return GenerationMode.I2VA
    if len(pictures) == 2:
        if {FrameRole.FIRST, FrameRole.LAST}.issubset(roles):
            return GenerationMode.FL2VA
        return GenerationMode.REF2VA
    return GenerationMode.REF2VA


def _check_mode_hint(mode: GenerationMode, manifest: list[ReferenceManifestItem]) -> None:
    pictures = [item for item in manifest if item.use_as == ReferenceUse.PICTURE]
    others = [item for item in manifest if item.use_as != ReferenceUse.PICTURE]
    if mode == GenerationMode.T2VA and manifest:
        raise PromptCompilationError("H3_MODE_REFERENCE_MISMATCH", "T2VA cannot carry reference files")
    if mode == GenerationMode.I2VA and (len(pictures) != 1 or others):
        raise PromptCompilationError("H3_MODE_REFERENCE_MISMATCH", "I2VA requires exactly one first-frame picture")
    if mode == GenerationMode.L2VA and (len(pictures) != 1 or others):
        raise PromptCompilationError("H3_MODE_REFERENCE_MISMATCH", "L2VA requires exactly one last-frame picture")
    if mode == GenerationMode.FL2VA and (len(pictures) != 2 or others):
        raise PromptCompilationError(
            "H3_MODE_REFERENCE_MISMATCH", "FL2VA requires exactly a first-frame and a last-frame picture"
        )
    if mode == GenerationMode.REF2VA and not manifest:
        raise PromptCompilationError("H3_MODE_REFERENCE_MISMATCH", "Ref2VA requires at least one reference file")


def _check_frame_roles(mode: GenerationMode, pictures: list[ReferenceManifestItem]) -> None:
    """Each generation mode has its own frame-role contract."""
    roles = {item.frame_role for item in pictures if item.frame_role}
    if mode == GenerationMode.I2VA and FrameRole.LAST in roles:
        raise PromptCompilationError(
            "H3_FRAME_ROLE_MISMATCH", "I2VA cannot use a last-frame picture"
        )
    if mode == GenerationMode.L2VA and FrameRole.FIRST in roles:
        raise PromptCompilationError(
            "H3_FRAME_ROLE_MISMATCH", "L2VA cannot use a first-frame picture"
        )
    if mode == GenerationMode.FL2VA:
        if not {FrameRole.FIRST, FrameRole.LAST}.issubset(roles):
            raise PromptCompilationError(
                "H3_FRAME_ROLE_MISMATCH",
                "FL2VA requires one first-frame picture and one last-frame picture",
            )


def _speakers_of(line: DialogueLine) -> list[str]:
    """The people a line names; a chorus line names several in one field."""
    from shine.asset_identity import split_character_names

    return split_character_names(line.speaker_id) or [line.speaker_id]


def _speaker_mapping(spec: ShotSpec) -> tuple[dict[str, str], list[str]]:
    order: list[str] = []
    for line in sorted(spec.dialogue, key=lambda item: item.start_s):
        for speaker in _speakers_of(line):
            if speaker not in order:
                order.append(speaker)
    return {speaker: f"S{index + 1}" for index, speaker in enumerate(order)}, order


def _label_for_character(character: str, ctx: _Context) -> str | None:
    images = [item for item in ctx.manifest if item.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}]
    if ctx.legacy_style:
        return next((item.label for item in images if item.asset_id == character or item.role == character
                     or character_reference_matches(character, item.entity_id)), None)
    subjects = [item for item in images if item.use_as == ReferenceUse.SUBJECT]
    pool = subjects or images
    matches = ([item for item in pool if item.asset_id == character]
               or [item for item in pool if item.role == character]
               or [item for item in pool if item.entity_id == character]
               or [item for item in pool if character_reference_matches(character, item.entity_id)])
    return matches[0].label if len(matches) == 1 else None


def _validate_dialogue(ctx: _Context, mode: GenerationMode) -> None:
    dialogue: list[DialogueLine] = sorted(ctx.spec.dialogue, key=lambda item: item.start_s)
    previous_end = -1.0
    for line in dialogue:
        if line.end_s > ctx.spec.duration_target_s:
            raise PromptCompilationError("H3_DIALOGUE_TIMING", "Dialogue exceeds the target shot duration")
        if line.start_s < previous_end - 0.001:
            raise PromptCompilationError("H3_DIALOGUE_OVERLAP", "Dialogue lines must not overlap")
        previous_end = max(previous_end, line.end_s)
    if ctx.spec.duration_target_s > 15 or ctx.spec.duration_target_s < 4:
        raise PromptCompilationError("H3_DURATION", "H3 supports 4 to 15 second clips")
    if len(ctx.speaker_order) and mode == GenerationMode.REF2VA:
        for speaker in ctx.speaker_order:
            # A narrator talks over the picture and is never visible in it, so
            # it owns no face to keep consistent.  Requiring a subject image
            # here would force a fake "narrator portrait" into every narrated
            # shot; the prompt still states the narrator never appears.
            if is_narrator_speaker(speaker):
                continue
            if not _label_for_character(speaker, ctx):
                raise PromptCompilationError(
                    "H3_SPEAKER_REFERENCE_MISSING",
                    f"Speaker {speaker} has no bound subject or picture reference",
                )
    # Every person the shot says it shows needs a reference of their own.
    # Without this check a shot silently renders a character from the text
    # alone: the model then invents a face or borrows another subject's, which
    # is exactly the identity drift this pipeline must not hide.
    if mode == GenerationMode.REF2VA and any(
        item.use_as == ReferenceUse.SUBJECT for item in ctx.manifest
    ):
        from shine.asset_identity import split_character_names

        characters = (ctx.spec.characters if ctx.legacy_style else list(dict.fromkeys(
            name for entry in ctx.spec.characters for name in split_character_names(entry)
        )))
        used: set[str] = set()
        for character in characters:
            if is_narrator_speaker(character):
                continue
            label = _label_for_character(character, ctx)
            if not label or (not ctx.legacy_style and label in used):
                raise PromptCompilationError(
                    "H3_CHARACTER_REFERENCE_MISSING",
                    f"Character {character} appears in this shot but has no bound subject reference",
                    {"character": character, "characters": ctx.spec.characters},
                )
            used.add(label)


_PITCH_PHRASE = {
    "low_flat": "a low, flat pitch",
    "steady": "a steady pitch",
    "rising": "a rising pitch",
    "falling": "a falling pitch",
    "cracking": "a cracking pitch",
}


def _delivery_modifier(line: DialogueLine) -> str:
    """Compile non-empty 4D vocal delivery cues into an English clause outside ``<d>``."""
    delivery = line.delivery
    if delivery is None or delivery.is_empty():
        return ""
    adjs = [value for value in (delivery.volume, delivery.pace, delivery.voice_quality) if value]
    pitch_phrase = _PITCH_PHRASE.get(delivery.pitch or "")
    if adjs and pitch_phrase:
        return f"in a {', '.join(adjs)} voice with {pitch_phrase}"
    if adjs:
        return f"in a {', '.join(adjs)} voice"
    if pitch_phrase:
        return f"with {pitch_phrase}"
    return ""


def _dialogue_sentence(ctx: _Context, line: DialogueLine) -> str:
    language = _language_tag(line.text, ctx.project_language)
    speakers = _speakers_of(line)
    labels = [label for label in (_label_for_character(speaker, ctx) for speaker in speakers) if label]
    if labels:
        subject = ", ".join(labels)
    elif all(is_narrator_speaker(speaker) for speaker in speakers):
        subject = "The off-screen narrator"
    else:
        subject = "The speaker"
    speaker = ", ".join(ctx.speaker_labels[name] for name in speakers)
    spoken_line = (
        line.text if (ctx.legacy_semantics or ctx.pre_sanitize)
        else (sanitize_h3_dialogue_text(line.text) or line.text)
    )
    if ctx.acting_v25:
        modifier = _delivery_modifier(line)
        if modifier:
            return (
                f"At {_format_timestamp(line.start_s)}, {subject} ({speaker}), {modifier}, speaks: "
                f"<d>[{language}] {spoken_line}</d>"
            )
    return (
        f"At {_format_timestamp(line.start_s)}, {subject} ({speaker}) says: "
        f"<d>[{language}] {spoken_line}</d>"
    )


def _camera_sentence(spec: ShotSpec) -> str:
    camera = spec.camera or {}
    if not camera:
        return ""
    preferred = ("shot_size", "movement", "angle", "axis_rule", "lens")
    parts = [f"{key.replace('_', ' ')}: {camera[key]}" for key in preferred if camera.get(key)]
    parts.extend(f"{key.replace('_', ' ')}: {value}" for key, value in camera.items() if key not in preferred)
    return "Camera: " + "; ".join(parts) + "." if parts else ""


def _camera_narrative(spec: ShotSpec) -> str:
    """Natural camera prose for 1.4.0 (plan P0-D.4 / R3).

    The cinematographer's fields are already English prose; the compiler weaves
    them into flowing sentences instead of the mechanical ``Camera: key: value``
    enumeration.  A static camera is stated as holding still rather than left
    ambiguous, and movement is described with its type/amplitude/speed as written.
    """
    camera = spec.camera or {}
    if not camera:
        return ""
    order = ("composition", "shot_size", "movement", "angle", "axis_rule", "lens", "lighting")
    prose: list[str] = []
    for key in order:
        value = str(camera.get(key) or "").strip().rstrip(".")
        if value:
            prose.append(value)
    for key, value in camera.items():
        if key in order or key == "director_style":
            continue
        text = str(value or "").strip().rstrip(".")
        if text:
            prose.append(text)
    style = str(camera.get("director_style") or "").strip().rstrip(".")
    if style:
        # The frozen director-style constraint keeps its label so the shot states
        # which style rules bind it; the camera prose itself stays natural.
        prose.append(f"Director style: {style}")
    if not prose:
        return ""
    return ". ".join(prose) + "."


def _shot_text(ctx: _Context) -> str:
    parts: list[str] = ["[Shot 1] " + _style_sentences(
        _prompt_style(ctx.spec, ctx.style, legacy_style=ctx.legacy_style), ctx.spec.style_version_id,
        include_sound=bool(ctx.spec.dialogue), legacy_sound=ctx.legacy_sound,
        legacy_semantics=ctx.legacy_semantics, pre_sanitize=ctx.pre_sanitize,
    )]
    cast = _cast_order(ctx)
    if cast:
        parts.append(cast)
    if not ctx.legacy_audio:
        if ctx.spec.dialogue:
            parts.append(
                "Perform speech only for the timed dialogue blocks below. Between those lines, "
                "use silent reactions and non-vocal scene sounds."
                if ctx.legacy_sound else
                "Deliver only the timed dialogue blocks below, exactly as written; between those "
                "lines the performers react silently and the scene sound stays non-vocal."
            )
        else:
            parts.append(
                "Show reactions through gaze, gestures and movement with no speaking or mouthing words. "
                "Use only non-vocal scene sounds."
                if ctx.legacy_sound else
                "Performers act with closed mouths, expressing reactions through gaze, gestures and "
                "movement; the soundtrack stays non-vocal."
            )
    parts.append(ctx.spec.story_beat.strip())
    camera = _camera_sentence(ctx.spec) if ctx.legacy_semantics else _camera_narrative(ctx.spec)
    if camera:
        parts.append(camera)
    environment = ctx.spec.sound.get("environment")
    if environment and not ctx.spec.dialogue and not ctx.legacy_semantics and not ctx.pre_sanitize:
        environment = non_vocal_ambience_text(environment)
    if environment:
        parts.append(f"The scene is set in {environment}.")
    facts = ctx.spec.locked_story_facts
    if any(re.search(r"[\u3400-\u9fff]", fact) for fact in facts):
        translated = ctx.spec.prompt_story_facts
        if (len(translated) != len(facts) or any(not value.strip() or re.search(r"[\u3400-\u9fff]", value)
                                                  for value in translated)):
            raise PromptCompilationError("H3_LOCKED_FACT_TRANSLATION_REQUIRED",
                                         "Chinese locked facts require corresponding English prompt translations")
        facts = translated
    if facts:
        continuity_notes = " ".join(f"{fact.strip().rstrip('.')}." for fact in facts)
        lead = (
            "Story continuity facts below are background context only; they are not dialogue and must "
            "never be spoken, narrated or performed as voice-over: "
            if ctx.legacy_sound else
            "Story continuity facts below are silent background context for the picture only; they "
            "inform the visuals and are never heard as audio: "
        )
        parts.append(lead + continuity_notes)
    if any(is_narrator_speaker(speaker) for speaker in ctx.speaker_order):
        parts.append(narrator_sentence())
    for line in sorted(ctx.spec.dialogue, key=lambda item: item.start_s):
        parts.append(_dialogue_sentence(ctx, line))
    parts.extend(_continuity_sentences(ctx))
    base = " ".join(part.strip() for part in parts if part.strip())
    # Plan P0-C: the official native in-shot timeline.  Each internal cut becomes
    # its own [Shot N] At MM:SS.mmm block instead of a flattened "hard cut"
    # sentence, so H3 reads the cut structure directly.  Legacy versions keep the
    # historical single-block bytes untouched.
    if not ctx.legacy_semantics and ctx.spec.internal_cuts:
        blocks = [base]
        for index, cut in enumerate(ctx.spec.internal_cuts, start=2):
            blocks.append(
                f"[Shot {index}] At {_format_timestamp(cut.start_s)}, {cut.visual_description.strip()}"
            )
        return "\n".join(blocks)
    return base


def _continuity_mode(continuity: Any) -> tuple[ContinuityMode | None, str | None]:
    if isinstance(continuity, dict):
        raw_mode = continuity.get("mode")
        prev_id = continuity.get("previous_shot_id")
    else:
        raw_mode = getattr(continuity, "mode", None)
        prev_id = getattr(continuity, "previous_shot_id", None)
    try:
        mode = raw_mode if isinstance(raw_mode, ContinuityMode) else ContinuityMode(str(raw_mode))
    except ValueError:
        mode = None
    return mode, (str(prev_id) if prev_id else None)


def _has_last_frame_continuity(ctx: _Context) -> bool:
    mode, prev_shot_id = _continuity_mode(ctx.spec.continuity)
    if mode != ContinuityMode.LAST_FRAME:
        return False
    return bool(prev_shot_id) or any(ref.role == "continuity_derivative" for ref in ctx.manifest)


def _continuity_sentences(ctx: _Context) -> list[str]:
    """How the shot joins the one before it, from the derivative it was given.

    Every pre-2.3.0 version keeps its historical wording; 2.3.0+ places
    soft ``last_frame`` opening continuity at the top of ``[Shot 1]`` and
    anchors 0.0s posture and held props so actions from the previous shot
    are not reset or repeated; 2.4.0+ additionally locks white balance,
    exposure, color grading, and strict forward motion without rewinding.
    """
    mode, prev_shot_id = _continuity_mode(ctx.spec.continuity)
    continuation = next((ref for ref in ctx.manifest if ref.role == "continuity_derivative"), None)
    if continuation:
        if mode == ContinuityMode.LAST_FRAME:
            if ctx.continuity_clean_v26:
                return [
                    f"At 0.0s, continue directly from {continuation.label} (the preceding shot's final frame) "
                    "with identical color grading, white balance, exposure, shadow depth, framing, character "
                    "posture, gaze direction, and held prop placement; advance strictly forward in time from "
                    f"the final instant of {continuation.label} without rewinding, replaying earlier motion, or "
                    "resetting the lighting; keep each performer on the same physical side of fixed set "
                    "landmarks and never materialize an unseen prop without a visible continuous motion."
                ]
            if ctx.continuity_color_v24:
                return [
                    f"At 0.0s, continue directly from {continuation.label} (the preceding shot's final frame) "
                    "with identical color grading, white balance, exposure, shadow depth, framing, character "
                    "posture, gaze direction, and held prop placement; advance strictly forward in time from "
                    f"the final instant of {continuation.label} without rewinding, replaying earlier motion, or "
                    "resetting the lighting."
                ]
            if ctx.handoff_v23:
                return [
                    f"At 0.0s, open seamlessly from the closing framing, character positions, posture, "
                    f"gaze direction, and held prop placement of {continuation.label} (the preceding shot's "
                    "final frame) as a soft visual reference; do not reset or repeat the preceding "
                    "shot's ending motion, and proceed directly with the new action below."
                ]
            return [f"Use {continuation.label}, the preceding shot's final frame, as a soft visual reference "
                    "for the opening composition."]
        if mode == ContinuityMode.HARD_FRAME_ANCHOR:
            return [f"Use {continuation.label}, the preceding shot's final frame, as the opening frame."]
        if mode == ContinuityMode.VIDEO_REFERENCE:
            return [f"Continue the motion and framing in {continuation.label}, the source shot's video."]
        if mode == ContinuityMode.AUDIO_REFERENCE:
            return [
                f"Continue the sound bed in {continuation.label}, the source shot's audio."
                if ctx.legacy_audio else
                f"Use {continuation.label} for non-vocal ambience only."
            ]
        return []
    if mode == ContinuityMode.LAST_FRAME and prev_shot_id:
        if ctx.continuity_clean_v26:
            return [
                "At 0.0s, continue directly from the approved final frame of shot "
                f"{prev_shot_id} with identical color grading, white balance, exposure, shadow depth, "
                "framing, posture, gaze direction, and held prop placement; advance strictly forward in "
                "time without rewinding, replaying earlier motion, or resetting the lighting; keep each "
                "performer on the same physical side of fixed set landmarks and never materialize an "
                "unseen prop without a visible continuous motion."
            ]
        if ctx.continuity_color_v24:
            return [
                "At 0.0s, continue directly from the approved final frame of shot "
                f"{prev_shot_id} with identical color grading, white balance, exposure, shadow depth, "
                "framing, posture, gaze direction, and held prop placement; advance strictly forward in "
                "time without rewinding, replaying earlier motion, or resetting the lighting."
            ]
        if ctx.handoff_v23:
            return [
                f"At 0.0s, the opening framing, posture, gaze direction, and held prop placement continue "
                f"seamlessly from the approved final frame of shot {prev_shot_id} without "
                "resetting or repeating the ending motion."
            ]
        return [
            f"The opening composition continues seamlessly from the approved final frame of shot "
            f"{prev_shot_id}."
        ]
    if mode == ContinuityMode.VIDEO_REFERENCE and prev_shot_id:
        return [
            f"The shot continues the motion and framing of the approved video from shot "
            f"{prev_shot_id}."
        ]
    if mode == ContinuityMode.AUDIO_REFERENCE and prev_shot_id:
        return [
            "The audio bed continues uninterrupted from the previous approved shot."
            if ctx.legacy_audio else
            "Only non-vocal ambience continues from the previous approved shot."
        ]
    return []


def _performance_sentence(ctx: _Context) -> str:
    """The positive performance contract of the shot (never a list of forbidden acts)."""
    if ctx.legacy_audio:
        return ""
    if ctx.spec.dialogue:
        return (
            "Perform speech only for the timed dialogue blocks below. Between those lines, "
            "use silent reactions and non-vocal scene sounds."
            if ctx.legacy_sound else
            "Deliver only the timed dialogue blocks below, exactly as written; between those "
            "lines the performers react silently and the scene sound stays non-vocal."
        )
    return (
        "Show reactions through gaze, gestures and movement with no speaking or mouthing words. "
        "Use only non-vocal scene sounds."
        if ctx.legacy_sound else
        "Performers act with closed mouths, expressing reactions through gaze, gestures and "
        "movement; the soundtrack stays non-vocal."
    )


_FACING_PHRASE = {
    Facing.FACING_CAMERA: "facing the camera",
    Facing.FACING_LEFT: "facing screen-left",
    Facing.FACING_RIGHT: "facing screen-right",
    Facing.THREE_QUARTER_LEFT: "turned three-quarter toward screen-left",
    Facing.THREE_QUARTER_RIGHT: "turned three-quarter toward screen-right",
    Facing.BACK_TO_CAMERA: "back to the camera",
}
_DEPTH_PLANE = {"fg": "foreground", "mg": "midground", "bg": "background"}


def _zone_phrase(zone: FrameZone) -> str:
    side, _, plane = zone.value.partition("_")
    return f"{side} {_DEPTH_PLANE.get(plane, plane)}"


def _placement_sentence(ctx: _Context) -> str:
    """Where every placed performer stands, anchored in the shot's first action sentence."""
    blocking = ctx.spec.blocking
    if blocking is None:
        return ""
    placements: list[str] = []
    for placement in blocking.placements:
        label = _label_for_character(placement.character, ctx)
        if not label:
            continue
        phrase = f"{label} is in the {_zone_phrase(placement.zone)}, {_FACING_PHRASE[placement.facing]}"
        if placement.posture.strip():
            posture_text = placement.posture.strip().rstrip(".")
            if ctx.continuity_clean_v26:
                posture_text = _resolve_character_tokens(posture_text, ctx)
            phrase += f", {posture_text}"
        if placement.props:
            prop_items = [prop.strip().rstrip(".") for prop in placement.props]
            if ctx.continuity_clean_v26:
                prop_items = [_resolve_character_tokens(prop, ctx) for prop in prop_items]
            phrase += ", holding " + ", ".join(prop_items)
        placements.append(phrase)
    if not placements:
        return ""
    return "In frame: " + "; ".join(placements) + "."


def _timeline_sentence(beats) -> str:
    """The performance as second-by-second prose: action, then the face, then the camera."""
    windows: list[str] = []
    for beat in beats:
        window = f"From {beat.start_s:.1f}s to {beat.end_s:.1f}s, {beat.action.strip().rstrip('.')}"
        if beat.expression.strip():
            window += f"; {beat.expression.strip().rstrip('.')}."
        else:
            window += "."
        if beat.camera.strip():
            window += f" {beat.camera.strip().rstrip('.')}."
        windows.append(window)
    return " ".join(windows)


def _environment_sentences(ctx: _Context) -> list[str]:
    environment = ctx.spec.environment
    if environment is None:
        return []
    sentences = []
    for label, text in (
        ("Space", environment.depth), ("Light", environment.light), ("Air", environment.micro_motion),
    ):
        if text.strip():
            sentences.append(f"{label}: {_clause(text)}")
    return sentences


def _resolve_character_tokens(text: str, ctx: _Context) -> str:
    """Resolve stable ``{{Ck}}`` character placeholders into request-local ``<Subject n>`` labels.

    Only active from 2.1.0 onward so 2.0.0 requests replay byte-identically.
    Never leaks raw ``{{Ck}}`` syntax into the compiled H3 prompt.
    """
    if not text or not ctx.anchored_v21 or "{{" not in text:
        return text
    tokens = ctx.spec.character_tokens or {}

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        character = tokens.get(token)
        if character:
            label = _label_for_character(character, ctx)
            if label:
                return label
        note = f"Character placeholder {token} had no matching bound reference and fell back to generic wording."
        if note not in ctx.warnings:
            ctx.warnings.append(note)
        return "the performer"

    return CHARACTER_TOKEN_PATTERN.sub(replace, text)


_FACT_NON_PERSON_PREFIXES = frozenset({
    "道具", "关键道具", "物品", "场景", "核心场景", "世界观", "背景", "风格", "规则",
    "时间", "地点", "人物", "角色", "说明", "提示", "注意",
    "prop", "props", "scene", "scenes", "world", "setting", "style", "rule", "note",
})
_WORLD_FACT_PREFIXES = frozenset({
    "时代", "年代", "基调", "气氛", "氛围", "世界观", "背景", "风格",
    "时间", "地点", "场景", "天气", "光影", "色调", "配色", "主题", "环境",
    "era", "period", "tone", "mood", "atmosphere", "world", "setting",
    "background", "style", "time", "location", "place", "weather",
    "lighting", "palette", "theme", "environment",
})
_META_STRUCTURE_FACT_RE = re.compile(
    r"(?:全片共\s*[0-9一二三四五六七八九十两]+\s*[场集]|拆分为\s*\d+\s*个?镜头|总计\s*\d+\s*个?镜头)"
)
_PERSON_BIO_START_RE = re.compile(
    r"^\s*(?:大约|约|年约)?\s*[0-9０-９一二三四五六七八九十两]{1,4}\s*(?:岁|余年|多岁|来岁)"
    r"|^\s*[^，,。.；;：:]{0,10}?(?:男性|女性|男孩|女孩|男人|女人|男子|女子|少年|少女|老者|老妇|老人|中年|青年|孩童)"
)
_PERSON_BIO_EN_RE = re.compile(r"\b\d{1,3}\s*[-–]?\s*years?\s*[-–]?\s*old\b", re.IGNORECASE)
_PERSON_APPEARANCE_ZH_RE = re.compile(
    r"(?:法袍|长袍|长裙|短裙|斗篷|披风|铠甲|盔甲|甲胄|胸针|皮靴|靴子|兜帽|"
    r"长编发|发辫|剑疤|银发|银白发|红发|黑发|金发|眼眸|双眼|双眸|"
    r"手持|手提|手握|腰佩|背负|身穿|穿着|披着|戴着|"
    r"骑士|牧师|法师|守望者|刺客|女巫)"
)
_PERSON_APPEARANCE_EN_RE = re.compile(
    r"\b(?:robe|cloak|gown|tunic|armor|armour|brooch|boots|hood|braid|hair|eyes|scar|"
    r"holding|carrying|wearing|wielding|staff|wand|sword|lantern|dagger|shield|bow|spear|"
    r"knight|priest|mage|wizard|watcher)\b",
    re.IGNORECASE,
)


def _looks_like_person_bio(zh_fact: str, en_fact: str, *, include_appearance: bool = False) -> bool:
    """Whether one labelled fact is a person's appearance bio rather than a world note."""
    body = re.sub(r"^\s*[^：:]{1,20}\s*[：:]\s*", "", zh_fact)
    en_body = re.sub(r"^\s*[^:]{1,30}\s*:\s*", "", en_fact)
    if _PERSON_BIO_START_RE.search(body) or _PERSON_BIO_EN_RE.search(en_fact):
        return True
    if include_appearance:
        return bool(_PERSON_APPEARANCE_ZH_RE.search(body) or _PERSON_APPEARANCE_EN_RE.search(en_body))
    return False


def _split_top_level(text: str, seps: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in "（(":
            depth += 1
            buf.append(ch)
        elif ch in "）)":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in seps:
            part = "".join(buf).strip()
            if part:
                items.append(part)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _split_prop_items_en(text: str) -> list[str]:
    """Split English prop inventories on top-level commas/semicolons and top-level ``and`` / ``&``."""
    raw_chunks = _split_top_level(text, ",;")
    items: list[str] = []
    for chunk in raw_chunks:
        cleaned = re.sub(r"^\s*(?:and|&)\s+", "", chunk.strip(), flags=re.IGNORECASE)
        if not cleaned:
            continue
        buf: list[str] = []
        depth = 0
        i = 0
        while i < len(cleaned):
            ch = cleaned[i]
            if ch in "（(":
                depth += 1
                buf.append(ch)
                i += 1
            elif ch in "）)":
                depth = max(0, depth - 1)
                buf.append(ch)
                i += 1
            elif depth == 0:
                m = re.match(r"^\s+(?:and|&)\s+", cleaned[i:], flags=re.IGNORECASE)
                if m:
                    part = "".join(buf).strip()
                    if part:
                        items.append(part)
                    buf = []
                    i += len(m.group(0))
                    continue
                buf.append(ch)
                i += 1
            else:
                buf.append(ch)
                i += 1
        tail = "".join(buf).strip()
        if tail:
            items.append(tail)
    return items


def _scoped_story_facts(ctx: _Context, zh_facts: list[str], en_facts: list[str]) -> list[str]:
    """Keep only background facts relevant to this shot's visible cast, props and world.

    A project locks cast bios and prop inventories globally; emitting an off-cast
    character's appearance bio (e.g. Morrigan's silver braid and velvet cloak) inside
    a solo shot of Adrian causes H3 to render that off-screen character into frame.
    Every version before 2.2.0 emits the facts unchanged, and 2.2.0 keeps its
    exact pre-2.3.0 scoping bytes, so stored requests of every released version
    replay byte-exactly.
    """
    if not ctx.scoped_facts:
        return list(en_facts)
    cast_names = {
        name.strip()
        for name in (ctx.spec.characters or [])
        if isinstance(name, str) and name.strip()
    }
    for ref in ctx.manifest:
        if ref.use_as == ReferenceUse.SUBJECT:
            for candidate in (ref.entity_id, ref.role):
                if isinstance(candidate, str) and candidate.strip():
                    cast_names.add(candidate.strip())
    off_cast_names = {
        str(name).strip()
        for name in (ctx.spec.character_tokens or {}).values()
        if isinstance(name, str) and name.strip()
        and not any(character_reference_matches(name.strip(), c) for c in cast_names)
    }
    shot_props: set[str] = set()
    for ref in ctx.manifest:
        if ref.use_as == ReferenceUse.PICTURE and ref.asset_role == "prop":
            for candidate in (ref.entity_id, ref.role):
                if isinstance(candidate, str) and candidate.strip():
                    shot_props.add(candidate.strip())
    if ctx.spec.blocking:
        for pl in ctx.spec.blocking.placements:
            for prop in pl.props:
                if isinstance(prop, str) and prop.strip():
                    shot_props.add(prop.strip())
    story_norm = re.sub(r"[\s_-]+", " ", (ctx.spec.story_beat or "").lower())
    props_norm = {re.sub(r"[\s_-]+", " ", p.lower()).strip() for p in shot_props if p.strip()}

    def _en_prop_matches(e_item: str) -> bool:
        e_name = re.sub(r"（[^）]*）|\([^)]*\)", "", e_item).strip()
        e_norm = re.sub(r"[\s_-]+", " ", e_name.lower()).strip()
        return bool(
            e_norm
            and (
                e_norm in props_norm
                or any(e_norm in p or p in e_norm for p in props_norm if len(p) >= 3)
                or re.search(r"(?<!\w)" + re.escape(e_norm) + r"(?!\w)", story_norm) is not None
            )
        )

    kept: list[str] = []
    paired_zh = zh_facts if len(zh_facts) == len(en_facts) else en_facts
    for zh_raw, en_raw in zip(paired_zh, en_facts, strict=True):
        zh_fact = str(zh_raw or "").strip()
        en_fact = str(en_raw or "").strip()
        if not en_fact:
            continue
        if _META_STRUCTURE_FACT_RE.search(zh_fact):
            continue
        # Off-cast character bio filtering.
        zh_prefix_m = re.match(r"^\s*([^：:\s]{1,20})\s*[：:]", zh_fact)
        zh_prefix = zh_prefix_m.group(1).strip() if zh_prefix_m else ""
        en_prefix_m = re.match(r"^\s*([A-Za-z][A-Za-z0-9' -]{0,24})\s*:", en_fact)
        en_prefix = en_prefix_m.group(1).strip() if en_prefix_m else ""
        if off_cast_names:
            mentions_off = any(off in zh_fact for off in off_cast_names)
            mentions_in = any(c in zh_fact for c in cast_names)
            if (zh_prefix and any(character_reference_matches(zh_prefix, off) for off in off_cast_names)) or (
                mentions_off and not mentions_in
            ):
                continue
        if zh_prefix and cast_names and zh_prefix.lower() not in _FACT_NON_PERSON_PREFIXES:
            is_world_prefix = ctx.handoff_v23 and zh_prefix.lower() in _WORLD_FACT_PREFIXES
            if (
                not is_world_prefix
                and not any(character_reference_matches(zh_prefix, c) for c in cast_names)
                and zh_prefix not in (ctx.spec.story_beat or "")
                and _looks_like_person_bio(zh_fact, en_fact, include_appearance=ctx.handoff_v23)
            ):
                # A bio of somebody this shot's scene never tokenised. A labelled world
                # fact (Era: …, Tone: …) stays untouched.
                continue
        elif (
            ctx.handoff_v23
            and en_prefix
            and cast_names
            and en_prefix.lower() not in _FACT_NON_PERSON_PREFIXES
            and en_prefix.lower() not in _WORLD_FACT_PREFIXES
            and not any(character_reference_matches(en_prefix, c) for c in cast_names)
            and en_prefix.lower() not in story_norm
            and _looks_like_person_bio(zh_fact, en_fact, include_appearance=True)
        ):
            continue

        # Prop inventory fact filtering (e.g. "Props: rune sword (...), brass lantern (...)").
        is_prop_fact = bool(
            re.match(r"^\s*(?:道具|关键道具|物品)\s*[：:]", zh_fact)
            or re.match(r"^\s*Props?\s*:", en_fact, flags=re.IGNORECASE)
        )
        if is_prop_fact:
            en_head_m = re.match(r"^\s*(Props?\s*:)\s*(.*)$", en_fact, flags=re.IGNORECASE)
            zh_head_m = re.match(r"^\s*[^：:]+[：:]\s*(.*)$", zh_fact)
            en_prefix_head = en_head_m.group(1) if en_head_m else "Props:"
            en_body = (en_head_m.group(2) if en_head_m else en_fact).strip().rstrip(".")
            zh_body = (zh_head_m.group(1) if zh_head_m else zh_fact).strip().rstrip("。")
            en_items = _split_prop_items_en(en_body) if ctx.handoff_v23 else _split_top_level(en_body, ",;")
            zh_items = _split_top_level(zh_body, "、，,;；")
            if len(en_items) == len(zh_items) and len(en_items) >= 1:
                selected_en: list[str] = []
                for z_item, e_item in zip(zh_items, en_items, strict=True):
                    z_name = re.sub(r"（[^）]*）|\([^)]*\)", "", z_item).strip()
                    matched = (z_name and z_name in shot_props) or _en_prop_matches(e_item)
                    if matched:
                        selected_en.append(e_item.strip().rstrip("."))
                if not selected_en:
                    continue
                kept.append(f"{en_prefix_head} {', '.join(selected_en)}.")
                continue
            if ctx.handoff_v23 and en_items:
                selected_en = [e_item.strip().rstrip(".") for e_item in en_items if _en_prop_matches(e_item)]
                if not selected_en:
                    continue
                kept.append(f"{en_prefix_head} {', '.join(selected_en)}.")
                continue
        kept.append(en_fact)
    return kept


def _shot_text_thin(ctx: _Context) -> str:
    """The 2.0.0+ shot text: one shell, a rich timeline, every line in its own block.

    A shot with internal cuts is one ``[Shot N]`` block per sub-shot.  A spoken
    line belongs to the sub-shot that performs it, so each block carries the
    lines and performance windows that start inside it; up to 1.4.0 every line
    sat in the ``[Shot 1]`` block whatever its timestamp said.
    """
    spec = ctx.spec
    cuts = list(spec.internal_cuts)
    blocks: list[list[str]] = [[] for _ in range(len(cuts) + 1)]
    for index, cut in enumerate(cuts, start=1):
        blocks[index].append(
            f"[Shot {index + 1}] At {_format_timestamp(cut.start_s)}, "
            f"{_resolve_character_tokens(cut.visual_description.strip(), ctx)}"
        )

    def block_of(seconds: float) -> int:
        # The sub-shot that performs a moment: the last cut at or before it.
        return sum(1 for cut in cuts if cut.start_s <= seconds)

    blocks[0].append("[Shot 1] " + _style_sentences(
        _prompt_style(spec, ctx.style, legacy_style=ctx.legacy_style), spec.style_version_id,
        include_sound=bool(spec.dialogue), legacy_sound=ctx.legacy_sound,
        legacy_semantics=ctx.legacy_semantics, pre_sanitize=ctx.pre_sanitize, thin_shell=True,
    ))
    cast = _cast_order(ctx)
    if cast:
        blocks[0].append(cast)
    blocks[0].append(_performance_sentence(ctx))
    mode, _ = _continuity_mode(spec.continuity)
    top_last_frame_continuity = (
        ctx.handoff_v23 and mode == ContinuityMode.LAST_FRAME
    )
    if top_last_frame_continuity:
        blocks[0].extend(_continuity_sentences(ctx))
    include_story_beat = not (ctx.continuity_clean_v26 and bool(spec.timeline))
    for sentence in (
        _placement_sentence(ctx),
        f"It opens on {_clause(_resolve_character_tokens(spec.entry_state, ctx))}" if spec.entry_state.strip() else "",
        _resolve_character_tokens(spec.story_beat.strip(), ctx) if include_story_beat else "",
        _resolve_character_tokens(_camera_narrative(spec), ctx),
    ):
        if sentence:
            blocks[0].append(sentence)
    environment = spec.sound.get("environment")
    if environment and not spec.dialogue:
        environment = non_vocal_ambience_text(environment)
    facts = spec.locked_story_facts
    raw_zh_facts = list(facts)
    if any(re.search(r"[\u3400-\u9fff]", fact) for fact in facts):
        translated = spec.prompt_story_facts
        if (len(translated) != len(facts) or any(not value.strip() or re.search(r"[\u3400-\u9fff]", value)
                                                  for value in translated)):
            raise PromptCompilationError("H3_LOCKED_FACT_TRANSLATION_REQUIRED",
                                         "Chinese locked facts require corresponding English prompt translations")
        facts = translated
    facts = _scoped_story_facts(ctx, raw_zh_facts, list(facts))

    if ctx.acting_v25:
        if environment:
            blocks[0].append(f"The scene is set in {environment}.")
        blocks[0].extend(_resolve_character_tokens(line, ctx) for line in _environment_sentences(ctx))
        if facts:
            blocks[0].append(
                "Background story facts, for the picture only and never heard: "
                + " ".join(f"{fact.strip().rstrip('.')}." for fact in facts)
            )
        if any(is_narrator_speaker(speaker) for speaker in ctx.speaker_order):
            blocks[0].append(narrator_sentence())
        # Interleave timeline windows and timed dialogue chronologically per [Shot N]
        # block. On equal start_s, dialogue (kind_order=0) precedes the concurrent
        # continuation window (kind_order=1), while a pre-speech beat ending at
        # line.start_s naturally has an earlier start_s and stays immediately before
        # the spoken line.
        per_block_events: list[list[tuple[float, int, int, str]]] = [[] for _ in range(len(blocks))]
        for idx, line in enumerate(sorted(spec.dialogue, key=lambda item: item.start_s)):
            per_block_events[block_of(line.start_s)].append(
                (float(line.start_s), 0, idx, _dialogue_sentence(ctx, line))
            )
        for idx, beat in enumerate(sorted(spec.timeline, key=lambda item: item.start_s)):
            per_block_events[block_of(beat.start_s)].append(
                (float(beat.start_s), 1, idx, _resolve_character_tokens(_timeline_sentence([beat]), ctx))
            )
        for block_idx, events in enumerate(per_block_events):
            events.sort(key=lambda item: (item[0], item[1], item[2]))
            for _start, _kind, _idx, sentence in events:
                blocks[block_idx].append(sentence)
    else:
        for beat in sorted(spec.timeline, key=lambda item: item.start_s):
            blocks[block_of(beat.start_s)].append(_resolve_character_tokens(_timeline_sentence([beat]), ctx))
        if environment:
            blocks[0].append(f"The scene is set in {environment}.")
        blocks[0].extend(_resolve_character_tokens(line, ctx) for line in _environment_sentences(ctx))
        if facts:
            blocks[0].append(
                "Background story facts, for the picture only and never heard: "
                + " ".join(f"{fact.strip().rstrip('.')}." for fact in facts)
            )
        if any(is_narrator_speaker(speaker) for speaker in ctx.speaker_order):
            blocks[0].append(narrator_sentence())
        for line in sorted(spec.dialogue, key=lambda item: item.start_s):
            blocks[block_of(line.start_s)].append(_dialogue_sentence(ctx, line))
    if not top_last_frame_continuity:
        blocks[0].extend(_continuity_sentences(ctx))
    if spec.exit_state.strip():
        blocks[-1].append(f"The shot settles on {_clause(_resolve_character_tokens(spec.exit_state, ctx))}")
    return "\n".join(" ".join(part.strip() for part in block if part.strip()) for block in blocks)


_DIALOGUE_AMBIENCE_PREP_RE = re.compile(
    r"\s*\b(?:beneath|under|below)\s+(?:the\s+)?(?:original\s+)?dialogue\b",
    flags=re.IGNORECASE,
)


def non_vocal_ambience_text(text: str) -> str:
    """Strip dialogue-assuming clauses from an ambience/environment note for silent shots.

    A project-wide sound direction rule (e.g. ``...restrained beneath the original
    dialogue and preserve intentional silence``) is valid on spoken shots, but on
    dialogue-free shots telling H3 about "original dialogue" primes it to invent
    foreign-language speech.  Sharing this one sanitizer between landing and
    prompt compilation guarantees consistent behaviour across new and retaken shots.
    """
    cleaned = _DIALOGUE_AMBIENCE_PREP_RE.sub("", text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned).strip()
    if re.search(r"\bdialogue\b", cleaned, flags=re.IGNORECASE):
        return ""
    return cleaned


def _spoken_language_sentence(
    spec: ShotSpec, project_language: str, *, legacy_sound: bool = False, pre_anchor: bool = False
) -> str:
    """The spoken-language contract of the shot.

    The whole prompt is written in English, so the language of the voice has to
    be stated explicitly.  A per-line language tag alone is not enough: the
    model invents English speech when nothing tells it which voice to use, and
    it invents speech at all when a silent shot never says so.

    The contract is written as a positive description of the soundtrack.  The
    pre-1.3.0 sentences enumerated forbidden vocal acts (narration, voice-over,
    whispering, mumbling, singing, humming); the video model treats that
    enumeration as concepts to render and produced unscripted foreign-language
    narration from it.  The current sentences say what the soundtrack contains
    and anchor every voice to the project language instead of naming the acts
    they must not perform.

    The language is written in its canonical English name: the official rule
    pack rejects CJK outside the dialogue tags, so a project setting written in
    another script may never be copied into the prompt.
    """
    language = (project_language or "").strip()
    if not spec.dialogue:
        if legacy_sound:
            return (
                "There is no spoken dialogue in this shot: do not produce speech, narration, voice-over, "
                "whispering, mumbling, singing, humming or vocal sounds of any kind. Any speech or voice "
                "mentioned in story facts or visual descriptions is context only, not an audio instruction."
            )
        setting = (
            f" The scene takes place in a {language}-language world, and every sound stays wordless."
            if language and not pre_anchor else ""
        )
        return (
            "This shot has no dialogue: the soundtrack is entirely non-vocal."
            f"{setting} Words quoted in the story facts or visual description stay silent context "
            "and are never heard."
        )
    if not language:
        return ""
    if legacy_sound:
        return (
            f"Only the explicitly tagged dialogue lines in this shot may be spoken in {language}, matching "
            "the original line text exactly; no other language, unscripted speech, narration, voice-over, "
            "background chatter, whispering or extra words may appear. Any voice or reading described in "
            "the story facts or visual description refers only to these exact lines."
        )
    return (
        f"Every voice in this shot speaks {language}. The only words heard are the explicitly tagged "
        "dialogue lines of this shot, delivered exactly as written at their timed positions; all "
        "other sound stays non-vocal. Any voice or reading described in the story facts "
        "or visual description refers only to these exact lines."
    )


def _soundscape(
    spec: ShotSpec, style: StyleBible, project_language: str = "", *,
    legacy_sound: bool = False, pre_anchor: bool = False,
) -> str:
    style = _prompt_style(spec, style)
    environment = spec.sound.get("environment", "").strip()
    ambience = spec.sound.get("ambience", "").strip()
    if not spec.dialogue and not legacy_sound and not pre_anchor:
        environment = non_vocal_ambience_text(environment)
        ambience = non_vocal_ambience_text(ambience)
    if not environment and not ambience:
        if spec.dialogue:
            sentences = [
                f"Natural room ambience continues throughout the video, shaped by the frozen sound rule: "
                f"{style.sound_performance}."
            ]
        else:
            # The frozen sound-performance dimension describes voices and speech;
            # in a dialogue-free shot it contradicts the no-speech contract below
            # and invites the model to invent foreign-language mumbling.
            sentences = [
                "Natural room ambience continues throughout the video; this shot has no spoken dialogue."
                if legacy_sound else
                "Natural room ambience continues throughout the video; the soundtrack stays entirely non-vocal."
            ]
    else:
        pieces = [piece.rstrip(".") + "." for piece in (environment, ambience) if piece]
        sentences = pieces
    spoken = _spoken_language_sentence(spec, project_language, legacy_sound=legacy_sound, pre_anchor=pre_anchor)
    if spoken:
        sentences.append(spoken)
    return " ".join(sentences)


def _music(spec: ShotSpec, *, legacy_audio: bool, legacy_sound: bool = False) -> str:
    policy = (spec.sound.get("music") or spec.sound.get("music_policy") or "").strip()
    if not policy:
        return "N/A"
    lowered = policy.lower()
    if lowered in {"none", "silent", "n/a", "no_music"}:
        return "N/A"
    if lowered in {"low_or_none", "low"}:
        policy = "Sparse low strings at a slow tempo, kept low in the mix and fading out before the final frame."
        if legacy_audio or spec.dialogue:
            policy = policy.replace("kept low in the mix", "kept well below the dialogue")
    music = policy if policy.endswith(".") else policy + "."
    if legacy_audio:
        return music
    # "Instrumental" already excludes voices; naming vocals and lyrics only
    # primed them (see _spoken_language_sentence).
    return music + (" Instrumental only, with no vocals or lyrics." if legacy_sound else " Instrumental only.")


def _align_instruction(mode: GenerationMode, manifest: list[ReferenceManifestItem], duration: float) -> str:
    pictures = [item for item in manifest if item.use_as == ReferenceUse.PICTURE]
    if mode == GenerationMode.T2VA:
        return ""
    if mode == GenerationMode.I2VA:
        return (
            f"For the target video, at 0.00 seconds into the target video, "
            f"{pictures[0].label} (from [Shot 1]) is fully referenced."
        )
    if mode == GenerationMode.L2VA:
        return (
            "How the reference pictures align with the target video — "
            f"{pictures[0].label} (from [Shot 1]) aligns with the {duration:.2f}-second mark of the target video."
        )
    if mode == GenerationMode.FL2VA:
        return (
            "How the reference pictures align with the target video — "
            f"Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {duration:.2f}-second mark of the target video."
        )
    return ""


def _image_positions(manifest: list[ReferenceManifestItem]) -> dict[int, int]:
    """Presentation slot of every reference image, 1-based in manifest order.

    The H3 text encoder presents all reference images to Qwen as ``<Picture i>``
    in request order regardless of their Shine role, so subject definitions must
    cite this shared index instead of a per-family counter.
    """
    positions: dict[int, int] = {}
    index = 0
    for item in manifest:
        if item.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}:
            index += 1
            positions[id(item)] = index
    return positions


_ORDINALS = (
    "first", "second", "third", "fourth", "fifth", "sixth",
    "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth",
)


def _ordinal(index: int) -> str:
    return _ORDINALS[index] if 0 <= index < len(_ORDINALS) else f"number {index + 1}"


def _subject_definitions(ctx: _Context) -> str:
    lines: list[str] = []
    positions = _image_positions(ctx.manifest)
    # The manifest is family-ordered by the server, so the subject sequence is
    # the sequence the storyboard lists the cast in.  The compiled prompt states
    # that order explicitly: H3 sees the reference images as one flat sequence,
    # and "approved subject" alone gives it no way to tell which face belongs to
    # which described person.
    subject_index = 0
    for item in ctx.manifest:
        if item.use_as == ReferenceUse.SUBJECT:
            role = item.role or item.entity_id or ""
            if re.search(r"[\u3400-\u9fff]", role):
                # The prompt is English; a Chinese name/role cannot be written
                # outside a quoted visible-text span.  The cast order still
                # identifies the person deterministically.
                role = ""
            identity = f"the {_ordinal(subject_index)} listed performer"
            if role:
                identity += f" ({role})"
            if ctx.thin_shell:
                # One binding sentence.  The reference image already fixes the
                # face and the costume, so the prompt spends no words
                # re-describing appearance that could compete with it.
                if ctx.anchored_v21:
                    lines.append(
                        f"{item.label} is {identity}, shown in <Picture {positions[id(item)]}>; "
                        "keep that exact reference appearance, face, hairstyle and outfit throughout."
                    )
                else:
                    lines.append(
                        f"{item.label} is {identity}, shown in <Picture {positions[id(item)]}>; "
                        "keep that exact reference appearance throughout."
                    )
                subject_index += 1
                continue
            appearance_note = (
                ", build, and reference appearance"
                if not ctx.legacy_sound and not ctx.pre_anchor
                else " and build"
            )
            lines.append(
                f"{item.label} is the approved, locked reference for {identity}, "
                f"shown in <Picture {positions[id(item)]}>; this exact face, hairstyle{appearance_note} "
                "must be kept in every frame of this shot."
            )
            subject_index += 1
        elif item.use_as == ReferenceUse.PICTURE:
            last_frame_cont = ctx.continuity_color_v24 and _has_last_frame_continuity(ctx)
            if item.frame_role:
                lines.append(f"{item.label} is the {item.frame_role.value} reference for [Shot 1].")
            elif ctx.anchored_v21 and item.role == "continuity_derivative":
                if last_frame_cont:
                    lines.append(
                        f"{item.label} is the preceding-shot final-frame anchor for [Shot 1] at 0.0s, "
                        "establishing the exact opening framing, exposure, white balance, color grading "
                        "and shadow depth."
                    )
                else:
                    lines.append(f"{item.label} is the preceding-shot final-frame continuity reference for [Shot 1].")
            elif ctx.anchored_v21 and item.asset_role == "prop":
                lines.append(
                    f"{item.label} is a prop appearance reference for [Shot 1]; "
                    "keep this object's design and details consistent when shown."
                )
            elif ctx.anchored_v21 and item.asset_role == "scene":
                if last_frame_cont:
                    lines.append(
                        f"{item.label} is the architectural and spatial layout reference for [Shot 1]; "
                        "use it for room geometry and materials only, without overriding the lighting, "
                        "exposure or color temperature of the preceding shot's final frame."
                    )
                else:
                    lines.append(
                        f"{item.label} is the empty environment and lighting reference for [Shot 1]; "
                        "use it for the setting, architecture and atmosphere only."
                    )
            else:
                lines.append(f"{item.label} is the composition reference for [Shot 1].")
        elif item.use_as == ReferenceUse.VIDEO:
            if ctx.thin_shell:
                lines.append(f"{item.label} is a reference for motion, framing or continuation.")
            else:
                lines.append(f"{item.label} is an approved video reference for motion, framing, or continuation.")
        else:
            if ctx.legacy_audio:
                lines.append(f"{item.label} is an approved audio reference.")
            elif ctx.legacy_sound:
                purpose = "non-vocal ambience"
                if item.role != "continuity_derivative" and ctx.spec.dialogue:
                    purpose = "voice timbre for scripted lines and non-vocal sound"
                lines.append(f"{item.label} is a reference for {purpose} only.")
            elif item.role != "continuity_derivative" and ctx.spec.dialogue:
                lines.append(
                    f"{item.label} is a reference for the scripted lines' voice timbre and non-vocal sound only."
                )
            else:
                lines.append(f"{item.label} is a reference for non-vocal ambience only.")
    return "\n".join(lines)


def _cast_order(ctx: _Context) -> str:
    """State the one-to-one cast/reference order H3 cannot infer on its own.

    The storyboard lists the people of a shot in a stable order (the directorial
    plan and the shot bindings share it), and the reference manifest keeps that
    order.  Saying so is what stops the model from handing one performer's face
    to another when several subjects appear together.
    """
    subjects = [item for item in ctx.manifest if item.use_as == ReferenceUse.SUBJECT]
    if len(subjects) < 2:
        return ""
    if ctx.thin_shell:
        cast = ", ".join(f"{item.label} ({_ordinal(index)})" for index, item in enumerate(subjects))
        return f"Cast order: {cast}; each person keeps exactly their own reference identity."
    cast = ", ".join(
        f"{item.label} ({_ordinal(index)})" for index, item in enumerate(subjects)
    )
    return (
        "Reference cast order (mandatory): the people of this shot appear in the order they are "
        f"described, and that order is {cast}. The first described person is {subjects[0].label}, "
        f"the second is {subjects[1].label}, and so on. Each person keeps exactly their own face, "
        "hairstyle, age and build from their reference; never swap, merge or borrow a face between "
        "two referenced subjects."
    )


def _is_continuation_video(item: ReferenceManifestItem, ctx: _Context) -> bool:
    """A video is a true continuation only when the shot declares video-reference
    continuity AND the bound video is the continuity derivative (plan P0-D / R4).
    A video bound merely as a motion/pacing reference is NOT a continuation."""
    mode, _ = _continuity_mode(ctx.spec.continuity)
    return (
        item.use_as == ReferenceUse.VIDEO
        and item.role == "continuity_derivative"
        and mode == ContinuityMode.VIDEO_REFERENCE
    )


def _summary(ctx: _Context) -> str:
    task_types = ["reference generation"]
    if ctx.legacy_semantics:
        # Historical behaviour: any video reference was labelled continuation.
        if any(item.use_as == ReferenceUse.VIDEO for item in ctx.manifest):
            task_types.append("video continuation")
    else:
        # Task types follow the actual role each reference plays (official ref-en
        # §3): a concrete frame anchor is keyframe completion; only a true
        # continuation is video continuation; a motion reference stays reference
        # generation.  The mere presence of video/audio creates no task type.
        if any(item.use_as == ReferenceUse.PICTURE and item.frame_role for item in ctx.manifest):
            task_types.append("keyframe completion")
        if any(_is_continuation_video(item, ctx) for item in ctx.manifest):
            task_types.append("video continuation")
    if any(item.use_as == ReferenceUse.AUDIO for item in ctx.manifest):
        # The product references timbre/ambience; it never copies the source signal.
        task_types.append("audio reference")
    labels = ", ".join(item.label for item in ctx.manifest)
    medium = ""
    if not ctx.legacy_style and ctx.style.film_type is not None:
        if ctx.thin_shell:
            medium = f"{film_type_sentence(ctx.style.film_type)} Keep this medium through every frame and internal cut."
            if not any(item.frame_role for item in ctx.manifest):
                medium += (" Render the references' identities, composition or motion in that frozen medium "
                           "even when the source uses another medium.")
            medium += " "
        else:
            medium = (
                f"{film_type_sentence(ctx.style.film_type)} "
                "Keep this medium through every frame and internal cut. "
                "Use visual references for the identities, composition or motion assigned below. "
            )
            if not any(item.frame_role for item in ctx.manifest):
                medium += "Render those features in the frozen medium even when the source uses another medium. "
    if ctx.thin_shell:
        # The task types and the labels are the contract; the sentence that
        # said the video "adapts the frozen story beat" told the model nothing.
        return f"[{' + '.join(task_types)}] {medium}Reference labels used: {labels}."
    return (
        f"[{' + '.join(task_types)}] The target video adapts the frozen story beat within the locked Style Bible. "
        f"{medium}Reference labels used: {labels}."
    )


def _retention(ctx: _Context) -> str:
    lines: list[str] = []
    subject_index = 0
    for item in ctx.manifest:
        marker = "reference" if item.use_as == ReferenceUse.AUDIO else "fully_preserved"
        if item.use_as == ReferenceUse.SUBJECT:
            if ctx.thin_shell:
                if ctx.anchored_v21:
                    lines.append(f"{item.label}: {marker} - the same person in the same outfit in every frame.")
                else:
                    lines.append(f"{item.label}: {marker} - the same person in every frame.")
                subject_index += 1
                continue
            lines.append(
                f"{item.label} (the {_ordinal(subject_index)} listed performer in [Shot 1]): "
                f"{marker} - this exact face, hairstyle and build are retained and are never "
                "replaced by another subject's."
            )
            subject_index += 1
        elif item.use_as == ReferenceUse.PICTURE:
            last_frame_cont = ctx.continuity_color_v24 and _has_last_frame_continuity(ctx)
            if ctx.legacy_semantics:
                lines.append(f"{item.label} ([Shot 1] frame): {marker} - the approved frame composition is retained.")
            elif item.frame_role:
                lines.append(
                    f"{item.label} ([Shot 1] {item.frame_role.value} frame): fully_preserved - "
                    "the approved frame composition is retained."
                )
            elif ctx.anchored_v21 and item.role == "continuity_derivative":
                mode, _ = _continuity_mode(ctx.spec.continuity)
                if last_frame_cont and mode == ContinuityMode.LAST_FRAME:
                    lines.append(
                        f"{item.label} ([Shot 1] opening continuity anchor): fully_preserved - "
                        "locks the 0.0s framing, exposure, color temperature, white balance, character posture "
                        "and held prop placement from the preceding shot's final frame, then advances "
                        "smoothly forward in time."
                    )
                elif ctx.handoff_v23 and mode == ContinuityMode.LAST_FRAME:
                    lines.append(
                        f"{item.label} ([Shot 1] continuity reference): partially_preserved - "
                        "guides the opening framing, lighting, character posture and held prop placement at 0.0s "
                        "from the preceding shot's final frame before transitioning into the new action."
                    )
                else:
                    lines.append(
                        f"{item.label} ([Shot 1] continuity reference): partially_preserved - "
                        "guides the opening framing and lighting from the preceding shot's final frame."
                    )
            elif ctx.anchored_v21 and item.asset_role == "prop":
                lines.append(
                    f"{item.label} ([Shot 1] prop reference): fully_preserved - "
                    "the prop keeps its reference appearance whenever visible."
                )
            elif ctx.anchored_v21 and item.asset_role == "scene":
                if last_frame_cont:
                    lines.append(
                        f"{item.label} ([Shot 1] architectural reference): partially_preserved - "
                        "guides the setting geometry and materials only while preserving the current scene's "
                        "lighting and color grade."
                    )
                else:
                    lines.append(
                        f"{item.label} ([Shot 1] environment reference): partially_preserved - "
                        "guides the setting, architecture and lighting while the action proceeds."
                    )
            else:
                if ctx.thin_shell:
                    lines.append(
                        f"{item.label} ([Shot 1] composition reference): partially_preserved - "
                        "guides the frame while the action proceeds."
                    )
                else:
                    lines.append(
                        f"{item.label} ([Shot 1] composition reference): partially_preserved - "
                        "the approved composition and scene layout guide the frame while the action proceeds."
                    )
        elif item.use_as == ReferenceUse.VIDEO:
            visual_track = "" if ctx.legacy_audio else " Use the visual track only."
            if ctx.legacy_semantics:
                lines.append(
                    f"{item.label} (continuation structure): {marker} - the approved motion and framing are retained."
                    + visual_track
                )
            elif _is_continuation_video(item, ctx):
                lines.append(
                    f"{item.label} (continuation structure): fully_preserved - "
                    "the approved motion and framing are retained." + visual_track
                )
            elif ctx.thin_shell:
                lines.append(
                    f"{item.label} (motion and pacing reference): attribute_transfer - "
                    "the referenced camera motion and rhythm transfer to the new subjects." + visual_track
                )
            else:
                lines.append(
                    f"{item.label} (motion and pacing reference): attribute_transfer - "
                    "the referenced camera motion and rhythm are transferred to the new subjects." + visual_track
                )
        else:
            if ctx.legacy_audio:
                lines.append(f"{item.label}: {marker} - the approved audio signal or timbre is referenced.")
            elif ctx.legacy_sound:
                lines.append(f"{item.label}: {marker} - never carry over any words or vocals.")
            elif ctx.spec.dialogue and item.role != "continuity_derivative":
                lines.append(
                    f"{item.label}: {marker} - the approved timbre voices only the scripted dialogue "
                    "lines of this shot."
                )
            else:
                lines.append(f"{item.label}: {marker} - referenced only for non-vocal ambience.")
    return "\n".join(lines)


def _used_labels(text: str) -> set[str]:
    return {match.group(0) for match in LABEL_PATTERN.finditer(text)}


class H3PromptCompiler:
    """Deterministic compiler for the official H3 prompt formats.

    It accepts a structured ShotSpec and a request-local reference manifest;
    it never accepts free-form final prompts from the agent.
    """

    def compile(
        self,
        *,
        spec: ShotSpec,
        style: StyleBible,
        manifest: list[ReferenceManifestItem],
        project_language: str = "Chinese",
        mode_hint: GenerationMode | None = None,
        compiler_version: str = COMPILER_VERSION,
    ) -> CompiledPrompt:
        legacy_audio = compiler_version == LEGACY_AUDIO_COMPILER_VERSION
        legacy_style = compiler_version in (LEGACY_AUDIO_COMPILER_VERSION, LEGACY_STYLE_COMPILER_VERSION)
        legacy_sound = compiler_version in (
            LEGACY_AUDIO_COMPILER_VERSION, LEGACY_STYLE_COMPILER_VERSION, LEGACY_SOUND_COMPILER_VERSION
        )
        pre_anchor = compiler_version == LEGACY_PRE_ANCHOR_COMPILER_VERSION
        legacy_semantics = compiler_version in LEGACY_SEMANTIC_VERSIONS
        pre_sanitize = compiler_version == LEGACY_PRE_SANITIZE_COMPILER_VERSION
        thin_shell = compiler_version not in LEGACY_SHELL_VERSIONS
        anchored_v21 = compiler_version not in LEGACY_200_VERSIONS
        scoped_facts = compiler_version not in LEGACY_210_VERSIONS
        handoff_v23 = compiler_version not in LEGACY_220_VERSIONS
        continuity_color_v24 = compiler_version not in LEGACY_230_VERSIONS
        acting_v25 = compiler_version not in LEGACY_240_VERSIONS
        continuity_clean_v26 = compiler_version not in LEGACY_250_VERSIONS
        _validate_manifest(manifest)
        mode = _infer_mode(manifest, mode_hint)
        _check_frame_roles(mode, [item for item in manifest if item.use_as == ReferenceUse.PICTURE])
        speaker_labels, speaker_order = _speaker_mapping(spec)
        # The prompt is English.  The spoken language is therefore stated in its
        # canonical English name; a project setting written in another script
        # falls back to the default instead of breaking the rule pack.
        spoken_language = canonical_spoken_language(project_language)
        language_fallback = spoken_language is None
        if spoken_language is None:
            spoken_language = DEFAULT_SPOKEN_LANGUAGE
        ctx = _Context(
            spec=spec,
            style=style,
            manifest=manifest,
            label_to_asset={item.label: item.asset_id for item in manifest},
            asset_roles={item.asset_id: item.role or "" for item in manifest},
            speaker_labels=speaker_labels,
            speaker_order=speaker_order,
            project_language=spoken_language,
            legacy_audio=legacy_audio,
            legacy_style=legacy_style,
            legacy_sound=legacy_sound,
            pre_anchor=pre_anchor,
            legacy_semantics=legacy_semantics,
            pre_sanitize=pre_sanitize,
            thin_shell=thin_shell,
            anchored_v21=anchored_v21,
            scoped_facts=scoped_facts,
            handoff_v23=handoff_v23,
            continuity_color_v24=continuity_color_v24,
            acting_v25=acting_v25,
            continuity_clean_v26=continuity_clean_v26,
        )
        if language_fallback:
            ctx.warnings.append(
                "The project language cannot be written in the English prompt; "
                f"the shot is spoken in {spoken_language} instead."
            )
        if mode == GenerationMode.REF2VA:
            _validate_dialogue(ctx, mode)
        else:
            if any(line.end_s > spec.duration_target_s for line in spec.dialogue):
                raise PromptCompilationError("H3_DIALOGUE_TIMING", "Dialogue exceeds the target shot duration")
        if not spec.style_version_id:
            raise PromptCompilationError("H3_STYLE_MISSING", "A frozen style version is required")
        if mode == GenerationMode.REF2VA and not manifest:
            raise PromptCompilationError("H3_MODE_REFERENCE_MISMATCH", "Ref2VA requires reference files")

        sections: dict[str, str] = {}
        shots = _shot_text_thin(ctx) if thin_shell else _shot_text(ctx)
        if mode in {GenerationMode.T2VA, GenerationMode.I2VA, GenerationMode.FL2VA, GenerationMode.L2VA}:
            sections["integrated_multimodal_description"] = shots
            sections["overall_soundscape"] = _soundscape(
                spec, style, spoken_language, legacy_sound=legacy_sound, pre_anchor=pre_anchor
            )
            sections["non_diegetic_music"] = _music(spec, legacy_audio=legacy_audio, legacy_sound=legacy_sound)
            instruction = _align_instruction(mode, manifest, spec.duration_target_s)
            body = "\n".join(f"{name}: {sections[name]}" for name in BASE_SECTION_ORDER)
            prompt = f"{instruction}\n\n{body}" if instruction else body
            section_order = BASE_SECTION_ORDER
        else:
            sections["subject_definitions"] = _subject_definitions(ctx)
            sections["summary"] = _summary(ctx)
            sections["retention_analysis"] = _retention(ctx)
            sections["detailed_description"] = shots
            sections["overall_soundscape"] = _soundscape(
                spec, style, spoken_language, legacy_sound=legacy_sound, pre_anchor=pre_anchor
            )
            sections["non_diegetic_music"] = _music(spec, legacy_audio=legacy_audio, legacy_sound=legacy_sound)
            prompt = "\n\n".join(f"{name}:\n{sections[name]}" for name in REF_SECTION_ORDER)
            section_order = REF_SECTION_ORDER

        if not legacy_audio and re.findall(r"</?d\s*>", prompt, flags=re.I) != ["<d>", "</d>"] * len(spec.dialogue):
            raise PromptCompilationError("H3_DIALOGUE_TAGS", "Only structured dialogue may emit dialogue tags")

        defined = {item.label for item in manifest}
        # Every reference image is presented to the model as <Picture i> in
        # request order, so subject definitions may cite presentation slots
        # that have no standalone manifest label of their own.
        image_count = len(_image_positions(manifest))
        defined |= {f"<Picture {index}>" for index in range(1, image_count + 1)}
        unresolved = _used_labels(prompt) - defined
        if unresolved:
            raise PromptCompilationError(
                "H3_UNRESOLVED_LABEL",
                "The compiled prompt references labels that are not defined by this request",
                {"labels": sorted(unresolved)},
            )
        for name in section_order:
            if not sections.get(name):
                raise PromptCompilationError("H3_SECTION_MISSING", f"Official section {name} is empty")
        if spoken_language and any(
            _language_tag(line.text, "") != spoken_language for line in spec.dialogue
        ):
            ctx.warnings.append(
                "A dialogue line is written in another language than the project language; "
                "the line is tagged with the project language and the prompt asks for that voice."
            )

        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return CompiledPrompt(
            mode=mode,
            compiler_version=compiler_version if compiler_version in KNOWN_COMPILER_VERSIONS else COMPILER_VERSION,
            rule_version=RULE_VERSION,
            skill_sha256=SKILL_SHA256,
            base_rule_sha256=BASE_RULE_SHA256,
            ref_rule_sha256=REF_RULE_SHA256,
            sections=sections,
            prompt=prompt,
            prompt_sha256=prompt_sha256,
            labels=dict(ctx.label_to_asset),
            warnings=ctx.warnings,
        )

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from shine.asset_identity import character_reference_matches
from shine.h3.spoken_language import (
    DEFAULT_SPOKEN_LANGUAGE,
    canonical_spoken_language,
)
from shine.schemas import (
    CompiledPrompt,
    ContinuityMode,
    DialogueLine,
    FrameRole,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)

# Pinned official skill material from MiniMax-AI/MiniMax-H3 commit
# d21241f0a4b3acbb34c97dae47fa417b7065e438 (skills/h3-prompt-writing).
COMPILER_VERSION = "shine-h3-compiler/1.2.1"
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
    warnings: list[str] = field(default_factory=list)


def _language_tag(text: str, project_language: str) -> str:
    if project_language:
        return project_language
    if re.search(r"[\u4e00-\u9fff]", text):
        return "Chinese"
    return "English"


def _style_sentences(style: StyleBible, style_version_id: str, include_sound: bool = True) -> str:
    sound_clause = (
        f"Performance and sound: {style.sound_performance}."
        if include_sound
        else "Performance and sound: no spoken dialogue in this shot."
    )
    return (
        "Visual style constraints from the frozen Style Bible "
        f"(version {style_version_id}). Photography language: {style.photography_language}. "
        f"Blocking: {style.shot_blocking}. Color palette: {style.color_palette}. "
        f"Lighting and tone: {style.lighting_tone}. Lens and texture: {style.lens_texture}. "
        f"Editing rhythm: {style.editing_rhythm}. {sound_clause}"
    )


def _prompt_style(spec: ShotSpec, style: StyleBible) -> StyleBible:
    # The translated draft is part of the shot revision; the original frozen
    # style and its hash remain authoritative and unchanged.
    return spec.prompt_style or style


def _format_timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


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


def _label_for_character(
    character: str, manifest: list[ReferenceManifestItem], style_roles: dict[str, str]
) -> str | None:
    for item in manifest:
        if item.use_as not in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}:
            continue
        if (item.asset_id == character or item.role == character
                or character_reference_matches(character, item.entity_id)):
            return item.label
        if item.role and style_roles.get(item.role) == character:
            return item.label
    return None


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
            if not _label_for_character(speaker, ctx.manifest, {}):
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
        for character in ctx.spec.characters:
            if not _label_for_character(character, ctx.manifest, {}):
                raise PromptCompilationError(
                    "H3_CHARACTER_REFERENCE_MISSING",
                    f"Character {character} appears in this shot but has no bound subject reference",
                    {"character": character, "characters": ctx.spec.characters},
                )


def _dialogue_sentence(ctx: _Context, line: DialogueLine) -> str:
    language = _language_tag(line.text, ctx.project_language)
    speakers = _speakers_of(line)
    labels = [label for label in (_label_for_character(speaker, ctx.manifest, {}) for speaker in speakers) if label]
    subject = ", ".join(labels) if labels else "The speaker"
    speaker = ", ".join(ctx.speaker_labels[name] for name in speakers)
    return (
        f"At {_format_timestamp(line.start_s)}, {subject} ({speaker}) says: "
        f"<d>[{language}] {line.text}</d>"
    )


def _camera_sentence(spec: ShotSpec) -> str:
    camera = spec.camera or {}
    if not camera:
        return ""
    preferred = ("shot_size", "movement", "angle", "axis_rule", "lens")
    parts = [f"{key.replace('_', ' ')}: {camera[key]}" for key in preferred if camera.get(key)]
    parts.extend(f"{key.replace('_', ' ')}: {value}" for key, value in camera.items() if key not in preferred)
    return "Camera: " + "; ".join(parts) + "." if parts else ""


def _shot_text(ctx: _Context) -> str:
    parts: list[str] = ["[Shot 1] " + _style_sentences(
        _prompt_style(ctx.spec, ctx.style), ctx.spec.style_version_id,
        include_sound=bool(ctx.spec.dialogue),
    )]
    cast = _cast_order(ctx)
    if cast:
        parts.append(cast)
    parts.append(ctx.spec.story_beat.strip())
    camera = _camera_sentence(ctx.spec)
    if camera:
        parts.append(camera)
    environment = ctx.spec.sound.get("environment")
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
        parts.append(
            "Story continuity facts below are background context only; they are not dialogue and must "
            "never be spoken, narrated or performed as voice-over: " + continuity_notes
        )
    for line in sorted(ctx.spec.dialogue, key=lambda item: item.start_s):
        parts.append(_dialogue_sentence(ctx, line))
    continuity = ctx.spec.continuity
    continuation = next((ref for ref in ctx.manifest if ref.role == "continuity_derivative"), None)
    if continuation:
        if continuity.mode == ContinuityMode.LAST_FRAME:
            parts.append(f"Use {continuation.label}, the preceding shot's final frame, as a soft visual reference "
                         "for the opening composition.")
        elif continuity.mode == ContinuityMode.HARD_FRAME_ANCHOR:
            parts.append(f"Use {continuation.label}, the preceding shot's final frame, as the opening frame.")
        elif continuity.mode == ContinuityMode.VIDEO_REFERENCE:
            parts.append(f"Continue the motion and framing in {continuation.label}, the source shot's video.")
        elif continuity.mode == ContinuityMode.AUDIO_REFERENCE:
            parts.append(f"Continue the sound bed in {continuation.label}, the source shot's audio.")
    elif continuity.mode == ContinuityMode.LAST_FRAME and continuity.previous_shot_id:
        parts.append(
            f"The opening composition continues seamlessly from the approved final frame of shot "
            f"{continuity.previous_shot_id}."
        )
    elif continuity.mode == ContinuityMode.VIDEO_REFERENCE and continuity.previous_shot_id:
        parts.append(
            f"The shot continues the motion and framing of the approved video from shot "
            f"{continuity.previous_shot_id}."
        )
    elif continuity.mode == ContinuityMode.AUDIO_REFERENCE and continuity.previous_shot_id:
        parts.append("The audio bed continues uninterrupted from the previous approved shot.")
    return " ".join(part.strip() for part in parts if part.strip())


def _spoken_language_sentence(spec: ShotSpec, project_language: str) -> str:
    """The spoken-language contract of the shot.

    The whole prompt is written in English, so the language of the voice has to
    be stated explicitly.  A per-line language tag alone is not enough: the
    model invents English speech when nothing tells it which voice to use, and
    it invents speech at all when a silent shot never says so.

    The language is written in its canonical English name: the official rule
    pack rejects CJK outside the dialogue tags, so a project setting written in
    another script may never be copied into the prompt.
    """
    if not spec.dialogue:
        return (
            "There is no spoken dialogue in this shot: do not produce speech, narration, voice-over, "
            "whispering, mumbling, singing, humming or vocal sounds of any kind. Any speech or voice "
            "mentioned in story facts or visual descriptions is context only, not an audio instruction."
        )
    language = (project_language or "").strip()
    if not language:
        return ""
    return (
        f"Only the explicitly tagged dialogue lines in this shot may be spoken in {language}, matching "
        "the original line text exactly; no other language, unscripted speech, narration, voice-over, "
        "background chatter, whispering or extra words may appear. Any voice or reading described in "
        "the story facts or visual description refers only to these exact lines."
    )


def _soundscape(spec: ShotSpec, style: StyleBible, project_language: str = "") -> str:
    style = _prompt_style(spec, style)
    environment = spec.sound.get("environment", "").strip()
    ambience = spec.sound.get("ambience", "").strip()
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
            ]
    else:
        pieces = [piece.rstrip(".") + "." for piece in (environment, ambience) if piece]
        sentences = pieces
    spoken = _spoken_language_sentence(spec, project_language)
    if spoken:
        sentences.append(spoken)
    return " ".join(sentences)


def _music(spec: ShotSpec) -> str:
    policy = (spec.sound.get("music") or spec.sound.get("music_policy") or "").strip()
    if not policy:
        return "N/A"
    lowered = policy.lower()
    if lowered in {"none", "silent", "n/a", "no_music"}:
        return "N/A"
    if lowered in {"low_or_none", "low"}:
        return "Sparse low strings at a slow tempo, kept well below the dialogue and fading out before the final frame."
    return policy if policy.endswith(".") else policy + "."


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
            lines.append(
                f"{item.label} is the approved, locked reference for {identity}, "
                f"shown in <Picture {positions[id(item)]}>; this exact face, hairstyle and build "
                "must be kept in every frame of this shot."
            )
            subject_index += 1
        elif item.use_as == ReferenceUse.PICTURE:
            frame = item.frame_role.value if item.frame_role else "composition"
            lines.append(f"{item.label} is the {frame} reference for [Shot 1].")
        elif item.use_as == ReferenceUse.VIDEO:
            lines.append(f"{item.label} is an approved video reference for motion, framing, or continuation.")
        else:
            lines.append(f"{item.label} is an approved audio reference.")
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


def _summary(ctx: _Context) -> str:
    task_types = ["reference generation"]
    if any(item.use_as == ReferenceUse.VIDEO for item in ctx.manifest):
        task_types.append("video continuation")
    if any(item.use_as == ReferenceUse.AUDIO for item in ctx.manifest):
        task_types.append("audio reference")
    labels = ", ".join(item.label for item in ctx.manifest)
    return (
        f"[{' + '.join(task_types)}] The target video adapts the frozen story beat within the locked Style Bible. "
        f"Reference labels used: {labels}."
    )


def _retention(ctx: _Context) -> str:
    lines: list[str] = []
    subject_index = 0
    for item in ctx.manifest:
        marker = "reference" if item.use_as == ReferenceUse.AUDIO else "fully_preserved"
        if item.use_as == ReferenceUse.SUBJECT:
            lines.append(
                f"{item.label} (the {_ordinal(subject_index)} listed performer in [Shot 1]): "
                f"{marker} - this exact face, hairstyle and build are retained and are never "
                "replaced by another subject's."
            )
            subject_index += 1
        elif item.use_as == ReferenceUse.PICTURE:
            lines.append(f"{item.label} ([Shot 1] frame): {marker} - the approved frame composition is retained.")
        elif item.use_as == ReferenceUse.VIDEO:
            lines.append(
                f"{item.label} (continuation structure): {marker} - the approved motion and framing are retained."
            )
        else:
            lines.append(f"{item.label}: {marker} - the approved audio signal or timbre is referenced.")
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
    ) -> CompiledPrompt:
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
        shots = _shot_text(ctx)
        if mode in {GenerationMode.T2VA, GenerationMode.I2VA, GenerationMode.FL2VA, GenerationMode.L2VA}:
            sections["integrated_multimodal_description"] = shots
            sections["overall_soundscape"] = _soundscape(spec, style, spoken_language)
            sections["non_diegetic_music"] = _music(spec)
            instruction = _align_instruction(mode, manifest, spec.duration_target_s)
            body = "\n".join(f"{name}: {sections[name]}" for name in BASE_SECTION_ORDER)
            prompt = f"{instruction}\n\n{body}" if instruction else body
            section_order = BASE_SECTION_ORDER
        else:
            sections["subject_definitions"] = _subject_definitions(ctx)
            sections["summary"] = _summary(ctx)
            sections["retention_analysis"] = _retention(ctx)
            sections["detailed_description"] = shots
            sections["overall_soundscape"] = _soundscape(spec, style, spoken_language)
            sections["non_diegetic_music"] = _music(spec)
            prompt = "\n\n".join(f"{name}:\n{sections[name]}" for name in REF_SECTION_ORDER)
            section_order = REF_SECTION_ORDER

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
            compiler_version=COMPILER_VERSION,
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

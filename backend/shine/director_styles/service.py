"""Persistence helpers for draft-only selection and immutable frozen bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director_styles.catalog import CatalogEntry, canonical_sha256
from shine.director_styles.contracts import DirectorStyleProfile, StyleResolution
from shine.director_styles.resolver import DirectorStyleResolver
from shine.models import DirectorStyleProfileRevision
from shine.schemas import FilmType, StyleBible

DEFAULT_STYLE_BIBLE_ZH: dict[str, str] = {
    "photography_language": "以中景和近景叙事，机位接近人物视线；镜头稳定，仅在叙事需要时缓慢推进，避免无动机环绕。",
    "shot_blocking": "保持人物空间关系和视线方向；对话正反打遵守同一动作轴线，走位服务于剧情。",
    "color_palette": "自然中性色彩，肤色真实；同一场景保持主辅色和冷暖关系一致，避免过度饱和。",
    "lighting_tone": "使用有明确来源的自然柔光，保留阴影细节，跨镜主光方向一致，避免高光溢出。",
    "lens_texture": "干净写实，适度景深与清晰度；保留皮肤纹理，避免塑料肤质、过锐和突变颗粒。",
    "editing_rhythm": "均衡叙事，按动作和对白安排切点；保留必要停顿，默认硬切，不为追求节奏截断对白。",
    "sound_performance": "使用项目指定语言和已确认音色；自然语速、清楚对白、适量环境声，音乐不遮盖人声。",
}

DEFAULT_STYLE_BIBLE_EN: dict[str, str] = {
    "photography_language": "Eye-level medium and close framing with a stable camera and motivated movement only.",
    "shot_blocking": "Keep clear spatial geography, grounded posture, and consistent eye-line axis across shots.",
    "color_palette": "Coherent natural colors with accurate character tones and balanced contrast.",
    "lighting_tone": "Motivated directional light with legible facial detail and preserved shadow depth.",
    "lens_texture": "Clean optical clarity with natural surface detail and readable silhouettes.",
    "editing_rhythm": "Measured narrative pacing cut on completed actions and natural dialogue breaths.",
    "sound_performance": "Project spoken language for timed dialogue, low room ambience, and non-intrusive music.",
}

_ZH_TO_EN_STYLE_DIMENSIONS: dict[str, dict[str, str]] = {
    "photography_language": {
        "以中景和近景叙事，机位接近人物视线；镜头稳定，仅在叙事需要时缓慢推进，避免无动机环绕。": (
            "Eye-level medium and close framing with a stable camera and motivated slow push-ins only."
        ),
        "以中景、近景与空镜结合，机位多平视或轻微仰俯；缓慢推进与横移，禁止无动机环绕。": (
            "Eye-level medium, close, and atmospheric shots with measured push-ins "
            "and lateral tracks; no unmotivated orbit."
        ),
        "以中近景和特写为主，机位贴近视线，手持感轻微；仅在情绪高点缓慢推进，禁止无动机环绕。": (
            "Close and medium-close framing near eye level with subtle natural poise "
            "and slow emotional push-ins."
        ),
        "机位稳定，产品与人物并重，景别对比明确；环绕展示仅在本机工作流能力已验证时使用，否则以平移与固定机位替代。": (
            "Stable tripod framing balancing subject and object clarity with "
            "deliberate shot-scale contrast."
        ),
    },
    "shot_blocking": {
        "保持人物空间关系和视线方向；对话正反打遵守同一动作轴线，走位服务于剧情。": (
            "Preserve spatial geography, eye-lines, and the 180-degree dialogue axis "
            "with story-driven blocking."
        ),
        "单人聚焦与对角构图为主，对话正反打克制；保持轴线稳定，利用前景遮挡制造不安。": (
            "Diagonal single-subject staging with restrained reverse angles, steady "
            "axis, and foreground depth."
        ),
        "人物同框与正反打并用；同场镜头保持人物方位一致，走位自然。": (
            "Two-shot and reverse-angle staging keeping consistent screen positions "
            "and natural blocking."
        ),
        "人物站位符合身份礼序，正反打保守；保持轴线与人物朝向一致，走位含蓄。": (
            "Measured staging honoring posture, screen direction, and consistent "
            "character facing."
        ),
    },
    "color_palette": {
        "自然中性色彩，肤色真实；同一场景保持主辅色和冷暖关系一致，避免过度饱和。": (
            "Natural neutral colors with accurate skin tones and consistent scene "
            "warmth without oversaturation."
        ),
        "暖色调、柔和饱和度，肤色红润真实；场景主色统一，跨镜冷暖关系一致，避免过度饱和。": (
            "Warm softly saturated palette with natural complexion and consistent "
            "scene color harmony."
        ),
        "冷色低饱和，青蓝与灰绿主导，肤色偏冷但真实；跨镜保持同一冷基调，避免暖色突兀。": (
            "Muted cool cyan-slate palette with natural complexion and steady "
            "cross-shot tone."
        ),
        "明亮清透，主色鲜明、辅色克制；肤色明亮真实，跨镜色彩一致，避免杂色干扰。": (
            "Bright clean palette with clear primary contrast and consistent "
            "accurate complexion."
        ),
    },
    "lighting_tone": {
        "使用有明确来源的自然柔光，保留阴影细节，跨镜主光方向一致，避免高光溢出。": (
            "Motivated soft natural key light with preserved shadow detail and "
            "consistent direction across shots."
        ),
        "明亮柔和的低反差光，日光感明确；保留阴影细节，无死黑。": (
            "Bright soft low-contrast daylight with clean shadow legibility and "
            "no crushed blacks."
        ),
        "低调布光，明暗比大但保留暗部细节；侧光和边缘光塑造立体感，主光方向跨镜一致。": (
            "Low-key directional lighting with side and rim separation, clear "
            "shadow detail, and steady key side."
        ),
        "使用有明确方向的侧光塑造人物立体感，反差较强但保留暗部细节，跨镜主光方向一致。": (
            "Directional side key light sculpting form while keeping facial "
            "features and shadows legible."
        ),
    },
    "lens_texture": {
        "干净写实，适度景深与清晰度；保留皮肤纹理，避免塑料肤质、过锐和突变颗粒。": (
            "Clean optical depth and natural surface clarity without oversharpening "
            "or synthetic smoothing."
        ),
        "柔和浅景深，轻微暖柔；皮肤质感真实，避免塑料肤质和过锐。": (
            "Soft shallow depth of field with gentle warmth and natural surface texture."
        ),
        "柔和适度的胶片感，颗粒细腻；保留织物与皮肤细节，避免过锐。": (
            "Restrained fine-grain optical texture preserving fabric and facial "
            "detail without harsh sharpening."
        ),
        "清晰展示质感，高清晰度、深景深；皮肤干净真实，避免塑料肤质。": (
            "Crisp deep-focus texture rendering material and surface details cleanly."
        ),
    },
    "editing_rhythm": {
        "均衡叙事，按动作和对白安排切点；保留必要停顿，默认硬切，不为追求节奏截断对白。": (
            "Balanced cutting on completed actions and dialogue breaths with clean "
            "hard cuts."
        ),
        "舒缓节奏，切点宽松，保留生活动作的完整过程；默认硬切。": (
            "Unhurried editorial pacing letting physical actions settle cleanly "
            "with hard cuts."
        ),
        "紧凑叙事，对白切点稍密，保留情绪停顿；冲突段加快，默认硬切。": (
            "Tight narrative pacing around dialogue exchanges while preserving key "
            "reaction beats."
        ),
        "克制悬疑节奏，镜头保持完整，停顿蓄势；关键信息后留空拍，默认硬切。": (
            "Restrained suspense pacing holding beats after key reveals with clean "
            "hard cuts."
        ),
    },
    "sound_performance": {
        "使用项目指定语言和已确认音色；自然语速、清楚对白、适量环境声，音乐不遮盖人声。": (
            "Project spoken language and confirmed timbre with clear natural "
            "delivery, balanced ambience, and restrained music."
        ),
        "使用项目指定语言和已确认音色；对白清楚靠前，城市环境声适度，音乐只在情绪点进入。": (
            "Project spoken language with foreground clarity, light urban room "
            "tone, and selective music cues."
        ),
        "使用项目指定语言和已确认音色；语速自然偏慢，生活环境声丰富，配乐轻。": (
            "Project spoken language at a calm natural pace with grounded ambient "
            "texture and light score."
        ),
        "使用项目指定语言和已确认音色；语速克制、对白简短，环境声克制且有层次，配乐低频铺底不盖人声。": (
            "Project spoken language with measured phrasing, layered subtle "
            "ambience, and low-level underscore."
        ),
        "使用项目指定语言和已确认音色；语速明快、对白清楚，音乐明快不盖人声，环境声弱化。": (
            "Project spoken language with brisk clear delivery, light score, and "
            "minimal background ambience."
        ),
    },
}


def default_style_bible_for_film_type(film_type: FilmType | str | None = None) -> StyleBible:
    """Return a clean director-neutral Chinese StyleBible for new creations."""
    resolved_film_type = FilmType(film_type) if film_type else None
    return StyleBible.model_validate({
        **DEFAULT_STYLE_BIBLE_ZH,
        "film_type": resolved_film_type,
    })


def default_english_style_bible(style: StyleBible | dict[str, Any] | None = None) -> StyleBible:
    """Project a director-neutral StyleBible into English H3 prose without CJK."""
    if isinstance(style, StyleBible):
        source = style.model_dump(mode="json")
    elif isinstance(style, dict):
        source = dict(style)
    else:
        source = {}
    translated: dict[str, Any] = {}
    for field, fallback in DEFAULT_STYLE_BIBLE_EN.items():
        raw = str(source.get(field) or "").strip()
        if raw and not any("\u3400" <= ch <= "\u9fff" for ch in raw):
            translated[field] = raw
        else:
            translated[field] = _ZH_TO_EN_STYLE_DIMENSIONS.get(field, {}).get(raw, fallback)
    film_type = source.get("film_type")
    if film_type:
        translated["film_type"] = FilmType(film_type)
    if source.get("dialogue_ratio_percent") is not None:
        translated["dialogue_ratio_percent"] = source.get("dialogue_ratio_percent")
    if source.get("narration_level") in {"none", "low", "high"}:
        translated["narration_level"] = source.get("narration_level")
    if source.get("max_shot_seconds") is not None:
        translated["max_shot_seconds"] = source.get("max_shot_seconds")
    return StyleBible.model_validate(translated)

_RESOLVER = DirectorStyleResolver()


def draft_digest(style: dict[str, Any] | None, binding: dict[str, Any] | None = None) -> str:
    """Version both visible style fields and the selected source for DSE CAS."""
    return canonical_sha256({"style": style, "director_style": binding_summary(binding)})


def make_binding(entry: CatalogEntry, strength: str, style: StyleBible) -> dict[str, Any]:
    resolution = _RESOLVER.resolve(entry, strength=strength)
    style_payload = style.model_dump(mode="json")
    return {
        "schema_version": "director-style-binding/1",
        "source": entry.source,
        "profile_id": entry.profile.id,
        "profile_version": entry.profile.version,
        "profile_sha256": entry.sha256,
        "strength": strength,
        "style_bible_snapshot": style_payload,
        "style_bible_sha256": canonical_sha256(style_payload),
        "profile_snapshot": entry.profile.model_dump(mode="json"),
        "preview_resolution": resolution.model_dump(mode="json"),
        "resolution_sha256": resolution.resolution_sha256,
    }


def validate_binding(
    binding: dict[str, Any],
    *,
    expected_style: dict[str, Any] | None = None,
) -> tuple[CatalogEntry, StyleResolution]:
    try:
        if binding.get("schema_version") != "director-style-binding/1":
            raise ValueError("STYLE_PROFILE_INVALID")
        profile = DirectorStyleProfile.model_validate(binding["profile_snapshot"])
        profile_payload = profile.model_dump(mode="json")
        profile_sha = canonical_sha256(profile_payload)
        if (
            profile.id != binding.get("profile_id")
            or profile.version != binding.get("profile_version")
            or profile_sha != binding.get("profile_sha256")
        ):
            raise ValueError("STYLE_PROFILE_STALE")
        style = StyleBible.model_validate(binding["style_bible_snapshot"]).model_dump(mode="json")
        if canonical_sha256(style) != binding.get("style_bible_sha256"):
            raise ValueError("STYLE_RESOLUTION_STALE")
        if expected_style is not None and style != StyleBible.model_validate(expected_style).model_dump(mode="json"):
            raise ValueError("STYLE_DRAFT_CONFLICT")
        entry = CatalogEntry(profile=profile, sha256=profile_sha, source=binding.get("source", "builtin"))
        resolution = _RESOLVER.resolve(entry, strength=binding["strength"])
        saved = StyleResolution.model_validate(binding["preview_resolution"])
        if saved != resolution or binding.get("resolution_sha256") != resolution.resolution_sha256:
            raise ValueError("STYLE_RESOLUTION_STALE")
        return entry, resolution
    except HTTPException:
        raise
    except Exception as exc:  # validate imported JSON snapshots at the trust boundary
        code = str(exc) if str(exc).startswith("STYLE_") else "STYLE_PROFILE_INVALID"
        raise HTTPException(status_code=409, detail=code) from exc


def resolve_frozen_binding(
    binding: dict[str, Any],
    *,
    scene_tags: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    blocked_constraints: Iterable[str] = (),
) -> StyleResolution:
    entry, _ = validate_binding(binding)
    return _RESOLVER.resolve(
        entry,
        strength=binding["strength"],
        scene_tags=scene_tags,
        capabilities=capabilities,
        blocked_constraints=blocked_constraints,
    )


def camera_style_text(resolution: StyleResolution) -> str:
    return " ".join(
        instruction
        for target in ("camera", "lighting", "color", "texture")
        for instruction in resolution.constraints[target]
    )


def sound_style_text(resolution: StyleResolution) -> str:
    return " ".join(resolution.constraints["sound"])


def editing_style_text(resolution: StyleResolution) -> str:
    return " ".join(resolution.constraints["editing"])[:1800]


def bind_editing_style(proposal, profile_id: str, resolution: StyleResolution):
    """Attach deterministic editing guidance without changing order or timing."""
    return proposal.model_copy(update={
        "director_style_id": profile_id,
        "director_style_resolution_sha256": resolution.resolution_sha256,
        "editing_direction": editing_style_text(resolution) or None,
    })


def register_profile_revision(db: Session, entry: CatalogEntry) -> DirectorStyleProfileRevision:
    existing = db.scalar(
        select(DirectorStyleProfileRevision).where(
            DirectorStyleProfileRevision.profile_id == entry.profile.id,
            DirectorStyleProfileRevision.version == entry.profile.version,
        )
    )
    if existing:
        if existing.canonical_sha256 != entry.sha256:
            raise HTTPException(status_code=409, detail="STYLE_PROFILE_VERSION_IMMUTABLE")
        return existing
    row = DirectorStyleProfileRevision(
        profile_id=entry.profile.id,
        version=entry.profile.version,
        source=entry.source,
        canonical_sha256=entry.sha256,
        payload_json=entry.profile.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def binding_summary(binding: dict[str, Any] | None) -> dict[str, Any] | None:
    if not binding:
        return None
    return {
        "profile_id": binding.get("profile_id"),
        "profile_version": binding.get("profile_version"),
        "profile_sha256": binding.get("profile_sha256"),
        "source": binding.get("source"),
        "strength": binding.get("strength"),
        "resolution_sha256": binding.get("resolution_sha256"),
    }


def english_style_bible(binding: dict[str, Any] | None) -> StyleBible | None:
    """Render a frozen director template's English techniques as a StyleBible.

    The official H3 rule accepts only English visual description outside the
    tagged dialogue.  A manually created shot carries no per-shot English
    ``prompt_style``, while the visible Style Bible may be Chinese; the same
    frozen template already declares English ``instruction_en`` for each
    technique, so the compiler can use that faithful projection instead of
    copying CJK into the prompt.  The frozen version and hash stay unchanged.
    """
    if not isinstance(binding, dict):
        return None
    snapshot = binding.get("profile_snapshot")
    if not isinstance(snapshot, dict):
        return None
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for item in snapshot.get("techniques") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "")
        instruction = str(item.get("instruction_en") or "").strip()
        if not target or not instruction:
            continue
        grouped.setdefault(target, []).append(
            (int(item.get("priority") or 0), str(item.get("id") or ""), instruction)
        )
    if not grouped:
        return None
    ordered = {
        target: [text for _priority, _key, text in sorted(items, key=lambda row: (-row[0], row[1]))]
        for target, items in grouped.items()
    }

    def join(target: str, fallback: str) -> str:
        return " ".join(ordered.get(target, [])[:2]) or fallback

    camera = ordered.get("camera", [])
    return StyleBible(
        photography_language=join(
            "camera", "Use restrained, balanced compositions with readable spatial depth."
        ),
        shot_blocking=(
            camera[1] if len(camera) > 1 else join(
                "camera", "Keep the subject's movement direction and screen axis clear."
            )
        ),
        color_palette=join(
            "color", "Use muted, coherent colors with a readable subject-background contrast."
        ),
        lighting_tone=join(
            "lighting", "Use motivated light with legible shadows and facial detail."
        ),
        lens_texture=join(
            "texture", "Preserve natural surface detail and spatial clarity."
        ),
        editing_rhythm=join(
            "editing", "Let each shot hold briefly after its information lands."
        ),
        sound_performance=join(
            "sound", "Keep sparse room tone and low ambient texture; preserve scripted dialogue."
        ),
    )

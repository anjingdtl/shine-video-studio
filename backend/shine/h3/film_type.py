"""Fixed English film-type clauses for the H3 prompt.

The H3 rule pack accepts English visual description outside dialogue tags and
forbids keyword soup.  The film type is therefore a server-owned sentence, not
a user-editable style field: an animation project must not silently become
photoreal because a model omitted the medium.
"""

from __future__ import annotations

from shine.schemas import FilmType

FILM_TYPE_LABELS: dict[FilmType, str] = {
    FilmType.LIVE_ACTION: "仿真人（写实实拍）",
    FilmType.ANIMATION: "动画（2D 赛璐璐）",
}

FILM_TYPE_INSTRUCTIONS: dict[FilmType, str] = {
    FilmType.LIVE_ACTION: (
        "The entire shot is photorealistic live-action footage: real human performers "
        "with natural skin texture and pores, practical sets, physically based lighting "
        "and a real camera lens; no illustration, no cel shading, and no cartoon or "
        "anime rendering."
    ),
    FilmType.ANIMATION: (
        "The entire shot is 2D cel-shaded animation: hand-drawn line art with clean "
        "contours, flat color fills, hard-edged shading and painted backgrounds; no "
        "photorealistic skin, no live-action footage, and no 3D CGI rendering."
    ),
}


FILM_TYPE_SCENE_INSTRUCTIONS: dict[FilmType, str] = {
    FilmType.LIVE_ACTION: (
        "The entire image is a photorealistic live-action film set plate: practical "
        "architecture, authentic weathered materials, physically based lighting and a "
        "real camera lens, with no people anywhere; no illustration, no cel shading, "
        "and no cartoon or anime rendering."
    ),
    FilmType.ANIMATION: (
        "The entire image is a 2D cel-shaded animation background plate: hand-drawn "
        "line art with clean contours, flat color fills, hard-edged shading and "
        "painted set architecture, with no people anywhere; no photorealistic "
        "live-action footage, and no 3D CGI rendering."
    ),
}

FILM_TYPE_PROP_INSTRUCTIONS: dict[FilmType, str] = {
    FilmType.LIVE_ACTION: (
        "The entire image is a photorealistic live-action prop still-life photograph: "
        "authentic physical materials, physically based studio lighting and a real "
        "camera lens, with no person or hands; no illustration, no cel shading, and "
        "no cartoon or anime rendering."
    ),
    FilmType.ANIMATION: (
        "The entire image is a 2D cel-shaded animation prop study: hand-drawn line "
        "art with clean contours, flat color fills and hard-edged shading, with no "
        "person or hands; no photorealistic live-action footage, and no 3D CGI rendering."
    ),
}


def film_type_sentence(film_type: FilmType | None) -> str:
    if film_type is None:
        return ""
    return f"Film type: {FILM_TYPE_INSTRUCTIONS[film_type]}"


def material_film_type_sentence(film_type: FilmType | None, family: str | None = None) -> str:
    """Single source of film-medium prose for material stills, scoped by family.

    Scene plates and standalone props must never carry "real human performers
    with natural skin texture and pores": an image model reads that phrase as
    an instruction to paint people into an empty set or hands onto a prop.
    """
    if film_type is None:
        return ""
    if family == "scene":
        return f"Film type: {FILM_TYPE_SCENE_INSTRUCTIONS[film_type]}"
    if family == "prop":
        return f"Film type: {FILM_TYPE_PROP_INSTRUCTIONS[film_type]}"
    return film_type_sentence(film_type)

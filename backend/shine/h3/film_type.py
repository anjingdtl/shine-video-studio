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


def film_type_sentence(film_type: FilmType | None) -> str:
    if film_type is None:
        return ""
    return f"Film type: {FILM_TYPE_INSTRUCTIONS[film_type]}"

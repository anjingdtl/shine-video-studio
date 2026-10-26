"""Explicit synthetic media-review transport fixtures, never real QC evidence."""

def fixture_style_input_checks():
    """Transport fixtures only; these claims do not inspect real media."""
    return [dict(field=name, status="consistent", detail="Synthetic fixture comparison only") for name in (
        "photography_language", "shot_blocking", "color_palette", "lighting_tone",
        "lens_texture", "editing_rhythm", "sound_performance",
    )]



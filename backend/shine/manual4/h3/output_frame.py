"""Mirrored canonical H3 output frame calculation (from shine/h3/output_frame.py)."""

from __future__ import annotations

from typing import Literal

Orientation = Literal["landscape", "portrait"]

BASELINE: dict[str, tuple[int, int]] = {
    "landscape": (1344, 768),
    "portrait": (768, 1344),
}
ASPECT_TO_ORIENTATION: dict[str, Orientation] = {
    "16:9": "landscape",
    "landscape": "landscape",
    "9:16": "portrait",
    "portrait": "portrait",
}
RESOLUTION_SCALES: tuple[float, ...] = (0.3, 0.5, 0.7, 1.0)
MIN_SIDE = 256
MAX_SIDE = 2048
GRID = 32


def _round_grid(value: float) -> int:
    side = int((value / GRID) + 0.5) * GRID
    return max(MIN_SIDE, min(MAX_SIDE, side))


def orientation_for(aspect_or_orientation: str | None) -> Orientation:
    key = str(aspect_or_orientation or "16:9").strip().lower()
    return ASPECT_TO_ORIENTATION.get(key, "landscape")


def frame_size(orientation_or_aspect: str, scale: float) -> tuple[int, int]:
    orientation = orientation_for(orientation_or_aspect)
    base_w, base_h = BASELINE[orientation]
    return _round_grid(base_w * float(scale)), _round_grid(base_h * float(scale))


def frame_options() -> list[dict[str, object]]:
    options = []
    for orientation in BASELINE:
        for scale in RESOLUTION_SCALES:
            width, height = frame_size(orientation, scale)
            options.append(
                {
                    "orientation": orientation,
                    "aspect_ratio": "16:9" if orientation == "landscape" else "9:16",
                    "scale": scale,
                    "width": width,
                    "height": height,
                }
            )
    return options

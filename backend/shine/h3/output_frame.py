"""The canonical output frame: orientation + resolution scale -> H3 width/height.

This is the single definition behind the director package's frame control.  It
does not introduce a second render path: the pair it produces is exactly the
``OutputSpec.width``/``height`` that the package freezes, the profile gate
checks for a ``width``/``height`` parameter mapping, and the renderer consumes
through ``31.inputs.width``/``height``.  The baseline is the model-native canvas
already pinned in the registered H3 workflows (1344x768 landscape, 768x1344
portrait); a scale tier shrinks that canvas, rounded to H3's 32-pixel grid and
clamped to the schema's 256-2048 window.  The lowest tier therefore clamps its
short side to 256 rather than promising a size H3 cannot render.
"""

from typing import Literal

Orientation = Literal["landscape", "portrait"]

#: Model-native canvas, the same baseline the registered Ref2VA workflows load.
BASELINE: dict[str, tuple[int, int]] = {
    "landscape": (1344, 768),
    "portrait": (768, 1344),
}

#: Resolution tiers relative to the baseline (1.0 = native).  Suggested values;
#: each is rounded to the 32-pixel grid and clamped to the OutputSpec window.
RESOLUTION_SCALES: tuple[float, ...] = (0.3, 0.5, 0.7, 1.0)

MIN_SIDE = 256
MAX_SIDE = 2048
GRID = 32


def _round_grid(value: float) -> int:
    side = int((value / GRID) + 0.5) * GRID
    return max(MIN_SIDE, min(MAX_SIDE, side))


def frame_size(orientation: Orientation, scale: float) -> tuple[int, int]:
    """The (width, height) for one orientation and resolution tier."""
    base_w, base_h = BASELINE[orientation]
    return _round_grid(base_w * scale), _round_grid(base_h * scale)


def frame_options() -> list[dict]:
    """Every selectable frame, for the UI and for tests to pin the same fact."""
    options = []
    for orientation in BASELINE:
        for scale in RESOLUTION_SCALES:
            width, height = frame_size(orientation, scale)
            options.append({
                "orientation": orientation,
                "scale": scale,
                "width": width,
                "height": height,
            })
    return options
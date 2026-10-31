"""Select a supported PDD schedule or the original model's native schedule.

Vendored verbatim from the operator's local
``custom_nodes/shine_h3_sampling_mode.py`` (project-owned file) so a new
machine receives it from the repository rather than a personal cache.
"""


class ShineH3SamplingMode:
    MODES = ["PDD 4 steps", "PDD 8 steps", "Native 20 steps"]

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"mode": (cls.MODES, {"default": "PDD 8 steps"})}}

    RETURN_TYPES = (["8", "4", "6"], "BOOLEAN")
    RETURN_NAMES = ("pdd_nfe", "pdd_enabled")
    FUNCTION = "select"
    CATEGORY = "Shine/H3"

    def select(self, mode):
        if mode not in self.MODES:
            raise ValueError(f"Unsupported H3 sampling mode: {mode}")
        return ("4" if mode == "PDD 4 steps" else "8", mode != "Native 20 steps")


NODE_CLASS_MAPPINGS = {"ShineH3SamplingMode": ShineH3SamplingMode}
NODE_DISPLAY_NAME_MAPPINGS = {"ShineH3SamplingMode": "H3 · 4 / 8 / Native 20 steps"}

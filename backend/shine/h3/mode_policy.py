"""The project's one video-generation mode.

Shine renders reference-driven shots: every shot is described by who and what is
in it, and those references are what keep a performer's face and a location's
look stable from take to take.  T2VA (text-only) has no reference to anchor any
of that, so it is not a mode this project may render in.

The rule is enforced at exactly one boundary — the request builder every real
render goes through — and the package readiness check asks the same question, so
"startable" and "this shot can be queued" cannot disagree.
"""

from __future__ import annotations

from shine.schemas import GenerationMode

REQUIRED_GENERATION_MODE = GenerationMode.REF2VA
MODE_POLICY_ID = "shine-ref2va-only/1.0"

MODE_POLICY_MESSAGE = (
    "本项目只走 Ref2VA：每一镜都必须有参考素材（人物/场景/道具至少一项）。"
    "纯文本 T2VA 及其他插帧模式不允许使用。"
)


class ModePolicyError(ValueError):
    """A request whose generation mode this project does not allow."""

    code = "PROJECT_REQUIRES_REF2VA"

    def __init__(self, actual: GenerationMode | str):
        self.actual = str(getattr(actual, "value", actual))
        super().__init__(f"{MODE_POLICY_MESSAGE}（本次推断为 {self.actual}）")


def require_project_mode(mode: GenerationMode | str) -> None:
    """Refuse any generation mode other than Ref2VA."""
    actual = mode if isinstance(mode, GenerationMode) else GenerationMode(str(mode))
    if actual != REQUIRED_GENERATION_MODE:
        raise ModePolicyError(actual)


def allows(mode: GenerationMode | str) -> bool:
    return (mode if isinstance(mode, GenerationMode) else GenerationMode(str(mode))
            ) == REQUIRED_GENERATION_MODE


__all__ = [
    "MODE_POLICY_ID",
    "MODE_POLICY_MESSAGE",
    "ModePolicyError",
    "REQUIRED_GENERATION_MODE",
    "allows",
    "require_project_mode",
]

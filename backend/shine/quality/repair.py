from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shine.quality.semantic import FusedQCResult
from shine.schemas import RepairAction, RepairPlan

REFERENCE_CODES = {
    "REFERENCE_MISSING",
    "REFERENCE_WRONG",
    "ASSET_NOT_APPROVED",
    "IDENTITY_MISMATCH",
    "COSTUME_DRIFT",
    "SPEAKER_MISMATCH",
    "MISSING_ASSET",
}
DIALOGUE_CODES = {
    "WRONG_LINE",
    "DIALOGUE_TIMING",
    "H3_DIALOGUE_TIMING",
    "H3_DIALOGUE_OVERLAP",
    "H3_SPEAKER_REFERENCE_MISSING",
}
PROMPT_CODES = {"STYLE_DRIFT", "PROMPT_INVALID", "H3_UNRESOLVED_LABEL", "H3_LABEL_MISMATCH", "H3_SECTION_MISSING"}
WORKFLOW_CODES = {"COMFY_WORKFLOW_INVALID", "COMFY_PROVIDER_ERROR", "WORKFLOW_PARAMETER_MISMATCH"}
TRANSIENT_CODES = {"COMFY_TIMEOUT", "COMFY_UNAVAILABLE", "TECHNICAL_FILE_EXISTS", "TECHNICAL_FILE_NONEMPTY"}


def categorize(code: str) -> str:
    if code in REFERENCE_CODES:
        return "REFERENCE_WRONG"
    if code in DIALOGUE_CODES:
        return "DIALOGUE_WRONG"
    if code in PROMPT_CODES:
        return "PROMPT_WRONG"
    if code in WORKFLOW_CODES:
        return "WORKFLOW_WRONG"
    if code in TRANSIENT_CODES:
        return "TRANSIENT"
    if code.startswith("TECHNICAL_"):
        return "TECHNICAL_FAILURE"
    return "UNKNOWN"


def _action_for(category: str) -> tuple[RepairAction, list[str]]:
    if category == "REFERENCE_WRONG":
        return (
            RepairAction.REBUILD_REFERENCE_BINDINGS,
            ["Re-check each shot binding against approved assets", "Rebuild the request-local H3 label manifest"],
        )
    if category == "DIALOGUE_WRONG":
        return (
            RepairAction.REVISE_SHOT_INTENT,
            ["Correct dialogue text, speaker, or timing", "Keep locked story facts unchanged"],
        )
    if category == "PROMPT_WRONG":
        return (
            RepairAction.ADJUST_PROMPT_STRATEGY,
            ["Recompile with the frozen Style Bible", "Correct unresolved labels and section order"],
        )
    if category == "WORKFLOW_WRONG":
        return (
            RepairAction.ADJUST_WORKFLOW_PARAMETERS,
            ["Validate the workflow profile against installed nodes", "Fix mapped input paths or values"],
        )
    if category == "TRANSIENT":
        return (
            RepairAction.RERENDER_AFTER_TRANSIENT,
            ["Reconcile the previous submission before retrying", "Retry only when no external job exists"],
        )
    return (RepairAction.WAIT_FOR_HUMAN, ["Escalate to a human operator"])


@dataclass(frozen=True)
class RepairDecision:
    plan: RepairPlan
    auto_apply: bool
    error_category: str


class RepairEngine:
    """Root-cause classification with a hard retry budget and passed-version protection."""

    def plan(
        self,
        report: FusedQCResult,
        *,
        retries_left: int,
        auto_repair_enabled: bool = False,
    ) -> RepairDecision:
        codes = [failure.code for failure in report.hard_failures]
        categories = [categorize(code) for code in codes]
        preferred: str | None = None
        for candidate in ("REFERENCE_WRONG", "DIALOGUE_WRONG", "PROMPT_WRONG", "WORKFLOW_WRONG", "TRANSIENT"):
            if candidate in categories:
                preferred = candidate
                break
        if preferred is None and report.result == "needs_review":
            preferred = "UNKNOWN"
        if preferred is None:
            preferred = "UNKNOWN"
        action, changes = _action_for(preferred)
        if retries_left <= 0:
            action = RepairAction.WAIT_FOR_HUMAN
            changes = ["The semantic retry budget is exhausted", "Escalate to a human operator"]
        if report.review_confidence < 0.5:
            action = RepairAction.WAIT_FOR_HUMAN
            changes = ["QC confidence is too low for an automatic decision", "Escalate to a human operator"]
        if preferred == "UNKNOWN" and report.result != "passed":
            action = RepairAction.WAIT_FOR_HUMAN
        plan = RepairPlan(
            action=action,
            root_cause=(
                f"{preferred}: " + (", ".join(codes[:6]) if codes else "no hard failure code")
            )[:500],
            changes=changes,
            max_additional_attempts=max(0, min(2, retries_left)),
            human_review_required=action in {RepairAction.WAIT_FOR_HUMAN, RepairAction.REVISE_SHOT_INTENT},
            error_category=preferred,
        )
        auto_apply = (
            auto_repair_enabled
            and not plan.human_review_required
            and plan.max_additional_attempts > 0
            and action
            in {
                RepairAction.RERENDER_AFTER_TRANSIENT,
                RepairAction.ADJUST_PROMPT_STRATEGY,
                RepairAction.ADJUST_WORKFLOW_PARAMETERS,
            }
        )
        return RepairDecision(plan=plan, auto_apply=auto_apply, error_category=preferred)

    @staticmethod
    def guard_passed(job: Any) -> bool:
        """A passed version must never be rewritten by an automatic repair."""
        return getattr(job, "status", None) != "PASSED"

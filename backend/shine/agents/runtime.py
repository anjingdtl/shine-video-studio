from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from shine.agents.planner import LLMScriptPlanner, ShotPlanner
from shine.agents.tools import ProjectContext
from shine.schemas import RepairPlan, ScriptPlan, ShotProposal, ShotSpec


class StructuredModel(Protocol):
    """Agent model port. Implementations return JSON data and have no DB access."""

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class ProducerAgentRuntime:
    """Validates model proposals before application services can consider them.

    The runtime never holds a database session, filesystem handle, or tool that
    can approve assets or freeze a style. Application services re-validate every
    proposal against the current project state.
    """

    def __init__(self, model: StructuredModel | None = None):
        self._model = model

    def _require_model(self) -> StructuredModel:
        if self._model is None:
            raise ValueError("No model is configured for this agent role")
        return self._model

    def propose_shot(self, brief: str, *, style_version_id: str) -> ShotSpec:
        schema = ShotSpec.model_json_schema()
        response = self._require_model().complete_json(
            system=(
                "Return a proposed shot as schema-valid JSON. Respect the locked style, user assets, "
                "story facts, and the requested duration. You cannot approve assets or modify state."
            ),
            user=f"Style version: {style_version_id}\nBrief:\n{brief}",
            schema=schema,
        )
        try:
            proposal = ShotSpec.model_validate(response)
        except ValidationError as exc:
            raise ValueError("Agent proposal failed the ShotSpec contract") from exc
        if proposal.style_version_id not in {"", style_version_id}:
            raise ValueError("Agent proposal cannot select a different style version")
        return proposal.model_copy(update={"style_version_id": style_version_id})

    def analyze_script(self, text: str, *, title: str = "") -> ScriptPlan:
        result = LLMScriptPlanner(self._require_model()).plan(text, title)
        return result.plan

    def plan_shots(
        self,
        plan: ScriptPlan,
        *,
        style_version_id: str,
        context: ProjectContext,
        max_shots: int = 6,
    ) -> list[ShotProposal]:
        planner = ShotPlanner(model=self._require_model())
        proposals, _kind, _warnings = planner.propose(
            plan,
            style_version_id=style_version_id,
            style=context.style,
            context=context,
            max_shots=max_shots,
        )
        return proposals

    def suggest_repair(
        self, *, qc_summary: str, error_categories: list[str], retries_left: int
    ) -> RepairPlan:
        schema = RepairPlan.model_json_schema()
        response = self._require_model().complete_json(
            system=(
                "You are Shine's RepairPlanner. Diagnose the failed shot and choose one typed repair action. "
                "Never claim a shot passed, never approve assets, and stay within the retry budget."
            ),
            user=(
                f"Retries left: {max(0, retries_left)}\n"
                f"Error categories: {', '.join(error_categories) or 'unknown'}\n"
                f"QC evidence:\n{qc_summary}"
            ),
            schema=schema,
        )
        try:
            plan = RepairPlan.model_validate(response)
        except ValidationError as exc:
            raise ValueError("Agent repair proposal failed the RepairPlan contract") from exc
        if plan.max_additional_attempts > max(0, retries_left):
            plan = plan.model_copy(update={"max_additional_attempts": max(0, retries_left)})
        return plan

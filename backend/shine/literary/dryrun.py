"""Dry-run planner: what the four stages would send, before anything is sent.

The planner answers the question a stalled run could not: *why* is this unit
refused?  It renders each stage's real prompt (the same constants the worker
uses), measures the request with the shared estimator, approves the budget with
the shared planner and reports the verdict - without contacting a provider.

It is also the intake check for a long manuscript: a section whose scene does
not fit the window is reported with the number of beats it must be split into,
so the scheduler can plan the split instead of discovering it as a failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from shine.literary.budget import CallBudget, plan_call_budget, schema_shape_risk
from shine.literary.requests import CompiledRequest, compile_request

#: The source share of one scene request, mirroring the pipeline's grouping:
#: a scene's context is the section text plus the bible snapshot and the
#: previous hand-off line.
SCENE_CONTEXT_OVERHEAD_TOKENS = 1_500


@dataclass
class PlannedUnit:
    unit_id: str
    stage: str
    title: str
    source_chars: int
    input_tokens: int
    output_request_tokens: int
    fits: bool
    refusal: dict[str, Any] | None = None
    suggested_beats: int = 1
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id, "stage": self.stage, "title": self.title,
            "source_chars": self.source_chars,
            "input_tokens_estimated": self.input_tokens,
            "output_request_tokens": self.output_request_tokens,
            "fits": self.fits, "suggested_beats": self.suggested_beats,
            "warnings": list(self.warnings),
            **({"refusal": self.refusal} if self.refusal else {}),
        }


@dataclass
class DryRunPlan:
    project_id: str
    stage_units: list[PlannedUnit] = field(default_factory=list)
    document_chars: int = 0
    window_verified: bool = False
    context_window_tokens: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def blocked_units(self) -> list[PlannedUnit]:
        return [unit for unit in self.stage_units if not unit.fits]

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "document_chars": self.document_chars,
            "context_window_tokens": self.context_window_tokens,
            "window_verified": self.window_verified,
            "units": [unit.as_dict() for unit in self.stage_units],
            "blocked_unit_ids": [unit.unit_id for unit in self.blocked_units],
            "notes": list(self.notes),
            "dry_run": True,
        }


def _scene_request(section_text: str, *, schema: dict[str, Any], system: str) -> CompiledRequest:
    context = {
        "bible": {"mainline": "", "characters": [], "timeline": [], "foreshadowing": [],
                  "locked_facts": [], "ending_direction": ""},
        "section": {"section_id": "dry-run", "scene_key": "dry-run", "title": "",
                    "summary": "", "order": 1},
        "source_text": section_text,
        "previous_ending": "",
    }
    return compile_request(system=system, user=json.dumps(context, ensure_ascii=False), schema=schema)


def _beats_needed(source_chars: int, per_beat_chars: int) -> int:
    if source_chars <= 0:
        return 1
    return max(1, -(-source_chars // max(1, per_beat_chars)))


def plan_stage_units(
    *,
    project_id: str,
    sections: list[dict[str, Any]],
    document_text: str,
    config,
    scene_schema: dict[str, Any],
    scene_system: str,
    bible_schema: dict[str, Any] | None = None,
    bible_system: str = "",
    bible_context_chars: int = 0,
) -> DryRunPlan:
    """Plan every section's scene unit, plus the blueprint unit when asked."""
    plan = DryRunPlan(project_id=project_id, document_chars=len(document_text or ""))
    for index, section in enumerate(sections):
        start, end = section.get("start_offset"), section.get("end_offset")
        text = ""
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(document_text):
            text = document_text[start:end]
        request = _scene_request(text, schema=scene_schema, system=scene_system)
        budget = plan_call_budget(config, request, purpose="scene")
        plan.context_window_tokens = budget.context_window_tokens
        plan.window_verified = budget.window_verified
        per_beat = max(1_000, budget.burst_input_limit - SCENE_CONTEXT_OVERHEAD_TOKENS)
        plan.stage_units.append(PlannedUnit(
            unit_id=str(section.get("production_section_id") or f"section-{index + 1}"),
            stage="scene",
            title=str(section.get("title") or ""),
            source_chars=len(text),
            input_tokens=budget.input_tokens,
            output_request_tokens=budget.output_request_tokens,
            fits=budget.fits,
            refusal=budget.refusal,
            suggested_beats=_beats_needed(len(text), per_beat),
            warnings=list(budget.warnings),
        ))
    if bible_schema is not None:
        context = {"master": {"char_count": len(document_text or ""),
                              "outline_count": max(1, len(sections))},
                   "outline_summaries": [{"order": i + 1, "summary_chars": bible_context_chars}
                                         for i in range(max(1, len(sections)))]}
        request = compile_request(system=bible_system,
                                  user=json.dumps(context, ensure_ascii=False),
                                  schema=bible_schema)
        budget = plan_call_budget(config, request, purpose="outline")
        plan.stage_units.append(PlannedUnit(
            unit_id="bible", stage="bible", title="全书故事蓝图",
            source_chars=bible_context_chars, input_tokens=budget.input_tokens,
            output_request_tokens=budget.output_request_tokens, fits=budget.fits,
            refusal=budget.refusal, warnings=list(budget.warnings)))
    shapes = schema_shape_risk(scene_schema)
    if shapes:
        plan.notes.append(
            f"scene schema worst case is {shapes['worst_case_tokens']} tokens; "
            "it is a risk check, not the output target")
    if not plan.window_verified:
        plan.notes.append(
            "provider did not declare a context window; the plan uses a conservative "
            "planning window and no capability claim is made")
    return plan


def explain(budget: CallBudget) -> str:
    """One plain-language line for the ordinary Web screen."""
    if budget.fits:
        return f"预计输入 {budget.input_tokens} tokens，可用输出 {budget.output_request_tokens} tokens，可以开始。"
    refusal = budget.refusal or {}
    return str(refusal.get("message_zh") or "该单元无法在当前模型窗口中完成，请拆分后重试。")

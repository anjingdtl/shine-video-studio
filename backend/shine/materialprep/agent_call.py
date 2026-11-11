"""One material-side agent call, delegated to the Call Governor.

The material path talks to a text model twice: once per scene for the
pre-production scriptwriter, and once per requirement for the producer's
appearance brief.  Both calls are executed by
:class:`shine.literary.governor.CallGovernor`, so the whole product shares one
request compiler, one budget and one failure matrix:

* the final request is compiled (schema included) and measured before it is
  sent, and the gateway sends exactly those bytes;
* an over-budget request is refused by name without being truncated;
* a complete answer is kept even when its envelope breaks the contract, because
  the governor salvages the text before it classifies the failure;
* an uncertain external outcome is reported as uncertain and is never replayed
  by the caller;
* the provider text is never returned as a validated object unless it really
  validates - it is returned as a *candidate* draft instead.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shine.literary import triage
from shine.literary.governor import (
    PRE_REQUEST_PHASES,
    CallGovernor,
    UnitBudget,
    classify_external_outcome,
)
from shine.llm.base import LLMError, ProviderConfig
from shine.materialprep.contracts import APPEARANCE_MAX_LENGTH, MaterialImageBrief

__all__ = [
    "PRE_REQUEST_PHASES", "AgentCallOutcome", "budget_report", "call_material_agent",
    "classify_external_outcome", "estimate_tokens", "pace_provider_call",
]

logger = logging.getLogger(__name__)

CONTEXT_MARGIN_TOKENS = 512
"""The safety margin kept free in the context window (planning estimate)."""

CONSERVATIVE_CONTEXT_WINDOW = 1_000_000
"""The planning window used by :func:`budget_report` when none is declared.

This is a *queue-time* estimate used to decide whether a unit is too large to
queue.  It never authorises a send: the Call Governor computes the approved
budget from the rendered request and refuses an oversized one by name.
"""

MIN_OUTPUT_RESERVE_TOKENS = 4_096
MAX_OUTPUT_RESERVE_TOKENS = 131_072

CORRECTION_RULE = (
    " Your previous answer did not satisfy the contract. Regenerate the complete "
    "JSON object from the original task. Check the named fields against the schema. "
    "For overlong fields, shorten from actual_length to max_length, preserving essential details."
)


@dataclass
class AgentCallOutcome:
    """What one call concluded."""

    parsed: dict[str, Any] | None = None
    correction_used: bool = False
    schema_errors: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    uncertain: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    candidate_text: str = ""
    """The complete literary text that really arrived, even when the envelope
    was rejected.  The caller persists it as a candidate draft: an unapproved
    answer is still a chapter, and a person may accept it after review."""
    candidate_payload: dict[str, Any] | None = None
    findings: list[triage.Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.parsed is not None

    @property
    def review(self) -> dict[str, Any]:
        return triage.summarize(self.findings)

    @property
    def severity(self) -> str:
        return self.review.get("state") or triage.INFO



STEP_DELAY_MS_ENV = "SHINE_AGENT_STEP_DELAY_MS"
"""The pause between two real model calls.

Two calls back to back share the provider connection and the same rate limit,
and the gap gives the workbench a visible boundary between two steps.  It is
the convention the crew chain already uses.
"""


def step_delay_seconds() -> float:
    try:
        return max(0.0, int(os.getenv(STEP_DELAY_MS_ENV, "1200")) / 1000)
    except ValueError:
        return 1.2


_LAST_CALL_AT: float | None = None
"""Completion time of the last provider call, or ``None`` before the first.

The anchor is the *completion* of the previous call, not the moment pacing
started: a call issued right after pacing would otherwise eat into the gap,
and on Windows ``time.sleep`` may wake slightly early, so the next call could
start measurably before the requested gap has passed.
"""


def pace_provider_call() -> None:
    """Keep a real gap between two provider calls in this process.

    The gap is measured from the previous call's completion and enforced
    against a monotonic deadline, so an early wake cannot shorten it.
    """
    global _LAST_CALL_AT
    delay = step_delay_seconds()
    if delay <= 0:
        return
    if _LAST_CALL_AT is not None:
        deadline = _LAST_CALL_AT + delay
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def note_provider_call_finished() -> None:
    """Record when the provider call actually finished, as the pacing anchor."""
    global _LAST_CALL_AT
    _LAST_CALL_AT = time.monotonic()


def estimate_tokens(text: str) -> int:
    """A deliberately conservative token estimate.

    CJK costs about one token per character and UTF-8 spends three bytes on it;
    ASCII costs roughly four characters per token and one byte each.  Dividing
    the UTF-8 byte count by three therefore over-estimates ASCII and matches
    CJK, which is the safe direction for a budget that refuses rather than
    truncates.
    """
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


def budget_report(context: dict[str, Any], schema: dict[str, Any], system_prompt: str, config,
                  *, input_share: float = 1.0) -> dict[str, Any]:
    """Prove the request fits the window before it is sent.

    ``input_share`` lets a long-running chain spend only a fraction of the same
    window on one unit (a third, for the outline/storyboard chunks).  The
    input estimate, output reserve and margin stay in one controller so a
    smaller chunk is a scheduling decision, not a second budget model.
    """
    input_tokens = sum(
        estimate_tokens(part)
        for part in (
            json.dumps(context, ensure_ascii=False),
            json.dumps(schema, ensure_ascii=False),
            system_prompt,
        )
    )
    declared_window = config.context_window_tokens
    window = declared_window or CONSERVATIVE_CONTEXT_WINDOW
    source = config.context_window_source if declared_window else "conservative_default"
    output_reserve = min(
        MAX_OUTPUT_RESERVE_TOKENS,
        max(MIN_OUTPUT_RESERVE_TOKENS, int(config.max_output_tokens or 0)),
    )
    total = input_tokens + output_reserve + CONTEXT_MARGIN_TOKENS
    available = int(window * input_share)
    return {
        "input_tokens_estimated": input_tokens,
        "output_reserve_tokens": output_reserve,
        "margin_tokens": CONTEXT_MARGIN_TOKENS,
        "total_tokens_estimated": total,
        "context_window_tokens": window,
        "context_window_source": source,
        "input_share": input_share,
        "available_tokens": available,
        "fits": total <= available,
    }


def _shorten_appearance(payload: dict[str, Any]) -> dict[str, Any] | None:
    appearance = payload.get("appearance")
    if not isinstance(appearance, str) or len(appearance) <= APPEARANCE_MAX_LENGTH:
        return None
    prefix = appearance[:APPEARANCE_MAX_LENGTH + 1]
    ends = list(re.finditer(r'[.!?]["\']?(?=\s)', prefix))
    if not ends:
        return None
    return {**payload, "appearance": appearance[:ends[-1].end()]}


def call_material_agent(
    gateway,
    session_factory,
    role: str,
    *,
    system: str,
    context: dict[str, Any],
    schema: dict[str, Any],
    contract,
    on_progress=None,
    before_call=None,
    task_category: str = "default",
    unit_id: str | None = None,
    unit_budget: UnitBudget | None = None,
    purpose: str | None = None,
    visible_goal_tokens: int | None = None,
    record_attempt=None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> AgentCallOutcome:
    """Ask one role for one contract through the shared Call Governor.

    ``session_factory`` yields the database session the gateway needs.  The
    caller keeps the durable task bookkeeping; this module performs the call
    and reports what really happened - including the text that arrived when the
    envelope did not validate.
    """
    response_schema = schema
    if contract is MaterialImageBrief:
        # Keep the original string available for local validation and correction.
        properties = schema["properties"]
        response_schema = {**schema, "properties": {
            **properties,
            "appearance": {key: value for key, value in properties["appearance"].items()
                           if key != "maxLength"},
        }}

    with session_factory() as db:
        try:
            profile = gateway.profile_for_role(db, role)
            config = gateway.config_for(profile)
        except LLMError as exc:
            # The provider was never reachable: name it locally instead of
            # letting an unconfigured profile escape as an unhandled exception.
            finding = triage.classify_failure(
                exc.code, unit_ids=(unit_id or role,),
                evidence={"role": role, "message": str(exc)[:200]})
            return AgentCallOutcome(
                error_code=exc.code, uncertain=False, findings=[finding],
                details={"external_outcome": "NOT_SENT", "provider_error": str(exc)[:400],
                         "review": triage.summarize([finding])})
        except AttributeError:
            # A gateway double that exposes only ``complete_for_role`` (tests and
            # offline probes) has no profile: plan against the conservative
            # default profile rather than failing the unit.
            config = ProviderConfig(
                profile_id="unresolved", name="unresolved", kind="mock", base_url="",
                model="unresolved", api_key=None)
    goal = purpose or ("scene" if task_category == "creative" else "structured")
    if record_attempt is None and project_id:
        # No caller-side recorder: use the shared one so no money is spent
        # outside the ledger.  Every stage - outline, preparation, crew, scene -
        # writes the same rows, which is what makes cross-stage questions
        # answerable in one place.
        from shine.literary.ledger import recorder
        record_attempt = recorder(session_factory, project_id, task_id)
    governor = CallGovernor(
        gateway, config, role=role, unit_id=unit_id or role,
        budget=unit_budget, record_attempt=record_attempt)
    qualified = governor.execute(
        system=system,
        context=context,
        schema=schema,
        response_schema=response_schema,
        contract=contract,
        purpose=goal,
        task_category=task_category,
        visible_goal_tokens=visible_goal_tokens,
        before_call=before_call,
        on_progress=on_progress,
        session_factory=session_factory,
        repair_rule=CORRECTION_RULE,
        aliases={"content": "text", "script": "text", "scene_text": "text"},
        error_logger=logger,
        # Keep the project-wide gap between two real provider calls: a model
        # that is asked twice back to back shares one rate limit.
        before_send=pace_provider_call,
        after_send=note_provider_call_finished,
    )
    if contract is MaterialImageBrief and not qualified.ok and isinstance(qualified.payload, dict):
        # Last local resort for the producer's appearance: the provider's own
        # correction could not shorten it, so cut at a sentence boundary and
        # validate again.  This is metadata, never literary prose.
        shortened = _shorten_appearance(qualified.payload)
        if shortened is not None:
            original_length = len(qualified.payload.get("appearance") or "")
            try:
                contract.model_validate(shortened)
            except ValidationError:
                pass
            else:
                qualified.parsed = shortened
                qualified.payload = shortened
                qualified.error_code = None
                qualified.details["appearance_truncated"] = True
                qualified.details["appearance_original_length"] = original_length
                qualified.findings.append(triage.Finding(
                    severity=triage.INFO, reason_code="SCHEMA_LOCAL_REPAIR",
                    message_zh="外观描述过长，已在本机按完整句子截短。",
                    next_action="continue_with_independent_units",
                    affected_unit_ids=(unit_id or role,), content_saved=True))
    outcome = AgentCallOutcome(
        parsed=qualified.parsed,
        correction_used=qualified.corrective,
        schema_errors=[error for error in qualified.details.get("schema_errors") or []
                       if isinstance(error, dict)],
        error_code=qualified.error_code,
        uncertain=qualified.uncertain,
        candidate_text=qualified.text if not qualified.ok else "",
        candidate_payload=qualified.payload if not qualified.ok else None,
        findings=qualified.findings,
        details={
            **{key: value for key, value in qualified.details.items() if key != "schema_errors"},
            "external_outcome": qualified.external_outcome,
            "truncated": qualified.finish_reason in {"length", "max_tokens", "content_filter"},
            "usage": qualified.usage,
            "output_budget": qualified.budget,
            "review": qualified.review,
            "request": qualified.request,
            "attempts": qualified.attempts,
            "candidate_chars": len(qualified.text) if not qualified.ok else 0,
        },
    )
    if contract is MaterialImageBrief and outcome.ok and isinstance(outcome.parsed, dict):
        appearance = outcome.parsed.get("appearance")
        if isinstance(appearance, str) and len(appearance) > APPEARANCE_MAX_LENGTH:
            prefix = appearance[:APPEARANCE_MAX_LENGTH + 1]
            ends = list(re.finditer(r'[.!?]["\']?(?=\s)', prefix))
            if ends:
                outcome.parsed = {**outcome.parsed, "appearance": appearance[:ends[-1].end()]}
                outcome.details.update(appearance_truncated=True,
                                       appearance_original_length=len(appearance))
    return outcome

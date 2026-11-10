"""One material-side agent call: budgeted, corrected once, never replayed.

The material path talks to a text model twice: once per scene for the
pre-production scriptwriter, and once per requirement for the producer's
appearance brief.  Both calls share the same discipline, so it lives here:

* the context is budgeted before the call, and an over-budget request is
  refused by name instead of being truncated;
* a schema error earns at most ONE targeted correction carrying the broken
  fields, the validator and the original task;
* an uncertain external outcome is reported as uncertain and is never
  replayed by the caller;
* the provider text is never stored: only the validated object leaves this
  module.
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

from fastapi import HTTPException
from pydantic import ValidationError

from shine.llm.base import LLMError
from shine.llm.output_budget import truncated_output, usage_summary
from shine.materialprep.contracts import APPEARANCE_MAX_LENGTH, MaterialImageBrief

logger = logging.getLogger(__name__)

PRE_REQUEST_PHASES = ("PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER")
"""The phases that prove the provider request was never opened.

The gateway writes ``WAITING_PROVIDER`` immediately before it opens the request,
so a call that stopped at one of these owns no answer: the outcome is
``NOT_SENT``, not an unknown one.  Classifying an unexpected local fault as
``UNKNOWN`` without this evidence blocks a scene forever under the no-replay
gate, which is how one local bug turned into an unrecoverable "provider result
unknown".
"""

CONTEXT_MARGIN_TOKENS = 512
"""The safety margin kept free in the context window."""

CONSERVATIVE_CONTEXT_WINDOW = 1_000_000
"""The window assumed when the provider declares none.

The plan refuses an unbounded window.  Current 1M-context reasoning models are
the budget baseline: the window is 1M tokens and the output ceiling is 128K, so
the estimate starts from that class instead of a small legacy window that
refuses ordinary scripts.  A provider that declares its own smaller window still
overrides this default and is recorded by name.
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

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.parsed is not None


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


def classify_external_outcome(exc: LLMError) -> str:
    """Name what the provider actually did, without guessing.

    * NOT_SENT: no provider request was ever opened - a local configuration
      error, a refused consent, or a transport failure before the request
      left this machine.
    * RESPONSE_RECEIVED: the provider answered.  A 4xx refusal carries no
      answer at all; a 2xx answer that broke the contract arrived but is
      unusable.
    * UNKNOWN: the provider may have answered and the answer was lost (a
      timeout, a 5xx, a connection that broke after the request).

    The distinction matters: a request that produced no answer can be asked
    again, while an unknown outcome may still own an answer upstream and must
    never be replayed.
    """
    recorded = exc.details.get("external_outcome")
    if recorded in {"NOT_SENT", "UNKNOWN"}:
        return recorded
    if exc.code in {"LLM_NOT_CONFIGURED", "LLM_CAPABILITY"}:
        # The provider was never asked.
        return "NOT_SENT"
    status = exc.details.get("status")
    if isinstance(status, int) and not isinstance(status, bool) and status < 500:
        # The provider answered: a refusal (4xx) or an unusable answer (2xx).
        return "RESPONSE_RECEIVED"
    return "UNKNOWN"


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
) -> AgentCallOutcome:
    """Ask one role for one contract, correcting at most once.

    ``session_factory`` yields the database session the gateway needs.  The
    caller keeps the durable task bookkeeping; this module only performs the
    call and reports what happened.
    """

    response_schema = schema
    if contract is MaterialImageBrief:
        # Keep the original string available for local validation and correction.
        # The gateway otherwise discards it before we can measure or shorten it.
        properties = schema["properties"]
        response_schema = {**schema, "properties": {
            **properties,
            "appearance": {key: value for key, value in properties["appearance"].items()
                           if key != "maxLength"},
        }}

    last_phase: list[str] = []

    def report(phase: str) -> None:
        # Remembered locally because the caller's own progress writer overwrites
        # the durable phase with FAILED, which is exactly the evidence the
        # unknown-outcome gate needs.
        last_phase.append(phase)
        if on_progress:
            on_progress(phase)

    def unexpected_fault(exc: Exception, *, correction_used: bool, schema_errors: list | None = None):
        """Name a local fault honestly instead of asserting an unknown result.

        The exception is logged with its traceback: swallowing it left the fault
        undiagnosable, because neither the exception class nor the phase it
        happened at survived into the durable trace.
        """
        phase = last_phase[-1] if last_phase else "PREPARING"
        may_have_answered = phase not in PRE_REQUEST_PHASES
        logger.exception("material_agent_fault role=%s phase=%s may_have_answered=%s",
                         role, phase, may_have_answered)
        return AgentCallOutcome(
            error_code="LLM_PROVIDER_FAULT",
            uncertain=may_have_answered,
            correction_used=correction_used,
            schema_errors=schema_errors or [],
            details={"error_type": type(exc).__name__,
                     "external_outcome": "UNKNOWN" if may_have_answered else "NOT_SENT",
                     "last_phase": phase},
        )

    def call(system_prompt: str, user_payload: str):
        # One gap before every provider call, in one place.  A long outline
        # makes one call per scene; two calls back to back share the provider
        # connection and the rate limit, and that is how a run of scenes ends
        # in a rate-limit failure that looks like a stall.
        pace_provider_call()
        try:
            with session_factory() as db:
                if before_call:
                    before_call(db)
                return gateway.complete_for_role(
                    db,
                    role,
                    system=system_prompt,
                    user=user_payload,
                    schema=response_schema,
                    on_progress=report,
                    task_category=task_category,
                )
        finally:
            # The anchor is the call's completion, so the gap to the next call
            # is measured from the real boundary, not from the pacing entry.
            note_provider_call_finished()

    try:
        response = call(system, json.dumps(context, ensure_ascii=False))
        contract.model_validate(response.parsed)
        return AgentCallOutcome(parsed=response.parsed, details={
            "usage": usage_summary(response.usage),
            "output_budget": (response.raw_meta or {}).get("output_budget"),
        })
    except LLMError as exc:
        external_outcome = classify_external_outcome(exc)
        validator = exc.details.get("validator")
        if external_outcome == "RESPONSE_RECEIVED" and exc.code == "LLM_FORMAT" and validator:
            # The transport validates the JSON schema before Pydantic sees it.
            # Only a response with a known contract violation earns a correction.
            errors = [{"field": exc.details.get("path", ""), "type": validator}]
        else:
            return AgentCallOutcome(
                error_code=exc.code,
                uncertain=external_outcome == "UNKNOWN",
                details={"error_type": type(exc).__name__, "external_outcome": external_outcome,
                         "response_status": exc.details.get("status"),
                         "provider_code": exc.details.get("provider_code"),
                         "truncated": truncated_output(exc),
                         # The provider's own account of why the answer is
                         # unusable.  A completion ceiling spent on reasoning and
                         # a broken contract field are different failures with
                         # different next steps, and "LLM_FORMAT" alone names
                         # neither.
                         "finish_reason": exc.details.get("finish_reason"),
                         "completion_tokens": exc.details.get("completion_tokens"),
                         "reasoning_tokens": exc.details.get("reasoning_tokens"),
                         "provider_schema_path": exc.details.get("path"),
                         "provider_schema_validator": validator},
            )
    except HTTPException as exc:
        # A refused stage never opened a provider request. That makes it
        # NOT_SENT and retryable, the opposite of an unknown outcome; reporting
        # it as LLM_PROVIDER_FAULT marked a whole batch uncertain because one
        # stale lease from a killed worker was holding the GPU.
        detail = exc.detail if isinstance(exc.detail, str) else "AGENT_STAGE_REFUSED"
        return AgentCallOutcome(
            error_code=detail,
            uncertain=False,
            details={"error_type": "HTTPException", "external_outcome": "NOT_SENT",
                     "status": exc.status_code},
        )
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_url=False):
            field_name = ".".join(str(part) for part in error["loc"])
            entry = {"field": field_name, "type": error["type"]}
            if error["type"] == "string_too_long":
                entry.update(actual_length=len(error["input"]), max_length=error["ctx"]["max_length"])
            errors.append(entry)
    except Exception as exc:  # noqa: BLE001 - an unexpected fault must not kill the batch
        return unexpected_fault(exc, correction_used=False)

    correction = {"task": context, "schema_errors": errors, "validator": schema}
    try:
        corrected = call(system + CORRECTION_RULE, json.dumps(correction, ensure_ascii=False))
        parsed = corrected.parsed
        details = {"usage": usage_summary(corrected.usage),
                   "output_budget": (corrected.raw_meta or {}).get("output_budget")}
        if contract is MaterialImageBrief and isinstance(parsed, dict):
            appearance = parsed.get("appearance")
            if isinstance(appearance, str) and len(appearance) > APPEARANCE_MAX_LENGTH:
                prefix = appearance[:APPEARANCE_MAX_LENGTH + 1]
                ends = list(re.finditer(r'[.!?]["\']?(?=\s)', prefix))
                if ends:
                    parsed = {**parsed, "appearance": appearance[:ends[-1].end()]}
                    details.update(appearance_truncated=True, appearance_original_length=len(appearance))
        contract.model_validate(parsed)
        return AgentCallOutcome(parsed=parsed, correction_used=True, schema_errors=errors, details=details)
    except LLMError as second:
        external_outcome = classify_external_outcome(second)
        uncertain = external_outcome == "UNKNOWN"
        return AgentCallOutcome(
            error_code=second.code,
            uncertain=uncertain,
            correction_used=True,
            schema_errors=errors,
            details={"error_type": type(second).__name__, "external_outcome": external_outcome,
                     "response_status": second.details.get("status"),
                     "provider_code": second.details.get("provider_code"),
                     "truncated": truncated_output(second)},
        )
    except ValidationError:
        return AgentCallOutcome(
            error_code="SCHEMA_VALIDATION_FAILED",
            correction_used=True,
            schema_errors=errors,
            details={"external_outcome": "RESPONSE_RECEIVED"},
        )
    except Exception as second:  # noqa: BLE001 - an unexpected fault must not kill the batch
        return unexpected_fault(second, correction_used=True, schema_errors=errors)

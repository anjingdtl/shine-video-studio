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
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shine.llm.base import LLMError

CONTEXT_MARGIN_TOKENS = 512
"""The safety margin kept free in the context window."""

CONSERVATIVE_CONTEXT_WINDOW = 8192
"""The window assumed when the provider declares none.

The plan refuses an unbounded window.  A conservative, named default is used
and recorded in the evidence, so the reader knows the number was a default and
not a measurement.
"""

MIN_OUTPUT_RESERVE_TOKENS = 800
MAX_OUTPUT_RESERVE_TOKENS = 2000

CORRECTION_RULE = (
    " Your previous answer did not satisfy the contract. Fix only the fields the "
    "validator named, keep everything else that was already correct, and return the same "
    "JSON object shape."
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


def budget_report(context: dict[str, Any], schema: dict[str, Any], system_prompt: str, config) -> dict[str, Any]:
    """Prove the request fits the window before it is sent."""
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
    return {
        "input_tokens_estimated": input_tokens,
        "output_reserve_tokens": output_reserve,
        "margin_tokens": CONTEXT_MARGIN_TOKENS,
        "total_tokens_estimated": total,
        "context_window_tokens": window,
        "context_window_source": source,
        "fits": total <= window,
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
) -> AgentCallOutcome:
    """Ask one role for one contract, correcting at most once.

    ``session_factory`` yields the database session the gateway needs.  The
    caller keeps the durable task bookkeeping; this module only performs the
    call and reports what happened.
    """

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
                    schema=schema,
                    on_progress=on_progress,
                )
        finally:
            # The anchor is the call's completion, so the gap to the next call
            # is measured from the real boundary, not from the pacing entry.
            note_provider_call_finished()

    try:
        response = call(system, json.dumps(context, ensure_ascii=False))
    except LLMError as exc:
        external_outcome = classify_external_outcome(exc)
        uncertain = external_outcome == "UNKNOWN"
        return AgentCallOutcome(
            error_code=exc.code,
            uncertain=uncertain,
            details={"error_type": type(exc).__name__, "external_outcome": external_outcome,
                     "response_status": exc.details.get("status")},
        )
    except Exception as exc:  # noqa: BLE001 - an unexpected fault must not kill the batch
        # The request may already have reached the provider, so the outcome is
        # unknown rather than failed.  The scene is reviewed, never replayed.
        return AgentCallOutcome(
            error_code="LLM_PROVIDER_FAULT",
            uncertain=True,
            details={"error_type": type(exc).__name__, "external_outcome": "UNKNOWN"},
        )

    try:
        contract.model_validate(response.parsed)
        return AgentCallOutcome(parsed=response.parsed)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(part) for part in error.get("loc", ())), "type": error.get("type")}
            for error in exc.errors(include_input=False, include_url=False)
        ]

    correction = {"task": context, "schema_errors": errors, "validator": schema}
    try:
        corrected = call(system + CORRECTION_RULE, json.dumps(correction, ensure_ascii=False))
        contract.model_validate(corrected.parsed)
        return AgentCallOutcome(parsed=corrected.parsed, correction_used=True, schema_errors=errors)
    except LLMError as second:
        external_outcome = classify_external_outcome(second)
        uncertain = external_outcome == "UNKNOWN"
        return AgentCallOutcome(
            error_code=second.code,
            uncertain=uncertain,
            correction_used=True,
            schema_errors=errors,
            details={"error_type": type(second).__name__, "external_outcome": external_outcome,
                     "response_status": second.details.get("status")},
        )
    except ValidationError:
        return AgentCallOutcome(
            error_code="SCHEMA_VALIDATION_FAILED",
            correction_used=True,
            schema_errors=errors,
            details={"external_outcome": "RESPONSE_RECEIVED"},
        )
    except Exception as second:  # noqa: BLE001 - an unexpected fault stays unknown
        return AgentCallOutcome(
            error_code="LLM_PROVIDER_FAULT",
            uncertain=True,
            correction_used=True,
            schema_errors=errors,
            details={"error_type": type(second).__name__, "external_outcome": "UNKNOWN"},
        )

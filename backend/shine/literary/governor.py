"""The single request governor every literary call goes through.

One place compiles the request, one place approves its budget, one place
decides what a failure means, and one place keeps the answer that arrived.  The
four-stage pipeline, the memory observation calls and the targeted semantic
review all share it, so a repair in one lane cannot invent its own retry policy.

What the governor owns
----------------------
* **compile once** - the request is rendered (schema included) before any
  measurement, and the gateway is told to send those exact bytes;
* **one budget per logical unit** - ``max_calls``, ``max_repair_rounds`` and
  ``max_completion_tokens`` are spent by the writer, the memory call, the
  semantic review and every correction together, so no lane can loop;
* **never lose a received answer** - a contract violation with substantive text
  is salvaged locally and kept as a candidate, not thrown away;
* **name the outcome** - ``NOT_SENT``, ``RESPONSE_RECEIVED`` or ``UNKNOWN``,
  with an attempt record the caller can persist.

What it does not own: task state, artifact promotion and user approval.  Those
stay in the pipeline, which is what keeps a candidate from becoming accepted
just because it exists.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shine.literary import salvage, triage
from shine.literary.budget import CallBudget, plan_call_budget
from shine.literary.requests import CompiledRequest, compile_request
from shine.llm.base import LLMError

logger = logging.getLogger(__name__)

DEFAULT_MATERIAL_CORRECTION_RULE = (
    " Your previous answer did not satisfy the contract. Regenerate the complete "
    "JSON object from the original task. Check the named fields against the schema. "
    "For overlong fields, shorten from actual_length to max_length, preserving essential details."
)

PRE_REQUEST_PHASES = ("PREPARING", "WAITING_RESOURCE", "PREPARING_PROVIDER")
"""Phases that prove the provider request was never opened."""


def accepts_precompiled(method) -> bool:
    """Whether a gateway entry point can accept an already-compiled request.

    Only the real gateway renders the schema itself; a test double that
    implements the older keyword set must keep working, and it must never
    receive the request twice because of an argument it does not know.
    """
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    if "precompiled" in parameters:
        return True
    return any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())


@dataclass
class UnitBudget:
    """The single budget shared by every call of one logical unit."""

    max_calls: int = 4
    max_repair_rounds: int = 1
    max_completion_tokens: int = 600_000
    calls: int = 0
    repairs: int = 0
    completion_tokens: int = 0

    def may_call(self) -> str | None:
        if self.calls >= self.max_calls:
            return "LLM_UNIT_CALL_BUDGET_EXHAUSTED"
        if self.completion_tokens >= self.max_completion_tokens:
            return "LLM_UNIT_TOKEN_BUDGET_EXHAUSTED"
        return None

    def may_repair(self) -> bool:
        return self.repairs < self.max_repair_rounds

    def charge_call(self, *, repair: bool = False) -> None:
        self.calls += 1
        if repair:
            self.repairs += 1

    def charge_tokens(self, completion_tokens: int | None) -> None:
        self.completion_tokens += max(0, int(completion_tokens or 0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls, "calls": self.calls,
            "max_repair_rounds": self.max_repair_rounds, "repairs": self.repairs,
            "max_completion_tokens": self.max_completion_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class CallOutcome:
    """What one governed call (or one governed pipeline of calls) concluded."""

    parsed: dict[str, Any] | None = None
    text: str = ""
    payload: dict[str, Any] | None = None
    findings: list[triage.Finding] = field(default_factory=list)
    error_code: str | None = None
    external_outcome: str = "NOT_SENT"
    uncertain: bool = False
    corrective: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    finish_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.parsed is not None

    @property
    def content_saved(self) -> bool:
        return bool(self.text.strip())

    @property
    def review(self) -> dict[str, Any]:
        return triage.summarize(self.findings)

    def as_details(self) -> dict[str, Any]:
        return {
            "external_outcome": self.external_outcome,
            "finish_reason": self.finish_reason,
            "unit_budget": (self.request or {}).get("unit_budget"),
            "request": self.request,
            "attempts": list(self.attempts),
            "review": self.review,
        }


def _error_entry(error: dict[str, Any]) -> dict[str, Any]:
    """One validator error, with the length evidence a correction needs."""
    entry: dict[str, Any] = {
        "field": ".".join(str(part) for part in error.get("loc") or ()),
        "type": error.get("type"),
    }
    if error.get("type") == "string_too_long":
        value = error.get("input")
        context = error.get("ctx") or {}
        entry.update(actual_length=len(value) if isinstance(value, str) else None,
                     max_length=context.get("max_length"))
    return entry


def _usage_tokens(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    reasoning = details.get("reasoning_tokens")
    if not isinstance(reasoning, int) or isinstance(reasoning, bool):
        reasoning = None
    output = usage.get("completion_tokens")
    if not isinstance(output, int) or isinstance(output, bool):
        output = None
    return {"completion_tokens": output or 0, "reasoning_tokens": reasoning}


def classify_external_outcome(exc: LLMError) -> str:
    """Name what the provider actually did, without guessing."""
    recorded = exc.details.get("external_outcome")
    if recorded in {"NOT_SENT", "UNKNOWN"}:
        return recorded
    if exc.code in {"LLM_NOT_CONFIGURED", "LLM_CAPABILITY"}:
        return "NOT_SENT"
    status = exc.details.get("status")
    if isinstance(status, int) and not isinstance(status, bool) and status < 500:
        return "RESPONSE_RECEIVED"
    return "UNKNOWN"


class CallGovernor:
    """Execute literary calls for one logical unit under one shared budget."""

    def __init__(
        self,
        gateway,
        config,
        *,
        role: str,
        unit_id: str,
        budget: UnitBudget | None = None,
        record_attempt=None,
    ):
        self.gateway = gateway
        self.config = config
        self.role = role
        self.unit_id = unit_id
        self.budget = budget or UnitBudget()
        self._record_attempt = record_attempt

    # -- planning ---------------------------------------------------------

    def plan(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        purpose: str = "creative",
        visible_goal_tokens: int | None = None,
        output_ceiling: int | None = None,
    ) -> tuple[CompiledRequest, CallBudget]:
        request = compile_request(system=system, user=user, schema=schema)
        budget = plan_call_budget(
            self.config, request, purpose=purpose,
            visible_goal_tokens=visible_goal_tokens, output_ceiling=output_ceiling)
        return request, budget

    # -- execution --------------------------------------------------------

    def execute(
        self,
        *,
        system: str,
        user: str | None = None,
        context: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None,
        contract=None,
        response_schema: dict[str, Any] | None = None,
        purpose: str = "creative",
        task_category: str = "creative",
        visible_goal_tokens: int | None = None,
        output_ceiling: int | None = None,
        before_call=None,
        on_progress=None,
        session_factory=None,
        repair_rule: str = DEFAULT_MATERIAL_CORRECTION_RULE,
        aliases: dict[str, str] | None = None,
        accept_partial: bool = True,
        error_logger: Any | None = None,
        before_send=None,
        after_send=None,
    ) -> CallOutcome:
        """Compile, approve, send, salvage and (at most once) correct."""
        payload_text = user if user is not None else json.dumps(context or {}, ensure_ascii=False)
        provider_schema = response_schema if response_schema is not None else schema
        request, budget = self.plan(
            system=system, user=payload_text, schema=provider_schema,
            purpose=purpose, visible_goal_tokens=visible_goal_tokens,
            output_ceiling=output_ceiling)
        evidence = {"request": request.evidence(), "budget": budget.as_dict(),
                    "unit_budget": self.budget.as_dict()}
        if not budget.fits:
            return self._refused(request, budget, evidence)

        outcome = self._call_and_read(
            request=request, budget=budget, contract=contract, schema=schema,
            task_category=task_category, before_call=before_call,
            on_progress=on_progress, session_factory=session_factory,
            purpose=purpose, aliases=aliases, evidence=evidence,
            error_logger=error_logger, before_send=before_send, after_send=after_send)

        if outcome.ok or not accept_partial:
            return outcome
        if not self._can_repair(outcome):
            return outcome

        errors = self._schema_errors(outcome)
        correction = {
            "task": context if context is not None else payload_text,
            "schema_errors": errors,
            "validator": schema if schema is not None else provider_schema,
        }
        repair_request, repair_budget = self.plan(
            system=system + repair_rule,
            user=json.dumps(correction, ensure_ascii=False),
            schema=provider_schema, purpose=purpose,
            visible_goal_tokens=visible_goal_tokens, output_ceiling=output_ceiling)
        if not repair_budget.fits:
            return outcome
        repaired = self._call_and_read(
            request=repair_request, budget=repair_budget, contract=contract, schema=schema,
            task_category=task_category, before_call=before_call,
            on_progress=on_progress, session_factory=session_factory,
            purpose=purpose, aliases=aliases, corrective=True,
            error_logger=error_logger, before_send=before_send, after_send=after_send,
            evidence={"request": repair_request.evidence(), "budget": repair_budget.as_dict(),
                      "unit_budget": self.budget.as_dict()})
        repaired.details["first_attempt_error"] = outcome.error_code
        repaired.details["schema_errors"] = errors
        if repaired.ok:
            return repaired
        # Keep the *first* answer's content when the correction produced
        # nothing better: a complete first draft outranks an empty retry.
        if not repaired.content_saved and outcome.content_saved:
            return outcome
        return repaired

    # -- internals --------------------------------------------------------

    def _refused(self, request: CompiledRequest, budget: CallBudget,
                 evidence: dict[str, Any]) -> CallOutcome:
        refusal = dict(budget.refusal or {})
        code = str(refusal.get("code") or "LLM_CREATIVE_CONTEXT_OVERFLOW")
        finding = triage.classify_failure(
            code, unit_ids=(self.unit_id,),
            evidence={"request_fingerprint": request.fingerprint,
                      "input_tokens_estimated": budget.input_tokens,
                      "context_window_tokens": budget.context_window_tokens},
            details=refusal)
        return CallOutcome(
            error_code=code, external_outcome="NOT_SENT", uncertain=False,
            findings=[finding], budget=budget.as_dict(), request=evidence,
            details={"refusal": refusal, "not_sent": True},
        )

    def _can_repair(self, outcome: CallOutcome) -> bool:
        if outcome.uncertain or outcome.external_outcome == "UNKNOWN":
            return False
        if outcome.error_code not in {"LLM_FORMAT", "SCHEMA_VALIDATION_FAILED"}:
            return False
        if not self.budget.may_repair():
            return False
        if self.budget.may_call() is not None:
            return False
        return True

    @staticmethod
    def _schema_errors(outcome: CallOutcome) -> list[dict[str, Any]]:
        recorded = outcome.details.get("schema_errors")
        if isinstance(recorded, list) and recorded:
            return recorded
        return [{"field": outcome.details.get("provider_schema_path", ""),
                 "type": outcome.details.get("provider_schema_validator") or outcome.error_code}]

    def _call_and_read(
        self,
        *,
        request: CompiledRequest,
        budget: CallBudget,
        contract,
        schema: dict[str, Any] | None,
        task_category: str,
        before_call,
        on_progress,
        session_factory,
        purpose: str,
        aliases: dict[str, str] | None,
        evidence: dict[str, Any],
        corrective: bool = False,
        error_logger: Any | None = None,
        before_send=None,
        after_send=None,
    ) -> CallOutcome:
        blocked = self.budget.may_call()
        if blocked:
            finding = triage.classify_failure(blocked, unit_ids=(self.unit_id,),
                                              evidence={"purpose": purpose})
            return CallOutcome(error_code=blocked, external_outcome="NOT_SENT",
                               findings=[finding], budget=budget.as_dict(),
                               request=evidence, details={"unit_budget": self.budget.as_dict()})
        attempt: dict[str, Any] = {
            "unit_id": self.unit_id, "role": self.role, "purpose": purpose,
            "request_fingerprint": request.fingerprint,
            "input_tokens_estimated": budget.input_tokens,
            "output_request_tokens": budget.output_request_tokens,
            "corrective": corrective,
            "attempt_no": self.budget.calls + 1,
            "output_budget": budget.as_dict(),
        }
        # The intent is durable *before* the request leaves: a crash between
        # this row and the answer is reconcilable, not a guess.
        self._emit({**attempt, "phase": "SENDING"})
        self.budget.charge_call(repair=corrective)
        phases: list[str] = []

        def progress(phase: str) -> None:
            phases.append(phase)
            if on_progress:
                on_progress(phase)

        if before_send:
            before_send()
        try:
            try:
                with session_factory() as db:
                    if before_call:
                        before_call(db)
                    if accepts_precompiled(self.gateway.complete_for_role):
                        response = self.gateway.complete_for_role(
                            db, self.role, precompiled=request, schema=schema,
                            on_progress=progress, task_category=task_category)
                    else:
                        # A gateway double with the legacy keyword set: send the
                        # compiled strings as its own system/user messages.
                        response = self.gateway.complete_for_role(
                            db, self.role, system=request.system, user=request.user,
                            schema=schema, on_progress=progress, task_category=task_category)
            finally:
                # The pacing anchor is the call's real boundary - success or
                # failure - so the gap to the next call is measured from it.
                if after_send:
                    after_send()
        except LLMError as exc:
            return self._failed(exc, attempt=attempt, phases=phases, contract=contract,
                                aliases=aliases, budget=budget, evidence=evidence,
                                corrective=corrective)
        except Exception as exc:  # noqa: BLE001 - a local fault must not kill the batch
            phase = phases[-1] if phases else None
            # A missing phase record is *not* proof that nothing was sent; only
            # a phase that precedes the request may weaken the outcome.
            sent = phase is None or phase not in PRE_REQUEST_PHASES
            (error_logger or logger).exception(
                "literary_call_fault role=%s unit=%s phase=%s may_have_answered=%s",
                self.role, self.unit_id, phase, sent)
            attempt.update({"external_outcome": "UNKNOWN" if sent else "NOT_SENT",
                            "error_type": type(exc).__name__, "last_phase": phase,
                            "error_code": "LLM_PROVIDER_FAULT", "finished": True})
            self._emit({**attempt, "phase": "FINISHED"})
            finding = triage.classify_failure(
                "LLM_PROVIDER_FAULT", unit_ids=(self.unit_id,),
                evidence={"last_phase": phase, "error_type": type(exc).__name__})
            return CallOutcome(error_code="LLM_PROVIDER_FAULT",
                               external_outcome="UNKNOWN" if sent else "NOT_SENT",
                               uncertain=sent, findings=[finding], budget=budget.as_dict(),
                               request=evidence, attempts=[attempt],
                               details={"error_type": type(exc).__name__, "last_phase": phase})

        usage = _usage_tokens(response.usage)
        self.budget.charge_tokens(usage["completion_tokens"])
        meta = response.raw_meta or {}
        attempt.update({
            "external_outcome": "RESPONSE_RECEIVED", "finished": True,
            "latency_ms": response.latency_ms,
            "completion_tokens": usage["completion_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
            "finish_reason": meta.get("finish_reason"),
            "http_status": meta.get("http_status"),
            "first_event_ms": meta.get("first_event_ms"),
            "last_event_ms": meta.get("last_event_ms"),
            "streamed": bool(meta.get("streamed")),
        })
        self._emit({**attempt, "phase": "FINISHED"})
        parsed = response.parsed
        text = response.text or ""
        findings: list[triage.Finding] = []
        if parsed is None and text.strip() and contract is not None:
            # The transport returned unparsed text: read it locally before
            # declaring the answer unusable.
            recovered = salvage.salvage_answer(text, aliases=aliases)
            if recovered.payload is not None:
                parsed = recovered.payload
                findings.append(triage.Finding(
                    severity=triage.INFO, reason_code="SCHEMA_LOCAL_REPAIR",
                    message_zh="已按本地规则无损修正格式。",
                    next_action="continue_with_independent_units",
                    affected_unit_ids=(self.unit_id,),
                    source_evidence={"notes": recovered.notes}, content_saved=True))
        if parsed is not None and contract is not None:
            normalized, alias_notes = salvage.apply_aliases(parsed, aliases)
            try:
                contract.model_validate(normalized)
                if alias_notes:
                    # A complete answer that named the body ``content`` is read
                    # locally; asking the provider again would buy the same text.
                    parsed = normalized
                    findings.append(triage.Finding(
                        severity=triage.INFO, reason_code="SCHEMA_LOCAL_REPAIR",
                        message_zh="已按本地规则无损修正格式。",
                        next_action="continue_with_independent_units",
                        affected_unit_ids=(self.unit_id,),
                        source_evidence={"notes": alias_notes}, content_saved=True))
            except ValidationError as exc:
                errors = [_error_entry(error) for error in exc.errors(include_url=False)]
                finding = triage.classify_failure(
                    "SCHEMA_VALIDATION_FAILED", unit_ids=(self.unit_id,),
                    evidence={"errors": errors[:8]})
                return CallOutcome(parsed=None, text=text, payload=parsed, findings=[finding],
                                   error_code="SCHEMA_VALIDATION_FAILED",
                                   external_outcome="RESPONSE_RECEIVED",
                                   details={"schema_errors": errors, "provider_text_kept": True},
                                   usage=usage, budget=budget.as_dict(), request=evidence,
                                   attempts=[attempt], corrective=corrective,
                                   finish_reason=attempt.get("finish_reason"))
        if parsed is None:
            finding = triage.classify_failure(
                "LLM_FORMAT", unit_ids=(self.unit_id,),
                evidence={"text_chars": len(text)})
            return CallOutcome(parsed=None, text=text, findings=[finding],
                               error_code="LLM_FORMAT", external_outcome="RESPONSE_RECEIVED",
                               details={"provider_text_kept": bool(text)}, usage=usage,
                               budget=budget.as_dict(), request=evidence, attempts=[attempt],
                               corrective=corrective)
        return CallOutcome(parsed=parsed, text=text, payload=parsed, findings=findings,
                           external_outcome="RESPONSE_RECEIVED", details={}, usage=usage,
                           budget=budget.as_dict(), request=evidence, attempts=[attempt],
                           corrective=corrective, finish_reason=attempt.get("finish_reason"))

    def _failed(self, exc: LLMError, *, attempt: dict[str, Any], phases: list[str],
                contract, aliases, budget: CallBudget, evidence: dict[str, Any],
                corrective: bool) -> CallOutcome:
        external = classify_external_outcome(exc)
        raw = exc.details.get("provider_text")
        raw = raw if isinstance(raw, str) else ""
        attempt.update({"external_outcome": external, "error_code": exc.code,
                        "http_status": exc.details.get("status"),
                        "provider_code": exc.details.get("provider_code"),
                        "finish_reason": exc.details.get("finish_reason"),
                        "last_phase": phases[-1] if phases else None, "finished": True})
        self._emit({**attempt, "phase": "FINISHED"})
        recovered = salvage.salvage_answer(raw, aliases=aliases) if raw else None
        parsed: dict[str, Any] | None = None
        if recovered is not None and recovered.payload is not None and contract is not None:
            try:
                contract.model_validate(recovered.payload)
                parsed = recovered.payload
            except ValidationError:
                parsed = None
        if recovered is not None and recovered.payload is not None and contract is None:
            parsed = recovered.payload
        received_chars = len(recovered.text) if recovered is not None else 0
        schema_errors = ([{"field": exc.details.get("path", ""), "type": exc.details.get("validator")}]
                         if exc.details.get("validator") else [])
        finding = triage.classify_failure(
            exc.code, unit_ids=(self.unit_id,),
            evidence={"http_status": exc.details.get("status"),
                      "finish_reason": exc.details.get("finish_reason"),
                      "text_chars": received_chars},
            received_chars=received_chars,
            details={"schema_errors": schema_errors})
        if parsed is not None:
            finding = triage.Finding(
                severity=triage.INFO, reason_code="SCHEMA_LOCAL_REPAIR",
                message_zh="已按本地规则无损修正格式。",
                next_action="continue_with_independent_units",
                affected_unit_ids=(self.unit_id,),
                source_evidence={"notes": recovered.notes}, content_saved=True)
        return CallOutcome(
            parsed=parsed, text=(recovered.text if recovered is not None else ""),
            payload=(recovered.payload if recovered is not None else None),
            findings=[finding], error_code=None if parsed is not None else exc.code,
            external_outcome=external, uncertain=external == "UNKNOWN",
            details={"provider_text_kept": received_chars > 0,
                     "schema_errors": schema_errors,
                     "finish_reason": exc.details.get("finish_reason"),
                     "completion_tokens": exc.details.get("completion_tokens"),
                     "reasoning_tokens": exc.details.get("reasoning_tokens"),
                     "provider_schema_path": exc.details.get("path"),
                     "provider_schema_validator": exc.details.get("validator"),
                     "salvage": recovered.as_dict() if recovered is not None else None},
            usage={"completion_tokens": exc.details.get("completion_tokens") or 0,
                   "reasoning_tokens": exc.details.get("reasoning_tokens")},
            budget=budget.as_dict(), request=evidence, attempts=[attempt],
            corrective=corrective,
            finish_reason=exc.details.get("finish_reason"))

    def _emit(self, attempt: dict[str, Any]) -> None:
        if not self._record_attempt:
            return
        try:
            self._record_attempt({**attempt, "unit_budget": self.budget.as_dict(),
                                  "output_budget": attempt.get("output_budget")})
        except Exception:  # noqa: BLE001 - a ledger write must never fail a call
            logger.exception("literary_attempt_record_failed unit=%s", self.unit_id)

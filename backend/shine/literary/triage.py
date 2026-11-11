"""One failure-severity matrix for the whole literary pipeline.

The V2.1 plan replaces the old habit of turning every contract violation into a
failed task with three named levels.  This module is the only place that
decides which level a failure belongs to, so a new error path cannot invent its
own blocking rule:

``INFO`` / ``WARNING``
    The unit produced usable content.  Save it, record the note, keep going.
``LOCAL_REVIEW``
    The unit's content is preserved but a named doubt exists: only that unit -
    and the units that really depend on it - wait for a person.
``HARD_BLOCK``
    The necessary input or the necessary text is missing, or an external outcome
    is genuinely unknown.  The named units stop; everything independent keeps
    running.

Two properties are enforced in code rather than by convention:

* a ``HARD_BLOCK`` cannot be constructed without its own reason code, affected
  unit ids, source evidence and next action - the plan's §0.2 requirement;
* a received answer always outranks the contract error that describes it: a
  format or schema failure that still carried substantive text is downgraded
  from ``HARD_BLOCK`` to ``LOCAL_REVIEW`` with ``content_saved=True``, because a
  chapter is not invalidated by one wrong optional field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

INFO = "INFO"
WARNING = "WARNING"
LOCAL_REVIEW = "LOCAL_REVIEW"
HARD_BLOCK = "HARD_BLOCK"

Severity = Literal["INFO", "WARNING", "LOCAL_REVIEW", "HARD_BLOCK"]
SEVERITY_ORDER = {INFO: 0, WARNING: 1, LOCAL_REVIEW: 2, HARD_BLOCK: 3}
SCOPES = ("unit", "project")


@dataclass(frozen=True)
class TriageRule:
    """The default reading of one failure code."""

    code: str
    severity: Severity
    message_zh: str
    next_action: str
    scope: str = "unit"
    retryable: bool = False
    """Whether a *new* request may be attempted without human authorization.

    Only failures with positive proof that nothing was sent - or a provider
    refusal that produced no answer - may be retryable.  An unknown external
    outcome is never retryable here, no matter how transient it looks.
    """
    content_possible: bool = False
    """Whether this failure can still carry a complete literary answer."""


#: The single matrix.  Every runtime classification resolves through this table;
#: a code that is missing falls back to the conservative unknown-outcome reading
#: instead of silently becoming a warning.
MATRIX: dict[str, TriageRule] = {
    rule.code: rule
    for rule in (
        TriageRule(
            "CONTENT_SAVED", INFO,
            "正文已保存为候选草稿。", "continue_with_independent_units",
            content_possible=True,
        ),
        TriageRule(
            "OPTIONAL_FIELD_MISSING", WARNING,
            "已保存正文，个别可选字段缺失，可稍后补齐。", "fill_locally_or_ignore",
            content_possible=True,
        ),
        TriageRule(
            "MEMORY_OBSERVATION_INVALID", WARNING,
            "故事记忆观测未通过校验，不影响已生成正文。", "retry_memory_only",
        ),
        TriageRule(
            "SCHEMA_LOCAL_REPAIR", INFO,
            "已按本地规则无损修正格式。", "continue_with_independent_units",
            content_possible=True,
        ),
        TriageRule(
            "SCHEMA_VALIDATION_FAILED", LOCAL_REVIEW,
            "正文已保存，结构校验未通过，需人工确认。", "review_candidate",
            content_possible=True,
        ),
        TriageRule(
            "LLM_FORMAT", LOCAL_REVIEW,
            "模型输出格式不合规，但已收到正文，可复核后继续。", "review_candidate",
            content_possible=True,
        ),
        TriageRule(
            "TRUNCATED", LOCAL_REVIEW,
            "输出被截断，已保存收到的部分，需拆分后重写该单元。", "split_and_rewrite_unit",
            content_possible=True,
        ),
        TriageRule(
            "LLM_CREATIVE_CONTEXT_OVERFLOW", LOCAL_REVIEW,
            "请求超出模型窗口，未发出；需缩小该单元后重试。", "split_unit_then_retry",
            retryable=True,
        ),
        TriageRule(
            "LLM_RATE_LIMIT", LOCAL_REVIEW,
            "服务商限流，本次未产生回答；稍后可重试该单元。", "retry_after_backoff",
            retryable=True,
        ),
        TriageRule(
            "LLM_TIMEOUT", HARD_BLOCK,
            "请求超时，服务商是否已生成无法确认；请先对账再决定。", "reconcile_before_resend",
        ),
        TriageRule(
            "LLM_CONNECTION", HARD_BLOCK,
            "连接中断，服务商是否已生成无法确认；请先对账再决定。", "reconcile_before_resend",
        ),
        TriageRule(
            "LLM_SERVER", HARD_BLOCK,
            "服务商返回错误，结果未知；请先对账再决定。", "reconcile_before_resend",
        ),
        TriageRule(
            "LLM_PROVIDER_FAULT", HARD_BLOCK,
            "调用过程出现本地故障，结果未知；请先对账。", "reconcile_before_resend",
        ),
        TriageRule(
            "LLM_NOT_CONFIGURED", HARD_BLOCK,
            "模型未配置，所有后续单元都需要先完成配置。", "configure_provider",
            scope="project",
        ),
        TriageRule(
            "LLM_AUTH", HARD_BLOCK,
            "模型鉴权失败，所有后续单元都需要先修复密钥。", "fix_provider_credentials",
            scope="project",
        ),
        TriageRule(
            "LLM_MODEL_NOT_FOUND", HARD_BLOCK,
            "配置的模型不存在，请重新选择模型。", "choose_existing_model",
            scope="project",
        ),
        TriageRule(
            "LLM_MOCK_PROVIDER", HARD_BLOCK,
            "当前是模拟模型，不能作为正式作品来源。", "configure_real_provider",
            scope="project",
        ),
        TriageRule(
            "SOURCE_RANGE_MISSING", HARD_BLOCK,
            "缺少本单元对应的原文范围，无法生成。", "restore_source_range",
        ),
        TriageRule(
            "SOURCE_UNCOVERED", HARD_BLOCK,
            "原文存在未被任何单元覆盖的区间，不能视为完整改编。", "restore_source_coverage",
        ),
        TriageRule(
            "UNAUTHORIZED_EGRESS", HARD_BLOCK,
            "文本外传未获授权，已停止调用。", "request_text_consent",
            scope="project",
        ),
        TriageRule(
            "UNKNOWN_OUTCOME", HARD_BLOCK,
            "上一次调用的结果无法确认，已停止自动重发。", "reconcile_before_resend",
        ),
        TriageRule(
            "PIPELINE_SEND_OUTCOME_UNKNOWN", HARD_BLOCK,
            "上一次调用的结果无法确认，已停止自动重发。", "reconcile_before_resend",
        ),
    )
}


@dataclass(frozen=True)
class Finding:
    """One classified fact about one unit."""

    severity: Severity
    reason_code: str
    message_zh: str
    next_action: str
    affected_unit_ids: tuple[str, ...] = ()
    source_evidence: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    content_saved: bool = False
    scope: str = "unit"
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"unknown severity: {self.severity!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"unknown scope: {self.scope!r}")
        if self.severity == HARD_BLOCK:
            missing = [
                name for name, value in (
                    ("reason_code", self.reason_code),
                    ("affected_unit_ids", self.affected_unit_ids),
                    ("source_evidence", self.source_evidence),
                    ("next_action", self.next_action),
                ) if not value
            ]
            if missing:
                raise ValueError(
                    "a HARD_BLOCK must name its reason, units, evidence and next action; "
                    f"missing: {', '.join(missing)}")

    @property
    def blocks(self) -> bool:
        return self.severity == HARD_BLOCK

    @property
    def needs_review(self) -> bool:
        return self.severity in {LOCAL_REVIEW, HARD_BLOCK}

    def as_dict(self) -> dict[str, Any]:
        """The serialised form stored in a task trace and shown to a person."""
        return {
            "severity": self.severity,
            "reason_code": self.reason_code,
            "message": self.message_zh,
            "next_action": self.next_action,
            "affected_unit_ids": list(self.affected_unit_ids),
            "source_evidence": dict(self.source_evidence),
            "retryable": self.retryable,
            "content_saved": self.content_saved,
            "scope": self.scope,
            **({"details": dict(self.details)} if self.details else {}),
        }


def rule_for(code: str) -> TriageRule:
    return MATRIX.get(code) or TriageRule(
        code=code or "UNKNOWN_ERROR",
        severity=HARD_BLOCK,
        message_zh="出现未登记的错误，结果未知；请先对账。",
        next_action="reconcile_before_resend",
    )


def classify_failure(
    code: str,
    *,
    unit_ids: tuple[str, ...] | list[str] = (),
    evidence: dict[str, Any] | None = None,
    received_chars: int = 0,
    details: dict[str, Any] | None = None,
) -> Finding:
    """Classify one provider/contract failure under the single matrix.

    ``received_chars`` is the length of the literary text that really arrived.
    It is the strongest evidence available: when it is non-zero and the failure
    is about structure rather than about the provider's answer, the finding is
    downgraded to ``LOCAL_REVIEW`` and marked ``content_saved`` so the caller
    persists the draft instead of discarding it.
    """
    rule = rule_for(code)
    units = tuple(str(unit) for unit in unit_ids)
    proof = dict(evidence or {})
    severity = rule.severity
    content_saved = False
    next_action = rule.next_action
    if received_chars > 0 and rule.content_possible:
        content_saved = True
        if severity == HARD_BLOCK:
            severity = LOCAL_REVIEW
            next_action = "review_candidate"
    if severity == HARD_BLOCK and not units:
        # A hard block must be attributable.  When the caller did not name a
        # unit, the honest scope is the project itself, not "everything".
        units = ("project",)
        proof = {**proof, "scope_note": "no unit id was supplied; blocking the project"}
    if severity == HARD_BLOCK and not proof:
        proof = {"code": rule.code, "external_outcome": "UNKNOWN"}
    return Finding(
        severity=severity,
        reason_code=rule.code,
        message_zh=rule.message_zh,
        next_action=next_action,
        affected_unit_ids=units,
        source_evidence=proof,
        retryable=rule.retryable,
        content_saved=content_saved,
        scope=rule.scope,
        details=dict(details or {}),
    )


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity, SEVERITY_ORDER[HARD_BLOCK])


def worst(findings: list[Finding]) -> Finding | None:
    """The finding that decides the unit's state: the most severe, then the first."""
    if not findings:
        return None
    return max(findings, key=lambda item: severity_rank(item.severity))


def summarize(findings: list[Finding]) -> dict[str, Any]:
    """A compact, UI-safe summary of many findings.

    The web workbench translates this; internal error codes never reach the
    ordinary four-step screen as the only explanation.
    """
    top = worst(findings)
    return {
        "state": top.severity if top else INFO,
        "reason_code": top.reason_code if top else None,
        "message": top.message_zh if top else "一切正常",
        "next_action": top.next_action if top else "continue",
        "blocked_units": sorted({unit for item in findings if item.blocks
                                 for unit in item.affected_unit_ids}),
        "review_units": sorted({unit for item in findings if item.needs_review
                                for unit in item.affected_unit_ids}),
        "content_saved_units": sorted({unit for item in findings if item.content_saved
                                       for unit in item.affected_unit_ids}),
        "findings": [item.as_dict() for item in findings],
    }

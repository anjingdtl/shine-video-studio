"""Named quality gates.

Technical QC, semantic review, audio review, style review and the final gate
are evaluated separately and recorded separately. A shot may only be marked
final-passed when every gate the project's frozen quality policy requires has
actually been evaluated *and* passed.

The policy is project configuration. It may relax *non-critical* thresholds per
shot priority (A/B/C), but it can never relax a hard rule: identity, master
asset and key dialogue failures always block a shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shine.schemas import (
    FusedQCResult,
    HardFailure,
    SemanticFinding,
    SemanticReviewResult,
    ShotSpec,
    TechnicalQCResult,
)

GATE_TECHNICAL = "technical"
GATE_SEMANTIC = "semantic"
GATE_AUDIO = "audio"
GATE_STYLE = "style"
GATE_FINAL = "final"

GATE_NAMES = (GATE_TECHNICAL, GATE_SEMANTIC, GATE_AUDIO, GATE_STYLE, GATE_FINAL)

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_NOT_RUN = "not_run"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_UNAVAILABLE = "unavailable"

COVERAGE_COVERED = "covered"
COVERAGE_NOT_COVERED = "not_covered"
COVERAGE_FAILED = "failed"
COVERAGE_MOCK = "mock_covered"

VERIFICATION_REAL = "real"
VERIFICATION_MOCK = "mock_verified"
VERIFICATION_PARTIAL = "partial"

#: Failures that are never relaxed by a shot priority or a confidence threshold.
HARD_RULES = (
    "IDENTITY_MISMATCH",
    "ASSET_NOT_APPROVED",
    "ASSET_NOT_LOCKED",
    "ASSET_WRONG",
    "REFERENCE_WRONG",
    "REFERENCE_MISSING",
    "MISSING_ASSET",
    "SPEAKER_MISMATCH",
    "WRONG_LINE",
    "STYLE_BIBLE_VIOLATION",
    "TECHNICAL_FILE_EXISTS",
    "TECHNICAL_FILE_NONEMPTY",
    "TECHNICAL_CONTAINER",
    "TECHNICAL_DURATION",
    "TECHNICAL_RESOLUTION",
    "TECHNICAL_FRAME_RATE",
    "TECHNICAL_AUDIO_TRACK",
)

DEFAULT_PRIORITY_THRESHOLDS = {"A": 0.85, "B": 0.7, "C": 0.55}


@dataclass(frozen=True)
class QualityPolicy:
    """Per-project quality configuration."""

    require_semantic_review: bool = True
    require_audio_review: bool = True
    require_style_review: bool = True
    min_confidence: float = 0.7
    accept_mock_review: bool = False
    allow_unverified_technical: bool = False
    priority_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_PRIORITY_THRESHOLDS)
    )
    hard_rules: tuple[str, ...] = HARD_RULES

    def threshold_for(self, priority: str | None) -> float:
        if priority and priority in self.priority_thresholds:
            return float(self.priority_thresholds[priority])
        return float(self.min_confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_semantic_review": self.require_semantic_review,
            "require_audio_review": self.require_audio_review,
            "require_style_review": self.require_style_review,
            "min_confidence": self.min_confidence,
            "accept_mock_review": self.accept_mock_review,
            "allow_unverified_technical": self.allow_unverified_technical,
            "priority_thresholds": dict(self.priority_thresholds),
            "hard_rules": list(self.hard_rules),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> QualityPolicy:
        data = data or {}
        thresholds = dict(DEFAULT_PRIORITY_THRESHOLDS)
        declared = data.get("priority_thresholds")
        if isinstance(declared, dict):
            for key, value in declared.items():
                try:
                    thresholds[str(key).upper()] = float(value)
                except (TypeError, ValueError):
                    continue
        hard_rules = data.get("hard_rules")
        rules = tuple(str(rule) for rule in hard_rules) if isinstance(hard_rules, list) and hard_rules else HARD_RULES
        return cls(
            require_semantic_review=bool(data.get("require_semantic_review", True)),
            require_audio_review=bool(data.get("require_audio_review", True)),
            require_style_review=bool(data.get("require_style_review", True)),
            min_confidence=float(data.get("min_confidence", 0.7)),
            accept_mock_review=bool(data.get("accept_mock_review", False)),
            allow_unverified_technical=bool(data.get("allow_unverified_technical", False)),
            priority_thresholds=thresholds,
            hard_rules=rules,
        )


def default_policy() -> QualityPolicy:
    return QualityPolicy()


@dataclass(frozen=True)
class GateOutcome:
    gate_name: str
    status: str
    required: bool
    is_mock: bool
    coverage: str
    model: str | None = None
    confidence: float | None = None
    error_code: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "status": self.status,
            "required": self.required,
            "is_mock": self.is_mock,
            "coverage": self.coverage,
            "model": self.model,
            "confidence": self.confidence,
            "error_code": self.error_code,
            "evidence": self.evidence,
        }


def _finding_status(
    findings: list[SemanticFinding],
    *,
    threshold: float,
    hard_rules: tuple[str, ...],
) -> tuple[str, str | None, list[HardFailure]]:
    """Fold findings into a gate status.

    Hard rules fail regardless of confidence or shot priority.
    """
    status = STATUS_PASSED
    error_code: str | None = None
    hard_failures: list[HardFailure] = []
    for finding in findings:
        if finding.code in hard_rules:
            status = STATUS_FAILED
            error_code = error_code or finding.code
            hard_failures.append(
                HardFailure(
                    code=finding.code,
                    evidence=finding.message or finding.code,
                    evidence_time_s=finding.evidence_time_s,
                )
            )
            continue
        if finding.severity == "hard":
            if finding.confidence >= threshold:
                status = STATUS_FAILED
                error_code = error_code or finding.code
                hard_failures.append(
                    HardFailure(
                        code=finding.code,
                        evidence=finding.message or finding.code,
                        evidence_time_s=finding.evidence_time_s,
                    )
                )
            elif status != STATUS_FAILED:
                status = STATUS_NEEDS_REVIEW
                error_code = error_code or f"{finding.code}_LOW_CONFIDENCE"
        elif finding.severity == "uncertain":
            if status != STATUS_FAILED:
                status = STATUS_NEEDS_REVIEW
                error_code = error_code or f"{finding.code}_UNCERTAIN"
        else:
            if finding.confidence >= 0.8 and status != STATUS_FAILED:
                status = STATUS_NEEDS_REVIEW
                error_code = error_code or f"{finding.code}_SOFT"
    return status, error_code, hard_failures


def _technical_gate(technical: TechnicalQCResult | None, *, required: bool = True) -> GateOutcome:
    if technical is None:
        return GateOutcome(
            gate_name=GATE_TECHNICAL,
            status=STATUS_NOT_RUN,
            required=required,
            is_mock=False,
            coverage=COVERAGE_NOT_COVERED,
            error_code="TECHNICAL_QC_NOT_RUN",
            evidence={"detail": "No technical QC result was produced for this attempt."},
        )
    failing = [check for check in technical.checks if check.status == "fail"]
    unavailable = [check for check in technical.checks if check.status == "unavailable"]
    error_code = None
    if technical.result == "failed" and failing:
        error_code = f"TECHNICAL_{failing[0].name.upper()}"
    elif technical.result == "needs_review":
        error_code = (
            f"TECHNICAL_{unavailable[0].name.upper()}" if unavailable else "TECHNICAL_QC_INCOMPLETE"
        )
    coverage = COVERAGE_COVERED if technical.tool not in {"none", ""} else COVERAGE_NOT_COVERED
    return GateOutcome(
        gate_name=GATE_TECHNICAL,
        status=technical.result,
        required=required,
        is_mock=False,
        coverage=coverage,
        model=technical.tool,
        confidence=None,
        error_code=error_code,
        evidence={
            "tool": technical.tool,
            "artifact_sha256": technical.artifact_sha256,
            "byte_size": technical.byte_size,
            "duration_s": technical.duration_s,
            "width": technical.width,
            "height": technical.height,
            "fps": technical.fps,
            "has_audio": technical.has_audio,
            "checks": [check.model_dump(mode="json") for check in technical.checks],
            "unavailable_checks": [check.name for check in unavailable],
        },
    )


def _semantic_gate(
    *,
    policy: QualityPolicy,
    required: bool,
    threshold: float,
    semantic_results: list[SemanticReviewResult],
    semantic_error: str | None,
) -> tuple[GateOutcome, list[HardFailure]]:
    if not required:
        return (
            GateOutcome(
                gate_name=GATE_SEMANTIC,
                status=STATUS_NOT_APPLICABLE,
                required=False,
                is_mock=False,
                coverage=COVERAGE_NOT_COVERED,
                error_code=None,
                evidence={"detail": "The project quality policy does not require a semantic review for this shot."},
            ),
            [],
        )
    if semantic_error:
        return (
            GateOutcome(
                gate_name=GATE_SEMANTIC,
                status=STATUS_UNAVAILABLE,
                required=True,
                is_mock=False,
                coverage=COVERAGE_FAILED,
                error_code=semantic_error,
                evidence={"detail": "The semantic reviewer could not be executed."},
            ),
            [],
        )
    if not semantic_results:
        return (
            GateOutcome(
                gate_name=GATE_SEMANTIC,
                status=STATUS_NOT_RUN,
                required=True,
                is_mock=False,
                coverage=COVERAGE_NOT_COVERED,
                error_code="SEMANTIC_REVIEW_NOT_CONFIGURED",
                evidence={
                    "detail": (
                        "No semantic reviewer ran for this attempt. The shot cannot be declared "
                        "final-passed while a required semantic review is missing."
                    )
                },
            ),
            [],
        )
    is_mock = any(result.is_mock for result in semantic_results)
    findings = [finding for result in semantic_results for finding in result.findings]
    status, error_code, hard_failures = _finding_status(
        findings, threshold=threshold, hard_rules=policy.hard_rules
    )
    confidence = min((result.overall_confidence for result in semantic_results), default=1.0)
    coverage = COVERAGE_MOCK if is_mock else COVERAGE_COVERED
    evidence = {
        "reviewers": [
            {
                "reviewer": result.reviewer,
                "model": result.model,
                "is_mock": result.is_mock,
                "overall_confidence": result.overall_confidence,
                "input_sha256": result.input_sha256,
                "finding_codes": sorted({finding.code for finding in result.findings}),
                "raw_summary": result.raw_summary,
            }
            for result in semantic_results
        ],
        "finding_codes": sorted({finding.code for finding in findings}),
        "verification": VERIFICATION_MOCK if is_mock else VERIFICATION_REAL,
    }
    if is_mock and not policy.accept_mock_review:
        status = STATUS_NEEDS_REVIEW if status != STATUS_FAILED else status
        error_code = error_code or "MOCK_REVIEW_NOT_ACCEPTED"
        evidence["detail"] = (
            "The semantic review was produced by a mock reviewer. The project policy does not accept "
            "mock reviews as a final pass."
        )
    return (
        GateOutcome(
            gate_name=GATE_SEMANTIC,
            status=status,
            required=True,
            is_mock=is_mock,
            coverage=coverage,
            model=next((result.model for result in semantic_results if result.model), None),
            confidence=confidence,
            error_code=error_code,
            evidence=evidence,
        ),
        hard_failures,
    )


def _audio_gate(
    *,
    policy: QualityPolicy,
    required: bool,
    threshold: float,
    transcript_findings: list[SemanticFinding],
    asr_error: str | None,
    transcript_model: str | None,
    transcript_is_mock: bool,
) -> tuple[GateOutcome, list[HardFailure]]:
    if not required:
        return (
            GateOutcome(
                gate_name=GATE_AUDIO,
                status=STATUS_NOT_APPLICABLE,
                required=False,
                is_mock=False,
                coverage=COVERAGE_NOT_COVERED,
                evidence={"detail": "The shot has no dialogue, so no dialogue check is required."},
            ),
            [],
        )
    if asr_error:
        return (
            GateOutcome(
                gate_name=GATE_AUDIO,
                status=STATUS_UNAVAILABLE,
                required=True,
                is_mock=bool(transcript_is_mock),
                coverage=COVERAGE_FAILED,
                error_code=asr_error,
                evidence={"detail": "The speech recognition step could not be executed."},
            ),
            [],
        )
    if transcript_findings is None:
        return (
            GateOutcome(
                gate_name=GATE_AUDIO,
                status=STATUS_NOT_RUN,
                required=True,
                is_mock=bool(transcript_is_mock),
                coverage=COVERAGE_NOT_COVERED,
                error_code="ASR_NOT_CONFIGURED",
                evidence={
                    "detail": (
                        "The shot contains dialogue but no ASR ran. Dialogue coverage is explicitly "
                        "recorded as missing."
                    )
                },
            ),
            [],
        )
    status, error_code, hard_failures = _finding_status(
        transcript_findings, threshold=threshold, hard_rules=policy.hard_rules
    )
    evidence = {
        "finding_codes": sorted({finding.code for finding in transcript_findings}),
        "verification": VERIFICATION_MOCK if transcript_is_mock else VERIFICATION_REAL,
    }
    if transcript_is_mock and not policy.accept_mock_review:
        status = STATUS_NEEDS_REVIEW if status != STATUS_FAILED else status
        error_code = error_code or "MOCK_REVIEW_NOT_ACCEPTED"
        evidence["detail"] = (
            "The dialogue check was produced by a mock ASR. The project policy does not accept mock "
            "reviews as a final pass."
        )
    return (
        GateOutcome(
            gate_name=GATE_AUDIO,
            status=status,
            required=True,
            is_mock=bool(transcript_is_mock),
            coverage=COVERAGE_MOCK if transcript_is_mock else COVERAGE_COVERED,
            model=transcript_model,
            confidence=min((finding.confidence for finding in transcript_findings), default=1.0),
            error_code=error_code,
            evidence=evidence,
        ),
        hard_failures,
    )


def _style_gate(
    *,
    policy: QualityPolicy,
    required: bool,
    semantic_results: list[SemanticReviewResult],
    style_version_id: str,
    style_hash: str,
) -> GateOutcome:
    if not required:
        return GateOutcome(
            gate_name=GATE_STYLE,
            status=STATUS_NOT_APPLICABLE,
            required=False,
            is_mock=False,
            coverage=COVERAGE_NOT_COVERED,
            evidence={"detail": "The project quality policy does not require a style review."},
        )
    if not semantic_results:
        return GateOutcome(
            gate_name=GATE_STYLE,
            status=STATUS_NOT_RUN,
            required=True,
            is_mock=False,
            coverage=COVERAGE_NOT_COVERED,
            error_code="STYLE_REVIEW_NOT_COVERED",
            evidence={
                "detail": "Style conformance cannot be asserted without a semantic review.",
                "style_version_id": style_version_id,
                "style_hash": style_hash,
            },
        )
    is_mock = any(result.is_mock for result in semantic_results)
    style_findings = [
        finding
        for result in semantic_results
        for finding in result.findings
        if finding.category in {"style", "style_drift"} or finding.code.startswith("STYLE")
    ]
    status, error_code, _hard = _finding_status(
        style_findings, threshold=policy.threshold_for(None), hard_rules=policy.hard_rules
    )
    if not style_findings:
        status = STATUS_PASSED
        error_code = None
    return GateOutcome(
        gate_name=GATE_STYLE,
        status=status,
        required=True,
        is_mock=is_mock,
        coverage=COVERAGE_MOCK if is_mock else COVERAGE_COVERED,
        model=next((result.model for result in semantic_results if result.model), None),
        confidence=min((result.overall_confidence for result in semantic_results), default=1.0),
        error_code=error_code,
        evidence={
            "style_version_id": style_version_id,
            "style_hash": style_hash,
            "finding_codes": sorted({finding.code for finding in style_findings}),
            "verification": VERIFICATION_MOCK if is_mock else VERIFICATION_REAL,
        },
    )


def _final_gate(gates: list[GateOutcome]) -> GateOutcome:
    required = [gate for gate in gates if gate.required]
    failed = [gate for gate in required if gate.status == STATUS_FAILED]
    incomplete = [
        gate
        for gate in required
        if gate.status in {STATUS_NEEDS_REVIEW, STATUS_NOT_RUN, STATUS_UNAVAILABLE}
    ]
    any_mock = any(gate.is_mock for gate in required)
    unresolved_optional = [
        gate
        for gate in gates
        if not gate.required and gate.status in {STATUS_NEEDS_REVIEW, STATUS_NOT_RUN, STATUS_UNAVAILABLE}
    ]
    if any_mock:
        verification = VERIFICATION_MOCK
    elif unresolved_optional:
        verification = VERIFICATION_PARTIAL
    else:
        verification = VERIFICATION_REAL
    evidence = {
        "gates": {gate.gate_name: gate.status for gate in gates},
        "verification": verification,
        "required_gates": [gate.gate_name for gate in required],
        "unresolved_gates": [gate.gate_name for gate in incomplete],
        "unresolved_optional_gates": [gate.gate_name for gate in unresolved_optional],
    }
    if failed:
        return GateOutcome(
            gate_name=GATE_FINAL,
            status=STATUS_FAILED,
            required=True,
            is_mock=any_mock,
            coverage=COVERAGE_COVERED if not any_mock else COVERAGE_MOCK,
            error_code=failed[0].error_code or f"GATE_{failed[0].gate_name.upper()}_FAILED",
            evidence=evidence,
        )
    if incomplete:
        return GateOutcome(
            gate_name=GATE_FINAL,
            status=STATUS_NEEDS_REVIEW,
            required=True,
            is_mock=any_mock,
            coverage=COVERAGE_NOT_COVERED,
            error_code=incomplete[0].error_code or f"GATE_{incomplete[0].gate_name.upper()}_INCOMPLETE",
            evidence=evidence,
        )
    return GateOutcome(
        gate_name=GATE_FINAL,
        status=STATUS_PASSED,
        required=True,
        is_mock=any_mock,
        coverage=COVERAGE_COVERED if not any_mock else COVERAGE_MOCK,
        error_code=None,
        evidence=evidence,
    )


def evaluate_gates(
    *,
    policy: QualityPolicy,
    shot_spec: ShotSpec,
    technical: TechnicalQCResult | None,
    semantic_results: list[SemanticReviewResult] | None = None,
    semantic_error: str | None = None,
    transcript_findings: list[SemanticFinding] | None = None,
    asr_error: str | None = None,
    transcript_model: str | None = None,
    transcript_is_mock: bool = False,
    style_version_id: str = "",
    style_hash: str = "",
    reference_count: int = 0,
) -> tuple[list[GateOutcome], FusedQCResult]:
    """Evaluate every gate and return both the gate records and a fused result."""
    semantic_results = semantic_results or []
    threshold = policy.threshold_for(shot_spec.shot_priority)
    semantic_required = policy.require_semantic_review and (
        bool(shot_spec.characters) or reference_count > 0 or bool(shot_spec.dialogue)
    )
    audio_required = policy.require_audio_review and bool(shot_spec.dialogue)
    technical_required = not (
        policy.allow_unverified_technical
        and technical is not None
        and technical.result == STATUS_NEEDS_REVIEW
    )

    technical_gate = _technical_gate(technical, required=technical_required)
    semantic_gate, semantic_hard = _semantic_gate(
        policy=policy,
        required=semantic_required,
        threshold=threshold,
        semantic_results=semantic_results,
        semantic_error=semantic_error,
    )
    audio_gate, audio_hard = _audio_gate(
        policy=policy,
        required=audio_required,
        threshold=threshold,
        transcript_findings=transcript_findings,
        asr_error=asr_error,
        transcript_model=transcript_model,
        transcript_is_mock=transcript_is_mock,
    )
    style_gate = _style_gate(
        policy=policy,
        required=policy.require_style_review,
        semantic_results=semantic_results,
        style_version_id=style_version_id,
        style_hash=style_hash,
    )
    final_gate = _final_gate([technical_gate, semantic_gate, audio_gate, style_gate])

    hard_failures = list(semantic_hard) + list(audio_hard)
    if technical_gate.status == STATUS_FAILED:
        for check in (technical.checks if technical else []):
            if check.status == "fail":
                hard_failures.append(
                    HardFailure(code=f"TECHNICAL_{check.name.upper()}", evidence=check.detail or check.name)
                )

    coverage = {
        "technical": technical_gate.coverage,
        "semantic": semantic_gate.coverage,
        "asr": audio_gate.coverage,
        "style": style_gate.coverage,
        "final": final_gate.coverage,
    }
    fused = FusedQCResult(
        result=final_gate.status,
        technical=technical,
        semantic=semantic_results,
        hard_failures=hard_failures,
        soft_scores={},
        review_confidence=(
            semantic_gate.confidence
            if semantic_gate.confidence is not None
            else (audio_gate.confidence if audio_gate.confidence is not None else 1.0)
        ),
        human_review_required=final_gate.status != STATUS_PASSED,
        is_mock=any(gate.is_mock for gate in (semantic_gate, audio_gate, style_gate)),
        coverage=coverage,
    )
    return (
        [
            technical_gate,
            semantic_gate,
            audio_gate,
            style_gate,
            final_gate,
        ],
        fused,
    )


__all__ = [
    "COVERAGE_COVERED",
    "COVERAGE_FAILED",
    "COVERAGE_MOCK",
    "COVERAGE_NOT_COVERED",
    "DEFAULT_PRIORITY_THRESHOLDS",
    "GATE_AUDIO",
    "GATE_FINAL",
    "GATE_NAMES",
    "GATE_SEMANTIC",
    "GATE_STYLE",
    "GATE_TECHNICAL",
    "HARD_RULES",
    "STATUS_FAILED",
    "STATUS_NEEDS_REVIEW",
    "STATUS_NOT_APPLICABLE",
    "STATUS_NOT_RUN",
    "STATUS_PASSED",
    "STATUS_UNAVAILABLE",
    "VERIFICATION_MOCK",
    "VERIFICATION_REAL",
    "GateOutcome",
    "QualityPolicy",
    "default_policy",
    "evaluate_gates",
]

from __future__ import annotations

import pytest

from shine.quality.gates import (
    GATE_AUDIO,
    GATE_FINAL,
    GATE_SEMANTIC,
    GATE_STYLE,
    GATE_TECHNICAL,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_APPLICABLE,
    STATUS_NOT_RUN,
    STATUS_PASSED,
    STATUS_UNAVAILABLE,
    VERIFICATION_MOCK,
    QualityPolicy,
    evaluate_gates,
)
from shine.schemas import (
    DialogueLine,
    SemanticFinding,
    SemanticReviewResult,
    ShotSpec,
    TechnicalCheck,
    TechnicalQCResult,
)


def technical(*, result: str = "passed", failing: str | None = None) -> TechnicalQCResult:
    checks = [
        TechnicalCheck(name="file_exists", status="pass", detail="Artifact exists"),
        TechnicalCheck(name="decode", status="pass", detail="Every frame was decoded"),
    ]
    if failing:
        checks.append(TechnicalCheck(name=failing, status="fail", detail="failed check"))
    if result == "needs_review":
        checks.append(TechnicalCheck(name="decode", status="unavailable", detail="FFmpeg missing"))
    return TechnicalQCResult(
        result=result,
        checks=checks,
        tool="ffprobe",
        artifact_sha256="a" * 64,
        byte_size=1024,
        duration_s=6.0,
        width=768,
        height=1344,
        fps=24.0,
        has_audio=True,
    )


def semantic(*findings: SemanticFinding, is_mock: bool = True, confidence: float = 0.9) -> SemanticReviewResult:
    return SemanticReviewResult(
        reviewer="mock-vlm" if is_mock else "openai-compatible-vlm",
        model="mock" if is_mock else "gpt-4o-mini",
        is_mock=is_mock,
        input_sha256="b" * 64,
        findings=list(findings),
        overall_confidence=confidence,
        raw_summary="test",
    )


def finding(
    code: str,
    *,
    severity: str = "hard",
    confidence: float = 0.9,
    category: str = "identity",
) -> SemanticFinding:
    return SemanticFinding(
        code=code,
        category=category,
        severity=severity,
        confidence=confidence,
        message=f"{code} detected",
    )


def shot(*, priority: str = "B", dialogue: bool = False, characters: bool = True) -> ShotSpec:
    return ShotSpec(
        story_beat="雨夜码头交换线索",
        duration_target_s=6,
        shot_priority=priority,
        style_version_id="style-v1",
        characters=["林岚"] if characters else [],
        dialogue=(
            [DialogueLine(speaker_id="林岚", text="东西在���里", start_s=0.0, end_s=2.0)] if dialogue else []
        ),
    )


def gate(gates, name):
    return next(item for item in gates if item.gate_name == name)


def test_technical_pass_without_semantic_review_is_not_final_passed():
    policy = QualityPolicy()
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        reference_count=1,
    )
    assert gate(gates, GATE_TECHNICAL).status == STATUS_PASSED
    assert gate(gates, GATE_SEMANTIC).status == STATUS_NOT_RUN
    assert gate(gates, GATE_SEMANTIC).error_code == "SEMANTIC_REVIEW_NOT_CONFIGURED"
    assert gate(gates, GATE_FINAL).status == STATUS_NEEDS_REVIEW
    assert fused.result == STATUS_NEEDS_REVIEW
    assert fused.coverage["semantic"] == "not_covered"


def test_all_required_gates_pass_yields_a_final_pass():
    policy = QualityPolicy(accept_mock_review=True)
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic()],
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == STATUS_PASSED
    assert gate(gates, GATE_STYLE).status == STATUS_PASSED
    assert gate(gates, GATE_FINAL).status == STATUS_PASSED
    assert fused.result == STATUS_PASSED
    assert fused.coverage["semantic"] == "mock_covered"


def test_dialogue_without_asr_records_missing_audio_coverage():
    policy = QualityPolicy()
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(dialogue=True),
        technical=technical(),
        semantic_results=[semantic()],
        reference_count=1,
    )
    audio = gate(gates, GATE_AUDIO)
    assert audio.required is True
    assert audio.status == STATUS_NOT_RUN
    assert audio.error_code == "ASR_NOT_CONFIGURED"
    assert gate(gates, GATE_FINAL).status == STATUS_NEEDS_REVIEW
    assert fused.coverage["asr"] == "not_covered"


def test_asr_failure_is_classified_separately():
    policy = QualityPolicy()
    gates, _fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(dialogue=True),
        technical=technical(),
        semantic_results=[semantic()],
        asr_error="ASR_FAILED: timeout",
        reference_count=1,
    )
    audio = gate(gates, GATE_AUDIO)
    assert audio.status == STATUS_UNAVAILABLE
    assert audio.error_code == "ASR_FAILED: timeout"


def test_semantic_reviewer_failure_is_classified_separately():
    policy = QualityPolicy()
    gates, _fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        semantic_error="SEMANTIC_REVIEW_TIMEOUT: the reviewer did not answer",
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == STATUS_UNAVAILABLE
    assert gate(gates, GATE_SEMANTIC).error_code.startswith("SEMANTIC_REVIEW_TIMEOUT")


def test_mock_review_is_not_accepted_as_a_final_pass_by_default():
    policy = QualityPolicy()
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic(is_mock=True)],
        reference_count=1,
    )
    semantic_gate = gate(gates, GATE_SEMANTIC)
    assert semantic_gate.is_mock is True
    assert semantic_gate.status == STATUS_NEEDS_REVIEW
    assert semantic_gate.error_code == "MOCK_REVIEW_NOT_ACCEPTED"
    assert fused.is_mock is True
    assert gate(gates, GATE_FINAL).evidence["verification"] == VERIFICATION_MOCK


def test_mock_review_can_be_accepted_when_the_project_allows_it():
    policy = QualityPolicy(accept_mock_review=True)
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic(is_mock=True)],
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == STATUS_PASSED
    assert gate(gates, GATE_FINAL).status == STATUS_PASSED
    assert fused.is_mock is True
    assert gate(gates, GATE_FINAL).evidence["verification"] == VERIFICATION_MOCK


def test_hard_rules_are_never_relaxed_by_a_low_priority_shot():
    policy = QualityPolicy(priority_thresholds={"A": 0.9, "B": 0.7, "C": 0.1})
    gates, _fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(priority="C"),
        technical=technical(),
        semantic_results=[semantic(finding("IDENTITY_MISMATCH", confidence=0.2))],
        reference_count=1,
    )
    semantic_gate = gate(gates, GATE_SEMANTIC)
    assert semantic_gate.status == STATUS_FAILED
    assert semantic_gate.error_code == "IDENTITY_MISMATCH"


def test_priority_changes_only_the_non_critical_threshold():
    strict = QualityPolicy(accept_mock_review=True, priority_thresholds={"A": 0.9, "B": 0.7, "C": 0.5})
    drift = finding("STYLE_DRIFT", severity="hard", confidence=0.8, category="style")
    strict_gates, _ = evaluate_gates(
        policy=strict,
        shot_spec=shot(priority="A"),
        technical=technical(),
        semantic_results=[semantic(drift, confidence=0.8)],
        reference_count=1,
    )
    relaxed_gates, _ = evaluate_gates(
        policy=strict,
        shot_spec=shot(priority="C"),
        technical=technical(),
        semantic_results=[semantic(drift, confidence=0.8)],
        reference_count=1,
    )
    assert gate(strict_gates, GATE_SEMANTIC).status == STATUS_NEEDS_REVIEW
    assert gate(relaxed_gates, GATE_SEMANTIC).status == STATUS_FAILED


def test_hard_identity_failure_is_never_relaxed_by_priority():
    strict = QualityPolicy(accept_mock_review=True, priority_thresholds={"A": 0.9, "B": 0.7, "C": 0.1})
    gates, _ = evaluate_gates(
        policy=strict,
        shot_spec=shot(priority="C"),
        technical=technical(),
        semantic_results=[semantic(finding("IDENTITY_MISMATCH", confidence=0.2))],
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == STATUS_FAILED


def test_style_findings_fail_the_style_gate():
    policy = QualityPolicy()
    gates, _fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic(finding("STYLE_DRIFT", category="style"))],
        reference_count=1,
    )
    assert gate(gates, GATE_STYLE).status == STATUS_FAILED


def test_semantic_review_may_be_switched_off_by_policy():
    policy = QualityPolicy(require_semantic_review=False, require_style_review=False)
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(),
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == STATUS_NOT_APPLICABLE
    assert gate(gates, GATE_STYLE).status == STATUS_NOT_APPLICABLE
    assert gate(gates, GATE_FINAL).status == STATUS_PASSED
    assert fused.result == STATUS_PASSED


def test_unverified_technical_tools_may_be_downgraded_but_stay_visible():
    policy = QualityPolicy(accept_mock_review=True, allow_unverified_technical=True)
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(result="needs_review"),
        semantic_results=[semantic()],
        reference_count=1,
    )
    technical_gate = gate(gates, GATE_TECHNICAL)
    assert technical_gate.status == STATUS_NEEDS_REVIEW
    assert technical_gate.required is False
    assert technical_gate.error_code == "TECHNICAL_DECODE"
    assert gate(gates, GATE_FINAL).status == STATUS_PASSED
    assert gate(gates, GATE_FINAL).evidence["unresolved_optional_gates"] == ["technical"]
    assert fused.result == STATUS_PASSED


def test_technical_failure_always_fails_the_final_gate():
    policy = QualityPolicy()
    gates, fused = evaluate_gates(
        policy=policy,
        shot_spec=shot(),
        technical=technical(result="failed", failing="duration"),
        semantic_results=[semantic()],
        reference_count=1,
    )
    assert gate(gates, GATE_TECHNICAL).status == STATUS_FAILED
    assert gate(gates, GATE_FINAL).status == STATUS_FAILED
    assert fused.result == STATUS_FAILED
    assert any(item.code == "TECHNICAL_DURATION" for item in fused.hard_failures)


@pytest.mark.parametrize("priority", ["A", "B", "C"])
def test_priority_thresholds_are_configurable_per_project(priority):
    policy = QualityPolicy(priority_thresholds={"A": 0.9, "B": 0.7, "C": 0.5})
    assert policy.threshold_for(priority) == {"A": 0.9, "B": 0.7, "C": 0.5}[priority]

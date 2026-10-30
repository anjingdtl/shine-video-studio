"""The local small-model self-review is advisory for the production flow.

The hosted crew model, the real H3 render and the deterministic gates decide
whether a shot is produced.  The local small-model review runs exactly one
round, reports what it found for the human review that follows, and never
stops the autonomous film generation.
"""

from __future__ import annotations

from shine.quality.gates import (
    GATE_FINAL,
    GATE_SEMANTIC,
    GATE_TECHNICAL,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PASSED,
    QualityPolicy,
    evaluate_gates,
)
from shine.worker import _only_advisory_reviews_outstanding
from tests.test_quality_gates import finding, gate, semantic, shot, technical


def test_the_advisory_policy_is_explicit_and_reversible():
    policy = QualityPolicy()
    assert policy.advisory_model_reviews is True
    assert QualityPolicy.from_dict(policy.to_dict()).advisory_model_reviews is True
    strict = QualityPolicy.from_dict({"advisory_model_reviews": False})
    assert strict.advisory_model_reviews is False


def test_an_advisory_model_review_never_stops_the_production_flow():
    gates, fused = evaluate_gates(
        policy=QualityPolicy(),
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic(finding("IDENTITY_MISMATCH"), is_mock=False, confidence=1)],
        reference_count=1,
    )
    # The reviewer reported a violation and the record says so.
    assert gate(gates, GATE_SEMANTIC).status == STATUS_FAILED
    assert gate(gates, GATE_SEMANTIC).advisory is True
    assert "IDENTITY_MISMATCH" in [item.code for item in fused.hard_failures]
    # The verdict stays honest and the human review is requested.
    assert fused.result == STATUS_NEEDS_REVIEW
    assert fused.human_review_required is True
    assert fused.advisory_reviews[GATE_SEMANTIC] == STATUS_FAILED
    # Only the advisory review is outstanding, so the production continues.
    assert _only_advisory_reviews_outstanding(gates) is True


def test_a_deterministic_gate_still_stops_the_production_flow():
    gates, fused = evaluate_gates(
        policy=QualityPolicy(),
        shot_spec=shot(),
        technical=technical(result="failed", failing="duration"),
        semantic_results=[semantic(is_mock=False, confidence=1)],
        reference_count=1,
    )
    assert gate(gates, GATE_TECHNICAL).status == STATUS_FAILED
    assert fused.result == STATUS_FAILED
    assert _only_advisory_reviews_outstanding(gates) is False


def test_a_clean_review_is_a_clean_pass():
    gates, fused = evaluate_gates(
        policy=QualityPolicy(),
        shot_spec=shot(),
        technical=technical(),
        semantic_results=[semantic(is_mock=False, confidence=1)],
        reference_count=1,
    )
    assert gate(gates, GATE_FINAL).status == STATUS_PASSED
    assert fused.result == STATUS_PASSED
    assert fused.human_review_required is False
    assert _only_advisory_reviews_outstanding(gates) is False


def test_a_missing_review_is_not_a_model_opinion():
    """A review that never ran is a fact about the run, not an opinion."""
    gates, fused = evaluate_gates(
        policy=QualityPolicy(),
        shot_spec=shot(),
        technical=technical(),
        reference_count=1,
    )
    assert gate(gates, GATE_SEMANTIC).status == "not_run"
    assert fused.result == STATUS_NEEDS_REVIEW
    # The shot waits: the review never happened.
    assert _only_advisory_reviews_outstanding(gates) is False

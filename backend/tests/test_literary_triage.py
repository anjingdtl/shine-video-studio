"""The one failure matrix: what may block, what must be saved, what may retry."""

from __future__ import annotations

import pytest

from shine.literary import salvage, triage
from shine.literary.adapters import SceneDraft, adapt_scene_script, extract_dialogue

pytestmark = pytest.mark.unit

SCENE = {"section_id": "sec-1", "scene_key": "S1", "title": "出海", "order": 1}


# ---------------------------------------------------------------------------
# Matrix invariants
# ---------------------------------------------------------------------------


def test_a_hard_block_must_name_reason_units_evidence_and_next_action():
    with pytest.raises(ValueError) as error:
        triage.Finding(severity=triage.HARD_BLOCK, reason_code="SOMETHING",
                       message_zh="坏了", next_action="")
    assert "next_action" in str(error.value)

    with pytest.raises(ValueError) as error:
        triage.Finding(severity=triage.HARD_BLOCK, reason_code="",
                       message_zh="坏了", next_action="do_something",
                       affected_unit_ids=("u1",), source_evidence={"a": 1})
    assert "reason_code" in str(error.value)


def test_every_hard_block_rule_carries_a_next_action():
    for code, rule in triage.MATRIX.items():
        assert rule.next_action, code
        assert rule.message_zh, code
        assert rule.scope in {"unit", "project"}, code


def test_an_unregistered_code_is_a_hard_block_not_a_warning():
    finding = triage.classify_failure("SOMETHING_NEW", unit_ids=("u1",))
    assert finding.severity == triage.HARD_BLOCK
    assert finding.next_action == "reconcile_before_resend"


def test_a_complete_answer_downgrades_its_own_contract_error():
    """The chapter outranks the envelope that describes it."""
    finding = triage.classify_failure("SCHEMA_VALIDATION_FAILED", unit_ids=("u1",),
                                      received_chars=4_200)
    assert finding.severity == triage.LOCAL_REVIEW
    assert finding.content_saved is True
    assert finding.next_action == "review_candidate"


def test_a_transport_failure_never_becomes_a_review_just_because_bytes_arrived():
    """Timeout / 5xx may own an answer upstream: the block stays."""
    finding = triage.classify_failure("LLM_TIMEOUT", unit_ids=("u1",), received_chars=99)
    assert finding.severity == triage.HARD_BLOCK
    assert finding.retryable is False


def test_only_provably_unsent_or_refused_calls_are_retryable():
    assert triage.classify_failure("LLM_RATE_LIMIT", unit_ids=("u1",)).retryable is True
    assert triage.classify_failure("LLM_CREATIVE_CONTEXT_OVERFLOW", unit_ids=("u1",)).retryable is True
    for code in ("LLM_TIMEOUT", "LLM_CONNECTION", "LLM_SERVER", "UNKNOWN_OUTCOME"):
        assert triage.classify_failure(code, unit_ids=("u1",)).retryable is False, code


def test_a_project_failure_names_the_project_instead_of_everything():
    finding = triage.classify_failure("LLM_NOT_CONFIGURED")
    assert finding.severity == triage.HARD_BLOCK
    assert finding.scope == "project"
    assert finding.affected_unit_ids == ("project",)


def test_summary_separates_blocked_reviewed_and_saved_units():
    findings = [
        triage.classify_failure("LLM_FORMAT", unit_ids=("s1",), received_chars=500),
        triage.classify_failure("LLM_TIMEOUT", unit_ids=("s2",)),
    ]
    summary = triage.summarize(findings)
    assert summary["state"] == triage.HARD_BLOCK
    assert summary["blocked_units"] == ["s2"]
    assert summary["content_saved_units"] == ["s1"]
    assert summary["message"]


# ---------------------------------------------------------------------------
# Local lossless salvage
# ---------------------------------------------------------------------------


def test_a_fenced_answer_is_read_without_touching_the_prose():
    raw = '```json\n{"text": "德明：我要出海。\\n风很大。", "continuity_out": "登船"}\n```'
    result = salvage.salvage_answer(raw)
    assert result.payload is not None
    assert result.payload["text"] == "德明：我要出海。\n风很大。"


def test_prose_before_the_object_is_tolerated():
    raw = '好的，以下是剧本：\n{"text": "第一场。"}\n希望满意。'
    result = salvage.salvage_answer(raw)
    assert result.payload["text"] == "第一场。"
    assert "object_extracted_from_envelope" in result.notes


def test_a_trailing_comma_is_repaired_and_recorded():
    result = salvage.salvage_answer('{"text": "一场戏", "continuity_out": "结束",}')
    assert result.payload["text"] == "一场戏"
    assert "trailing_comma_removed" in result.notes


def test_a_complete_body_without_any_json_is_still_kept():
    result = salvage.salvage_answer("第一场\n德明站在船头。\n德明：起风了。")
    assert result.payload is None
    assert result.text.startswith("第一场")
    assert "no_json_object_raw_text_kept" in result.notes


def test_brace_inside_dialogue_does_not_break_the_scan():
    raw = '{"text": "他念道：{看不懂}", "continuity_out": "离开"}'
    result = salvage.salvage_answer(raw)
    assert result.payload["text"] == "他念道：{看不懂}"


def test_salvage_never_rewrites_a_field_that_already_exists():
    raw = '{"text": "正文", "content": "另一个正文"}'
    result = salvage.salvage_answer(raw, aliases={"content": "text"})
    assert result.payload["text"] == "正文"


# ---------------------------------------------------------------------------
# Stage adapters
# ---------------------------------------------------------------------------


def test_server_owned_fields_are_written_from_the_trusted_section():
    adapted = adapt_scene_script({"text": "正文", "section_id": "模型乱写", "scene_key": "X9"},
                                 section=SCENE)
    assert adapted.payload["scene_key"] == "S1"
    assert adapted.payload["section_id"] == "sec-1"
    assert "模型乱写" not in str(adapted.payload["section_id"])


def test_an_overlong_synopsis_is_capped_by_name_while_the_scene_is_untouched():
    long_text = "浪" * 30_000
    adapted = adapt_scene_script({"text": long_text, "continuity_out": "尾" * 600},
                                 section=SCENE)
    assert adapted.payload["text"] == long_text
    assert len(adapted.payload["continuity_out"]) == 400
    assert any(note.startswith("metadata_capped:continuity_out") for note in adapted.notes)


def test_a_missing_hand_off_is_a_named_gap_not_a_lost_scene():
    adapted = adapt_scene_script({"text": "正文"}, section=SCENE)
    assert adapted.has_text
    assert "continuity_out" in adapted.gaps
    assert adapted.complete is False


def test_dialogue_is_indexed_locally_and_deliberate_repeats_survive():
    text = "德明：走。\n德明：走。\n（转场）\n阿娘：回来。"
    adapted = adapt_scene_script({"text": text}, section=SCENE)
    assert len(adapted.payload["dialogue"]) == 3
    assert [line["line"] for line in adapted.payload["dialogue"]] == ["走。", "走。", "回来。"]


def test_labels_are_not_mistaken_for_speakers():
    assert extract_dialogue("时间：黄昏\n地点：码头\n德明：到了。") == [
        {"speaker": "德明", "line": "到了。"}]


def test_the_minimal_scene_contract_sends_only_what_the_model_must_own():
    schema = SceneDraft.model_json_schema()
    assert set(schema["properties"]) == {"text", "continuity_out"}
    assert schema["required"] == ["text"]

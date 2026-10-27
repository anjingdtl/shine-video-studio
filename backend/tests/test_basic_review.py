import pytest

from shine.models import Job
from shine.quality.basic_review import (
    CharacterEvidence,
    CharacterObservation,
    CharacterReviewResponse,
    DialogueEvidence,
    character_evidence_from_response,
    dialogue_evidence_from_transcript,
    evaluate_basic_gates,
    evaluate_basic_review,
    validate_character_response,
)
from shine.quality.semantic import Transcript
from shine.schemas import DialogueLine, SemanticFinding, ShotSpec, TechnicalCheck, TechnicalQCResult
from tests.conftest import create_project, create_shot, freeze_style


def sample(*, characters=None, dialogue=False):
    return ShotSpec(
        story_beat="sample", duration_target_s=6, characters=characters or [],
        dialogue=[DialogueLine(speaker_id="A", text="你好", start_s=0, end_s=2)] if dialogue else [],
    )


def technical(*, extra=(), width=768):
    return TechnicalQCResult(
        result="failed" if extra else "passed", tool="ffprobe", artifact_sha256="a" * 64,
        byte_size=100, width=width, height=1344,
        checks=[TechnicalCheck(name=n, status="pass") for n in
                ("file_exists", "file_nonempty", "container", "stream_metadata", "resolution", "decode")]
        + list(extra),
    )


def review(shot, **kwargs):
    return evaluate_basic_review(shot=shot, technical=technical(), expected_resolution=(768, 1344), **kwargs)


def test_basic_scope_does_not_require_style_framerate_or_duration():
    result = evaluate_basic_review(
        shot=sample(), expected_resolution=(768, 1344),
        technical=technical(extra=[TechnicalCheck(name="frame_rate", status="fail")]),
    )
    assert (result.result, result.characters, result.dialogue) == ("passed", "not_applicable", "not_applicable")


def test_missing_or_mock_character_and_audio_evidence_cannot_pass():
    shot = sample(characters=["A"], dialogue=True)
    assert review(shot).result == "needs_review"
    result = review(shot, characters=(CharacterEvidence("A", "passed", "a" * 64, "vlm", True),),
                    dialogue=DialogueEvidence("a" * 64, "asr", True, True))
    assert (result.characters, result.dialogue, result.result) == ("needs_review", "needs_review", "needs_review")


def test_real_character_and_dialogue_evidence_must_match_artifact_and_character():
    shot = sample(characters=["A"], dialogue=True)
    character = CharacterEvidence("A", "passed", "a" * 64, "vlm", False)
    matching = review(shot, characters=(character,), dialogue=DialogueEvidence("a" * 64, "asr", False, True))
    assert matching.result == "passed"
    wrong_artifact = review(shot, characters=(character,), dialogue=DialogueEvidence("b" * 64, "asr", False, True))
    assert wrong_artifact.result == "needs_review"
    unmatched = review(shot, characters=(character,), dialogue=DialogueEvidence("a" * 64, "asr", False, False))
    assert unmatched.dialogue == "needs_review"
    assert review(shot, characters=(CharacterEvidence("B", "passed", "a" * 64, "vlm", False),)).result == "needs_review"


def test_wrong_resolution_failed_decode_and_dialogue_failure_block():
    wrong_size = evaluate_basic_review(shot=sample(), technical=technical(width=512), expected_resolution=(768, 1344))
    assert wrong_size.result == "failed"
    broken = technical(extra=[TechnicalCheck(name="decode", status="fail")])
    assert evaluate_basic_review(shot=sample(), technical=broken, expected_resolution=(768, 1344)).result == "failed"
    bad_line = SemanticFinding(code="WRONG_LINE", category="audio", severity="hard", confidence=0.9)
    shot = sample(characters=["A"], dialogue=True)
    result = review(shot, characters=(CharacterEvidence("A", "passed", "a" * 64, "vlm", False),),
                    dialogue=DialogueEvidence("a" * 64, "asr", False, False, (bad_line,)))
    assert result.dialogue == result.result == "failed"


def test_invalid_expectation_and_unbound_digest_cannot_pass():
    with pytest.raises(ValueError):
        evaluate_basic_review(shot=sample(), technical=technical(), expected_resolution=(0, 1344))
    unbound = technical().model_copy(update={"artifact_sha256": "not-a-digest"})
    result = evaluate_basic_review(shot=sample(), technical=unbound, expected_resolution=(768, 1344))
    assert result.result == "needs_review"
    missing_size = technical().model_copy(update={"width": None})
    result = evaluate_basic_review(shot=sample(), technical=missing_size, expected_resolution=(768, 1344))
    assert result.result == "needs_review"


def test_dialogue_adapter_uses_actual_transcript_and_preserves_mock_origin():
    shot = sample(characters=["A"], dialogue=True)
    character = CharacterEvidence("A", "passed", "a" * 64, "vlm", False)
    good = dialogue_evidence_from_transcript(shot, Transcript(text="你好。", model="asr", is_mock=False), "a" * 64)
    assert review(shot, characters=(character,), dialogue=good).dialogue == "passed"
    absent = dialogue_evidence_from_transcript(shot, Transcript(text="", model="asr", is_mock=False), "a" * 64)
    assert review(shot, characters=(character,), dialogue=absent).dialogue == "needs_review"
    mocked = dialogue_evidence_from_transcript(shot, Transcript(text="你好", model="asr", is_mock=True), "a" * 64)
    assert review(shot, characters=(character,), dialogue=mocked).dialogue == "needs_review"


def test_dialogue_must_contain_all_lines_in_order_including_repetitions():
    shot = sample(characters=["A"], dialogue=True)
    shot.dialogue.append(DialogueLine(speaker_id="A", text="再见", start_s=2, end_s=3))
    shot.dialogue.append(DialogueLine(speaker_id="A", text="你好", start_s=3, end_s=4))
    for transcript in ("再见，你好，你好", "你好，再见"):
        evidence = dialogue_evidence_from_transcript(
            shot, Transcript(text=transcript, model="asr", is_mock=False), "a" * 64
        )
        assert evidence.matched_script is False
    evidence = dialogue_evidence_from_transcript(
        shot, Transcript(text="你好，再见，你好", model="asr", is_mock=False), "a" * 64
    )
    assert evidence.matched_script is True


def test_basic_gates_record_scope_without_promoting_missing_or_mock_evidence():
    shot = sample(characters=["A"], dialogue=True)
    character = CharacterEvidence("A", "passed", "a" * 64, "vlm", False)
    dialogue = DialogueEvidence("a" * 64, "asr", False, True)
    gates, fused = evaluate_basic_gates(shot=shot, technical=technical(), expected_resolution=(768, 1344),
                                        artifact_sha256="a" * 64, characters=(character,), dialogue=dialogue)
    assert fused.result == "passed" and fused.coverage["scope"] == "basic-self-review-v1"
    assert gates[3].status == "not_applicable" and not gates[3].required
    assert gates[-1].evidence["scope"] == "basic-self-review-v1"
    _, wrong_file = evaluate_basic_gates(shot=shot, technical=technical(), expected_resolution=(768, 1344),
                                         artifact_sha256="b" * 64, characters=(character,), dialogue=dialogue)
    assert wrong_file.result == "needs_review"
    _, mock = evaluate_basic_gates(shot=shot, technical=technical(), expected_resolution=(768, 1344),
                                  artifact_sha256="a" * 64,
                                  characters=(CharacterEvidence("A", "passed", "a" * 64, "vlm", True),),
                                  dialogue=dialogue)
    assert mock.result == "needs_review" and mock.is_mock
    _, simulated = evaluate_basic_gates(shot=sample(), technical=technical(), expected_resolution=(768, 1344),
                                       artifact_sha256="a" * 64, offline_simulation=True)
    assert simulated.result == "needs_review" and simulated.is_mock


def test_character_contract_requires_exact_coverage_and_bound_reference():
    observation = CharacterObservation(character="A", status="passed", observation="Visible", reference_compared=False)
    response = CharacterReviewResponse(characters=[observation])
    validate_character_response(response, expected_characters=["A"], reference_characters=set())
    generic = CharacterReviewResponse(characters=[observation.model_copy(update={"observation": "passed"})])
    with pytest.raises(ValueError, match="CHARACTER_OBSERVATION_MISSING"):
        validate_character_response(generic, expected_characters=["A"], reference_characters=set())
    with pytest.raises(ValueError, match="CHARACTER_REFERENCE_NOT_COMPARED"):
        validate_character_response(response, expected_characters=["A"], reference_characters={"A"})
    compared = CharacterReviewResponse(characters=[observation.model_copy(update={"reference_compared": True})])
    with pytest.raises(ValueError, match="CHARACTER_REFERENCE_UNBOUND"):
        validate_character_response(compared, expected_characters=["A"], reference_characters=set())
    validate_character_response(compared, expected_characters=["A"], reference_characters={"A"})
    evidence = character_evidence_from_response(
        compared, expected_characters=["A"], reference_characters={"A"},
        artifact_sha256="a" * 64, reviewer="vlm", is_mock=False,
    )
    assert review(sample(characters=["A"]), characters=evidence).characters == "passed"
    with pytest.raises(ValueError, match="CHARACTER_COVERAGE_INVALID"):
        validate_character_response(response, expected_characters=["A", "B"], reference_characters=set())
    duplicated = CharacterReviewResponse(characters=[observation, observation])
    with pytest.raises(ValueError, match="CHARACTER_COVERAGE_INVALID"):
        validate_character_response(duplicated, expected_characters=["A", "B"], reference_characters=set())
    second = observation.model_copy(update={"character": "B"})
    with pytest.raises(ValueError, match="CHARACTER_IDENTITY_UNBOUND"):
        validate_character_response(CharacterReviewResponse(characters=[observation, second]),
                                    expected_characters=["A", "B"], reference_characters=set())


def test_job_persists_explicit_review_dimensions_without_changing_workflow(studio):
    client, factory, _root, _settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    path = f"/api/v1/projects/{project}/jobs"
    response = client.post(
        path, json={"shot_id": shot["id"], "self_review_scope": "basic",
                    "basic_review_resolution": {"width": 768, "height": 1344}}
    )
    assert response.status_code == 201, response.text
    expected = {"width": 768, "height": 1344}
    assert response.json()["request"]["basic_review_resolution"] == expected
    assert response.json()["request"]["self_review_scope"] == "basic"
    with factory() as db:
        job = db.get(Job, response.json()["id"])
        assert job.request["basic_review_resolution"] == expected
        assert job.request["self_review_scope"] == "basic"
        assert job.request["workflow_profile_hash"] is None
    for invalid in ({"width": 0, "height": 1344}, {"width": "768", "height": 1344}):
        assert client.post(path, json={"shot_id": shot["id"], "basic_review_resolution": invalid}).status_code == 422
    assert client.post(path, json={"shot_id": shot["id"], "self_review_scope": "basic"}).status_code == 422

"""The worker reports real review failure classes without accepting a result."""

import httpx
import pytest

from shine.quality.semantic import ReceivedReviewError
from shine.schemas import SemanticReviewResult, ShotSpec, StyleBible
from shine.worker import _semantic_review, _transcript_review
from tests.test_film_crew import ENGLISH_STYLE


@pytest.mark.parametrize(("exception", "expected"), [
    (ReceivedReviewError("VLM_RESPONSE_CONTRACT_INVALID", "a" * 64,
                         validation={"code": "STYLE_INPUT_DECISION_CONTRADICTION"}),
     "SEMANTIC_REVIEW_CONTRACT_INVALID: STYLE_INPUT_DECISION_CONTRADICTION"),
    (ReceivedReviewError("VLM_RESPONSE_CONTRACT_INVALID", "a" * 64, validation={"code": "JSON_DECODE_FAILED"}),
     "SEMANTIC_REVIEW_INVALID_JSON: JSON_DECODE_FAILED"),
    (ReceivedReviewError("VLM_RESPONSE_CONTRACT_INVALID", "a" * 64, validation={"code": "private response text"}),
     "SEMANTIC_REVIEW_CONTRACT_INVALID: RESPONSE_REJECTED"),
    (httpx.ReadTimeout("private URL or response"), "SEMANTIC_REVIEW_TIMEOUT: response unavailable"),
])
def test_worker_distinguishes_received_contract_failure_and_unknown_timeout(api, tmp_path, exception, expected):
    _, _, settings = api

    class Reviewer:
        is_mock = True

        def review(self, context):
            raise exception

    results, error = _semantic_review(
        reviewer=Reviewer(), job_id="synthetic", spec=ShotSpec(story_beat="fixture", duration_target_s=6,
                                                               style_version_id="fixture"),
        style=StyleBible.model_validate(ENGLISH_STYLE), artifact_path=tmp_path / "synthetic.mp4",
        artifact_sha256="a" * 64, settings=settings, remote_consent=False, project_id="synthetic", transcript=None,
    )
    assert results == [] and error == expected
    assert "private" not in error


@pytest.mark.parametrize("transcript_kind", ["missing", "unverified", "mock", "real", "real-hard"])
def test_image_review_cannot_invent_missing_dialogue_without_real_asr(tmp_path, monkeypatch, transcript_kind):
    import json
    from dataclasses import replace

    from shine.llm.base import ProviderConfig
    from shine.quality.semantic import OpenAICompatibleVisionReviewer, Transcript
    from tests.review_fixtures import fixture_style_input_checks
    from tests.test_semantic_qc import context

    value = {"reviewer": "synthetic", "is_mock": True, "input_sha256": "a" * 64,
             "findings": [{"code": "MISSING_DIALOGUE", "category": "Dialogue",
                           "severity": "uncertain" if transcript_kind == "real" else "hard",
                           "confidence": 1}], "verdict": "failed", "style_input_checks": fixture_style_input_checks(),
             "overall_confidence": 1, "raw_summary": "Synthetic finding"}
    original = httpx.Client
    monkeypatch.setattr("shine.quality.semantic.httpx.Client", lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(value)}}]})), **kw))
    transcript = (None if transcript_kind == "missing" else "unverified" if transcript_kind == "unverified"
                  else Transcript("synthetic", is_mock=transcript_kind == "mock"))
    cfg = ProviderConfig("fixture", "fixture", "openai_compatible", "http://127.0.0.1:1", "fixture", None)
    reviewer = OpenAICompatibleVisionReviewer(cfg)
    ctx = replace(context(tmp_path, consent=True), transcript=transcript)
    if transcript_kind == "real":
        assert reviewer.review(ctx).findings[0].code == "MISSING_DIALOGUE"
    else:
        with pytest.raises(ReceivedReviewError) as error:
            reviewer.review(ctx)
        assert error.value.validation["code"] == (
            "VLM_DIALOGUE_HARD_UNSUPPORTED" if transcript_kind == "real-hard" else "VLM_DIALOGUE_EVIDENCE_MISSING")


def test_mock_media_providers_cannot_erase_their_provenance_in_worker(api, tmp_path):
    from shine.quality.semantic import Transcript

    _, _, settings = api
    spec = ShotSpec(story_beat="fixture", duration_target_s=6, style_version_id="fixture",
                    characters=["fixture"],
                    dialogue=[{"speaker_id": "fixture", "text": "hello", "start_s": 1, "end_s": 2}])

    class MockProvider:
        is_mock = True

        def review(self, context):
            return SemanticReviewResult(reviewer="fixture", is_mock=False, input_sha256="a" * 64,
                                        verdict="passed", findings=[], overall_confidence=1)

        def transcribe(self, path):
            return Transcript("hello", is_mock=False, model="synthetic")

    provider = MockProvider()
    transcript, _, error = _transcript_review(asr=provider, artifact_path=tmp_path / "fixture", spec=spec)
    assert not error and transcript.is_mock is True
    results, error = _semantic_review(
        reviewer=provider, job_id="synthetic", spec=spec, style=StyleBible.model_validate(ENGLISH_STYLE),
        artifact_path=tmp_path / "fixture", artifact_sha256="a" * 64, settings=settings,
        remote_consent=False, project_id="synthetic", transcript=transcript,
    )
    assert not error and results[0].is_mock is True


def test_compatible_media_transport_cannot_erase_mock_envelope(tmp_path, monkeypatch):
    import json

    from shine.llm.base import ProviderConfig
    from shine.quality.semantic import OpenAICompatibleASRReviewer, OpenAICompatibleVisionReviewer
    from tests.review_fixtures import fixture_style_input_checks
    from tests.test_semantic_qc import context

    wire = {"reviewer": "synthetic", "is_mock": False, "input_sha256": "a" * 64,
            "findings": [], "verdict": "passed", "style_input_checks": fixture_style_input_checks(),
            "overall_confidence": 1, "raw_summary": "Synthetic"}

    def respond(request):
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "fixture", "is_mock": False, "mock": True})
        return httpx.Response(200, json={"mock": True, "choices": [{"message": {"content": json.dumps(wire)}}]})

    original = httpx.Client
    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig("fixture", "fixture", "openai_compatible", "https://fixture.invalid/v1", "fixture", None)
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    assert OpenAICompatibleASRReviewer(cfg).transcribe(audio).is_mock is True
    assert OpenAICompatibleVisionReviewer(cfg).review(context(tmp_path, consent=True)).is_mock is True


def test_mock_transport_cannot_claim_a_real_local_executor_identity():
    from shine.llm.base import ProviderConfig
    from shine.quality.semantic import LocalVisionReviewer

    cfg = ProviderConfig("fixture", "fixture", "openai_compatible", "http://127.0.0.1:1", "fixture", None)
    identity = {"schema_version": "1", "model_id": "fixture", "files_sha256": "a" * 64,
                "runtime_sha256": "b" * 64, "file_count": 3, "bytes": 42}
    envelope = {"model": "fixture", "is_mock": False, "mock": True, "executor_identity": identity}
    with pytest.raises(ReceivedReviewError, match="EXECUTOR_IDENTITY_INVALID"):
        LocalVisionReviewer(cfg).record_executor_identity(envelope, "c" * 64)

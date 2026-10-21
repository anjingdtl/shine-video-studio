from __future__ import annotations

from pathlib import Path

from shine.llm.base import ProviderConfig
from shine.media.probe import make_mp4_fixture
from shine.quality.semantic import (
    MockASRReviewer,
    MockSemanticReviewer,
    OpenAICompatibleVisionReviewer,
    ReviewContext,
    Transcript,
    dialogue_findings,
    fuse,
)
from shine.quality.technical import TechnicalQC
from shine.schemas import SemanticFinding, ShotSpec, StyleBible, TechnicalCheck, TechnicalQCResult

STYLE = StyleBible(
    photography_language="平视",
    shot_blocking="轴线",
    color_palette="青灰",
    lighting_tone="侧光",
    lens_texture="浅景深",
    editing_rhythm="停顿后切",
    sound_performance="自然语速",
)


def passed_technical(tmp_path: Path):
    return TechnicalQCResult(
        result="passed",
        checks=[TechnicalCheck(name="file_exists", status="pass", detail="synthetic")],
        tool="synthetic-test",
        artifact_sha256="a" * 64,
        byte_size=1024,
    )


def context(tmp_path: Path, *, consent: bool = False) -> ReviewContext:
    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(make_mp4_fixture())
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    return ReviewContext(
        job_id="job-1",
        shot_spec=ShotSpec(story_beat="对话", duration_target_s=6, style_version_id="s1"),
        style=STYLE,
        artifact_path=artifact,
        artifact_sha256="a" * 64,
        frame_paths=(frame,),
        remote_consent=consent,
    )


def test_hard_mock_findings_never_pass_and_low_confidence_needs_review(tmp_path):
    technical = passed_technical(tmp_path)
    hard = MockSemanticReviewer(
        [SemanticFinding(code="IDENTITY_MISMATCH", category="character", severity="hard", confidence=0.9)]
    ).review(context(tmp_path))
    fused = fuse(technical=technical, semantic_results=[hard])
    assert fused.result == "failed"
    assert fused.hard_failures[0].code == "IDENTITY_MISMATCH"
    assert fused.is_mock is True
    assert fused.coverage["semantic"] == "mock"

    uncertain = MockSemanticReviewer(
        [SemanticFinding(code="IDENTITY_MISMATCH", category="character", severity="hard", confidence=0.5)]
    ).review(context(tmp_path))
    fused_low = fuse(technical=technical, semantic_results=[uncertain])
    assert fused_low.result == "needs_review"
    assert fused_low.human_review_required is True


def test_soft_high_confidence_finding_requires_review(tmp_path):
    technical = passed_technical(tmp_path)
    soft = MockSemanticReviewer(
        [SemanticFinding(code="STYLE_DRIFT", category="style", severity="soft", confidence=0.85)]
    ).review(context(tmp_path))
    fused = fuse(technical=technical, semantic_results=[soft])
    assert fused.result == "needs_review"
    assert fused.soft_scores["style"] > 0
    low = MockSemanticReviewer(
        [SemanticFinding(code="STYLE_DRIFT", category="style", severity="soft", confidence=0.2)]
    ).review(context(tmp_path))
    assert fuse(technical=technical, semantic_results=[low]).result == "passed"


def test_no_semantic_reviewer_limits_coverage_and_cannot_claim_semantic_quality(tmp_path):
    fused = fuse(technical=passed_technical(tmp_path), semantic_results=[])
    assert fused.coverage["semantic"] == "not_run"
    assert fused.is_mock is False
    assert fused.result == "passed"


def test_dialogue_transcript_mismatch_is_uncertain_not_an_automatic_pass(tmp_path):
    spec = ShotSpec(
        story_beat="对白",
        duration_target_s=6,
        style_version_id="s1",
        characters=["CHAR_A"],
        dialogue=[{"speaker_id": "CHAR_A", "text": "账册在哪儿？", "start_s": 0.4, "end_s": 2.0}],
    )
    matching = dialogue_findings(spec, Transcript(text="账册在哪儿", is_mock=True))
    assert matching == []
    missing = dialogue_findings(spec, Transcript(text="别的东西", is_mock=True))
    assert missing and missing[0].severity == "uncertain"
    fused = fuse(technical=passed_technical(tmp_path), transcript_findings=missing)
    assert fused.result == "needs_review"
    assert fused.coverage["asr"] == "checked"


def test_mock_asr_returns_a_transcript():
    transcript = MockASRReviewer("账册在哪儿").transcribe(Path("unused.wav"))
    assert transcript.text == "账册在哪儿" and transcript.is_mock is True


def test_remote_vlm_requires_explicit_consent_and_derived_frames(tmp_path):
    reviewer = OpenAICompatibleVisionReviewer(
        ProviderConfig(
            profile_id="p",
            name="vlm",
            kind="openai_compatible",
            base_url="http://127.0.0.1:1/v1",
            model="vlm",
            api_key="x",
            timeout_s=1,
        )
    )
    try:
        reviewer.review(context(tmp_path, consent=False))
    except PermissionError as exc:
        assert "consent" in str(exc)
    else:
        raise AssertionError("remote review must require consent")


def test_technical_failure_overrides_semantic_mock_pass(tmp_path):
    failed = TechnicalQC(ffprobe_path="missing", ffmpeg_path="missing").check(tmp_path / "missing.mp4")
    fused = fuse(technical=failed, semantic_results=[MockSemanticReviewer([]).review(context(tmp_path))])
    assert fused.result == "failed"
    assert fused.hard_failures

from __future__ import annotations

import pytest

from shine.media.probe import make_mp4_fixture, parse_bytes, probe_file
from shine.quality.technical import TechnicalExpectations, TechnicalQC


def offline_qc() -> TechnicalQC:
    return TechnicalQC(ffprobe_path="definitely-missing-ffprobe", ffmpeg_path="definitely-missing-ffmpeg")


def test_container_probe_extracts_duration_resolution_fps_and_audio(tmp_path):
    path = tmp_path / "shot.mp4"
    path.write_bytes(make_mp4_fixture(duration_s=6, width=768, height=1344, fps=24, audio=True))
    info = probe_file(path, ffprobe_path="definitely-missing", ffmpeg_path="definitely-missing")
    assert info.tool == "python-fallback"
    assert info.decoded is False
    assert abs((info.duration_s or 0) - 6.0) < 0.05
    assert (info.width, info.height) == (768, 1344)
    assert info.has_audio is True and info.has_video is True
    assert info.fps and abs(info.fps - 24) < 1


def test_technical_qc_never_claims_decode_without_ffprobe(tmp_path):
    path = tmp_path / "shot.mp4"
    path.write_bytes(make_mp4_fixture(duration_s=6, audio=True))
    result = offline_qc().check(path, expectations=TechnicalExpectations(duration_s=6))
    checks = {check.name: check.status for check in result.checks}
    assert result.result == "needs_review"
    assert checks["file_exists"] == "pass"
    assert checks["container"] == "pass"
    assert checks["decode"] == "unavailable"
    assert checks["duration"] == "pass"
    assert checks["resolution"] == "pass"
    assert checks["frame_rate"] == "pass"
    assert checks["audio_track"] == "pass"
    assert checks["audio_silence"] == "unavailable"
    assert len(result.artifact_sha256) == 64


def test_technical_qc_fails_on_missing_file_and_bad_duration(tmp_path):
    missing = offline_qc().check(tmp_path / "missing.mp4")
    assert missing.result == "failed"
    assert missing.checks[0].name == "file_exists"
    path = tmp_path / "shot.mp4"
    path.write_bytes(make_mp4_fixture(duration_s=6))
    wrong = offline_qc().check(path, expectations=TechnicalExpectations(duration_s=5))
    checks = {check.name: check.status for check in wrong.checks}
    assert wrong.result == "failed"
    assert checks["duration"] == "fail"


def test_corrupt_or_audio_missing_output_fails(tmp_path):
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not-a-video")
    result = offline_qc().check(corrupt, expectations=TechnicalExpectations(duration_s=6))
    assert result.result == "failed"
    silent = tmp_path / "silent.mp4"
    silent.write_bytes(make_mp4_fixture(duration_s=6, audio=False))
    audio_result = offline_qc().check(
        silent, expectations=TechnicalExpectations(duration_s=6, expect_audio=True)
    )
    checks = {check.name: check.status for check in audio_result.checks}
    assert checks["audio_track"] == "fail"
    assert audio_result.result == "failed"


def test_probe_fallback_handles_wav():
    wav = (
        b"RIFF"
        + (36).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (16000).to_bytes(4, "little")
        + b"\x00" * 16000
    )
    info = parse_bytes(wav, "audio/wav")
    assert info.container == "wav" and info.has_audio is True
    assert abs((info.duration_s or 0) - 1.0) < 0.01


def test_real_ffmpeg_proves_a_full_decode(tmp_path):
    """The other half of the contract: with the tools present the check really decodes."""
    from tests.conftest import ffmpeg_executable, make_test_video_bytes

    executable = ffmpeg_executable()
    if not executable:
        pytest.skip("no FFmpeg binary is available on this host")
    path = tmp_path / "real.mp4"
    path.write_bytes(make_test_video_bytes(duration_s=2, width=320, height=240, fps=24))
    result = TechnicalQC(ffprobe_path="definitely-missing-ffprobe", ffmpeg_path=executable).check(
        path,
        expectations=TechnicalExpectations(duration_s=2.0, expect_audio=True),
    )
    checks = {check.name: check for check in result.checks}
    assert checks["decode"].status == "pass"
    # Without FFprobe the stream metadata comes from the FFmpeg fallback.
    assert checks["stream_metadata"].status == "pass"
    assert checks["duration"].status == "pass"
    assert result.result == "passed"

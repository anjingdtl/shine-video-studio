"""Tests for deterministic inter-shot seam analysis and accurate delivery trimming."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from shine.media.seam import analyze_continuity_seam, apply_seam_analysis_to_edl


def _make_color_clip(path: Path, frames_rgb: list[tuple[int, int, int]], *, fps: int = 24) -> None:
    """Create a real decoded-frame MP4 from exact per-frame RGB tuples via FFmpeg rawvideo."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "ffmpeg required for seam tests"
    w, h = 64, 64
    raw = bytearray()
    for r, g, b in frames_rgb:
        raw.extend(bytes((r, g, b)) * (w * h))
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "10",
        str(path),
    ]
    subprocess.run(cmd, input=bytes(raw), capture_output=True, check=True)


def test_seam_rule_a_skips_duplicated_anchored_opening_frames(tmp_path: Path):
    prev_mp4 = tmp_path / "prev.mp4"
    curr_mp4 = tmp_path / "curr.mp4"
    # prev ends at bright cyan-slate (120, 140, 160)
    prev_colors = [(40, 40, 40)] * 48 + [(120, 140, 160)] * 24
    # curr repeats exact anchored frame for 3 frames then smoothly evolves to warm (200, 140, 100)
    curr_colors = [(120, 140, 160)] * 3 + [(140, 140, 140)] * 5 + [(200, 140, 100)] * 64
    _make_color_clip(prev_mp4, prev_colors)
    _make_color_clip(curr_mp4, curr_colors)

    seam = analyze_continuity_seam(
        prev_mp4, curr_mp4,
        continuity_mode="last_frame",
        curr_duration_s=len(curr_colors) / 24.0,
        curr_fps=24.0,
    )
    assert seam.status == "measured"
    assert seam.boundary_mae_raw is not None and seam.boundary_mae_raw <= 12.0
    assert seam.trim_reason == "dedupe_anchored_opening_frame"
    assert seam.trimmed_frames >= 2
    assert seam.recommended_in_s > 0.0
    assert seam.backtrack_detected is False


def test_seam_rule_b_trims_backtrack_overlap_and_respects_dialogue_and_locks(tmp_path: Path):
    prev_mp4 = tmp_path / "prev.mp4"
    curr_mp4 = tmp_path / "curr.mp4"
    # prev ends at (210, 210, 210); curr starts backtracked at dark (30, 30, 30) for 10 frames then reaches (210, 210, 210)
    prev_colors = [(30, 30, 30)] * 48 + [(210, 210, 210)] * 24
    curr_colors = [(30, 30, 30)] * 10 + [(210, 210, 210)] * 14 + [(160, 160, 160)] * 48
    _make_color_clip(prev_mp4, prev_colors)
    _make_color_clip(curr_mp4, curr_colors)

    seam = analyze_continuity_seam(
        prev_mp4, curr_mp4,
        continuity_mode="last_frame",
        curr_duration_s=len(curr_colors) / 24.0,
        curr_fps=24.0,
    )
    assert seam.status == "measured"
    assert seam.backtrack_detected is True
    assert seam.trim_reason == "trim_continuity_backtrack"
    assert seam.trimmed_frames >= 10
    assert seam.recommended_in_s >= 0.4
    assert seam.boundary_mae_trimmed < seam.boundary_mae_raw

    # Dialogue starting at 0.45s clamps trim buffer (<= 0.15s) so speech is never truncated.
    guarded = analyze_continuity_seam(
        prev_mp4, curr_mp4,
        continuity_mode="last_frame",
        curr_duration_s=len(curr_colors) / 24.0,
        first_dialogue_start_s=0.45,
    )
    assert guarded.recommended_in_s <= 0.151

    # Cut shots or user-locked EpisodePostContract cuts are never trimmed automatically.
    cut_seam = analyze_continuity_seam(
        prev_mp4, curr_mp4, continuity_mode="cut", curr_duration_s=len(curr_colors) / 24.0,
    )
    assert cut_seam.recommended_in_s == 0.0 and cut_seam.trimmed_frames == 0

    locked_seam = analyze_continuity_seam(
        prev_mp4, curr_mp4,
        continuity_mode="last_frame",
        curr_duration_s=len(curr_colors) / 24.0,
        user_locked_cut=True,
    )
    assert locked_seam.recommended_in_s == 0.0 and locked_seam.trimmed_frames == 0

    # Batch EDL helper updates clip in_s cleanly and emits structured seam_report.
    clips = [
        {"job_id": "j1", "artifact_id": "a1", "shot_id": "s1", "in_s": 0.0, "out_s": 3.0, "reason": "s1"},
        {"job_id": "j2", "artifact_id": "a2", "shot_id": "s2", "in_s": 0.0, "out_s": 3.0, "reason": "s2"},
    ]
    jobs = {
        "j1": SimpleNamespace(id="j1", shot_id="s1", request={"shot_spec": {"continuity": {"mode": "cut"}}}),
        "j2": SimpleNamespace(id="j2", shot_id="s2", request={"shot_spec": {"continuity": {"mode": "last_frame"}}}),
    }
    report = apply_seam_analysis_to_edl(
        clips,
        jobs_by_id=jobs,
        paths_by_artifact_id={"a1": prev_mp4, "a2": curr_mp4},
    )
    assert len(report) == 1
    assert report[0]["backtrack_detected"] is True
    assert clips[1]["in_s"] == report[0]["recommended_in_s"]
    assert "接缝回溯" in clips[1]["reason"]


def test_seam_degrades_cleanly_on_non_media_fake_bytes(tmp_path: Path):
    fake_a = tmp_path / "a.mp4"
    fake_b = tmp_path / "b.mp4"
    fake_a.write_bytes(b"not-a-real-mp4")
    fake_b.write_bytes(b"not-a-real-mp4")
    seam = analyze_continuity_seam(fake_a, fake_b, continuity_mode="last_frame", curr_duration_s=4.0)
    assert seam.status == "unavailable"
    assert seam.recommended_in_s == 0.0
    assert seam.trimmed_frames == 0

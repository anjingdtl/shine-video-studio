"""Deterministic inter-shot continuity seam analysis and safe overlap trimming.

Reads only real decoded media frames via FFmpeg (no ML models, no database
locks).  Measures boundary pixel MAE, luminance delta, and warm-cool shift
across consecutive cuts, removes duplicated anchored opening frames or
continuity backtrack overlaps on ``last_frame`` joins, and returns structured
telemetry for ``edit_rules["seam_report"]``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROBE_WIDTH = 96
_PROBE_HEIGHT = 54
_PROBE_FRAME_BYTES = _PROBE_WIDTH * _PROBE_HEIGHT * 3

_MAX_AUTO_TRIM_S = 1.5
_MIN_REMAINING_S = 2.0
_DIALOGUE_HEAD_BUFFER_S = 0.3

# Rule A: MiniMaxH3AddGuide(frame_idx=0) anchors f000 to prev[-1]; skip exact/near-frozen
# duplicate guide frames at cut entry (bounded by _MAX_ANCHOR_SKIP_FRAMES).
_ANCHOR_MATCH_MAE_THRESHOLD = 12.0
_ANCHOR_STATIC_DELTA_THRESHOLD = 2.5
_MAX_ANCHOR_SKIP_FRAMES = 4

# Rule B: Legacy/soft last_frame clips may rewind into prev and catch up to prev[-1].
_BACKTRACK_IMPROVEMENT_MARGIN = 5.0
_BACKTRACK_MATCH_MAE_CEILING = 20.0


@dataclass(frozen=True)
class SeamAnalysis:
    boundary_index: int
    prev_shot_id: str | None
    curr_shot_id: str | None
    continuity_mode: str
    status: str  # "measured" | "unavailable"
    boundary_mae_raw: float | None = None
    boundary_mae_trimmed: float | None = None
    prev_end_rgb: tuple[float, float, float] | None = None
    curr_start_rgb_raw: tuple[float, float, float] | None = None
    curr_start_rgb_trimmed: tuple[float, float, float] | None = None
    delta_luma_raw: float | None = None
    delta_luma_trimmed: float | None = None
    delta_warmth_raw: float | None = None
    delta_warmth_trimmed: float | None = None
    recommended_in_s: float = 0.0
    trimmed_frames: int = 0
    trim_reason: str | None = None
    backtrack_detected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "boundary_index": self.boundary_index,
            "prev_shot_id": self.prev_shot_id,
            "curr_shot_id": self.curr_shot_id,
            "continuity_mode": self.continuity_mode,
            "status": self.status,
            "boundary_mae_raw": self.boundary_mae_raw,
            "boundary_mae_trimmed": self.boundary_mae_trimmed,
            "prev_end_rgb": list(self.prev_end_rgb) if self.prev_end_rgb else None,
            "curr_start_rgb_raw": list(self.curr_start_rgb_raw) if self.curr_start_rgb_raw else None,
            "curr_start_rgb_trimmed": list(self.curr_start_rgb_trimmed) if self.curr_start_rgb_trimmed else None,
            "delta_luma_raw": self.delta_luma_raw,
            "delta_luma_trimmed": self.delta_luma_trimmed,
            "delta_warmth_raw": self.delta_warmth_raw,
            "delta_warmth_trimmed": self.delta_warmth_trimmed,
            "recommended_in_s": self.recommended_in_s,
            "trimmed_frames": self.trimmed_frames,
            "trim_reason": self.trim_reason,
            "backtrack_detected": self.backtrack_detected,
        }


def _decode_rgb_frames(
    path: Path,
    *,
    ffmpeg_path: str,
    sseof_s: float | None = None,
    duration_s: float | None = None,
) -> list[bytes]:
    executable = shutil.which(ffmpeg_path)
    if not executable or not path.is_file():
        return []
    cmd = [executable, "-v", "error", "-nostdin"]
    if sseof_s is not None:
        cmd += ["-sseof", f"{sseof_s:.3f}"]
    cmd += ["-i", str(path)]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += [
        "-vf",
        f"scale={_PROBE_WIDTH}:{_PROBE_HEIGHT}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or len(completed.stdout) < _PROBE_FRAME_BYTES:
        return []
    count = len(completed.stdout) // _PROBE_FRAME_BYTES
    return [completed.stdout[i * _PROBE_FRAME_BYTES : (i + 1) * _PROBE_FRAME_BYTES] for i in range(count)]


def _mean_rgb(frame: bytes) -> tuple[float, float, float]:
    pixels = _PROBE_WIDTH * _PROBE_HEIGHT
    r = sum(frame[0::3]) / pixels
    g = sum(frame[1::3]) / pixels
    b = sum(frame[2::3]) / pixels
    return (round(r, 1), round(g, 1), round(b, 1))


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _warmth(rgb: tuple[float, float, float]) -> float:
    return rgb[0] - rgb[2]


def _mae(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def analyze_continuity_seam(
    prev_video: Path,
    curr_video: Path,
    *,
    boundary_index: int = 1,
    prev_shot_id: str | None = None,
    curr_shot_id: str | None = None,
    continuity_mode: str = "cut",
    curr_duration_s: float = 0.0,
    curr_fps: float = 24.0,
    first_dialogue_start_s: float | None = None,
    user_locked_cut: bool = False,
    ffmpeg_path: str = "ffmpeg",
) -> SeamAnalysis:
    """Measure boundary continuity metrics and compute a safe ``in_s`` trim if needed."""
    eff_fps = float(curr_fps) if curr_fps and curr_fps > 0 else 24.0
    prev_frames = _decode_rgb_frames(prev_video, ffmpeg_path=ffmpeg_path, sseof_s=-0.5)
    head_window_s = min(max(0.5, _MAX_AUTO_TRIM_S + 0.25), curr_duration_s if curr_duration_s > 0 else 2.0)
    curr_frames = _decode_rgb_frames(curr_video, ffmpeg_path=ffmpeg_path, duration_s=head_window_s)
    if not prev_frames or not curr_frames:
        return SeamAnalysis(
            boundary_index=boundary_index,
            prev_shot_id=prev_shot_id,
            curr_shot_id=curr_shot_id,
            continuity_mode=continuity_mode,
            status="unavailable",
        )

    prev_end = prev_frames[-1]
    prev_rgb = _mean_rgb(prev_end)
    curr_rgb_raw = _mean_rgb(curr_frames[0])
    mae_raw = round(_mae(curr_frames[0], prev_end), 2)
    d_luma_raw = round(_luma(curr_rgb_raw) - _luma(prev_rgb), 2)
    d_warmth_raw = round(_warmth(curr_rgb_raw) - _warmth(prev_rgb), 2)

    trimmed_frames = 0
    recommended_in_s = 0.0
    trim_reason: str | None = None
    backtrack_detected = False

    if continuity_mode == "last_frame" and not user_locked_cut and curr_duration_s > _MIN_REMAINING_S:
        max_trim_s = min(_MAX_AUTO_TRIM_S, max(0.0, curr_duration_s - _MIN_REMAINING_S))
        if first_dialogue_start_s is not None:
            max_trim_s = min(max_trim_s, max(0.0, float(first_dialogue_start_s) - _DIALOGUE_HEAD_BUFFER_S))
        max_trim_frames = min(len(curr_frames) - 1, int(max_trim_s * eff_fps))
        if max_trim_frames >= 1:
            if mae_raw <= _ANCHOR_MATCH_MAE_THRESHOLD:
                skip_limit = min(_MAX_ANCHOR_SKIP_FRAMES, max_trim_frames)
                skip = 1
                while skip < skip_limit:
                    if (
                        _mae(curr_frames[skip], prev_end) <= _ANCHOR_MATCH_MAE_THRESHOLD
                        and _mae(curr_frames[skip], curr_frames[skip - 1]) < _ANCHOR_STATIC_DELTA_THRESHOLD
                    ):
                        skip += 1
                    else:
                        break
                trimmed_frames = skip
                recommended_in_s = round(skip / eff_fps, 3)
                trim_reason = "dedupe_anchored_opening_frame"
            else:
                maes = [_mae(curr_frames[t], prev_end) for t in range(max_trim_frames + 1)]
                best_t = min(range(len(maes)), key=lambda idx: maes[idx])
                if (
                    best_t >= 2
                    and maes[best_t] + _BACKTRACK_IMPROVEMENT_MARGIN < maes[0]
                    and maes[best_t] <= _BACKTRACK_MATCH_MAE_CEILING
                ):
                    skip = min(best_t + 1, max_trim_frames)
                    trimmed_frames = skip
                    recommended_in_s = round(skip / eff_fps, 3)
                    trim_reason = "trim_continuity_backtrack"
                    backtrack_detected = True

    idx_after = min(trimmed_frames, len(curr_frames) - 1)
    curr_rgb_trimmed = _mean_rgb(curr_frames[idx_after])
    mae_trimmed = round(_mae(curr_frames[idx_after], prev_end), 2)
    d_luma_trimmed = round(_luma(curr_rgb_trimmed) - _luma(prev_rgb), 2)
    d_warmth_trimmed = round(_warmth(curr_rgb_trimmed) - _warmth(prev_rgb), 2)

    return SeamAnalysis(
        boundary_index=boundary_index,
        prev_shot_id=prev_shot_id,
        curr_shot_id=curr_shot_id,
        continuity_mode=continuity_mode,
        status="measured",
        boundary_mae_raw=mae_raw,
        boundary_mae_trimmed=mae_trimmed,
        prev_end_rgb=prev_rgb,
        curr_start_rgb_raw=curr_rgb_raw,
        curr_start_rgb_trimmed=curr_rgb_trimmed,
        delta_luma_raw=d_luma_raw,
        delta_luma_trimmed=d_luma_trimmed,
        delta_warmth_raw=d_warmth_raw,
        delta_warmth_trimmed=d_warmth_trimmed,
        recommended_in_s=recommended_in_s,
        trimmed_frames=trimmed_frames,
        trim_reason=trim_reason,
        backtrack_detected=backtrack_detected,
    )


def apply_seam_analysis_to_edl(
    clips: list[dict[str, Any]],
    *,
    jobs_by_id: dict[str, Any],
    paths_by_artifact_id: dict[str, Path],
    fps_by_artifact_id: dict[str, float] | None = None,
    locked_shot_ids: set[str] | frozenset[str] = frozenset(),
    ffmpeg_path: str = "ffmpeg",
) -> list[dict[str, Any]]:
    """Inspect all adjacent cuts in an EDL, trim safe overlaps on ``last_frame``, return telemetry."""
    report: list[dict[str, Any]] = []
    if len(clips) < 2:
        return report
    fps_map = fps_by_artifact_id or {}
    for index in range(1, len(clips)):
        prev_clip = clips[index - 1]
        curr_clip = clips[index]
        prev_job = jobs_by_id.get(prev_clip.get("job_id"))
        curr_job = jobs_by_id.get(curr_clip.get("job_id"))
        prev_path = paths_by_artifact_id.get(prev_clip.get("artifact_id"))
        curr_path = paths_by_artifact_id.get(curr_clip.get("artifact_id"))
        if prev_job is None or curr_job is None or prev_path is None or curr_path is None:
            continue
        spec = ((curr_job.request or {}).get("shot_spec")) or {}
        continuity = spec.get("continuity") or {}
        mode = str(continuity.get("mode") or "cut").lower()
        dialogue = sorted(
            (line for line in (spec.get("dialogue") or []) if isinstance(line, dict)),
            key=lambda row: float(row.get("start_s") or 0.0),
        )
        first_dialogue = float(dialogue[0]["start_s"]) if dialogue else None
        shot_id = str(curr_clip.get("shot_id") or curr_job.shot_id)
        analysis = analyze_continuity_seam(
            prev_path,
            curr_path,
            boundary_index=index,
            prev_shot_id=str(prev_clip.get("shot_id") or prev_job.shot_id),
            curr_shot_id=shot_id,
            continuity_mode=mode,
            curr_duration_s=float(curr_clip.get("out_s") or 0.0),
            curr_fps=float(fps_map.get(curr_clip.get("artifact_id") or "") or 24.0),
            first_dialogue_start_s=first_dialogue,
            user_locked_cut=shot_id in locked_shot_ids,
            ffmpeg_path=ffmpeg_path,
        )
        if (
            analysis.status == "measured"
            and analysis.recommended_in_s > float(curr_clip.get("in_s") or 0.0)
            and float(curr_clip.get("out_s") or 0.0) - analysis.recommended_in_s >= _MIN_REMAINING_S
        ):
            curr_clip["in_s"] = analysis.recommended_in_s
            note = (
                f"；已跳过 {analysis.trimmed_frames} 帧接缝重复首帧"
                if analysis.trim_reason == "dedupe_anchored_opening_frame"
                else f"；已修剪 {analysis.recommended_in_s:.3f}s 接缝回溯段"
            )
            curr_clip["reason"] = f"{curr_clip.get('reason', '')}{note}"
        report.append(analysis.as_dict())
    return report


__all__ = [
    "SeamAnalysis",
    "analyze_continuity_seam",
    "apply_seam_analysis_to_edl",
]

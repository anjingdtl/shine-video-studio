"""Assemble a delivery reel from an edit decision list over real artifacts.

The editor agent proposes an edit decision list; this module turns that
proposal into a real, playable file with FFmpeg. It never invents media and it
never marks an unreviewed shot as accepted: every clip records the status of
the job it came from, and the reel itself is only reported as review-complete
when all of its source jobs were passed.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.media.artifacts import safe_asset_path
from shine.media.probe import probe_file
from shine.models import Job, JobAttempt, MediaArtifact, QualityGate, Shot


class DeliveryError(HTTPException):
    """A refusal with a stable machine-readable detail."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)


@dataclass(frozen=True)
class DeliveryClip:
    job_id: str
    attempt_id: str
    artifact_id: str
    in_s: float
    out_s: float

    @property
    def duration_s(self) -> float:
        return self.out_s - self.in_s


@dataclass(frozen=True)
class AssembledDelivery:
    artifact: MediaArtifact
    clips: list[dict]
    duration_s: float
    width: int
    height: int
    fps: float
    review_state: str


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class DeliveryAssembler:
    """Cut, normalise and concatenate passed artifacts into one reel."""

    def __init__(self, *, asset_root: Path, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.asset_root = Path(asset_root)
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    # -- validation -----------------------------------------------------
    def _technical_gate_passed(self, db: Session, attempt_id: str) -> bool:
        """The authoritative per-gate record, the same one the review screen shows.

        ``qc_reports`` holds the fused verdict for the whole attempt, which stays
        ``needs_review`` while a semantic or style review is outstanding. The
        technical gate itself is recorded per gate in ``quality_gates``.
        """
        gate = db.scalar(
            select(QualityGate)
            .where(
                QualityGate.attempt_id == attempt_id,
                QualityGate.gate_name == "technical",
            )
            .order_by(QualityGate.created_at.desc())
        )
        return gate is not None and gate.status == "passed" and not gate.is_mock

    def _review_complete(self, db: Session, clip: DeliveryClip) -> bool:
        job = db.get(Job, clip.job_id)
        if job.status != "PASSED" or job.quality_verification not in {"real", "manual_verified"}:
            return False
        gates = db.scalars(
            select(QualityGate)
            .where(
                QualityGate.job_id == clip.job_id,
                QualityGate.attempt_id == clip.attempt_id,
            )
            .order_by(QualityGate.created_at)
        ).all()
        latest = {gate.gate_name: gate for gate in gates}
        return all(
            name in latest
            and latest[name].artifact_id == clip.artifact_id
            and not latest[name].is_mock
            and latest[name].status in {"passed", "not_applicable"}
            for name in ("technical", "semantic", "audio", "style", "final")
        )

    def _resolve(self, db: Session, project_id: str, clip: DeliveryClip) -> tuple[MediaArtifact, str, float]:
        artifact = db.get(MediaArtifact, clip.artifact_id)
        if not artifact or artifact.project_id != project_id:
            raise DeliveryError(404, "DELIVERY_SOURCE_ARTIFACT_NOT_FOUND")
        if artifact.is_mock or artifact.artifact_kind != "video":
            raise DeliveryError(409, "DELIVERY_SOURCE_NOT_REAL_VIDEO")
        if artifact.job_id != clip.job_id or artifact.attempt_id != clip.attempt_id:
            raise DeliveryError(409, "DELIVERY_SOURCE_IDENTITY_MISMATCH")
        job = db.get(Job, clip.job_id)
        if not job or job.project_id != project_id:
            raise DeliveryError(404, "DELIVERY_SOURCE_JOB_NOT_FOUND")
        if job.stale:
            raise DeliveryError(409, "DELIVERY_SOURCE_JOB_IS_STALE")
        shot = db.get(Shot, job.shot_id)
        if not shot or shot.revision != job.shot_revision or job.artifact_id != artifact.id:
            raise DeliveryError(409, "DELIVERY_SOURCE_REVISION_IS_NOT_CURRENT")
        attempt = db.get(JobAttempt, clip.attempt_id)
        if not attempt or attempt.job_id != job.id:
            raise DeliveryError(409, "DELIVERY_SOURCE_ATTEMPT_MISMATCH")
        highest = max(job.attempts, key=lambda a: a.attempt_no)
        if highest.id != attempt.id:
            raise DeliveryError(409, "DELIVERY_SOURCE_ATTEMPT_IS_NOT_CURRENT")
        if not self._technical_gate_passed(db, attempt.id):
            raise DeliveryError(409, "DELIVERY_SOURCE_TECHNICAL_GATE_NOT_PASSED")

        path = safe_asset_path(self.asset_root, artifact.storage_path)
        if not path.is_file():
            raise DeliveryError(409, "DELIVERY_SOURCE_FILE_MISSING")
        if _digest_file(path) != artifact.sha256:
            raise DeliveryError(409, "DELIVERY_SOURCE_FILE_HASH_MISMATCH")

        info = probe_file(path, ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)
        if info.duration_s is None:
            raise DeliveryError(409, "DELIVERY_SOURCE_DURATION_UNKNOWN")
        if clip.out_s > info.duration_s + 0.05:
            raise DeliveryError(409, "DELIVERY_CLIP_OUTSIDE_SOURCE_DURATION")
        if clip.out_s <= clip.in_s:
            raise DeliveryError(409, "DELIVERY_CLIP_RANGE_INVALID")
        return artifact, job.status, info.duration_s

    # -- rendering ------------------------------------------------------
    def _run(self, command: list[str], cwd: Path | None = None) -> None:
        completed = subprocess.run(command, capture_output=True, timeout=1800, check=False, cwd=cwd)
        if completed.returncode != 0:
            tail = completed.stderr.decode("utf-8", errors="replace")[-800:]
            raise DeliveryError(500, f"DELIVERY_FFMPEG_FAILED: {tail}")

    def _run_with_progress(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        total_s: float | None = None,
        on_tick: Callable[[float, float], None] | None = None,
    ) -> None:
        """Stream FFmpeg `-progress pipe:1` ticks without risking stderr deadlock."""
        if on_tick is None or not total_s or total_s <= 0:
            self._run(command, cwd=cwd)
            return
        prog_cmd = [command[0], "-progress", "pipe:1", "-stats_period", "0.5", *command[1:]]
        with tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(
                prog_cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.strip()
                    if line.startswith("out_time_us="):
                        val = line.split("=", 1)[1].strip()
                        try:
                            elapsed_s = max(0.0, int(val) / 1_000_000.0)
                        except ValueError:
                            continue
                        on_tick(min(1.0, elapsed_s / total_s), min(total_s, elapsed_s))
                    elif line == "progress=end":
                        on_tick(1.0, total_s)
                returncode = proc.wait(timeout=1800)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
                raise
            if returncode != 0:
                stderr_file.seek(0)
                tail = stderr_file.read().decode("utf-8", errors="replace")[-800:]
                raise DeliveryError(500, f"DELIVERY_FFMPEG_FAILED: {tail}")

    def _normalise_clip(
        self,
        source: Path,
        target: Path,
        clip: DeliveryClip,
        *,
        width: int,
        height: int,
        fps: float,
        crf: int,
        has_audio: bool,
    ) -> None:
        command = [self.ffmpeg_path, "-nostdin", "-y", "-ss", f"{clip.in_s:.3f}", "-i", str(source)]
        if not has_audio:
            command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        command += [
            "-t",
            f"{clip.duration_s:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0" if has_audio else "1:a:0",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},format=yuv420p",
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(target),
        ]
        self._run(command)

    def assemble(
        self,
        db: Session,
        project_id: str,
        clips: list[DeliveryClip],
        *,
        fps: float = 24.0,
        width: int | None = None,
        height: int | None = None,
        crf: int = 18,
        subtitle_ass: str | None = None,
        subtitle_sha256: str | None = None,
        reuse_existing: bool = False,
        page_size: int = 100,
        output_subdir: str = "delivery/history",
        on_progress: Callable[[str, int, str], None] | None = None,
    ) -> AssembledDelivery:
        """Render one reel from an ordered clip list.

        ``page_size`` bounds how many normalised segments one concat pass sees:
        a long episode is first folded into page reels and the pages are then
        concatenated.  Every level is a stream copy, so a source clip is still
        encoded exactly once, and the ordered clip list — not the page layout —
        remains the authority on the result.
        """
        if not clips:
            raise DeliveryError(400, "DELIVERY_EMPTY_EDIT")
        if not shutil.which(self.ffmpeg_path):
            raise DeliveryError(409, "DELIVERY_FFMPEG_UNAVAILABLE")

        resolved = []
        for clip in clips:
            artifact, job_status, source_duration = self._resolve(db, project_id, clip)
            resolved.append((clip, artifact, job_status, source_duration))

        if width is None or height is None:
            first = probe_file(
                safe_asset_path(self.asset_root, resolved[0][1].storage_path),
                ffprobe_path=self.ffprobe_path,
                ffmpeg_path=self.ffmpeg_path,
            )
            width = width or (first.width or 1280)
            height = height or (first.height or 720)
        width = max(16, int(width) // 2 * 2)
        height = max(16, int(height) // 2 * 2)

        norm_ceiling = 45 if subtitle_ass else 85
        total_duration_s = sum(max(0.0, clip.out_s - clip.in_s) for clip, *_ in resolved)
        if on_progress is not None:
            on_progress("normalizing", 2, f"正在准备规范化 {len(resolved)} 个镜头片段")

        with tempfile.TemporaryDirectory(prefix="shine-delivery-") as workdir:
            work = Path(workdir)
            segments: list[Path] = []
            for index, (clip, artifact, _status, _duration) in enumerate(resolved):
                if on_progress is not None:
                    pct = 2 + int((norm_ceiling - 2) * index / max(1, len(resolved)))
                    on_progress("normalizing", pct, f"正在规范化第 {index + 1}/{len(resolved)} 镜")
                source = safe_asset_path(self.asset_root, artifact.storage_path)
                info = probe_file(source, ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)
                target = work / f"segment{index:03d}.mp4"
                self._normalise_clip(
                    source,
                    target,
                    clip,
                    width=width,
                    height=height,
                    fps=fps,
                    crf=crf,
                    has_audio=bool(info.has_audio),
                )
                segments.append(target)

            # Absolute POSIX paths: FFmpeg resolves the concat list entries
            # against its own working directory, not against this process.
            def concat(paths: list[Path], target: Path, name: str) -> Path:
                page_listing = work / f"{name}.txt"
                page_listing.write_text(
                    "\n".join(f"file '{segment.resolve().as_posix()}'" for segment in paths) + "\n",
                    encoding="utf-8",
                )
                self._run(
                    [
                        self.ffmpeg_path,
                        "-nostdin",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(page_listing),
                        "-c",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(target),
                    ]
                )
                return target

            pages: list[Path] = []
            bound = max(1, int(page_size))
            if len(segments) > bound:
                for index in range(0, len(segments), bound):
                    pages.append(
                        concat(
                            segments[index:index + bound],
                            work / f"page{index // bound:04d}.mp4",
                            f"page{index // bound:04d}",
                        )
                    )
                sources = pages
            else:
                sources = segments
            if on_progress is not None:
                on_progress("concat", norm_ceiling + 2, "正在拼接分集片段")
            reel = concat(sources, work / "delivery.mp4", "concat")
            final = reel
            if subtitle_ass:
                # The subtitles are composited here, at the very end.  The
                # picture and the audio are untouched by them, and the reel
                # without subtitles stays available as its own artifact.
                # ``subtitle_ass`` is the ASS *text*; the filter needs a real
                # file.  Write it into the work dir and reference it by a bare
                # relative name with cwd=work: an absolute Windows path (drive
                # colon + backslashes) is re-parsed as filtergraph syntax by the
                # ass filter and breaks the burn-in.
                final = work / "delivery-subtitled.mp4"
                (work / "subtitles.ass").write_text(subtitle_ass, encoding="utf-8")
                if on_progress is not None:
                    on_progress("burning_subtitles", 50, "正在准备烧录字幕")

                def _subtitle_tick(ratio: float, elapsed_s: float) -> None:
                    if on_progress is None:
                        return
                    pct = 50 + int(46 * ratio)
                    on_progress(
                        "burning_subtitles",
                        min(96, pct),
                        f"正在烧录字幕（{int(elapsed_s)}/{max(1, round(total_duration_s))} 秒）",
                    )

                self._run_with_progress(
                    [
                        self.ffmpeg_path,
                        "-nostdin",
                        "-y",
                        "-i",
                        str(reel),
                        "-vf",
                        "ass=subtitles.ass",
                        "-c:v",
                        "libx264",
                        "-crf",
                        str(crf),
                        "-preset",
                        "medium",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(final),
                    ],
                    cwd=work,
                    total_s=total_duration_s,
                    on_tick=_subtitle_tick if on_progress is not None else None,
                )
            if on_progress is not None:
                on_progress("finalizing", 98, "正在写入并校验成片文件")
            content = final.read_bytes()
            # Probe while the temporary directory still exists.
            probe = probe_file(final, ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)

        from shine.media.artifacts import store_artifact
        from shine.media.output_layout import ensure_storage_writable, project_type_dir
        from shine.media.output_names import TYPE_VIDEO
        from shine.media.video_naming import delivery_filename
        from shine.models import Project

        ensure_storage_writable(db)
        project = db.get(Project, project_id)
        if project is None:
            raise DeliveryError(409, "DELIVERY_PROJECT_MISSING")
        source_jobs = [job for job in (db.get(Job, clip.job_id) for clip in clips) if job]
        # The reel's real file name names the project, shot range and episode
        # the ledger says these clips belong to; new colliding versions get a
        # short _vN suffix instead of overwriting an earlier delivery.
        reel_name = delivery_filename(db, project_id, project.title, source_jobs)
        directory = project_type_dir(db, project, TYPE_VIDEO, subdir=output_subdir)
        stored_path, digest, byte_size = store_artifact(
            self.asset_root, project_id, "delivery", content, "video/mp4",
            filename=reel_name, subdir=output_subdir, directory=directory,
        )
        relative = Path(stored_path)

        clip_records = [
            {
                "job_id": clip.job_id,
                "attempt_id": clip.attempt_id,
                "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256,
                "in_s": clip.in_s,
                "out_s": clip.out_s,
                "duration_s": round(clip.duration_s, 3),
                "source_job_status": status,
                "source_artifact_sha256": artifact.sha256,
            }
            for clip, artifact, status, _duration in resolved
        ]
        review_state = "all_passed" if all(self._review_complete(db, clip) for clip in clips) else "pending_review"

        if reuse_existing:
            # The identical reel may already be registered: a previous attempt
            # wrote the MP4 but crashed before its ledger row was committed.
            # Reuse that row instead of producing a duplicate delivery.
            prior = db.scalar(
                select(MediaArtifact)
                .where(
                    MediaArtifact.project_id == project_id,
                    MediaArtifact.artifact_kind == "delivery",
                    MediaArtifact.sha256 == digest,
                )
                .order_by(MediaArtifact.created_at)
            )
            if prior is not None:
                return AssembledDelivery(
                    artifact=prior,
                    clips=clip_records,
                    duration_s=probe.duration_s or 0.0,
                    width=width,
                    height=height,
                    fps=fps,
                    review_state=review_state,
                )

        artifact_row = MediaArtifact(
            project_id=project_id,
            job_id=None,
            attempt_id=None,
            artifact_kind="delivery",
            storage_path=relative.as_posix(),
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
            provenance={
                "kind": "delivery",
                "clips": clip_records,
                "fps": fps,
                "width": width,
                "height": height,
                "duration_s": probe.duration_s,
                "review_state": review_state,
                "ffmpeg": shutil.which(self.ffmpeg_path),
                "assembly_pages": len(pages) if len(segments) > max(1, int(page_size)) else 1,
                **({"subtitles_sha256": subtitle_sha256, "subtitles_composited": True}
                   if subtitle_ass else {"subtitles_composited": False}),
            },
        )
        db.add(artifact_row)
        db.flush()

        return AssembledDelivery(
            artifact=artifact_row,
            clips=clip_records,
            duration_s=probe.duration_s or 0.0,
            width=width,
            height=height,
            fps=fps,
            review_state=review_state,
        )

"""Derive a bounded continuity reference from immutable rendered bytes.

This module does not create or approve an Asset.  Its caller must persist the
returned bytes as a MediaArtifact with the source job and hash in its lineage.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shine.media.probe import probe_file


@dataclass(frozen=True)
class ContinuityMedia:
    content: bytes
    content_type: str
    source_sha256: str
    start_s: float | None
    end_s: float | None
    duration_s: float | None


@dataclass(frozen=True)
class ContinuityReference:
    """A derived artifact, deliberately distinct from a user-approved Asset."""

    id: str
    project_id: str
    filename: str
    content_type: str
    storage_path: str
    sha256: str
    byte_size: int
    duration_s: float | None
    provenance: dict
    entity_id: str | None = None


def load_continuity_reference(db, project_id: str, artifact_id: str, source_job_id: str, mode: str):
    from shine.models import MediaArtifact

    artifact = db.get(MediaArtifact, artifact_id)
    lineage = artifact.provenance if artifact and isinstance(artifact.provenance, dict) else {}
    if (
        artifact is None
        or artifact.project_id != project_id
        or artifact.job_id != source_job_id
        or artifact.artifact_kind != "continuity"
        or artifact.is_mock
        or lineage.get("kind") != "continuity_derivative_v1"
        or lineage.get("mode") != mode
        or lineage.get("source") != _source_identity(db, project_id, source_job_id)
    ):
        raise ValueError("CONTINUITY_DERIVATIVE_INVALID")
    expected_type = {
        "last_frame": "image/png",
        "hard_frame_anchor": "image/png",
        "video_reference": "video/mp4",
        "audio_reference": "audio/wav",
    }.get(mode)
    if not expected_type or artifact.content_type != expected_type:
        raise ValueError("CONTINUITY_DERIVATIVE_INVALID")
    return ContinuityReference(
        id=artifact.id,
        project_id=artifact.project_id,
        filename=Path(artifact.storage_path).name,
        content_type=artifact.content_type,
        storage_path=artifact.storage_path,
        sha256=artifact.sha256,
        byte_size=artifact.byte_size,
        duration_s=lineage.get("duration_s"),
        provenance=lineage,
    )


def continuity_source_job(db, project_id: str, spec):
    """The job that really produced this shot's continuity source.

    The stored spec carries the *intent*; the ledger carries the fact.  A
    source is only a source when it really passed, belongs to this project
    and still matches the current shot revision.
    """
    from sqlalchemy import select

    from shine.models import Job, Shot

    continuity = spec.continuity
    if continuity.mode.value == "cut":
        return None
    if continuity.previous_job_id:
        job = db.get(Job, continuity.previous_job_id)
        if job is None or job.project_id != project_id:
            raise ValueError("CONTINUITY_SOURCE_NOT_VERIFIED")
        return job
    if not continuity.previous_shot_id:
        return None
    rows = list(db.scalars(
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.shot_id == continuity.previous_shot_id,
            Job.status == "PASSED",
            Job.stale.is_(False),
        )
        .order_by(Job.created_at.desc())
    ))
    for job in rows:
        shot = db.get(Shot, job.shot_id)
        if shot is not None and job.shot_revision == shot.revision:
            return job
    return None


def find_continuity_artifact(db, project_id: str, source_job_id: str, mode: str, *,
                              start_s: float | None = None, end_s: float | None = None):
    """An already derived reference of this exact source and window.

    A repair or a restarted Worker must not derive a second copy of the same
    bytes; the derivative is content addressed and reusable as long as its
    lineage still names the same source.
    """
    from sqlalchemy import select

    from shine.models import MediaArtifact

    rows = db.scalars(
        select(MediaArtifact).where(
            MediaArtifact.project_id == project_id,
            MediaArtifact.job_id == source_job_id,
            MediaArtifact.artifact_kind == "continuity",
            MediaArtifact.is_mock.is_(False),
        )
    ).all()
    for artifact in rows:
        lineage = artifact.provenance if isinstance(artifact.provenance, dict) else {}
        if lineage.get("kind") != "continuity_derivative_v1" or lineage.get("mode") != mode:
            continue
        if start_s is not None and lineage.get("start_s") != start_s:
            continue
        if end_s is not None and lineage.get("end_s") != end_s:
            continue
        return artifact
    return None


def _source_identity(db, project_id: str, job_id: str) -> dict:
    """Read the complete source identity; a stale or mock render is never eligible."""
    from shine.models import Job, JobAttempt, MediaArtifact, Shot

    job = db.get(Job, job_id)
    shot = db.get(Shot, job.shot_id) if job else None
    artifact = db.get(MediaArtifact, job.artifact_id) if job and job.artifact_id else None
    attempt = db.get(JobAttempt, artifact.attempt_id) if artifact and artifact.attempt_id else None
    if (
        job is None
        or job.project_id != project_id
        or job.provider != "comfyui"
        or job.status != "PASSED"
        or job.stale
        or shot is None
        or shot.revision != job.shot_revision
        or artifact is None
        or artifact.project_id != project_id
        or artifact.job_id != job.id
        or artifact.artifact_kind != "video"
        or artifact.is_mock
        or attempt is None
        or attempt.job_id != job.id
        or attempt.output_sha256 != artifact.sha256
        or attempt.output_bytes != artifact.byte_size
    ):
        raise ValueError("CONTINUITY_SOURCE_NOT_VERIFIED")
    return {
        "project_id": project_id,
        "job_id": job.id,
        "shot_id": job.shot_id,
        "shot_revision": job.shot_revision,
        "artifact_id": artifact.id,
        "attempt_id": attempt.id,
        "sha256": artifact.sha256,
        "storage_path": artifact.storage_path,
    }


def prepare_continuity_artifact(
    session_factory,
    settings,
    project_id: str,
    job_id: str,
    mode: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
) -> str:
    """Store a provenance-bound derivative without holding a DB write lock during FFmpeg.

    Recheck the source in the committing transaction.  Orphaned content-addressed
    bytes are harmless if the source changes before that transaction commits.
    """
    from sqlalchemy import select, text

    from shine.media.artifacts import safe_asset_path, store_artifact
    from shine.models import MediaArtifact

    with session_factory() as db:
        identity = _source_identity(db, project_id, job_id)
    media = derive_continuity_media(
        safe_asset_path(settings.data_dir, identity["storage_path"]),
        identity["sha256"],
        mode,
        start_s=start_s,
        end_s=end_s,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    lineage = {
        "kind": "continuity_derivative_v1",
        "source": identity,
        "mode": mode,
        "start_s": media.start_s,
        "end_s": media.end_s,
        "duration_s": media.duration_s,
    }
    path, digest, size = store_artifact(settings.data_dir, project_id, job_id, media.content, media.content_type)
    with session_factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if _source_identity(db, project_id, job_id) != identity:
            raise ValueError("CONTINUITY_SOURCE_CHANGED")
        existing = db.scalars(
            select(MediaArtifact).where(
                MediaArtifact.project_id == project_id,
                MediaArtifact.job_id == job_id,
                MediaArtifact.artifact_kind == "continuity",
                MediaArtifact.sha256 == digest,
            )
        ).all()
        for artifact in existing:
            if artifact.provenance == lineage and artifact.byte_size == size:
                return artifact.id
        artifact = MediaArtifact(
            project_id=project_id,
            job_id=job_id,
            attempt_id=identity["attempt_id"],
            artifact_kind="continuity",
            storage_path=path,
            sha256=digest,
            byte_size=size,
            content_type=media.content_type,
            is_mock=False,
            provenance=lineage,
        )
        db.add(artifact)
        db.flush()
        return artifact.id


def derive_continuity_media(
    source: Path,
    expected_sha256: str,
    mode: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> ContinuityMedia:
    """Verify the source and extract a frame, video segment, or audio segment.

    The window is explicit in the result. H3 audio/video references must be
    between 2 and 15 seconds; invalid windows are refused, never truncated.
    """
    if mode not in {"last_frame", "hard_frame_anchor", "video_reference", "audio_reference"}:
        raise ValueError("CONTINUITY_MODE_UNSUPPORTED")
    ffmpeg = shutil.which(ffmpeg_path)
    if not ffmpeg or not shutil.which(ffprobe_path):
        raise ValueError("CONTINUITY_MEDIA_TOOLS_UNAVAILABLE")
    if not source.is_file():
        raise ValueError("CONTINUITY_SOURCE_MISSING")
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_sha256:
        raise ValueError("CONTINUITY_SOURCE_HASH_MISMATCH")
    info = probe_file(source, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
    if info.tool != "ffprobe" or not info.duration_s or not math.isfinite(info.duration_s):
        raise ValueError("CONTINUITY_SOURCE_PROBE_FAILED")
    frame = mode in {"last_frame", "hard_frame_anchor"}
    if (frame or mode == "video_reference") and not info.has_video:
        raise ValueError("CONTINUITY_SOURCE_VIDEO_MISSING")
    if mode == "audio_reference" and not info.has_audio:
        raise ValueError("CONTINUITY_SOURCE_AUDIO_MISSING")
    if frame:
        if start_s is not None or end_s is not None:
            raise ValueError("CONTINUITY_FRAME_WINDOW_UNSUPPORTED")
        start, end = None, None
    else:
        end = info.duration_s if end_s is None else end_s
        start = max(0.0, end - 15.0) if start_s is None else start_s
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end > info.duration_s + 0.001
            or not 2 <= end - start <= 15
        ):
            raise ValueError("CONTINUITY_REFERENCE_WINDOW_INVALID")

    with tempfile.TemporaryDirectory(prefix="shine-continuity-") as folder:
        target = Path(folder) / ("frame.png" if frame else "clip.mp4" if mode == "video_reference" else "audio.wav")
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if frame:
            # Reverse decoding gives the last actual frame, even with variable FPS.
            command += ["-sseof", "-1", "-i", str(source), "-vf", "reverse", "-frames:v", "1", str(target)]
        else:
            command += ["-ss", str(start), "-i", str(source), "-t", str(end - start)]
            if mode == "video_reference":
                command += ["-map", "0:v:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]
            else:
                command += ["-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", str(target)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("CONTINUITY_EXTRACTION_FAILED") from exc
        if result.returncode or not target.is_file() or not target.stat().st_size:
            raise ValueError("CONTINUITY_EXTRACTION_FAILED")
        content = target.read_bytes()
    # Catch source replacement during extraction as well as before it.
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("CONTINUITY_SOURCE_HASH_MISMATCH")
    return ContinuityMedia(
        content,
        "image/png" if frame else "video/mp4" if mode == "video_reference" else "audio/wav",
        digest,
        start,
        end,
        None if frame else end - start,
    )

"""A produced shot is previewable: the workbench can play it and show a poster.

The segments grid needs the real clip of a produced shot.  The operator route
only answers an operator request, so the workbench had no way to play a shot
at all: the preview button asked for a URL that did not exist.
"""

from __future__ import annotations

import hashlib
import shutil
from uuid import uuid4

from shine.models import Job, JobAttempt, MediaArtifact
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes


def _seed_produced_job(factory, settings, project_id, shot_id, style_id):
    """A passed job with a real, decodable artifact on disk."""
    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    digest = hashlib.sha256(content).hexdigest()
    relative = f"artifacts/{project_id}/shots/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with factory.begin() as db:
        job = Job(
            project_id=project_id,
            shot_id=shot_id,
            shot_revision=1,
            style_version_id=style_id,
            request={},
            idempotency_key=uuid4().hex,
            status="PASSED",
            quality_verification="real",
            provider="comfyui",
            generation_mode="Ref2VA",
        )
        db.add(job)
        db.flush()
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            request_fingerprint=digest,
            status="COMPLETED",
            lease_owner="test-worker",
            lease_expires_at=job.created_at,
            output_path=relative,
            output_sha256=digest,
            output_bytes=len(content),
        )
        db.add(attempt)
        db.flush()
        artifact = MediaArtifact(
            project_id=project_id,
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_kind="video",
            storage_path=relative,
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
        )
        db.add(artifact)
        db.flush()
        job.artifact_id = artifact.id
        job_id = job.id
    return job_id, content, relative


def test_a_produced_shot_can_be_played(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, content, _ = _seed_produced_job(factory, settings, project, shot["id"], style["id"])

    response = client.get(f"/api/v1/jobs/{job_id}/media")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("video/mp4")


def test_a_produced_shot_has_a_poster_frame(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, _, _ = _seed_produced_job(factory, settings, project, shot["id"], style["id"])

    response = client.get(f"/api/v1/jobs/{job_id}/thumbnail")

    if not shutil.which(settings.ffmpeg_path):
        # No decoder is a stated state, not a crash: the grid says the preview
        # is unavailable and still offers the clip.
        assert response.status_code == 503
        assert response.json()["detail"] == "IMAGE_PREVIEW_UNAVAILABLE"
        return
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_a_job_without_produced_media_says_so(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    with factory.begin() as db:
        job = Job(
            project_id=project,
            shot_id=shot["id"],
            shot_revision=1,
            style_version_id=style["id"],
            request={},
            idempotency_key=uuid4().hex,
            status="QUEUED",
            provider="comfyui",
            generation_mode="Ref2VA",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    response = client.get(f"/api/v1/jobs/{job_id}/media")

    assert response.status_code == 404
    assert response.json()["detail"] == "This job has no produced media yet"


def test_a_tampered_shot_file_is_refused(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, _, relative = _seed_produced_job(factory, settings, project, shot["id"], style["id"])
    (settings.data_dir / relative).write_bytes(b"not the produced bytes")

    response = client.get(f"/api/v1/jobs/{job_id}/media")

    assert response.status_code == 409
    assert response.json()["detail"] == "ARTIFACT_FILE_HASH_MISMATCH"


def test_an_unknown_job_has_no_media(studio):
    client, _, _, _ = studio

    assert client.get("/api/v1/jobs/does-not-exist/media").status_code == 404

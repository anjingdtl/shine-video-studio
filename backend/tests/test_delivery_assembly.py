"""Delivery assembly: an edit decision list becomes a real, traceable reel.

These tests use synthetic test-pattern media. They never use or assert on
character, location or prop master assets.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from uuid import uuid4

import pytest

from shine.models import Job, JobAttempt, MediaArtifact, QualityGate
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes


def _ffprobe(path, settings) -> dict:
    executable = shutil.which(settings.ffprobe_path)
    if not executable:
        pytest.skip("FFprobe is not installed")
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    return json.loads(completed.stdout.decode("utf-8") or "{}")


def _seed_passed_job(session_factory, settings, project_id, shot_id, style_id, *, duration_s=2.0):
    """Create a passed job with a real artifact and a real technical QC report."""
    content = make_test_video_bytes(duration_s=duration_s, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(content).hexdigest()
    relative = f"artifacts/{project_id}/delivery-test/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    session = session_factory()
    try:
        job = Job(
            project_id=project_id,
            shot_id=shot_id,
            shot_revision=1,
            style_version_id=style_id,
            request={},
            idempotency_key=uuid4().hex,
            status="PASSED",
            provider="comfyui",
            generation_mode="Ref2VA",
        )
        session.add(job)
        session.flush()
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
        session.add(attempt)
        session.flush()
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
        session.add(artifact)
        session.flush()
        job.artifact_id = artifact.id
        session.add(
            QualityGate(
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_id=artifact.id,
                gate_name="technical",
                status="passed",
                required=True,
                is_mock=False,
                model="ffprobe",
                coverage="covered",
                evidence={"tool": "ffprobe", "result": "passed"},
            )
        )
        session.commit()
        return job.id, attempt.id, artifact.id, relative, digest
    finally:
        session.close()


def test_delivery_assembles_a_playable_reel(studio):
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付测试")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, attempt_id, artifact_id, relative, digest = _seed_passed_job(
        factory, settings, project, shot["id"], style["id"]
    )

    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={
            "schema_version": "1.1",
            "clips": [
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "artifact_id": artifact_id,
                    "in_s": 0.0,
                    "out_s": 1.5,
                    "transition": "CUT",
                    "reason": "keep the first move",
                }
            ],
            "fps": 24.0,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duration_s"] == pytest.approx(1.5, abs=0.25)
    assert body["width"] == 320 and body["height"] == 240
    assert body["review_state"] == "all_passed"

    # The reel is a real, decodable file with exactly one video and one audio stream.
    reel = settings.data_dir / body["storage_path"]
    assert reel.is_file()
    probe = _ffprobe(reel, settings)
    kinds = [stream["codec_type"] for stream in probe.get("streams", [])]
    assert kinds.count("video") == 1
    assert kinds.count("audio") == 1

    # Every segment is traceable to the artifact it came from.
    assert body["clips"][0]["artifact_id"] == artifact_id
    assert body["clips"][0]["source_job_status"] == "PASSED"

    # The source artifact is untouched.
    source = settings.data_dir / relative
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest

    # The delivery is listed and downloadable.
    listing = client.get(f"/api/v1/projects/{project}/deliveries").json()
    assert [row["artifact_id"] for row in listing] == [body["artifact_id"]]
    media = client.get(f"/api/v1/deliveries/{body['artifact_id']}/media")
    assert media.status_code == 200
    assert media.content == reel.read_bytes()

    events = client.get(f"/api/v1/projects/{project}/events?limit=200").json()["items"]
    assert any(event["event_type"] == "DELIVERY_ASSEMBLED" for event in events)


def test_delivery_refuses_a_source_without_a_passed_technical_gate(studio):
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付门禁")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, attempt_id, artifact_id, _relative, _digest = _seed_passed_job(
        factory, settings, project, shot["id"], style["id"]
    )

    session = factory()
    try:
        gate = (
            session.query(QualityGate)
            .filter(QualityGate.attempt_id == attempt_id, QualityGate.gate_name == "technical")
            .one()
        )
        gate.status = "failed"
        session.commit()
    finally:
        session.close()

    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={
            "schema_version": "1.1",
            "clips": [
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "artifact_id": artifact_id,
                    "in_s": 0.0,
                    "out_s": 1.0,
                    "transition": "CUT",
                    "reason": "gated",
                }
            ],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DELIVERY_SOURCE_TECHNICAL_GATE_NOT_PASSED"


def test_delivery_refuses_a_tampered_source_file(studio):
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付防篡改")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, attempt_id, artifact_id, relative, _digest = _seed_passed_job(
        factory, settings, project, shot["id"], style["id"]
    )

    (settings.data_dir / relative).write_bytes(b"not the recorded media")

    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={
            "schema_version": "1.1",
            "clips": [
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "artifact_id": artifact_id,
                    "in_s": 0.0,
                    "out_s": 1.0,
                    "transition": "CUT",
                    "reason": "tampered",
                }
            ],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DELIVERY_SOURCE_FILE_HASH_MISMATCH"


def test_delivery_refuses_a_clip_outside_the_source_duration(studio):
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付越界")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, attempt_id, artifact_id, _relative, _digest = _seed_passed_job(
        factory, settings, project, shot["id"], style["id"], duration_s=1.0
    )

    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={
            "schema_version": "1.1",
            "clips": [
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "artifact_id": artifact_id,
                    "in_s": 0.0,
                    "out_s": 30.0,
                    "transition": "CUT",
                    "reason": "beyond the source",
                }
            ],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DELIVERY_CLIP_OUTSIDE_SOURCE_DURATION"


def test_delivery_refuses_an_artifact_from_another_project(studio):
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付跨项目")
    other = create_project(client, "另一个项目")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, attempt_id, artifact_id, _relative, _digest = _seed_passed_job(
        factory, settings, other, shot["id"], style["id"]
    )

    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={
            "schema_version": "1.1",
            "clips": [
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "artifact_id": artifact_id,
                    "in_s": 0.0,
                    "out_s": 1.0,
                    "transition": "CUT",
                    "reason": "cross project",
                }
            ],
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "DELIVERY_SOURCE_ARTIFACT_NOT_FOUND"


def test_delivery_keeps_a_single_audio_track_across_clips(studio):
    """Two clips must produce one audio stream, never a duplicated or missing track."""
    client, factory, tmp_path, settings = studio
    project = create_project(client, "交付音轨")
    style = freeze_style(client, project)
    shot_a = create_shot(client, project, style["id"])
    shot_b = create_shot(client, project, style["id"])
    first = _seed_passed_job(factory, settings, project, shot_a["id"], style["id"])
    second = _seed_passed_job(factory, settings, project, shot_b["id"], style["id"])

    clips = [
        {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "artifact_id": artifact_id,
            "in_s": 0.0,
            "out_s": 1.0,
            "transition": "CUT",
            "reason": f"segment {index}",
        }
        for index, (job_id, attempt_id, artifact_id, _r, _d) in enumerate([first, second])
    ]
    response = client.post(
        f"/api/v1/projects/{project}/deliveries",
        json={"schema_version": "1.1", "clips": clips, "fps": 24.0},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duration_s"] == pytest.approx(2.0, abs=0.4)
    probe = _ffprobe(settings.data_dir / body["storage_path"], settings)
    kinds = [stream["codec_type"] for stream in probe.get("streams", [])]
    assert kinds.count("video") == 1
    assert kinds.count("audio") == 1

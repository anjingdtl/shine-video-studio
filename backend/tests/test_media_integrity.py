"""Playback integrity is cached, conditional and Range-capable.

The media routes used to read the whole file and re-hash it on every request —
including every Range segment a video player issues.  These tests pin the new
contract: one digest per file identity, cheap 304 revalidation, real 206 Range
answers, and a tampered file still refused.
"""

from __future__ import annotations

import hashlib
import os
import shutil

import pytest
from fastapi import HTTPException

from shine.media import integrity
from shine.models import MediaArtifact
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes
from tests.test_job_media import _seed_produced_job


@pytest.fixture(autouse=True)
def _fresh_cache():
    integrity.clear_verified_cache()
    yield
    integrity.clear_verified_cache()


def _count_hashes(monkeypatch):
    original = integrity.file_sha256
    calls = {"n": 0}

    def counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(integrity, "file_sha256", counting)
    return calls


def test_repeated_and_range_requests_hash_the_file_once(studio, monkeypatch):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, content, _ = _seed_produced_job(factory, settings, project, shot["id"], style["id"])
    calls = _count_hashes(monkeypatch)

    first = client.get(f"/api/v1/jobs/{job_id}/media")
    second = client.get(f"/api/v1/jobs/{job_id}/media")
    ranged = client.get(f"/api/v1/jobs/{job_id}/media", headers={"Range": "bytes=0-99"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert ranged.status_code == 206
    assert ranged.content == content[:100]
    assert ranged.headers["content-range"] == f"bytes 0-99/{len(content)}"
    assert calls["n"] == 1


def test_media_etag_revalidation_is_a_cheap_304(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, content, _ = _seed_produced_job(factory, settings, project, shot["id"], style["id"])

    first = client.get(f"/api/v1/jobs/{job_id}/media")
    etag = first.headers["etag"]
    assert etag == f'"{hashlib.sha256(content).hexdigest()}"'
    assert "no-cache" in first.headers["cache-control"]

    again = client.get(f"/api/v1/jobs/{job_id}/media", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_same_size_tampering_is_still_refused(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, content, relative = _seed_produced_job(factory, settings, project, shot["id"], style["id"])
    assert client.get(f"/api/v1/jobs/{job_id}/media").status_code == 200

    path = settings.data_dir / relative
    # Same byte count, different bytes: only a re-hash can catch this, and the
    # changed modification time must force exactly that.
    path.write_bytes(b"x" * len(content))
    os.utime(path, (path.stat().st_atime + 10, path.stat().st_mtime + 10))

    response = client.get(f"/api/v1/jobs/{job_id}/media")
    assert response.status_code == 409
    assert response.json()["detail"] == "ARTIFACT_FILE_HASH_MISMATCH"


def _seed_delivery(factory, settings, project_id):
    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    digest = hashlib.sha256(content).hexdigest()
    relative = f"artifacts/{project_id}/delivery/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with factory.begin() as db:
        artifact = MediaArtifact(
            project_id=project_id,
            artifact_kind="delivery",
            storage_path=relative,
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
            provenance={"kind": "delivery", "review_state": "pending_review"},
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
    return artifact_id, content


def test_delivery_media_is_conditional_and_range_capable(studio, monkeypatch):
    client, factory, _, settings = studio
    project = create_project(client)
    artifact_id, content = _seed_delivery(factory, settings, project)
    calls = _count_hashes(monkeypatch)

    first = client.get(f"/api/v1/deliveries/{artifact_id}/media")
    assert first.status_code == 200
    etag = first.headers["etag"]

    again = client.get(f"/api/v1/deliveries/{artifact_id}/media", headers={"If-None-Match": etag})
    assert again.status_code == 304

    ranged = client.get(f"/api/v1/deliveries/{artifact_id}/media",
                        headers={"Range": f"bytes=10-{len(content) - 1}"})
    assert ranged.status_code == 206
    assert ranged.content == content[10:]
    assert calls["n"] == 1


def test_delivery_tampering_keeps_its_named_refusal(studio):
    client, factory, _, settings = studio
    project = create_project(client)
    artifact_id, content = _seed_delivery(factory, settings, project)
    assert client.get(f"/api/v1/deliveries/{artifact_id}/media").status_code == 200
    with factory.begin() as db:
        artifact = db.get(MediaArtifact, artifact_id)
        path = settings.data_dir / artifact.storage_path
    path.write_bytes(b"not the reel")

    response = client.get(f"/api/v1/deliveries/{artifact_id}/media")
    assert response.status_code == 409
    assert response.json()["detail"] == "DELIVERY_FILE_HASH_MISMATCH"


def test_thumbnail_revalidation_never_touches_the_file(tmp_path):
    from shine.media.thumbnails import thumbnail

    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    path = tmp_path / "clip.mp4"
    path.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    etag = f'"{sha}"'

    # The 304 answer must not depend on the file (or even on FFmpeg) at all:
    # the caller already holds the derivative of exactly these bytes.
    path.unlink()
    response = thumbnail(path, sha, "ffmpeg", if_none_match=etag)
    assert response.status_code == 304
    assert response.headers["etag"] == etag


def test_thumbnail_missing_file_and_tampering_keep_their_codes(tmp_path):
    from shine.media.thumbnails import thumbnail

    path = tmp_path / "clip.mp4"
    with pytest.raises(HTTPException) as missing:
        thumbnail(path, "0" * 64)
    assert missing.value.status_code == 404
    assert missing.value.detail == "ASSET_FILE_MISSING"

    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    path.write_bytes(content)
    with pytest.raises(HTTPException) as changed:
        thumbnail(path, "0" * 64)
    assert changed.value.status_code == 409
    assert changed.value.detail == "ASSET_HASH_CHANGED"


def test_verify_media_file_names_its_own_refusals(tmp_path):
    path = tmp_path / "bin"
    path.write_bytes(b"abc")
    with pytest.raises(HTTPException) as wrong_size:
        integrity.verify_media_file(path, hashlib.sha256(b"abc").hexdigest(), 99,
                                    mismatch_code="CUSTOM_MISMATCH")
    assert wrong_size.value.status_code == 409
    assert wrong_size.value.detail == "CUSTOM_MISMATCH"
    # The correct size and digest pass, and the identity is then remembered.
    integrity.verify_media_file(path, hashlib.sha256(b"abc").hexdigest(), 3)


def test_a_real_decoder_still_produces_a_poster(tmp_path):
    from shine.media.thumbnails import thumbnail

    if not shutil.which("ffmpeg"):
        pytest.skip("no FFmpeg on this machine")
    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    path = tmp_path / "clip.mp4"
    path.write_bytes(content)
    response = thumbnail(path, hashlib.sha256(content).hexdigest(), "ffmpeg", seek_s=1.0)
    assert response.status_code == 200
    assert response.body[:2] == b"\xff\xd8"
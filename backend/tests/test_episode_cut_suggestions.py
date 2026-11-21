"""P1-B: real-video-aware cut suggestions are advisory, measured and dialogue-safe.

The suggestions read the same current passed takes and measured durations the EDL
uses, propose one legal continuous range per shot, and never write a contract or
assemble.  Applying them goes through the existing EpisodePostContract path.
"""
from __future__ import annotations

import hashlib
import shutil
from uuid import uuid4

import pytest

from shine.director.models import DirectorPackageVersion
from shine.models import (
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    utcnow,
)
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes

pytestmark = pytest.mark.integration


def _seed_passed_job(factory, settings, project_id, shot_id, style_id, *, duration_s=6.0):
    content = make_test_video_bytes(duration_s=duration_s, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(bytes(content)).hexdigest()
    relative = f"artifacts/{project_id}/cut-suggest/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    session = factory()
    try:
        job = Job(project_id=project_id, shot_id=shot_id, shot_revision=1, style_version_id=style_id,
                  request={}, idempotency_key=uuid4().hex, status="PASSED", quality_verification="real",
                  provider="comfyui", generation_mode="Ref2VA")
        session.add(job)
        session.flush()
        attempt = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint=digest, status="COMPLETED",
                             lease_owner="test", lease_expires_at=utcnow(), output_path=relative,
                             output_sha256=digest, output_bytes=len(content))
        session.add(attempt)
        session.flush()
        artifact = MediaArtifact(project_id=project_id, job_id=job.id, attempt_id=attempt.id,
                                 artifact_kind="video", storage_path=relative, sha256=digest,
                                 byte_size=len(content), content_type="video/mp4", is_mock=False)
        session.add(artifact)
        session.flush()
        job.artifact_id = artifact.id
        session.commit()
        return job.id
    finally:
        session.close()


def _run_episode(factory, project_id, shot_ids, job_ids):
    with factory.begin() as db:
        package = DirectorPackageVersion(project_id=project_id, revision=1, manifest={},
                                         scope_digest="a" * 64, manifest_sha256="b" * 64, policy_hash="c" * 64)
        db.add(package)
        db.flush()
        run = ProductionRun(project_id=project_id, package_id=package.id, package_revision=1,
                            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
                            idempotency_key="run-cut-suggest", request_hash="d" * 64, status="COMPLETED")
        db.add(run)
        db.flush()
        episode = ProductionRunEpisode(run_id=run.id, project_id=project_id, episode_id="e1",
                                       position=1, manifest_sha256="e" * 64, shot_ids=list(shot_ids),
                                       status="DELIVERED")
        db.add(episode)
        db.flush()
        series = ProductionSeries(project_id=project_id, status="COMPLETED", provider="comfyui",
                                  shot_count=len(shot_ids), completed_count=len(shot_ids),
                                  finalization_state="RELEASED")
        db.add(series)
        db.flush()
        for position, (sid, jid) in enumerate(zip(shot_ids, job_ids, strict=False)):
            db.add(ProductionSeriesItem(series_id=series.id, shot_id=sid, position=position,
                                        status="PASSED", job_id=jid))
        db.add(ProductionRunBatch(run_id=run.id, episode_row_id=episode.id, episode_id="e1",
                                  batch_key="b1", position=0, input_digest="f" * 64,
                                  shot_ids=list(shot_ids), status="PASSED", series_id=series.id))
        return run.id, episode.episode_id


def _skip_without_ffmpeg(settings):
    if not shutil.which(settings.ffmpeg_path) or not shutil.which(settings.ffprobe_path):
        pytest.skip("FFmpeg/FFprobe is not installed")


def test_cut_suggestions_use_real_duration_and_keep_dialogue(api):
    client, factory, settings = api
    _skip_without_ffmpeg(settings)
    project = create_project(client, "剪辑建议测试")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], characters=["R01"],
                       dialogue=[{"speaker_id": "R01", "text": "你好。", "start_s": 2.0, "end_s": 3.0}],
                       duration=6)
    job_id = _seed_passed_job(factory, settings, project, shot["id"], style["id"], duration_s=6.0)
    run_id, episode_id = _run_episode(factory, project, [shot["id"]], [job_id])

    resp = client.get(f"/api/v1/production-runs/{run_id}/episodes/{episode_id}/cut-suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory"] is True
    assert body["rules_version"] == "run-episode-cut-suggestions-v1"
    s = body["suggestions"][0]
    assert s["status"] == "ok"
    # Measured real duration, not the planned number.
    assert s["real_duration_s"] == pytest.approx(6.0, abs=0.15)
    # A 2s silent head and 3s silent tail are candidate trims, marked uncertain,
    # and the range still contains the whole spoken line 2.0-3.0s.
    assert s["confidence"] == "uncertain"
    assert s["in_s"] == pytest.approx(1.7, abs=0.02)
    assert s["out_s"] == pytest.approx(3.5, abs=0.02)
    assert s["in_s"] <= 2.0 and s["out_s"] >= 3.0
    assert s["changed"] is True
    assert s["dialogue_line_count"] == 1


def test_cut_suggestions_keep_full_take_without_dialogue(api):
    client, factory, settings = api
    _skip_without_ffmpeg(settings)
    project = create_project(client, "无台词剪辑建议")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], duration=6)
    job_id = _seed_passed_job(factory, settings, project, shot["id"], style["id"], duration_s=6.0)
    run_id, episode_id = _run_episode(factory, project, [shot["id"]], [job_id])

    body = client.get(f"/api/v1/production-runs/{run_id}/episodes/{episode_id}/cut-suggestions").json()
    s = body["suggestions"][0]
    assert s["status"] == "ok"
    # No dialogue: never guess a trim from picture content; keep the whole take.
    assert s["in_s"] == 0.0
    assert s["out_s"] == pytest.approx(6.0, abs=0.15)
    assert s["confidence"] == "verified"
    assert s["changed"] is False


def test_cut_suggestions_report_not_passed_without_a_take(api):
    client, factory, settings = api
    _skip_without_ffmpeg(settings)
    project = create_project(client, "未通过剪辑建议")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], duration=6)
    run_id, episode_id = _run_episode(factory, project, [shot["id"]], [])

    body = client.get(f"/api/v1/production-runs/{run_id}/episodes/{episode_id}/cut-suggestions").json()
    s = body["suggestions"][0]
    assert s["status"] == "not_passed"
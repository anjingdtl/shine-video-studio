from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime

from shine.director.models import DirectorPackageVersion
from shine.media.artifacts import store_artifact
from shine.media.delivery_models import DeliveryOrder
from shine.media.thumbnails import thumbnail
from shine.media.video_layout import current_job_ids, move_adoption, shot_output_subdir
from shine.media.video_naming import clip_filename, delivery_filename, sanitize_component
from shine.models import (
    AgentTaskRecord,
    AppSetting,
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
)
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from shine.security import sha256_bytes
from tests.conftest import create_project, create_shot, ffmpeg_executable, freeze_style


def _video(db, project_id, *, shot_job, content=b"video-bytes", kind="video", path=None):
    digest = sha256_bytes(content)
    if path is None:
        path = f"artifacts/{project_id}/fixture/{digest}.mp4"
    artifact = MediaArtifact(
        project_id=project_id,
        job_id=shot_job.id if shot_job else None,
        attempt_id=None,
        artifact_kind=kind,
        storage_path=path,
        sha256=digest,
        byte_size=len(content),
        content_type="video/mp4",
        is_mock=False,
        provenance={"clips": []},
    )
    db.add(artifact)
    db.flush()
    return artifact


def _passed_job(db, project_id, shot, style_id, artifact, *, status="PASSED", created=None):
    job = Job(
        project_id=project_id,
        shot_id=shot["id"],
        shot_revision=shot["revision"],
        style_version_id=style_id,
        request={"shot_id": shot["id"]},
        idempotency_key=f"idem-{artifact.id}",
        status=status,
        provider="comfyui",
        artifact_id=artifact.id,
    )
    if created is not None:
        job.created_at = created
    db.add(job)
    db.flush()
    artifact.job_id = job.id
    db.flush()
    return job


def _write(db, settings, artifact, content):
    target = settings.data_dir / artifact.storage_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _library(client, project_id):
    response = client.get(f"/api/v1/projects/{project_id}/video-library")
    assert response.status_code == 200, response.text
    return response.json()


def _setup(api):
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    style = freeze_style(client, project_id)
    shot_a = create_shot(client, project_id, style["id"], title="01 · 平视全景")
    shot_b = create_shot(client, project_id, style["id"], title="02 · 俯拍近景")
    return client, factory, settings, project_id, style, shot_a, shot_b


def _delete(client, project_id, ids):
    return client.post(
        f"/api/v1/projects/{project_id}/video-library/delete",
        headers={"X-Shine-Local-Action": "1"},
        json={"artifact_ids": ids},
    )


def test_library_lists_every_file_once_with_real_names(api):
    client, factory, settings, project_id, style, shot_a, shot_b = _setup(api)
    with factory() as db:
        first = _video(db, project_id, shot_job=None, content=b"first")
        _write(db, settings, first, b"first")
        job_first = _passed_job(db, project_id, shot_a, style["id"], first)
        second = _video(db, project_id, shot_job=None, content=b"second")
        _write(db, settings, second, b"second")
        job_second = _passed_job(db, project_id, shot_b, style["id"], second)
        reel = _video(db, project_id, shot_job=None, content=b"reel", kind="delivery")
        _write(db, settings, reel, b"reel")
        reel.provenance = {"clips": [
            {"job_id": job_first.id, "artifact_id": first.id},
            {"job_id": job_second.id, "artifact_id": second.id},
        ], "duration_s": 8.9, "width": 1344, "height": 768, "fps": 24.0, "review_state": "pending_review"}
        # Two receipts naming the same reel must still produce one entry.
        for version in (1, 2):
            db.add(DeliveryOrder(project_id=project_id, scope_kind="series", scope_id="s1",
                                 version=version, status="DELIVERED", artifact_id=reel.id))
        db.commit()
        ids = {first.id, second.id, reel.id}

    body = _library(client, project_id)
    assert {entry["artifact_id"] for entry in body["entries"]} == ids
    entries = {entry["artifact_id"]: entry for entry in body["entries"]}
    assert entries[first.id]["kind"] == "shot"
    assert entries[first.id]["shot_range_label"] == "001"
    assert entries[first.id]["episode_label"] == "未分集"
    assert entries[second.id]["shot_range_label"] == "002"
    assert entries[reel.id]["kind"] == "delivery"
    assert entries[reel.id]["shot_range_label"] == "001-002"
    assert entries[reel.id]["version_label"] == "v2"
    assert entries[reel.id]["play_url"].startswith("/api/v1/deliveries/")
    assert entries[first.id]["play_url"].startswith("/api/v1/jobs/")
    assert entries[reel.id]["duration_s"] == 8.9
    assert body["summary"]["total"] == 3


def test_current_version_and_referenced_sources_cannot_be_deleted(api):
    client, factory, settings, project_id, style, shot_a, shot_b = _setup(api)
    with factory() as db:
        old = _video(db, project_id, shot_job=None, content=b"old")
        _write(db, settings, old, b"old")
        job_old = _passed_job(db, project_id, shot_a, style["id"], old, created=datetime(2026, 1, 1))
        current = _video(db, project_id, shot_job=None, content=b"current")
        _write(db, settings, current, b"current")
        _passed_job(db, project_id, shot_a, style["id"], current, created=datetime(2026, 1, 2))
        reel = _video(db, project_id, shot_job=None, content=b"reel", kind="delivery")
        _write(db, settings, reel, b"reel")
        reel.provenance = {"clips": [{"job_id": job_old.id, "artifact_id": old.id}], "review_state": "all_passed"}
        db.add(DeliveryOrder(project_id=project_id, scope_kind="series", scope_id="s2",
                             version=1, status="DELIVERED", artifact_id=reel.id))
        continuity = _video(db, project_id, shot_job=None, content=b"frame", kind="continuity")
        continuity.provenance = {"source": {"artifact_id": old.id, "job_id": job_old.id}}
        _write(db, settings, continuity, b"frame")
        db.commit()
        old_id, current_id = old.id, current.id

    body = _library(client, project_id)
    entries = {entry["artifact_id"]: entry for entry in body["entries"]}
    assert {blocker["code"] for blocker in entries[old_id]["delete_blockers"]} >= {
        "USED_BY_DELIVERY", "USED_BY_CONTINUITY",
    }
    assert entries[old_id]["deletable"] is False
    assert {blocker["code"] for blocker in entries[current_id]["delete_blockers"]} >= {"CURRENT_VERSION"}

    response = _delete(client, project_id, [old_id, current_id])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["deleted"] == []
    blocked = {item["artifact_id"] for item in result["blocked"]}
    assert blocked == {old_id, current_id}
    assert (settings.data_dir / old.storage_path).is_file()


def test_deleting_a_history_take_removes_bytes_and_keeps_the_ledger(api):
    client, factory, settings, project_id, style, shot_a, _shot_b = _setup(api)
    with factory() as db:
        old = _video(db, project_id, shot_job=None, content=b"old-take")
        _write(db, settings, old, b"old-take")
        job_old = _passed_job(db, project_id, shot_a, style["id"], old, created=datetime(2026, 1, 1))
        current = _video(db, project_id, shot_job=None, content=b"new-take")
        _write(db, settings, current, b"new-take")
        _passed_job(db, project_id, shot_a, style["id"], current, created=datetime(2026, 1, 2))
        db.commit()
        old_id, old_job_id, old_path = old.id, job_old.id, old.storage_path

    response = _delete(client, project_id, [old_id])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["deleted"] == [old_id]
    assert result["files_deleted"] == 1
    assert not (settings.data_dir / old_path).exists()

    with factory() as db:
        row = db.get(MediaArtifact, old_id)
        assert row is not None and row.deleted_at is not None
        assert row.deleted_reason == "USER_DELETED"
        job = db.get(Job, old_job_id)
        assert job is not None and job.artifact_id == old_id  # the history is not rewritten

    # A repeated request is idempotent, not a second deletion.
    again = _delete(client, project_id, [old_id])
    assert again.json()["already_deleted"] == [old_id]
    assert _library(client, project_id)["summary"]["total"] == 1


def test_missing_and_changed_files_are_reported_and_missing_can_be_cleared(api):
    client, factory, settings, project_id, style, shot_a, _shot_b = _setup(api)
    with factory() as db:
        gone = _video(db, project_id, shot_job=None, content=b"gone")
        _passed_job(db, project_id, shot_a, style["id"], gone, created=datetime(2026, 1, 1))
        tampered = _video(db, project_id, shot_job=None, content=b"tampered")
        _write(db, settings, tampered, b"different-bytes")
        _passed_job(db, project_id, shot_a, style["id"], tampered, created=datetime(2026, 1, 2))
        db.commit()
        gone_id, tampered_id = gone.id, tampered.id

    entries = {entry["artifact_id"]: entry for entry in _library(client, project_id)["entries"]}
    assert entries[gone_id]["file_state"] == "missing"
    assert entries[gone_id]["deletable"] is True
    assert entries[tampered_id]["file_state"] == "changed"
    assert entries[tampered_id]["deletable"] is False

    result = _delete(client, project_id, [gone_id, tampered_id]).json()
    assert result["missing"] == [gone_id]
    assert result["deleted"] == []
    assert [item["artifact_id"] for item in result["blocked"]] == [tampered_id]
    with factory() as db:
        assert db.get(MediaArtifact, gone_id).deleted_reason == "FILE_MISSING"


def test_shared_file_is_kept_when_one_reference_is_deleted(api):
    client, factory, settings, project_id, style, shot_a, shot_b = _setup(api)
    content = b"shared-bytes"
    digest = hashlib.sha256(content).hexdigest()
    shared_path = f"artifacts/{project_id}/shared/{digest}.mp4"
    with factory() as db:
        one = _video(db, project_id, shot_job=None, content=content, path=shared_path)
        _passed_job(db, project_id, shot_a, style["id"], one, created=datetime(2026, 1, 1))
        newer = _video(db, project_id, shot_job=None, content=b"newer")
        _write(db, settings, newer, b"newer")
        _passed_job(db, project_id, shot_a, style["id"], newer, created=datetime(2026, 1, 3))
        two = _video(db, project_id, shot_job=None, content=content, path=shared_path)
        _passed_job(db, project_id, shot_b, style["id"], two, created=datetime(2026, 1, 2))
        _write(db, settings, one, content)
        db.commit()
        one_id = one.id

    result = _delete(client, project_id, [one_id]).json()
    assert result["deleted"] == [one_id]
    assert result["files_kept_shared"] == 1
    assert result["files_deleted"] == 0
    assert (settings.data_dir / shared_path).is_file()


def test_a_foreign_artifact_id_is_refused_before_anything_is_deleted(api):
    client, factory, settings, project_id, style, shot_a, _shot_b = _setup(api)
    other_project = create_project(client, "另一个作品")
    with factory() as db:
        foreign = _video(db, other_project, shot_job=None, content=b"foreign")
        _write(db, settings, foreign, b"foreign")
        db.commit()
        foreign_id = foreign.id
        mine = _video(db, project_id, shot_job=None, content=b"mine")
        _write(db, settings, mine, b"mine")
        _passed_job(db, project_id, shot_a, style["id"], mine)
        db.commit()
        mine_id = mine.id

    response = _delete(client, project_id, [mine_id, foreign_id])
    assert response.status_code == 422
    assert (settings.data_dir / foreign.storage_path).is_file()
    with factory() as db:
        assert db.get(MediaArtifact, mine_id).deleted_at is None


def test_naming_is_ledger_backed_and_windows_safe():
    assert sanitize_component('雾港<夜>账:"一"|?*') == "雾港-夜-账--一----"
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("LPT9.report") == "_LPT9.report"
    assert sanitize_component("  ") == "未命名"
    long_title = "很长" * 80
    assert len(sanitize_component(long_title)) <= 60


def test_named_storage_keeps_content_identity_and_never_overwrites(tmp_path):
    first, digest, size = store_artifact(
        tmp_path, "project", "job-1", b"take-one", "video/mp4",
        filename="《雾港_001镜_未分集》.mp4",
    )
    assert first.endswith("《雾港_001镜_未分集》.mp4")
    assert digest == hashlib.sha256(b"take-one").hexdigest() and size == len(b"take-one")
    second, _digest2, _ = store_artifact(
        tmp_path, "project", "job-2", b"take-two", "video/mp4",
        filename="《雾港_001镜_未分集》.mp4",
    )
    assert second.endswith("《雾港_001镜_未分集》_v2.mp4")
    same, _digest3, _ = store_artifact(
        tmp_path, "project", "job-3", b"take-one", "video/mp4",
        filename="《雾港_001镜_未分集》.mp4",
    )
    assert same == first


def test_clip_and_reel_names_come_from_the_shot_order(api):
    client, factory, _settings, project_id, style, shot_a, shot_b = _setup(api)
    with factory() as db:
        job_a = _passed_job(db, project_id, shot_a, style["id"], type("A", (), {"id": "artifact-a"})())
        job_b = _passed_job(db, project_id, shot_b, style["id"], type("A", (), {"id": "artifact-b"})())
        db.commit()
        from shine.models import Project

        project = db.get(Project, project_id)
        assert clip_filename(db, project, job_a) == "《雾港夜账_001镜_未分集》.mp4"
        assert clip_filename(db, project, job_b) == "《雾港夜账_002镜_未分集》.mp4"
        assert delivery_filename(db, project_id, project.title, [job_a, job_b]) == "《雾港夜账_001-002镜_未分集》.mp4"


def test_episode_number_and_range_come_from_the_run_rows(api):
    client, factory, _settings, project_id, style, shot_a, shot_b = _setup(api)
    with factory() as db:
        package = DirectorPackageVersion(
            project_id=project_id, revision=1, manifest={}, scope_digest="a" * 64,
            manifest_sha256="b" * 64, policy_hash="c" * 64,
        )
        db.add(package)
        db.flush()
        run = ProductionRun(
            project_id=project_id, package_id=package.id, package_revision=1,
            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
            idempotency_key="run-1", request_hash="d" * 64, status="RUNNING",
        )
        db.add(run)
        db.flush()
        episode = ProductionRunEpisode(
            run_id=run.id, project_id=project_id, episode_id="ep-2", position=2,
            manifest_sha256="e" * 64, shot_ids=[shot_b["id"]],
        )
        db.add(episode)
        series = ProductionSeries(project_id=project_id, status="COMPLETED", shot_count=1)
        db.add(series)
        db.flush()
        item = ProductionSeriesItem(series_id=series.id, shot_id=shot_b["id"], position=0, status="PASSED")
        db.add(item)
        db.flush()
        db.add(ProductionRunBatch(
            run_id=run.id, episode_row_id=episode.id, episode_id="ep-2", batch_key="b1",
            input_digest="f" * 64, shot_ids=[shot_b["id"]], series_id=series.id,
        ))
        job_b = _passed_job(db, project_id, shot_b, style["id"], type("A", (), {"id": "artifact-b"})())
        item.job_id = job_b.id
        db.commit()
        from shine.models import Project

        project = db.get(Project, project_id)
        assert clip_filename(db, project, job_b) == "《雾港夜账_001镜_02集》.mp4"


def _series_with_item(db, project_id, shot, status="COMPLETED"):
    series = ProductionSeries(project_id=project_id, status=status, shot_count=1, completed_count=1)
    db.add(series)
    db.flush()
    item = ProductionSeriesItem(series_id=series.id, shot_id=shot["id"], position=0, status="PASSED")
    db.add(item)
    db.flush()
    return series, item


def _retake_record(db, project_id, series_id, job_id, source_job_id=None):
    db.add(AgentTaskRecord(
        project_id=project_id, role_id="segment_regenerate", input_hash=f"in-{series_id}",
        request_identity=f"req-{series_id}", asset_manifest_hash="a" * 64,
        rulepack_id="local-production-action", rulepack_hash="b" * 64,
        model_profile_id="local-production-service", model_id="H3PromptCompiler",
        schema_version="1.0", status="COMPLETED",
        trace={"series_id": series_id, "job_id": job_id, "source_job_id": source_job_id},
    ))
    db.flush()


def test_retake_is_history_until_selected_and_selection_moves_the_files(api):
    client, factory, settings, project_id, style, shot_a, _shot_b = _setup(api)
    with factory() as db:
        series, item = _series_with_item(db, project_id, shot_a)
        first = _video(db, project_id, shot_job=None, content=b"first-take",
                       path=f"artifacts/{project_id}/current/first.mp4")
        _write(db, settings, first, b"first-take")
        job_first = _passed_job(db, project_id, shot_a, style["id"], first, created=datetime(2026, 1, 1))
        item.job_id = job_first.id
        attempt = JobAttempt(
            job_id=job_first.id, attempt_no=1, request_fingerprint="a" * 64, status="COMPLETED",
            lease_owner="worker", lease_expires_at=datetime(2030, 1, 1),
            output_path=first.storage_path, output_sha256=first.sha256, output_bytes=first.byte_size,
        )
        db.add(attempt)
        continuity = _video(db, project_id, shot_job=None, content=b"frame", kind="continuity")
        continuity.provenance = {"kind": "continuity_derivative_v1",
                                 "source": {"artifact_id": first.id, "storage_path": first.storage_path}}
        db.flush()

        retake_series, retake_item = _series_with_item(db, project_id, shot_a)
        replacement = _video(db, project_id, shot_job=None, content=b"replacement-take",
                             path=f"artifacts/{project_id}/history/replacement.mp4")
        _write(db, settings, replacement, b"replacement-take")
        job_replacement = _passed_job(db, project_id, shot_a, style["id"], replacement,
                                      created=datetime(2026, 1, 2))
        retake_item.job_id = job_replacement.id
        _retake_record(db, project_id, retake_series.id, job_replacement.id, job_first.id)
        db.commit()
        ids = {"first": first.id, "replacement": replacement.id, "series": series.id,
               "replacement_job": job_replacement.id, "continuity": continuity.id}

    with factory() as db:
        first = db.get(MediaArtifact, ids["first"])
        replacement = db.get(MediaArtifact, ids["replacement"])
        job_first = db.get(Job, db.get(MediaArtifact, ids["first"]).job_id)
        job_replacement = db.get(Job, ids["replacement_job"])
        assert shot_output_subdir(db, job_first) == "current"
        assert shot_output_subdir(db, job_replacement) == "history"
        assert current_job_ids(db, project_id)[shot_a["id"]] == job_first.id

        moves = move_adoption(db, settings.data_dir, shot_id=shot_a["id"],
                              replacement_job_id=job_replacement.id)
        db.flush()
        assert len(moves) == 2
        assert replacement.storage_path == f"artifacts/{project_id}/current/replacement.mp4"
        assert first.storage_path == f"artifacts/{project_id}/history/first.mp4"
        assert (settings.data_dir / replacement.storage_path).read_bytes() == b"replacement-take"
        assert (settings.data_dir / first.storage_path).read_bytes() == b"first-take"
        assert db.get(JobAttempt, attempt.id).output_path == first.storage_path
        assert db.get(MediaArtifact, ids["continuity"]).provenance["source"]["storage_path"] == first.storage_path

        db.add(AppSetting(key=f"assembly-selection:{ids['series']}",
                          value={shot_a["id"]: job_replacement.id}))
        db.flush()
        assert current_job_ids(db, project_id)[shot_a["id"]] == job_replacement.id
        assert shot_output_subdir(db, job_replacement) == "current"
        db.rollback()


def test_video_thumbnail_uses_a_representative_frame(tmp_path):
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        import pytest

        pytest.skip("FFmpeg is not available for the poster test")
    video = tmp_path / "fade.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
        "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,1)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()

    def average_red(poster: bytes) -> int:
        poster_path = tmp_path / "poster.jpg"
        poster_path.write_bytes(poster)
        result = subprocess.run([
            ffmpeg, "-v", "error", "-i", str(poster_path), "-vf", "scale=1:1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], capture_output=True, check=True)
        return result.stdout[0]

    early = thumbnail(video, digest, ffmpeg, seek_s=0.0).body
    representative = thumbnail(video, digest, ffmpeg, seek_s=1.0).body
    assert average_red(early) < 60
    assert average_red(representative) > 150


def test_organize_moves_current_and_history_into_their_folders(api):
    client, factory, settings, project_id, style, shot_a, _shot_b = _setup(api)
    with factory() as db:
        old = _video(db, project_id, shot_job=None, content=b"old",
                     path=f"artifacts/{project_id}/shots/old.mp4")
        _write(db, settings, old, b"old")
        _passed_job(db, project_id, shot_a, style["id"], old, created=datetime(2026, 1, 1))
        current = _video(db, project_id, shot_job=None, content=b"current",
                         path=f"artifacts/{project_id}/shots/current.mp4")
        _write(db, settings, current, b"current")
        _passed_job(db, project_id, shot_a, style["id"], current, created=datetime(2026, 1, 2))
        reel = _video(db, project_id, shot_job=None, content=b"reel", kind="delivery",
                      path=f"artifacts/{project_id}/delivery/reel.mp4")
        _write(db, settings, reel, b"reel")
        reel.provenance = {"clips": [{"job_id": db.get(Job, current.job_id).id}] if False else []}
        db.add(DeliveryOrder(project_id=project_id, scope_kind="series", scope_id="scope-1",
                             version=1, status="DELIVERED", artifact_id=reel.id))
        db.commit()
        ids = {"old": old.id, "current": current.id, "reel": reel.id}

    response = client.post(f"/api/v1/projects/{project_id}/video-library/organize-current",
                           headers={"X-Shine-Local-Action": "1"}, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files_moved"] == 3
    with factory() as db:
        assert db.get(MediaArtifact, ids["current"]).storage_path.endswith("/current/current.mp4")
        assert db.get(MediaArtifact, ids["old"]).storage_path.endswith("/history/old.mp4")
        assert db.get(MediaArtifact, ids["reel"]).storage_path.endswith("/delivery/current/reel.mp4")
        from sqlalchemy import text

        assert db.execute(
            text("select count(*) from audit_events where project_id=:p "
                 "and event_type='VIDEO_LIBRARY_ORGANIZED'"),
            {"p": project_id},
        ).scalar() == 1
    assert (settings.data_dir / "artifacts" / project_id / "current" / "current.mp4").is_file()
    assert (settings.data_dir / "artifacts" / project_id / "history" / "old.mp4").is_file()

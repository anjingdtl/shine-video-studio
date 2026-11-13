"""The RelayVSR X2 post-production stage: honest blocker, full chain, contract.

Covers the WP5/WP6 contract:

* on this machine the backend is genuinely unavailable (weights unpinned,
  core nodes merged after the pinned ComfyUI revision): an item fails with
  the named blocker and NOTHING is submitted - never a mocked pass;
* with the backend monkeypatched available and a fake renderer speaking the
  official node contract, one item runs the whole chain: upload → intent
  persisted before submit → prompt id recorded → download → lossless audio
  mux → 2x/fps/duration contract → new artifact stored, source untouched;
* an output that is not exactly 2x is a named NEEDS_REVIEW, never a quiet
  1.5x delivery;
* enqueue is idempotent per (project, job, stage), and the episode delivery
  waits while items are unfinished;
* the EDL artifact lookup only returns an upscale artifact whose recorded
  digest really matches the stored artifact row.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from shine.comfy.client import ComfyUIClient
from shine.comfyops import relayvsr
from shine.comfyops.relay_stage import (
    enqueue_relay_items_for_episode,
    episode_relay_items_unfinished,
    relay_artifact_for_job,
    run_pending_relay_items,
)
from shine.models import Job, MediaArtifact, RelayUpscaleItem
from tests.conftest import (
    create_job,
    create_project,
    create_referenced_shot,
    freeze_style,
    make_studio,
    make_test_video_bytes,
    register_workflow_profile,
)
from tests.test_comfy_recovery import fake_comfy, worker_settings  # noqa: F401

pytestmark = pytest.mark.full


class _EpisodeStub:
    def __init__(self, shot_ids):
        self.shot_ids = shot_ids


class _RunStub:
    def __init__(self, project_id, run_id):
        self.project_id = project_id
        self.id = run_id


def _relay_object_info() -> dict:
    info = {name: {"input": {"required": {}, "optional": {}}} for name in (
        "Note", "Reroute", "UNETLoader", "VAELoader", "LoadVideo",
        "BasicScheduler", "BasicGuider", "RandomNoise", "KSamplerSelect",
        "SamplerCustomAdvanced", "CreateVideo", "SaveVideo",
    )}
    info["RelayVSRConditioning"] = {"input": {"required": {"model": [], "images": []}, "optional": {}}}
    info["VSRDecode"] = {"input": {"required": {"vae": [], "samples": [], "images": [], "detail": []}, "optional": {}}}
    return info


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def _passed_job_with_artifact(client, factory, settings) -> tuple[str, str, str, str]:
    """A passed job owning a real stored video artifact: (project, job, artifact, shot)."""
    from shine.media.artifacts import store_artifact

    project_id = create_project(client, title="超分舞台")
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"], character="主角")
    profile = register_workflow_profile(client)
    job = create_job(client, project_id, shot["id"], provider="comfyui",
                     workflow_profile_id=profile["id"])
    video = make_test_video_bytes(duration_s=6, width=512, height=288)
    root = Path(settings.data_dir) / "assets"
    storage_path, digest, _size = store_artifact(root, project_id, job["id"], video, "video/mp4")
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "PASSED"
        artifact = MediaArtifact(
            project_id=project_id, job_id=job["id"],
            artifact_kind="video", storage_path=storage_path,
            sha256=digest, byte_size=len(video), content_type="video/mp4",
            is_mock=False, provenance={},
        )
        db.add(artifact)
        db.flush()
        row.artifact_id = artifact.id
        return project_id, job["id"], artifact.id, row.shot_id


def _enqueue_one(factory, project_id, job_id, shot_id) -> None:
    with factory.begin() as db:
        run = _RunStub(project_id, "run-1")
        episode = _EpisodeStub([shot_id])
        assert enqueue_relay_items_for_episode(
            db, run, episode, {shot_id: db.get(Job, job_id)}) == 1


def test_unavailable_backend_fails_the_item_by_name_without_submitting(studio, fake_comfy):
    """The pinned machine has no RelayVSR: the item names the blocker, no prompt."""
    client, factory, settings = studio
    project_id, job_id, _artifact_id, shot_id = _passed_job_with_artifact(client, factory, settings)
    _enqueue_one(factory, project_id, job_id, shot_id)

    comfy = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    processed = run_pending_relay_items(
        factory, settings=worker_settings(settings), worker_id="relay-worker",
        comfy_client=comfy, asset_root=Path(settings.data_dir) / "assets")
    assert processed == 1
    assert fake_comfy.prompt_count == 0
    with factory() as db:
        item = db.scalar(select(RelayUpscaleItem))
        assert item.status == "NEEDS_REVIEW"
        assert item.error_code == "RELAYVSR_WEIGHTS_NOT_PINNED"
        assert item.prompt_id is None


def test_full_chain_with_available_backend(studio, fake_comfy, monkeypatch):
    """Upload → intent → submit → download → audio mux → 2x contract → artifact."""
    client, factory, settings = studio
    project_id, job_id, artifact_id, shot_id = _passed_job_with_artifact(client, factory, settings)
    _enqueue_one(factory, project_id, job_id, shot_id)

    monkeypatch.setattr(relayvsr, "RELAYVSR_X2_MODEL_SHA256", "a" * 64, raising=False)
    monkeypatch.setattr(relayvsr, "RELAYVSR_X2_VAE_SHA256", "b" * 64, raising=False)
    monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: _relay_object_info())
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6, width=1024, height=576)

    comfy = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    processed = run_pending_relay_items(
        factory, settings=worker_settings(settings), worker_id="relay-worker",
        comfy_client=comfy, asset_root=Path(settings.data_dir) / "assets")
    assert processed == 1

    with factory() as db:
        item = db.scalar(select(RelayUpscaleItem))
        assert item.status == "PASSED", (item.error_code, item.payload)
        assert item.prompt_id
        assert item.external_outcome == "SENT"
        assert item.intent_sha256
        assert item.output_width == 1024 and item.output_height == 576
        # The source artifact is untouched; a NEW artifact carries the bytes.
        assert item.output_artifact_id != artifact_id
        output = db.get(MediaArtifact, item.output_artifact_id)
        assert output.provenance["stage"] == "relayvsr_x2"
        assert output.provenance["source_sha256"] == item.source_sha256
        from shine.media.artifacts import safe_asset_path
        from shine.media.probe import probe_file

        path = safe_asset_path(Path(settings.data_dir) / "assets", output.storage_path)
        info = probe_file(path, ffprobe_path=settings.ffprobe_path or "ffprobe",
                          ffmpeg_path=settings.ffmpeg_path or "ffmpeg")
        assert (info.width, info.height) == (1024, 576)
        assert info.has_audio, "the source audio must survive the mux"


def test_not_exactly_2x_is_a_named_refusal(studio, fake_comfy, monkeypatch):
    client, factory, settings = studio
    project_id, job_id, _artifact_id, shot_id = _passed_job_with_artifact(client, factory, settings)
    _enqueue_one(factory, project_id, job_id, shot_id)

    monkeypatch.setattr(relayvsr, "RELAYVSR_X2_MODEL_SHA256", "a" * 64, raising=False)
    monkeypatch.setattr(relayvsr, "RELAYVSR_X2_VAE_SHA256", "b" * 64, raising=False)
    monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: _relay_object_info())
    # 1.5x: exactly the quiet substitution the contract forbids.
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6, width=768, height=432)

    comfy = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    processed = run_pending_relay_items(
        factory, settings=worker_settings(settings), worker_id="relay-worker",
        comfy_client=comfy, asset_root=Path(settings.data_dir) / "assets")
    assert processed == 1
    with factory() as db:
        item = db.scalar(select(RelayUpscaleItem))
        assert item.status == "NEEDS_REVIEW"
        assert item.error_code.startswith("RELAYVSR_OUTPUT_SIZE_MISMATCH")
        assert item.output_artifact_id is None


def test_enqueue_is_idempotent_and_delivery_waits(studio):
    client, factory, settings = studio
    project_id, job_id, _artifact_id, shot_id = _passed_job_with_artifact(client, factory, settings)

    with factory.begin() as db:
        run = _RunStub(project_id, "run-1")
        episode = _EpisodeStub([shot_id])
        first = enqueue_relay_items_for_episode(db, run, episode, {shot_id: db.get(Job, job_id)})
        second = enqueue_relay_items_for_episode(db, run, episode, {shot_id: db.get(Job, job_id)})
    assert (first, second) == (1, 0)
    with factory() as db:
        assert len(db.scalars(select(RelayUpscaleItem.id)).all()) == 1
        assert episode_relay_items_unfinished(db, "run-1", _EpisodeStub([shot_id])) == 1
        item = db.scalar(select(RelayUpscaleItem))
        item.status = "PASSED"
        db.commit()
        assert episode_relay_items_unfinished(db, "run-1", _EpisodeStub([shot_id])) == 0


def test_edl_prefers_a_fully_verified_upscale_artifact(studio):
    client, factory, settings = studio
    project_id, job_id, artifact_id, _shot_id = _passed_job_with_artifact(client, factory, settings)
    with factory.begin() as db:
        db.add(RelayUpscaleItem(
            project_id=project_id, run_id="run-1", job_id=job_id, shot_id=_shot_id,
            stage="relayvsr_x2", source_artifact_id=artifact_id,
            source_sha256="0" * 64, status="PASSED",
            output_artifact_id=artifact_id, output_sha256=None,
        ))
    with factory() as db:
        # output_sha256 None -> the identity check rejects it: a half-written
        # row must never leak into the delivery EDL.
        assert relay_artifact_for_job(db, job_id) is None
        item = db.scalar(select(RelayUpscaleItem))
        item.output_sha256 = db.get(MediaArtifact, artifact_id).sha256
        db.commit()
        assert relay_artifact_for_job(db, job_id).id == artifact_id

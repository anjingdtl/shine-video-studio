"""Continuity derivatives use real decoded media, never master assets."""

import hashlib
import shutil

import pytest

from shine.comfy.media import ComfyReferenceError
from shine.comfy.provider import ComfyUIRenderService
from shine.media.continuity import derive_continuity_media, load_continuity_reference, prepare_continuity_artifact
from shine.media.probe import probe_file
from shine.models import Job, MediaArtifact, Project, Shot
from shine.schemas import ContinuityMode, CreateJob, RenderProvider, ShotSpec
from shine.services import make_render_request
from tests.conftest import make_test_video_bytes
from tests.test_director_long_form import _seed_passed_job, _studio


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_derives_frame_video_and_audio_from_verified_source(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(make_test_video_bytes(duration_s=4, width=64, height=64))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    frame = derive_continuity_media(source, digest, "last_frame")
    assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")
    for mode, suffix, mime in [("video_reference", ".mp4", "video/mp4"), ("audio_reference", ".wav", "audio/wav")]:
        result = derive_continuity_media(source, digest, mode, start_s=1, end_s=3)
        target = tmp_path / (mode + suffix)
        target.write_bytes(result.content)
        info = probe_file(target)
        assert result.content_type == mime and result.source_sha256 == digest
        assert result.start_s == 1 and result.end_s == 3 and result.duration_s == 2
        assert 1.95 <= info.duration_s <= 2.1
        assert info.has_video if mode == "video_reference" else info.has_audio
    with pytest.raises(ValueError, match="CONTINUITY_SOURCE_HASH_MISMATCH"):
        derive_continuity_media(source, "0" * 64, "last_frame")
    with pytest.raises(ValueError, match="CONTINUITY_REFERENCE_WINDOW_INVALID"):
        derive_continuity_media(source, digest, "video_reference", start_s=0, end_s=1)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_absent_audio_is_not_silently_substituted(tmp_path):
    source = tmp_path / "silent.mp4"
    source.write_bytes(make_test_video_bytes(duration_s=4, width=64, height=64, with_audio=False))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="CONTINUITY_SOURCE_AUDIO_MISSING"):
        derive_continuity_media(source, digest, "audio_reference")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_derivative_is_idempotent_and_source_invalidation_blocks_reuse(api):
    client, factory, settings = api
    studio = _studio(client, shots=1)
    job_id = _seed_passed_job(factory, settings, studio["project"], studio["shots"][0]["id"], studio["style"]["id"])
    artifact_id = prepare_continuity_artifact(factory, settings, studio["project"], job_id, "last_frame")
    assert prepare_continuity_artifact(factory, settings, studio["project"], job_id, "last_frame") == artifact_id
    with factory.begin() as db:
        artifact = db.get(MediaArtifact, artifact_id)
        assert artifact.artifact_kind == "continuity"
        assert artifact.provenance["source"]["job_id"] == job_id
        assert artifact.is_mock is False
        ref = load_continuity_reference(db, studio["project"], artifact_id, job_id, "last_frame")
        assert ref.sha256 == artifact.sha256
        with pytest.raises(ValueError, match="CONTINUITY_DERIVATIVE_INVALID"):
            load_continuity_reference(db, studio["project"], artifact_id, job_id, "audio_reference")
        db.get(Job, job_id).stale = True
    with pytest.raises(ValueError, match="CONTINUITY_SOURCE_NOT_VERIFIED"):
        prepare_continuity_artifact(factory, settings, studio["project"], job_id, "last_frame")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_source_invalidated_during_extraction_is_not_recorded(api, monkeypatch):
    from sqlalchemy import select

    from shine.media import continuity

    client, factory, settings = api
    studio = _studio(client, shots=1)
    job_id = _seed_passed_job(factory, settings, studio["project"], studio["shots"][0]["id"], studio["style"]["id"])
    derive = continuity.derive_continuity_media

    def invalidate_after_read(*args, **kwargs):
        result = derive(*args, **kwargs)
        with factory.begin() as db:
            db.get(Job, job_id).stale = True
        return result

    monkeypatch.setattr(continuity, "derive_continuity_media", invalidate_after_read)
    with pytest.raises(ValueError, match="CONTINUITY_SOURCE_NOT_VERIFIED"):
        prepare_continuity_artifact(factory, settings, studio["project"], job_id, "last_frame")
    with factory() as db:
        assert not db.scalars(
            select(MediaArtifact).where(MediaArtifact.job_id == job_id, MediaArtifact.artifact_kind == "continuity")
        ).all()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_compiled_request_names_the_derived_frame_and_keeps_master_bindings(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    source_id = _seed_passed_job(factory, settings, studio["project"], studio["shots"][0]["id"], studio["style"]["id"])
    derived_id = prepare_continuity_artifact(factory, settings, studio["project"], source_id, "last_frame")
    with factory() as db:
        source = db.get(Job, source_id)
        shot = db.get(Shot, studio["shots"][1]["id"])
        spec = ShotSpec.model_validate(shot.spec)
        spec.continuity.mode = ContinuityMode.LAST_FRAME
        spec.continuity.previous_shot_id = source.shot_id
        spec.continuity.previous_job_id = source.id
        body = CreateJob(shot_id=shot.id, provider=RenderProvider.COMFYUI, workflow_profile_id=studio["profile"]["id"])
        request, assets, _ = make_render_request(
            db, db.get(Project, studio["project"]), shot, body, spec=spec, continuity_artifact_id=derived_id
        )
        refs = [ref for ref in request.reference_manifest if ref.role == "continuity_derivative"]
        assert len(refs) == 1 and refs[0].asset_id == derived_id
        assert refs[0].label in request.compiled_prompt
        assert len(request.reference_manifest) == len(shot.bindings) + 1
        ref = next(asset for asset in assets if asset.id == derived_id)
        service = ComfyUIRenderService(None, settings.data_dir)
        assert service._verify_asset_for_upload(refs[0], ref, project_id=studio["project"]).startswith(b"\x89PNG")
        source_path = settings.data_dir / ref.provenance["source"]["storage_path"]
        original = source_path.read_bytes()
        source_path.write_bytes(b"tampered source")
        with pytest.raises(ComfyReferenceError, match="continuity source changed"):
            service._verify_asset_for_upload(refs[0], ref, project_id=studio["project"])
        source_path.write_bytes(original)
        (settings.data_dir / ref.storage_path).write_bytes(b"tampered")
        with pytest.raises(ComfyReferenceError):
            service._verify_asset_for_upload(refs[0], ref, project_id=studio["project"])

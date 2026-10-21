"""Global review checks for the M1/M2/M3 offline hardening."""

from __future__ import annotations

import pytest

from shine.comfy.client import ComfyNodeUnavailable, ComfyWorkflowVersionMismatch
from shine.comfy.provider import ComfyUIRenderService
from shine.h3.compiler import H3PromptCompiler, PromptCompilationError
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import (
    FrameRole,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from shine.security import is_loopback_url, require_loopback_url
from tests.conftest import make_test_video_bytes

STYLE = StyleBible(
    photography_language="平视",
    shot_blocking="轴线",
    color_palette="青灰",
    lighting_tone="侧光",
    lens_texture="浅景深",
    editing_rhythm="停顿后切",
    sound_performance="自然语速",
)


def manifest_item(
    *,
    use_as: ReferenceUse,
    label: str,
    content_type: str,
    frame_role: FrameRole | None = None,
    duration_s: float | None = None,
    entity_id: str | None = None,
    role: str | None = None,
) -> ReferenceManifestItem:
    return ReferenceManifestItem(
        label=label,
        asset_id=f"asset-{label}",
        sha256="a" * 64,
        storage_key="assets/aa/x",
        content_type=content_type,
        use_as=use_as,
        frame_role=frame_role,
        duration_s=duration_s,
        entity_id=entity_id,
        role=role,
    )


def compile_manifest(manifest, *, mode=None, spec=None):
    compiler = H3PromptCompiler()
    return compiler.compile(
        spec=spec or ShotSpec(story_beat="雨夜码头", duration_target_s=6, style_version_id="style-v1"),
        style=STYLE,
        manifest=manifest,
        mode_hint=mode,
    )


def test_i2va_rejects_a_last_frame_picture():
    manifest = [
        manifest_item(
            use_as=ReferenceUse.PICTURE,
            label="<Picture 1>",
            content_type="image/png",
            frame_role=FrameRole.LAST,
        )
    ]
    with pytest.raises(PromptCompilationError) as excinfo:
        compile_manifest(manifest, mode=GenerationMode.I2VA)
    assert excinfo.value.code == "H3_FRAME_ROLE_MISMATCH"


def test_fl2va_requires_a_first_and_a_last_frame():
    manifest = [
        manifest_item(
            use_as=ReferenceUse.PICTURE,
            label="<Picture 1>",
            content_type="image/png",
            frame_role=FrameRole.FIRST,
        ),
        manifest_item(
            use_as=ReferenceUse.PICTURE,
            label="<Picture 2>",
            content_type="image/png",
            frame_role=FrameRole.KEYFRAME,
        ),
    ]
    with pytest.raises(PromptCompilationError) as excinfo:
        compile_manifest(manifest, mode=GenerationMode.FL2VA)
    assert excinfo.value.code == "H3_FRAME_ROLE_MISMATCH"


def test_two_pictures_without_frame_roles_fall_back_to_ref2va():
    manifest = [
        manifest_item(use_as=ReferenceUse.PICTURE, label="<Picture 1>", content_type="image/png"),
        manifest_item(use_as=ReferenceUse.PICTURE, label="<Picture 2>", content_type="image/png"),
    ]
    compiled = compile_manifest(manifest)
    assert compiled.mode == GenerationMode.REF2VA


def test_ref2va_requires_a_bound_speaker_reference():
    manifest = [
        manifest_item(use_as=ReferenceUse.AUDIO, label="<Audio 1>", content_type="audio/wav", duration_s=4),
    ]
    spec = ShotSpec(
        story_beat="雨夜码头",
        duration_target_s=6,
        style_version_id="style-v1",
        characters=["林岚"],
        dialogue=[{"speaker_id": "林岚", "text": "东西在里面", "start_s": 0.0, "end_s": 2.0}],
    )
    with pytest.raises(PromptCompilationError) as excinfo:
        compile_manifest(manifest, spec=spec)
    assert excinfo.value.code == "H3_SPEAKER_REFERENCE_MISSING"


def test_mandarin_dialogue_uses_the_language_tag():
    manifest = [
        manifest_item(
            use_as=ReferenceUse.SUBJECT,
            label="<Subject 1>",
            content_type="image/png",
            entity_id="林岚",
        )
    ]
    spec = ShotSpec(
        story_beat="雨夜码头",
        duration_target_s=6,
        style_version_id="style-v1",
        characters=["林岚"],
        dialogue=[{"speaker_id": "林岚", "text": "东西在里面", "start_s": 0.0, "end_s": 2.0}],
    )
    compiled = compile_manifest(manifest, spec=spec)
    assert "<d>[Chinese] 东西在里面</d>" in compiled.prompt


def test_technical_qc_separates_metadata_probing_from_a_full_decode(tmp_path):
    payload = make_test_video_bytes(duration_s=2, width=320, height=240, fps=24, with_audio=True)
    target = tmp_path / "sample.mp4"
    target.write_bytes(payload)
    result = TechnicalQC(ffprobe_path="missing-ffprobe", ffmpeg_path="missing-ffmpeg").check(
        target,
        expectations=TechnicalExpectations(duration_s=2.0, expect_audio=True),
    )
    checks = {check.name: check for check in result.checks}
    assert checks["stream_metadata"].status == "unavailable"
    assert checks["decode"].status == "unavailable"
    assert "not decoded" in checks["decode"].detail.lower()
    assert result.result == "needs_review"


def test_missing_media_tools_are_never_reported_as_a_pass(tmp_path):
    target = tmp_path / "sample.mp4"
    target.write_bytes(make_test_video_bytes(duration_s=2, width=320, height=240, with_audio=True))
    result = TechnicalQC(ffprobe_path="missing-ffprobe", ffmpeg_path="missing-ffmpeg").check(
        target,
        expectations=TechnicalExpectations(duration_s=2.0, expect_audio=True),
    )
    assert result.result != "passed"
    assert any(check.status == "unavailable" for check in result.checks)


def test_renderer_url_must_be_local_unless_explicitly_allowed():
    assert is_loopback_url("http://127.0.0.1:8188")
    assert is_loopback_url("http://localhost:8188")
    assert not is_loopback_url("http://192.168.1.20:8188")
    assert not is_loopback_url("http://example.com")
    assert require_loopback_url("http://127.0.0.1:8188")
    with pytest.raises(Exception) as excinfo:
        require_loopback_url("http://192.168.1.20:8188")
    assert "SHINE_ALLOW_REMOTE_COMFYUI" in str(excinfo.value)
    assert require_loopback_url("http://192.168.1.20:8188", allow_remote=True)


def test_profile_with_a_missing_node_class_is_refused(tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        graph={"1": {"class_type": "H3BaseSampler", "inputs": {"positive_prompt": ""}}},
        parameter_map={"positive_prompt": "1.inputs.positive_prompt"},
        media_transfers=None,
        required_nodes=["H3BaseSampler"],
        checkpoint_id="MiniMax-H3-Base",
        validated_against={"missing_nodes": ["H3BaseSampler"], "missing_inputs": []},
    )
    service = ComfyUIRenderService(client=None, asset_root=tmp_path)
    with pytest.raises(ComfyNodeUnavailable) as excinfo:
        service._check_profile_compatibility(profile)
    assert excinfo.value.code == "COMFY_NODE_UNAVAILABLE"
    assert excinfo.value.details["missing_nodes"] == ["H3BaseSampler"]


def test_profile_with_a_changed_node_definition_is_refused(tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        graph={"1": {"class_type": "H3BaseSampler", "inputs": {"positive_prompt": ""}}},
        parameter_map={"positive_prompt": "1.inputs.positive_prompt"},
        media_transfers=None,
        required_nodes=["H3BaseSampler"],
        checkpoint_id="MiniMax-H3-Base",
        validated_against={
            "missing_nodes": [],
            "missing_inputs": [{"node_id": "1", "class_type": "H3BaseSampler", "input": "positive_prompt"}],
        },
    )
    service = ComfyUIRenderService(client=None, asset_root=tmp_path)
    with pytest.raises(ComfyWorkflowVersionMismatch) as excinfo:
        service._check_profile_compatibility(profile)
    assert excinfo.value.code == "COMFY_WORKFLOW_VERSION_MISMATCH"

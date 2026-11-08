from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shine.comfy.client import ComfyError, ComfyUIClient
from shine.comfy.media import (
    ComfyPendingWorkflowMapping,
    ComfyReferenceError,
    extension_for,
    family_for_content_type,
)
from shine.comfy.provider import ComfyUIRenderService, reference_slots
from shine.models import Asset
from shine.schemas import (
    FrameRole,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    RenderRequest,
    ShotSpec,
    StyleBible,
)
from tests.fake_http import FakeComfyServer

STYLE = StyleBible(
    photography_language="平视",
    shot_blocking="轴线",
    color_palette="青灰",
    lighting_tone="侧光",
    lens_texture="浅景深",
    editing_rhythm="停顿后切",
    sound_performance="自然语速",
)


def make_profile(
    *,
    parameter_map: dict[str, str] | None = None,
    media_transfers: dict[str, dict] | None = None,
    graph: dict | None = None,
    required_nodes: list[str] | None = None,
):
    base_graph = graph or {
        "1": {
            "class_type": "H3BaseSampler",
            "inputs": {
                "positive_prompt": "",
                "seed": 0,
                "duration": 6.0,
                "reference_picture_1": "",
                "reference_video_1": "",
                "reference_audio_1": "",
            },
        },
        "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
    }
    return SimpleNamespace(
        graph=base_graph,
        parameter_map=parameter_map
        or {
            "positive_prompt": "1.inputs.positive_prompt",
            "seed": "1.inputs.seed",
            "duration": "1.inputs.duration",
            "reference_picture_1": "1.inputs.reference_picture_1",
            "reference_video_1": "1.inputs.reference_video_1",
            "reference_audio_1": "1.inputs.reference_audio_1",
        },
        media_transfers=media_transfers,
        required_nodes=required_nodes if required_nodes is not None else ["H3BaseSampler"],
        checkpoint_id="MiniMax-H3-Base",
    )


def manifest_item(
    *,
    use_as: ReferenceUse,
    asset_id: str,
    content_type: str,
    sha256: str = "a" * 64,
    ordinal: int = 0,
    frame_role: FrameRole | None = None,
    duration_s: float | None = None,
) -> ReferenceManifestItem:
    family = {"subject": "Subject", "picture": "Picture", "video": "Video", "audio": "Audio"}[use_as.value]
    return ReferenceManifestItem(
        label=f"<{family} 1>",
        asset_id=asset_id,
        sha256=sha256,
        storage_key=f"assets/{sha256[:2]}/{sha256}",
        content_type=content_type,
        use_as=use_as,
        role="character",
        frame_role=frame_role,
        ordinal=ordinal,
        duration_s=duration_s,
    )


def make_request(manifest: list[ReferenceManifestItem]) -> RenderRequest:
    spec = ShotSpec(story_beat="雨夜码头交换线索", duration_target_s=6, style_version_id="style-v1")
    return RenderRequest(
        idempotency_key="idem-1",
        shot_id="shot-1",
        shot_revision=1,
        style_version_id="style-v1",
        style_hash="b" * 64,
        provider="comfyui",
        generation_mode=GenerationMode.REF2VA,
        reference_manifest=manifest,
        shot_spec=spec,
        style_snapshot=STYLE,
        compiled_prompt="[Shot 1] A rain-soaked pier.",
        prompt_sha256="c" * 64,
        expected_duration_s=6,
    )


def make_asset(
    tmp_path: Path,
    *,
    asset_id: str,
    content_type: str,
    payload: bytes,
    approved: bool = True,
    locked: bool = True,
    filename: str = "asset.bin",
    project_id: str = "project-1",
) -> Asset:
    digest = __import__("hashlib").sha256(payload).hexdigest()
    storage_key = f"assets/{digest[:2]}/{digest}"
    target = tmp_path / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return Asset(
        id=asset_id,
        project_id=project_id,
        filename=filename,
        kind={"image/png": "image", "video/mp4": "video", "audio/wav": "audio"}[content_type],
        content_type=content_type,
        sha256=digest,
        storage_path=storage_key,
        byte_size=len(payload),
        role="character",
        approved=approved,
        locked=locked,
    )


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def test_reference_without_workflow_mapping_is_never_silently_dropped(fake_comfy, tmp_path):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id="asset-1", content_type="image/png")
    profile = make_profile(
        parameter_map={"positive_prompt": "1.inputs.positive_prompt", "duration": "1.inputs.duration"}
    )
    asset = make_asset(tmp_path, asset_id="asset-1", content_type="image/png", payload=b"png-bytes")
    request = make_request([item])
    request.reference_manifest[0].sha256 = asset.sha256
    with pytest.raises(ComfyPendingWorkflowMapping) as excinfo:
        service.submit(request, profile, {asset.id: asset}, project_id="project-1")
    assert "reference_picture_1" in excinfo.value.details["missing_reference_slots"]
    assert fake_comfy.prompt_count == 0
    assert fake_comfy.upload_count == 0


def test_missing_duration_mapping_blocks_the_submission(fake_comfy, tmp_path):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    profile = make_profile(parameter_map={"positive_prompt": "1.inputs.positive_prompt"})
    request = make_request([])
    with pytest.raises(ComfyPendingWorkflowMapping) as excinfo:
        service.submit(request, profile, {}, project_id="project-1")
    assert "duration" in excinfo.value.details["missing_parameters"]
    assert fake_comfy.prompt_count == 0


def test_upload_uses_the_content_type_extension_not_bin(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(
        tmp_path,
        asset_id="asset-1",
        content_type="image/png",
        payload=b"png-bytes",
        filename="角色.png",
    )
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id=asset.id, content_type="image/png", sha256=asset.sha256)
    profile = make_profile()
    submission = service.submit(make_request([item]), profile, {asset.id: asset}, project_id="project-1")
    assert submission.uploaded_references == 1
    assert fake_comfy.upload_count == 1
    uploaded = fake_comfy.uploads[0]
    assert uploaded["filename"].endswith(".png")
    assert ".bin" not in uploaded["filename"]
    assert uploaded["content_type"] == "image/png"


def test_video_reference_without_a_declared_transfer_is_blocked(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-v", content_type="video/mp4", payload=b"mp4-bytes")
    item = manifest_item(
        use_as=ReferenceUse.VIDEO,
        asset_id=asset.id,
        content_type="video/mp4",
        sha256=asset.sha256,
        duration_s=4.0,
    )
    profile = make_profile()
    with pytest.raises(ComfyPendingWorkflowMapping) as excinfo:
        service.submit(make_request([item]), profile, {asset.id: asset}, project_id="project-1")
    assert excinfo.value.details["family"] == "video"
    assert fake_comfy.prompt_count == 0


def test_video_reference_uses_the_declared_transfer_adapter(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-v", content_type="video/mp4", payload=b"mp4-bytes")
    item = manifest_item(
        use_as=ReferenceUse.VIDEO,
        asset_id=asset.id,
        content_type="video/mp4",
        sha256=asset.sha256,
        duration_s=4.0,
    )
    profile = make_profile(
        media_transfers={
            "video": {
                "upload_path": "/upload/video",
                "field_name": "video",
                "subfolder": "shine",
                "required_node_classes": [],
            }
        }
    )
    submission = service.submit(make_request([item]), profile, {asset.id: asset}, project_id="project-1")
    assert submission.uploaded_references == 1
    assert fake_comfy.upload_paths == ["/upload/video"]
    assert fake_comfy.uploads[0]["filename"].endswith(".mp4")
    assert fake_comfy.uploads[0]["content_type"] == "video/mp4"


def test_a_shot_without_a_picture_is_not_refused_by_an_image_transfer_family(tmp_path, fake_comfy):
    """素材是可选类需求：没绑图片的镜头放行，缺的那张由 H3 生成。

    The profile declares an image transfer, but this shot binds no picture at
    all.  The submitted graph prunes the unused image loader, so no
    placeholder file name reaches the renderer: refusing the shot here would
    stop a perfectly renderable shot for a missing optional material.
    """
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    profile = make_profile(
        media_transfers={
            "image": {
                "upload_path": "/upload/image",
                "field_name": "image",
                "subfolder": "shine",
                "required_node_classes": [],
            }
        }
    )
    request = make_request([])
    # No reference at all: the mode is the text-only one.
    request.generation_mode = GenerationMode.T2VA
    submission = service.submit(request, profile, {}, project_id="project-1")
    assert submission.uploaded_references == 0
    assert fake_comfy.prompt_count == 1
    assert fake_comfy.upload_count == 0


def test_a_bound_picture_is_never_dropped_by_the_image_transfer_family(tmp_path, fake_comfy):
    """The exemption only covers a shot that binds no picture at all.

    Every picture that IS bound is uploaded: a reference that exists always
    reaches the renderer, and the family is satisfied by the bound picture.
    """
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    graph = {
        "1": {
            "class_type": "H3BaseSampler",
            "inputs": {
                "positive_prompt": "",
                "seed": 0,
                "duration": 6.0,
                "reference_picture_1": "",
                "reference_picture_2": "",
            },
        },
        "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
    }
    profile = make_profile(
        graph=graph,
        parameter_map={
            "positive_prompt": "1.inputs.positive_prompt",
            "seed": "1.inputs.seed",
            "duration": "1.inputs.duration",
            "reference_picture_1": "1.inputs.reference_picture_1",
            "reference_picture_2": "1.inputs.reference_picture_2",
        },
        media_transfers={
            "image": {
                "upload_path": "/upload/image",
                "field_name": "image",
                "subfolder": "shine",
                "required_node_classes": [],
            }
        },
    )
    first = make_asset(tmp_path, asset_id="asset-p1", content_type="image/png", payload=b"png-one")
    second = make_asset(tmp_path, asset_id="asset-p2", content_type="image/png", payload=b"png-two")
    items = [
        manifest_item(use_as=ReferenceUse.PICTURE, asset_id=first.id, content_type="image/png",
                      sha256=first.sha256, ordinal=0),
        manifest_item(use_as=ReferenceUse.PICTURE, asset_id=second.id, content_type="image/png",
                      sha256=second.sha256, ordinal=1),
    ]
    submission = service.submit(
        make_request(items), profile, {first.id: first, second.id: second}, project_id="project-1"
    )
    assert submission.uploaded_references == 2
    assert fake_comfy.upload_count == 2
    uploaded = {row["filename"] for row in fake_comfy.uploads}
    assert uploaded == {f"shine_{first.id}.png", f"shine_{second.id}.png"}, uploaded


def test_missing_loader_node_capability_is_reported(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-v", content_type="video/mp4", payload=b"mp4-bytes")
    item = manifest_item(
        use_as=ReferenceUse.VIDEO,
        asset_id=asset.id,
        content_type="video/mp4",
        sha256=asset.sha256,
        duration_s=4.0,
    )
    profile = make_profile(
        media_transfers={
            "video": {
                "upload_path": "/upload/video",
                "field_name": "video",
                "required_node_classes": ["LoadVideo"],
            }
        }
    )
    with pytest.raises(ComfyPendingWorkflowMapping) as excinfo:
        service.submit(make_request([item]), profile, {asset.id: asset}, project_id="project-1")
    assert "LoadVideo" in excinfo.value.details["missing_node_classes"]


def test_unapproved_or_unlocked_asset_is_refused_before_upload(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(
        tmp_path, asset_id="asset-1", content_type="image/png", payload=b"png", approved=False
    )
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id=asset.id, content_type="image/png", sha256=asset.sha256)
    with pytest.raises(ComfyReferenceError) as excinfo:
        service.submit(make_request([item]), make_profile(), {asset.id: asset}, project_id="project-1")
    assert "not approved" in str(excinfo.value)
    assert fake_comfy.upload_count == 0

    locked_asset = make_asset(
        tmp_path, asset_id="asset-2", content_type="image/png", payload=b"png2", locked=False
    )
    locked_item = manifest_item(
        use_as=ReferenceUse.PICTURE, asset_id=locked_asset.id, content_type="image/png", sha256=locked_asset.sha256
    )
    with pytest.raises(ComfyReferenceError) as excinfo:
        service.submit(
            make_request([locked_item]), make_profile(), {locked_asset.id: locked_asset}, project_id="project-1"
        )
    assert "not locked" in str(excinfo.value)
    assert fake_comfy.upload_count == 0


def test_asset_changed_on_disk_is_detected_before_upload(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-1", content_type="image/png", payload=b"original")
    target = tmp_path / asset.storage_path
    target.write_bytes(b"tampered")
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id=asset.id, content_type="image/png", sha256=asset.sha256)
    with pytest.raises(ComfyReferenceError) as excinfo:
        service.submit(make_request([item]), make_profile(), {asset.id: asset}, project_id="project-1")
    assert "changed on disk" in str(excinfo.value)
    assert fake_comfy.upload_count == 0


def test_media_type_must_match_the_bound_role(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-1", content_type="video/mp4", payload=b"mp4")
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id=asset.id, content_type="video/mp4", sha256=asset.sha256)
    with pytest.raises(ComfyReferenceError) as excinfo:
        service.submit(make_request([item]), make_profile(), {asset.id: asset}, project_id="project-1")
    assert "media type does not match" in str(excinfo.value)


def test_upload_failure_never_produces_a_prompt(tmp_path, fake_comfy):
    fake_comfy.mode = "upload_failure"
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, max_retries=0)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    asset = make_asset(tmp_path, asset_id="asset-1", content_type="image/png", payload=b"png")
    item = manifest_item(use_as=ReferenceUse.PICTURE, asset_id=asset.id, content_type="image/png", sha256=asset.sha256)
    with pytest.raises(ComfyError):
        service.submit(make_request([item]), make_profile(), {asset.id: asset}, project_id="project-1")
    assert fake_comfy.prompt_count == 0


def test_two_shots_never_cross_talk_between_their_references(tmp_path, fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=tmp_path)
    first = make_asset(tmp_path, asset_id="asset-1", content_type="image/png", payload=b"first")
    second = make_asset(tmp_path, asset_id="asset-2", content_type="image/png", payload=b"second")
    item_one = manifest_item(
        use_as=ReferenceUse.PICTURE, asset_id=first.id, content_type="image/png", sha256=first.sha256
    )
    item_two = manifest_item(
        use_as=ReferenceUse.PICTURE, asset_id=second.id, content_type="image/png", sha256=second.sha256
    )
    request_one = make_request([item_one])
    request_two = make_request([item_two])
    request_two.idempotency_key = "idem-2"
    service.submit(request_one, make_profile(), {first.id: first}, project_id="project-1")
    service.submit(request_two, make_profile(), {second.id: second}, project_id="project-1")
    names = sorted(upload["filename"] for upload in fake_comfy.uploads)
    assert len(names) == 2
    assert any("asset-1" in name for name in names)
    assert any("asset-2" in name for name in names)
    prompts = fake_comfy.submitted_prompts
    assert prompts[0]["prompt"]["1"]["inputs"]["reference_picture_1"].endswith("asset-1.png")
    assert prompts[1]["prompt"]["1"]["inputs"]["reference_picture_1"].endswith("asset-2.png")


def test_reference_slots_are_deterministic_per_family():
    items = [
        manifest_item(use_as=ReferenceUse.PICTURE, asset_id="a", content_type="image/png"),
        manifest_item(use_as=ReferenceUse.SUBJECT, asset_id="b", content_type="image/png"),
        manifest_item(use_as=ReferenceUse.PICTURE, asset_id="c", content_type="image/png"),
    ]
    slots = [logical for logical, _item in reference_slots(items)]
    assert slots == ["reference_picture_1", "reference_subject_1", "reference_picture_2"]


def test_extension_and_family_detection():
    assert extension_for("image/png") == ".png"
    assert extension_for("video/mp4") == ".mp4"
    assert extension_for("audio/wav") == ".wav"
    assert family_for_content_type("video/mp4") == "video"
    assert family_for_content_type("audio/mpeg") == "audio"

from __future__ import annotations

import pytest

from shine.comfy.client import ComfyUIClient
from shine.config import Settings
from shine.quality.revisions import (
    derive_seed,
    manifest_fingerprint,
    parameter_fingerprint,
    revision_for_parameters,
    revision_for_prompt,
    revision_for_references,
)
from shine.schemas import (
    ReferenceManifestItem,
    ReferenceUse,
    SemanticFinding,
    SemanticReviewResult,
    ShotSpec,
    StyleBible,
)
from shine.worker import run_one
from tests.conftest import (
    create_comfy_job,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
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


class ScriptedReviewer:
    """A controllable mock semantic reviewer for the offline repair loop."""

    is_mock = True
    name = "mock-vlm"

    def __init__(self, findings_by_call: list[list[SemanticFinding]]):
        self._calls = list(findings_by_call)
        self.calls = 0

    def review(self, context) -> SemanticReviewResult:
        findings = self._calls.pop(0) if self._calls else []
        self.calls += 1
        return SemanticReviewResult(
            reviewer=self.name,
            model="mock",
            is_mock=True,
            input_sha256=context.artifact_sha256,
            findings=findings,
            overall_confidence=0.95,
            raw_summary="scripted mock review",
        )


def worker_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )


def manifest_item(
    *,
    use_as: ReferenceUse = ReferenceUse.PICTURE,
    asset_id: str = "asset-1",
    content_type: str = "image/png",
    sha256: str = "a" * 64,
    ordinal: int = 0,
) -> ReferenceManifestItem:
    label = {
        "subject": "Subject",
        "picture": "Picture",
        "video": "Video",
        "audio": "Audio",
    }[use_as.value]
    return ReferenceManifestItem(
        label=f"<{label} 1>",
        asset_id=asset_id,
        sha256=sha256,
        storage_key=f"assets/{sha256[:2]}/{sha256}",
        content_type=content_type,
        use_as=use_as,
        ordinal=ordinal,
    )


def make_request(manifest=None) -> dict:
    spec = ShotSpec(
        story_beat="雨夜码头交换线索",
        duration_target_s=6,
        style_version_id="style-v1",
        characters=["林岚"],
        camera={"shot_size": "medium"},
    )
    return {
        "idempotency_key": "idem-1",
        "shot_id": "shot-1",
        "shot_revision": 1,
        "style_version_id": "style-v1",
        "style_hash": "b" * 64,
        "provider": "comfyui",
        "generation_mode": "REF2VA",
        "reference_manifest": [item.model_dump(mode="json") for item in (manifest or [])],
        "shot_spec": spec.model_dump(mode="json"),
        "style_snapshot": STYLE.model_dump(mode="json"),
        "compiled_prompt": "[Shot 1] 雨夜码头。",
        "compiled_prompt_sections": {"integrated_multimodal_description": "[Shot 1] 雨夜码头。"},
        "prompt_sha256": "c" * 64,
        "expected_duration_s": 6,
    }


def test_prompt_revision_recompiles_through_the_h3_compiler():
    request = make_request()
    spec = ShotSpec.model_validate(request["shot_spec"])
    revision = revision_for_prompt(
        request=request,
        spec=spec,
        style=STYLE,
        manifest=[],
        root_cause="PROMPT_WRONG: H3_LABEL_MISMATCH",
        attempt_no=1,
    )
    assert revision is not None
    assert revision.prompt_sha256 != request["prompt_sha256"]
    assert revision.request["compiled_prompt"] != request["compiled_prompt"]
    assert revision.request["compiled_prompt_sections"]
    assert "repair_directive" in revision.request["shot_spec"]["camera"]
    # Locked story facts and the Style Bible are never touched.
    assert revision.request["shot_spec"]["story_beat"] == request["shot_spec"]["story_beat"]
    assert revision.request["style_snapshot"] == request["style_snapshot"]


def test_prompt_revision_is_refused_when_nothing_would_change():
    request = make_request()
    spec = ShotSpec.model_validate(request["shot_spec"])
    # A compiler failure cannot produce a revision.
    broken = spec.model_copy(update={"style_version_id": ""})
    assert (
        revision_for_prompt(
            request=request,
            spec=broken,
            style=STYLE,
            manifest=[],
            root_cause="PROMPT_WRONG: H3_SECTION_MISSING",
            attempt_no=1,
        )
        is None
    )


def test_workflow_parameter_revision_changes_the_seed_and_parameter_hash():
    request = make_request()
    revision = revision_for_parameters(
        request=request,
        root_cause="WORKFLOW_WRONG: COMFY_WORKFLOW_INVALID",
        attempt_no=1,
        new_seed=4242,
        reason="Re-validated the workflow profile and changed the generation parameters",
    )
    assert revision is not None
    assert revision.request["seed"] == 4242
    assert revision.parameter_sha256 != parameter_fingerprint(request)
    assert revision.changes


def test_reference_rebuild_changes_the_manifest_hash():
    request = make_request([manifest_item()])
    rebuilt = [manifest_item(asset_id="asset-2", sha256="d" * 64)]
    revision = revision_for_references(
        request=request,
        manifest=rebuilt,
        root_cause="REFERENCE_WRONG: MISSING_ASSET",
        attempt_no=1,
    )
    assert revision is not None
    assert revision.manifest_sha256 != manifest_fingerprint(
        [ReferenceManifestItem.model_validate(item) for item in request["reference_manifest"]]
    )
    assert revision.request["reference_manifest"][0]["asset_id"] == "asset-2"


def test_reference_rebuild_is_refused_when_the_manifest_is_unchanged():
    request = make_request([manifest_item()])
    same = [ReferenceManifestItem.model_validate(item) for item in request["reference_manifest"]]
    assert (
        revision_for_references(
            request=request,
            manifest=same,
            root_cause="REFERENCE_WRONG: MISSING_ASSET",
            attempt_no=1,
        )
        is None
    )


def test_derive_seed_is_deterministic_but_different():
    request = make_request()
    first = derive_seed(request, 1)
    second = derive_seed(request, 2)
    assert first != second
    assert derive_seed(request, 1) == first


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def _prepare_project(client, *, auto_repair: bool = True):
    project_id = create_project(client)
    policy = client.put(
        f"/api/v1/projects/{project_id}/quality-policy",
        json={
            "require_semantic_review": True,
            "require_audio_review": True,
            "require_style_review": True,
            "min_confidence": 0.7,
            "accept_mock_review": True,
            "allow_unverified_technical": True,
            "priority_thresholds": {"A": 0.85, "B": 0.7, "C": 0.55},
        },
    )
    assert policy.status_code == 200, policy.text
    settings = client.patch(
        f"/api/v1/projects/{project_id}/settings",
        json={"auto_repair_enabled": auto_repair},
    )
    assert settings.status_code == 200, settings.text
    style = freeze_style(client, project_id)
    shot = create_shot(
        client,
        project_id,
        style["id"],
        characters=["林岚"],
        beat="雨夜码头交换线索",
    )
    return project_id, style, shot


def test_auto_repair_creates_a_materially_different_request(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id, _style, shot = _prepare_project(client)
    job = create_comfy_job(client, project_id, shot["id"])
    drift = SemanticFinding(
        code="STYLE_DRIFT",
        category="style",
        severity="hard",
        confidence=0.95,
        message="色彩与冻结风格圣经不一致",
    )
    reviewer = ScriptedReviewer([[drift], []])
    offline = worker_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        semantic_reviewer=reviewer,
    )
    first = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert first["status"] == "QUEUED"
    assert first["semantic_retries"] == 1
    assert first["request_revision"] == 2
    assert first["repairs"][0]["outcome"] == "auto_queued"
    assert "Recompiled" in " ".join(first["repairs"][0]["changes"])

    # The second attempt uses a different request and a different prompt hash.
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        semantic_reviewer=reviewer,
    )
    second = client.get(f"/api/v1/jobs/{job['id']}").json()
    attempts = second["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["request_fingerprint"] != attempts[1]["request_fingerprint"]
    assert attempts[0]["prompt_sha256"] != attempts[1]["prompt_sha256"]
    assert attempts[1]["attempt_kind"] == "repair"
    assert attempts[1]["parent_attempt_id"] == attempts[0]["id"]
    assert attempts[1]["repair_reason"]
    assert attempts[0]["status"] != "COMPLETED" or attempts[0]["output_sha256"]
    # The original attempt is preserved, not overwritten.
    assert attempts[0]["id"] != attempts[1]["id"]
    assert fake_comfy.prompt_count == 2


def test_auto_repair_is_skipped_when_the_project_disables_it(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id, _style, shot = _prepare_project(client, auto_repair=False)
    job = create_comfy_job(client, project_id, shot["id"])
    drift = SemanticFinding(
        code="STYLE_DRIFT",
        category="style",
        severity="hard",
        confidence=0.95,
        message="色彩与冻结风格圣经不一致",
    )
    offline = worker_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        semantic_reviewer=ScriptedReviewer([[drift]]),
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "REPAIR_PENDING"
    assert result["semantic_retries"] == 0
    assert result["repairs"][0]["outcome"] == "escalated"


def test_identity_mismatch_is_never_repaired_automatically(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id, _style, shot = _prepare_project(client)
    job = create_comfy_job(client, project_id, shot["id"])
    identity = SemanticFinding(
        code="IDENTITY_MISMATCH",
        category="identity",
        severity="hard",
        confidence=0.95,
        message="人物身份与批准素材不一致",
    )
    offline = worker_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        semantic_reviewer=ScriptedReviewer([[identity]]),
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "REPAIR_PENDING"
    assert result["semantic_retries"] == 0
    assert result["repairs"][0]["outcome"] == "escalated"


def test_repair_budget_stops_after_two_rounds(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id, _style, shot = _prepare_project(client)
    job = create_comfy_job(client, project_id, shot["id"])
    drift = SemanticFinding(
        code="STYLE_DRIFT",
        category="style",
        severity="hard",
        confidence=0.95,
        message="色彩与冻结风格圣经不一致",
    )
    offline = worker_settings(settings)
    reviewer = ScriptedReviewer([[drift], [drift], [drift], [drift]])
    for _ in range(4):
        run_one(
            factory,
            settings=offline,
            worker_id="comfy-worker",
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
            semantic_reviewer=reviewer,
        )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["semantic_retries"] <= 2
    assert result["status"] in {"REPAIR_PENDING", "NEEDS_REVIEW"}

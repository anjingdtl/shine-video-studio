"""V3 Phase 2 tests: on-demand optional switches, multimodal Ref2VA & honest readiness."""
from __future__ import annotations

import pytest

from shine.comfy.client import ComfyUIClient
from shine.comfy.graph_compiler import (
    LATENT_UPSCALER,
    PDD_FILE,
    GraphCompileError,
    SecondPassSpec,
    compile_ref2va_workflow,
)
from shine.comfy.provider import prepare_workflow_submission
from shine.comfyops import relayvsr
from shine.comfyops.mode_capabilities import probe_optional_capabilities
from shine.comfyops.runtime_configuration import (
    ACTIVE_KEY,
    record_service_evidence,
)
from shine.comfyops.setup_summary import record_installation_choice, relayvsr_machine_readiness
from shine.foundation.h3 import (
    ComfyH3Executor,
    H3ExecutionError,
    H3ExecutionRequest,
    H3ReferenceInput,
    compile_h3_ref2va_graph,
)
from shine.h3.compiler import H3PromptCompiler
from shine.models import AppSetting, Job, MediaArtifact, RelayUpscaleItem
from shine.schemas import (
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from shine.security import sha256_bytes
from shine.strategy import GenerationStrategy, SecondPassConfig, UpscaleConfig, strategy_blockers, strategy_options
from shine.worker import run_one
from tests.conftest import (
    PNG,
    _approve_lock_keeping_operator,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
)
from tests.test_comfy_recovery import fake_comfy, worker_settings  # noqa: F401

pytestmark = pytest.mark.integration


def _object_info_for(graph: dict) -> dict:
    info: dict = {}
    for node in graph.values():
        cls = node["class_type"]
        required: dict = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                required.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                required.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": required, "optional": {}}})
    return info


@pytest.mark.parametrize("accelerator_id", ("native20", "pdd8"))
@pytest.mark.parametrize("second_pass_on", (False, True))
@pytest.mark.parametrize("images,videos,audio", [
    (1, 0, 0),
    (3, 1, 0),
    (6, 3, 3),  # max total 12 with 3 video + 3 audio
    (9, 3, 0),  # max total 12 with 9 image + 3 video
    (9, 0, 3),  # max total 12 with 9 image + 3 audio
])
def test_p2_combination_matrix_minimal_closure(accelerator_id, second_pass_on, images, videos, audio):
    sp = SecondPassSpec() if second_pass_on else None
    compiled = compile_h3_ref2va_graph(
        accelerator_id=accelerator_id,
        second_pass=sp,
        image_count=images,
        video_count=videos,
        audio_count=audio,
    )
    classes = set(compiled.required_nodes)
    assert ("ShineH3SamplingMode" in classes) == (accelerator_id == "pdd8")
    assert ("MiniMaxH3PDDAccApply" in classes) == (accelerator_id == "pdd8" or second_pass_on)
    assert ("MinimaxH3LatentUpscaler3DRefineHandoff" in classes) == second_pass_on
    assert ("MiniMaxH3PDDAccScheduler" in classes) == second_pass_on
    assert ("VRAM_Debug" in classes) == second_pass_on
    assert ("VHS_LoadVideo" in classes) == (videos > 0)
    assert ("VHS_LoadAudioUpload" in classes) == (audio > 0)

    expected_transfers = {"image"}
    if videos:
        expected_transfers.add("video")
    if audio:
        expected_transfers.add("audio")
    assert set(compiled.media_transfers) == expected_transfers

    refs = {f"reference_image_{i}": f"shine/img-{i}.png" for i in range(1, images + 1)}
    refs.update({f"reference_video_{i}": f"shine/vid-{i}.mp4" for i in range(1, videos + 1)})
    refs.update({f"reference_audio_{i}": f"shine/aud-{i}.wav" for i in range(1, audio + 1)})
    prepared = prepare_workflow_submission(
        compiled, prompt="test prompt", seed=77, duration_s=5.0, references=refs, width=672, height=384,
    )
    assert prepared.uploaded_references == images + videos + audio
    ref_node = prepared.prompt["31"]["inputs"]
    assert sum(1 for k in ref_node if k.startswith("ref_images.")) == images
    assert sum(1 for k in ref_node if k.startswith("ref_videos.")) == videos
    assert sum(1 for k in ref_node if k.startswith("ref_audios.")) == audio
    # Video soundtracks are never silently attached into ref_video_audios.
    assert not any(k.startswith("ref_video_audios.") for k in ref_node)


@pytest.mark.parametrize("shape,expected_code", [
    (dict(image_count=0, video_count=1, audio_count=1), "PROJECT_REQUIRES_REF2VA"),
    (dict(image_count=10, video_count=0, audio_count=0), "IMAGE_REFERENCE_LIMIT_EXCEEDED"),
    (dict(image_count=1, video_count=4, audio_count=0), "VIDEO_REFERENCE_LIMIT_EXCEEDED"),
    (dict(image_count=1, video_count=0, audio_count=4), "AUDIO_REFERENCE_LIMIT_EXCEEDED"),
    (dict(image_count=9, video_count=3, audio_count=3), "TOTAL_REFERENCE_LIMIT_EXCEEDED"),
])
def test_p2_boundary_refusals(shape, expected_code):
    with pytest.raises((GraphCompileError, H3ExecutionError)) as exc:
        compile_h3_ref2va_graph(**shape)
    assert exc.value.code == expected_code


def test_missing_vhs_load_video_only_blocks_shots_using_video():
    img_only = compile_ref2va_workflow(accelerator_id="native20", image_count=1)
    inv_without_vhs_video = _object_info_for(img_only.graph)
    checked_img = compile_ref2va_workflow(accelerator_id="native20", image_count=1, object_info=inv_without_vhs_video)
    assert checked_img.validated_against == {"missing_nodes": [], "missing_inputs": []}

    with_video = compile_ref2va_workflow(
        accelerator_id="native20", image_count=1, video_count=1, object_info=inv_without_vhs_video,
    )
    assert with_video.validated_against["missing_nodes"] == ["VHS_LoadVideo"]


def test_probe_optional_capabilities_distinguishes_live_nodes_and_weights():
    full_sp = compile_ref2va_workflow(
        accelerator_id="pdd8", second_pass=SecondPassSpec(), image_count=1, video_count=1, audio_count=1,
    )
    info = _object_info_for(full_sp.graph)
    info["MiniMaxH3PDDAccApply"]["input"]["required"]["pdd_file"] = [[PDD_FILE], {}]
    info["MinimaxH3LatentUpscaler3DRefineHandoff"]["input"]["required"]["model_name"] = [[LATENT_UPSCALER]]

    caps = probe_optional_capabilities(info)
    assert caps["pdd"]["available"] is True
    assert caps["second_pass"]["available"] is True
    assert caps["video_reference"]["available"] is True
    assert caps["audio_reference"]["available"] is True
    assert caps["relayvsr"]["available"] is False
    assert caps["relayvsr"]["blockers"][0]["code"] == "RELAYVSR_NODES_MISSING_IN_LIVE"

    # Missing 3D upscaler weight disables second_pass without blocking pdd.
    info["MinimaxH3LatentUpscaler3DRefineHandoff"]["input"]["required"]["model_name"] = [[]]
    caps_no_up = probe_optional_capabilities(info)
    assert caps_no_up["pdd"]["available"] is True
    assert caps_no_up["second_pass"]["available"] is False
    assert LATENT_UPSCALER in caps_no_up["second_pass"]["missing_weights"]


def test_missing_optional_backends_refused_in_strategy_options_and_blockers(api, tmp_path, monkeypatch):
    _client, factory, settings = api
    # Install root has baseline models/ folder but NO PDD weight, NO 3D upscaler weight,
    # and RelayVSR weights ARE present on disk while live object_info lacks RelayVSR nodes.
    comfy_root = tmp_path / "ComfyUI"
    (comfy_root / "models" / "diffusion_models").mkdir(parents=True)
    (comfy_root / "models" / "vae").mkdir(parents=True)
    (comfy_root / "models" / "diffusion_models" / relayvsr.RELAYVSR_X2_MODEL).write_bytes(b"x")
    (comfy_root / "models" / "vae" / relayvsr.RELAYVSR_X2_FLASH_VAE).write_bytes(b"x")

    with factory.begin() as db:
        inst = record_installation_choice(db, root=str(comfy_root), port=8188, actor="local-user")
        identity = {"instance_id": inst.get("instance_id") or "inst-p2", "instance_sha256": "i" * 64,
                    "catalog_sha256": "c" * 64, "recipe_sha256": "r" * 64,
                    "comfyui_base_url": "http://127.0.0.1:8188", "files": {}}
        from shine.comfyops import setup_summary
        from shine.comfyops.models import ComfyInstance

        db.add(ComfyInstance(id=identity["instance_id"], status="STOPPED", sha256="i" * 64,
                             payload={"root": str(comfy_root), "port": 8188, "managed": True, "adopted": True}))
        monkeypatch.setattr(setup_summary, "_runtime_instance_id", lambda *_args: identity["instance_id"])

        monkeypatch.setattr(
            "shine.comfyops.runtime_configuration._current_identity",
            lambda _db, _s: identity,
        )
        monkeypatch.setattr(
            "shine.comfyops.runtime_configuration.service_evidence_status",
            lambda _db, _s, identity=None: {"status": "READY", "blockers": []},
        )
        base_native = compile_ref2va_workflow(accelerator_id="native20", image_count=1)
        record_service_evidence(db, identity, {
            "mode_capabilities": {"required_nodes": base_native.required_nodes},
            "optional_capabilities": probe_optional_capabilities(_object_info_for(base_native.graph)),
        })
        db.add(AppSetting(key=ACTIVE_KEY, value={"version": 1, "identity": identity, "sha256": "a" * 64}))

    with factory() as db:
        # Even though RelayVSR weight files are on disk, live object_info lacks
        # RelayVSRConditioning/VSRDecode -> must report BLOCKED, never AVAILABLE!
        relay_state = relayvsr_machine_readiness(db, settings)
        assert relay_state["available"] is False
        assert any(b["code"] == "RELAYVSR_NODES_MISSING_IN_LIVE" for b in relay_state["blockers"])

        opts = strategy_options(db, settings)
        acc = {row["accelerator_id"]: row for row in opts["accelerators"]}
        assert acc["native20"]["verified"] is True
        assert acc["pdd8"]["verified"] is False
        assert acc["pdd8"]["unverified_reason"] == "PDD_BACKEND_UNAVAILABLE"
        assert opts["second_pass"]["supported"] is False
        assert opts["upscale"][0]["enabled"] is False

        # Selected missing capabilities must be refused by name, never silently downgraded.
        assert {b["code"] for b in strategy_blockers(
            GenerationStrategy(accelerator_id="pdd8"), db, settings
        )} == {"PDD_BACKEND_UNAVAILABLE"}
        assert {b["code"] for b in strategy_blockers(
            GenerationStrategy(accelerator_id="native20", second_pass=SecondPassConfig(enabled=True)),
            db, settings,
        )} == {"SECOND_PASS_BACKEND_UNAVAILABLE"}
        assert {b["code"] for b in strategy_blockers(
            GenerationStrategy(accelerator_id="native20", upscale=UpscaleConfig(enabled=True)),
            db, settings,
        )} == {"UPSCALE_BACKEND_UNAVAILABLE"}
        # Baseline native20 with optional capabilities OFF has zero blockers!
        assert strategy_blockers(GenerationStrategy(accelerator_id="native20"), db, settings) == []
        # DB-only callers without settings read the same active identity verdict:
        assert {b["code"] for b in strategy_blockers(
            GenerationStrategy(accelerator_id="native20", upscale=UpscaleConfig(enabled=True)),
            db,
        )} == {"UPSCALE_BACKEND_UNAVAILABLE"}

    # Job creation path (make_render_request / create_render_job) enforces the exact same strategy blocker.
    from fastapi import HTTPException

    from shine.models import Project, Shot
    from shine.schemas import CreateJob
    from shine.services import create_render_job
    from tests.conftest import create_referenced_shot

    project_id = create_project(_client, title="HTTP 策略拒绝")
    style = freeze_style(_client, project_id)
    shot_dict = create_referenced_shot(_client, project_id, style["id"], character="林晚")
    with factory.begin() as db:
        project = db.get(Project, project_id)
        shot = db.get(Shot, shot_dict["id"])
        with pytest.raises(HTTPException) as refused:
            create_render_job(
                db,
                project,
                shot,
                CreateJob(
                    shot_id=shot.id,
                    provider="comfyui",
                    strategy={"accelerator_id": "native20", "upscale": {"enabled": True}},
                ),
                settings=settings,
            )
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "STRATEGY_BLOCKED"
    assert {b["code"] for b in refused.value.detail["blockers"]} == {"UPSCALE_BACKEND_UNAVAILABLE"}


def test_7stage_worker_multimodal_video_and_sound_anti_regression(api, fake_comfy, monkeypatch):  # noqa: F811
    """7-stage Worker runs image + video + audio and keeps silent contract clean."""
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=5)
    project_id = create_project(client, title="P2 多模态测试")
    style = freeze_style(client, project_id)

    img_resp = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image", "role": "character", "entity_id": "林晚"},
        files={"file": ("linwan.png", PNG + b"p2-img", "image/png")},
    )
    assert img_resp.status_code == 201, img_resp.text
    img_id = img_resp.json()["id"]
    vid_bytes = make_test_video_bytes(duration_s=4)
    vid_resp = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "video", "role": "prop", "entity_id": "动作参考"},
        files={"file": ("motion.mp4", vid_bytes, "video/mp4")},
    )
    assert vid_resp.status_code == 201, vid_resp.text
    vid_id = vid_resp.json()["id"]
    for aid in (img_id, vid_id):
        _approve_lock_keeping_operator(client, aid)

    shot = create_shot(
        client,
        project_id,
        style["id"],
        title="镜1",
        beat="林晚缓缓转身望向雨夜街口。",
        characters=["林晚"],
        dialogue=[],
        duration=5.0,
        asset_ids=[img_id, vid_id],
        bindings=[
            {"asset_id": img_id, "use_as": "subject", "role": "林晚", "ordinal": 0},
            {"asset_id": vid_id, "use_as": "video", "role": "motion", "ordinal": 1},
        ],
    )
    shot_id = shot["id"]

    strategy = GenerationStrategy(
        accelerator_id="native20",
        second_pass=SecondPassConfig(enabled=True),
    )
    job_resp = client.post(f"/api/v1/projects/{project_id}/jobs", json={
        "shot_id": shot_id,
        "provider": "comfyui",
        "strategy": strategy.model_dump(mode="json"),
    })
    assert job_resp.status_code == 201, job_resp.text
    job_id = job_resp.json()["id"]

    expected = compile_ref2va_workflow(
        accelerator_id="native20",
        second_pass=strategy.second_pass_spec(),
        image_count=1,
        video_count=1,
    )
    monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: _object_info_for(expected.graph))
    comfy_client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    processed = run_one(factory, worker_id="p2-worker", settings=worker_settings(settings), comfy_client=comfy_client)
    assert processed == job_id

    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["artifact_id"], result
    assert result["attempts"][0]["status"] == "COMPLETED", result
    submitted = fake_comfy.prompts[list(fake_comfy.prompts)[0]]["prompt"]
    assert submitted["26"]["class_type"] == "VHS_LoadVideo"
    assert submitted["26"]["inputs"]["video"].startswith("shine/")
    assert submitted["26"]["inputs"]["force_rate"] == 24.0
    assert submitted["31"]["inputs"]["ref_videos.ref_video_0"] == ["26", 0]
    assert "ref_video_audios.ref_video_audio_0" not in submitted["31"]["inputs"]
    # Sound anti-regression: silent shot with video reference still enforces non-vocal contract.
    prompt_text = submitted["31"]["inputs"]["prompt"]
    assert "This shot has no dialogue: the soundtrack is entirely non-vocal." in prompt_text
    assert "Use the visual track only." in prompt_text


def test_foundation_executor_handles_video_reference_and_refuses_missing_loader(tmp_path, fake_comfy, monkeypatch):  # noqa: F811
    vid_path = tmp_path / "ref.mp4"
    img_path = tmp_path / "ref.png"
    vid_bytes = make_test_video_bytes(duration_s=4)
    img_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08\x08\x02\x00\x00\x00\x4b\x6d\x29\xdc"
        b"\x00\x00\x00\x12IDATx\x9cc`\x18\x05\xa3`\x14\x8c\x82Q0\x0a\x00\x00\x02\x08\x00\x01\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    vid_path.write_bytes(vid_bytes)
    img_path.write_bytes(img_bytes)
    fake_comfy.output_bytes = vid_bytes

    prompt_text = "test prompt"
    req = H3ExecutionRequest(
        prompt_text=prompt_text,
        prompt_sha256=sha256_bytes(prompt_text.encode()),
        duration_s=5.0,
        width=672,
        height=384,
        seed=42,
        accelerator_id="native20",
        references=[
            H3ReferenceInput(family="image", path=img_path, filename="ref.png",
                             content_type="image/png", sha256=sha256_bytes(img_bytes)),
            H3ReferenceInput(family="video", path=vid_path, filename="ref.mp4",
                             content_type="video/mp4", sha256=sha256_bytes(vid_bytes)),
        ],
    )
    compiled = compile_h3_ref2va_graph(accelerator_id="native20", image_count=1, video_count=1)
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)

    # Missing VHS_LoadVideo must fail loudly before upload/submit.
    with pytest.raises(H3ExecutionError) as refused:
        ComfyH3Executor().execute(client, req, object_info={"Note": {}})
    assert refused.value.code == "H3_WORKFLOW_NODES_UNAVAILABLE"
    assert fake_comfy.prompt_count == 0

    receipt = ComfyH3Executor().execute(client, req, object_info=_object_info_for(compiled.graph))
    assert receipt.status == "completed"
    submitted = fake_comfy.prompts[list(fake_comfy.prompts)[0]]["prompt"]
    assert submitted["26"]["inputs"]["video"] == "shine/ref.mp4"
    assert submitted["31"]["inputs"]["ref_videos.ref_video_0"] == ["26", 0]


def test_sound_anti_regression_compiler_preserves_silent_and_dialogue_contracts():
    compiler = H3PromptCompiler()
    style = StyleBible(
        photography_language="natural realistic cinematography",
        shot_blocking="grounded actor movement in real space",
        color_palette="cool slate night palette",
        lighting_tone="soft practical tungsten and rain reflections",
        lens_texture="35mm spherical lens with subtle grain",
        editing_rhythm="measured natural rhythm",
        sound_performance="quiet ambient room tone",
        narration_level="none",
    )
    img_item = ReferenceManifestItem(
        label="<Subject 1>",
        asset_id="a1",
        sha256="a" * 64,
        storage_key="k1",
        content_type="image/png",
        use_as=ReferenceUse.SUBJECT,
        role="林晚",
        ordinal=0,
    )
    vid_item = ReferenceManifestItem(
        label="<Video 1>",
        asset_id="v1",
        sha256="v" * 64,
        storage_key="k2",
        content_type="video/mp4",
        use_as=ReferenceUse.VIDEO,
        role="motion",
        ordinal=1,
        duration_s=4.0,
    )
    silent_spec = ShotSpec(
        story_beat="林晚站在雨中回头。",
        duration_target_s=5.0,
        style_version_id="style-1",
        characters=["林晚"],
        camera={"shot_size": "medium"},
        dialogue=[],
    )
    compiled_silent = compiler.compile(
        spec=silent_spec, style=style, manifest=[img_item, vid_item],
        project_language="Chinese", mode_hint=GenerationMode.REF2VA,
    )
    assert "This shot has no dialogue: the soundtrack is entirely non-vocal." in compiled_silent.prompt
    assert "Use the visual track only." in compiled_silent.prompt
    assert "Every voice in this shot speaks" not in compiled_silent.prompt

    aud_item = ReferenceManifestItem(
        label="<Audio 1>",
        asset_id="aud1",
        sha256="c" * 64,
        storage_key="k3",
        content_type="audio/wav",
        use_as=ReferenceUse.AUDIO,
        role="林晚",
        ordinal=2,
        duration_s=3.0,
    )
    dialogue_spec = ShotSpec(
        story_beat="林晚站在雨中开口说话。",
        duration_target_s=5.0,
        style_version_id="style-1",
        characters=["林晚"],
        camera={"shot_size": "medium"},
        dialogue=[{"speaker_id": "林晚", "text": "今天下雨了。", "start_s": 0.5, "end_s": 2.5}],
    )
    compiled_dialogue = compiler.compile(
        spec=dialogue_spec, style=style, manifest=[img_item, vid_item, aud_item],
        project_language="Chinese", mode_hint=GenerationMode.REF2VA,
    )
    assert "Every voice in this shot speaks Chinese." in compiled_dialogue.prompt
    assert "<d>[Chinese]" in compiled_dialogue.prompt
    assert "the soundtrack is entirely non-vocal" not in compiled_dialogue.prompt
    assert "Use the visual track only." in compiled_dialogue.prompt


def test_episode_input_digest_changes_when_relay_upscale_artifact_lands(api):
    """RelayVSR 2X passed artifact must produce a distinct episode_input_digest."""
    from shine.director.models import DirectorPackageVersion
    from shine.models import ProductionSeries, ProductionSeriesItem
    from shine.production.episode_delivery import episode_input_digest
    from shine.production.models import (
        ProductionRun,
        ProductionRunBatch,
        ProductionRunEpisode,
    )

    client, factory, _settings = api
    project_id = create_project(client, title="X2 摘要")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"], title="镜1")

    with factory.begin() as db:
        pkg = DirectorPackageVersion(
            id="pkg-1",
            project_id=project_id,
            revision=1,
            status="CONFIRMED",
            manifest={},
            scope_digest="s" * 64,
            manifest_sha256="m" * 64,
            policy_hash="p" * 64,
        )
        db.add(pkg)
        db.flush()
        job = Job(
            project_id=project_id,
            shot_id=shot["id"],
            shot_revision=shot["revision"],
            style_version_id=style["id"],
            provider="comfyui",
            status="PASSED",
            idempotency_key="x2-digest-job",
            request={},
            artifact_id="art-1x",
        )
        db.add(job)
        db.flush()
        series = ProductionSeries(
            project_id=project_id,
            status="COMPLETED",
        )
        db.add(series)
        db.flush()
        db.add(ProductionSeriesItem(
            series_id=series.id,
            shot_id=shot["id"],
            position=1,
            job_id=job.id,
            status="PASSED",
        ))
        run = ProductionRun(
            project_id=project_id,
            package_id=pkg.id,
            package_revision=1,
            manifest_sha256="m" * 64,
            scope_digest="s" * 64,
            policy_hash="p" * 64,
            idempotency_key="x2-digest-run",
            request_hash="r" * 64,
            status="RUNNING",
        )
        db.add(run)
        db.flush()
        ep = ProductionRunEpisode(
            run_id=run.id,
            project_id=project_id,
            episode_id="ep-01",
            position=1,
            title="第1集",
            manifest_sha256="e" * 64,
            shot_ids=[shot["id"]],
        )
        db.add(ep)
        db.flush()
        batch = ProductionRunBatch(
            run_id=run.id,
            episode_row_id=ep.id,
            episode_id="ep-01",
            position=1,
            batch_key="b1",
            input_digest="d" * 64,
            shot_ids=[shot["id"]],
            series_id=series.id,
        )
        db.add(batch)
        db.flush()
        art_2x = MediaArtifact(
            project_id=project_id,
            job_id=job.id,
            artifact_kind="video",
            storage_path="shots/x2.mp4",
            sha256="2" * 64,
            byte_size=1234,
            content_type="video/mp4",
            is_mock=False,
            provenance={"stage": "relayvsr_x2"},
        )
        db.add(art_2x)
        db.flush()
        batches = [batch]
        # Before relay item passes:
        d_1x = episode_input_digest(db, run, ep, batches)
        db.add(RelayUpscaleItem(
            project_id=project_id,
            run_id=run.id,
            job_id=job.id,
            shot_id=shot["id"],
            stage="relayvsr_x2",
            source_artifact_id="art-1x",
            source_sha256="1" * 64,
            status="PASSED",
            output_artifact_id=art_2x.id,
            output_sha256=art_2x.sha256,
        ))
        db.flush()
        d_2x = episode_input_digest(db, run, ep, batches)
        assert d_2x != d_1x


def test_manual4_worker_video_reference_sha_and_graph_binding(api, tmp_path):
    """4-stage worker validates vid_items SHA and binds reference_video_1 to VHS_LoadVideo."""
    from shine.manual4.storage.models import M4ExecutionSnapshot, M4Job, M4ProductionRun
    from shine.manual4.worker import _execute_single_job

    client, factory, settings = api
    headers = {"x-shine-local-action": "1"}
    m4_factory = client.app.state.m4_session_factory
    m4_root = client.app.state.m4_root

    pid = client.post("/api/v1/platform/manual4-projects", headers=headers, json={"title": "M4 视频测试"}).json()["id"]
    imp = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=headers,
        data={"text": "第1集：夜巷（目标总时长：5s）\n第1镜（5s）：林晚走近。\n人物：林晚\n", "filename": "s.txt"},
    )
    assert imp.status_code == 201, imp.text
    conf = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=headers,
        json={"expected_revision": imp.json()["draft"]["revision"]},
    )
    assert conf.status_code == 200, conf.text
    mats = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    req_id = mats["requirements"][0]["id"]
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=headers,
        data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
        files={"file": ("linwan.png", PNG + b"m4-p2-img", "image/png")},
    )
    assert up.status_code == 200, up.text
    aid = up.json()["asset"]["id"]
    prom = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{aid}/promote",
        headers=headers,
        json={"requirement_id": req_id},
    )
    assert prom.status_code == 200, prom.text
    mats2 = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    shot_id = mats2["shots"][0]["id"]
    img_ver_id = mats2["shots"][0]["bindings"][0]["asset_version_id"]
    client.put(
        f"/api/v1/manual4/projects/{pid}/shots/{shot_id}/bindings",
        headers=headers,
        json={"bindings": [{"asset_version_id": img_ver_id, "use_as": "image", "role_label": "林晚"}]},
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/shots/{shot_id}/prompt/freeze",
        headers=headers,
        json={"prompt_text": "林晚在雨夜走近"},
    )
    run_res = client.post(
        f"/api/v1/manual4/projects/{pid}/production/runs",
        headers=headers,
        json={"scope_kind": "shot", "scope_target_ids": [shot_id], "provider": "comfyui"},
    )
    assert run_res.status_code == 201, run_res.text
    run_id = run_res.json()["run"]["id"]
    job_id = run_res.json()["run"]["jobs"][0]["id"]

    vid_rel = f"projects/{pid}/refs/motion.mp4"
    vid_path = m4_root / vid_rel
    vid_path.parent.mkdir(parents=True, exist_ok=True)
    vid_bytes = make_test_video_bytes(duration_s=2)
    vid_path.write_bytes(vid_bytes)
    vid_sha = sha256_bytes(vid_bytes)

    with m4_factory() as db:
        run_row = db.get(M4ProductionRun, run_id)
        snap = db.get(M4ExecutionSnapshot, run_row.execution_snapshot_id)
        base_item = dict(snap.items_json[0])
        base_item["reference_manifest"] = list(base_item["reference_manifest"]) + [{
            "use_as": "video",
            "storage_path": vid_rel,
            "sha256": vid_sha,
            "content_type": "video/mp4",
            "role_label": "<Video 1>",
        }]
        run_info = {
            "id": run_row.id,
            "project_id": pid,
            "provider": "comfyui",
            "execution_generation": 2,
            "config": snap.config_json,
        }

    # 1. Tampered video SHA must fail with M4_REFERENCE_SHA_MISMATCH before video_stub is called.
    bad_item = dict(base_item)
    bad_item["reference_manifest"] = [
        base_item["reference_manifest"][0],
        {**base_item["reference_manifest"][1], "sha256": "0" * 64},
    ]
    called_bad = False

    def _stub_bad(**_kwargs):
        nonlocal called_bad
        called_bad = True
        return vid_bytes

    res_bad = _execute_single_job(
        m4_factory,
        m4_root,
        run_info,
        job_id,
        bad_item,
        worker_id="w-m4",
        platform_factory=factory,
        settings=settings,
        video_stub=_stub_bad,
    )
    assert res_bad == "FAILED"
    assert called_bad is False
    with m4_factory.begin() as db:
        jr = db.get(M4Job, job_id)
        assert jr is not None and jr.failure_code == "M4_REFERENCE_SHA_MISMATCH"
        jr.status = "QUEUED"
        jr.failure_code = None
        jr.failure_detail = None

    # 2. Valid video item uploads reference_video_1 and compiles VHS_LoadVideo.
    captured: dict[str, object] = {}
    out_bytes = make_test_video_bytes(duration_s=5)

    def _stub_ok(**kwargs):
        captured.update(kwargs)
        return out_bytes

    ok_graph = compile_h3_ref2va_graph(accelerator_id="pdd8", image_count=1, video_count=1).graph
    res_ok = _execute_single_job(
        m4_factory,
        m4_root,
        run_info,
        job_id,
        base_item,
        worker_id="w-m4",
        object_info=_object_info_for(ok_graph),
        platform_factory=factory,
        settings=settings,
        video_stub=_stub_ok,
    )
    with m4_factory() as db:
        done = db.get(M4Job, job_id)
        detail = (done.status, done.failure_code, done.failure_detail) if done else None
    assert res_ok in {"PASSED", "NEEDS_REVIEW"}, detail
    assert any(slot == "reference_video_1" for slot, _, _, _ in captured["references"])  # type: ignore[index]



from __future__ import annotations

from types import SimpleNamespace

import pytest

from shine.comfy.client import ComfySubmissionUncertain, ComfyUIClient, ComfyUIError, ComfyWorkflowError
from shine.comfy.provider import ComfyUIRenderService
from shine.comfy.workflow import apply_parameters, generic_h3_parameters
from shine.config import Settings
from shine.media.probe import make_mp4_fixture
from shine.schemas import GenerationMode, RenderRequest, ShotSpec, StyleBible
from shine.worker import run_one
from tests.conftest import create_job, create_project, create_shot, freeze_style
from tests.fake_http import FakeComfyServer

pytestmark = pytest.mark.integration

GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {"positive_prompt": "", "seed": 0, "duration": 6.0, "reference_picture_1": ""},
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}
PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_picture_1": "1.inputs.reference_picture_1",
}
PROFILE = SimpleNamespace(
    graph=GRAPH,
    parameter_map=PARAM_MAP,
    required_nodes=["H3BaseSampler"],
    checkpoint_id="MiniMax-H3-Base",
)
STYLE = StyleBible(
    photography_language="平视",
    shot_blocking="轴线",
    color_palette="青灰",
    lighting_tone="侧光",
    lens_texture="浅景深",
    editing_rhythm="停顿后切",
    sound_performance="自然语速",
)


def render_request(*, manifest=None) -> RenderRequest:
    spec = ShotSpec(story_beat="雨夜码头交换线索", duration_target_s=6, style_version_id="style-v1")
    return RenderRequest(
        idempotency_key="idem-comfy",
        shot_id="shot-1",
        shot_revision=1,
        style_version_id="style-v1",
        style_hash="a" * 64,
        provider="comfyui",
        generation_mode=GenerationMode.T2VA,
        reference_manifest=manifest or [],
        shot_spec=spec,
        style_snapshot=STYLE,
        compiled_prompt="[Shot 1] A rain-soaked pier.",
        prompt_sha256="b" * 64,
        expected_duration_s=6,
    )


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def test_client_reads_capabilities_and_downloads_outputs(fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    stats = client.system_stats()
    assert stats["system"]["comfyui_version"] == "fake"
    object_info = client.object_info()
    assert "H3BaseSampler" in object_info
    service = ComfyUIRenderService(client, asset_root=__import__("pathlib").Path("."))
    submission = service.submit(render_request(), PROFILE, {})
    assert submission.prompt_id.startswith("prompt-")
    result = service.poll(submission.prompt_id)
    assert result.status == "completed"
    assert result.content == fake_comfy.output_bytes
    assert result.content_type == "video/mp4"


def test_submission_timeout_is_uncertain_but_history_can_be_reconciled(fake_comfy):
    fake_comfy.mode = "timeout_then_accept"
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=0.1, max_retries=0)
    service = ComfyUIRenderService(client, asset_root=__import__("pathlib").Path("."))
    with pytest.raises(ComfySubmissionUncertain):
        service.submit(render_request(), PROFILE, {})
    prompt_id = next(iter(fake_comfy.prompts))
    assert service.reconcile(prompt_id) == "completed"
    assert fake_comfy.prompt_count == 1


def test_missing_output_and_node_errors_are_explicit(fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=__import__("pathlib").Path("."))
    submission = service.submit(render_request(), PROFILE, {})
    fake_comfy.mode = "missing_output"
    result = service.poll(submission.prompt_id)
    assert result.status == "completed_without_output"
    fake_comfy.mode = "node_error"
    with pytest.raises(ComfyWorkflowError):
        service.submit(render_request(), PROFILE, {})
    fake_comfy.mode = "reject"
    with pytest.raises(ComfyUIError):
        service.submit(render_request(), PROFILE, {})


def test_duplicate_prompt_id_is_stable(fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    service = ComfyUIRenderService(client, asset_root=__import__("pathlib").Path("."))
    first = service.submit(render_request(), PROFILE, {})
    second = service.submit(render_request(), PROFILE, {})
    assert first.prompt_id == second.prompt_id
    assert len(fake_comfy.prompts) == 1


def test_workflow_parameter_mapping_is_strict():
    values = generic_h3_parameters(prompt="hello", seed=7, duration_s=6, references={})
    prompt = apply_parameters(GRAPH, PARAM_MAP, values)
    assert prompt["1"]["inputs"]["positive_prompt"] == "hello"
    assert prompt["1"]["inputs"]["seed"] == 7
    with pytest.raises(ComfyWorkflowError):
        apply_parameters(GRAPH, PARAM_MAP, {"unknown": 1})
    with pytest.raises(ComfyWorkflowError):
        apply_parameters(GRAPH, {"bad": "1.inputs.missing"}, {"bad": 1})


def test_autogrow_dotted_parameter_paths_map_and_validate():
    """Real H3 nodes expose autogrow inputs as dotted keys (values.a,
    ref_images.ref_image_0); parameter paths and live object_info checks must
    accept them while still rejecting unknown groups."""
    from shine.comfy.workflow import validate_against_object_info

    graph = {
        "30": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": "round(a * 24)", "values.a": 5.17},
        },
        "31": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["20", 0],
                "prompt": "",
                "width": 480,
                "height": 864,
                "length": ["30", 1],
                "ref_images.ref_image_0": ["40", 0],
            },
        },
    }
    parameter_map = {
        "positive_prompt": "31.inputs.prompt",
        "duration": "30.inputs.values.a",
    }
    object_info = {
        "ComfyMathExpression": {"input": {"required": {"expression": ["STRING"], "values": ["COMFY_AUTOGROW_V3"]}}},
        "MiniMaxH3ReferenceToVideo": {
            "input": {
                "required": {
                    "clip": ["CLIP"],
                    "prompt": ["STRING"],
                    "width": ["INT"],
                    "height": ["INT"],
                    "length": ["INT"],
                },
                "optional": {"ref_images": ["COMFY_AUTOGROW_V3"]},
            }
        },
    }
    result = validate_against_object_info(graph, parameter_map, object_info)
    assert result["missing_nodes"] == []
    assert result["missing_inputs"] == []
    prompt = apply_parameters(graph, parameter_map, {"positive_prompt": "p", "duration": 6.5})
    assert prompt["30"]["inputs"]["values.a"] == 6.5
    assert prompt["31"]["inputs"]["prompt"] == "p"
    without_autogrow = {
        "ComfyMathExpression": {"input": {"required": {"expression": ["STRING"]}}},
        "MiniMaxH3ReferenceToVideo": {"input": {"required": {"clip": ["CLIP"]}}},
    }
    unknown_group = validate_against_object_info(graph, parameter_map, without_autogrow)
    assert unknown_group["missing_inputs"]


def test_comfy_disconnect_is_reported_without_resubmission(fake_comfy):
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=0.2, max_retries=0)
    service = ComfyUIRenderService(client, asset_root=__import__("pathlib").Path("."))
    submission = service.submit(render_request(), PROFILE, {})
    fake_comfy.stop()
    assert service.reconcile(submission.prompt_id).startswith("unavailable:")


def test_workflow_profile_registration_validates_the_template(api):
    client, _factory, _settings = api
    created = client.post(
        "/api/v1/workflow-profiles",
        json={
            "name": "local-h3-t2va",
            "mode": "T2VA",
            "graph": GRAPH,
            "parameter_map": PARAM_MAP,
            "required_nodes": ["H3BaseSampler"],
            "checkpoint_id": "MiniMax-H3-Base",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["graph_sha256"]
    profiles = client.get("/api/v1/workflow-profiles").json()
    assert profiles[0]["name"] == "local-h3-t2va"
    invalid = client.post(
        "/api/v1/workflow-profiles",
        json={
            "name": "invalid",
            "mode": "T2VA",
            "graph": {"1": {"class_type": "H3BaseSampler"}},
            "parameter_map": {"positive_prompt": "1.inputs.positive_prompt"},
        },
    )
    assert invalid.status_code == 422


def test_comfy_worker_never_reports_a_real_video_as_passed_without_decode(api, fake_comfy, monkeypatch):
    # Exercise the post-render QC contract with the synthetic HTTP renderer.
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.output_bytes = make_mp4_fixture(duration_s=6, width=768, height=1344, fps=24, audio=True)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    profile = client.post(
        "/api/v1/workflow-profiles",
        json={
            "name": "worker-profile",
            "mode": "T2VA",
            "graph": GRAPH,
            "parameter_map": PARAM_MAP,
            "required_nodes": ["H3BaseSampler"],
        },
    )
    assert profile.status_code == 201, profile.text
    job = create_job(
        client,
        project_id,
        shot["id"],
        provider="comfyui",
        workflow_profile_id=profile.json()["id"],
    )
    offline_settings = Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )
    assert (
        run_one(
            factory,
            settings=offline_settings,
            worker_id="comfy-worker",
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == job["id"]
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "QC_NEEDS_REVIEW"
    assert result["video_created"] is True
    assert result["artifact_id"]
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1 and artifacts[0]["is_mock"] is False
    qc = client.get(f"/api/v1/jobs/{job['id']}/qc").json()
    assert len(qc) == 1 and qc[0]["is_mock"] is False
    checks = {check["name"]: check["status"] for check in qc[0]["report"]["technical"]["checks"]}
    assert checks["decode"] == "unavailable"
    assert checks["duration"] == "pass"
    assert qc[0]["report"]["result"] == "needs_review"


def test_unload_ack_is_not_release_proof_without_observed_memory(monkeypatch):
    import httpx
    import pytest

    from shine.comfy.client import ComfyUIClient, ComfyUIError

    monkeypatch.setattr("shine.comfy.client.time.sleep", lambda _: None)
    allocation = [1024]

    def handle(request):
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"torch_vram_total": allocation[0], "torch_vram_free": 0}]})
        return httpx.Response(200, json={})

    client = ComfyUIClient("http://127.0.0.1:1", transport=httpx.MockTransport(handle), max_retries=0,
                           model_release_timeout_s=0.05, model_release_poll_interval_s=0.01)
    with pytest.raises(ComfyUIError, match="MODEL_RELEASE_UNCONFIRMED"):
        client.free_models()
    allocation[0] = 0
    client.free_models()

"""Tests for MiniMax H3 LowVRAMAttention high-watermark gating and OOM fallback."""

from __future__ import annotations

import struct
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from shine.comfy.client import ComfyUIClient
from shine.comfy.graph_compiler import (
    SecondPassSpec,
    compile_ref2va_workflow,
    is_low_vram_guard_variant,
)
from shine.comfy.provider import ComfyUIRenderService
from shine.comfy.vram_guard import (
    DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD,
    align_h3_frame_count,
    decide_low_vram_attention,
    estimate_h3_attention_tokens,
    extract_comfy_history_error,
    is_cuda_oom_error,
    video_latent_frames,
)
from shine.comfy.workflow import validate_against_object_info, validate_graph
from shine.config import Settings
from shine.foundation.h3 import (
    ComfyH3Executor,
    H3ExecutionRequest,
    H3ReferenceInput,
    compile_h3_ref2va_graph,
)
from shine.manual4.comfy.h3_adapter import compile_m4_ref2va_workflow
from shine.manual4.storage.models import (
    M4AuditEvent,
    M4ExecutionSnapshot,
    M4JobAttempt,
    M4ProductionRun,
)
from shine.manual4.worker import _execute_single_job
from shine.models import AuditEvent, Job
from shine.strategy import DEFAULT_STRATEGY
from shine.worker import run_one
from tests.conftest import (
    create_project,
    create_referenced_shot,
    freeze_style,
    make_studio,
    make_test_video_bytes,
)
from tests.fake_http import FakeComfyServer

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _png_bytes(color: tuple[int, int, int] = (30, 140, 210), size: tuple[int, int] = (64, 64)) -> bytes:
    w, h = size
    raw = (b"\x00" + bytes(color) * w) * h

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _compiled_object_info(graph: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for node in graph.values():
        cls = str(node["class_type"])
        required: dict[str, Any] = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                required.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                required.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": required, "optional": {}}})
    return info


# ---------------------------------------------------------------------------
# 1. Token estimation & threshold boundary unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duration_s", [1.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0])
def test_align_h3_frame_count_matches_length_expression(duration_s: float):
    raw = max(5, round(duration_s * 24))
    expected = raw + (5 - (raw % 17)) % 17
    aligned = align_h3_frame_count(duration_s)
    assert aligned == expected
    assert aligned >= 5
    assert aligned % 17 == 5
    assert video_latent_frames(aligned) == (2 if aligned <= 5 else ((aligned - 5) // 17) * 5 + 2)


def test_token_estimation_and_threshold_boundaries():
    # 0.5 tier (672x384, 5s, 2 images): calibrated PackedLayout seq_len = 11,266 < 45,000
    d_low = decide_low_vram_attention(width=672, height=384, duration_s=5.0, image_count=2)
    assert d_low.enabled is False
    assert d_low.reason == "below_threshold"
    assert d_low.threshold == DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD
    assert d_low.estimate is not None
    assert d_low.estimate.total_tokens == 11_266

    # 0.7 tier (960x544, 5s, 2 images): calibrated PackedLayout seq_len = 21,328 < 45,000
    d_mid = decide_low_vram_attention(width=960, height=544, duration_s=5.0, image_count=2)
    assert d_mid.enabled is False
    assert d_mid.reason == "below_threshold"
    assert d_mid.estimate is not None
    assert d_mid.estimate.total_tokens == 21_328

    # 1.0 tier 5s (1344x768, 5s, 2 images): 40,750 < 45,000 (below the 56k-78k wall)
    d_std = decide_low_vram_attention(width=1344, height=768, duration_s=5.0, image_count=2)
    assert d_std.enabled is False
    assert d_std.estimate is not None
    assert d_std.estimate.total_tokens == 40_750

    # 1.0 tier 7s+ (1344x768, 7s, 2 images): 56,040 >= 45,000 -> auto-enabled
    d_7s = decide_low_vram_attention(width=1344, height=768, duration_s=7.0, image_count=2)
    assert d_7s.enabled is True
    assert d_7s.reason == "token_threshold_reached"
    assert d_7s.estimate is not None
    assert d_7s.estimate.total_tokens == 56_040

    d_10s = decide_low_vram_attention(width=1344, height=768, duration_s=10.0, image_count=2)
    assert d_10s.enabled is True
    assert d_10s.estimate is not None
    assert d_10s.estimate.total_tokens == 76_426

    # Multi-image (9 refs) and video reference push 1344x768 5s across 45,000
    d_9img = decide_low_vram_attention(width=1344, height=768, duration_s=5.0, image_count=9)
    assert d_9img.enabled is True
    assert d_9img.estimate is not None and d_9img.estimate.total_tokens == 47_806

    d_vid = decide_low_vram_attention(width=1344, height=768, duration_s=5.0, image_count=1, video_count=1)
    assert d_vid.enabled is True
    assert d_vid.estimate is not None and d_vid.estimate.total_tokens > 45_000

    # Legacy default when width/height are omitted
    d_legacy = decide_low_vram_attention(image_count=1)
    assert d_legacy.enabled is True
    assert d_legacy.reason == "legacy_default"
    assert d_legacy.estimate is None

    # Force & threshold overrides
    d_zero = decide_low_vram_attention(width=672, height=384, duration_s=5.0, image_count=1, threshold=0)
    assert d_zero.enabled is True
    assert d_zero.reason == "token_threshold_reached"

    d_high = decide_low_vram_attention(width=1344, height=768, duration_s=7.0, image_count=2, threshold=80_000)
    assert d_high.enabled is False
    assert d_high.reason == "below_threshold"

    d_oom = decide_low_vram_attention(
        width=672, height=384, duration_s=5.0, image_count=1, force_low_vram_attention=True
    )
    assert d_oom.enabled is True
    assert d_oom.reason == "oom_fallback"


def test_settings_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD", "0")
    monkeypatch.setenv("SHINE_H3_OOM_LOW_VRAM_RETRY", "0")
    s = Settings.from_env()
    assert s.h3_low_vram_attention_token_threshold == 0
    assert s.h3_oom_low_vram_retry_enabled is False

    monkeypatch.setenv("SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD", "-1")
    with pytest.raises(ValueError, match=">= 0"):
        Settings.from_env()


# ---------------------------------------------------------------------------
# 2. Graph topology & hash contract tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"variant_id": "h3-ref2va-int8-convrot", "accelerator_id": "pdd8", "image_count": 2},
        {"variant_id": "h3-ref2va-int8-convrot", "accelerator_id": "native20", "image_count": 2},
        {
            "variant_id": "h3-ref2va-int8-convrot",
            "accelerator_id": "pdd8",
            "image_count": 2,
            "second_pass": SecondPassSpec(),
        },
        {"variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20", "image_count": 2},
    ],
)
def test_graph_topology_and_recipe_hash_isolation(kwargs: dict[str, Any]):
    with_low = compile_ref2va_workflow(**kwargs, low_vram_attention=True)
    without_low = compile_ref2va_workflow(**kwargs, low_vram_attention=False)

    assert "MiniMaxChunkFeedForward" in without_low.required_nodes
    assert "MiniMaxLowVRAMAttention" not in without_low.required_nodes
    assert "MiniMaxLowVRAMAttention" in with_low.required_nodes

    # Validate connectivity and object_info for both topologies
    for wf in (with_low, without_low):
        validate_graph(wf.graph, required_nodes=wf.required_nodes)
        checked = validate_against_object_info(wf.graph, wf.parameter_map, _compiled_object_info(wf.graph))
        assert checked == {"missing_nodes": [], "missing_inputs": []}

    # recipe_sha256 MUST stay identical; graph_sha256 MUST differ
    assert without_low.recipe_sha256 == with_low.recipe_sha256
    assert without_low.graph_sha256 != with_low.graph_sha256

    # Guard variant verification accepts exact LowVRAM variant and rejects others
    assert is_low_vram_guard_variant(
        with_low.graph_sha256,
        expected_graph_sha256=without_low.graph_sha256,
        **kwargs,
    )
    m4_without = compile_m4_ref2va_workflow(**kwargs, low_vram_attention=False)
    assert m4_without.graph_sha256 == without_low.graph_sha256
    est = estimate_h3_attention_tokens(width=672, height=384, duration_s=5.0, image_count=2)
    assert est.total_tokens == 11_266
    assert not is_low_vram_guard_variant(
        without_low.graph_sha256,
        expected_graph_sha256=without_low.graph_sha256,
        **kwargs,
    )
    assert not is_low_vram_guard_variant(
        "f" * 64,
        expected_graph_sha256=without_low.graph_sha256,
        **kwargs,
    )

    # Lowend without LowVRAM must wire node 14 straight to sampler/scheduler without dangling ["16", 0]
    if kwargs["variant_id"] == "ref2va-lowend-candidate":
        assert "16" not in without_low.graph
        assert without_low.graph["17"]["inputs"]["model"] == ["14", 0]
        assert without_low.graph["50"]["inputs"]["model"] == ["14", 0]


# ---------------------------------------------------------------------------
# 3. OOM error extraction & detection tests
# ---------------------------------------------------------------------------


def test_extract_and_classify_cuda_oom_errors():
    oom_record = {
        "status": {
            "status_str": "error",
            "messages": [
                ["execution_start", {"prompt_id": "p1"}],
                [
                    "execution_error",
                    {
                        "prompt_id": "p1",
                        "node_id": "53",
                        "node_type": "SamplerCustomAdvanced",
                        "exception_type": "torch.OutOfMemoryError",
                        "exception_message": "CUDA out of memory. Tried to allocate 2.40 GiB.",
                        "traceback": ["line 1", "torch.OutOfMemoryError: CUDA out of memory."],
                    },
                ],
            ],
        }
    }
    info = extract_comfy_history_error(oom_record)
    assert info is not None
    assert info["node_id"] == "53"
    assert info["node_type"] == "SamplerCustomAdvanced"
    assert info["exception_type"] == "torch.OutOfMemoryError"
    assert is_cuda_oom_error(info) is True
    assert is_cuda_oom_error(None, "RuntimeError: CUBLAS_STATUS_ALLOC_FAILED") is True
    assert is_cuda_oom_error(None, "Allocation on device 0 would exceed allowed memory") is True

    non_oom_record = {
        "status": {
            "status_str": "error",
            "messages": [
                [
                    "execution_error",
                    {
                        "node_id": "10",
                        "node_type": "UNETLoader",
                        "exception_type": "ValueError",
                        "exception_message": "Model file not found on disk.",
                    },
                ]
            ],
        }
    }
    non_oom = extract_comfy_history_error(non_oom_record)
    assert non_oom is not None
    assert is_cuda_oom_error(non_oom) is False
    assert is_cuda_oom_error(None, "ValueError: invalid prompt") is False
    assert is_cuda_oom_error(None, None) is False


def test_comfy_render_service_poll_populates_oom_error_details(tmp_path: Path):
    server = _MainlineFakeComfyServer(["oom"]).start()
    try:
        server.prompts["prompt-1"] = {"prompt": {}, "extra_data": {}}
        client = ComfyUIClient(server.base_url, timeout_s=2)
        service = ComfyUIRenderService(client, tmp_path)
        res = service.poll("prompt-1")
        assert res.status == "failed"
        assert res.error_code == "COMFY_EXECUTION_ERROR"
        assert res.error_details is not None
        assert res.error_details["exception_type"] == "torch.OutOfMemoryError"
        assert is_cuda_oom_error(res.error_details, res.error) is True
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# 4. Manual4 / ComfyH3Executor OOM single-fallback closed-loop tests
# ---------------------------------------------------------------------------


class _ScriptedOOMComfyClient:
    """In-memory ComfyUI client scripting per-submission outcomes."""

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.uploads: list[str] = []
        self.submitted_prompts: list[dict[str, Any]] = []
        self.submitted_keys: list[str] = []
        self.history_map: dict[str, dict[str, Any]] = {}
        self.output_bytes = b"\x00\x00\x00\x18ftypmp42fake-h3-video"

    def object_info(self) -> dict[str, Any]:
        cw = compile_h3_ref2va_graph(image_count=4, audio_count=2, low_vram_attention=True)
        return _compiled_object_info(cw.graph)

    def upload_file(self, filename: str, content: bytes, **_kwargs) -> dict[str, Any]:
        self.uploads.append(filename)
        return {"name": filename, "subfolder": "shine"}

    def queue_prompt(self, prompt: dict[str, Any], *, extra_data=None) -> dict[str, Any]:
        idx = len(self.submitted_prompts)
        self.submitted_prompts.append(prompt)
        shine_meta = (extra_data or {}).get("shine") or {}
        self.submitted_keys.append(str(shine_meta.get("idempotency_key") or ""))
        pid = f"pid-{idx + 1}"
        outcome = self.outcomes[idx] if idx < len(self.outcomes) else "ok"
        if outcome == "oom":
            self.history_map[pid] = {
                "prompt": [idx + 1, pid, prompt, extra_data, []],
                "extra_data": extra_data,
                "status": {
                    "status_str": "error",
                    "messages": [
                        [
                            "execution_error",
                            {
                                "node_id": "53",
                                "node_type": "SamplerCustomAdvanced",
                                "exception_type": "torch.OutOfMemoryError",
                                "exception_message": "CUDA out of memory. Tried to allocate 1.5 GiB.",
                            },
                        ]
                    ],
                },
                "outputs": {},
            }
        elif outcome == "value_error":
            self.history_map[pid] = {
                "prompt": [idx + 1, pid, prompt, extra_data, []],
                "extra_data": extra_data,
                "status": {
                    "status_str": "error",
                    "messages": [
                        [
                            "execution_error",
                            {
                                "node_id": "31",
                                "node_type": "MiniMaxH3ReferenceToVideo",
                                "exception_type": "ValueError",
                                "exception_message": "Invalid canvas dimension.",
                            },
                        ]
                    ],
                },
                "outputs": {},
            }
        else:
            self.history_map[pid] = {
                "prompt": [idx + 1, pid, prompt, extra_data, []],
                "extra_data": extra_data,
                "status": {"status_str": "success", "completed": True},
                "outputs": {"57": {"videos": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]}},
            }
        return {"prompt_id": pid}

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        if prompt_id is not None:
            return {prompt_id: self.history_map[prompt_id]} if prompt_id in self.history_map else {}
        return dict(self.history_map)

    def queue(self) -> dict[str, Any]:
        return {"queue_running": [], "queue_pending": []}

    def view(self, filename: str, **_kwargs) -> bytes:
        assert filename == "out.mp4"
        return self.output_bytes


def test_comfy_executor_oom_fallback_and_no_double_retry(tmp_path: Path):
    import hashlib

    img_path = tmp_path / "ref.png"
    img_bytes = _png_bytes()
    img_path.write_bytes(img_bytes)
    img_sha = hashlib.sha256(img_bytes).hexdigest()
    prompt_text = "<Picture 1> walks slowly."
    prompt_sha = hashlib.sha256(prompt_text.encode()).hexdigest()

    # 1) OOM on attempt 1 (low-token 672x384) -> retries once with LowVRAM -> succeeds
    client_ok = _ScriptedOOMComfyClient(["oom", "ok"])
    req = H3ExecutionRequest(
        prompt_text=prompt_text,
        prompt_sha256=prompt_sha,
        duration_s=5.0,
        width=672,
        height=384,
        seed=42,
        references=[H3ReferenceInput("image", img_path, "ref.png", "image/png", img_sha)],
        idempotency_key="k-1",
    )
    receipt = ComfyH3Executor().execute(client_ok, req, poll_interval_s=0.05)
    assert receipt.status == "completed"
    assert receipt.oom_fallback_triggered is True
    assert receipt.low_vram_attention_used is True
    assert len(client_ok.submitted_prompts) == 2
    assert "MiniMaxLowVRAMAttention" not in {n["class_type"] for n in client_ok.submitted_prompts[0].values()}
    assert "MiniMaxLowVRAMAttention" in {n["class_type"] for n in client_ok.submitted_prompts[1].values()}
    assert client_ok.submitted_keys == ["k-1", "k-1:oom-lowvram"]
    assert len(client_ok.uploads) == 1  # Reuses uploaded reference

    # 2) Double OOM -> stops after 2 attempts (never infinite loop)
    client_2oom = _ScriptedOOMComfyClient(["oom", "oom", "ok"])
    r2 = ComfyH3Executor().execute(client_2oom, req, poll_interval_s=0.05)
    assert r2.status == "failed"
    assert r2.oom_fallback_triggered is True
    assert len(client_2oom.submitted_prompts) == 2

    # 3) Non-OOM error -> no retry
    client_val = _ScriptedOOMComfyClient(["value_error", "ok"])
    r3 = ComfyH3Executor().execute(client_val, req, poll_interval_s=0.05)
    assert r3.status == "failed"
    assert r3.oom_fallback_triggered is False
    assert len(client_val.submitted_prompts) == 1


def test_manual4_worker_persists_two_attempts_on_oom_fallback(tmp_path: Path):
    from tests.manual4.test_p3_unified_h3 import _seed_two_episode_project, _setup_app

    app, settings = _setup_app(tmp_path)
    try:
        with TestClient(app) as client:
            pid, shots = _seed_two_episode_project(client)
            r = client.post(
                f"/api/v1/manual4/projects/{pid}/production/runs",
                headers=LOCAL_ACTION_HEADERS,
                json={
                    "scope_kind": "shot",
                    "scope_target_ids": [shots[0]["id"]],
                    "provider": "comfyui",
                    "resolution_scale": 0.5,
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run"]["id"]
            job_id = r.json()["run"]["jobs"][0]["id"]

        m4_factory = app.state.m4_session_factory
        m4_root = Path(app.state.m4_root)
        with m4_factory() as m4_db:
            run_row = m4_db.get(M4ProductionRun, run_id)
            snap = m4_db.get(M4ExecutionSnapshot, run_row.execution_snapshot_id)
            snap_item = snap.items_json[0]
            run_info = {
                "id": run_row.id,
                "project_id": pid,
                "provider": "comfyui",
                "execution_generation": 1,
                "config": snap.config_json,
            }

        oom_client = _ScriptedOOMComfyClient(["oom", "ok"])
        oom_client.output_bytes = make_test_video_bytes(duration_s=5.0)
        worker_settings = replace(
            settings,
            ffprobe_path="missing-ffprobe",
            ffmpeg_path="missing-ffmpeg",
            h3_low_vram_attention_token_threshold=45000,
            h3_oom_low_vram_retry_enabled=True,
        )
        status = _execute_single_job(
            m4_factory,
            m4_root,
            run_info,
            job_id,
            snap_item,
            worker_id="w-m4-oom",
            comfy_client=oom_client,
            platform_factory=app.state.session_factory,
            settings=worker_settings,
        )
        assert status == "NEEDS_REVIEW"  # ffprobe missing -> NEEDS_REVIEW (not FAILED)
        assert len(oom_client.submitted_prompts) == 2
        assert "MiniMaxLowVRAMAttention" not in {n["class_type"] for n in oom_client.submitted_prompts[0].values()}
        assert "MiniMaxLowVRAMAttention" in {n["class_type"] for n in oom_client.submitted_prompts[1].values()}

        with m4_factory() as m4_db:
            attempts = list(
                m4_db.scalars(
                    select(M4JobAttempt)
                    .where(M4JobAttempt.job_id == job_id)
                    .order_by(M4JobAttempt.attempt_no.asc())
                ).all()
            )
            assert len(attempts) == 2
            assert attempts[0].attempt_no == 1
            assert attempts[0].status == "FAILED"
            assert attempts[0].error_code == "M4_CUDA_OOM_RETRYING"
            assert attempts[1].attempt_no == 2
            assert attempts[1].status == "NEEDS_REVIEW"
            assert attempts[0].prompt_graph_sha256 != attempts[1].prompt_graph_sha256

            events = list(
                m4_db.scalars(select(M4AuditEvent).where(M4AuditEvent.entity_id == job_id)).all()
            )
            assert any(e.event_type == "M4_H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED" for e in events)
            done_event = next(e for e in events if e.event_type == "M4_JOB_NEEDS_REVIEW")
            assert done_event.payload["oom_fallback_triggered"] is True
            assert done_event.payload["low_vram_attention_used"] is True
    finally:
        app.state.engine.dispose()
        app.state.m4_engine.dispose()


# ---------------------------------------------------------------------------
# 5. Mainline pipeline (services.py + provider.py + worker.py) OOM closed-loop
# ---------------------------------------------------------------------------


def _seed_mainline_job(
    client: TestClient,
    project_id: str,
    style_id: str,
    *,
    width: int,
    height: int,
    duration_s: float,
) -> str:
    shot = create_referenced_shot(
        client, project_id, style_id, character="主角", duration=duration_s
    )
    resp = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": shot["id"],
            "provider": "comfyui",
            "strategy": DEFAULT_STRATEGY.model_dump(mode="json"),
            "output": {"width": width, "height": height, "fps": 24},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class _MainlineFakeComfyServer(FakeComfyServer):
    """FakeComfyServer that returns execution_error messages per prompt index."""

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self._prompt_outcome: dict[str, str] = {}
        super().__init__()

    def _history_record(self, prompt_id: str) -> dict[str, Any] | None:
        record = self.prompts.get(prompt_id)
        if record is None:
            return None
        idx = int(prompt_id.split("-")[-1]) - 1 if "-" in prompt_id else 0
        outcome = self.outcomes[idx] if idx < len(self.outcomes) else "ok"
        if outcome == "oom":
            return {
                "prompt": record.get("prompt"),
                "extra_data": record.get("extra_data"),
                "status": {
                    "status_str": "error",
                    "messages": [
                        [
                            "execution_error",
                            {
                                "node_id": "53",
                                "node_type": "SamplerCustomAdvanced",
                                "exception_type": "torch.OutOfMemoryError",
                                "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB.",
                            },
                        ]
                    ],
                },
                "outputs": {},
            }
        if outcome == "value_error":
            return {
                "prompt": record.get("prompt"),
                "extra_data": record.get("extra_data"),
                "status": {
                    "status_str": "error",
                    "messages": [
                        [
                            "execution_error",
                            {
                                "node_id": "10",
                                "node_type": "UNETLoader",
                                "exception_type": "ValueError",
                                "exception_message": "Model checkpoint corrupt.",
                            },
                        ]
                    ],
                },
                "outputs": {},
            }
        return super()._history_record(prompt_id)


def test_mainline_request_threshold_and_oom_fallback_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app, factory, settings = make_studio(tmp_path)
    server = _MainlineFakeComfyServer(["oom", "ok"]).start()
    try:
        server.output_bytes = make_test_video_bytes(duration_s=5.0)
        full_graph = compile_ref2va_workflow(
            image_count=1,
            accelerator_id=DEFAULT_STRATEGY.accelerator_id,
            low_vram_attention=True,
        ).graph
        monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: _compiled_object_info(full_graph))

        with TestClient(app) as client:
            project_id = create_project(client, title="主线LowVRAM门控")
            style = freeze_style(client, project_id)

            # High-watermark job (1344x768, 7s) freezes LowVRAM=True graph hash
            high_job_id = _seed_mainline_job(
                client, project_id, style["id"], width=1344, height=768, duration_s=7.0
            )
            # Low-watermark job (672x384, 5s) freezes LowVRAM=False graph hash
            low_job_id = _seed_mainline_job(
                client, project_id, style["id"], width=672, height=384, duration_s=5.0
            )

            expected_with = compile_ref2va_workflow(
                image_count=1, accelerator_id=DEFAULT_STRATEGY.accelerator_id, low_vram_attention=True
            )
            expected_without = compile_ref2va_workflow(
                image_count=1, accelerator_id=DEFAULT_STRATEGY.accelerator_id, low_vram_attention=False
            )
            with factory() as db:
                high_job = db.get(Job, high_job_id)
                low_job = db.get(Job, low_job_id)
                assert high_job.request["workflow_profile_hash"] == expected_with.graph_sha256
                assert low_job.request["workflow_profile_hash"] == expected_without.graph_sha256
                # Pause high_job so worker runs low_job first
                high_job.paused = True
                db.commit()

            offline = Settings(
                data_dir=settings.data_dir,
                database_url=settings.database_url,
                secret_store_dir=settings.secret_store_dir,
                ffprobe_path="missing-ffprobe",
                ffmpeg_path="missing-ffmpeg",
                render_provider="comfyui",
                offline_simulation=True,
            )
            comfy_client = ComfyUIClient(server.base_url, timeout_s=2)

            # Attempt 1: runs without LowVRAM -> CUDA OOM -> schedules single fallback
            assert run_one(factory, worker_id="w-main-1", settings=offline, comfy_client=comfy_client) == low_job_id
            assert server.prompt_count == 1
            prompt_1 = server.submitted_prompts[0]["prompt"]
            assert "MiniMaxLowVRAMAttention" not in {n["class_type"] for n in prompt_1.values()}

            with factory() as db:
                j = db.get(Job, low_job_id)
                assert j is not None and j.status == "QUEUED"
                assert j.pending_repair_context["attempt_kind"] == "oom_low_vram"

            # Attempt 2: picks up QUEUED job, forces LowVRAM=True, passes is_low_vram_guard_variant, completes!
            assert run_one(factory, worker_id="w-main-2", settings=offline, comfy_client=comfy_client) == low_job_id
            assert server.prompt_count == 2
            prompt_2 = server.submitted_prompts[1]["prompt"]
            assert "MiniMaxLowVRAMAttention" in {n["class_type"] for n in prompt_2.values()}

            res = client.get(f"/api/v1/jobs/{low_job_id}").json()
            assert res["artifact_id"]
            assert res["status"] in {"PASSED", "NEEDS_REVIEW"}
            assert res["failure_code"] != "COMFY_EXECUTION_ERROR"
            assert len(res["attempts"]) == 2
            assert res["attempts"][0]["status"] == "FAILED"
            assert res["attempts"][0]["failure_code"] == "COMFY_CUDA_OOM"
            assert res["attempts"][1]["status"] == "COMPLETED"
            assert res["attempts"][1]["attempt_kind"] == "oom_low_vram"

            with factory() as db:
                events = list(
                    db.scalars(select(AuditEvent).where(AuditEvent.entity_id == low_job_id)).all()
                )
                compiled_events = [e for e in events if e.event_type == "STRATEGY_GRAPH_COMPILED"]
                assert len(compiled_events) == 2
                assert compiled_events[0].payload["low_vram_attention"] is False
                assert compiled_events[0].payload["low_vram_attention_reason"] == "below_threshold"
                assert compiled_events[0].payload["estimated_attention_tokens"] is not None
                assert compiled_events[0].payload["low_vram_attention_threshold"] == 45000
                assert compiled_events[1].payload["low_vram_attention"] is True
                assert compiled_events[1].payload["low_vram_attention_reason"] == "oom_fallback"
                assert any(e.event_type == "H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED" for e in events)
    finally:
        server.stop()
        app.state.engine.dispose()


def test_mainline_double_oom_and_non_oom_do_not_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app, factory, settings = make_studio(tmp_path)
    server = _MainlineFakeComfyServer(["oom", "oom", "value_error"]).start()
    try:
        full_graph = compile_ref2va_workflow(
            image_count=1, accelerator_id=DEFAULT_STRATEGY.accelerator_id, low_vram_attention=True
        ).graph
        monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: _compiled_object_info(full_graph))
        offline = Settings(
            data_dir=settings.data_dir,
            database_url=settings.database_url,
            secret_store_dir=settings.secret_store_dir,
            ffprobe_path="missing-ffprobe",
            ffmpeg_path="missing-ffmpeg",
            render_provider="comfyui",
            offline_simulation=True,
        )
        comfy_client = ComfyUIClient(server.base_url, timeout_s=2)

        with TestClient(app) as client:
            project_id = create_project(client, title="主线不死循环校验")
            style = freeze_style(client, project_id)
            j_2oom = _seed_mainline_job(
                client, project_id, style["id"], width=672, height=384, duration_s=5.0
            )
            j_val = _seed_mainline_job(
                client, project_id, style["id"], width=672, height=384, duration_s=5.0
            )
            with factory.begin() as db:
                db.get(Job, j_val).paused = True

            # Double OOM: Attempt 1 OOM -> Attempt 2 OOM -> NEEDS_REVIEW (no 3rd try)
            run_one(factory, worker_id="w1", settings=offline, comfy_client=comfy_client)
            run_one(factory, worker_id="w2", settings=offline, comfy_client=comfy_client)
            r_2oom = client.get(f"/api/v1/jobs/{j_2oom}").json()
            assert r_2oom["status"] == "NEEDS_REVIEW"
            assert len(r_2oom["attempts"]) == 2
            assert server.prompt_count == 2

            # Non-OOM ValueError: stays NEEDS_REVIEW after 1 attempt
            with factory.begin() as db:
                db.get(Job, j_val).paused = False
            run_one(factory, worker_id="w3", settings=offline, comfy_client=comfy_client)
            r_val = client.get(f"/api/v1/jobs/{j_val}").json()
            assert r_val["status"] == "NEEDS_REVIEW"
            assert len(r_val["attempts"]) == 1
            assert server.prompt_count == 3
    finally:
        server.stop()
        app.state.engine.dispose()

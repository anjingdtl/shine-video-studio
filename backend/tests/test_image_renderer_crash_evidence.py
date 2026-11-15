"""A renderer that dies inside its own native stack must leave evidence.

The picture workshop was blocked for days by a renderer that started, crashed
inside its own torch build and disappeared.  Nothing in the workbench said why:
the start waited ten minutes for a listener that would never come, the
capability report folded the dead renderer into "the graph is not verified",
and the attempt that had already been acknowledged stayed "submitted" for ever.

These tests pin the three repairs:

* the start fails fast with the exit code the kernel reported;
* the evidence names the cause, the launch parameters and the log;
* the attempt reaches a terminal state instead of blocking every later render.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops import image_renderer
from shine.comfyops.models import PreProductionPlan
from shine.materialprep import execution, runtime
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from shine.models import AppSetting
from tests.test_material_image_jobs import object_info_fixture

pytestmark = pytest.mark.integration

#: 0xC0000005 - the Windows access violation code the renderer died with.
ACCESS_VIOLATION = 3221225477

JOB_NAME = "Local\\ShineHandoff-" + "a" * 32


class _DeadGroup:
    """An owned group whose renderer exited before it bound the port."""

    def __init__(self, exit_code=ACCESS_VIOLATION):
        self.exit_code = exit_code

    def exit_code_if_stopped(self):
        return self.exit_code

    def owns(self, pid, birth):
        return False


class _LiveGroup:
    """An owned group that is still running."""

    def exit_code_if_stopped(self):
        return None

    def owns(self, pid, birth):
        return False


def test_a_renderer_that_exits_while_starting_fails_fast_with_its_exit_code(monkeypatch):
    """A crash must not hide behind the listener timeout."""
    monkeypatch.setattr(image_renderer, "windows_port_listeners", lambda port: [])
    with pytest.raises(HTTPException) as failure:
        image_renderer._wait_for_owned_listener(_DeadGroup(), 8188, timeout=5.0)
    assert failure.value.detail["code"] == "IMAGE_RENDERER_EXITED_DURING_STARTUP"
    assert failure.value.detail["exit_code"] == ACCESS_VIOLATION


def test_a_running_renderer_is_never_called_a_crash(monkeypatch):
    """Uncertainty is not a crash: the wait still ends in the named timeout."""
    monkeypatch.setattr(image_renderer, "windows_port_listeners", lambda port: [])
    with pytest.raises(HTTPException) as failure:
        image_renderer._wait_for_owned_listener(_LiveGroup(), 8188, timeout=0.0)
    assert failure.value.detail == "IMAGE_RENDERER_LISTENER_TIMEOUT"


def _record_dead_renderer(factory, instance_id="fake-instance"):
    with factory() as db:
        db.add(AppSetting(
            key=image_renderer.image_startup_key(instance_id),
            value={
                "stage": "RUNNING",
                "instance_id": instance_id,
                "port": 8188,
                "process": {"pid": 4242, "birth": "1", "job_name": JOB_NAME},
                "exit_code": ACCESS_VIOLATION,
                "launch": {
                    "interpreter": "I:/Trae/ComfyUI/venv/Scripts/python.exe",
                    "argv": ["I:/Trae/ComfyUI/venv/Scripts/python.exe",
                             "I:/Trae/shine/scripts/managed_comfy_bootstrap.py"],
                    "cwd": "I:/Trae/ComfyUI",
                    "port": 8188,
                    "interpreter_env": {"PYTHONPATH": "I:/Trae/shine/backend"},
                    "recipe_sha256": "b" * 64,
                    "adapter_sha256": "c" * 64,
                    "pinned_torch": "torch 2.7.1+cu128",
                },
            },
        ))
        db.commit()


def test_the_crash_evidence_names_the_exit_code_and_the_start_parameters(api, monkeypatch):
    """The evidence a later diagnosis needs is kept with the start."""
    _, factory, settings = api
    _record_dead_renderer(factory)
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "GONE")
    with factory() as db:
        evidence = image_renderer.renderer_crash_evidence(db, settings, instance_id="fake-instance")
    assert evidence["crashed"] is True
    assert evidence["code"] == "IMAGE_RENDERER_GONE"
    assert evidence["exit_code"] == ACCESS_VIOLATION
    assert evidence["launch"]["interpreter"].endswith("python.exe")
    assert evidence["launch"]["interpreter_env"]["PYTHONPATH"] == "I:/Trae/shine/backend"
    assert evidence["launch"]["pinned_torch"] == "torch 2.7.1+cu128"
    # The handoff token is an environment *value* and never reaches the record.
    assert "SHINE_COMFY_HANDOFF_TOKEN" not in json.dumps(evidence["launch"])


def test_a_failed_start_keeps_its_own_named_reason(api, monkeypatch):
    """A start that failed with a named reason does not become "gone"."""
    _, factory, settings = api
    with factory() as db:
        db.add(AppSetting(
            key=image_renderer.image_startup_key("fake-instance"),
            value={
                "stage": "FAILED",
                "instance_id": "fake-instance",
                "port": 8188,
                "error_code": "IMAGE_RENDERER_EXITED_DURING_STARTUP",
                "exit_code": ACCESS_VIOLATION,
            },
        ))
        db.commit()
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "STOPPED")
    with factory() as db:
        evidence = image_renderer.renderer_crash_evidence(db, settings, instance_id="fake-instance")
    assert evidence["crashed"] is True
    assert evidence["code"] == "IMAGE_RENDERER_EXITED_DURING_STARTUP"
    assert evidence["exit_code"] == ACCESS_VIOLATION


def test_a_renderer_that_never_started_has_no_crash_to_report(api):
    """Nothing ran, so nothing crashed: the report stays empty."""
    _, factory, settings = api
    with factory() as db:
        evidence = image_renderer.renderer_crash_evidence(db, settings, instance_id="fake-instance")
    assert evidence["crashed"] is False
    assert evidence["code"] is None
    assert image_renderer.renderer_blocker(db, settings, instance_id="fake-instance") is None


def test_the_crash_stays_visible_after_the_record_is_normalized(api, monkeypatch):
    """A dead renderer that was reconciled keeps naming its own cause.

    The record of a crashed renderer is normalized into a stopped one when the
    next start or the release path touches it.  The cause must not disappear
    with the state, or the person is sent back to "the graph is not verified".
    """
    _, factory, settings = api
    with factory() as db:
        db.add(AppSetting(
            key=image_renderer.image_startup_key("fake-instance"),
            value={
                "stage": "STOPPED",
                "instance_id": "fake-instance",
                "port": 8188,
                "stopped_reason": "renderer_gone",
                "exit_code": ACCESS_VIOLATION,
            },
        ))
        db.commit()
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "STOPPED")
    with factory() as db:
        evidence = image_renderer.renderer_crash_evidence(db, settings, instance_id="fake-instance")
    assert evidence["crashed"] is True
    assert evidence["code"] == "IMAGE_RENDERER_GONE"
    assert evidence["exit_code"] == ACCESS_VIOLATION


def test_an_operator_stop_is_not_reported_as_a_crash(api, monkeypatch):
    """A renderer that was stopped on purpose did not crash."""
    _, factory, settings = api
    with factory() as db:
        db.add(AppSetting(
            key=image_renderer.image_startup_key("fake-instance"),
            value={
                "stage": "STOPPED",
                "instance_id": "fake-instance",
                "port": 8188,
                "stopped_reason": "operator_release",
            },
        ))
        db.commit()
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "STOPPED")
    with factory() as db:
        evidence = image_renderer.renderer_crash_evidence(db, settings, instance_id="fake-instance")
    assert evidence["crashed"] is False
    assert evidence["code"] is None


def test_the_evidence_endpoint_is_read_only_and_answers_for_a_dead_renderer(api, monkeypatch):
    """The operator can read the evidence without starting anything."""
    client, factory, settings = api
    _record_dead_renderer(factory)
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "GONE")
    response = client.get("/api/v1/setup/renderer-evidence", params={"instance_id": "fake-instance"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "IMAGE_RENDERER_GONE"
    assert body["exit_code"] == ACCESS_VIOLATION
    assert body["launch"]["interpreter"].endswith("python.exe")


# --- the capability report -------------------------------------------------


def _bind_runtime(settings, tmp_path, base_url):
    """A binding with a verified weight and a matching recipe."""
    from shine.comfyops.deployment import ADAPTER_DIR
    from shine.comfyops.managed_recipe import adapter_identity

    models = tmp_path / "models"
    weights = models / "diffusion_models"
    weights.mkdir(parents=True)
    weight = weights / "qwen.safetensors"
    weight.write_bytes(b"pinned-weight-bytes")
    bootstrap = Path(__file__).resolve().parents[2] / "scripts" / "managed_comfy_bootstrap.py"
    image_renderer.recipe_path(settings).parent.mkdir(parents=True, exist_ok=True)
    image_renderer.recipe_path(settings).write_text(json.dumps({
        "renderer": {
            "instance_id": "fake-instance",
            "python": str(Path(sys.executable).resolve()),
            "root": str(tmp_path),
            "base_directory": str(tmp_path),
            "runtime_data_root": str(tmp_path),
            "bootstrap": str(bootstrap),
            "adapter_dir": str(ADAPTER_DIR),
            "adapter_sha256": adapter_identity(ADAPTER_DIR),
            "gpu_uuid": "GPU-TEST",
            "port": 8188,
            "argv": ["{python}", "{bootstrap}", "--listen", "127.0.0.1", "--port", "{port}"],
            "pinned_files": ["{python}", "{bootstrap}"],
        },
        "reviews": {},
    }), encoding="utf-8")
    config = runtime.ImageRuntimeConfig(
        config_revision=1,
        instance_id="fake-instance",
        base_url=base_url,
        model_root=str(models),
        model_files=[{
            "role": "unet",
            "folder": "diffusion_models",
            "filename": "qwen.safetensors",
            "bytes": weight.stat().st_size,
            "sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
            "required": True,
        }],
    )
    runtime.save_config(settings, config)


def test_the_readiness_report_names_a_dead_renderer_instead_of_an_unverified_graph(api, tmp_path, monkeypatch):
    """A dead renderer has its own cause; it is not "the graph is not verified"."""
    _, factory, settings = api
    _bind_runtime(settings, tmp_path, "http://127.0.0.1:8188")
    _record_dead_renderer(factory)
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "GONE")
    with factory() as db:
        report = runtime.readiness(db, settings, object_info=object_info_fixture())
    codes = [blocker["code"] for blocker in report["blockers"]]
    assert "IMAGE_RENDERER_GONE" in codes
    assert "IMAGE_GRAPH_NOT_VERIFIED" not in codes
    assert report["layer"] == runtime.LAYER_FILES_VERIFIED


def test_a_live_renderer_keeps_reporting_the_graph_layer(api, tmp_path, monkeypatch):
    """A renderer that runs is verified by its own object_info, not by a blocker."""
    _, factory, settings = api
    _bind_runtime(settings, tmp_path, "http://127.0.0.1:8188")
    monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "RUNNING")
    with factory() as db:
        report = runtime.readiness(db, settings, object_info=object_info_fixture())
    assert report["layer"] == runtime.LAYER_GRAPH_VERIFIED
    assert report["blockers"] == []


# --- the attempt that was already acknowledged ------------------------------


class _DyingRendererHandler(BaseHTTPRequestHandler):
    """A renderer that acknowledges the prompt and then dies."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output clean
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - the BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if parsed.path == "/object_info":
            return self._send(self.server.object_info)
        if parsed.path == "/queue":
            return self._send({"queue_running": [], "queue_pending": []})
        if parsed.path == "/system_stats":
            return self._send({
                "system": {"comfyui_version": "0.39.0"},
                "devices": [{"name": "cuda:0", "torch_vram_total": 1 << 40, "torch_vram_free": 1 << 40}],
            })
        if parsed.path.startswith("/history/"):
            # The renderer died here: the answer is a server error, never a result.
            return self._send({"error": "renderer is gone"}, status=500)
        return self._send({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802 - the BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if parsed.path == "/prompt":
            return self._send({"prompt_id": "prompt-1", "number": 1, "node_errors": {}})
        if parsed.path == "/free":
            return self._send({"status": "ok"})
        return self._send({"error": "not found"}, status=404)


class _DyingRenderer:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _DyingRendererHandler)
        self.server.object_info = object_info_fixture()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _seed_material_item(factory, project_id):
    with factory() as db:
        plan_payload = {
            "purpose": "asset_preparation",
            "document_id": "doc",
            "script_hash": "h",
            "scene_preparations": [],
            "requirements": [],
            "visual_direction": None,
            "confirmation": {"confirmation_id": "c" * 64, "confirmation_hash": "d" * 64},
            "manifest": None,
        }
        plan = PreProductionPlan(
            project_id=project_id,
            payload=plan_payload,
            sha256=graph_hash(plan_payload),
            status="CONFIRMED",
            purpose="asset_preparation",
        )
        db.add(plan)
        db.flush()
        run_payload = {"kind": "validation", "rights_id": None}
        run = MaterialRun(
            project_id=project_id,
            plan_id=plan.id,
            confirmation_id="c" * 64,
            confirmation_hash="d" * 64,
            idempotency_key="render-crash",
            request_hash="rq",
            mode="real",
            status="RUNNING",
            payload=run_payload,
            sha256=graph_hash(run_payload),
        )
        db.add(run)
        db.flush()
        item_payload = {
            "family": "character",
            "entity_id": "林知远",
            "brief": {
                "schema_version": "1.0",
                "appearance": "a man in his thirties with short black hair, wearing a long dark wool coat",
                "must_keep": [],
                "avoid": [],
                "questions": [],
            },
            "visual_direction": {},
            "seed": 7,
            "kind": "validation",
        }
        item = MaterialItem(
            run_id=run.id,
            project_id=project_id,
            requirement_id=None,
            generation_revision=1,
            status="QUEUED",
            payload=item_payload,
            sha256=graph_hash(item_payload),
        )
        db.add(item)
        db.flush()
        item_id = item.id
        db.commit()
        return item_id


def test_the_attempt_reaches_a_terminal_state_when_the_renderer_dies(api, tmp_path, monkeypatch):
    """A renderer that dies after /prompt must not leave the attempt "submitted".

    The bytes may or may not exist, so the outcome stays unknown and is never
    resubmitted.  What must not happen is a row that claims the work is still
    running - that row blocks every later render of this item.
    """
    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "崩溃取证"}).json()["id"]
    with _DyingRenderer() as base_url:
        _bind_runtime(settings, tmp_path, base_url)
        _record_dead_renderer(factory)
        monkeypatch.setattr(image_renderer, "managed_process_state", lambda record: "GONE")
        item_id = _seed_material_item(factory, project_id)

        result = execution.execute_item(factory, settings, item_id, "worker-1")

    assert result["status"] == "NEEDS_REVIEW", result
    assert result["code"] == "IMAGE_RENDERER_GONE", result
    with factory() as db:
        item = db.get(MaterialItem, item_id)
        attempt = db.scalar(select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item_id))
    assert attempt is not None
    assert attempt.status == "UNKNOWN"
    assert attempt.external_outcome == "UNKNOWN"
    assert attempt.reconciliation_state == "RECONCILE_REQUIRED"
    assert attempt.error_code == "IMAGE_RENDERER_GONE"
    assert attempt.prompt_id == "prompt-1"
    assert item.status == "NEEDS_REVIEW"
    assert item.error_code == "IMAGE_RENDERER_GONE"

"""The whole image path, against a renderer that speaks the real protocol.

The GPU is not needed to prove that the workbench submits, harvests, verifies
and registers an image: a loopback server that answers /object_info, /prompt,
/history and /view with the pinned shapes exercises exactly the code the real
renderer will.

What this file canNOT prove, and what stays PENDING_VERIFICATION until the
managed instance is really started:

* the real weight files and the real node implementations;
* that the real renderer produces the declared output node;
* the actual memory use and generation time.

The candidate this test creates carries the real ledger shape, so it lives in
the disposable test database only. It is never evidence of a real render.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.assets import fake_image
from shine.comfyops.models import PreProductionPlan, RightsRecord
from shine.materialprep import execution, runtime
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from tests.test_material_image_jobs import object_info_fixture

IMAGE_BYTES = fake_image("material-e2e")


class _FakeRendererHandler(BaseHTTPRequestHandler):
    """The ComfyUI HTTP surface the image path actually uses."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output clean
        pass

    def _send(self, payload, status=200, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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
            prompt_id = parsed.path.split("/history/", 1)[1]
            return self._send({
                prompt_id: {
                    "outputs": {"461": {"images": [{"filename": "material.png", "subfolder": "", "type": "output"}]}},
                    "status": {"status_str": "success"},
                }
            })
        if parsed.path == "/view":
            return self._send(self.server.image_bytes, content_type="image/png")
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


class FakeRenderer:
    """A loopback renderer stand-in with the real wire shapes."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRendererHandler)
        self.server.object_info = object_info_fixture()
        self.server.image_bytes = IMAGE_BYTES
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def test_the_image_path_submits_harvests_and_registers_a_candidate(api, tmp_path):
    """Bind -> claim -> submit -> poll -> harvest -> verify -> register.

    This is the code the real renderer will run. Only the renderer is a
    stand-in; the workbench side is the production path.
    """
    from shine.comfyops.models import CandidateAsset

    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "出图链路"}).json()["id"]

    with FakeRenderer() as base_url:
        models = tmp_path / "models"
        weights = models / "diffusion_models"
        weights.mkdir(parents=True)
        weight = weights / "qwen.safetensors"
        weight.write_bytes(b"pinned-weight-bytes")
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
            rights_payload = {"scope": "asset", "license_id": "local-research", "purpose": "material preparation"}
            rights = RightsRecord(
                project_id=project_id,
                status="APPROVED",
                payload=rights_payload,
                sha256=graph_hash(rights_payload),
            )
            db.add(rights)
            db.flush()
            run_payload = {"kind": "validation", "rights_id": rights.id}
            run = MaterialRun(
                project_id=project_id,
                plan_id=plan.id,
                confirmation_id="c" * 64,
                confirmation_hash="d" * 64,
                idempotency_key="render-e2e",
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

        # The live node check runs against the stand-in's declared nodes.
        with factory() as db:
            report = runtime.readiness(db, settings, object_info=object_info_fixture())
        assert report["layer"] == runtime.LAYER_GRAPH_VERIFIED, report
        assert report["families"]["character"]["can_generate"] is False

        result = execution.execute_item(factory, settings, item_id, "worker-1")

        assert result["status"] == "CANDIDATE_READY", result
        with factory() as db:
            row = db.get(MaterialItem, item_id)
            assert row.status == "CANDIDATE_READY"
            attempt = db.scalar(select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item_id))
            assert attempt is not None
            assert attempt.prompt_id == "prompt-1"
            assert attempt.external_outcome == "SUCCEEDED"
            assert attempt.content_sha256 == hashlib.sha256(IMAGE_BYTES).hexdigest()
            candidate = db.get(CandidateAsset, row.payload["candidate_id"])
            assert candidate is not None
            assert candidate.payload["content_sha256"] == hashlib.sha256(IMAGE_BYTES).hexdigest()
            assert candidate.payload["qc"]["technical"] == "pass"
            # The fourth readiness layer is derived from the ledger, not a flag.
            report = runtime.readiness(db, settings, object_info=object_info_fixture())
        assert report["families"]["character"]["can_generate"] is True, report

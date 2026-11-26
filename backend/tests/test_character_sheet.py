from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from shine.asset_center import character_sheet as cs
from shine.asset_center import execution as asset_execution
from shine.asset_center.models import (
    LibraryAsset,
    LibraryRenderTask,
    LibraryRights,
    LibrarySourceReference,
    LibraryTaskAttempt,
    LibraryVersion,
)
from shine.comfyops import loras
from shine.comfyops.models import StyleLoraAsset
from shine.materialprep import qwen_workflow
from shine.materialprep import runtime as image_runtime
from shine.schemas import TechnicalCheck
from tests.conftest import make_studio
from tests.test_style_lora_assembly import _make_valid_safetensors

pytestmark = pytest.mark.integration


def _make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), rgb).save(buf, format="PNG")
    return buf.getvalue()


def _install_multiple_angles_lora(factory, settings, tmp_path) -> StyleLoraAsset:
    lora_path = tmp_path / "angles_v2.safetensors"
    _make_valid_safetensors(lora_path, seed_tag=b"mva")
    actual_sha = hashlib.sha256(lora_path.read_bytes()).hexdigest()
    # Patch the module constant in tests via monkeypatch when needed, OR register directly at pinned hash.
    with factory.begin() as db:
        row = StyleLoraAsset(
            id=cs.MULTIPLE_ANGLES_LORA_ID,
            display_name="Qwen 多视角镜头",
            filename=lora_path.name,
            storage_path=str(lora_path.resolve()),
            sha256=actual_sha,
            file_size_bytes=lora_path.stat().st_size,
            default_strength=0.8,
            trigger_word="<mva>",
            negative_trigger_word="",
            trigger_position="prefix",
            base_model=loras.IMAGE_LORA_BASE,
            origin="discovered",
        )
        db.add(row)
        db.flush()
    return row


def _seed_i2i_validated(factory, fingerprint: str) -> None:
    from shine.asset_center.contracts import qwen_edit_adapter_hash

    img = _make_png(512, 512, (80, 90, 100))
    digest = hashlib.sha256(img).hexdigest()
    graph_sha = "a" * 64
    with factory.begin() as db:
        rights = LibraryRights(
            source_type="generated",
            license_kind="QWEN_RESEARCH_ONLY",
            scope="asset",
            statement="validation fixture",
            subject="validation",
            allowed_project_ids=["*"],
            permitted_uses=["research"],
            commercial_allowed=False,
            status="APPROVED",
            recorded_actor="test",
            approved_by="test",
        )
        asset = LibraryAsset(title="I2I验证", kind="image", category="character", created_by="test")
        db.add_all([rights, asset])
        db.flush()
        ver = LibraryVersion(
            library_asset_id=asset.id,
            ordinal=1,
            filename="i2i-val.png",
            kind="image",
            content_type="image/png",
            content_sha256=digest,
            storage_path="unused/i2i-val.png",
            byte_size=len(img),
            width=512,
            height=512,
            source_mode="I2I",
            rights_id=rights.id,
            provenance={},
            qc={"technical": "pass", "sha256": digest},
            status="READY_FOR_REVIEW",
            is_mock=False,
            created_by="test",
        )
        db.add(ver)
        db.flush()
        task = LibraryRenderTask(
            idempotency_key="val-i2i-seed:1",
            request_hash="b" * 64,
            kind="image",
            mode="I2I",
            payload={
                "validation_only": True,
                "validation_adapter_hash": qwen_edit_adapter_hash("I2I"),
            },
            workflow_revision_hash=graph_sha,
            runtime_fingerprint=fingerprint,
            status="CANDIDATE_READY",
            external_outcome="SUCCEEDED",
            result_version_id=ver.id,
        )
        db.add(task)
        db.flush()
        db.add(
            LibraryTaskAttempt(
                task_id=task.id,
                attempt_no=1,
                snapshot_hash="c" * 64,
                submit_stage="OUTPUT_STORED",
                status="SUCCEEDED",
                external_outcome="SUCCEEDED",
                output_sha256=digest,
                payload={
                    "renderer_owned": True,
                    "adapter_hash": qwen_edit_adapter_hash("I2I"),
                    "graph_sha256": graph_sha,
                },
            )
        )


def _upload_character_ref(client: TestClient, *, width: int = 1024, height: int = 1024) -> dict:
    raw = _make_png(width, height, (120, 140, 160))
    resp = client.post(
        "/api/v1/asset-center/uploads",
        data={
            "asset_kind": "image",
            "purpose": "reference",
            "allowed_project_ids": '["project-1"]',
            "permitted_uses": '["research"]',
        },
        files={"file": ("hero.png", raw, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fake_object_info(lora_filename: str) -> dict:
    return {
        "UNETLoader": {
            "input": {"required": {"unet_name": [[qwen_workflow.UNET_FILE]], "weight_dtype": [["default"]]}}
        },
        "CLIPLoader": {
            "input": {
                "required": {
                    "clip_name": [[qwen_workflow.CLIP_FILE]],
                    "type": [["qwen_image"]],
                    "device": [["default"]],
                }
            }
        },
        "VAELoader": {"input": {"required": {"vae_name": [[qwen_workflow.VAE_FILE]]}}},
        "LoraLoaderModelOnly": {
            "input": {
                "required": {
                    "lora_name": [[lora_filename]],
                    "strength_model": ["FLOAT"],
                    "model": ["MODEL"],
                }
            }
        },
        "QwenImage21Cache": {
            "input": {"required": {"model": ["MODEL"], "device": [["auto"]], "dtype": [["default"]]}}
        },
        "TextEncodeQwenImage21": {
            "input": {
                "required": {
                    "clip": ["CLIP"],
                    "vae": ["VAE"],
                    "prompt": ["STRING"],
                    "negative_prompt": ["STRING"],
                    "resolution": ["INT"],
                },
                "optional": {"images": ["COMFY_AUTOGROW_V3"]},
            }
        },
        "KSampler": {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "positive": ["CONDITIONING"],
                    "negative": ["CONDITIONING"],
                    "latent_image": ["LATENT"],
                    "seed": ["INT"],
                    "steps": ["INT"],
                    "cfg": ["FLOAT"],
                    "sampler_name": [["euler"]],
                    "scheduler": [["simple"]],
                    "denoise": ["FLOAT"],
                }
            }
        },
        "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
        "ImageScale": {
            "input": {
                "required": {
                    "image": ["IMAGE"],
                    "upscale_method": [["lanczos"]],
                    "width": ["INT"],
                    "height": ["INT"],
                    "crop": [["center"]],
                }
            }
        },
        "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING"]}}},
        "LoadImage": {"input": {"required": {"image": ["STRING"]}}},
    }


def test_deterministic_pillow_compositor_layout_and_proportional_scaling():
    closeup_front = _make_png(736, 928, (220, 120, 40))
    closeup_3_4 = _make_png(736, 928, (200, 50, 50))
    front = _make_png(576, 1280, (50, 200, 50))
    side = _make_png(576, 1280, (50, 50, 200))
    back = _make_png(576, 1280, (200, 200, 50))

    composed = cs.compose_character_sheet(
        closeup_front_bytes=closeup_front,
        closeup_3_4_bytes=closeup_3_4,
        front_bytes=front,
        side_bytes=side,
        back_bytes=back,
    )
    assert composed["fullbody_triview_size"] == {"width": 1872, "height": 1376}
    assert composed["composite_sheet_size"] == {"width": 3584, "height": 2016}
    assert 3584 / 2016 == pytest.approx(16 / 9)
    assert composed["layout"]["version"] == "character-sheet-16x9-dual-closeup-triview-v2"
    assert composed["layout"]["aspect_ratio"] == "16:9"

    with Image.open(io.BytesIO(composed["fullbody_triview_bytes"])) as tri:
        assert tri.size == (1872, 1376)
        assert tri.getpixel((10, 10)) == cs.CHARACTER_SHEET_BG_RGB
        assert tri.getpixel((48 + 288, 48 + 640)) == (50, 200, 50)
        assert tri.getpixel((48 + 576 + 12, 640)) == cs.CHARACTER_SHEET_BG_RGB  # gap
        assert tri.getpixel((48 + 576 + 24 + 288, 48 + 640)) == (50, 50, 200)
        assert tri.getpixel((48 + 576 + 24 + 576 + 24 + 288, 48 + 640)) == (200, 200, 50)

    with Image.open(io.BytesIO(composed["composite_sheet_bytes"])) as comp:
        assert comp.size == (3584, 2016)
        assert comp.getpixel((0, 0)) == cs.CHARACTER_SHEET_BG_RGB
        assert comp.getpixel((64 + 368, 64 + 464)) == (220, 120, 40)      # top-left closeup_front
        assert comp.getpixel((64 + 368, 64 + 928 + 16)) == cs.CHARACTER_SHEET_BG_RGB  # vertical closeup gap
        assert comp.getpixel((64 + 368, 1024 + 464)) == (200, 50, 50)      # bottom-left closeup_3_4
        assert comp.getpixel((64 + 736 + 32, 1000)) == cs.CHARACTER_SHEET_BG_RGB      # section gap
        assert comp.getpixel((864 + 432, 64 + 944)) == (50, 200, 50)       # front
        assert comp.getpixel((1760 + 432, 64 + 944)) == (50, 50, 200)      # side
        assert comp.getpixel((2656 + 432, 64 + 944)) == (200, 200, 50)     # back

    # Non-standard input heights must scale proportionally without stretching or cropping.
    wide_front = _make_png(400, 500, (40, 50, 60))
    scaled = cs.compose_character_sheet(
        closeup_front_bytes=closeup_front,
        closeup_3_4_bytes=closeup_3_4,
        front_bytes=wide_front,
        side_bytes=side,
        back_bytes=back,
    )
    fb_box = scaled["layout"]["composite_boxes"]["front"]
    assert fb_box["width"] == 864
    assert fb_box["height"] == round(864 * 500 / 400)
    assert cs.step_dimensions(576, 1280, target_width=576, target_height=1280) == (576, 1280)
    assert cs.step_dimensions(1024, 1024, target_width=576, target_height=1280) == (576, 1280)


def test_missing_lora_blocks_without_degradation_and_rejects_conflicts(tmp_path, monkeypatch):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    fingerprint = "fp-char-sheet-test"
    _seed_i2i_validated(factory, fingerprint)
    monkeypatch.setattr(
        image_runtime,
        "load_config",
        lambda _settings: SimpleNamespace(base_url="", instance_id=None),
    )
    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": fingerprint,
            "graph_sha256": "g" * 64,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )

    caps = client.get("/api/v1/asset-center/capabilities")
    assert caps.status_code == 200
    cs_cap = caps.json()["image_capabilities"]["QWEN_IMAGE_CHARACTER_SHEET"]
    assert cs_cap["can_generate"] is False
    assert cs_cap["lora_ready"] is False
    assert cs_cap["lora_status"] == "NOT_INSTALLED"
    assert "制作环境" in cs_cap["setup_hint"]

    media = _upload_character_ref(client)
    base_body = {
        "idempotency_key": "cs-missing-lora-001",
        "mode": "I2I",
        "category": "character",
        "character_sheet": True,
        "character_sheet_note": "dark boots",
        "references": [{"media_id": media["id"], "order": 0, "role": "subject"}],
    }
    missing_resp = client.post("/api/v1/asset-center/image-jobs", json=base_body)
    assert missing_resp.status_code == 409
    assert missing_resp.json()["detail"]["code"] == "CHARACTER_SHEET_LORA_REQUIRED"

    # Parameter conflicts are explicitly refused.
    bad_mode = client.post("/api/v1/asset-center/image-jobs", json={**base_body, "mode": "T2I"})
    assert bad_mode.status_code == 422
    assert bad_mode.json()["detail"]["code"] == "CHARACTER_SHEET_REQUIRES_I2I"

    bad_preset = client.post(
        "/api/v1/asset-center/image-jobs",
        json={**base_body, "style_preset": "cinematic_photorealism"},
    )
    assert bad_preset.status_code == 422
    assert bad_preset.json()["detail"]["code"] == "CHARACTER_SHEET_STYLE_CONFLICT"

    bad_note = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "cs-note-without-mode",
            "mode": "I2I",
            "prompt": "keep look",
            "character_sheet_note": "boots",
            "references": [{"media_id": media["id"], "order": 0, "role": "canvas"}],
        },
    )
    assert bad_note.status_code == 422
    assert bad_note.json()["detail"]["code"] == "CHARACTER_SHEET_NOTE_REQUIRES_MODE"


def test_character_sheet_e2e_single_lease_4_steps_rights_and_delete_protection(tmp_path, monkeypatch):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    fingerprint = "fp-char-sheet-e2e"
    _seed_i2i_validated(factory, fingerprint)
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)
    monkeypatch.setattr(
        image_runtime,
        "load_config",
        lambda _settings: SimpleNamespace(base_url="", instance_id=None),
    )
    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": fingerprint,
            "graph_sha256": "g" * 64,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )

    caps = client.get("/api/v1/asset-center/capabilities").json()
    cs_cap = caps["image_capabilities"]["QWEN_IMAGE_CHARACTER_SHEET"]
    assert cs_cap["can_generate"] is True
    assert cs_cap["lora_ready"] is True

    media = _upload_character_ref(client, width=1024, height=1024)
    req_body = {
        "idempotency_key": "cs-e2e-task-0001",
        "title": "许知微定妆照",
        "mode": "I2I",
        "category": "character",
        "character_sheet": True,
        "character_sheet_note": "black leather boots and dark trousers",
        "references": [{"media_id": media["id"], "order": 0, "role": "subject"}],
    }
    resp = client.post("/api/v1/asset-center/image-jobs", json=req_body)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["items"][0]["id"]

    # Idempotent replay returns same task; active task protects LoRA from deletion.
    replay = client.post("/api/v1/asset-center/image-jobs", json=req_body)
    assert replay.status_code == 202
    assert replay.json()["items"][0]["id"] == task_id

    del_resp = client.delete(f"/api/v1/loras/{lora_row.id}", headers={"X-Shine-Local-Action": "1"})
    assert del_resp.status_code == 409
    assert del_resp.json()["detail"]["code"] == "LORA_IN_USE"

    # Mock single GPU lease, renderer, ComfyUIClient, and FFmpeg full-decode QC.
    lease_entries: list[str] = []
    submitted_graphs: list[dict] = []
    uploaded_blobs: list[tuple[str, bytes]] = []
    step_outputs = {
        1: _make_png(736, 928, (220, 120, 40)),   # closeup_front
        2: _make_png(736, 928, (210, 80, 80)),    # closeup_3_4
        3: _make_png(576, 1280, (80, 210, 80)),   # fullbody_front
        4: _make_png(576, 1280, (80, 80, 210)),   # fullbody_side
        5: _make_png(576, 1280, (210, 210, 80)),  # fullbody_back
    }

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        lease_entries.append("lease-1")
        yield "lease-1"

    class FakeComfyClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return _fake_object_info(lora_row.filename)

        def upload_image(self, filename, content, **_kwargs):
            uploaded_blobs.append((filename, content))
            return {"name": filename}

        def queue_prompt(self, graph, **_kwargs):
            idx = len(submitted_graphs) + 1
            submitted_graphs.append(graph)
            return {"prompt_id": f"prompt-cs-{idx}"}

        def history(self, prompt_id):
            idx = int(prompt_id.rsplit("-", 1)[-1])
            return {
                prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"461": {"images": [{"filename": f"out-{idx}.png", "subfolder": "", "type": "output"}]}},
                }
            }

        def view(self, filename, **_kwargs):
            idx = int(filename.removeprefix("out-").removesuffix(".png"))
            return step_outputs[idx]

        def free_models(self):
            return None

    monkeypatch.setattr(asset_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(asset_execution, "ComfyUIClient", FakeComfyClient)
    monkeypatch.setattr(
        asset_execution.image_execution,
        "_ensure_renderer",
        lambda *_args, **_kwargs: {"renderer_owned": True, "renderer_instance_id": "inst-1"},
    )
    monkeypatch.setattr(
        asset_execution.image_execution,
        "resolve_runtime",
        lambda *_args, **_kwargs: {
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": fingerprint,
            "config": {},
            "object_info": FakeComfyClient("http://127.0.0.1:8188").object_info(),
        },
    )
    monkeypatch.setattr(asset_execution, "_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "shine.quality.technical._full_decode_check",
        lambda *_args, **_kwargs: TechnicalCheck(name="decode", status="pass", detail="decoded_ok"),
    )

    processed = asset_execution.run_one_pending_job(factory, settings, "worker-cs-test")
    assert processed == 1
    assert lease_entries == ["lease-1"]  # single GPU lease across all 5 steps
    assert len(submitted_graphs) == 5

    # Verify prompt syntax and LoRA chain 451 -> 4511 -> 469.
    g1, g2, g3, g4, g5 = submitted_graphs
    assert "4511" not in g1  # closeup_front uses base model without LoRA
    assert g1["469"]["inputs"]["model"] == ["451", 0]
    assert "front view head-and-shoulders" in g1["474"]["inputs"]["prompt"]

    assert g2["474"]["inputs"]["prompt"].startswith("<mva> front-right quarter view, eye-level shot close-up")
    assert "three-quarter side angle" in g2["474"]["inputs"]["prompt"]
    assert "straight front view" in g2["474"]["inputs"]["negative_prompt"]
    assert g2["4511"]["class_type"] == "LoraLoaderModelOnly"
    assert g2["4511"]["inputs"]["model"] == ["451", 0]
    assert g2["4511"]["inputs"]["strength_model"] == 0.8
    assert g2["469"]["inputs"]["model"] == ["4511", 0]
    assert g2["458"]["inputs"]["steps"] == 36
    assert g2["458"]["inputs"]["cfg"] == 1.0
    assert "35mm" not in g2["474"]["inputs"]["prompt"]

    # Step 3 (fullbody_front) uses original ref, NO LoRA, and injects the note.
    assert "4511" not in g3
    assert g3["469"]["inputs"]["model"] == ["451", 0]
    assert "straight front view" not in g3["474"]["inputs"]["negative_prompt"]
    assert "black leather boots and dark trousers" in g3["474"]["inputs"]["prompt"]

    # Step 4 & 5 use step 3's fullbody_front bytes as <image1>, with <mva> prefix and LoRA chain.
    front_sha = hashlib.sha256(step_outputs[3]).hexdigest()
    assert g4["700"]["inputs"]["image"] == f"{front_sha}.png"
    assert g5["700"]["inputs"]["image"] == f"{front_sha}.png"
    assert g4["474"]["inputs"]["prompt"].startswith("<mva> right side view, eye-level shot")
    assert g5["474"]["inputs"]["prompt"].startswith("<mva> back view, eye-level shot")
    assert g4["4511"]["inputs"]["model"] == ["451", 0]
    assert g5["469"]["inputs"]["model"] == ["4511", 0]

    # Inspect persisted task & 4 versions.
    task_json = client.get(f"/api/v1/asset-center/image-jobs/{task_id}").json()
    assert task_json["status"] == "CANDIDATE_READY"
    assert len(task_json["attempts"]) == 5
    cs_info = task_json["character_sheet"]
    assert cs_info["enabled"] is True
    assert cs_info["outputs"]["closeup_front"]["width"] == 736
    assert cs_info["outputs"]["closeup_3_4"]["width"] == 736
    assert cs_info["outputs"]["fullbody_triview"]["width"] == 1872
    assert cs_info["outputs"]["composite_sheet"]["width"] == 3584
    assert cs_info["outputs"]["composite_sheet"]["height"] == 2016

    with factory() as db:
        comp_ver = db.get(LibraryVersion, task_json["result_version_id"])
        assert comp_ver is not None
        assert comp_ver.source_mode == "CHARACTER_SHEET"
        assert comp_ver.status == "READY_FOR_REVIEW"
        prov_cs = comp_ver.provenance["character_sheet"]
        assert prov_cs["layout_version"] == cs.CHARACTER_SHEET_LAYOUT_VERSION
        assert prov_cs["source_reference_sha256"] == media["sha256"]
        assert prov_cs["lora"]["sha256"] == lora_row.sha256
        assert [s["prompt_id"] for s in prov_cs["steps"]] == [
            "prompt-cs-1", "prompt-cs-2", "prompt-cs-3", "prompt-cs-4", "prompt-cs-5"
        ]
        # Inherited rights enforce project-1 scope and non-commercial research.
        comp_rights = db.get(LibraryRights, comp_ver.rights_id)
        assert comp_rights.allowed_project_ids == ["project-1"]
        assert comp_rights.commercial_allowed is False
        refs = db.scalars(select(LibrarySourceReference).where(LibrarySourceReference.version_id == comp_ver.id)).all()
        assert len(refs) == 1 and refs[0].media_id == media["id"]


def test_character_sheet_failure_does_not_replay_and_reconcile_refuses_partial_adoption(tmp_path, monkeypatch):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    fingerprint = "fp-char-sheet-fail"
    _seed_i2i_validated(factory, fingerprint)
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)
    monkeypatch.setattr(
        image_runtime,
        "load_config",
        lambda _settings: SimpleNamespace(base_url="http://127.0.0.1:8188", instance_id="inst-1"),
    )
    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": fingerprint,
            "graph_sha256": "g" * 64,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )

    media = _upload_character_ref(client)
    resp = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "cs-fail-no-replay-001",
            "mode": "I2I",
            "category": "character",
            "character_sheet": True,
            "references": [{"media_id": media["id"], "order": 0, "role": "subject"}],
        },
    )
    assert resp.status_code == 202
    task_id = resp.json()["items"][0]["id"]

    submitted = []

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        yield "lease-fail"

    class FailingStep2Client:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return _fake_object_info(lora_row.filename)

        def upload_image(self, filename, _content, **_kwargs):
            return {"name": filename}

        def queue_prompt(self, _graph, **_kwargs):
            idx = len(submitted) + 1
            submitted.append(idx)
            if idx == 2:
                raise TimeoutError("read timeout during submit")
            return {"prompt_id": f"prompt-part-{idx}"}

        def history(self, prompt_id):
            if prompt_id == "prompt-part-1":
                return {
                    prompt_id: {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"461": {"images": [{"filename": "out-1.png", "subfolder": "", "type": "output"}]}},
                    }
                }
            return {
                prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"461": {"images": [{"filename": "out-2.png", "subfolder": "", "type": "output"}]}},
                }
            }

        def view(self, *_args, **_kwargs):
            return _make_png(736, 928, (120, 120, 120))

        def free_models(self):
            return None

    monkeypatch.setattr(asset_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(asset_execution, "ComfyUIClient", FailingStep2Client)
    monkeypatch.setattr("shine.comfy.client.ComfyUIClient", FailingStep2Client)
    monkeypatch.setattr(
        asset_execution.image_execution,
        "_ensure_renderer",
        lambda *_args, **_kwargs: {"renderer_owned": True, "renderer_instance_id": "inst-1"},
    )
    monkeypatch.setattr(
        asset_execution.image_execution,
        "resolve_runtime",
        lambda *_args, **_kwargs: {
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": fingerprint,
            "config": {},
            "object_info": FailingStep2Client("http://127.0.0.1:8188").object_info(),
        },
    )
    monkeypatch.setattr(asset_execution, "_POLL_INTERVAL_S", 0.0)

    asset_execution.run_one_pending_job(factory, settings, "worker-cs-fail")
    assert submitted == [1, 2]  # stopped immediately on step 2 timeout

    task_json = client.get(f"/api/v1/asset-center/image-jobs/{task_id}").json()
    assert task_json["status"] == "UNKNOWN"
    assert task_json["can_new_attempt"] is False
    assert len(task_json["attempts"]) == 2
    assert task_json["attempts"][0]["status"] == "SUCCEEDED"
    assert task_json["attempts"][1]["status"] == "UNKNOWN"

    # Simulate an operator finding external_prompt_id for step 2 and reconciling:
    with factory.begin() as db:
        task_row = db.get(LibraryRenderTask, task_id)
        task_row.external_prompt_id = "prompt-part-2"
        att2 = db.scalar(
            select(LibraryTaskAttempt).where(
                LibraryTaskAttempt.task_id == task_id,
                LibraryTaskAttempt.attempt_no == 2,
            )
        )
        att2.external_prompt_id = "prompt-part-2"

    rec = client.post(f"/api/v1/asset-center/image-jobs/{task_id}/reconcile", json={"evidence": "check step 2"})
    assert rec.status_code == 200, rec.text
    assert rec.json()["reconciliation"] == "CHARACTER_SHEET_STEP_RECONCILED_INCOMPLETE"
    assert rec.json()["result_version_id"] is None  # never adopts a single step as final composite


def test_character_sheet_blocks_revoked_rights_and_supports_promoted_version_reference(tmp_path, monkeypatch):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    fingerprint = "fp-char-sheet-rights"
    _seed_i2i_validated(factory, fingerprint)
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)
    monkeypatch.setattr(
        image_runtime,
        "load_config",
        lambda _settings: SimpleNamespace(base_url="", instance_id=None),
    )
    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": fingerprint,
            "graph_sha256": "g" * 64,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )

    media = _upload_character_ref(client)
    created_asset = client.post(
        "/api/v1/asset-center/assets",
        json={"title": "南宋档案官", "kind": "image", "category": "character", "media_id": media["id"]},
    ).json()
    ver_id = created_asset["versions"][0]["id"]
    assert client.post(f"/api/v1/asset-center/versions/{ver_id}/qc").status_code == 200
    grant = client.post(
        f"/api/v1/asset-center/versions/{ver_id}/grant",
        headers={"X-Shine-Local-Action": "1"},
    ).json()
    promoted = client.post(
        f"/api/v1/asset-center/versions/{ver_id}/promote",
        headers={"X-Shine-Local-Action": "1"},
        json={"grant_id": grant["grant_id"], "snapshot": grant["snapshot"], "evidence": "promoted for test"},
    )
    assert promoted.status_code == 200

    # Promoted version as character-sheet reference succeeds.
    queued = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "cs-promoted-ref-001",
            "mode": "I2I",
            "category": "character",
            "character_sheet": True,
            "target_asset_id": created_asset["id"],
            "parent_version_id": ver_id,
            "expected_asset_state_version": created_asset["state_version"] + 1,
            "references": [{"version_id": ver_id, "order": 0, "role": "subject"}],
        },
    )
    assert queued.status_code == 202, queued.text

    # Revoking source rights blocks subsequent submissions.
    revoked = client.post(
        f"/api/v1/asset-center/rights/{media['rights_id']}/revoke",
        params={"evidence": "rights revoked"},
        headers={"X-Shine-Local-Action": "1"},
    )
    assert revoked.status_code == 200
    blocked = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "cs-revoked-ref-002",
            "mode": "I2I",
            "category": "character",
            "character_sheet": True,
            "references": [{"version_id": ver_id, "order": 0, "role": "subject"}],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "LIBRARY_RIGHTS_NOT_APPROVED"


def _seed_material_batch(client: TestClient, factory, settings, *, families=("character", "prop")):
    from shine.comfyops.models import AssetRequirement, CandidateAsset, PreProductionPlan
    from shine.comfyops.preproduction import current_material_plan
    from shine.materialprep import execution as mat_execution
    from shine.materialprep import qwen_workflow as mat_qwen
    from shine.materialprep import runtime as mat_runtime
    from shine.materialprep import service as mat_service
    from shine.materialprep.models import MaterialImageAttempt, MaterialItem
    from tests.conftest import stamp_brief_basis
    from tests.test_material_image_jobs import object_info_fixture

    project_id = client.post("/api/v1/projects", json={"title": "视频定妆照流水线"}).json()["id"]
    with factory.begin() as db:
        rights_id = None
        plan_payload = {
            "purpose": "asset_preparation",
            "document_id": None,
            "script_hash": "h",
            "scene_preparations": [],
            "requirements": [],
            "visual_direction": None,
            "confirmation": None,
            "manifest": None,
        }
        plan = PreProductionPlan(
            project_id=project_id,
            payload=plan_payload,
            sha256="p" * 64,
            status="CONFIRMED",
            purpose="asset_preparation",
        )
        db.add(plan)
        db.flush()

        reqs = {}
        briefs = {}
        for fam in families:
            entity = "燕三娘" if fam == "character" else "碧玉簪" if fam == "prop" else "竹林客栈"
            desc = (
                "Ming-dynasty wuxia heroine wearing dark teal coarse woven martial robe and black boots"
                if fam == "character"
                else "carved green jade hairpin"
            )
            req = AssetRequirement(
                project_id=project_id,
                plan_id=plan.id,
                payload={"family": fam, "entity_id": entity, "description": desc, "strategy": "generate"},
                sha256=fam[0] * 64,
                status="CONFIRMED",
            )
            db.add(req)
            db.flush()
            reqs[fam] = req.id
            briefs[req.id] = {
                "schema_version": "1.0",
                "appearance": desc,
                "must_keep": [],
                "avoid": [],
                "questions": [],
            }
        plan.payload = {
            **plan_payload,
            "confirmation": {"confirmation_id": "conf-cs-1", "confirmation_hash": "conf-hash-1"},
        }
        batch = mat_service.create_batch(
            db,
            project_id,
            confirmation_id="conf-cs-1",
            confirmation_hash="conf-hash-1",
            requirement_ids=list(reqs.values()),
            idempotency_key="mat-cs-batch-1",
            rights_id=rights_id,
            briefs=briefs,
            settings=settings,
        )
    for item_id in batch["items"]:
        stamp_brief_basis(factory, item_id)
    return project_id, batch["items"], {
        "CandidateAsset": CandidateAsset,
        "MaterialImageAttempt": MaterialImageAttempt,
        "MaterialItem": MaterialItem,
        "mat_execution": mat_execution,
        "mat_qwen": mat_qwen,
        "mat_runtime": mat_runtime,
        "object_info_fixture": object_info_fixture,
        "current_material_plan": current_material_plan,
    }


def test_materialprep_character_runs_t2i_to_16x9_character_sheet_and_keeps_prop_single_t2i(
    tmp_path, monkeypatch
):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)

    project_id, item_ids, ctx = _seed_material_batch(client, factory, settings, families=("character", "prop"))
    MaterialItem = ctx["MaterialItem"]
    MaterialImageAttempt = ctx["MaterialImageAttempt"]
    CandidateAsset = ctx["CandidateAsset"]
    mat_execution = ctx["mat_execution"]
    mat_runtime = ctx["mat_runtime"]
    object_info_fixture = ctx["object_info_fixture"]

    with factory() as db:
        items_by_fam = {
            (db.get(MaterialItem, iid).payload or {}).get("family"): db.get(MaterialItem, iid) for iid in item_ids
        }
    char_item = items_by_fam["character"]
    prop_item = items_by_fam["prop"]
    assert char_item.payload["character_sheet_mode"] is True
    assert prop_item.payload.get("character_sheet_mode") is None

    # Active character material item blocks deleting multiple-angles LoRA (One Adjudicator).
    del_resp = client.delete(f"/api/v1/loras/{lora_row.id}", headers={"X-Shine-Local-Action": "1"})
    assert del_resp.status_code == 409
    assert del_resp.json()["detail"]["code"] == "LORA_IN_USE"

    lease_calls: list[str] = []
    submitted: list[tuple[str, dict]] = []
    prompt_outputs = {
        "mat-prompt-1": _make_png(1024, 1024, (140, 140, 140)),  # T2I seed portrait
        "mat-prompt-2": _make_png(736, 928, (220, 120, 40)),     # closeup_front
        "mat-prompt-3": _make_png(736, 928, (200, 50, 50)),      # closeup_3_4
        "mat-prompt-4": _make_png(576, 1280, (50, 200, 50)),     # fullbody_front
        "mat-prompt-5": _make_png(576, 1280, (50, 50, 200)),     # fullbody_side
        "mat-prompt-6": _make_png(576, 1280, (200, 200, 50)),    # fullbody_back
        "mat-prompt-7": _make_png(1024, 1024, (80, 160, 80)),    # prop T2I single step
    }
    merged_object_info = {**object_info_fixture(), **_fake_object_info(lora_row.filename)}

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        lease_calls.append(f"lease-{len(lease_calls) + 1}")
        yield lease_calls[-1]

    class FakeMatClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return merged_object_info

        def upload_image(self, filename, _content, **_kwargs):
            return {"name": filename}

        def queue_prompt(self, graph, extra_data=None, **_kwargs):
            idx = len(submitted) + 1
            pid = f"mat-prompt-{idx}"
            submitted.append((pid, graph))
            return {"prompt_id": pid}

        def history(self, prompt_id):
            return {
                prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {
                        "461": {"images": [{"filename": f"{prompt_id}.png", "subfolder": "", "type": "output"}]}
                    },
                }
            }

        def view(self, filename, **_kwargs):
            pid = filename.removesuffix(".png")
            return prompt_outputs[pid]

        def free_models(self):
            return None

    monkeypatch.setattr(mat_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(mat_execution, "ComfyUIClient", FakeMatClient)
    monkeypatch.setattr(mat_execution, "_ensure_renderer", lambda *_a, **_k: {"renderer_owned": True})
    monkeypatch.setattr(
        mat_execution,
        "resolve_runtime",
        lambda *_a, **_k: {
            "config": mat_runtime.ImageRuntimeConfig(),
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": "f" * 64,
            "graph_report": {"complete": True},
            "object_info": merged_object_info,
        },
    )
    monkeypatch.setattr(mat_execution, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "shine.quality.technical._full_decode_check",
        lambda *_a, **_k: TechnicalCheck(name="decode", status="pass", detail="decoded_ok"),
    )

    char_res = mat_execution.execute_item(factory, settings, char_item.id, "mat-worker-1")
    assert char_res["status"] == "CANDIDATE_READY", char_res
    assert lease_calls == ["lease-1"]  # single GPU lease across T2I + 5 Character Sheet steps
    assert len(submitted) == 6

    t2i_sha = hashlib.sha256(prompt_outputs["mat-prompt-1"]).hexdigest()
    front_sha = hashlib.sha256(prompt_outputs["mat-prompt-4"]).hexdigest()
    assert submitted[1][1]["700"]["inputs"]["image"] == f"mat_{t2i_sha[:16]}.png"
    assert submitted[4][1]["700"]["inputs"]["image"] == f"mat_{front_sha[:16]}.png"
    assert submitted[2][1]["4511"]["inputs"]["strength_model"] == 0.8

    with factory() as db:
        cand = db.get(CandidateAsset, char_res["candidate_id"])
        assert cand is not None
        assert cand.payload["media"]["width"] == 3584
        assert cand.payload["media"]["height"] == 2016
        assert cand.payload["media"]["width"] / cand.payload["media"]["height"] == pytest.approx(16 / 9)
        prov = cand.payload["provenance"]
        assert prov["pipeline"] == "t2i_to_character_sheet_v2"
        assert prov["t2i_seed_portrait"]["sha256"] == t2i_sha
        assert len(prov["character_sheet"]["steps"]) == 5
        attempts = db.scalars(
            select(MaterialImageAttempt)
            .where(MaterialImageAttempt.item_id == char_item.id)
            .order_by(MaterialImageAttempt.attempt_no)
        ).all()
        assert len(attempts) == 6
        assert all(att.status == "SUCCEEDED" for att in attempts)

    # Prop item still runs exact single-step T2I.
    prop_res = mat_execution.execute_item(factory, settings, prop_item.id, "mat-worker-1")
    assert prop_res["status"] == "CANDIDATE_READY"
    assert len(submitted) == 7  # only 1 additional T2I prompt for prop


def test_materialprep_character_fails_closed_without_lora_and_halts_on_unknown_timeout(tmp_path, monkeypatch):
    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)
    _project_id, item_ids, ctx = _seed_material_batch(client, factory, settings, families=("character",))
    char_item_id = item_ids[0]
    MaterialItem = ctx["MaterialItem"]
    mat_execution = ctx["mat_execution"]
    mat_runtime = ctx["mat_runtime"]
    object_info_fixture = ctx["object_info_fixture"]

    submitted: list[dict] = []

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        yield "lease-precheck"

    class TrackingClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return {**object_info_fixture(), **_fake_object_info("missing.safetensors")}

        def queue_prompt(self, graph, **_kwargs):
            submitted.append(graph)
            return {"prompt_id": "should-not-run"}

        def free_models(self):
            return None

    monkeypatch.setattr(mat_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(mat_execution, "ComfyUIClient", TrackingClient)
    monkeypatch.setattr(mat_execution, "_ensure_renderer", lambda *_a, **_k: {"renderer_owned": True})
    monkeypatch.setattr(
        mat_execution,
        "resolve_runtime",
        lambda *_a, **_k: {
            "config": mat_runtime.ImageRuntimeConfig(),
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": "f" * 64,
            "graph_report": {"complete": True},
            "object_info": TrackingClient("http://127.0.0.1:8188").object_info(),
        },
    )

    fail_res = mat_execution.execute_item(factory, settings, char_item_id, "mat-worker-fail")
    assert fail_res["status"] == "FAILED"
    assert fail_res["code"] == "CHARACTER_SHEET_LORA_REQUIRED"
    assert submitted == []  # failed closed before wasting GPU T2I

    # With LoRA installed, a timeout on edit step 2 halts in NEEDS_REVIEW / UNKNOWN without replaying.
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)
    _project_id2, item_ids2, _ctx2 = _seed_material_batch(client, factory, settings, families=("character",))
    char_item_id2 = item_ids2[0]
    merged_object_info = {**object_info_fixture(), **_fake_object_info(lora_row.filename)}
    call_count = {"n": 0}

    class Step2TimeoutClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return merged_object_info

        def upload_image(self, filename, _content, **_kwargs):
            return {"name": filename}

        def queue_prompt(self, _graph, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise TimeoutError("step 2 submit timeout")
            return {"prompt_id": f"p-{call_count['n']}"}

        def history(self, prompt_id):
            return {
                prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {
                        "461": {"images": [{"filename": f"{prompt_id}.png", "subfolder": "", "type": "output"}]}
                    },
                }
            }

        def view(self, filename, **_kwargs):
            if filename == "p-1.png":
                return _make_png(1024, 1024, (100, 100, 100))
            return _make_png(736, 928, (200, 100, 100))

        def free_models(self):
            return None

    monkeypatch.setattr(mat_execution, "ComfyUIClient", Step2TimeoutClient)
    monkeypatch.setattr(
        mat_execution,
        "resolve_runtime",
        lambda *_a, **_k: {
            "config": mat_runtime.ImageRuntimeConfig(),
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": "f" * 64,
            "graph_report": {"complete": True},
            "object_info": merged_object_info,
        },
    )
    uncertain_res = mat_execution.execute_item(factory, settings, char_item_id2, "mat-worker-unknown")
    assert uncertain_res["status"] == "NEEDS_REVIEW"
    assert call_count["n"] == 3  # stopped cleanly at step 2 timeout without replaying
    with factory() as db:
        row = db.get(MaterialItem, char_item_id2)
        assert row.status == "NEEDS_REVIEW"

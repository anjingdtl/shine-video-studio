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


def _seed_material_batch(
    client: TestClient,
    factory,
    settings,
    *,
    families=("character", "prop"),
    character_sheet_mode: bool = False,
):
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
            character_sheet_mode=character_sheet_mode,
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

    project_id, item_ids, ctx = _seed_material_batch(
        client, factory, settings, families=("character", "prop"), character_sheet_mode=True
    )
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
    # Seed a character-sheet item whose frozen LoRA disappears before Worker execution
    # (verifies Worker-side last-line defense fails closed without wasting GPU T2I).
    lora_row_tmp = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row_tmp.sha256)
    _project_id, item_ids, ctx = _seed_material_batch(
        client, factory, settings, families=("character",), character_sheet_mode=True
    )
    char_item_id = item_ids[0]
    MaterialItem = ctx["MaterialItem"]
    mat_execution = ctx["mat_execution"]
    mat_runtime = ctx["mat_runtime"]
    object_info_fixture = ctx["object_info_fixture"]

    # Remove the LoRA asset and frozen pin to simulate a pre-execution missing-LoRA condition.
    from pathlib import Path

    with factory.begin() as db:
        from shine.comfyops.models import StyleLoraAsset

        item_row = db.get(MaterialItem, char_item_id)
        item_row.payload = {k: v for k, v in (item_row.payload or {}).items() if k != "character_sheet_lora"}
        row_to_del = db.get(StyleLoraAsset, lora_row_tmp.id)
        if row_to_del is not None:
            db.delete(row_to_del)
    Path(lora_row_tmp.storage_path).unlink(missing_ok=True)

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
    _project_id2, item_ids2, _ctx2 = _seed_material_batch(
        client, factory, settings, families=("character",), character_sheet_mode=True
    )
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


def test_character_sheet_capability_discovery_plan_and_legacy_sha256_reuse(tmp_path, monkeypatch):
    from shine.comfyops import deployment, loras, resource_discovery, setup_preparation, setup_reconcile, setup_summary
    from shine.comfyops.verification_receipts import save_receipt
    from tests.test_style_lora_assembly import _make_valid_safetensors

    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)

    # 1. Fresh machine: character_sheet is an independent optional capability (1 file, ~304 MiB)
    #    and never blocks zero-LoRA image or H3 readiness.
    with factory() as db:
        cap = setup_summary.capability_report(db, settings, "character_sheet", installation=None, project_id=None)
        assert cap["optional"] is True
        assert cap["state"] == "MISSING"
        assert cap["missing_bytes"] == 318_819_792
        assert len(cap["missing_files"]) == 1
        assert cap["missing_files"][0]["component_id"] == cs.MULTIPLE_ANGLES_LORA_ID

        plan_bundle = setup_preparation.plan_prepare_downloads(
            db, settings, capabilities=["character_sheet"], verify_runtime=True
        )
        assert plan_bundle["download_bytes"] == 318_819_792
        assert plan_bundle["plans"][0]["capability"] == "character_sheet"
        assert plan_bundle["plans"][0]["components"] == [cs.MULTIPLE_ANGLES_LORA_ID]

    exec_resp = client.post(
        "/api/v1/setup/prepare/execute",
        headers={"X-Shine-Local-Action": "1"},
        json={"capabilities": ["character_sheet"], "verify_runtime": True, "deep_scan": False},
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["steps"] == ["RECONCILE", "DOWNLOAD"]

    # 2. Legacy machine scenario A: file already installed in isolated-packages under the former
    #    ``shine-qwen-image-loras-windows-x64`` receipt must immediately project READY with 0 download bytes.
    cs_catalog = deployment.load_deployment_catalog(deployment.CHARACTER_SHEET_CATALOG)
    comp = cs_catalog["components"][0]
    pinned = comp["files"][0]
    pkg_dir = settings.comfyops_dir / "isolated-packages" / comp["component_id"]
    pkg_file = pkg_dir / pinned["relative_path"]
    real_sha, real_size = _make_valid_safetensors(pkg_file, seed_tag=b"legacy-cs")
    patched_catalog = {
        **cs_catalog,
        "components": [
            {
                **comp,
                "files": [{**pinned, "sha256": real_sha, "size": real_size}],
            }
        ],
        "total_bytes": real_size,
    }
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", real_sha)
    orig_loader = deployment.load_deployment_catalog

    def _patched_loader(name: str):
        if name == deployment.CHARACTER_SHEET_CATALOG:
            return patched_catalog
        return orig_loader(name)

    monkeypatch.setattr(deployment, "load_deployment_catalog", _patched_loader)

    with factory.begin() as db:
        save_receipt(
            db,
            variant_id=None,
            catalog_id="shine-qwen-image-loras-windows-x64",
            catalog_version="1.0",
            component=patched_catalog["components"][0],
            root=settings.comfyops_dir / "isolated-packages",
            directory=pkg_dir,
        )
    with factory() as db:
        legacy_cap = setup_summary.capability_report(
            db, settings, "character_sheet", installation=None, project_id=None
        )
        assert legacy_cap["state"] == "READY"
        assert legacy_cap["facts"]["FILES_VERIFIED"] is True
        assert legacy_cap["facts"]["RESOURCE_REGISTERED"] is True
        assert legacy_cap["missing_bytes"] == 0
        legacy_plan = setup_preparation.plan_prepare_downloads(
            db, settings, capabilities=["character_sheet"], verify_runtime=True
        )
        assert legacy_plan["download_bytes"] == 0
        assert legacy_plan["plans"][0]["needs_download"] is False

    # 3. Legacy machine scenario B: file only exists on disk (no receipt) in isolated-packages;
    #    resource_discovery + setup_reconcile verify by SHA-256 and register without re-downloading.
    app2, factory2, settings2 = make_studio(tmp_path / "legacy-reconcile")
    client2 = TestClient(app2)
    pkg_dir2 = settings2.comfyops_dir / "isolated-packages" / comp["component_id"]
    pkg_file2 = pkg_dir2 / pinned["relative_path"]
    sha2, size2 = _make_valid_safetensors(pkg_file2, seed_tag=b"legacy-cs")
    assert (sha2, size2) == (real_sha, real_size)

    with factory2() as db:
        disc = resource_discovery.report(db)
        assert "character_sheet" in disc["capabilities"]
        rec = setup_reconcile.reconcile_installation(
            db, settings2, path=None, capability="character_sheet", adopt=False
        )
        assert rec["status"] == "RECONCILED"
        assert rec["complete"] is True
        assert comp["component_id"] in rec["registered"]

    with factory2() as db:
        cap2 = setup_summary.capability_report(
            db, settings2, "character_sheet", installation=None, project_id=None
        )
        assert cap2["state"] == "READY"
        assert cap2["missing_bytes"] == 0
        plan2 = setup_preparation.plan_prepare_downloads(
            db, settings2, capabilities=["character_sheet"], verify_runtime=True
        )
        assert plan2["download_bytes"] == 0

    loras_resp = client2.get("/api/v1/loras?base_model=qwen_image_21")
    assert loras_resp.status_code == 200
    assert loras_resp.json()["character_sheet"]["lora_ready"] is True
    assert loras_resp.json()["character_sheet"]["lora_id"] == cs.MULTIPLE_ANGLES_LORA_ID
    assert loras.list_style_loras(factory2(), settings2, base_model=loras.IMAGE_LORA_BASE, probe_comfy=False)["items"]


def test_materialprep_character_default_mode_runs_single_t2i_without_lora(tmp_path, monkeypatch):
    from shine.quality.technical import TechnicalCheck

    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)

    # Zero-LoRA machine: character_sheet_mode defaults to False -> character runs standard single-step T2I.
    _project_id, item_ids, ctx = _seed_material_batch(
        client, factory, settings, families=("character",), character_sheet_mode=False
    )
    char_item_id = item_ids[0]
    MaterialItem = ctx["MaterialItem"]
    CandidateAsset = ctx["CandidateAsset"]
    mat_execution = ctx["mat_execution"]
    mat_runtime = ctx["mat_runtime"]
    object_info_fixture = ctx["object_info_fixture"]

    with factory() as db:
        item = db.get(MaterialItem, char_item_id)
        assert not (item.payload or {}).get("character_sheet_mode")
        assert "character_sheet_lora" not in (item.payload or {})

    submitted: list[dict] = []

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        yield "lease-zero-lora"

    class SingleT2IClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def object_info(self):
            return object_info_fixture()

        def queue_prompt(self, graph, **_kwargs):
            submitted.append(graph)
            return {"prompt_id": "t2i-only-1"}

        def history(self, prompt_id):
            return {
                prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {
                        "461": {"images": [{"filename": f"{prompt_id}.png", "subfolder": "", "type": "output"}]}
                    },
                }
            }

        def view(self, _filename, **_kwargs):
            return _make_png(1024, 1024, (90, 120, 150))

        def free_models(self):
            return None

    monkeypatch.setattr(mat_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(mat_execution, "ComfyUIClient", SingleT2IClient)
    monkeypatch.setattr(mat_execution, "_ensure_renderer", lambda *_a, **_k: {"renderer_owned": True})
    monkeypatch.setattr(
        mat_execution,
        "resolve_runtime",
        lambda *_a, **_k: {
            "config": mat_runtime.ImageRuntimeConfig(),
            "base_url": "http://127.0.0.1:8188",
            "fingerprint": "f" * 64,
            "graph_report": {"complete": True},
            "object_info": object_info_fixture(),
        },
    )
    monkeypatch.setattr(mat_execution, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "shine.quality.technical._full_decode_check",
        lambda *_a, **_k: TechnicalCheck(name="decode", status="pass", detail="decoded_ok"),
    )

    res = mat_execution.execute_item(factory, settings, char_item_id, "mat-worker-t2i")
    assert res["status"] == "CANDIDATE_READY", res
    assert len(submitted) == 1
    assert all(node.get("class_type") != "LoraLoaderModelOnly" for node in submitted[0].values())
    with factory() as db:
        cand = db.get(CandidateAsset, res["candidate_id"])
        assert cand is not None
        assert cand.payload["media"]["width"] == 1024
        assert cand.payload["media"]["height"] == 1024


def test_materialprep_character_sheet_mode_missing_lora_refuses_synchronously_across_routes(tmp_path, monkeypatch):
    from sqlalchemy import select

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement, PreProductionPlan
    from shine.materialprep import service as mat_service
    from shine.materialprep.models import MaterialItem, MaterialRun

    app, factory, settings = make_studio(tmp_path)
    client = TestClient(app)

    # Readiness projection reports not ready when LoRA is absent.
    loras_before = client.get("/api/v1/loras?base_model=qwen_image_21")
    assert loras_before.status_code == 200
    assert loras_before.json()["character_sheet"]["lora_ready"] is False
    assert "设置 → 制作环境 → 角色五视图定妆照" in loras_before.json()["character_sheet"]["setup_hint"]

    project_id = client.post("/api/v1/projects", json={"title": "同步裁决校验"}).json()["id"]
    with factory.begin() as db:
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
        req = AssetRequirement(
            project_id=project_id,
            plan_id=plan.id,
            payload={"family": "character", "entity_id": "燕三娘", "description": "武侠女侠", "strategy": "generate"},
            sha256="c" * 64,
            status="CONFIRMED",
        )
        db.add(req)
        db.flush()
        conf_id = "a" * 64
        conf_hash = "b" * 64
        plan.payload = {
            **plan_payload,
            "requirements": [
                {"requirement_id": req.id, "family": "character", "entity_id": "燕三娘", "description": "武侠女侠"}
            ],
            "confirmation": {"confirmation_id": conf_id, "confirmation_hash": conf_hash},
        }
        plan.sha256 = graph_hash(plan.payload)
        db.flush()
        snapshot = mat_service.confirmation_snapshot_hash(plan)
        req_id = req.id
        plan_id = plan.id

    headers = {"X-Shine-Local-Action": "1"}

    # 1. /generate refuses synchronously with 409 CHARACTER_SHEET_LORA_REQUIRED and creates no run/item.
    gen_resp = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/generate",
        headers=headers,
        json={
            "confirmation_id": conf_id,
            "confirmation_hash": conf_hash,
            "requirement_ids": [req_id],
            "idempotency_key": "sync-refuse-gen-1",
            "mode": "real",
            "character_sheet_mode": True,
        },
    )
    assert gen_resp.status_code == 409
    assert gen_resp.json()["detail"]["code"] == "CHARACTER_SHEET_LORA_REQUIRED"
    assert "设置 → 制作环境 → 角色五视图定妆照" in gen_resp.json()["detail"]["message"]

    # 2. /regenerate refuses synchronously.
    regen_resp = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/regenerate",
        headers=headers,
        json={
            "requirement_id": req_id,
            "confirmation_id": conf_id,
            "confirmation_hash": conf_hash,
            "idempotency_key": "sync-refuse-regen-1",
            "brief": {
                "schema_version": "1.0",
                "appearance": "Ming-dynasty wuxia heroine in dark teal martial robe",
                "must_keep": [],
                "avoid": [],
                "questions": [],
            },
            "seed": 1,
            "character_sheet_mode": True,
        },
    )
    assert regen_resp.status_code == 409
    assert regen_resp.json()["detail"]["code"] == "CHARACTER_SHEET_LORA_REQUIRED"

    # 3. /regenerate-all refuses synchronously.
    regen_all_resp = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/regenerate-all",
        headers=headers,
        json={"idempotency_key": "sync-refuse-regen-all-1", "requirement_ids": [req_id], "character_sheet_mode": True},
    )
    assert regen_all_resp.status_code == 409
    assert regen_all_resp.json()["detail"]["code"] == "CHARACTER_SHEET_LORA_REQUIRED"

    # 4. /director/materials/commit refuses synchronously and rolls back cleanly.
    commit_resp = client.post(
        f"/api/v1/projects/{project_id}/director/materials/commit",
        headers=headers,
        json={
            "plan_revision_id": plan_id,
            "expected_snapshot": snapshot,
            "decisions": [{"requirement_id": req_id, "strategy": "generate", "entity_id": "燕三娘"}],
            "queue_generation": True,
            "character_sheet_mode": True,
        },
    )
    assert commit_resp.status_code == 409
    assert commit_resp.json()["detail"]["code"] == "CHARACTER_SHEET_LORA_REQUIRED"

    with factory() as db:
        assert list(db.scalars(select(MaterialRun).where(MaterialRun.project_id == project_id))) == []
        assert list(db.scalars(select(MaterialItem).where(MaterialItem.project_id == project_id))) == []

    # 5. Once multiple-angles LoRA is installed, projection turns ready and /generate succeeds with frozen pin.
    lora_row = _install_multiple_angles_lora(factory, settings, tmp_path)
    monkeypatch.setattr(cs, "MULTIPLE_ANGLES_LORA_SHA256", lora_row.sha256)

    loras_after = client.get("/api/v1/loras?base_model=qwen_image_21")
    assert loras_after.status_code == 200
    assert loras_after.json()["character_sheet"]["lora_ready"] is True

    ok_resp = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/generate",
        headers=headers,
        json={
            "confirmation_id": conf_id,
            "confirmation_hash": conf_hash,
            "requirement_ids": [req_id],
            "idempotency_key": "sync-ok-gen-1",
            "mode": "real",
            "character_sheet_mode": True,
        },
    )
    assert ok_resp.status_code == 202
    with factory() as db:
        items = list(db.scalars(select(MaterialItem).where(MaterialItem.project_id == project_id)))
        assert len(items) == 1
        assert items[0].payload["character_sheet_mode"] is True
        assert items[0].payload["character_sheet_lora"]["style_loras"][0]["sha256"] == lora_row.sha256


def test_setup_download_waits_for_cached_lora_packs_and_validate_defers_when_thread_alive(
    tmp_path, monkeypatch
):
    from shine.comfyops import deployment, installation_worker, setup_preparation
    from shine.comfyops.models import InstallationAttempt

    _app, factory, settings = make_studio(tmp_path)
    cache_dir = settings.comfyops_dir / "isolated-packages" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cs_catalog = deployment.load_deployment_catalog(deployment.CHARACTER_SHEET_CATALOG)
    cs_comp = cs_catalog["components"][0]
    cs_file = cs_comp["files"][0]
    cs_sha, cs_size = _make_valid_safetensors(cache_dir / cs_file["sha256"], seed_tag=b"cached-cs")
    monkeypatch.setattr(
        deployment,
        "load_deployment_catalog",
        lambda name: (
            {
                **cs_catalog,
                "components": [{**cs_comp, "files": [{**cs_file, "sha256": cs_sha, "size": cs_size}]}],
                "total_bytes": cs_size,
            }
            if name == deployment.CHARACTER_SHEET_CATALOG
            else deployment.load_default_catalog()
            if name == "h3-ref2va-windows-x64"
            else {"catalog_id": f"shine-{name}", "catalog_version": "1.0", "components": []}
        ),
    )

    with factory.begin() as db:
        from shine.comfyops.verification_receipts import verify_receipt

        comp_copy = {**cs_comp, "files": [{**cs_file, "sha256": cs_sha, "size": cs_size}]}
        verify_receipt(
            db,
            component=comp_copy,
            variant_id=None,
            catalog_id=cs_catalog["catalog_id"],
            catalog_version="1.0",
            cache_dir=cache_dir,
            read_only=False,
        )
        setup_preparation.enqueue(
            db,
            project_id=None,
            capabilities=["character_sheet", "h3"],
            settings=settings,
        )

    plan_bundle = {
        "plans": [
            {
                "capability": "h3",
                "label": "H3 视频生成",
                "catalog": "shine-h3-ref2va-windows-x64",
                "_catalog_file": "h3-ref2va-windows-x64",
                "_capability_components": [],
                "_closure_components": [],
                "components": [],
                "download_bytes": 0,
                "needs_download": False,
            },
            {
                "capability": "character_sheet",
                "label": "角色五视图定妆照",
                "catalog": cs_catalog["catalog_id"],
                "_catalog_file": deployment.CHARACTER_SHEET_CATALOG,
                "_capability_components": [cs_comp["component_id"]],
                "_closure_components": [],
                "components": [cs_comp["component_id"]],
                "download_bytes": 0,
                "needs_download": False,
            },
        ],
    }
    monkeypatch.setattr(setup_preparation, "plan_prepare_downloads", lambda *a, **kw: plan_bundle)
    monkeypatch.setattr(setup_preparation, "_assert_claim_in_tx", lambda *a, **kw: None)
    monkeypatch.setattr(setup_preparation, "_claim_guard_for", lambda task: (lambda db: None))

    task = {
        "task_id": "setup-cached-cs",
        "project_id": None,
        "claim_token": "tok-1",
        "request": {"capabilities": ["character_sheet", "h3"], "verify_runtime": True},
        "downloads": {},
    }
    first = setup_preparation._step_download(factory, settings, task)
    assert first["status"] == "PENDING"
    cs_dl = task["downloads"]["character_sheet"]
    assert cs_dl["background"] is False

    # While attempt is still in DOWNLOADING, _step_download stays PENDING (does not jump to VALIDATE).
    with factory.begin() as db:
        att = db.get(InstallationAttempt, cs_dl["attempt_id"])
        att.stage = "DOWNLOADING"
        att.status = "DOWNLOADING"
    second = setup_preparation._step_download(factory, settings, task)
    assert second["status"] == "PENDING"

    # Once cached copy finishes, _step_download reports DONE.
    with factory.begin() as db:
        att = db.get(InstallationAttempt, cs_dl["attempt_id"])
        att.stage = "FILES_VERIFIED_PENDING_RUNTIME"
        att.status = "FILES_VERIFIED_PENDING_RUNTIME"
    third = setup_preparation._step_download(factory, settings, task)
    assert third["status"] == "DONE"

    # _step_validate must defer if a background LoRA thread is still alive in the Worker.
    monkeypatch.setattr(installation_worker, "has_active_background_install", lambda: True)
    monkeypatch.setattr(
        setup_preparation,
        "_delegate_supervisor_validation",
        lambda *a, **kw: pytest.fail("must not delegate worker stop while background install thread is alive"),
    )
    with factory.begin() as db:
        from shine.comfyops.runtime_configuration import CANDIDATE_KEY
        from shine.models import AppSetting

        db.add(AppSetting(key=CANDIDATE_KEY, value={"project_id": None, "identity": {"recipe_sha256": "r" * 64}}))
    val_res = setup_preparation._step_validate(factory, settings, task)
    assert val_res["status"] == "PENDING"
    assert val_res["code"] == "BACKGROUND_INSTALL_IN_PROGRESS"

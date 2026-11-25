"""Tests for Shine Video Studio Style LoRA Assembly V1.

Covers:
1. Compiler V3: 0/1/2/3 chained LoraLoaderModelOnly, LowVRAMAttention & PDD
   coexistence, legacy REALISM_LORA recovery, W6A8 refusal, bounds & trigger
   sanitization, deterministic hashes, OOM fallback guard identity.
2. Registry & Adjudicator (one fact, one adjudicator, one executor):
   safetensors validation, local_path & multipart import, SHA-256 dedup,
   atomic user store & extra_model_paths wiring without mutating launch pins,
   auto-discovery of h3-realism-people, live ComfyUI list & v3 COMBO matching,
   tamper/missing/visibility gates, PATCH & DELETE (+ 409 LORA_IN_USE).
3. Dual-pipeline E2E (7-stage & 4-stage) + zero-LoRA zero new blockers.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from shine.comfy.client import ComfyUIClient
from shine.comfy.graph_compiler import (
    LEGACY_REALISM_LORA,
    GraphCompileError,
    StyleLoraSpec,
    compile_ref2va_workflow,
    is_low_vram_guard_variant,
    low_vram_attention_form_of_graph,
    resolve_frozen_graph_form,
)
from shine.comfy.workflow import validate_against_object_info, validate_graph
from shine.comfyops import deployment, managed_recipe
from shine.comfyops.loras import (
    REALISM_LORA_ID,
    StyleLoraError,
    ensure_style_lora_extra_paths_config,
    evaluate_style_lora_selection,
    extract_comfy_lora_names,
    import_style_lora,
    list_style_loras,
)
from shine.comfyops.mode_capabilities import sealed_mode_nodes_compatible
from shine.foundation.h3 import compile_h3_ref2va_graph
from shine.manual4.storage.models import M4Job, M4ProductionRun
from shine.manual4.worker import _execute_single_job, _load_run_context
from shine.models import Job
from shine.strategy import DEFAULT_STRATEGY, StyleLoraConfig, model_variant_options
from shine.worker import run_one
from tests.conftest import (
    create_project,
    create_referenced_shot,
    freeze_style,
    make_sparse_file,
    make_studio,
    make_test_video_bytes,
)
from tests.fake_http import FakeComfyServer

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}


def _make_valid_safetensors(path: Path, *, seed_tag: bytes = b"A") -> tuple[str, int]:
    """Write a minimal valid `.safetensors` binary and return (sha256, bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = seed_tag * 16
    header = {
        "__metadata__": {"format": "pt"},
        "diffusion_model.blocks.0.lora_A.weight": {
            "dtype": "F16",
            "shape": [4, 2],
            "data_offsets": [0, len(payload)],
        },
    }
    raw_header = json.dumps(header).encode("utf-8")
    blob = struct.pack("<Q", len(raw_header)) + raw_header + payload
    path.write_bytes(blob)
    return hashlib.sha256(blob).hexdigest(), len(blob)


def _minimal_png(width: int = 16, height: int = 16) -> bytes:
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + (b"\x20\x60\xd0" * width) for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _spec(
    idx: int,
    *,
    lora_name: str | None = None,
    strength: float = 0.65,
    trigger: str = "",
) -> StyleLoraSpec:
    return StyleLoraSpec(
        lora_id=f"lora_{idx}",
        lora_name=lora_name or f"shine_user/style_{idx}.safetensors",
        sha256=str(idx) * 64,
        strength_model=strength,
        trigger_word=trigger,
    )


def _object_info_from_graph(
    graph: dict[str, Any],
    lora_names: list[str] | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for node in graph.values():
        cls = node["class_type"]
        req: dict[str, Any] = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                req.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                req.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": req, "optional": {}}})
    if lora_names is not None:
        info["LoraLoaderModelOnly"] = {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "lora_name": [lora_names],
                    "strength_model": ["FLOAT"],
                },
                "optional": {},
            }
        }
    return info


# ---------------------------------------------------------------------------
# 1. Dynamic graph compiler (0..3 LoRA, LowVRAM, legacy, W6A8, validation)
# ---------------------------------------------------------------------------


def test_compiler_zero_to_three_loras_and_low_vram_chaining():
    g0 = compile_ref2va_workflow(image_count=2, accelerator_id="pdd8", low_vram_attention=False)
    assert "LoraLoaderModelOnly" not in g0.required_nodes
    assert "11" not in g0.graph
    assert g0.graph["12"]["inputs"]["model"] == ["10", 0]
    assert g0.execution_identity()["compiler"] == "shine.graph_compiler.v3"

    s1, s2, s3 = _spec(1, strength=0.4), _spec(2, strength=0.8), _spec(3, strength=1.1)
    g1 = compile_ref2va_workflow(image_count=2, accelerator_id="pdd8", style_loras=[s1], low_vram_attention=False)
    g2 = compile_ref2va_workflow(image_count=2, accelerator_id="pdd8", style_loras=[s1, s2], low_vram_attention=False)
    g3 = compile_ref2va_workflow(
        image_count=2,
        accelerator_id="pdd8",
        style_loras=[s1, s2, s3],
        low_vram_attention=True,
    )

    assert len({g0.graph_sha256, g1.graph_sha256, g2.graph_sha256, g3.graph_sha256}) == 4
    assert g1.graph["11"]["inputs"]["model"] == ["10", 0]
    assert g1.graph["12"]["inputs"]["model"] == ["11", 0]

    assert g2.graph["11"]["inputs"]["model"] == ["10", 0]
    assert g2.graph["111"]["inputs"]["model"] == ["11", 0]
    assert g2.graph["12"]["inputs"]["model"] == ["111", 0]

    # LoRA chain ("11" -> "111" -> "112") -> SigmaShift ("12") -> ChunkFF ("13")
    # -> LowVRAMAttention ("15") -> SolAttnPatch ("16") -> PDD ("18")
    assert g3.graph["11"]["inputs"]["model"] == ["10", 0]
    assert g3.graph["111"]["inputs"]["model"] == ["11", 0]
    assert g3.graph["112"]["inputs"]["model"] == ["111", 0]
    assert g3.graph["12"]["inputs"]["model"] == ["112", 0]
    assert g3.graph["15"]["class_type"] == "MiniMaxLowVRAMAttention"
    assert g3.graph["16"]["inputs"]["model"] == ["15", 0]
    validate_graph(g3.graph, required_nodes=g3.required_nodes)
    obj_info = _object_info_from_graph(g3.graph, [s1.lora_name, s2.lora_name, s3.lora_name])
    assert validate_against_object_info(g3.graph, g3.parameter_map, obj_info) == {
        "missing_nodes": [],
        "missing_inputs": [],
    }

    # OOM fallback identity & form detection work with stacked LoRAs
    g2_low = compile_ref2va_workflow(
        image_count=2, accelerator_id="pdd8", style_loras=[s1, s2], low_vram_attention=True
    )
    assert g2.recipe_sha256 == g2_low.recipe_sha256
    assert is_low_vram_guard_variant(
        g2_low.graph_sha256,
        expected_graph_sha256=g2.graph_sha256,
        accelerator_id="pdd8",
        image_count=2,
        style_loras=[s1, s2],
    )
    assert resolve_frozen_graph_form(
        g2.graph_sha256, accelerator_id="pdd8", image_count=2, style_loras=[s1, s2]
    ) == (False, False)
    assert resolve_frozen_graph_form(
        g2_low.graph_sha256, accelerator_id="pdd8", image_count=2, style_loras=[s1, s2]
    ) == (True, False)


def test_compiler_legacy_realism_lora_recovery():
    legacy_on = compile_ref2va_workflow(image_count=2, low_vram_attention=True, _legacy_realism_lora=True)
    legacy_off = compile_ref2va_workflow(image_count=2, low_vram_attention=False, _legacy_realism_lora=True)
    assert legacy_on.graph["11"]["inputs"]["lora_name"] == LEGACY_REALISM_LORA
    assert legacy_on.graph["11"]["inputs"]["strength_model"] == 0.65
    assert resolve_frozen_graph_form(legacy_on.graph_sha256, image_count=2) == (True, True)
    assert resolve_frozen_graph_form(legacy_off.graph_sha256, image_count=2) == (False, True)
    assert low_vram_attention_form_of_graph(legacy_off.graph_sha256, image_count=2) is False


def test_compiler_rejects_invalid_lora_configs():
    with pytest.raises(GraphCompileError) as exc4:
        compile_ref2va_workflow(image_count=1, style_loras=[_spec(1), _spec(2), _spec(3), _spec(4)])
    assert exc4.value.code == "STYLE_LORA_LIMIT_EXCEEDED"

    with pytest.raises(GraphCompileError) as exc_dup:
        compile_ref2va_workflow(image_count=1, style_loras=[_spec(1), _spec(1)])
    assert exc_dup.value.code == "STYLE_LORA_DUPLICATE"

    with pytest.raises(GraphCompileError) as exc_str:
        compile_ref2va_workflow(image_count=1, style_loras=[_spec(1, strength=1.55)])
    assert exc_str.value.code == "STYLE_LORA_STRENGTH_OUT_OF_RANGE"

    with pytest.raises(GraphCompileError) as exc_var:
        compile_ref2va_workflow(
            image_count=1,
            variant_id="ref2va-lowend-candidate",
            accelerator_id="native20",
            style_loras=[_spec(1)],
        )
    assert exc_var.value.code == "VARIANT_LORA_UNSUPPORTED"

    for bad in ("中文触发", "use <Subject> hero", "At 00:02.000 flash", "x" * 161):
        with pytest.raises(GraphCompileError) as exc_trg:
            compile_ref2va_workflow(image_count=1, style_loras=[_spec(1, trigger=bad)])
        assert exc_trg.value.code == "STYLE_LORA_INVALID_TRIGGER"


def test_strategy_hash_backcompat_and_variant_lora_flag():
    baseline_hash = DEFAULT_STRATEGY.strategy_sha256()
    raw_legacy = {
        "schema_version": "1",
        "generation_mode": "Ref2VA",
        "model_variant_id": DEFAULT_STRATEGY.model_variant_id,
        "accelerator_id": DEFAULT_STRATEGY.accelerator_id,
        "second_pass": DEFAULT_STRATEGY.second_pass.model_dump(mode="json"),
        "upscale": DEFAULT_STRATEGY.upscale.model_dump(mode="json"),
    }
    legacy_digest = hashlib.sha256(
        json.dumps(raw_legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert baseline_hash == legacy_digest

    s1 = StyleLoraConfig(
        lora_id="l1", lora_name="a.safetensors", sha256="a" * 64, strength_model=0.5, trigger_word="noir"
    )
    s2 = StyleLoraConfig(
        lora_id="l2", lora_name="b.safetensors", sha256="b" * 64, strength_model=0.8, trigger_word=""
    )
    h12 = DEFAULT_STRATEGY.model_copy(update={"style_loras": [s1, s2]}).strategy_sha256()
    h21 = DEFAULT_STRATEGY.model_copy(update={"style_loras": [s2, s1]}).strategy_sha256()
    assert h12 != h21 != baseline_hash

    opts = model_variant_options()
    by_id = {row["variant_id"]: row for row in opts}
    assert by_id["h3-ref2va-int8-convrot"]["lora_supported"] is True
    assert by_id["ref2va-lowend-candidate"]["lora_supported"] is False
    compiled_nodes = compile_h3_ref2va_graph(image_count=1, style_loras=[_spec(1)]).required_nodes
    assert sealed_mode_nodes_compatible(compiled_nodes) is True


# ---------------------------------------------------------------------------
# 2. Single registry / executor / adjudicator & REST routes
# ---------------------------------------------------------------------------


def test_lora_registry_discovery_import_dedup_adjudicate_patch_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "PROFILE_DIR", tmp_path / "profiles")
    app, factory, settings = make_studio(tmp_path)

    realism_path = (
        settings.comfyops_dir
        / "models"
        / "loras"
        / "MiniMax-H3"
        / "h3-realism-people-t2v-i2v-r2v.safetensors"
    )
    realism_sha, _ = _make_valid_safetensors(realism_path, seed_tag=b"R")

    ext_lora = tmp_path / "external" / "cyberpunk_neon.safetensors"
    ext_sha, _ = _make_valid_safetensors(ext_lora, seed_tag=b"C")
    bad_ext = tmp_path / "external" / "wrong.ckpt"
    bad_ext.write_bytes(b"ckpt")
    bad_hdr = tmp_path / "external" / "broken.safetensors"
    bad_hdr.write_bytes(b"\x05\x00\x00\x00\x00\x00\x00\x00notjson")

    with TestClient(app) as client:
        listed = client.get("/api/v1/loras")
        assert listed.status_code == 200
        body = listed.json()
        assert body["comfy_reachable"] is False
        assert len(body["items"]) == 1
        disc = body["items"][0]
        assert disc["id"] == REALISM_LORA_ID
        assert disc["origin"] == "discovered"
        assert disc["sha256"] == realism_sha
        assert disc["status"] == "COMFY_UNREACHABLE"

        r_ext = client.post("/api/v1/loras/import", headers=HEADERS, json={"local_path": str(bad_ext)})
        assert r_ext.status_code == 422
        assert r_ext.json()["detail"]["code"] == "LORA_INVALID_EXTENSION"

        r_hdr = client.post("/api/v1/loras/import", headers=HEADERS, json={"local_path": str(bad_hdr)})
        assert r_hdr.status_code == 422
        assert r_hdr.json()["detail"]["code"] == "LORA_CORRUPT_HEADER"

        r_imp = client.post(
            "/api/v1/loras/import",
            headers=HEADERS,
            json={
                "local_path": str(ext_lora),
                "display_name": "赛博霓虹",
                "default_strength": 0.75,
                "trigger_word": "cyberpunk_neon",
            },
        )
        assert r_imp.status_code == 201
        imp_item = r_imp.json()["item"]
        assert r_imp.json()["deduplicated"] is False
        assert imp_item["sha256"] == ext_sha
        assert imp_item["origin"] == "imported"
        expected_user_dir = (settings.comfyops_dir / "models" / "loras" / "shine_user").resolve()
        assert Path(imp_item["storage_path"]).parent == expected_user_dir
        yaml_file = settings.comfyops_dir / "setup" / "shine-style-loras.yaml"
        assert yaml_file.is_file()
        assert "shine_style_loras" in yaml_file.read_text(encoding="utf-8")

        r_dup = client.post(
            "/api/v1/loras/import",
            headers=HEADERS,
            files={"file": ("alias.safetensors", BytesIO(ext_lora.read_bytes()), "application/octet-stream")},
        )
        assert r_dup.status_code == 201
        assert r_dup.json()["deduplicated"] is True
        assert r_dup.json()["item"]["id"] == imp_item["id"]

        r_bad_patch = client.patch(
            f"/api/v1/loras/{imp_item['id']}", headers=HEADERS, json={"trigger_word": "中文"}
        )
        assert r_bad_patch.status_code == 422
        assert r_bad_patch.json()["detail"]["code"] == "STYLE_LORA_INVALID_TRIGGER"

        r_patch = client.patch(
            f"/api/v1/loras/{imp_item['id']}",
            headers=HEADERS,
            json={"display_name": "霓虹风格V2", "default_strength": 0.9, "trigger_word": "neon_glow"},
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["item"]["display_name"] == "霓虹风格V2"

        v3_object_info = {
            "LoraLoaderModelOnly": {
                "input": {
                    "required": {
                        "lora_name": [
                            "COMBO",
                            {"options": ["MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"]},
                        ]
                    }
                }
            }
        }
        assert extract_comfy_lora_names(v3_object_info) == [
            "MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"
        ]
        with factory() as db:
            proj = list_style_loras(db, settings, object_info=v3_object_info, probe_comfy=False)
            statuses = {row["id"]: row["status"] for row in proj["items"]}
            assert statuses[REALISM_LORA_ID] == "READY"
            assert statuses[imp_item["id"]] == "NEEDS_COMFY_REFRESH"

            with pytest.raises(StyleLoraError) as exc_vis:
                evaluate_style_lora_selection(
                    db,
                    settings,
                    [
                        {
                            "lora_id": imp_item["id"],
                            "lora_name": imp_item["comfy_lora_name"],
                            "sha256": ext_sha,
                            "strength_model": 0.7,
                        }
                    ],
                    variant_id="h3-ref2va-int8-convrot",
                    object_info=v3_object_info,
                )
            assert exc_vis.value.code == "LORA_NOT_VISIBLE_IN_COMFYUI"

            resolved = evaluate_style_lora_selection(
                db,
                settings,
                [
                    {
                        "lora_id": REALISM_LORA_ID,
                        "lora_name": "MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors",
                        "sha256": realism_sha,
                        "strength_model": 0.5,
                    }
                ],
                variant_id="h3-ref2va-int8-convrot",
                object_info=v3_object_info,
            )
            assert resolved[0].lora_name == "MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"

            Path(imp_item["storage_path"]).write_bytes(b"tampered")
            with pytest.raises(StyleLoraError) as exc_tamper:
                evaluate_style_lora_selection(
                    db,
                    settings,
                    [
                        {
                            "lora_id": imp_item["id"],
                            "lora_name": imp_item["comfy_lora_name"],
                            "sha256": ext_sha,
                            "strength_model": 0.7,
                        }
                    ],
                    variant_id="h3-ref2va-int8-convrot",
                )
            assert exc_tamper.value.code == "LORA_HASH_MISMATCH"

        r_del_imp = client.delete(f"/api/v1/loras/{imp_item['id']}", headers=HEADERS)
        assert r_del_imp.status_code == 200
        assert not Path(imp_item["storage_path"]).exists()

        r_del_disc = client.delete(f"/api/v1/loras/{REALISM_LORA_ID}", headers=HEADERS)
        assert r_del_disc.status_code == 200
        assert realism_path.is_file()

    app.state.engine.dispose()


def test_build_renderer_launch_injects_style_loras_yaml_without_mutating_pins(tmp_path):
    python_exe = tmp_path / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    main_py = tmp_path / "ComfyUI" / "main.py"
    main_py.parent.mkdir(parents=True)
    main_py.write_text("", encoding="utf-8")
    bootstrap_py = tmp_path / "bootstrap.py"
    bootstrap_py.write_text("", encoding="utf-8")
    comfyops_dir = tmp_path / "comfyops"
    adapter_dir = comfyops_dir / "adapter"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "__init__.py").write_text("", encoding="utf-8")
    extra_yaml = comfyops_dir / "setup" / "shine-node-paths.yaml"
    extra_yaml.parent.mkdir(parents=True, exist_ok=True)
    extra_yaml.write_text("{}", encoding="utf-8")
    recipe = {
        "python": str(python_exe),
        "bootstrap": str(bootstrap_py),
        "root": str(main_py.parent),
        "base_directory": str(main_py.parent),
        "adapter_dir": str(adapter_dir),
        "adapter_sha256": "a" * 64,
        "runtime_data_root": str(comfyops_dir / "adopted-runtime" / "inst1"),
        "argv": ["{python}", "{bootstrap}", "--extra-model-paths-config", str(extra_yaml)],
        "pinned_files": ["{python}", "{bootstrap}", str(extra_yaml)],
        "log_file": str(comfyops_dir / "logs" / "comfy.log"),
    }

    argv_before, _, _, pins_before, _, _ = managed_recipe.build_renderer_launch(
        recipe, port=8188, token="t"
    )
    assert argv_before.count("--extra-model-paths-config") == 1

    style_yaml = ensure_style_lora_extra_paths_config(type("S", (), {"comfyops_dir": comfyops_dir})())
    assert style_yaml is not None and style_yaml.is_file()

    argv_after, _, _, pins_after, _, _ = managed_recipe.build_renderer_launch(
        recipe, port=8188, token="t"
    )
    assert argv_after.count("--extra-model-paths-config") == 2
    assert str(style_yaml) in argv_after
    assert pins_after == pins_before


# ---------------------------------------------------------------------------
# 3. Dual-pipeline E2E (7-stage & 4-stage) + DELETE 409 LORA_IN_USE
# ---------------------------------------------------------------------------


def _setup_mock_comfy_with_loras(server: FakeComfyServer, lora_names: list[str]) -> None:
    specs = [
        StyleLoraSpec(lora_id=f"s{i}", lora_name=name, sha256=str(i) * 64, strength_model=0.6)
        for i, name in enumerate(lora_names, start=1)
    ]
    for acc in ("pdd8", "native20"):
        sample_graph = compile_h3_ref2va_graph(
            image_count=1, accelerator_id=acc, low_vram_attention=True, style_loras=specs
        ).graph
        server.extra_object_info.update(_object_info_from_graph(sample_graph, lora_names))
    server.output_bytes = make_test_video_bytes(duration_s=5.0)


def test_seven_stage_e2e_with_style_loras_and_in_use_delete_block(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "PROFILE_DIR", tmp_path / "profiles")
    app, factory, settings = make_studio(tmp_path)

    lora_path = tmp_path / "src_loras" / "painterly_oil.safetensors"
    lora_sha, _ = _make_valid_safetensors(lora_path, seed_tag=b"P")
    lora_rel_name = "shine_user/painterly_oil.safetensors"

    sparse_marker = tmp_path / "sparse.bin"
    make_sparse_file(sparse_marker, 1024 * 1024)
    assert sparse_marker.stat().st_size == 1024 * 1024

    server = FakeComfyServer().start()
    try:
        with TestClient(app) as client:
            monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
            _setup_mock_comfy_with_loras(server, ["shine_user\\painterly_oil.safetensors"])
            settings_comfy = replace(
                settings,
                ffprobe_path="missing-ffprobe",
                ffmpeg_path="missing-ffmpeg",
                render_provider="comfyui",
                comfyui_base_url=server.base_url,
            )
            app.state.settings = settings_comfy

            imp = client.post(
                "/api/v1/loras/import",
                headers=HEADERS,
                json={
                    "local_path": str(lora_path),
                    "display_name": "油画厚涂",
                    "default_strength": 0.55,
                    "trigger_word": "impasto_oil",
                },
            )
            assert imp.status_code == 201
            lora_id = imp.json()["item"]["id"]

            pid = create_project(client, title="七阶 LoRA 测试")
            style = freeze_style(client, pid)
            shot = create_referenced_shot(client, pid, style["id"], title="镜头一")
            shot_id = shot["id"]

            req_zero = client.post(
                f"/api/v1/projects/{pid}/jobs",
                headers=HEADERS,
                json={"shot_id": shot_id, "provider": "comfyui", "strategy": DEFAULT_STRATEGY.model_dump(mode="json")},
            )
            assert req_zero.status_code == 201
            # Cleanly cancel baseline job so only strat_1 job runs via run_one
            z_job_id = req_zero.json()["id"]
            with factory.begin() as db:
                z_job = db.get(Job, z_job_id)
                assert z_job is not None
                z_job.status = "CANCELLED"

            strat_1 = {
                **DEFAULT_STRATEGY.model_dump(mode="json"),
                "style_loras": [
                    {
                        "lora_id": lora_id,
                        "lora_name": lora_rel_name,
                        "sha256": lora_sha,
                        "strength_model": 0.85,
                        "trigger_word": "impasto_oil",
                    }
                ],
            }
            job_res = client.post(
                f"/api/v1/projects/{pid}/jobs",
                headers=HEADERS,
                json={"shot_id": shot_id, "provider": "comfyui", "strategy": strat_1},
            )
            assert job_res.status_code == 201
            job_id = job_res.json()["id"]
            with factory() as db:
                db_job = db.get(Job, job_id)
                assert db_job is not None
                req_data = db_job.request
            assert "Style LoRA triggers: impasto_oil." in req_data["compiled_prompt"]
            assert req_data["prompt_sha256"] == hashlib.sha256(
                req_data["compiled_prompt"].encode("utf-8")
            ).hexdigest()

            del_busy = client.delete(f"/api/v1/loras/{lora_id}", headers=HEADERS)
            assert del_busy.status_code == 409
            assert del_busy.json()["detail"]["code"] == "LORA_IN_USE"

            comfy_client = ComfyUIClient(server.base_url, timeout_s=2)
            ran = run_one(
                factory,
                worker_id="test-w1",
                settings=settings_comfy,
                comfy_client=comfy_client,
            )
            assert ran == job_id

            with factory() as db:
                job = db.get(Job, job_id)
                assert job is not None
                assert job.status in {"PASSED", "NEEDS_REVIEW", "REPAIR_PENDING"}
                assert job.artifact_id is not None

            assert server.prompt_count == 1
            submitted = server.prompts[list(server.prompts)[0]]["prompt"]
            assert submitted["11"]["class_type"] == "LoraLoaderModelOnly"
            assert submitted["11"]["inputs"]["lora_name"] == "shine_user\\painterly_oil.safetensors"
            assert submitted["11"]["inputs"]["strength_model"] == 0.85
            assert "Style LoRA triggers: impasto_oil." in submitted["31"]["inputs"]["prompt"]
    finally:
        server.stop()

    app.state.engine.dispose()


def test_four_stage_e2e_dual_lora_lowvram_trigger_and_continuity(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "PROFILE_DIR", tmp_path / "profiles")
    app, factory, settings = make_studio(tmp_path)

    lora_a = tmp_path / "src" / "noir_a.safetensors"
    lora_b = tmp_path / "src" / "grain_b.safetensors"
    sha_a, _ = _make_valid_safetensors(lora_a, seed_tag=b"1")
    sha_b, _ = _make_valid_safetensors(lora_b, seed_tag=b"2")

    server = FakeComfyServer().start()
    try:
        with TestClient(app) as client:
            _setup_mock_comfy_with_loras(
                server, ["shine_user/noir_a.safetensors", "shine_user/grain_b.safetensors"]
            )
            settings_m4 = replace(
                settings,
                render_provider="comfyui",
                comfyui_base_url=server.base_url,
                h3_low_vram_attention_token_threshold=1,
            )
            app.state.settings = settings_m4

            with factory.begin() as db:
                row_a, _ = import_style_lora(
                    db, settings_m4, local_path=lora_a, display_name="黑色电影A", trigger_word="noir_rain"
                )
                row_b, _ = import_style_lora(
                    db, settings_m4, local_path=lora_b, display_name="胶片颗粒B", trigger_word="film_grain"
                )
                id_a, id_b = row_a.id, row_b.id

            proj_res = client.post(
                "/api/v1/platform/manual4-projects", headers=HEADERS, json={"title": "四阶双LoRA测试"}
            )
            assert proj_res.status_code == 201
            proj_id = proj_res.json()["id"]

            raw_sb = (
                "第1集：霓虹雨夜（目标总时长：10s）\n"
                "第1镜（5s）：林晚撑伞穿过雨夜霓虹巷口。\n人物：林晚\n"
                "第2镜（5s）：林晚转身望向时钟招牌。\n人物：林晚\n"
            )
            assert client.post(
                f"/api/v1/manual4/projects/{proj_id}/storyboard/import",
                headers=HEADERS,
                data={"text": raw_sb, "filename": "plan.txt"},
            ).status_code in (200, 201)
            assert client.post(
                f"/api/v1/manual4/projects/{proj_id}/storyboard/confirm",
                headers=HEADERS,
                json={"acknowledge_duration_mismatch": False},
            ).status_code == 200

            mat = client.get(f"/api/v1/manual4/projects/{proj_id}/materials/requirements").json()
            req_id = mat["requirements"][0]["id"]
            png_bytes = _minimal_png()
            up_asset = client.post(
                f"/api/v1/manual4/projects/{proj_id}/assets/upload",
                headers=HEADERS,
                data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
                files={"file": ("linwan.png", png_bytes, "image/png")},
            ).json()["asset"]
            prom = client.post(
                f"/api/v1/manual4/projects/{proj_id}/assets/{up_asset['id']}/promote",
                headers=HEADERS,
                json={},
            ).json()
            shots = prom["state"]["shots"]
            s1_id, s2_id = shots[0]["id"], shots[1]["id"]

            assert client.put(
                f"/api/v1/manual4/projects/{proj_id}/shots/{s2_id}/render-options",
                headers=HEADERS,
                json={"continuity_mode": "last_frame"},
            ).status_code == 200

            p1 = "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nNeon alley."
            p2 = "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nContinue turn right."
            assert client.post(
                f"/api/v1/manual4/projects/{proj_id}/shots/{s1_id}/prompt/freeze",
                headers=HEADERS,
                json={"prompt_text": p1},
            ).status_code == 200
            assert client.post(
                f"/api/v1/manual4/projects/{proj_id}/shots/{s2_id}/prompt/freeze",
                headers=HEADERS,
                json={"prompt_text": p2},
            ).status_code == 200

            cfg = {
                "scope_kind": "project",
                "scope_target_ids": [],
                "variant_id": "h3-ref2va-int8-convrot",
                "accelerator_id": "pdd8",
                "resolution_scale": 0.5,
                "second_pass": None,
                "seed": 77,
                "provider": "comfyui",
                "style_loras": [
                    {
                        "lora_id": id_a,
                        "lora_name": "shine_user/noir_a.safetensors",
                        "sha256": sha_a,
                        "strength_model": 0.6,
                        "trigger_word": "noir_rain",
                    },
                    {
                        "lora_id": id_b,
                        "lora_name": "shine_user/grain_b.safetensors",
                        "sha256": sha_b,
                        "strength_model": 0.35,
                        "trigger_word": "film_grain",
                    },
                ],
            }
            snap = client.post(
                f"/api/v1/manual4/projects/{proj_id}/production/snapshot/preview",
                headers=HEADERS,
                json=cfg,
            )
            assert snap.status_code == 200
            assert snap.json()["items"][0]["prompt_sha256"] != hashlib.sha256(p1.encode("utf-8")).hexdigest()

            created = client.post(
                f"/api/v1/manual4/projects/{proj_id}/production/runs",
                headers=HEADERS,
                json=cfg,
            )
            assert created.status_code == 201
            run_id = created.json()["run"]["id"]

            r_del_m4 = client.delete(f"/api/v1/loras/{id_a}", headers=HEADERS)
            assert r_del_m4.status_code == 409
            assert r_del_m4.json()["detail"]["code"] == "LORA_IN_USE"

            m4_factory = app.state.m4_session_factory
            run_info, items_by_shot, _ = _load_run_context(m4_factory, run_id)
            comfy_cli = ComfyUIClient(server.base_url)

            out1 = _execute_single_job(
                m4_factory,
                app.state.m4_root,
                run_info,
                created.json()["run"]["jobs"][0]["id"],
                items_by_shot[s1_id],
                worker_id="m4-w1",
                comfy_client=comfy_cli,
                object_info=server.extra_object_info,
                platform_factory=factory,
                settings=settings_m4,
            )
            assert out1 == "PASSED"
            assert len(server.submitted_prompts) == 1
            submitted = server.submitted_prompts[0]["prompt"]
            assert submitted["15"]["class_type"] == "MiniMaxLowVRAMAttention"
            assert submitted["11"]["inputs"]["model"] == ["10", 0]
            assert submitted["111"]["inputs"]["model"] == ["11", 0]
            assert submitted["12"]["inputs"]["model"] == ["111", 0]
            assert "Style LoRA triggers: noir_rain, film_grain." in submitted["31"]["inputs"]["prompt"]

            out2 = _execute_single_job(
                m4_factory,
                app.state.m4_root,
                run_info,
                created.json()["run"]["jobs"][1]["id"],
                items_by_shot[s2_id],
                worker_id="m4-w1",
                comfy_client=comfy_cli,
                object_info=server.extra_object_info,
                platform_factory=factory,
                settings=settings_m4,
            )
            assert out2 in {"PASSED", "FAILED"}
            assert len(server.submitted_prompts) == 2

            with m4_factory.begin() as m4_db:
                j1 = m4_db.get(M4Job, created.json()["run"]["jobs"][0]["id"])
                assert j1 is not None and j1.status == "PASSED"
                r_done = m4_db.get(M4ProductionRun, run_id)
                assert r_done is not None
                r_done.status = "COMPLETED"

            retake = client.post(
                f"/api/v1/manual4/projects/{proj_id}/shots/{s1_id}/retake",
                headers=HEADERS,
                json=cfg,
            )
            assert retake.status_code == 201
            retake_runs = retake.json()["state"]["runs"]
            retake_id = retake.json()["run"]["id"]
            matched_run = next(r for r in retake_runs if r["id"] == retake_id)
            retake_loras = matched_run["config"]["style_loras"]
            assert [row["lora_id"] for row in retake_loras] == [id_a, id_b]
            assert retake_loras[0]["lora_name"].replace("\\", "/") == "shine_user/noir_a.safetensors"
            assert retake_loras[0]["trigger_word"] == "noir_rain"
    finally:
        server.stop()

    app.state.engine.dispose()

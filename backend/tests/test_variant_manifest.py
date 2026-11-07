"""The versioned H3 variant manifests, the low-end candidates and the W4 slot.

These tests pin the identity discipline:

* every unknown field is declared, and nothing is guessed;
* the candidate variants never inherit the acceptance of the INT8 baseline;
* the W4 slot stays blocked with explicit "unknown" pins until a human
  supplies them;
* the default ready set contains only the audited baseline, and every model
  file it references is pinned in the deployment catalog;
* a candidate catalog carries the runtime closure and the same model closure
  as the baseline, and its size is computed rather than written down;
* regenerating the catalog never lowers its version or drops a profile.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from shine.comfy.capabilities import profile_metadata
from shine.comfyops import deployment, recommendation, variants

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "config" / "deployment"
WORKFLOW_DIR = REPO / "config" / "workflows"
GENERATOR = REPO / "scripts" / "generate_deployment_catalog.py"

BASELINE = "h3-ref2va-int8-convrot"
LOWEND = "ref2va-lowend-candidate"
NVFP4 = "ref2va-nvfp4-lowend"
W4 = "ref2va-w4-convrot"

#: The loaders whose file name identifies a model file in a workflow graph.
MODEL_INPUTS = ("unet_name", "clip_name", "vae_name", "model_name", "lora_name", "model_path")

#: The runtime every catalog needs before a renderer can start.
RUNTIME_COMPONENT_IDS = (
    "comfyui", "comfy-runtime-wheels", "ComfyUI-KJNodes", "comfyui-videohelpersuite",
    "ComfyUI-MiniMax-H3-PDD-Acc", "ComfyUI-SolAttn_triton", "Comfyui_Minimax_h3_latent_Upscaler",
)


def _manifest(variant_id: str) -> dict:
    return variants.load_variant_manifest(variant_id)


def _profile_by_name(name: str) -> dict:
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("name") == name:
            return raw
    raise AssertionError("profile file not found: " + name)


def _model_files(profile: dict) -> set:
    names = set()
    for node in (profile.get("graph") or {}).values():
        inputs = node.get("inputs") or {}
        for key in MODEL_INPUTS:
            value = inputs.get(key)
            if isinstance(value, str) and value:
                names.add(value.replace("\\", "/").split("/")[-1])
    return names


def _catalog_files(catalog: dict) -> dict:
    files = {}
    for component in catalog.get("components", []):
        for entry in component.get("files", []):
            files[entry["relative_path"].replace("\\", "/").split("/")[-1]] = {
                "url": entry.get("url"),
                "sha256": entry.get("sha256"),
                "size": entry.get("size"),
                "revision": component.get("revision"),
                "component_id": component.get("component_id"),
            }
    return files


def _host(vram_mb: int, *, compute_capability: str = "8.6") -> dict:
    return {
        "platform": {"system": "Windows", "machine": "AMD64"},
        "gpus": [{"index": "0", "uuid": "GPU-ONE", "name": "Contract GPU", "driver_version": "1",
                  "vram_total_mb": vram_mb, "vram_used_mb": 0, "vram_free_mb": vram_mb, "selected": True}],
        "gpu": {"status": "OK", "index": "0", "uuid": "GPU-ONE", "name": "Contract GPU",
                "driver_version": "1", "vram_mb": vram_mb, "vram_total_mb": vram_mb,
                "vram_used_mb": 0, "vram_free_mb": vram_mb},
        "cuda": {"status": "OK", "compute_capability": compute_capability},
        "ram": {"status": "OK", "ram_mb": 32768, "ram_available_mb": 32768},
        "commit": {"status": "OK", "commit_total_mb": 65536, "commit_available_mb": 65536,
                   "page_file_status": "PAGE_FILE_PRESENT"},
        "disk": {"status": "OK", "free_gb": 400.0},
    }


def test_every_manifest_declares_its_unknown_fields():
    for manifest in variants.list_variant_manifests():
        found = variants.unknown_paths(manifest)
        declared = variants.declared_unknown_paths(manifest)
        assert found == declared, (manifest["variant_id"], sorted(found ^ declared))
        assert manifest["acceptance_status"] in variants.ACCEPTANCE_STATUSES


def test_only_the_baseline_is_the_default_and_the_only_supported_one():
    manifests = {m["variant_id"]: m for m in variants.list_variant_manifests()}
    assert manifests[BASELINE]["default_recommended"] is True
    assert manifests[BASELINE]["acceptance_status"] == "SUPPORTED"
    for candidate_id in (LOWEND, NVFP4, W4):
        candidate = manifests[candidate_id]
        assert candidate["acceptance_status"] in {"CANDIDATE", "BLOCKED"}
        assert candidate["default_recommended"] is False
        assert candidate["auto_download_allowed"] is False
    assert manifests[W4]["source"] == "THIRD_PARTY_UNVERIFIED"
    assert manifests[NVFP4]["source"] == "THIRD_PARTY_PINNED"
    assert manifests[LOWEND]["source"] == "OFFICIAL_PINNED"


def test_the_candidates_inherit_nothing_from_the_baseline():
    baseline = _manifest(BASELINE)
    for candidate_id in (LOWEND, NVFP4, W4):
        candidate = _manifest(candidate_id)
        assert candidate["variant_id"] != baseline["variant_id"]
        assert candidate["catalog_id"] != baseline["catalog_id"]
        assert candidate["checkpoint_id"] != baseline["checkpoint_id"]
        assert not set(candidate["workflow_profile_ids"]) & set(baseline["workflow_profile_ids"])
        assert not set(candidate["graph_sha256"]) & set(baseline["graph_sha256"])
        assert candidate["acceptance_status"] != baseline["acceptance_status"]
        assert candidate["auto_download_allowed"] is False


def test_the_lowend_candidate_pins_every_official_file():
    candidate = _manifest(LOWEND)
    assert candidate["files"], "the candidate must list the files it would use"
    for entry in candidate["files"]:
        assert entry["revision"] != "unknown"
        assert entry["size"] != "unknown"
        assert entry["sha256"] != "unknown"
        assert entry["url"].startswith("https://")
        assert entry["license"]["id"] and entry["license"]["conditions"]
        assert entry["license"]["jurisdiction_note"]
    sizes = {entry["role"]: entry["size"] for entry in candidate["files"]}
    assert sizes["dit"] == 15983746636
    assert sizes["video_vae"] == 2811065184
    assert sizes["text_encoder"] == 15687142551


def test_the_nvfp4_candidate_pins_the_third_party_file_and_the_official_ones():
    candidate = _manifest(NVFP4)
    files = {entry["role"]: entry for entry in candidate["files"]}
    dit = files["dit"]
    loras = [entry for entry in candidate["files"] if entry["role"] == "lora"]
    official_lora = next(entry for entry in loras if entry["size"] == 1956193000)
    rank21_lora = next(entry for entry in loras if entry["size"] == 326935264)
    assert dit["repository"] == "DmitryDB/MiniMax-H3-ComfyUI-Quants"
    assert dit["revision"] == "06aba4816457400d1dcb12ffd77e37499c8116b1"
    assert dit["size"] == 11663443448
    assert dit["sha256"] == "f970a8a32ded2b4b72f68a624244f8e39d488b1fe0992b9de7133b400dbc95e2"
    assert dit["url"] == ("https://huggingface.co/DmitryDB/MiniMax-H3-ComfyUI-Quants/resolve/"
                          "06aba4816457400d1dcb12ffd77e37499c8116b1/Ref2VA/MiniMax-H3_Ref2VA-NVFP4.safetensors")
    assert dit["license"]["id"] == "minimax-h3-community-license-agreement"
    assert dit["evidence"]["level"] == "A_THIRD_PARTY_REVISION_LISTING"
    assert files["text_encoder"]["size"] == 15687142551
    assert files["video_vae"]["size"] == 2811065184
    assert files["audio_vae"]["size"] == 605254808
    assert official_lora["size"] == 1956193000
    assert official_lora["repository"] == "Comfy-Org/MiniMax-H3"
    # The optional rank-21 LoRA is a different file with a different licence.
    assert rank21_lora["license"]["id"] == "apache-2.0"
    assert rank21_lora["repository"] == "drbaph/MiniMax-H3-Turbo-Lora-ComfyUI"


def test_the_nvfp4_candidate_states_the_publishers_claims_as_claims():
    candidate = _manifest(NVFP4)
    codes = {gap["code"] for gap in candidate["known_evidence_gaps"]}
    assert "PUBLISHER_TABLE_ADA_NOT_COVERED" in codes
    assert "NO_FULL_GENERATION_RUN" in codes
    assert "CHECKPOINT_SIZE_IS_NOT_PEAK_VRAM" in codes
    assert candidate["third_party_claims"], "the publisher's statements are recorded as claims"
    for claim in candidate["third_party_claims"]:
        assert claim["claim"] and claim["source"]
        assert claim["is_project_measurement"] is False
    for item in candidate["runtime_requirements"]:
        assert item["requirement"] and item["source"]
        assert item["is_project_measurement"] is False


def test_the_w4_slot_is_blocked_and_carries_no_invented_identity():
    slot = _manifest(W4)
    assert slot["acceptance_status"] == "BLOCKED"
    assert slot["source"] == "THIRD_PARTY_UNVERIFIED"
    for entry in slot["files"]:
        assert entry["repository"] == "unknown"
        assert entry["revision"] == "unknown"
        assert entry["relative_path"] == "unknown"
        assert entry["size"] == "unknown"
        assert entry["sha256"] == "unknown"
        assert entry["url"] == "unknown"
        assert entry["license"]["id"] == "unknown"
    required = {entry["id"] for entry in slot["required_inputs"]}
    assert required == {
        "TRUSTED_REPOSITORY_URL", "PINNED_REVISION", "EXACT_FILE_NAME", "EXACT_SIZE",
        "SHA256", "LICENSE_TEXT", "REQUIRED_NODE_VERSIONS", "MEASURED_RESOURCE_FLOORS",
    }
    for entry in slot["required_inputs"]:
        assert entry["requirement"] and entry["detail"]


def test_the_twelve_gib_tier_prefers_the_pinned_nvfp4_candidate():
    report = recommendation.recommend_variants(_host(12288))
    assert report["tier"] == "lowend_12gb"
    assert report["recommended"] == []
    by_id = {row["variant_id"]: row for row in report["options"]}
    nvfp4 = by_id[NVFP4]
    assert nvfp4["acceptance_status"] == "CANDIDATE"
    assert nvfp4["status"] != "SUPPORTED"
    assert nvfp4["usable_in_production"] is False
    assert nvfp4["requires_explicit_user_ack"] is True
    gaps = {gap["code"] for gap in nvfp4["evidence_gaps"]}
    assert "PUBLISHER_TABLE_ADA_NOT_COVERED" in gaps
    assert "NO_FULL_GENERATION_RUN" in gaps
    assert "NO_REAL_ACCEPTANCE" in gaps
    assert nvfp4["third_party_claims"]
    assert nvfp4["runtime_requirements"]
    lowend = by_id[LOWEND]
    assert lowend["acceptance_status"] == "CANDIDATE"
    assert lowend["status"] != "SUPPORTED"
    w4 = by_id[W4]
    assert w4["acceptance_status"] == "BLOCKED"
    assert w4["status"] == "BLOCKED"
    assert w4["usable_in_production"] is False
    # The preference order inside the tier: pinned NVFP4, then the official
    # w6a8 candidate, then the named W4 slot that no audited repository has.
    assert [row["variant_id"] for row in report["candidates"]] == [NVFP4, LOWEND, W4]


def test_a_sixteen_gib_machine_gets_the_audited_baseline():
    report = recommendation.recommend_variants(_host(16303))
    assert report["tier"] == "standard"
    assert [row["variant_id"] for row in report["recommended"]] == [BASELINE]


def test_the_default_ready_set_contains_only_the_audited_profiles():
    catalog = deployment.load_default_catalog()
    assert catalog["variant_id"] == BASELINE
    deferred = {entry["name"] for entry in catalog.get("deferred_workflow_profiles") or []}
    assert "local-h3-ref2va-pdd8-upscale-2img" in deferred
    assert "local-h3-ref2va-pdd8-upscale-2img" not in catalog["workflow_profiles"]
    assert LOWEND not in catalog["workflow_profiles"]
    manifest = _manifest(BASELINE)
    assert sorted(manifest["workflow_profile_ids"]) == sorted(catalog["workflow_profiles"])


def test_every_model_file_of_a_default_profile_is_pinned_in_the_catalog():
    catalog = deployment.load_default_catalog()
    pinned = _catalog_files(catalog)
    for name in catalog["workflow_profiles"]:
        profile = _profile_by_name(name)
        for file_name in _model_files(profile):
            assert file_name in pinned, (name, file_name)
            entry = pinned[file_name]
            assert entry["url"].startswith("https://")
            assert entry["revision"]
            assert entry["size"] > 0
            assert len(entry["sha256"]) == 64


def test_every_model_file_of_a_candidate_profile_is_pinned_in_its_catalog():
    for catalog_name, profile_name in [
        ("h3-ref2va-lowend-candidate-windows-x64", "local-h3-ref2va-lowend-pdd-free-2img"),
        ("h3-ref2va-nvfp4-lowend-windows-x64", "local-h3-ref2va-nvfp4-turbo-4step-2img"),
    ]:
        catalog = deployment.load_deployment_catalog(catalog_name)
        pinned = _catalog_files(catalog)
        profile = _profile_by_name(profile_name)
        for file_name in _model_files(profile):
            assert file_name in pinned, (catalog_name, file_name)
            entry = pinned[file_name]
            assert entry["url"].startswith("https://")
            assert entry["revision"]
            assert entry["size"] > 0
            assert len(entry["sha256"]) == 64


def test_a_candidate_catalog_carries_the_runtime_closure_and_no_sol_vae():
    baseline = deployment.load_default_catalog()
    baseline_files = _catalog_files(baseline)
    for catalog_name in ("h3-ref2va-lowend-candidate-windows-x64",
                         "h3-ref2va-nvfp4-lowend-windows-x64"):
        catalog = deployment.load_deployment_catalog(catalog_name)
        component_ids = {component["component_id"] for component in catalog["components"]}
        assert set(RUNTIME_COMPONENT_IDS) <= component_ids
        pinned = _catalog_files(catalog)
        for name in RUNTIME_COMPONENT_IDS:
            for file_name, entry in baseline_files.items():
                if entry["component_id"] == name:
                    assert file_name in pinned, (catalog_name, file_name)
                    assert pinned[file_name]["sha256"] == entry["sha256"]
        # The SOL VAE is not part of the model closure: the baseline ships the
        # video VAE and the audio VAE only, and the SolAttn node pack does not
        # require a SOL VAE file.
        assert "minimax_h3_sol_vae_int8_convrot.safetensors" not in pinned
        assert "minimax-h3-sol-vae-int8" not in component_ids


def test_the_candidate_model_closure_matches_the_baseline_shape():
    baseline = deployment.load_default_catalog()
    baseline_models = {c["component_id"] for c in baseline["components"] if c["kind"] == "model"}
    assert baseline_models == {
        "minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae",
        "minimax-h3-lora", "minimax-h3-pdd",
    }
    catalog = deployment.load_deployment_catalog("h3-ref2va-nvfp4-lowend-windows-x64")
    breakdown = deployment.download_breakdown(catalog)
    # The size is computed from the catalog, never written down.
    assert breakdown["model_bytes"] == sum(
        int(f["size"]) for c in catalog["components"] if c["kind"] == "model" for f in c["files"])
    assert breakdown["model_bytes"] > 0
    assert breakdown["runtime_bytes"] > 0
    assert breakdown["model_bytes"] + breakdown["runtime_bytes"] == breakdown["default_production_bytes"]


def test_the_nvfp4_model_total_is_smaller_than_the_w6a8_one():
    nvfp4 = deployment.download_breakdown(
        deployment.load_deployment_catalog("h3-ref2va-nvfp4-lowend-windows-x64"))
    w6a8 = deployment.download_breakdown(
        deployment.load_deployment_catalog("h3-ref2va-lowend-candidate-windows-x64"))
    assert nvfp4["model_bytes"] < w6a8["model_bytes"]


def test_the_lowend_profiles_have_no_pdd_and_an_explicit_parameter_map():
    for name, checkpoint in [
        ("local-h3-ref2va-lowend-pdd-free-2img", None),
        ("local-h3-ref2va-nvfp4-turbo-4step-2img", "Minimax-H3-Quants\\MiniMax-H3_Ref2VA-NVFP4.safetensors"),
    ]:
        profile = _profile_by_name(name)
        class_types = {node.get("class_type") for node in profile["graph"].values()}
        assert "LoraLoaderModelOnly" not in class_types or name.endswith("turbo-4step-2img")
        assert profile["parameter_map"]
        assert profile["parameter_map"]["reference_subject_1"]
        if checkpoint:
            assert profile["checkpoint_id"] == checkpoint
        assert profile["checkpoint_id"] != _manifest(BASELINE)["checkpoint_id"]


def test_the_nvfp4_profile_uses_four_steps_because_the_turbo_lora_is_loaded():
    profile = _profile_by_name("local-h3-ref2va-nvfp4-turbo-4step-2img")
    class_types = {node.get("class_type") for node in profile["graph"].values()}
    assert "LoraLoaderModelOnly" in class_types
    scheduler = next(node for node in profile["graph"].values()
                     if node.get("class_type") == "BasicScheduler")
    assert scheduler["inputs"]["steps"] == 4
    runtime = profile.get("runtime_requirements") or []
    assert runtime
    for item in runtime:
        assert item["is_project_measurement"] is False


def test_the_baseline_profile_is_verified_and_a_candidate_profile_is_not():
    baseline_profile = _profile_by_name("local-h3-ref2va-pdd8-2img-1aud")
    identity = profile_metadata(baseline_profile["graph"], baseline_profile["mode"],
                                 baseline_profile["parameter_map"], baseline_profile["media_transfers"])
    assert identity["variant_verified"] is True
    assert identity["variant_id"] == BASELINE
    assert identity["variant_acceptance_status"] == "SUPPORTED"

    lowend_profile = _profile_by_name("local-h3-ref2va-lowend-pdd-free-2img")
    candidate = profile_metadata(lowend_profile["graph"], lowend_profile["mode"],
                                 lowend_profile["parameter_map"], lowend_profile["media_transfers"])
    assert candidate["variant_verified"] is False
    assert candidate["variant"] == "unverified"
    assert candidate["precision"] == "unverified"
    assert candidate["variant_id"] != BASELINE

    nvfp4_profile = _profile_by_name("local-h3-ref2va-nvfp4-turbo-4step-2img")
    third_party = profile_metadata(nvfp4_profile["graph"], nvfp4_profile["mode"],
                                   nvfp4_profile["parameter_map"], nvfp4_profile["media_transfers"])
    assert third_party["variant_verified"] is False
    assert third_party["variant_id"] != BASELINE


def test_regenerating_the_catalog_never_lowers_it():
    path = CATALOG_DIR / "h3-ref2va-windows-x64.json"
    before = path.read_text(encoding="utf-8")
    catalog = json.loads(before)
    completed = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    after = path.read_text(encoding="utf-8")
    regenerated = json.loads(after)
    assert regenerated["catalog_version"] == catalog["catalog_version"]
    assert set(regenerated["workflow_profiles"]) >= set(catalog["workflow_profiles"])
    assert {c["component_id"] for c in regenerated["components"]} >= {
        c["component_id"] for c in catalog["components"]}
    assert after == before


def test_the_download_breakdown_separates_the_three_sizes():
    catalog = deployment.load_default_catalog()
    breakdown = deployment.download_breakdown(catalog)
    assert breakdown["complete_catalog_bytes"] == catalog["total_bytes"]
    assert breakdown["default_production_bytes"] < breakdown["complete_catalog_bytes"]
    assert breakdown["optional_review_bytes"] > 0
    assert (breakdown["default_production_bytes"] + breakdown["optional_review_bytes"]
            == breakdown["complete_catalog_bytes"])
    assert set(breakdown["optional_review_components"]) == {"qwen3-vl-8b-fp8", "qwen3-asr-0.6b"}

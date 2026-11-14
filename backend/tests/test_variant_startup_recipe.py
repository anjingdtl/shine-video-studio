"""The variant-level startup recipe contract: declared facts, named refusals.

A managed recipe must carry the floors of the variant it starts.  The W6A8
candidate has no published render-time thresholds, so its contract is a
verifiable CANDIDATE_CONTRACT that refuses a determined startup by name —
it never borrows the INT8 baseline floors, and it never authorizes production.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import HTTPException

from shine.comfyops import variant_recipe
from shine.comfyops.variant_binding import load_binding
from shine.comfyops.variants import default_variant_id
from tests.conftest import create_project

CANDIDATE = "ref2va-lowend-candidate"
LOCAL = {"x-shine-local-action": "1"}


def test_candidate_contract_states_the_pinned_four_weight_stack():
    contract = variant_recipe.build_startup_contract(CANDIDATE)
    stack = {row["role"]: row for row in contract["model_stack"]}
    assert set(stack) >= {"dit", "video_vae", "text_encoder", "lora"}
    assert stack["dit"]["component_id"] == "minimax-h3-diffusion-w6a8"
    assert stack["dit"]["sha256"] == (
        "ece96bbbce76670ec782de84acc888fd9d9b210bd2ab97c1957b39896734f9f1")
    assert stack["video_vae"]["component_id"] == "minimax-h3-vae-int8"
    assert stack["text_encoder"]["component_id"] == "minimax-h3-text-encoder-nvfp4"
    # The graph identity is the variant's own, not the baseline's.
    graph = contract["graphs"][0]
    assert graph["profile_id"] == "local-h3-ref2va-lowend-pdd-free-2img"
    assert graph["graph_matches_manifest"] is True
    assert graph["graph_sha256"] == (
        "596d48912d236314e7bdcf93b676cb9aeabc97cdc0b6c3eaf8e916b1f6c6f715")
    assert "MiniMaxH3ReferenceToVideo" in graph["required_nodes"]
    assert contract["runtime_abi"]["python"] == "3.12"
    assert contract["acceptance_status"] == "CANDIDATE"
    assert contract["parameters_verified_on_variant"] is False


def test_candidate_contract_refuses_a_determined_startup_by_name():
    contract = variant_recipe.build_startup_contract(CANDIDATE)
    admission = contract["admission"]
    assert admission["startup_determined"] is False
    assert admission["status"] == "CANDIDATE_CONTRACT"
    assert admission["startup_floors"] is None
    codes = {blocker["code"] for blocker in admission["blockers"]}
    assert codes == {"VARIANT_STARTUP_THRESHOLDS_UNKNOWN"}
    fields = admission["blockers"][0]["fields"]
    assert fields == ["min_free_vram_mb", "min_free_ram_mb"]
    # The unknown stays an explicit fact everywhere it is reported.
    assert contract["memory_facts"]["min_free_vram_mb"] == "unknown"
    assert contract["memory_facts"]["min_free_ram_mb"] == "unknown"
    missing = {row["field"] for row in contract["missing_upstream_evidence"]}
    assert "acceptance.status" in missing
    assert "requirements.min_free_vram_mb" in missing


def test_baseline_contract_is_determined_with_the_declared_floors():
    contract = variant_recipe.build_startup_contract(default_variant_id())
    admission = contract["admission"]
    assert admission["startup_determined"] is True
    assert admission["status"] == "DECLARED"
    assert admission["blockers"] == []
    # The same render-time available floors the managed recipe has always used.
    assert admission["startup_floors"] == {"min_vram_mb": 12000, "min_ram_mb": 8192}
    assert contract["parameters_verified_on_variant"] is True


def test_contract_identity_binds_every_declared_fact(monkeypatch):
    first = variant_recipe.build_startup_contract(CANDIDATE)
    assert first["contract_sha256"] == variant_recipe.build_startup_contract(CANDIDATE)["contract_sha256"]

    binding = load_binding(CANDIDATE)
    changed_manifest = dict(binding.manifest)
    changed_manifest["requirements"] = {
        **changed_manifest["requirements"],
        "min_free_vram_mb": 9999, "min_free_ram_mb": 4096,
    }
    monkeypatch.setattr(variant_recipe, "load_binding",
                        lambda variant_id: dataclasses.replace(binding, manifest=changed_manifest))
    changed = variant_recipe.build_startup_contract(CANDIDATE)
    assert changed["contract_sha256"] != first["contract_sha256"]
    # A declared threshold makes the startup determined under a NEW identity;
    # the old contract hash can never be reused for the changed facts.
    assert changed["admission"]["startup_determined"] is True
    assert changed["admission"]["startup_floors"] == {"min_vram_mb": 9999, "min_ram_mb": 4096}


def test_unknown_variant_is_refused_by_name():
    with pytest.raises(HTTPException) as exc:
        variant_recipe.build_startup_contract("no-such-variant")
    assert exc.value.status_code == 404
    assert "VARIANT_MANIFEST_UNKNOWN" in str(exc.value.detail)


def test_launch_parameter_legality_is_structural_not_a_hardware_claim():
    params = variant_recipe._launch_parameters()
    assert variant_recipe.validate_launch_parameters(params) == []
    assert variant_recipe.validate_launch_parameters({**params, "listen": "0.0.0.0"})
    assert variant_recipe.validate_launch_parameters({**params, "flags": ["--rm-rf"]})
    assert variant_recipe.validate_launch_parameters({**params, "reserve_vram_gb": -1})
    assert variant_recipe.validate_launch_parameters({**params, "fast": "fp8_everything"})


def test_configuration_refuses_the_candidate_before_writing_any_recipe(studio, monkeypatch):
    client, _factory, _root, settings = studio
    project_id = create_project(client, "候选启动合同")
    # The advisory review-runtime preparation is unrelated to this gate.
    monkeypatch.setattr("shine.comfyops.review_runtime.prepare_review_runtime",
                        lambda settings, catalog: {"status": "TEST_NOOP"})
    response = client.post("/api/v1/setup/configuration", json={
        "project_id": project_id, "variant_id": CANDIDATE}, headers=LOCAL)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VARIANT_STARTUP_CONTRACT_NOT_DETERMINED"
    assert detail["variant_id"] == CANDIDATE
    assert detail["blockers"][0]["code"] == "VARIANT_STARTUP_THRESHOLDS_UNKNOWN"
    assert detail["contract_sha256"]
    assert detail["missing_upstream_evidence"]
    # Nothing was written: no recipe, no runtime candidate.
    assert not (settings.comfyops_dir / "setup" / "managed_recipe.json").exists()


def test_configuration_without_a_variant_keeps_the_default_path(studio, monkeypatch):
    client, _factory, _root, _settings = studio
    project_id = create_project(client, "基线启动路径")
    monkeypatch.setattr("shine.comfyops.review_runtime.prepare_review_runtime",
                        lambda settings, catalog: {"status": "TEST_NOOP"})
    response = client.post("/api/v1/setup/configuration", json={"project_id": project_id},
                           headers=LOCAL)
    # The unchanged baseline behavior: the instance gate, not a variant gate.
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "MANAGED_INSTANCE_REQUIRED"


def test_readonly_contract_endpoint_states_missing_evidence(studio):
    client, _factory, _root, _settings = studio
    response = client.get(f"/api/v1/setup/variant-startup-contract?variant_id={CANDIDATE}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["admission"]["startup_determined"] is False
    assert body["missing_upstream_evidence"]
    missing = client.get("/api/v1/setup/variant-startup-contract?variant_id=no-such-variant")
    assert missing.status_code == 404
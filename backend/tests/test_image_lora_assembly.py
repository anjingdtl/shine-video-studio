import json
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import select

from shine.asset_center.models import LibraryRenderTask
from shine.comfyops import deployment, loras
from shine.comfyops.models import StyleLoraAsset
from tests.conftest import make_studio
from tests.test_style_lora_assembly import _make_valid_safetensors


def test_optional_pack_is_separate_from_required_install_plans():
    from shine.comfyops.setup_preparation import _steps_for

    catalog = deployment.load_deployment_catalog(deployment.IMAGE_LORA_CATALOG)
    ids = {row["component_id"] for row in catalog["components"]}
    assert ids == set(deployment.IMAGE_LORA_COMPONENTS)
    assert sum(file["size"] for row in catalog["components"] for file in row["files"]) == 776_384_068
    assert deployment.optional_only_plan({"packages": [{"id": cid} for cid in ids]})
    for name in (deployment.IMAGE_CAPABILITY_CATALOG, "h3-ref2va-windows-x64"):
        assert ids.isdisjoint(row["component_id"] for row in deployment.load_deployment_catalog(name)["components"])
    assert _steps_for(["image_loras"], verify_runtime=True, deep_scan=False, adopt=True) == ["RECONCILE", "DOWNLOAD"]


def test_optional_pack_does_not_hold_worker_tick_or_claim_twice(tmp_path, monkeypatch):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops import installation_worker
    from shine.comfyops.models import InstallationAttempt, InstallationPlan

    _, factory, settings = make_studio(tmp_path)
    with factory.begin() as db:
        packages = {"packages": [{"id": deployment.IMAGE_LORA_COMPONENTS[0]}]}
        plan = InstallationPlan(payload=packages, sha256=graph_hash(packages), status="APPROVED")
        db.add(plan)
        db.flush()
        attempt = InstallationAttempt(plan_id=plan.id, payload={"executor": "LOCAL_ISOLATED"},
                                      sha256="a" * 64, status="QUEUED", stage="QUEUED")
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id

    started, release = Event(), Event()
    claims = []

    def transfer(_factory, owner, claimed_id, *, settings):
        claims.append((owner, claimed_id))
        started.set()
        release.wait(timeout=3)

    monkeypatch.setattr(installation_worker, "_image_lora_install", None)
    monkeypatch.setattr(installation_worker, "run_attempt", transfer)
    try:
        assert installation_worker.run_pending_installation(factory, "worker", settings=settings) is None
        assert started.wait(timeout=3)
        assert not release.is_set() and installation_worker._image_lora_install.is_alive()
        assert installation_worker.run_pending_installation(factory, "worker", settings=settings) is None
        with factory() as db:
            assert db.get(InstallationAttempt, attempt_id).lease_owner == "worker"
        assert claims == [("worker", attempt_id)]
    finally:
        release.set()
        if installation_worker._image_lora_install is not None:
            installation_worker._image_lora_install.join(timeout=3)


def test_verified_packages_discover_once_and_project_preset_availability(tmp_path, monkeypatch):
    _app, factory, settings = make_studio(tmp_path)
    catalog = deployment.load_deployment_catalog(deployment.IMAGE_LORA_CATALOG)
    packages = settings.comfyops_dir / "isolated-packages"
    for index, component in enumerate(catalog["components"]):
        file, = component["files"]
        path = packages / component["component_id"] / file["relative_path"]
        file["sha256"], file["size"] = _make_valid_safetensors(path, seed_tag=bytes([index]))
    monkeypatch.setattr(loras, "_image_lora_components", lambda: catalog["components"])
    with factory.begin() as db:
        rows = loras.sync_discovered_loras(db, settings, base_model=loras.IMAGE_LORA_BASE)
        assert len(rows) == 4
        assert all(row.base_model == loras.IMAGE_LORA_BASE and row.origin == "discovered" for row in rows)
        angle = db.get(StyleLoraAsset, "qwen-image-2.1-lora-multiple-angles")
        assert (angle.trigger_word, angle.trigger_position) == ("<mva>", "prefix")
        listed = loras.list_style_loras(db, settings, base_model=loras.IMAGE_LORA_BASE, probe_comfy=False)
        assert {row.id for row in db.scalars(select(StyleLoraAsset))} == {row["id"] for row in listed["items"]}
        # Only the live-action and fantasy recipes ship (user ruling, 2026-11-25).
        assert [preset["id"] for preset in listed["presets"]] == [
            "cinematic_photorealism", "dark_fantasy_illustration"]
        cinematic = listed["presets"][0]
        assert {row["status"] for row in cinematic["loras"]} == {"COMFY_UNREACHABLE"}
    yaml = loras.style_lora_extra_paths_file(settings).read_text(encoding="utf-8")
    assert all(cid in yaml for cid in deployment.IMAGE_LORA_COMPONENTS)


def test_image_selection_uses_ledger_metadata_and_fences_active_task(tmp_path):
    _app, factory, settings = make_studio(tmp_path)
    source = tmp_path / "angle.safetensors"
    _make_valid_safetensors(source, seed_tag=b"m")
    with factory.begin() as db:
        image, _ = loras.import_style_lora(db, settings, local_path=source, base_model=loras.IMAGE_LORA_BASE,
                                          trigger_word="<mva>", trigger_position="prefix", negative_trigger_word="过曝")
        request = [{"lora_id": image.id, "strength_model": .8, "trigger_word": "client override"}]
        with pytest.raises(loras.StyleLoraError, match="不适用于") as mismatch:
            loras.evaluate_style_lora_selection(db, settings, request)
        assert mismatch.value.code == "LORA_BASE_MODEL_MISMATCH"
        with pytest.raises(loras.StyleLoraError) as invisible:
            loras.evaluate_style_lora_selection(db, settings, request, base_model=loras.IMAGE_LORA_BASE, object_info={})
        assert invisible.value.code == "LORA_NOT_VISIBLE_IN_COMFYUI"
        spec, = loras.evaluate_style_lora_selection(db, settings, request, base_model=loras.IMAGE_LORA_BASE)
        assert spec.canonical_dict()["trigger_word"] == "<mva>"
        assert spec.canonical_dict()["negative_trigger_word"] == "过曝"
        task = LibraryRenderTask(idempotency_key="style-lock", request_hash="a" * 64, kind="image", mode="I2I",
                                 status="QUEUED", payload={"style_loras": [spec.canonical_dict()]})
        db.add(task)
        db.flush()
        with pytest.raises(loras.StyleLoraError) as in_use:
            loras.delete_style_lora(db, settings, image.id)
        assert in_use.value.code == "LORA_IN_USE"
        assert Path(image.storage_path).is_file()
        task.status = "SUCCEEDED"
        db.flush()
        assert loras.delete_style_lora(db, settings, image.id)["file_deleted"]


def test_shared_loader_chain_edit_negatives_sampling_and_zero_lora_fingerprints():
    from shine.asset_center.contracts import build_qwen_edit_graph, qwen_edit_adapter_hash
    from shine.materialprep import image_prompts, qwen_workflow, runtime
    from shine.materialprep.execution import _build_graph

    assert qwen_workflow.BASE_GRAPH_SHA256 == "83a2bafb67af29f3a8a5a4b4572f19f940ff5f2c31b64b0cef024eb726d42d85"
    assert qwen_edit_adapter_hash("I2I") == "33c35b727f6817045022d8ab55f2398e71ce0b5534a49ca6d25f033dc0b286c9"
    assert qwen_edit_adapter_hash("MULTIREF") == "7543c2b4bc11d7c13a8b6a26d8ab2f44a5d9a1eca5649b44065c63be7ecb875a"
    config = runtime.ImageRuntimeConfig()
    prompt = image_prompts.ImagePrompt("portrait", "modern dress", "v", "forwarded")
    baseline = _build_graph(config, prompt, seed=1, width=512, height=512)
    assert baseline["480"]["inputs"]["model"] == ["451", 0]
    assert (baseline["458"]["inputs"]["steps"], baseline["458"]["inputs"]["cfg"]) == (25, 1.0)
    assert all(node["class_type"] != "LoraLoaderModelOnly" for node in baseline.values())
    specs = [loras.ImageStyleLoraSpec("lora-A", "a.safetensors", "a" * 64, .75),
             loras.ImageStyleLoraSpec("lora-B", "b.safetensors", "b" * 64, .6)]
    sampling = {"steps": 35, "cfg": 3.5, "sampler_name": "euler", "scheduler": "simple"}
    text_graph = _build_graph(config, prompt, seed=1, width=512, height=512, style_loras=specs, style_sampling=sampling)
    edit_graph = build_qwen_edit_graph(prompt=prompt.prompt, negative_prompt=prompt.negative_prompt,
                                      uploaded_names=["a.png", "b.png"], width=512, height=512, seed=1,
                                      style_loras=specs, **sampling)
    assert edit_graph["474"]["inputs"]["negative_prompt"] == "modern dress"
    assert edit_graph["474"]["inputs"]["images.image_2"] == ["701", 0]
    for graph, cache in ((text_graph, "480"), (edit_graph, "469")):
        assert graph["4511"]["inputs"] == {"lora_name": "a.safetensors", "strength_model": .75, "model": ["451", 0]}
        assert graph["4512"]["inputs"] == {"lora_name": "b.safetensors", "strength_model": .6, "model": ["4511", 0]}
        assert graph[cache]["inputs"]["model"] == ["4512", 0]
        assert {key: graph["458"]["inputs"][key] for key in sampling} == sampling


def _import_image_lora(client, tmp_path):
    path = tmp_path / "new-angle.safetensors"
    _make_valid_safetensors(path, seed_tag=b"image-api")
    response = client.post("/api/v1/loras/import", headers={"X-Shine-Local-Action": "1"}, json={
        "local_path": str(path), "base_model": loras.IMAGE_LORA_BASE,
        "trigger_word": "<mva>", "trigger_position": "prefix", "negative_trigger_word": "overexposure",
    })
    assert response.status_code == 201, response.text
    item = response.json()["item"]
    return {"lora_id": item["id"], "sha256": item["sha256"], "strength_model": .8}


def test_asset_center_style_snapshot_retry_and_pixel_cutout_refusal(studio):
    client, factory, tmp_path, _settings = studio
    selection = _import_image_lora(client, tmp_path)
    jobs = "/api/v1/asset-center/image-jobs"
    body = {"idempotency_key": "lora-image-api", "mode": "T2I", "category": "character",
            "prompt": "A person looking at the camera", "negative_prompt": "modern clothing",
            "style_loras": [selection]}
    response = client.post(jobs, json=body)
    assert response.status_code == 202, response.text
    task_id = response.json()["items"][0]["id"]
    replay = client.post(jobs, json=body)
    assert replay.json()["items"][0]["id"] == task_id
    conflict = client.post(jobs, json={**body, "style_loras": [{**selection, "strength_model": .4}]})
    assert conflict.status_code == 409, conflict.text
    cutout = client.post(jobs, json={**body, "mode": "RGBA", "mask_media_id": "mask"})
    assert cutout.json()["detail"]["code"] == "IMAGE_LORA_UNSUPPORTED_FOR_LOCAL_OPERATION"
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        assert task.payload["prompt"] == "<mva> A person looking at the camera"
        assert task.payload["negative_prompt"] == "modern clothing, overexposure"
        task.status = "FAILED"
        original_style = loras.image_style_payload(task.payload)
    updated = client.patch(f"/api/v1/loras/{selection['lora_id']}", headers={"X-Shine-Local-Action": "1"},
                           json={"trigger_word": "new-word"})
    assert updated.status_code == 200, updated.text
    attempt = client.post(f"{jobs}/{task_id}/new-attempt", json={
        "confirmed": True, "idempotency_key": "lora-image-retry",
    })
    assert attempt.status_code == 202, attempt.text
    with factory() as db:
        fresh = db.get(LibraryRenderTask, attempt.json()["id"])
        assert fresh.payload["prompt"] == "<mva> A person looking at the camera"
        assert loras.image_style_payload(fresh.payload) == original_style


def test_project_generate_and_preview_share_snapshot_and_delete_protection(studio):
    from shine.comfyops.models import AssetRequirement
    from shine.materialprep.execution import _compose_prompt
    from shine.materialprep.models import MaterialItem, MaterialRun
    from tests.test_material_director_flow import _analyze, _confirm_material, _document, _project

    client, factory, tmp_path, _settings = studio
    project = _project(client)
    analysis = _analyze(client, project, _document(client, project)).json()
    confirmation = _confirm_material(client, project, analysis)
    rid = confirmation["decisions"][0]["requirement_id"]
    selection = _import_image_lora(client, tmp_path)
    selection_body = {"style_loras": [selection]}
    headers = {"X-Shine-Local-Action": "1"}
    prefix = f"/api/v1/projects/{project}"
    prompt_path = f"{prefix}/director/materials/requirements/{rid}/prompt"
    frozen = client.put(prompt_path, headers=headers, json={
        "prompt": "A rain-soaked old pier", "negative_prompt": "sun",
    })
    assert frozen.status_code == 200, frozen.text
    preview = client.get(prompt_path, params={"style_loras": json.dumps(selection_body["style_loras"])})
    assert preview.status_code == 200, preview.text
    assert preview.json()["prompt"].startswith("<mva> ")
    assert preview.json()["editor_prompt"] == "A rain-soaked old pier"
    generated = client.post(f"{prefix}/material-preparation/generate", headers=headers, json={
        "requirement_ids": [rid], "idempotency_key": "project-image-style",
        **{key: confirmation[key] for key in ("confirmation_id", "confirmation_hash")}, **selection_body,
    })
    assert generated.status_code == 202, generated.text
    with factory() as db:
        data = generated.json()
        item, run = db.get(MaterialItem, data["items"][0]), db.get(MaterialRun, data["run_id"])
        context = {"item_id": item.id, "item_payload": item.payload,
                   "requirement_payload": db.get(AssetRequirement, rid).payload}
        submitted = _compose_prompt(context)
        assert submitted.sha256 == preview.json()["prompt_sha256"]
        assert submitted.negative_prompt == preview.json()["negative_prompt"]
        assert run.payload["style_loras"] == item.payload["style_loras"]
    deletion = client.delete(f"/api/v1/loras/{selection['lora_id']}", headers=headers)
    assert deletion.status_code == 409, deletion.text
    assert deletion.json()["detail"]["code"] == "LORA_IN_USE"

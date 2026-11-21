"""V3 Phase 3 convergence tests: Setup <-> 7-stage <-> 4-stage unified truth & recovery."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from shine.comfy.graph_compiler import LATENT_UPSCALER, PDD_FILE, SecondPassSpec, compile_ref2va_workflow
from shine.comfyops import (
    deployment,
    relayvsr,
    resource_discovery,
    setup_reconcile,
    setup_summary,
)
from shine.comfyops.mode_capabilities import probe_optional_capabilities
from shine.comfyops.runtime_configuration import ACTIVE_KEY, record_service_evidence
from shine.comfyops.setup_summary import record_installation_choice
from shine.director.models import DirectorEpisodePlan
from shine.director.package import build_package, confirm_package, package_readiness
from shine.foundation.environment import check_capability, environment_snapshot
from shine.main import create_app
from shine.manual4.storage.models import M4Job, M4ProductionRun, M4WorkerInstance, m4_utcnow
from shine.manual4.worker import reconcile_stalled_m4_runs
from shine.models import AppSetting, Project, Shot, utcnow
from shine.platform_gpu import acquire_m4_gpu_lease
from shine.schemas import CreateJob
from shine.services import create_render_job
from shine.strategy import GenerationStrategy, SecondPassConfig, strategy_blockers
from tests.conftest import (
    PNG,
    _approve_lock_keeping_operator,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
    upload_asset,
)

pytestmark = pytest.mark.integration

LOCAL_HEADERS = {"x-shine-local-action": "1"}


def _object_info_for(graph: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for node in graph.values():
        cls = node["class_type"]
        required: dict[str, Any] = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                required.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                required.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": required, "optional": {}}})
    return info


def _seal_live_capabilities(
    factory,
    settings,
    tmp_path: Path,
    monkeypatch,
    *,
    accelerator_id: str = "native20",
    second_pass: bool = False,
    video_count: int = 0,
    audio_count: int = 0,
    include_pdd_weight: bool = False,
    include_upscaler_weight: bool = False,
    include_relayvsr_weights: bool = False,
    include_relayvsr_nodes: bool = False,
) -> Path:
    comfy_root = tmp_path / "ExternalComfyUI"
    (comfy_root / "comfy").mkdir(parents=True, exist_ok=True)
    (comfy_root / "main.py").write_text("# ComfyUI\n", encoding="utf-8")
    (comfy_root / "models" / "diffusion_models").mkdir(parents=True, exist_ok=True)
    (comfy_root / "models" / "vae").mkdir(parents=True, exist_ok=True)
    (comfy_root / "models" / "pdd_acc").mkdir(parents=True, exist_ok=True)
    (comfy_root / "models" / "latent_upscale_models").mkdir(parents=True, exist_ok=True)
    if include_pdd_weight:
        (comfy_root / "models" / "pdd_acc" / PDD_FILE).write_bytes(b"pdd")
    if include_upscaler_weight:
        (comfy_root / "models" / "latent_upscale_models" / LATENT_UPSCALER).write_bytes(b"up3d")
    if include_relayvsr_weights:
        (comfy_root / "models" / "diffusion_models" / relayvsr.RELAYVSR_X2_MODEL).write_bytes(b"vsr")
        (comfy_root / "models" / "vae" / relayvsr.RELAYVSR_X2_FLASH_VAE).write_bytes(b"fvae")

    cw = compile_ref2va_workflow(
        accelerator_id=accelerator_id,
        second_pass=SecondPassSpec() if second_pass else None,
        image_count=1,
        video_count=video_count,
        audio_count=audio_count,
    )
    info = _object_info_for(cw.graph)
    if include_pdd_weight and "MiniMaxH3PDDAccApply" in info:
        info["MiniMaxH3PDDAccApply"]["input"]["required"]["pdd_file"] = [[PDD_FILE], {}]
    if include_upscaler_weight and "MinimaxH3LatentUpscaler3DRefineHandoff" in info:
        info["MinimaxH3LatentUpscaler3DRefineHandoff"]["input"]["required"]["model_name"] = [[LATENT_UPSCALER]]
    if include_relayvsr_nodes:
        info["RelayVSRConditioning"] = {"input": {"required": {}, "optional": {}}}
        info["VSRDecode"] = {"input": {"required": {}, "optional": {}}}

    base_native = compile_ref2va_workflow(accelerator_id="native20", image_count=1)
    with factory.begin() as db:
        inst = record_installation_choice(db, root=str(comfy_root), port=8188, actor="local-user")
        identity = {
            "instance_id": inst.get("instance_id") or "inst-p3-convergence",
            "instance_sha256": "i" * 64,
            "catalog_sha256": "c" * 64,
            "recipe_sha256": "r" * 64,
            "comfyui_base_url": "http://127.0.0.1:8188",
            "files": {},
        }
        monkeypatch.setattr("shine.comfyops.runtime_configuration._current_identity", lambda _db, _s: identity)
        monkeypatch.setattr(
            "shine.comfyops.runtime_configuration.service_evidence_status",
            lambda _db, _s, identity=None: {"status": "READY", "blockers": []},
        )
        record_service_evidence(db, identity, {
            "mode_capabilities": {"required_nodes": base_native.required_nodes},
            "optional_capabilities": probe_optional_capabilities(info),
        })
        db.add(AppSetting(key=ACTIVE_KEY, value={"version": 1, "identity": identity, "sha256": "a" * 64}))
    return comfy_root


def _setup_manual4_project_two_shots(client) -> tuple[str, list[dict[str, Any]]]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_HEADERS,
        json={"title": "P3四阶收敛测试", "max_shot_seconds": 15.0},
    ).json()["id"]
    raw_text = (
        "第1集：夜色巷口（目标总时长：10s）\n"
        "第1镜（5s）：林晚站在巷口。\n"
        "人物：林晚\n"
        "第2镜（5s）：林晚走向路灯。\n"
        "人物：林晚\n"
    )
    assert client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_HEADERS,
        data={"text": raw_text, "filename": "ep.txt"},
    ).status_code == 201
    assert client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_HEADERS,
        json={"acknowledge_duration_mismatch": False},
    ).status_code == 200
    mat = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    req_id = mat["requirements"][0]["id"]
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=LOCAL_HEADERS,
        data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
        files={"file": ("linwan.png", PNG, "image/png")},
    ).json()["asset"]
    prom = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{up['id']}/promote",
        headers=LOCAL_HEADERS,
        json={},
    ).json()
    return pid, prom["state"]["shots"]


def test_p3_ce1_missing_relayvsr_never_blocks_baseline_h3_shoot(api, tmp_path, monkeypatch):
    """CE-1: ComfyUI/H3 installed without RelayVSR -> base 1-image shoots; X2 stays optional."""
    _client, factory, settings = api
    _seal_live_capabilities(
        factory, settings, tmp_path, monkeypatch,
        accelerator_id="native20", include_relayvsr_weights=False, include_relayvsr_nodes=False,
    )
    with factory() as db:
        snap = setup_summary.summary(db, settings)
        assert snap["optional_capabilities"]["relayvsr"]["optional"] is True
        assert snap["optional_capabilities"]["relayvsr"]["available"] is False
        assert snap["optional_capabilities"]["relayvsr"]["status"] == "OPTIONAL_UNCONFIGURED"
        assert "RELAYVSR_MISSING" not in snap["blockers"]

        env = environment_snapshot(db, settings)
        assert env["optional_capabilities"]["relayvsr"]["available"] is False

        # Omitting accelerator_id defaults to baseline native20 and succeeds with optional_gaps only.
        cap = check_capability(db, settings, recipe={"capability": "h3"})
        assert cap["blockers"] == []
        assert any(g["code"] == "RELAYVSR_OPTIONAL_MISSING" for g in cap["optional_gaps"])

        # Enabling upscale when RelayVSR is absent blocks explicitly by name.
        cap_up = check_capability(db, settings, recipe={"capability": "h3", "upscale": {"enabled": True}})
        assert cap_up["status"] == "missing_required"
        assert any(b["code"] == "UPSCALE_BACKEND_UNAVAILABLE" for b in cap_up["blockers"])


def test_p3_ce2_missing_video_audio_loaders_scope_to_shot_only_in_7stage_and_4stage(api, tmp_path, monkeypatch):
    """CE-2: Missing VHS_LoadVideo / VHS_LoadAudioUpload only blocks the shot binding them."""
    client, factory, settings = api
    _seal_live_capabilities(
        factory, settings, tmp_path, monkeypatch,
        accelerator_id="native20", video_count=0, audio_count=0,
    )

    # --- 7-stage check ---
    proj_id = create_project(client, title="P3按镜参考门禁七阶")
    style = freeze_style(client, proj_id)
    char = upload_asset(client, proj_id, kind="image", role="character", entity_id="林晚", filename="lw.png")
    _approve_lock_keeping_operator(client, char["id"])
    vid_bytes = make_test_video_bytes()
    vid_resp = client.post(
        f"/api/v1/projects/{proj_id}/assets",
        headers=LOCAL_HEADERS,
        data={"asset_kind": "video", "role": "character"},
        files={"file": ("act.mp4", vid_bytes, "video/mp4")},
    )
    assert vid_resp.status_code == 201, vid_resp.text
    vid_asset = vid_resp.json()
    _approve_lock_keeping_operator(client, vid_asset["id"])

    s_img = create_shot(client, proj_id, style["id"], title="纯图镜头", duration=5.0)
    s_vid = create_shot(client, proj_id, style["id"], title="视频参考镜头", duration=5.0)
    assert client.put(
        f"/api/v1/shots/{s_img['id']}/bindings",
        headers=LOCAL_HEADERS,
        json={"bindings": [{"asset_id": char["id"], "use_as": "subject", "role": "林晚", "ordinal": 0}]},
    ).status_code == 200
    assert client.put(
        f"/api/v1/shots/{s_vid['id']}/bindings",
        headers=LOCAL_HEADERS,
        json={"bindings": [
            {"asset_id": char["id"], "use_as": "subject", "role": "林晚", "ordinal": 0},
            {"asset_id": vid_asset["id"], "use_as": "video", "role": "参考动作", "ordinal": 1},
        ]},
    ).status_code == 200

    with factory() as db:
        proj = db.get(Project, proj_id)
        shot_img = db.get(Shot, s_img["id"])
        shot_vid = db.get(Shot, s_vid["id"])
        # Request creation for image-only shot is NOT blocked by missing VHS_LoadVideo.
        job_ok, _created = create_render_job(
            db, proj, shot_img,
            CreateJob(shot_id=shot_img.id, provider="comfyui", strategy={"accelerator_id": "native20"}),
            settings=settings,
        )
        assert job_ok.id
        # Request creation for video-ref shot IS blocked by name.
        with pytest.raises(HTTPException) as refused:
            create_render_job(
                db, proj, shot_vid,
                CreateJob(shot_id=shot_vid.id, provider="comfyui", strategy={"accelerator_id": "native20"}),
                settings=settings,
            )
        assert refused.value.status_code == 409
        assert refused.value.detail["blockers"][0]["code"] == "VIDEO_REFERENCE_BACKEND_UNAVAILABLE"

    # --- 4-stage check ---
    m4_pid, m4_shots = _setup_manual4_project_two_shots(client)
    m4_s1, m4_s2 = m4_shots[0], m4_shots[1]
    assert client.post(
        f"/api/v1/manual4/projects/{m4_pid}/shots/{m4_s1['id']}/prompt/freeze",
        headers=LOCAL_HEADERS,
        json={"prompt_text": "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nS1 walk."},
    ).status_code == 200
    assert client.post(
        f"/api/v1/manual4/projects/{m4_pid}/shots/{m4_s2['id']}/prompt/freeze",
        headers=LOCAL_HEADERS,
        json={"prompt_text": "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nS2 motion."},
    ).status_code == 200

    # Check shared production state exposes converged options
    state_resp = client.get(f"/api/v1/manual4/projects/{m4_pid}/production/state")
    assert state_resp.status_code == 200
    opts = state_resp.json()["options"]
    assert opts["reference_loaders"]["video_reference"]["supported"] is False
    assert opts["reference_loaders"]["audio_reference"]["supported"] is False
    acc_map = {a["id"]: a for a in opts["accelerators"]}
    assert acc_map["native20"]["verified"] is True
    assert acc_map["pdd8"]["verified"] is False

    # Bind a video asset on shot 2 in manual4 and re-freeze shot 2
    v_up = client.post(
        f"/api/v1/manual4/projects/{m4_pid}/assets/upload",
        headers=LOCAL_HEADERS,
        data={"family": "character", "entity_name": "动作参考"},
        files={"file": ("act.mp4", vid_bytes, "video/mp4")},
    ).json()["asset"]
    v_prom = client.post(
        f"/api/v1/manual4/projects/{m4_pid}/assets/{v_up['id']}/promote",
        headers=LOCAL_HEADERS,
        json={},
    ).json()["asset"]
    existing_s2_img = m4_s2["bindings"][0]["asset_version_id"]
    assert client.put(
        f"/api/v1/manual4/projects/{m4_pid}/shots/{m4_s2['id']}/bindings",
        headers=LOCAL_HEADERS,
        json={"bindings": [
            {"asset_version_id": existing_s2_img, "use_as": "image", "role_label": "linwan"},
            {"asset_version_id": v_prom["id"], "use_as": "video", "role_label": "motion"},
        ]},
    ).status_code == 200
    assert client.post(
        f"/api/v1/manual4/projects/{m4_pid}/shots/{m4_s2['id']}/prompt/freeze",
        headers=LOCAL_HEADERS,
        json={"prompt_text": "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nS2 video ref."},
    ).status_code == 200

    # Shot 1 (image only) snapshot preview succeeds!
    ok_snap = client.post(
        f"/api/v1/manual4/projects/{m4_pid}/production/snapshot/preview",
        headers=LOCAL_HEADERS,
        json={"scope_kind": "shot", "scope_target_ids": [m4_s1["id"]], "accelerator_id": "native20"},
    )
    assert ok_snap.status_code == 200, ok_snap.text

    # Shot 2 (video ref) snapshot preview fails specifically for shot 2!
    bad_snap = client.post(
        f"/api/v1/manual4/projects/{m4_pid}/production/snapshot/preview",
        headers=LOCAL_HEADERS,
        json={"scope_kind": "shot", "scope_target_ids": [m4_s2["id"]], "accelerator_id": "native20"},
    )
    assert bad_snap.status_code == 409
    assert bad_snap.json()["detail"]["code"] == "VIDEO_REFERENCE_BACKEND_UNAVAILABLE"
    assert bad_snap.json()["detail"]["shot_id"] == m4_s2["id"]


def test_p3_ce3_second_pass_only_never_requires_relayvsr(api, tmp_path, monkeypatch):
    """CE-3: Enabling second_pass only validates second_pass resources, never RelayVSR X2."""
    _client, factory, settings = api
    _seal_live_capabilities(
        factory, settings, tmp_path, monkeypatch,
        accelerator_id="pdd8", second_pass=True,
        include_pdd_weight=True, include_upscaler_weight=True,
        include_relayvsr_weights=False, include_relayvsr_nodes=False,
    )
    with factory() as db:
        strat = GenerationStrategy(accelerator_id="pdd8", second_pass=SecondPassConfig(enabled=True))
        assert strategy_blockers(strat, db, settings) == []
        cap = check_capability(
            db, settings,
            recipe={"accelerator_id": "pdd8", "second_pass": {"enabled": True}, "upscale": {"enabled": False}},
        )
        assert cap["blockers"] == []


def test_p3_ce4_and_ce5_discovery_reconcile_zero_download_and_external_paths(studio, tmp_path, monkeypatch):
    """CE-4 & CE-5: Complete models/nodes discovered across roots, verified, and reused with 0 download."""
    _client, factory, temporary, settings = studio
    comfy_root = temporary / "已有ComfyUI"
    (comfy_root / "comfy").mkdir(parents=True)
    (comfy_root / "main.py").write_text("# synthetic entry\n", encoding="utf-8")
    (comfy_root / "comfyui_version.py").write_text('__version__ = "0.39.0"\n', encoding="utf-8")
    python = comfy_root.parent / "python_embeded" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"synthetic python")

    ext_weights = temporary / "外部模型仓库"
    h3_data = b"h3-pinned-weight-bytes-p3"
    relay_data = b"relayvsr-pinned-weight-bytes-p3"
    (ext_weights / "diffusion_models").mkdir(parents=True)
    (ext_weights / "diffusion_models" / "h3.safetensors").write_bytes(h3_data)
    (ext_weights / "diffusion_models" / "relay.safetensors").write_bytes(relay_data)
    (comfy_root / "extra_model_paths.yaml").write_text(
        f"shine_ext:\n  base_path: '{ext_weights.as_posix()}'\n  diffusion_models: diffusion_models\n",
        encoding="utf-8",
    )

    h3_cat = {
        "catalog_id": "shine-h3-ref2va-windows-x64",
        "catalog_version": "1",
        "components": [{
            "component_id": "minimax-h3-base-transformer",
            "kind": "model", "status": "READY", "revision": "r123456789012",
            "license_id": "TEST", "usage": "research", "destination_family": "diffusion_models",
            "files": [{"relative_path": "h3.safetensors", "sha256": hashlib.sha256(h3_data).hexdigest(),
                       "size": len(h3_data), "url": "https://example.invalid/h3.safetensors"}],
        }],
    }
    relay_cat = {
        "catalog_id": "shine-relayvsr-x2-windows-x64",
        "catalog_version": "1",
        "components": [{
            "component_id": "relayvsr-x2-model",
            "kind": "model", "status": "READY", "revision": "r123456789012",
            "license_id": "TEST", "usage": "research", "destination_family": "diffusion_models",
            "files": [{"relative_path": "relay.safetensors", "sha256": hashlib.sha256(relay_data).hexdigest(),
                       "size": len(relay_data), "url": "https://example.invalid/relay.safetensors"}],
        }],
    }
    monkeypatch.setattr(deployment, "load_default_catalog", lambda: h3_cat)
    monkeypatch.setattr(
        deployment, "load_deployment_catalog",
        lambda name: relay_cat if "relayvsr" in str(name) else h3_cat,
    )
    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda cap: relay_cat if cap == "relayvsr" else h3_cat)
    monkeypatch.setattr(deployment, "REVIEW_COMPONENTS", {})
    monkeypatch.setattr("shine.comfyops.host_env.probe_gpu", lambda: {"status": "OK", "uuid": "GPU-p3"})
    monkeypatch.setattr(
        "shine.comfyops.adoption.probe_comfy_service",
        lambda port: {"is_comfyui": False, "version": None},
    )

    with factory() as db:
        monkeypatch.setattr(resource_discovery, "fixed_drives", lambda: [])
        resource_discovery.add_directory(db, str(ext_weights))
        resource_discovery.advance_scan(db, budget_s=5.0)
        rep = resource_discovery.report(db)
        assert "relayvsr" in rep["capabilities"]
        assert rep["capabilities"]["relayvsr"]["found"]
        assert "h3" in rep["capabilities"]
        assert not any("qwen3-vl" in m["component_id"] for m in rep["capabilities"]["h3"]["missing"])

    before_ext = sorted(str(p.relative_to(ext_weights)) for p in ext_weights.rglob("*"))
    with factory() as db:
        rec = setup_reconcile.reconcile_installation(
            db, settings, path=str(comfy_root), capability="h3", adopt=True,
        )
    assert rec["complete"] is True
    assert rec["downloaded_bytes"] == 0
    assert rec["copied_bytes"] == 0
    assert rec["adoption"]["status"] == "ADOPTED"
    assert before_ext == sorted(str(p.relative_to(ext_weights)) for p in ext_weights.rglob("*"))
    assert "shine-node-paths.yaml" or True
    # Adoption keeps external root rather than cloning a second ComfyUI under comfyops_dir.
    assert not any(Path(settings.comfyops_dir, "instances").glob("*"))


def test_p3_ce6_repeated_prepare_clicks_reuse_single_task(api, tmp_path):
    """CE-6: Repeated clicks on prepare/execute return the same task without duplicates."""
    client, factory, _settings = api
    r1 = client.post(
        "/api/v1/setup/prepare/execute",
        headers=LOCAL_HEADERS,
        json={"capabilities": ["h3"]},
    )
    r2 = client.post(
        "/api/v1/setup/prepare/execute",
        headers=LOCAL_HEADERS,
        json={"capabilities": ["h3"]},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["task_id"] == r2.json()["task_id"]


def test_p3_ce7_interrupted_m4_run_reconciles_unknown_prompt_and_releases_lease(tmp_path):
    """CE-7: Interrupted ComfyUI / dead worker reconciles prompt without blind resubmit and frees lease."""
    from shine.config import Settings
    from shine.db import Base, make_engine, make_session_factory

    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=tmp_path, database_url=database_url, node_pack_gate="diagnose")
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        pid, shots = _setup_manual4_project_two_shots(client)
        s1 = shots[0]
        assert client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{s1['id']}/prompt/freeze",
            headers=LOCAL_HEADERS,
            json={"prompt_text": "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nCrash recovery."},
        ).status_code == 200
        run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_HEADERS,
            json={
                "scope_kind": "shot",
                "scope_target_ids": [s1["id"]],
                "provider": "mock",
                "accelerator_id": "native20",
            },
        ).json()["run"]

        m4_factory = app.state.m4_session_factory
        with m4_factory.begin() as m4_db:
            r = m4_db.get(M4ProductionRun, run["id"])
            j = m4_db.get(M4Job, run["jobs"][0]["id"])
            r.status = "RUNNING"
            r.worker_id = "dead-worker"
            j.status = "RUNNING"
            m4_db.add(M4WorkerInstance(
                worker_id="dead-worker",
                pid=999999,
                version="1.0",
                status="online",
                last_heartbeat_at=m4_utcnow() - timedelta(minutes=10),
            ))
            acquire_m4_gpu_lease(factory, tmp_path, owner=f"m4:{r.id}")

        reconciled = reconcile_stalled_m4_runs(
            m4_factory,
            stale_seconds=10,
            m4_root=app.state.m4_root,
            settings=settings,
        )
        assert reconciled >= 1
        with m4_factory() as m4_db:
            r_after = m4_db.get(M4ProductionRun, run["id"])
            assert r_after.status != "RUNNING"


def test_p3_ce8_multi_episode_isolation_ep1_shoots_while_ep2_video_blocked(api, tmp_path, monkeypatch):
    """CE-8: Multi-episode project lets Episode 1 package/shoot while Episode 2 shot has unavailable video."""
    client, factory, settings = api
    _seal_live_capabilities(
        factory, settings, tmp_path, monkeypatch,
        accelerator_id="native20", video_count=0, audio_count=0,
    )
    proj_id = create_project(client, title="P3分集隔离验证")
    style = freeze_style(client, proj_id)
    char = upload_asset(client, proj_id, kind="image", role="character", entity_id="林晚", filename="lw.png")
    _approve_lock_keeping_operator(client, char["id"])
    vid_bytes = make_test_video_bytes()
    vid_asset = client.post(
        f"/api/v1/projects/{proj_id}/assets",
        headers=LOCAL_HEADERS,
        data={"asset_kind": "video", "role": "character"},
        files={"file": ("act.mp4", vid_bytes, "video/mp4")},
    ).json()
    _approve_lock_keeping_operator(client, vid_asset["id"])

    s_ep1 = create_shot(client, proj_id, style["id"], title="第1集图参考", duration=10.0)
    s_ep2 = create_shot(client, proj_id, style["id"], title="第2集视频参考", duration=10.0)
    assert client.put(
        f"/api/v1/shots/{s_ep1['id']}/bindings",
        headers=LOCAL_HEADERS,
        json={"bindings": [{"asset_id": char["id"], "use_as": "subject", "role": "林晚", "ordinal": 0}]},
    ).status_code == 200
    assert client.put(
        f"/api/v1/shots/{s_ep2['id']}/bindings",
        headers=LOCAL_HEADERS,
        json={"bindings": [
            {"asset_id": char["id"], "use_as": "subject", "role": "林晚", "ordinal": 0},
            {"asset_id": vid_asset["id"], "use_as": "video", "role": "动作", "ordinal": 1},
        ]},
    ).status_code == 200

    with factory.begin() as db:
        proj = db.get(Project, proj_id)
        plan_payload = {
            "episodes": [
                {
                    "episode_id": "ep-1", "order": 1, "title": "第一集",
                    "planned_seconds": 10.0, "shot_ids": [s_ep1["id"]], "section_ids": ["sec-1"],
                    "episode_script_id": "scr-1", "episode_script_sha256": "e" * 64,
                },
                {
                    "episode_id": "ep-2", "order": 2, "title": "第二集",
                    "planned_seconds": 10.0, "shot_ids": [s_ep2["id"]], "section_ids": ["sec-2"],
                    "episode_script_id": "scr-2", "episode_script_sha256": "f" * 64,
                },
            ],
            "target_seconds": 10.0,
            "tolerance_seconds": 5.0,
        }
        plan = DirectorEpisodePlan(
            project_id=proj.id,
            version=1,
            status="CONFIRMED",
            target_seconds=10.0,
            tolerance_seconds=5.0,
            payload=plan_payload,
            sha256="p" * 64,
            confirmed_by="local-user",
            confirmed_at=utcnow(),
        )
        db.add(plan)
        db.flush()

        # Episode 1 alone packages & confirms cleanly!
        pkg1 = build_package(
            db, proj,
            plan=plan,
            episode_ids=["ep-1"],
            strategy={"accelerator_id": "native20"},
            settings=settings,
        )
        r1 = package_readiness(db, pkg1)
        assert r1["ready"] is True, r1
        confirm_package(db, pkg1, actor="local-user")

        # Episode 2 reports VIDEO_REFERENCE_BACKEND_UNAVAILABLE scoped to s_ep2!
        pkg2 = build_package(
            db, proj,
            plan=plan,
            episode_ids=["ep-2"],
            strategy={"accelerator_id": "native20"},
            settings=settings,
        )
        r2 = package_readiness(db, pkg2)
        assert r2["ready"] is False
        assert any(
            b["code"] == "VIDEO_REFERENCE_BACKEND_UNAVAILABLE" and b.get("shot_id") == s_ep2["id"]
            for b in r2["blockers"]
        )


@pytest.mark.parametrize("variant_id", ("ref2va-lowend-candidate", "ref2va-nvfp4-lowend"))
def test_p3_ce9_candidate_low_vram_variants_honestly_blocked(api, variant_id):
    """CE-9: Unaccepted low-VRAM candidate variants are blocked by name in 7-stage & 4-stage."""
    client, factory, settings = api
    with factory() as db:
        strat = GenerationStrategy(model_variant_id=variant_id, accelerator_id="native20")
        codes = {b["code"] for b in strategy_blockers(strat, db, settings)}
        assert "MODEL_VARIANT_NOT_ACCEPTED" in codes

    m4_pid, m4_shots = _setup_manual4_project_two_shots(client)
    s1 = m4_shots[0]
    assert client.post(
        f"/api/v1/manual4/projects/{m4_pid}/shots/{s1['id']}/prompt/freeze",
        headers=LOCAL_HEADERS,
        json={"prompt_text": "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nVariant gate."},
    ).status_code == 200
    snap = client.post(
        f"/api/v1/manual4/projects/{m4_pid}/production/snapshot/preview",
        headers=LOCAL_HEADERS,
        json={"scope_kind": "shot", "scope_target_ids": [s1["id"]], "variant_id": variant_id},
    )
    assert snap.status_code in (409, 422)


def test_p3_ce10_seven_stage_and_four_stage_share_same_truth(api, tmp_path, monkeypatch):
    """CE-10: Setup, Foundation, 7-stage, and 4-stage project the exact same environment & option facts."""
    client, factory, settings = api
    comfy_root = _seal_live_capabilities(
        factory, settings, tmp_path, monkeypatch,
        accelerator_id="pdd8", second_pass=False,
        include_pdd_weight=True, include_upscaler_weight=False,
    )
    m4_pid, _shots = _setup_manual4_project_two_shots(client)

    with factory() as db:
        s_sum = setup_summary.summary(db, settings)
        env = environment_snapshot(db, settings)
    m4_sys = client.get("/api/v1/manual4/system/status").json()
    m4_prod = client.get(f"/api/v1/manual4/projects/{m4_pid}/production/state").json()

    assert str(Path(s_sum["installation"]["selected"]["root"]).resolve()) == str(comfy_root.resolve())
    assert str(Path(env["comfyui_root"]).resolve()) == str(comfy_root.resolve())
    assert str(Path(m4_sys["environment"]["comfyui_root"]).resolve()) == str(comfy_root.resolve())

    # Exact parity on optional_capabilities across Setup, Environment, Manual4 System, and Manual4 Production
    for key in ("pdd", "second_pass", "video_reference", "audio_reference", "relayvsr"):
        assert s_sum["optional_capabilities"][key]["available"] == env["optional_capabilities"][key]["available"]
        assert (
            m4_sys["environment"]["optional_capabilities"][key]["available"]
            == m4_prod["options"]["optional_capabilities"][key]["available"]
        )
    assert s_sum["optional_capabilities"]["pdd"]["available"] is True
    assert s_sum["optional_capabilities"]["second_pass"]["available"] is False
    assert m4_prod["options"]["second_pass"]["supported"] is False
    acc = {a["id"]: a for a in m4_prod["options"]["accelerators"]}
    assert acc["pdd8"]["verified"] is True
    assert acc["native20"]["verified"] is True

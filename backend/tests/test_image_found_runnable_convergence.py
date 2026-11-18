"""「文件找到却用不了」收敛轮反例（2026-11-19 用户报障）。

三个分裂链路的回归：
1. SCAN 活锁：整备链的 SCAN 步曾把「检索完成」当成「需要重搜」，与 Worker
   常规推进交替形成活锁，整备永远停在第一步，出图永远「已找到 / 跑不通」。
2. 图层证据不持久：渲染器停止后 readiness 回退「未核验」，卡片在刚证明过
   自己的机器上回退「跑不通」。
3. adopted 只读复用：用户自己的活体 ComfyUI 占着登记端口时，受管启动口
   一律拒绝，IMAGE_VERIFY 与出图执行在这类机器上永远失败。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from shine.comfyops import resource_discovery, setup_preparation
from shine.comfyops.models import ComfyInstance
from shine.materialprep import runtime
from shine.models import AppSetting

LOCAL = {"x-shine-local-action": "1"}


@pytest.fixture
def env(studio):
    client, factory, tmp_path, settings = studio
    return client, factory, tmp_path, settings


def _finished_scan(factory, tmp_path, monkeypatch):
    root = tmp_path / "res"
    root.mkdir(exist_ok=True)
    (root / "sub").mkdir(exist_ok=True)
    monkeypatch.setattr(resource_discovery, "fixed_drives", lambda: [])
    with factory() as db:
        started = resource_discovery.start_scan(db, drives=False, probe_roots=(str(root),))
        assert started["status"] == "QUEUED"
        for _ in range(50):
            if resource_discovery.advance_scan(db, budget_s=5.0)["status"] not in {"QUEUED", "RUNNING"}:
                break
        scan = resource_discovery.read_state(db)["scan"]
        assert scan["status"] == "DONE"
        return started["round_id"]


def test_scan_step_treats_a_finished_round_as_done_and_never_restarts_it(env, monkeypatch):
    """活锁反例：Worker 先把本轮搜完，SCAN 步必须判 DONE，不得重搜全驱。"""
    _, factory, tmp_path, settings = env
    round_id = _finished_scan(factory, tmp_path, monkeypatch)
    task = {"project_id": None, "request": {}, "result": {"scan_round_id": round_id}}
    detail = setup_preparation._step_scan(factory, settings, task)
    assert detail["status"] == "DONE"
    with factory() as db:
        scan = resource_discovery.read_state(db)["scan"]
        assert scan["status"] == "DONE"
        assert not scan.get("queue")
        assert scan.get("round_id") == round_id, "完成的轮次不得被重启"


def test_new_preparation_reuses_completed_coverage_instead_of_rewalking(env, monkeypatch):
    _, factory, tmp_path, settings = env
    _finished_scan(factory, tmp_path, monkeypatch)
    task = {"project_id": None, "request": {}}
    detail = setup_preparation._step_scan(factory, settings, task)
    assert detail["status"] == "DONE"
    with factory() as db:
        scan = resource_discovery.read_state(db)["scan"]
        assert not scan.get("queue"), "复用已完成覆盖，不重走固定盘"
    # 显式重检索入口语义不变：仍然重走已完成目录。
    with factory() as db:
        again = resource_discovery.start_scan(db, drives=False,
                                              probe_roots=(str(tmp_path / "res"),))
        assert again["status"] == "QUEUED"


def test_scan_step_completes_when_its_own_advance_finishes_the_round(env, monkeypatch):
    _, factory, tmp_path, settings = env
    root = tmp_path / "res"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(resource_discovery, "fixed_drives", lambda: [])
    with factory() as db:
        started = resource_discovery.start_scan(db, drives=False, probe_roots=(str(root),))
    task = {"project_id": None, "request": {}}
    for _ in range(50):
        detail = setup_preparation._step_scan(factory, settings, task)
        if detail["status"] == "DONE":
            break
    assert detail["status"] == "DONE"
    assert task["result"]["scan_round_id"] == started["round_id"]
    with factory() as db:
        assert not resource_discovery.read_state(db)["scan"].get("queue")


def test_graph_verification_survives_renderer_stop(env, monkeypatch):
    """图层证据持久化：停止后读同一份事实，卡片不回退「跑不通」。"""
    _, factory, tmp_path, settings = env
    config = runtime.ImageRuntimeConfig(
        instance_id="i1", base_url="http://127.0.0.1:8196", port=8196,
        model_root=str(tmp_path / "models"), recipe_sha256="r" * 64)
    runtime.save_config(settings, config)
    monkeypatch.setattr(runtime, "verify_model_files",
                        lambda config, settings=None: {"files": [], "problems": [], "complete": True})
    monkeypatch.setattr(runtime, "persist_verification", lambda *a, **k: None)
    from shine.comfyops import image_renderer

    monkeypatch.setattr(image_renderer, "image_recipe_state", lambda settings: {
        "state": "READY", "instance_id": "i1", "port": 8196, "recipe_sha256": "r" * 64})
    monkeypatch.setattr(runtime, "_renderer_state",
                        lambda db, settings, iid: {"state": "NOT_STARTED"})
    monkeypatch.setattr(runtime, "_renderer_blocker",
                        lambda db, settings, iid, state=None: None)
    with factory() as db:
        before = runtime.readiness(db, settings, object_info=None)
        assert before["layer"] == runtime.LAYER_FILES_VERIFIED
        assert any(row["code"] == "IMAGE_GRAPH_NOT_VERIFIED" for row in before["blockers"])
        runtime.record_graph_verification(
            db, settings, config,
            {"complete": True, "missing_nodes": [], "missing_inputs": []}, source="test")
        after = runtime.readiness(db, settings, object_info=None)
        assert after["layer"] == runtime.LAYER_GRAPH_VERIFIED
        assert after["graph_report"]["complete"] and after["graph_report"]["persisted"]
        assert not after["blockers"]
    # 换绑（指纹变化）后旧证据如实失效，不冒充新配置已核验。
    moved = config.model_copy(update={"instance_id": "i2", "base_url": "http://127.0.0.1:8197"})
    with factory() as db:
        assert runtime.persisted_graph_verification(db, settings, moved) is None


def _adopted_instance(factory, tmp_path, port=8196):
    with factory.begin() as db:
        db.add(ComfyInstance(
            id="adopt-1",
            payload={"managed": True, "adopted": True, "is_mock": False,
                     "port": port, "root": str(tmp_path)},
            sha256="s" * 64, status="STOPPED"))


def _live_port(monkeypatch, port=8196):
    from shine.comfyops import image_renderer

    monkeypatch.setattr(image_renderer, "windows_port_listeners",
                        lambda p: [("127.0.0.1", 4321)] if p == port else [])

    class Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())


def test_adopted_live_renderer_is_reused_read_only(env, monkeypatch):
    """用户自己的活体 ComfyUI 是这套安装的渲染器：只读复用，不启动不停止。"""
    _, factory, tmp_path, settings = env
    from shine.comfyops import image_renderer

    _adopted_instance(factory, tmp_path)
    _live_port(monkeypatch)
    with factory() as db:
        live = image_renderer.adopted_live_renderer(db, settings, "adopt-1")
    assert live is not None and live["adopted_live"]
    monkeypatch.setattr(image_renderer, "load_image_recipe", lambda settings: {
        "renderer": {"instance_id": "adopt-1", "port": 8196,
                     "adapter_sha256": "a" * 64, "gpu_uuid": "g"}})
    started = image_renderer.start_image_renderer(
        factory, settings, instance_id="adopt-1", actor="test")
    assert started["adopted_live"] is True
    assert started["process"] is None
    with factory() as db:
        assert db.get(AppSetting, image_renderer.image_startup_key("adopt-1")) is None, \
            "只读复用不得写受管启动记录"


def test_non_adopted_instance_never_reuses_an_external_listener(env, monkeypatch):
    _, factory, tmp_path, settings = env
    from shine.comfyops import image_renderer

    with factory.begin() as db:
        db.add(ComfyInstance(id="managed-1",
                             payload={"managed": True, "adopted": False, "is_mock": False,
                                      "port": 8196, "root": str(tmp_path)},
                             sha256="s" * 64, status="STOPPED"))
    _live_port(monkeypatch)
    with factory() as db:
        assert image_renderer.adopted_live_renderer(db, settings, "managed-1") is None


def test_image_verify_step_reuses_adopted_live_and_persists_evidence(env, monkeypatch):
    _, factory, tmp_path, settings = env
    from shine.comfyops import image_renderer

    _adopted_instance(factory, tmp_path)
    _live_port(monkeypatch)
    monkeypatch.setattr(image_renderer, "load_image_recipe", lambda settings: {
        "renderer": {"instance_id": "adopt-1", "port": 8196,
                     "adapter_sha256": "a" * 64, "gpu_uuid": "g"}})
    config = runtime.ImageRuntimeConfig(
        instance_id="adopt-1", base_url="http://127.0.0.1:8196", port=8196,
        recipe_sha256="r" * 64)
    runtime.save_config(settings, config)
    monkeypatch.setattr(runtime, "verify_graph_against_object_info",
                        lambda oi: {"complete": True, "missing_nodes": [], "missing_inputs": []})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def object_info(self):
            return {"SomeNode": {}}

    monkeypatch.setattr("shine.comfy.client.ComfyUIClient", FakeClient)
    monkeypatch.setattr(image_renderer, "stop_image_renderer",
                        lambda *a, **k: pytest.fail("只读复用不得停止用户的渲染器"))
    task = {"target": {"instance_id": "adopt-1", "port": 8196}}
    detail = setup_preparation._step_image_verify(factory, settings, task)
    assert detail["status"] == "DONE", detail
    assert "只读复用" in detail["detail"]
    with factory() as db:
        assert runtime.persisted_graph_verification(db, settings, config) is not None


def test_image_verify_waits_when_the_gpu_is_busy(env, monkeypatch):
    _, factory, tmp_path, settings = env
    from shine.comfyops import image_renderer

    _adopted_instance(factory, tmp_path)

    def busy(*a, **k):
        raise HTTPException(409, {"code": "IMAGE_RENDERER_GPU_LEASE_ACTIVE", "detail": "busy"})

    monkeypatch.setattr(image_renderer, "start_image_renderer", busy)
    task = {"target": {"instance_id": "adopt-1", "port": 8196}}
    detail = setup_preparation._step_image_verify(factory, settings, task)
    assert detail["status"] == "PENDING", "GPU 忙是等待，不是失败"
    assert detail["code"] == "IMAGE_RENDERER_GPU_LEASE_ACTIVE"


def test_summary_projects_preparing_instead_of_blocked_while_task_runs(env, monkeypatch):
    client, factory, _, settings = env
    monkeypatch.setattr(resource_discovery, "fixed_drives", lambda: [])
    response = client.post("/api/v1/setup/prepare/execute",
                           json={"capabilities": ["image"], "verify_runtime": True},
                           headers=LOCAL)
    assert response.status_code == 200, response.text
    summary = client.get("/api/v1/setup/summary").json()
    image = summary["capabilities"]["image"]
    assert image["two_step"]["run"]["state"] == "PREPARING"
    assert "后台整备中" in image["two_step"]["run"]["label"]
    assert summary["preparation"]["capabilities"] == ["image"]

"""顶部唯一机器进度条与整备下载批准的事务边界。

复现过的两件事：
* ``_step_download`` 在不提交的会话里批准安装计划，会话关闭即回滚，
  ``queue_install_attempt`` 读到的仍是 ``AWAITING_APPROVAL``，整备在下载步
  报 ``INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED``（真机 RelayVSR 下载）。
* 基础配置页的进度/回执被锁在「检测中」分支里，后台跑五分钟页面一个字不动。
  现在 ``/setup/summary`` 给出唯一 ``machine_progress`` 投影：只读既有台账
  （整备任务、检索、下载 attempt），100% 只留给唯一裁决的 READY。
"""

from __future__ import annotations

import hashlib

import pytest

from shine.comfyops import deployment, resource_discovery, setup_preparation, setup_summary
from shine.comfyops.models import InstallationAttempt, InstallationPlan
from shine.models import AppSetting

pytestmark = pytest.mark.integration

PAYLOAD = b"\x89H3" + b"weight-bytes-" * 32
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _catalog() -> dict:
    return {
        "catalog_id": "test-catalog",
        "catalog_version": "1.0",
        "components": [{
            "component_id": "minimax-h3-test", "kind": "model", "revision": "r1",
            "license_id": "test-license", "usage": "research",
            "destination_family": "diffusion_models",
            "files": [{"relative_path": "w.safetensors", "sha256": SHA,
                       "size": len(PAYLOAD), "url": "https://example.invalid/w",
                       "mirrors": []}],
        }],
    }


def test_download_step_commits_its_approval_before_queueing(tmp_path, api, monkeypatch, fake_deployment_host):
    client, factory, settings = api
    monkeypatch.setattr(setup_summary, "capability_report", lambda *a, **k: {
        "missing_files": [{"component_id": "minimax-h3-test"}],
        "missing_bytes": len(PAYLOAD),
        "catalog_file": "test-catalog",
    })
    monkeypatch.setattr(setup_summary, "resolve_installation", lambda *a, **k: {"selected": {}})

    import shine.api_setup as api_setup

    def stub_install_plan(body, settings, db):
        plan = deployment.create_install_plan(
            db, settings, _catalog(), ["minimax-h3-test"], body.project_id, body.disk_free)
        return {"plan_id": plan.id, "sha256": plan.sha256, "status": plan.status}

    monkeypatch.setattr(api_setup, "install_plan", stub_install_plan)

    task = {
        "task_id": "setup-testdownload", "project_id": None,
        "request": {"capabilities": ["relayvsr"], "variant_id": None, "installation_path": None},
        "downloads": {}, "steps": [], "result": {},
    }
    detail = setup_preparation._step_download(factory, settings, task)
    assert detail["status"] == "PENDING", detail
    attempt_id = task["downloads"]["relayvsr"]["attempt_id"]
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        plan = db.get(InstallationPlan, attempt.plan_id)
        assert plan.status == "APPROVED", "批准必须落库，不能随会话回滚"
        assert plan.payload["approved_by"] == "worker:setup"
        assert attempt.status in {"QUEUED", "SUBMIT_INTENT"}


def _seed_attempt(factory, *, confirmed: int, total: int) -> None:
    with factory.begin() as db:
        plan = InstallationPlan(payload={"packages": []}, sha256="a" * 64, status="APPROVED")
        db.add(plan)
        db.flush()
        db.add(InstallationAttempt(
            plan_id=plan.id, payload={}, sha256="b" * 64, status="INSTALLING",
            stage="INSTALLING", confirmed_bytes=confirmed, total_bytes=total,
            current_component="relayvsr-x2-diffusion", current_file="model.safetensors"))


def test_machine_progress_reserves_100_for_the_ready_verdict(tmp_path, api):
    client, factory, settings = api
    phases = [{"id": "DETECT", "state": "DONE"}, {"id": "FILES", "state": "DONE"},
              {"id": "REGISTER", "state": "PENDING"}, {"id": "BIND", "state": "PENDING"},
              {"id": "TRIAL", "state": "PENDING"}]
    blocked_verdict = {"mode": "BLOCKED", "label": "暂不可用", "detail": ""}
    ready_verdict = {"mode": "READY", "label": "真实制作已就绪", "detail": ""}
    with factory() as db:
        blocked = setup_summary.machine_progress(db, progress=phases, verdict=blocked_verdict)
        ready = setup_summary.machine_progress(db, progress=phases, verdict=ready_verdict)
    assert blocked["active"] is False
    assert blocked["percent"] == 40
    assert ready["percent"] == 100, "100% 只对应唯一裁决的真实制作已就绪"


def test_machine_progress_follows_the_preparation_download_bytes(tmp_path, api):
    client, factory, settings = api
    _seed_attempt(factory, confirmed=50, total=200)
    with factory.begin() as db:
        db.add(AppSetting(key=setup_preparation.TASK_KEY, value={
            "task_id": "setup-x", "status": "RUNNING", "current": "DOWNLOAD", "done": 2,
            "message": "正在下载并安装：relayvsr（关闭页面也会继续）。",
            "steps": [{"id": "SCAN", "label": "深搜本机已有资源", "status": "DONE"},
                      {"id": "RECONCILE", "label": "核验并登记已有字节", "status": "DONE"},
                      {"id": "DOWNLOAD", "label": "下载并安装缺失文件", "status": "RUNNING"},
                      {"id": "TARGET", "label": "确认本机安装与端口", "status": "PENDING"}],
        }))
    with factory() as db:
        progress = setup_summary.machine_progress(db, progress=[], verdict={"mode": "BLOCKED"})
    assert progress["active"] is True
    assert progress["source"] == "preparation"
    assert progress["label"] == "下载并安装缺失文件"
    assert progress["percent"] == 56, "(2 步 + 0.25 下载) / 4 步"
    assert progress["percent"] < 100
    assert "已下载" in progress["detail"], "长下载必须有实时字节文字，不能看起来像死了"


def test_machine_progress_follows_the_resource_scan_coverage(tmp_path, api):
    client, factory, settings = api
    with factory.begin() as db:
        db.add(AppSetting(key=resource_discovery.STATE_KEY, value={
            "version": 1, "directories": [],
            "scan": {"status": "RUNNING", "queue": [{"root": "c"}],
                     "coverage": {"a": {"status": "DONE"}, "b": {"status": "RUNNING"}},
                     "message": "正在深搜 D 盘"},
            "resources": {"comfy_roots": [], "model_candidates": [], "node_dirs": [], "workflows": []},
        }))
    with factory() as db:
        progress = setup_summary.machine_progress(db, progress=[], verdict={"mode": "BLOCKED"})
    assert progress["active"] is True
    assert progress["source"] == "scan"
    assert progress["percent"] == 33
    assert progress["label"] == "深搜本机已有资源"


def test_summary_exposes_the_single_verdict_and_progress(tmp_path, api):
    client, factory, settings = api
    response = client.get("/api/v1/setup/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["verdict"]) >= {"mode", "label", "detail", "services_verified"}
    assert 0 <= body["machine_progress"]["percent"] <= 100
    assert body["machine_progress"]["source"] in {"preparation", "scan", "download", "idle"}
    verdict = client.get("/api/v1/setup/verdict")
    assert verdict.status_code == 200
    assert verdict.json()["mode"] == body["verdict"]["mode"], "两个入口必须是同一份裁决"

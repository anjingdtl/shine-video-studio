"""四阶受管 ComfyUI 监听归属与启动超时的具名证据（远端报障 2026-11-21）。

远端安装的 ComfyUI venv 由 uv 创建：``python.exe`` 是 trampoline，真实解释器以
Job Object 子进程运行。监听 PID 是子进程，永不等于受管根 PID。旧判定要求
``listener == group.pid`` 且拿根进程 birth 去核对子进程，于是就绪永远不通过，
300 秒后报 M4_COMFY_STARTUP_TIMEOUT（服务器其实已在监听）。七阶渲染器监督器
早已按「监听者自己的 birth + Job 成员身份」裁决；本文件把四阶收敛到同一裁决，
并钉住超时错误必须带失败检查名与最后异常。
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from shine.comfyops.models import ResourceLease
from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.manual4.storage.models import M4RendererState

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership verdict"),
]

ROOT_PID = 1000
CHILD_PID = 2000
FOREIGN_PID = 3000
BIRTHS = {ROOT_PID: "root-birth", CHILD_PID: "child-birth", FOREIGN_PID: "foreign-birth"}


class _FakeGroup:
    """Job-membership semantics: owns() accepts exactly the recorded members."""

    def __init__(self, pid: int, members: dict[int, str]):
        self.pid = pid
        self._members = members

    def owns(self, pid, birth):
        return self._members.get(pid) == birth

    def exit_code_if_stopped(self):
        return None

    def terminate(self, timeout=15):
        return {"active_processes": 0}

    def close(self):
        pass


def _patch_listener(monkeypatch, listener_pid: int):
    from shine.foundation import runtime

    monkeypatch.setattr(runtime, "windows_loopback_listener_pid", lambda _port: listener_pid)
    monkeypatch.setattr(runtime, "process_identity", lambda pid: BIRTHS.get(pid, ""))


def test_trampoline_child_listener_is_owned(monkeypatch):
    from shine.foundation import runtime

    _patch_listener(monkeypatch, CHILD_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth", CHILD_PID: "child-birth"})
    assert runtime.verify_loopback_listener_ownership(8198, group) == CHILD_PID


def test_root_listener_is_still_owned(monkeypatch):
    from shine.foundation import runtime

    _patch_listener(monkeypatch, ROOT_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth"})
    assert runtime.verify_loopback_listener_ownership(8198, group) == ROOT_PID


def test_foreign_listener_is_refused(monkeypatch):
    from shine.foundation import runtime

    _patch_listener(monkeypatch, FOREIGN_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth", CHILD_PID: "child-birth"})
    with pytest.raises(ValueError, match="M4_COMFY_LISTENER_OWNERSHIP_UNVERIFIED"):
        runtime.verify_loopback_listener_ownership(8198, group)


def _setup(tmp_path):
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
    return app, settings


def _fake_spec(tmp_path):
    log_file = tmp_path / "m4" / "logs" / "comfy-m4.log"
    return {
        "source": "managed_recipe",
        "instance_id": "inst-test",
        "python": "python.exe",
        "root": str(tmp_path / "comfy"),
        "adapter_dir": str(tmp_path / "adapter"),
        "adapter_sha256": "0" * 64,
        "bootstrap": "bootstrap.py",
        "runtime_root": str(tmp_path / "m4" / "comfy-runtime"),
        "log_file": str(log_file),
        "port": 8198,
        "argv": ["python.exe", "bootstrap.py"],
        "pinned_files": [],
        "env_base": {},
    }


class _FakeClient:
    def __init__(self, base_url, *, timeout_s=30.0, resume_pid=CHILD_PID):
        self.base_url = base_url
        self._resume_pid = resume_pid

    def system_stats(self, *, timeout_s=None):
        return {"system": {}}

    def handoff_resume(self, token):
        return {"status": "accepting", "protocol": 1, "pid": self._resume_pid}

    def handoff_drain(self, token):
        return {"status": "drained", "protocol": 1, "pid": self._resume_pid}


def _patch_launch(monkeypatch, group, *, resume_pid=CHILD_PID):
    from shine.manual4.comfy import managed_instance

    monkeypatch.setattr(managed_instance, "resolve_m4_comfy_spec",
                        lambda *a, **k: _fake_spec(a[1] if len(a) > 1 else k["m4_root"]))
    monkeypatch.setattr(managed_instance, "retire_idle_director7_image_renderer",
                        lambda *a, **k: None)
    monkeypatch.setattr(managed_instance, "launch_owned_comfyui_group",
                        lambda *a, **k: (group, "root-birth"))
    monkeypatch.setattr(managed_instance, "M4ComfyClient",
                        lambda url, timeout_s=30.0: _FakeClient(url, resume_pid=resume_pid))


def test_trampoline_startup_reaches_running_and_releases(tmp_path, monkeypatch):
    from shine.manual4.comfy import managed_instance

    app, settings = _setup(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    _patch_listener(monkeypatch, CHILD_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth", CHILD_PID: "child-birth"})
    _patch_launch(monkeypatch, group)

    with managed_instance.managed_m4_comfyui(
        pf, mf, settings, tmp_path / "m4", owner="worker-1", startup_timeout_s=5.0
    ) as (client, spec):
        assert spec["port"] == 8198
        with mf() as db:
            state = db.get(M4RendererState, "comfyui-m4")
            assert state.status == "RUNNING"
            assert state.pid == ROOT_PID
            lease_id = state.lease_id
    with mf() as db:
        state = db.get(M4RendererState, "comfyui-m4")
        assert state.status == "STOPPED" and state.lease_id is None
    with pf() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_startup_timeout_names_the_failing_check(tmp_path, monkeypatch):
    from shine.manual4.comfy import managed_instance

    app, settings = _setup(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    _patch_listener(monkeypatch, FOREIGN_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth", CHILD_PID: "child-birth"})
    _patch_launch(monkeypatch, group)

    with pytest.raises(HTTPException) as exc:
        with managed_instance.managed_m4_comfyui(
            pf, mf, settings, tmp_path / "m4", owner="worker-1", startup_timeout_s=0.2
        ):
            pass
    assert exc.value.status_code == 504
    detail = exc.value.detail
    assert detail["code"] == "M4_COMFY_STARTUP_TIMEOUT"
    assert detail["failed_check"] == "listener_ownership"
    assert "M4_COMFY_LISTENER_OWNERSHIP_UNVERIFIED" in detail["last_error"]
    assert detail["log_file"].endswith("comfy-m4.log")
    with mf() as db:
        assert db.get(M4RendererState, "comfyui-m4").status == "STOPPED"


def test_handoff_pid_mismatch_is_named_on_timeout(tmp_path, monkeypatch):
    from shine.manual4.comfy import managed_instance

    app, settings = _setup(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    _patch_listener(monkeypatch, CHILD_PID)
    group = _FakeGroup(ROOT_PID, {ROOT_PID: "root-birth", CHILD_PID: "child-birth"})
    _patch_launch(monkeypatch, group, resume_pid=9999)

    with pytest.raises(HTTPException) as exc:
        with managed_instance.managed_m4_comfyui(
            pf, mf, settings, tmp_path / "m4", owner="worker-1", startup_timeout_s=0.2
        ):
            pass
    detail = exc.value.detail
    assert detail["code"] == "M4_COMFY_STARTUP_TIMEOUT"
    assert detail["failed_check"] == "handoff_resume"
    assert "M4_COMFY_LISTENER_IDENTITY_MISMATCH" in detail["last_error"]
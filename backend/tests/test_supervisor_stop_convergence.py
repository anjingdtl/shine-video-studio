"""supervisor 归入同一条身份核验停止链（用户报障 2026-11-17，远端真机）。

复现：整栈停止/空闲自动退出后，常驻 supervisor 仍活着（它故意不在
processes.json 里），用它自己的 venv 持有 site-packages 的 DLL；下次启动
`uv sync --locked` 要替换 brotli 时 `_brotli.cp312-win_amd64.pyd` 拒绝访问
（os error 5），唯一启动入口停死并被误导向 bootstrap.ps1。

收敛：完整停止由同一个 stop_shine.py 收尾 supervisor（birth 身份；旧记录用
记录端口自己的 health 证明归属）；supervisor 自发起的停止/重启带
SHINE_STOP_KEEP_SUPERVISOR=1 保留自己；启动器残栈收尾认 supervisor.json，
依赖失败给具名指引而不是 bootstrap 误导。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from shine.comfyops.installer import process_identity

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stop_shine = _load("stop_shine")
supervisor = _load("shine_supervisor")


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield proc
    proc.kill()
    proc.wait(timeout=10)


def _record(tmp_path, proc, **extra):
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    payload = {"pid": proc.pid, "port": 1, "data_dir": str(tmp_path),
               "repo_root": str(REPO), **extra}
    (run / "supervisor.json").write_text(json.dumps(payload), encoding="utf-8")
    return run / "supervisor.json"


def test_a_full_stop_finishes_the_recorded_supervisor(tmp_path, sleeper):
    record = _record(tmp_path, sleeper, birth=process_identity(sleeper.pid))
    assert stop_shine.stop_supervisor(tmp_path) == 0
    sleeper.wait(timeout=10)
    assert not record.exists(), "收尾成功后记录才消失"


def test_a_supervisor_initiated_stop_keeps_itself(tmp_path, sleeper, monkeypatch):
    record = _record(tmp_path, sleeper, birth=process_identity(sleeper.pid))
    monkeypatch.setenv("SHINE_STOP_KEEP_SUPERVISOR", "1")
    assert stop_shine._stop_with_manifest(tmp_path) == 0
    assert sleeper.poll() is None, "自发起停止不得杀掉自己"
    assert record.exists()


def test_a_legacy_record_is_stopped_only_with_health_proof(tmp_path, sleeper):
    payload = {"status": "ok", "role": "supervisor", "pid": sleeper.pid,
               "repo_root": str(REPO), "data_dir": str(tmp_path)}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        record = _record(tmp_path, sleeper, port=server.server_address[1])
        assert "birth" not in json.loads(record.read_text(encoding="utf-8"))
        assert stop_shine.stop_supervisor(tmp_path) == 0
        sleeper.wait(timeout=10)
        assert not record.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_a_legacy_record_without_proof_is_refused_and_retained(tmp_path, sleeper):
    record = _record(tmp_path, sleeper, port=1)  # 没有服务在证明归属
    assert stop_shine.stop_supervisor(tmp_path) == 1
    assert sleeper.poll() is None, "证明不了归属就不碰进程"
    assert record.exists(), "记录保留待对账"


def test_supervisor_stop_and_restart_carry_the_keep_marker(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    supervisor._run_stop(tmp_path, "page")
    stop_env = next(kwargs["env"] for args, kwargs in calls
                    if str(args[1]).endswith("stop_shine.py"))
    assert stop_env["SHINE_STOP_KEEP_SUPERVISOR"] == "1"

    calls.clear()
    supervisor._run_restart(tmp_path, 8000, 5173)
    stop_env = next(kwargs["env"] for args, kwargs in calls
                    if str(args[1]).endswith("stop_shine.py"))
    start_env = next(kwargs["env"] for args, kwargs in calls if args[0] == "pwsh")
    assert stop_env["SHINE_STOP_KEEP_SUPERVISOR"] == "1"
    assert start_env["SHINE_STOP_KEEP_SUPERVISOR"] == "1", "重启期间的嵌套收尾不得杀掉驱动重启的 supervisor"


def test_launcher_reconciles_the_supervisor_record_before_dependency_sync():
    text = (SCRIPTS / "start_shine.ps1").read_text(encoding="utf-8-sig")
    assert "$supervisorRecordPath = Join-Path $env:SHINE_DATA_DIR 'run/supervisor.json'" in text
    assert "if ((Test-Path $manifestPath) -or (Test-Path $supervisorRecordPath)) {" in text, \
        "没有 manifest 但有 supervisor 记录时也必须先收尾，再 uv sync"
    assert "本安装仍有进程占用依赖文件" in text, "依赖失败要给具名指引，不是 bootstrap 误导"
    assert "不需要 bootstrap.ps1" in text
    assert "SHINE_STOP_KEEP_SUPERVISOR" not in text, "用户双击启动不带保留标记：残栈 supervisor 必须被收尾"

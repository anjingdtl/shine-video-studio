from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "shine_stop", Path(__file__).resolve().parents[2] / "scripts/stop_shine.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("birth,current,expected", [("old", "new", 1), ("same", "same", 0), ("old", None, 0)])
def test_stop_checks_birth_and_retains_unresolved(tmp_path, birth, current, expected):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "owners": [{"pid": 123, "birth": birth}]}]}))
    calls = []

    class Runner:
        def stop(self, owner):
            calls.append(owner)

    assert module.stop_manifest(path, runner=Runner(), identity=lambda _: current) == expected
    assert bool(calls) is (current == birth)
    assert path.exists() is bool(expected)


def test_legacy_manifest_never_kills_by_pid_alone(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "pid": 123}]}))
    assert module.stop_manifest(path) == 1
    assert path.exists()


def test_stop_accepts_wrapped_root_array_from_windows_powershell_51(tmp_path):
    path = tmp_path / "processes.json"
    owners = [{"pid": 123, "birth": "api"}, {"pid": 456, "birth": "child"}]
    path.write_text(
        json.dumps(
            {"processes": [{"name": "api", "owners": [{"value": owners, "Count": len(owners)}]}]}
        )
    )
    stopped = []

    class Runner:
        def stop(self, owner):
            stopped.append(owner)

    identities = {123: "api", 456: "child"}
    assert module.stop_manifest(path, runner=Runner(), identity=identities.get) == 0
    assert stopped == list(reversed(owners))
    assert not path.exists()


def test_malformed_wrapped_process_identities_are_never_stopped(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(
        json.dumps(
            {
                "processes": [
                    {"name": "api", "owners": [{"value": [{"pid": 123, "birth": "old"}], "Count": 2}]}
                ]
            }
        )
    )
    calls = []

    class Runner:
        def stop(self, owner):
            calls.append(owner)

    assert module.stop_manifest(path, runner=Runner(), identity=lambda _: "old") == 1
    assert calls == []
    assert path.exists()


def test_stop_confirms_exit_when_launcher_child_exits_during_stop(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "owners": [{"pid": 123, "birth": "same"}]}]}))
    readings = iter(["same", None])

    class Runner:
        def stop(self, owner):
            raise ValueError("OWNED_PROCESS_STOP_FAILED")

    assert module.stop_manifest(path, runner=Runner(), identity=lambda _: next(readings)) == 0
    assert not path.exists()


def _unavailable_identity(_pid):
    raise ValueError("LOCK_OWNER_LIVENESS_UNKNOWN")


def test_recycled_pid_is_released_without_touching_the_new_holder(tmp_path):
    """A PID Windows handed to somebody else must not keep the launcher hostage.

    The recorded process is provably gone, so the record is released. The
    process that holds the PID now is never stopped: a reused PID never grants
    authority over its new owner.
    """
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "web", "owners": [{"pid": 123, "birth": "old"}]}]}))
    calls = []

    class Runner:
        def stop(self, owner):
            calls.append(owner)

    assert (
        module.stop_manifest(
            path,
            runner=Runner(),
            identity=_unavailable_identity,
            classify=lambda pid, birth: module.RECYCLED,
        )
        == 0
    )
    assert calls == []
    assert not path.exists()


def test_running_but_unverifiable_pid_is_retained_for_reconciliation(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "owners": [{"pid": 123, "birth": "old"}]}]}))
    calls = []

    class Runner:
        def stop(self, owner):
            calls.append(owner)

    assert (
        module.stop_manifest(
            path,
            runner=Runner(),
            identity=_unavailable_identity,
            classify=lambda pid, birth: module.SAME,
        )
        == 1
    )
    assert calls == []
    assert path.exists()


def test_unclassifiable_pid_is_retained_for_reconciliation(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "owners": [{"pid": 123, "birth": "old"}]}]}))
    calls = []

    class Runner:
        def stop(self, owner):
            calls.append(owner)

    assert (
        module.stop_manifest(
            path,
            runner=Runner(),
            identity=_unavailable_identity,
            classify=lambda pid, birth: module.UNKNOWN,
        )
        == 1
    )
    assert calls == []
    assert path.exists()


def test_launcher_continues_instead_of_bricking_on_an_unreconcilable_record():
    """One double click is the only entry, so an unverifiable record must not
    block it forever. The launcher archives the record and never kills it."""
    text = (Path(__file__).resolve().parents[2] / "scripts/start_shine.ps1").read_text(encoding="utf-8-sig")
    assert "processes.unresolved-" in text, "the launcher must archive the record it cannot reconcile"
    assert "no unverified process was killed" in text
    assert "stop_shine.py" in text, "the launcher must reconcile through the verified stop path"


def test_launcher_recognises_a_half_dead_stack_and_the_package_can_move_ports():
    """The single entry point must never send the browser to a dead web port.

    A stale manifest plus an unrelated process on 8000 used to look like a
    running workbench because only the API health was checked. The launcher now
    verifies the recorded listener identity, waits for the page itself, and the
    packaged start entry can move both ports.
    """
    root = Path(__file__).resolve().parents[2]
    dev = (root / "scripts/start_dev.ps1").read_text(encoding="utf-8-sig")
    shine = (root / "scripts/start_shine.ps1").read_text(encoding="utf-8-sig")
    install = (root / "scripts/install_once.ps1").read_text(encoding="utf-8-sig")
    start_app = (root / "packaging/windows/START_APP.cmd").read_text(encoding="utf-8-sig")

    assert "'start_shine.ps1'" in dev and "-AutoPorts" in dev
    assert "function Test-WebAlive" in shine and "function Test-WebAlive" not in dev
    assert "function Test-RecordedApiAlive" in shine and "function Test-RecordedApiAlive" not in dev
    assert "-AutoPorts" in start_app
    assert "Get-NetTCPConnection" in install
    assert (root / "scripts/update_dev_shortcut.ps1").is_file()


@pytest.mark.windows_only
@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows PowerShell is required")
def test_windows_launcher_resolves_checkout_settings_from_another_directory(tmp_path):
    checkout = tmp_path / "工作台 with spaces"
    checkout.mkdir()
    (checkout / ".env").write_text(
        "SHINE_DATA_DIR='本机数据 data'\nSHINE_BACKEND_ENV='本机工具 python'\n"
        "SHINE_DATABASE_URL=sqlite:///configured.db\n",
        encoding="utf-8",
    )
    script = tmp_path / "paths.ps1"
    script.write_text(
        """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. $env:SHINE_TEST_SETTINGS_HELPER
$configured = Get-ShineStackSettings $env:SHINE_TEST_CHECKOUT
$env:SHINE_DATA_DIR = 'other data'
$overridden = Get-ShineStackSettings $env:SHINE_TEST_CHECKOUT
$explicit = Get-ShineStackSettings $env:SHINE_TEST_CHECKOUT 'explicit data'
@($configured, $overridden, $explicit) | ConvertTo-Json -Depth 3
""",
        encoding="utf-8-sig",
    )
    env = {key: value for key, value in os.environ.items() if key.upper() not in {
        "SHINE_DATA_DIR", "SHINE_DATABASE_URL", "SHINE_BACKEND_ENV", "SHINE_BOOTSTRAP_ROOT",
    }}
    env["SHINE_TEST_SETTINGS_HELPER"] = str(
        Path(__file__).resolve().parents[2] / "scripts/shine_local_config.ps1"
    )
    env["SHINE_TEST_CHECKOUT"] = str(checkout)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=30, check=True,
    )
    configured, overridden, explicit = json.loads(result.stdout)
    assert Path(configured["DataDir"]) == checkout / "本机数据 data"
    assert configured["DatabaseUrl"] == "sqlite:///configured.db"
    assert Path(configured["Python"]) == checkout / "本机工具 python" / "Scripts/python.exe"
    assert Path(overridden["DataDir"]) == checkout / "other data"
    assert overridden["DatabaseUrl"] == f"sqlite:///{(checkout / 'other data/shine.db').as_posix()}"
    assert Path(explicit["DataDir"]) == tmp_path / "explicit data"
    assert explicit["DatabaseUrl"] == f"sqlite:///{(tmp_path / 'explicit data/shine.db').as_posix()}"

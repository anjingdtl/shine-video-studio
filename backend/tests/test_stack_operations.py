from __future__ import annotations

import importlib.util
import json
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


def test_stop_confirms_exit_when_launcher_child_exits_during_stop(tmp_path):
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [{"name": "api", "owners": [{"pid": 123, "birth": "same"}]}]}))
    readings = iter(["same", None])

    class Runner:
        def stop(self, owner):
            raise ValueError("OWNED_PROCESS_STOP_FAILED")

    assert module.stop_manifest(path, runner=Runner(), identity=lambda _: next(readings)) == 0
    assert not path.exists()

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
    text = (Path(__file__).resolve().parents[2] / "scripts/start_dev.ps1").read_text(encoding="utf-8-sig")
    assert "processes.unresolved-" in text, "the launcher must archive the record it cannot reconcile"
    assert "不会盲杀" in text, "the launcher must state that it does not kill unverified processes"
    assert "stop_shine.py" in text, "the launcher must reconcile through the verified stop path"

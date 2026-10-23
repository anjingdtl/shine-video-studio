"""Real lifecycle service, file hashing and command construction, Fake OS/HTTP only."""

# ruff: noqa: F811
import hashlib
import io
import json
import zipfile
from pathlib import Path

from shine.comfyops.installer import ControlledDownloader, LocalInstaller
from shine.comfyops.instances import FakeCommandRunner
from shine.comfyops.local import execute_approved
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, approve, post, prep  # noqa: F401


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    return stream.getvalue()


def install_fixture(prep, name, files, kind="node", **spec_extra):
    client, factory, settings = prep
    project = create_project(client)
    entries = [
        {
            "url": "https://raw.githubusercontent.com/fixture/" + path,
            "relative_path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in files.items()
    ]
    row = post(
        client,
        "/comfyops/packages",
        {
            "name": name,
            "kind": kind,
            "revision": "a" * 40,
            "license_id": "TEST-ONLY",
            "usage": "research",
            "files": entries,
            **spec_extra,
        },
    )
    for path, content in files.items():
        response = client.post(
            f"/api/v1/comfyops/packages/{row['id']}/offline-files",
            headers=HEADERS,
            data={"relative_path": path},
            files={"file": (Path(path).name, content)},
        )
        assert response.status_code == 200, response.text
    plan = post(
        client,
        "/comfyops/install-plans",
        {"project_id": project, "package_ids": [row["id"]], "executor": "local", "disk_free": 10**8},
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")

    class NeverNetwork:
        def stream(self, *args):
            raise AssertionError("Offline cache must not perform HTTP")

    installer = LocalInstaller(
        settings.data_dir / "comfyops" / "isolated-packages",
        ControlledDownloader({"raw.githubusercontent.com"}, NeverNetwork()),
    )
    execute_approved(factory, settings, plan["id"], plan["sha256"], "v13-test-operator", installer)
    return row


def operate(client, project, action, **kwargs):
    plan = post(
        client,
        "/comfyops/instance-plans",
        {"project_id": project, "name": "studio", "action": action, "risk_confirmed": True, **kwargs},
        operator=True,
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    result = post(
        client, f"/comfyops/instance-plans/{plan['id']}/execute", {"plan_hash": plan["sha256"]}, operator=True
    )
    return plan, result


def test_create_dependencies_nodes_binding_owned_start_stop_snapshot_restore(prep):
    client, _, settings = prep
    wheel = archive(
        {
            "fixture_dep-1.0.dist-info/METADATA": "Name: fixture-dep\nVersion: 1.0\n",
            "fixture_dep-1.0.dist-info/WHEEL": "Wheel-Version: 1.0\nTag: py3-none-any\n",
        }
    )
    source = install_fixture(
        prep, "comfy-source", {"main.py": b"# synthetic ComfyUI entrypoint", "requirements.txt": b"fixture-dep>=1.0"}
    )
    deps = install_fixture(prep, "wheels", {"fixture_dep-1.0-py3-none-any.whl": wheel})
    node = install_fixture(prep, "test-node", {"node.zip": archive({"node/__init__.py": "# synthetic node"})})
    model = install_fixture(prep, "model-fixture", {"fixture.bin": b"fake model bytes"}, kind="model")
    project = create_project(client)
    plan, created = operate(client, project, "CREATE", source_package_id=source["id"], package_ids=[deps["id"]])
    assert created["payload"]["is_mock"]
    root = Path(created["payload"]["result"]["root"])
    assert root.is_relative_to(settings.data_dir)
    assert "--hash=sha256:" in (root / ".shine-requirements.txt").read_text()
    runner = client.app.state.instance_runner
    assert isinstance(runner, FakeCommandRunner)
    assert all(isinstance(c, list) for c in runner.calls)
    assert "--no-index" in runner.calls[1] and "--require-hashes" in runner.calls[1]
    operate(client, project, "INSTALL_NODE", package_ids=[node["id"], deps["id"]])
    assert (root / "custom_nodes" / "test-node" / "__init__.py").is_file()
    operate(client, project, "BIND_MODELS", model_bindings={"checkpoints": model["id"]})
    assert "checkpoints" in json.loads((root / "extra_model_paths.yaml").read_text())["shine"]
    _, snapshot = operate(client, project, "SNAPSHOT")
    operate(client, project, "START")
    assert runner.owner
    operate(client, project, "STOP")
    assert runner.owner is None
    operate(client, project, "RESTORE", snapshot_id=snapshot["id"])
    replay = client.post(
        f"/api/v1/comfyops/instance-plans/{plan['id']}/execute", headers=HEADERS, json={"plan_hash": plan["sha256"]}
    )
    assert replay.status_code == 409


def test_lifecycle_denies_missing_auth_wrong_scope_and_unpinned_dependency(prep):
    client, _, _ = prep
    project = create_project(client)
    body = {"project_id": project, "name": "studio", "action": "CREATE", "source_package_id": "unknown"}
    assert client.post("/api/v1/comfyops/instance-plans", json=body).status_code == 403
    post(client, "/comfyops/instance-plans", {**body, "name": "../existing"}, operator=True, expected=422)
    post(client, "/comfyops/instance-plans", {**body, "action": "STOP"}, operator=True, expected=409)
    source = install_fixture(prep, "source-missing-deps", {"main.py": b"# fixture", "requirements.txt": b"missing>=1"})
    plan = post(client, "/comfyops/instance-plans", {**body, "source_package_id": source["id"]}, operator=True)
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    result = post(
        client,
        f"/comfyops/instance-plans/{plan['id']}/execute",
        {"plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )
    assert "PINNED_DEPENDENCY_WHEEL_MISSING" in result["detail"]
    assert not client.app.state.instance_runner.calls


def test_failed_instance_creation_can_be_explicitly_rolled_back_and_reinstalled(prep):
    client, _, settings = prep
    project = create_project(client)
    source = install_fixture(prep, "broken-instance", {"main.py": b"# fake", "requirements.txt": b"missing>=1"})
    plan = post(
        client,
        "/comfyops/instance-plans",
        {"project_id": project, "name": "studio", "action": "CREATE", "source_package_id": source["id"]},
        operator=True,
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    post(
        client,
        f"/comfyops/instance-plans/{plan['id']}/execute",
        {"plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )
    instance = next(i for i in client.get("/api/v1/comfyops/instances").json() if i["payload"].get("name") == "studio")
    assert instance["status"] == "NEEDS_REVIEW"
    _, recovered = operate(client, project, "ROLLBACK")
    assert recovered["payload"]["result"]["tree_hash"] is None
    assert not (settings.data_dir / "comfyops" / "fake-instances" / "studio").exists()
    fixed = install_fixture(prep, "fixed-instance", {"main.py": b"# fake", "requirements.txt": b"# no dependencies"})
    _, ready = operate(client, project, "CREATE", source_package_id=fixed["id"])
    assert ready["status"] == "COMPLETED"


def test_scope_selection_is_subset_bound_one_use_and_persistent(prep, tmp_path):
    from dataclasses import replace

    from shine.comfyops.scope import current_scope

    client, factory, settings = prep
    client.app.state.settings = replace(settings, comfy_probe_roots=(str(tmp_path),), comfy_probe_ports=(8190, 8191))
    project = create_project(client)
    previous = client.get("/api/v1/comfyops/execution-context", headers=HEADERS).json()
    context = {
        "project_id": project,
        "roots": [str(tmp_path / "allowed-child")],
        "ports": [8190],
        "previous_hash": previous["configuration_hash"],
    }
    grant = post(
        client,
        "/preparation/approval-grants",
        {"action": "scope_configure", "target_id": project, "context": context},
        operator=True,
    )
    body = {**context, **{k: grant[k] for k in ("grant_id", "snapshot")}}
    post(client, "/comfyops/scope", body, operator=True)
    post(client, "/comfyops/scope", body, operator=True, expected=409)
    with factory() as db:
        assert current_scope(db, client.app.state.settings)["ports"] == [8190]
    previous = client.get("/api/v1/comfyops/execution-context", headers=HEADERS).json()
    bad = {**context, "roots": [str(tmp_path.parent)], "previous_hash": previous["configuration_hash"]}
    grant = post(
        client,
        "/preparation/approval-grants",
        {"action": "scope_configure", "target_id": project, "context": bad},
        operator=True,
    )
    post(
        client,
        "/comfyops/scope",
        {**bad, **{k: grant[k] for k in ("grant_id", "snapshot")}},
        operator=True,
        expected=403,
    )


def crash_instance_operation(settings, identity, digest, stage):
    """Real process death; only Fake commands/HTTP and temporary fixture files."""
    import os

    from shine.api_instances import fake_http
    from shine.comfyops.instances import execute_operation
    from shine.db import make_engine, make_session_factory

    def interrupt(current):
        if current == stage:
            os._exit(73)

    execute_operation(
        make_session_factory(make_engine(settings.database_url)),
        settings,
        identity,
        digest,
        "v13-test-operator",
        runner=FakeCommandRunner(),
        http=fake_http(),
        checkpoint=interrupt,
    )


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "stage", ["INTENT", "SOURCE_COPIED", "COMMAND_COMPLETED", "FILES_COMPLETE", "BEFORE_COMMIT", "COMMITTED"]
)
def test_instance_process_interruption_reconciles_and_can_reinstall(prep, stage):
    import multiprocessing

    from sqlalchemy import select

    from shine.comfyops.models import InstallationAttempt

    client, factory, settings = prep
    source = install_fixture(
        prep, "crash-source", {"main.py": b"# fixture only", "requirements.txt": b"# no dependencies"}
    )
    project = create_project(client)
    plan = post(
        client,
        "/comfyops/instance-plans",
        {"project_id": project, "name": "studio", "action": "CREATE", "source_package_id": source["id"]},
        operator=True,
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    child = multiprocessing.get_context("spawn").Process(
        target=crash_instance_operation, args=(settings, plan["id"], plan["sha256"], stage)
    )
    child.start()
    child.join(90)
    if child.is_alive():
        child.terminate()  # this test owns only its synthetic child, never a user process
        child.join(5)
    assert child.exitcode == 73
    with factory() as db:
        row = db.scalar(select(InstallationAttempt).where(InstallationAttempt.plan_id == plan["id"]))
        attempt = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    if stage == "COMMITTED":
        assert attempt["status"] == "COMPLETED"
        post(
            client,
            f"/comfyops/instance-attempts/{attempt['id']}/reconcile",
            {"grant_id": "unused", "snapshot": "unused", "evidence": "Completed attempt must not be reconciled"},
            operator=True,
            expected=409,
        )
        operate(client, project, "SNAPSHOT")
    else:
        result = reconcile_crash(client, attempt)
        assert result["status"] == "NEEDS_REVIEW"
        operate(client, project, "ROLLBACK", snapshot_id=attempt["id"])
        operate(client, project, "CREATE", source_package_id=source["id"])


@pytest.mark.parametrize("stage", ["BACKUP_VERIFIED", "STAGED", "OLD_RENAMED", "NEW_RENAMED", "CLEANED"])
def test_restore_process_interruption_preserves_snapshot_and_existing_files(prep, stage):
    import multiprocessing

    from sqlalchemy import select

    from shine.comfyops.models import InstallationAttempt

    client, factory, settings = prep
    source = install_fixture(
        prep, "restore-source", {"main.py": b"# fixture only", "requirements.txt": b"# no dependencies"}
    )
    project = create_project(client)
    _, created = operate(client, project, "CREATE", source_package_id=source["id"])
    _, snapshot = operate(client, project, "SNAPSHOT")
    root = Path(created["payload"]["result"]["root"])
    # An approved restore sees the exact current tree, while keeping the historical snapshot.
    (root / "user-note.txt").write_text("must survive rollback", encoding="utf-8")
    plan = post(
        client,
        "/comfyops/instance-plans",
        {
            "project_id": project,
            "name": "studio",
            "action": "RESTORE",
            "snapshot_id": snapshot["id"],
            "risk_confirmed": True,
        },
        operator=True,
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    child = multiprocessing.get_context("spawn").Process(
        target=crash_instance_operation, args=(settings, plan["id"], plan["sha256"], stage)
    )
    child.start()
    child.join(90)
    if child.is_alive():
        child.terminate()  # this test owns only its synthetic child, never a user process
        child.join(5)
    assert child.exitcode == 73
    with factory() as db:
        row = db.scalar(select(InstallationAttempt).where(InstallationAttempt.plan_id == plan["id"]))
        attempt = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    result = reconcile_crash(client, attempt)
    if stage in {"BACKUP_VERIFIED", "STAGED"}:
        assert result["status"] == "ROLLED_BACK"
    else:
        assert result["status"] == "NEEDS_REVIEW"
        operate(client, project, "ROLLBACK", snapshot_id=attempt["id"])
    assert (root / "user-note.txt").read_text(encoding="utf-8") == "must survive rollback"
    operate(client, project, "SNAPSHOT")


def reconcile_crash(client, attempt):
    grant = post(
        client,
        "/preparation/approval-grants",
        {"action": "instance_reconcile", "target_id": attempt["id"]},
        operator=True,
    )
    return post(
        client,
        f"/comfyops/instance-attempts/{attempt['id']}/reconcile",
        {
            "grant_id": grant["grant_id"],
            "snapshot": grant["snapshot"],
            "evidence": "Child exit verified; inspect filesystem before separately approved rollback",
        },
        operator=True,
    )


def test_external_instance_backup_is_read_only_scoped_and_hash_bound(prep, tmp_path):
    from dataclasses import replace

    client, factory, settings = prep
    root = tmp_path / "existing-production"
    root.mkdir()
    (root / "main.py").write_bytes(b"# never executed")
    (root / "keep.txt").write_text("production files", encoding="utf-8")
    client.app.state.settings = replace(settings, comfy_probe_roots=(str(root),), comfy_probe_ports=(8189,))
    post(client, "/comfyops/inventory/refresh", {"mode": "fake"})
    instance = next(r for r in client.get("/api/v1/comfyops/instances").json() if r["payload"]["url"].endswith(":8189"))
    project = create_project(client)
    body = {
        "project_id": project,
        "instance_id": instance["id"],
        "root": str(root),
        "mode": "fake",
        "risk_confirmed": True,
    }
    post(client, "/comfyops/backup-plans", body, expected=403)
    post(client, "/comfyops/backup-plans", {**body, "root": str(tmp_path)}, operator=True, expected=403)
    plan = post(client, "/comfyops/backup-plans", body, operator=True)
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    post(
        client,
        f"/comfyops/install-plans/{plan['id']}/execute",
        {"executor": "fake", "plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )
    with pytest.raises(ValueError, match="USE_INSTANCE_LIFECYCLE_OR_BACKUP_EXECUTOR"):
        execute_approved(factory, client.app.state.settings, plan["id"], plan["sha256"], "v13-test-operator", None)
    result = post(client, f"/comfyops/backup-plans/{plan['id']}/execute", {"plan_hash": plan["sha256"]}, operator=True)
    assert result["status"] == "SNAPSHOT" and result["payload"]["source_unchanged"]
    backup = Path(result["payload"]["snapshot_path"])
    assert (backup / "keep.txt").read_bytes() == (root / "keep.txt").read_bytes()
    unchanged = next(r for r in client.get("/api/v1/comfyops/instances").json() if r["id"] == instance["id"])
    assert unchanged == instance  # no silent adoption, status change, or managed-instance overwrite
    plan = post(client, "/comfyops/backup-plans", body, operator=True)
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    (root / "keep.txt").write_text("changed after approval", encoding="utf-8")
    post(
        client,
        f"/comfyops/backup-plans/{plan['id']}/execute",
        {"plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )


def test_windows_owned_stop_uses_verified_handle_and_synchronize_right(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    import shine.comfyops.instances as instances

    calls = []

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def times(handle, created, *rest):
        ctypes.cast(created, ctypes.POINTER(ctypes.c_ulonglong))[0] = 123
        return 1

    def open_process(access, inherit, pid):
        assert access & 0x100000  # SYNCHRONIZE is required by WaitForSingleObject
        assert pid == 42 and access & 1
        return 99

    kernel = SimpleNamespace(
        OpenProcess=Function(open_process),
        GetProcessTimes=Function(times),
        TerminateProcess=Function(lambda handle, code: calls.append(("terminate", handle)) or 1),
        WaitForSingleObject=Function(lambda handle, timeout: calls.append(("wait", handle)) or 0),
        CloseHandle=Function(lambda handle: calls.append(("close", handle))),
    )
    with monkeypatch.context() as patch:
        patch.setattr(instances.os, "name", "nt")
        patch.setattr(instances, "process_identity", lambda pid: "123")
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
        instances.ProcessRunner().stop({"pid": 42, "birth": "123"})
        assert calls == [("terminate", 99), ("wait", 99), ("close", 99)]
        calls.clear()
        with pytest.raises(ValueError, match="OWNED_PROCESS_CHANGED"):
            instances.ProcessRunner().stop({"pid": 42, "birth": "other"})
        assert calls == [("close", 99)]


@pytest.mark.parametrize("state, expected", [(0, None), (258, "123"), (0xFFFFFFFF, "UNKNOWN")])
def test_windows_owner_identity_requires_live_process_not_just_creation_time(monkeypatch, state, expected):
    import ctypes
    from types import SimpleNamespace

    import shine.comfyops.installer as installer

    calls = []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def times(handle, created, *rest):
        ctypes.cast(created, ctypes.POINTER(ctypes.c_ulonglong))[0] = 123
        calls.append("birth")
        return 1

    def opened(access, inherit, pid):
        assert access & 0x100000
        return 99

    kernel = SimpleNamespace(
        OpenProcess=Function(opened),
        WaitForSingleObject=Function(lambda *args: state),
        GetProcessTimes=Function(times),
        CloseHandle=Function(lambda handle: calls.append("closed")),
    )
    with monkeypatch.context() as patch:
        patch.setattr(installer.os, "name", "nt")
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
        if expected == "UNKNOWN":
            with pytest.raises(ValueError, match="LOCK_OWNER_LIVENESS_UNKNOWN"):
                installer.process_identity(42)
        else:
            assert installer.process_identity(42) == expected
    assert calls[-1] == "closed"
    if state != 258:
        assert "birth" not in calls

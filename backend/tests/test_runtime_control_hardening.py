"""Final hardening of the unified control plane.

These tests cover the gaps the first acceptance round left open:

* the installation lifecycle guard refuses instead of failing open, and is
  re-entrant within one thread (a stop inside a start's section);
* two lifecycle actions on one physical installation never execute at once,
  and a stale page cannot stop an engine a newer action just started;
* the H3 startup section takes the *physical* installation guard and clears a
  proven-idle image renderer itself, so the "make way" step and the H3 intent
  cannot be separated by a competing image start;
* a global release owns every involved installation's section, in sorted
  order, before it opens its write transaction;
* the snapshot reports what the OS proves, not what a record claims;
* reconcile says what happened (nothing / cleared / needs a human / failed).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException

from shine.comfyops.models import ComfyInstance
from shine.models import AppSetting, WorkerInstance, utcnow
from shine.runtime_orchestrator import executor, guard, operations

pytestmark = pytest.mark.integration

INSTALLATION = "22222222-2222-2222-2222-222222222222"
OTHER_INSTALLATION = "33333333-3333-3333-3333-333333333333"


# --- lifecycle guard ----------------------------------------------------------


class _FakeKernel:
    """A kernel32 stand-in that records calls and returns chosen wait states."""

    def __init__(self, *, create=None, wait=0):
        self.created = 0
        self.released = 0
        self.closed = 0
        self._create = create
        self._wait = wait
        self.CreateMutexW = self._create_mutex
        self.WaitForSingleObject = self._wait_for
        self.ReleaseMutex = self._release
        self.CloseHandle = self._close

    def _create_mutex(self, attributes, initial_owner, name):
        self.created += 1
        return self._create

    def _wait_for(self, handle, milliseconds):
        return self._wait

    def _release(self, handle):
        self.released += 1
        return 1

    def _close(self, handle):
        self.closed += 1
        return 1


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="the lifecycle guard uses a Windows named mutex")
def test_guard_refuses_when_the_mutex_cannot_be_created(monkeypatch):
    fake = _FakeKernel(create=None)
    monkeypatch.setattr(guard, "_kernel32", lambda: fake)
    with pytest.raises(guard.LifecycleGuardUnavailable):
        with guard.renderer_lifecycle_guard(INSTALLATION):
            pytest.fail("a section must never open without its OS lock")


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="the lifecycle guard uses a Windows named mutex")
def test_guard_refuses_when_the_lock_cannot_be_taken(monkeypatch):
    fake = _FakeKernel(create=1234, wait=guard._WAIT_TIMEOUT)
    monkeypatch.setattr(guard, "_kernel32", lambda: fake)
    with pytest.raises(guard.LifecycleGuardBusy):
        with guard.renderer_lifecycle_guard(INSTALLATION):
            pytest.fail("a locked installation must not be entered")
    assert fake.closed == 1


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="the lifecycle guard uses a Windows named mutex")
def test_guard_refuses_an_unexpected_wait_state(monkeypatch):
    fake = _FakeKernel(create=1234, wait=guard._WAIT_FAILED)
    monkeypatch.setattr(guard, "_kernel32", lambda: fake)
    with pytest.raises(guard.LifecycleGuardUnavailable):
        with guard.renderer_lifecycle_guard(INSTALLATION):
            pytest.fail("WAIT_FAILED is not ownership")


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="the lifecycle guard uses a Windows named mutex")
def test_guard_is_reentrant_and_always_closes(monkeypatch):
    fake = _FakeKernel(create=1234, wait=guard._WAIT_OBJECT_0)
    monkeypatch.setattr(guard, "_kernel32", lambda: fake)
    with guard.renderer_lifecycle_guard(INSTALLATION):
        assert guard.guard_is_held(INSTALLATION)
        with guard.renderer_lifecycle_guard(INSTALLATION):
            assert guard.guard_is_held(INSTALLATION)
        assert guard.guard_is_held(INSTALLATION)
    assert not guard.guard_is_held(INSTALLATION)
    # One OS mutex for the outer section, one close, one release: the nested
    # section must not create a second handle or wait on itself.
    assert fake.created == 1
    assert fake.released == 1
    assert fake.closed == 1


def test_guard_name_is_stable_and_installation_scoped():
    assert guard.lifecycle_guard_name(INSTALLATION) == guard.lifecycle_guard_name(INSTALLATION)
    assert guard.lifecycle_guard_name(INSTALLATION) != guard.lifecycle_guard_name(OTHER_INSTALLATION)
    # Same physical installation, different casing/whitespace: same lock.
    assert guard.lifecycle_guard_name(INSTALLATION) == guard.lifecycle_guard_name(" " + INSTALLATION.upper())


# --- operation claiming -------------------------------------------------------


def _live_worker(db, worker_id="w-live"):
    db.add(WorkerInstance(
        worker_id=worker_id, version="test", status="RUNNING",
        last_heartbeat_at=utcnow(), started_at=utcnow(),
    ))
    return worker_id


def test_conflicting_action_is_not_claimed_while_another_runs(api):
    _, factory, _ = api
    with factory.begin() as db:
        owner = _live_worker(db)
        started = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-a", request={"params": {}},
        )
        operations.claim_pending_operations(db, worker_id=owner, worker_birth="b")
        assert started.status == "RUNNING"
        operations.create_or_get_operation(
            db, action="stop", installation_id=INSTALLATION,
            idempotency_key="stop-a", request={"params": {}},
        )
    with factory.begin() as db:
        claimed = operations.claim_pending_operations(db, worker_id="w-other", worker_birth="b2")
    assert [row.action for row in claimed] == [], "a stop must not run while the ensure is live"


def test_probe_is_never_blocked_by_a_lifecycle_action(api):
    _, factory, _ = api
    with factory.begin() as db:
        owner = _live_worker(db)
        operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-b", request={"params": {}},
        )
        operations.claim_pending_operations(db, worker_id=owner, worker_birth="b")
        operations.create_or_get_operation(
            db, action="probe", installation_id=INSTALLATION,
            idempotency_key="probe-b", request={"params": {}},
        )
    with factory.begin() as db:
        claimed = operations.claim_pending_operations(db, worker_id="w-other", worker_birth="b2")
    assert [row.action for row in claimed] == ["probe"]


def test_one_batch_never_claims_two_lifecycle_actions_for_one_installation(api):
    _, factory, _ = api
    with factory.begin() as db:
        operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-c", request={"params": {}},
        )
        operations.create_or_get_operation(
            db, action="stop", installation_id=INSTALLATION,
            idempotency_key="stop-c", request={"params": {}},
        )
    with factory.begin() as db:
        claimed = operations.claim_pending_operations(db, worker_id="w1", worker_birth="b")
    assert len(claimed) == 1
    assert claimed[0].action == "ensure"  # first in, first executed


def test_a_dead_owner_lets_a_new_worker_take_the_action_over(api):
    _, factory, _ = api
    with factory.begin() as db:
        stale = _live_worker(db, "w-stale")
        operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-d", request={"params": {}},
        )
        operations.claim_pending_operations(db, worker_id=stale, worker_birth="old")
        db.get(WorkerInstance, stale).last_heartbeat_at = utcnow() - timedelta(minutes=5)
    with factory.begin() as db:
        claimed = operations.claim_pending_operations(db, worker_id="w-new", worker_birth="new")
    assert [row.action for row in claimed] == ["ensure"]


# --- version (CAS) ------------------------------------------------------------


def test_a_stale_state_version_is_refused(api):
    _, factory, _ = api
    with factory.begin() as db:
        first = operations.create_or_get_operation(
            db, action="probe", installation_id=INSTALLATION,
            idempotency_key="probe-e", request={"params": {}},
        )
        first.status = "SUCCEEDED"
        first.state_version += 1
    with factory() as db:
        current = operations.state_version_for(db, INSTALLATION)
    assert current == 1
    with factory.begin() as db:
        with pytest.raises(HTTPException) as stale:
            operations.create_or_get_operation(
                db, action="ensure", installation_id=INSTALLATION,
                idempotency_key="ensure-e", request={"params": {}},
                expected_state_version=current - 1,
            )
        assert stale.value.detail["code"] == "RUNTIME_STATE_VERSION_STALE"
    with factory.begin() as db:
        accepted = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-e2", request={"params": {}},
            expected_state_version=current,
        )
        assert accepted.status == "PENDING"


def test_the_same_idempotency_key_replays_after_the_state_advanced(api):
    """A double click is one intent even when the first one already finished."""
    _, factory, _ = api
    with factory.begin() as db:
        first = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-f", request={"params": {"reason": "x"}},
            expected_state_version=0,
        )
        first.status = "SUCCEEDED"
        first.state_version += 1
    with factory.begin() as db:
        replay = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-f", request={"params": {"reason": "x"}},
            expected_state_version=0,
        )
        assert replay.id == first.id


def test_two_tabs_cannot_admit_conflicting_actions_at_the_same_version(api):
    client, _, _ = api

    def submit(action):
        return client.post(
            f"/api/v1/runtime-orchestrator/{action}",
            json={"installation_id": INSTALLATION, "idempotency_key": action, "expected_state_version": 0},
            headers={"x-shine-local-action": "1"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ensure, stop = pool.map(submit, ("ensure", "stop"))
    accepted, rejected = sorted((ensure, stop), key=lambda response: response.status_code)
    assert accepted.status_code == 202
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "RUNTIME_OPERATION_IN_PROGRESS"
    replay = submit(accepted.json()["action"])
    assert replay.json()["operation_id"] == accepted.json()["operation_id"]


def test_stop_is_refused_at_admission_while_a_real_job_owns_the_engine(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(
        "shine.runtime_orchestrator.identity.in_flight_image_work",
        lambda db: [{"kind": "library_task", "id": "real-task"}],
    )
    response = client.post(
        "/api/v1/runtime-orchestrator/stop",
        json={"installation_id": INSTALLATION, "idempotency_key": "stop-busy", "expected_state_version": 0},
        headers={"x-shine-local-action": "1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUNTIME_WORK_IN_FLIGHT"
    assert response.json()["detail"]["work"][0]["id"] == "real-task"


def test_reclaimed_stop_checks_evidence_without_stopping_again(api, monkeypatch):
    _, factory, settings = api
    with factory.begin() as db:
        owner = _live_worker(db)
        operation = operations.create_or_get_operation(
            db, action="stop", installation_id=INSTALLATION, idempotency_key="interrupted", request={},
        )
        operations.claim_pending_operations(db, worker_id=owner, worker_birth="old")
        db.get(WorkerInstance, owner).last_heartbeat_at = utcnow() - timedelta(minutes=5)
    monkeypatch.setattr(executor, "_execute_operation", lambda *a: pytest.fail("replayed a stale stop"))
    assert executor.advance_control_operations(factory, settings, worker_id="w-new", worker_birth="new") == 1
    with factory() as db:
        saved = operations.get_operation(db, operation.id)
        assert saved.status == "REFUSED"
        assert saved.failure_code == "RUNTIME_OPERATION_INTERRUPTED"
        assert saved.result["outcome"] == executor.OUTCOME_NEEDS_REVIEW


# --- H3 startup coordination --------------------------------------------------


def _image_record(db, *, stage="RUNNING", installation_id=INSTALLATION, **extra):
    value = {
        "stage": stage,
        "instance_id": installation_id,
        "purpose": "image",
        "port": 8188,
        "owner_pid": 4,
        "owner_birth": "dead",
        "process": {"pid": 999999, "birth": "nope", "job_name": "Local\\ShineHandoff-" + "0" * 32},
        "adapter_sha256": "a" * 64,
    }
    value.update(extra)
    db.add(AppSetting(key="renderer-startup:image:" + installation_id, value=value))


def test_h3_start_retires_a_proven_idle_image_renderer(api, monkeypatch):
    from shine.comfyops import handoff_startup

    _, factory, settings = api
    with factory.begin() as db:
        _image_record(db, stage="RUNNING")
    calls = []

    def fake_retire(factory_, settings_, installation_id, actor="x"):
        calls.append(installation_id)
        return {"status": "STOPPED"}

    monkeypatch.setattr("shine.comfyops.image_renderer.retire_idle_image_renderer", fake_retire)
    from shine.runtime_orchestrator import identity

    with factory() as db:
        record = identity.image_record_for_installation(db, INSTALLATION)
    monkeypatch.setitem(record, "live_state", "RUNNING")
    monkeypatch.setattr(identity, "image_record_for_installation", lambda db, iid: record)
    handoff_startup._make_way_for_h3(factory, settings, INSTALLATION)
    assert calls == [INSTALLATION]


def test_h3_start_refuses_an_unverifiable_image_renderer(api, monkeypatch):
    from shine.comfyops import handoff_startup
    from shine.runtime_orchestrator import identity

    _, factory, settings = api
    record = {"key": "k", "state": "ACTIVE", "live_state": "UNVERIFIED", "stage": "RUNNING"}
    monkeypatch.setattr(identity, "image_record_for_installation", lambda db, iid: record)
    with pytest.raises(HTTPException) as refused:
        handoff_startup._make_way_for_h3(factory, settings, INSTALLATION)
    assert refused.value.detail["code"] == "COMFY_INSTALLATION_IMAGE_RENDERER_UNVERIFIED"


def test_h3_start_takes_the_physical_installation_guard(api, monkeypatch):
    """The section is keyed by installation id, never by the per-attempt run id."""
    from shine.comfyops import handoff_startup

    seen = []

    class _Stop(Exception):
        pass

    def fake_guard(installation_id, timeout_s=30.0):
        seen.append(installation_id)
        raise _Stop()

    monkeypatch.setattr(
        "shine.runtime_orchestrator.guard.renderer_lifecycle_guard", fake_guard,
    )
    _, factory, settings = api
    with factory.begin() as db:
        db.add(ComfyInstance(id=INSTALLATION, payload={"managed": True}, sha256="x", status="STOPPED"))
    with pytest.raises(_Stop):
        handoff_startup.start_managed_renderer(
            factory, settings,
            instance_id="run-identity-not-the-lock-key",
            lease_id="lease-1", job_id="job-1", worker_id="w",
            argv=["python", "main.py"], cwd=str(settings.data_dir), env={},
            pinned_files=[], port=8188, handoff_token="t" * 40,
            adapter_sha256="a" * 64, gpu_uuid="GPU-x", min_ram_mb=1, min_vram_mb=1,
            installation={"id": INSTALLATION, "sha256": "x", "attempt_id": "a1"},
        )
    assert seen == [INSTALLATION]


# --- stop / release serialization ---------------------------------------------


def test_image_stop_takes_the_installation_guard(api, monkeypatch):
    from shine.comfyops import image_renderer

    seen = []

    class _Stop(Exception):
        pass

    def fake_guard(installation_id, timeout_s=30.0):
        seen.append(installation_id)
        raise _Stop()

    monkeypatch.setattr("shine.runtime_orchestrator.guard.renderer_lifecycle_guard", fake_guard)
    _, factory, settings = api
    with pytest.raises(_Stop):
        image_renderer.stop_image_renderer(
            factory, settings, instance_id=INSTALLATION, actor="test",
        )
    assert seen == [INSTALLATION]


def test_release_guards_every_installation_in_sorted_order(api, monkeypatch):
    from shine.comfyops import renderer_release

    order = []
    released = {}

    from contextlib import contextmanager

    @contextmanager
    def fake_guard(installation_id, timeout_s=30.0):
        order.append(installation_id)
        yield None

    monkeypatch.setattr("shine.runtime_orchestrator.guard.renderer_lifecycle_guard", fake_guard)
    monkeypatch.setattr(
        renderer_release, "managed_processes",
        lambda db: [
            {"key": "renderer-startup:x", "installation": {"id": OTHER_INSTALLATION}},
            {"key": "renderer-startup:image:y", "purpose": "image", "instance_id": INSTALLATION},
        ],
    )

    def fake_release(db, *, actor, reason, locked_installations, only=None):
        released["actor"] = actor
        # A global release (no target) still guards every installation.
        assert only is None
        assert locked_installations == {INSTALLATION, OTHER_INSTALLATION}
        return {"stopped": [], "refused": [], "released": []}

    monkeypatch.setattr(renderer_release, "release_managed_processes", fake_release)
    _, factory, _ = api
    report = renderer_release.release_managed_processes_guarded(factory, actor="test", reason="r")
    assert order == sorted([INSTALLATION, OTHER_INSTALLATION])
    assert released["actor"] == "test"
    assert report["stopped"] == []


def test_release_skips_installation_recorded_after_guard_discovery(api, monkeypatch):
    from shine.comfyops import renderer_release

    _, factory, _ = api
    calls = iter(([], [{
        "instance_id": INSTALLATION, "purpose": "image", "stage": "LAUNCHED",
    }]))
    monkeypatch.setattr(renderer_release, "managed_processes", lambda db: next(calls))
    monkeypatch.setattr(renderer_release, "_live_facts", lambda r: pytest.fail("unlocked installation"))
    report = renderer_release.release_managed_processes_guarded(factory, actor="test", reason="r")
    assert [(item["id"], item["outcome"]) for item in report["refused"]] == [(INSTALLATION, "busy")]

    assert report["stopped"] == []


# --- snapshot honesty ---------------------------------------------------------


def _write_recipe(api, tmp_path, *, instance_id=INSTALLATION, port=8188):
    import json
    import sys
    from pathlib import Path

    from shine.comfyops.deployment import ADAPTER_DIR
    from shine.comfyops.image_renderer import recipe_path
    from shine.comfyops.managed_recipe import adapter_identity

    _, _, settings = api
    root = tmp_path / "comfy-root"
    root.mkdir(parents=True, exist_ok=True)
    bootstrap = Path(__file__).resolve().parents[2] / "scripts" / "managed_comfy_bootstrap.py"
    recipe = {
        "renderer": {
            "instance_id": instance_id,
            "python": str(Path(sys.executable).resolve()),
            "root": str(root),
            "base_directory": str(root),
            "runtime_data_root": str(root),
            "bootstrap": str(bootstrap),
            "adapter_dir": str(ADAPTER_DIR),
            "adapter_sha256": adapter_identity(ADAPTER_DIR),
            "gpu_uuid": "GPU-TEST",
            "port": port,
            "argv": ["{python}", "{bootstrap}", "--listen", "127.0.0.1", "--port", "{port}"],
            "pinned_files": ["{python}", "{bootstrap}"],
        },
        "reviews": {},
    }
    path = recipe_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe), encoding="utf-8")


def _installation_row(db, installation_id=INSTALLATION, status="STOPPED"):
    db.add(ComfyInstance(id=installation_id, payload={"managed": True}, sha256="x", status=status))


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="managed image renderer start is Windows-only")
def test_image_start_failure_keeps_the_process_when_cleanup_is_unconfirmed(api, tmp_path, monkeypatch):
    from shine.comfyops import image_renderer

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    key = image_renderer.image_startup_key(INSTALLATION)
    with factory.begin() as db:
        _installation_row(db)

    class UnstoppableGroup:
        pid = 123
        job_name = "Local\\ShineHandoff-test"

        def owns(self, pid, birth):
            return pid == self.pid and birth == "known-birth"

        def terminate(self, *, timeout):
            with factory() as db:
                assert db.get(AppSetting, key).value["stage"] == "LAUNCHED"
            raise RuntimeError("exit could not be confirmed")

    def no_listener(group, port):
        raise HTTPException(409, "TEST_LISTENER_NOT_READY")

    monkeypatch.setattr(image_renderer, "build_renderer_launch", lambda recipe: ([], ".", {}, [], "", 8188))
    monkeypatch.setattr(image_renderer.OwnedProcessGroup, "launch", lambda *a, **k: UnstoppableGroup())
    monkeypatch.setattr(image_renderer, "process_identity", lambda pid: "known-birth")
    monkeypatch.setattr(image_renderer, "windows_port_listeners", lambda port: [])
    monkeypatch.setattr(image_renderer, "_wait_for_owned_listener", no_listener)
    with pytest.raises(HTTPException, match="TEST_LISTENER_NOT_READY"):
        image_renderer.start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    with factory() as db:
        record = db.get(AppSetting, key).value
    assert record["stage"] == "RECONCILE_REQUIRED"
    assert record["process"] == {"pid": 123, "birth": "known-birth", "job_name": UnstoppableGroup.job_name}


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="managed image renderer start is Windows-only")
def test_image_start_refuses_external_listener_before_launch(api, tmp_path, monkeypatch):
    from shine.comfyops import image_renderer

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(image_renderer, "windows_port_listeners", lambda port: [("127.0.0.1", 42)])
    monkeypatch.setattr(image_renderer.OwnedProcessGroup, "launch", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(HTTPException) as refused:
        image_renderer.start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    assert refused.value.detail["code"] == "EXTERNAL_INSTANCE_ON_PORT"
    with factory() as db:
        assert db.get(AppSetting, image_renderer.image_startup_key(INSTALLATION)) is None


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="managed image renderer admission is Windows-only")
@pytest.mark.parametrize(
    ("lease_status", "latest_state", "can_start"),
    [("RELEASED", "GONE", True), ("RELEASED", "UNVERIFIED", False), ("ACTIVE", "GONE", False)],
)
def test_image_admission_retires_released_h3_only_when_both_processes_are_gone(
    api, tmp_path, monkeypatch, lease_status, latest_state, can_start,
):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops import image_renderer, renderer_release
    from shine.comfyops.models import ResourceLease
    from shine.models import Job

    from .conftest import create_job, create_project, create_shot, freeze_style

    client, factory, settings = api
    _write_recipe(api, tmp_path)
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_job(client, project, shot["id"])
    run_id = "finished-h3-run"
    startup_key = "renderer-startup:" + run_id
    pin = {"id": INSTALLATION, "sha256": "x", "attempt_id": "install-attempt"}
    first = {"pid": 1, "birth": "first", "job_name": "first-group"}
    last = {"pid": 2, "birth": "latest", "job_name": "replacement-group"}
    base = {"startup_id": "start-id", "installation": pin, "port": 8188}
    with factory.begin() as db:
        _installation_row(db)
        db.get(Job, job["id"]).status = "PASSED"
        db.add(ResourceLease(
            id="first-lease", owner="h3:" + job["id"], status=lease_status, expires_at=utcnow(),
            budget_mb=16384, exclusive=True, priority=100,
        ))
        payload = {**base, "process": last}
        db.add(ComfyInstance(id=run_id, payload=payload, sha256=graph_hash(payload), status="RUNNING_PENDING_HEALTH"))
        db.add(AppSetting(key=startup_key, value={
            **base, "stage": "RUNNING", "process": first, "lease_id": "first-lease", "job_id": job["id"],
        }))

    def no_process_launch(recipe):
        raise HTTPException(409, "TEST_REACHED_IMAGE_LAUNCH")

    monkeypatch.setattr(
        renderer_release, "managed_process_state",
        lambda record: latest_state if record["process"] == last else "GONE",
    )
    monkeypatch.setattr(image_renderer, "windows_port_listeners", lambda port: [])
    monkeypatch.setattr(image_renderer, "build_renderer_launch", no_process_launch)
    with pytest.raises(HTTPException) as exc:
        image_renderer.start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    with factory() as db:
        saved = db.get(AppSetting, startup_key).value
        if can_start:
            assert exc.value.detail == "TEST_REACHED_IMAGE_LAUNCH"
            assert saved["stopped_reason"] == "h3_released_and_gone"
            assert db.get(ComfyInstance, run_id).status == "STOPPED"
        else:
            assert exc.value.detail["code"] == "IMAGE_RENDERER_INSTALLATION_BUSY_H3"
            assert saved["stage"] == "RUNNING"
        assert saved["process"] == first


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="image renderer process/port proof is Windows-only")
def test_snapshot_does_not_call_a_dead_record_busy(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    with factory.begin() as db:
        _installation_row(db)
        _image_record(db, stage="RUNNING")
    with factory() as db:
        snap = status_module.runtime_snapshot(db, settings)
    assert snap["state"] == "STOPPED"
    assert snap["ownership"]["verified"] is False
    assert snap["ownership"]["pid"] is None


def test_snapshot_reports_unverifiable_ownership(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    monkeypatch.setattr(
        status_module, "image_renderer_state",
        lambda db, settings_, iid: {"state": "UNVERIFIED", "pid": None, "port": 8188},
    )
    with factory.begin() as db:
        _installation_row(db)
        _image_record(db, stage="RUNNING")
    with factory() as db:
        snap = status_module.runtime_snapshot(db, settings)
    assert snap["state"] == "BLOCKED_UNVERIFIED"
    assert snap["recovery"]["safe_action"] == "reconcile"


def test_snapshot_reports_workflow_mismatch(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    monkeypatch.setattr(status_module, "_owned_listener_pid", lambda port, process: 4)
    monkeypatch.setattr(status_module, "_http_up", lambda base_url, **kwargs: True)
    monkeypatch.setattr(
        status_module, "image_renderer_state",
        lambda db, settings_, iid: {"state": "RUNNING", "pid": 4, "port": 8188},
    )
    with factory.begin() as db:
        _installation_row(db)
        _image_record(db, stage="RUNNING", adapter_sha256="b" * 64)
    with factory() as db:
        snap = status_module.runtime_snapshot(db, settings)
    assert snap["health"]["workflow"] == "MISMATCH"
    assert snap["state"] == "DEGRADED"


def test_snapshot_never_probes_a_listener_it_cannot_prove_owned(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_owned_listener_pid", lambda port, process: None)
    monkeypatch.setattr(status_module, "_http_up", lambda url: pytest.fail("probed an unowned listener"))
    monkeypatch.setattr(
        status_module, "image_renderer_state",
        lambda db, settings_, iid: {"state": "RUNNING", "pid": 4, "port": 8188},
    )
    with factory.begin() as db:
        _installation_row(db)
        _image_record(db)
    with factory() as db:
        snap = status_module.runtime_snapshot(db, settings)
    assert snap["state"] == "DEGRADED"
    assert snap["health"]["comfy_http"] == "UNKNOWN"
    assert snap["recovery"]["safe_action"] == "probe"


def test_snapshot_exposes_a_pending_operation(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    with factory.begin() as db:
        _installation_row(db)
        operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-live", request={"params": {}},
        )
    with factory() as db:
        snap = status_module.runtime_snapshot(db, settings)
    assert snap["current"]["operation_id"]
    assert snap["current"]["operation_action"] == "ensure"
    assert snap["current"]["operation_status"] == "PENDING"


# --- reconcile outcomes -------------------------------------------------------


def test_reconcile_reports_nothing_to_reconcile(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    with factory.begin() as db:
        _installation_row(db)
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_NOTHING


def test_reconcile_preserves_an_unknown_submission_for_a_human(api, tmp_path, monkeypatch):
    from shine.models import Job

    from .conftest import create_job, create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client, "未知提交")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    created = create_job(client, project, shot["id"])
    with factory.begin() as db:
        _installation_row(db)
        job = db.get(Job, created["id"])
        job.status = "NEEDS_REVIEW"
        job.failure_code = "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_NEEDS_REVIEW
    kinds = {item["kind"] for item in result["uncertain"]}
    assert "unknown_submission" in kinds
    assert "不会重发" in result["note"]


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="image renderer process/port proof is Windows-only")
def test_reconcile_clears_a_proven_dead_record(api, tmp_path, monkeypatch):
    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory.begin() as db:
        _installation_row(db, status="RUNNING_PENDING_HEALTH")
        _image_record(db, stage="RUNNING")
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_RECONCILED
    with factory() as db:
        row = db.get(ComfyInstance, INSTALLATION)
        assert row.status == "STOPPED"


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="image renderer process/port proof is Windows-only")
def test_reconcile_does_not_stop_a_replacement_renderer(api, monkeypatch):
    from shine.comfyops import image_renderer

    _, factory, settings = api
    key = image_renderer.image_startup_key(INSTALLATION)
    with factory.begin() as db:
        _installation_row(db, status="RUNNING_PENDING_HEALTH")
        _image_record(db, startup_id="dead-start")
    original = image_renderer.reconcile_dead_image_renderer

    def replace_before_lock(factory, installation_id):
        with factory.begin() as db:
            record = db.get(AppSetting, key)
            record.value = {**record.value, "startup_id": "replacement-start"}
        return original(factory, installation_id)

    monkeypatch.setattr(image_renderer, "reconcile_dead_image_renderer", replace_before_lock)
    monkeypatch.setattr(image_renderer, "_provably_gone", lambda record: record["startup_id"] == "dead-start")
    monkeypatch.setattr(image_renderer, "terminate_named_group", lambda *a, **k: pytest.fail("stopped replacement"))
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_NEEDS_REVIEW
    assert result["uncertain"] == [{"kind": "image_renderer", "state": "RECORD_CHANGED"}]
    with factory() as db:
        assert db.get(AppSetting, key).value["stage"] == "RUNNING"
        assert db.get(ComfyInstance, INSTALLATION).status == "RUNNING_PENDING_HEALTH"


def test_reconcile_reports_a_failure_instead_of_crashing(api, monkeypatch):
    _, factory, settings = api

    def explode(*args, **kwargs):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr("shine.comfyops.renderer_release.describe_managed_processes", explode)
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_FAILED
    assert result["error"] == "RuntimeError"


def test_reconcile_reports_uncertain_h3_startup_and_missing_nodes(api, monkeypatch):
    _, factory, settings = api
    monkeypatch.setattr("shine.comfyops.renderer_release.describe_managed_processes", lambda db: [])
    with factory.begin() as db:
        db.add(AppSetting(key="renderer-startup:test-run", value={
            "stage": "RECONCILE_REQUIRED", "installation": {"id": INSTALLATION},
            "missing_nodes": ["MiniMaxH3MemoryEfficientSageAttentionPatch"],
        }))
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_NEEDS_REVIEW
    assert result["uncertain"] == [{"kind": "h3_renderer", "run_id": "test-run",
                                    "stage": "RECONCILE_REQUIRED",
                                    "missing_nodes": ["MiniMaxH3MemoryEfficientSageAttentionPatch"]}]
    with factory() as db:
        assert db.get(AppSetting, "renderer-startup:test-run").value["stage"] == "RECONCILE_REQUIRED"


def test_reconcile_closes_only_h3_record_with_released_identity_proof(api, monkeypatch):
    from shine.runtime_orchestrator import identity

    _, factory, settings = api
    monkeypatch.setattr("shine.comfyops.renderer_release.describe_managed_processes", lambda db: [])
    monkeypatch.setattr(identity, "_h3_runtime_process", lambda db, run_id, startup: {"pid": 8})
    monkeypatch.setattr(identity, "_released_h3_processes_are_gone", lambda db, startup, process: True)
    with factory.begin() as db:
        db.add(AppSetting(key="renderer-startup:test-run", value={
            "stage": "RECONCILE_REQUIRED", "installation": {"id": INSTALLATION},
            "lease_id": "released-lease", "job_id": "past-job", "process": {"pid": 8},
        }))
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_RECONCILED
    with factory() as db:
        row = db.get(AppSetting, "renderer-startup:test-run")
        assert row.value["stage"] == "STOPPED"
        assert row.value["lease_id"] == "released-lease"
        assert row.value["process"] == {"pid": 8}


def test_reconcile_does_not_close_h3_when_process_proof_is_missing(api, monkeypatch):
    from shine.runtime_orchestrator import identity

    _, factory, settings = api
    monkeypatch.setattr("shine.comfyops.renderer_release.describe_managed_processes", lambda db: [])
    monkeypatch.setattr(identity, "_h3_runtime_process", lambda db, run_id, startup: {"pid": 8})
    monkeypatch.setattr(identity, "_released_h3_processes_are_gone", lambda db, startup, process: False)
    with factory.begin() as db:
        db.add(AppSetting(key="renderer-startup:test-run", value={
            "stage": "RECONCILE_REQUIRED", "installation": {"id": INSTALLATION},
            "lease_id": "uncertain-lease", "job_id": "past-job", "process": {"pid": 8},
        }))
    result = executor._reconcile(factory, settings, INSTALLATION)
    assert result["outcome"] == executor.OUTCOME_NEEDS_REVIEW
    with factory() as db:
        assert db.get(AppSetting, "renderer-startup:test-run").value["stage"] == "RECONCILE_REQUIRED"


def test_snapshot_exposes_missing_h3_nodes_without_claiming_production(api, tmp_path, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setattr(status_module, "_external_conflict", lambda port: False)
    with factory.begin() as db:
        _installation_row(db)
        db.add(AppSetting(key="renderer-startup:node-test", value={
            "stage": "RECONCILE_REQUIRED", "installation": {"id": INSTALLATION},
            "reason": "COMFY_REQUIRED_NODE_MISSING",
            "missing_nodes": ["MiniMaxH3MemoryEfficientSageAttentionPatch"],
        }))
    with factory() as db:
        snapshot = status_module.runtime_snapshot(db, settings)
    assert snapshot["diagnostics"]["reason_code"] == "COMFY_REQUIRED_NODE_MISSING"
    assert snapshot["diagnostics"]["missing_nodes"] == ["MiniMaxH3MemoryEfficientSageAttentionPatch"]
    assert snapshot["diagnostics"]["production_ready"] is False


def test_explicit_probe_distinguishes_http_from_unowned_listener(api, monkeypatch):
    from shine.runtime_orchestrator import status as status_module

    _, factory, settings = api
    monkeypatch.setattr(executor, "_activity_summary", lambda db, installation_id: {})
    monkeypatch.setattr(status_module, "runtime_snapshot", lambda db, settings: {
        "installation_id": INSTALLATION, "ownership": {"port": 8188},
        "health": {"comfy_http": "UNKNOWN"},
    })
    called = []
    monkeypatch.setattr(status_module, "_probe_http", lambda url: called.append(url) or True)
    result = executor._probe(factory, settings, INSTALLATION)
    assert called == ["http://127.0.0.1:8188"]
    assert result["transport"] == "UP"
    assert result["listener_ownership"] == "UNVERIFIED"
    assert result["production_ready"] is False

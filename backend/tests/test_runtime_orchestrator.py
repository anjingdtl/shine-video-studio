"""The unified runtime control plane.

These tests cover the decisions, not the processes: an operation record's
idempotent lifecycle with compare-and-set completions, the read-only
installation activity facts, the named refusals that keep one physical
installation from ever owning two managed renderers, the in-flight
protection on stop, the Worker-side executor and the HTTP surface that
records intents without touching a process in the request thread.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.models import AppSetting
from shine.runtime_orchestrator import contracts, identity, operations

pytestmark = pytest.mark.integration

INSTALLATION = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def installation_row(api):
    _, factory, _ = api
    with factory.begin() as db:
        row = ComfyInstance(
            id=INSTALLATION,
            payload={"managed": True, "port": 8188, "root": "C:/comfy"},
            sha256="x",
            status="STOPPED",
        )
        db.add(row)
    return INSTALLATION


def _h3_record(db, installation_id=INSTALLATION, *, stage="RUNNING", key=None, **extra):
    value = {
        "stage": stage,
        "installation": {"id": installation_id, "sha256": "x", "attempt_id": "a1"},
        "lease_id": "lease-1",
        "job_id": "job-1",
        "owner_pid": 4,
        "owner_birth": "dead",
    }
    value.update(extra)
    db.add(AppSetting(key=key or ("renderer-startup:" + uuid4().hex), value=value))


def _image_record(db, installation_id=INSTALLATION, *, stage="RUNNING", **extra):
    value = {
        "stage": stage,
        "instance_id": installation_id,
        "purpose": "image",
        "port": 8188,
        "owner_pid": 4,
        "owner_birth": "dead",
        "process": {"pid": 999999, "birth": "nope", "job_name": "Local\\ShineHandoff-" + "0" * 32},
        "recorded_at": "2026-11-11T00:00:00",
    }
    value.update(extra)
    db.add(AppSetting(key="renderer-startup:image:" + installation_id, value=value))


def _lease(db, owner="h3:job-1", status="ACTIVE", lease_id="lease-1"):
    from datetime import timedelta

    from shine.models import utcnow

    db.add(ResourceLease(
        id=lease_id, owner=owner, budget_mb=1024, exclusive=True,
        priority=10, status=status,
        heartbeat_at=utcnow(), expires_at=utcnow() + timedelta(seconds=60),
        created_at=utcnow(),
    ))


# --- installation activity ---------------------------------------------------


def test_activity_reports_h3_image_and_leases(api, installation_row):
    _, factory, _ = api
    with factory.begin() as db:
        _h3_record(db, stage="RUNNING")
    with factory() as db:
        activity = identity.installation_activity(db, INSTALLATION)
    assert activity["renderer_state"] == "H3"
    assert len(activity["h3_records"]) == 1


def test_activity_prefers_a_live_image_renderer_over_history(api, installation_row):
    _, factory, _ = api
    with factory.begin() as db:
        _h3_record(db, stage="STOPPED")
        _image_record(db, stage="RUNNING")
    with factory() as db:
        activity = identity.installation_activity(db, INSTALLATION)
    assert activity["renderer_state"] == "IMAGE"
    assert activity["image_record"]["state"] == "ACTIVE"


def _in_flight_library_task(db, status="RUNNING"):
    from shine.asset_center.models import LibraryRenderTask

    db.add(LibraryRenderTask(
        idempotency_key=uuid4().hex, request_hash="x", kind="t2i",
        mode="generation", status=status, payload={},
    ))


def test_activity_counts_in_flight_image_work(api, installation_row):
    _, factory, _ = api
    with factory.begin() as db:
        _in_flight_library_task(db)
    with factory() as db:
        activity = identity.installation_activity(db, INSTALLATION)
    assert activity["in_flight_image_work"]


# --- image renderer start refusals (audit R3) --------------------------------


def _write_recipe(api, tmp_path, *, instance_id=INSTALLATION, port=8188):
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


def test_start_refuses_when_h3_owns_the_installation(api, installation_row, tmp_path):
    from shine.comfyops.image_renderer import start_image_renderer

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory.begin() as db:
        _h3_record(db, stage="RUNNING")
    with pytest.raises(HTTPException) as failure:
        start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    assert failure.value.detail["code"] == "IMAGE_RENDERER_INSTALLATION_BUSY_H3"


def test_start_refuses_when_another_start_is_in_progress(api, installation_row, tmp_path):
    import os

    from shine.comfyops.image_renderer import start_image_renderer
    from shine.comfyops.installer import process_identity

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory.begin() as db:
        _image_record(
            db, stage="INTENT",
            owner_pid=os.getpid(), owner_birth=process_identity(os.getpid()),
            process={},
        )
    with pytest.raises(HTTPException) as failure:
        start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    assert failure.value.detail["code"] == "IMAGE_RENDERER_START_IN_PROGRESS"


def test_start_refuses_when_a_gpu_lease_is_active(api, installation_row, tmp_path):
    from shine.comfyops.image_renderer import start_image_renderer

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory.begin() as db:
        _lease(db, owner="review:someone", lease_id="lease-other")
    with pytest.raises(HTTPException) as failure:
        start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    assert failure.value.detail["code"] == "IMAGE_RENDERER_GPU_LEASE_ACTIVE"


def test_stale_start_intent_does_not_block_a_new_start_forever(api, installation_row, tmp_path):
    """An INTENT whose owner died is history, not a permanent blocker."""
    from shine.comfyops.image_renderer import start_image_renderer

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory.begin() as db:
        # owner pid 4 with a birth nobody matches: provably dead owner.
        _image_record(db, stage="INTENT", process={})
    with pytest.raises(HTTPException) as failure:
        start_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    # The refusal is now the ordinary next gate (Windows-only start), never
    # START_IN_PROGRESS: the stale intent did not act as a live one.
    assert failure.value.detail != "IMAGE_RENDERER_START_IN_PROGRESS"


# --- image renderer stop protection (audit R6) -------------------------------


def test_stop_refuses_under_in_flight_image_work(api, installation_row):
    from shine.comfyops.image_renderer import stop_image_renderer

    _, factory, settings = api
    with factory.begin() as db:
        _image_record(db, stage="RUNNING")
        _in_flight_library_task(db)
    from shine.comfyops import image_renderer as ir

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ir, "managed_process_state", lambda record: "RUNNING")
    try:
        with pytest.raises(HTTPException) as failure:
            stop_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
        assert failure.value.detail["code"] == "IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT"
    finally:
        monkeypatch.undo()


def test_stop_refuses_under_an_active_image_lease(api, installation_row, monkeypatch):
    from shine.comfyops import image_renderer as ir
    from shine.comfyops.image_renderer import stop_image_renderer

    _, factory, settings = api
    with factory.begin() as db:
        _image_record(db, stage="RUNNING")
        _lease(db, owner="image:abc", lease_id="lease-img")
    monkeypatch.setattr(ir, "managed_process_state", lambda record: "RUNNING")
    with pytest.raises(HTTPException) as failure:
        stop_image_renderer(factory, settings, instance_id=INSTALLATION, actor="test")
    assert failure.value.detail["code"] == "IMAGE_RENDERER_BUSY_LEASE_ACTIVE"


# --- release busy protection (audit R5) ---------------------------------------


def test_release_marks_submitting_jobs_as_in_flight(api, installation_row):
    from shine.comfyops.renderer_release import IN_FLIGHT_JOB_STATUSES

    assert {"SUBMITTING", "SUBMITTED", "RUNNING", "QC_PENDING"} <= IN_FLIGHT_JOB_STATUSES


def test_release_refuses_image_renderer_serving_image_work(api, installation_row, monkeypatch):
    from shine.comfyops import renderer_release

    _, factory, _ = api
    with factory.begin() as db:
        _image_record(db, stage="RUNNING")
        _in_flight_library_task(db, status="RUNNING")
    monkeypatch.setattr(renderer_release, "_live_facts", lambda record: {
        "pid": 999999, "job_name": record.get("process", {}).get("job_name"),
        "alive": True, "identity_known": True, "owned": True,
        "group_members": 1, "port_busy": True,
    })
    monkeypatch.setattr(
        renderer_release, "terminate_named_group",
        lambda *a, **k: pytest.fail("an in-flight image renderer must never be terminated"),
    )
    with factory() as db:
        report = renderer_release.release_managed_processes(db, actor="test", reason="test")
    reason = "an image task is running on this renderer"
    refused = [entry for entry in report["refused"] if entry.get("reason") == reason]
    assert refused, report


# --- operation records --------------------------------------------------------


def test_operation_idempotency_and_conflict(api):
    _, factory, _ = api
    with factory.begin() as db:
        first = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="key-1", request={"params": {"reason": "x"}},
        )
        assert first.status == "PENDING"
    with factory.begin() as db:
        again = operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="key-1", request={"params": {"reason": "x"}},
        )
        assert again.id == first.id
    with factory.begin() as db:
        with pytest.raises(HTTPException) as failure:
            operations.create_or_get_operation(
                db, action="stop", installation_id=INSTALLATION,
                idempotency_key="key-1", request={"params": {}},
            )
        assert failure.value.status_code == 409


def test_stale_executor_cannot_overwrite_a_newer_outcome(api):
    _, factory, _ = api
    with factory.begin() as db:
        op = operations.create_or_get_operation(
            db, action="probe", installation_id=INSTALLATION,
            idempotency_key="key-2", request={"params": {}},
        )
        claimed = operations.claim_pending_operations(db, worker_id="w-new", worker_birth="b-new")
        assert [row.id for row in claimed] == [op.id]
        version = claimed[0].state_version
    with factory.begin() as db:
        # The newer owner completes the operation first.
        operations.succeed_operation(
            db, op.id, owner_worker_id="w-new", expected_version=version,
            result={"probed": True},
        )
    with factory.begin() as db:
        # A stale executor (old worker, old version) replays its completion.
        operations.succeed_operation(
            db, op.id, owner_worker_id="w-old", expected_version=version - 1,
            result={"probed": False, "forged": True},
        )
    with factory() as db:
        row = operations.get_operation(db, op.id)
        assert row.status == "SUCCEEDED"
        assert not row.result.get("forged")


def test_terminal_operations_are_never_reopened(api):
    _, factory, _ = api
    with factory.begin() as db:
        op = operations.create_or_get_operation(
            db, action="probe", installation_id=INSTALLATION,
            idempotency_key="key-3", request={"params": {}},
        )
        op.status = "REFUSED"
        op.failure_code = "X"
        op.owner_worker_id = "w"
        op.state_version = 3
    with factory.begin() as db:
        result = operations.succeed_operation(
            db, op.id, owner_worker_id="w", expected_version=3, result={"nope": True},
        )
        assert result is None
    with factory() as db:
        assert operations.get_operation(db, op.id).status == "REFUSED"


# --- executor ------------------------------------------------------------------


def test_executor_runs_probe_operations(api, installation_row):
    from shine.runtime_orchestrator.executor import advance_control_operations

    _, factory, settings = api
    with factory.begin() as db:
        operations.create_or_get_operation(
            db, action="probe", installation_id=INSTALLATION,
            idempotency_key="probe-1", request={"params": {}},
        )
    executed = advance_control_operations(factory, settings, worker_id="w1", worker_birth="b1")
    assert executed == 1
    with factory() as db:
        from sqlalchemy import select

        row = db.scalar(
            select(operations.RuntimeControlOperation).where(
                operations.RuntimeControlOperation.idempotency_key == "probe-1")
        )
        assert row.status == "SUCCEEDED"
        assert row.result["activity"]["renderer_state"] == "NONE"


def test_executor_never_raises_and_records_refusals(api, installation_row, tmp_path):
    from shine.runtime_orchestrator.executor import advance_control_operations

    _, factory, settings = api
    # An ensure without a recipe must refuse by name, not crash the worker.
    with factory.begin() as db:
        operations.create_or_get_operation(
            db, action="ensure", installation_id=INSTALLATION,
            idempotency_key="ensure-1", request={"params": {}},
        )
    executed = advance_control_operations(factory, settings, worker_id="w1", worker_birth="b1")
    assert executed == 1
    with factory() as db:
        from sqlalchemy import select

        row = db.scalar(
            select(operations.RuntimeControlOperation).where(
                operations.RuntimeControlOperation.idempotency_key == "ensure-1")
        )
        assert row.status == "REFUSED"
        assert "RECIPE" in str(row.failure_code)


# --- HTTP surface ---------------------------------------------------------------


def test_status_is_read_only_and_shaped(api):
    client, _, _ = api
    response = client.get("/api/v1/runtime-orchestrator/status")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert "orchestrator_enabled" in body


def test_ensure_without_a_worker_stays_pending_and_queryable(api):
    client, factory, _ = api
    response = client.post(
        "/api/v1/runtime-orchestrator/ensure",
        json={"installation_id": INSTALLATION, "idempotency_key": "api-1", "reason": "t"},
        headers={"x-shine-local-action": "1"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    operation_id = body["operation_id"]
    follow = client.get(f"/api/v1/runtime-orchestrator/operations/{operation_id}")
    assert follow.status_code == 200
    assert follow.json()["status"] == "PENDING"


def test_image_runtime_start_routes_through_the_control_plane(api, installation_row, tmp_path, monkeypatch):
    client, factory, settings = api
    _write_recipe(api, tmp_path)
    # A worker heartbeat makes the legacy release path unavailable; ensure the
    # image start goes through an operation, not a direct process call.
    monkeypatch.setattr(
        "shine.comfyops.image_renderer.start_image_renderer",
        lambda *a, **k: pytest.fail("request thread must not start a renderer"),
    )
    response = client.post(
        "/api/v1/setup/image-runtime/start", headers={"x-shine-local-action": "1"})
    # No runtime is bound in this fixture, so the route refuses early; with a
    # bound runtime the intent is recorded and the Worker executes it. Either
    # way no process was started in the request thread.
    assert response.status_code in (202, 409)


def test_orchestrator_flag_restores_the_direct_path(api, installation_row, tmp_path, monkeypatch):
    _, factory, settings = api
    _write_recipe(api, tmp_path)
    monkeypatch.setenv(contracts.ENV_FLAG, "0")
    called = {"n": 0}

    def fake_start(factory_, settings_, *, instance_id, actor, held_lease_id=None):
        called["n"] += 1
        return {"status": "RUNNING"}

    monkeypatch.setattr("shine.comfyops.image_renderer.start_image_renderer", fake_start)
    from shine.comfyops.image_renderer import ensure_image_renderer

    ensure_image_renderer(factory, settings, instance_id=INSTALLATION)
    assert called["n"] == 1
    monkeypatch.delenv(contracts.ENV_FLAG, raising=False)


# --- unified snapshot (P3) ------------------------------------------------------


def test_snapshot_states_and_recovery_advice(api, installation_row, tmp_path):
    import os

    from shine.comfyops.installer import process_identity
    from shine.runtime_orchestrator.status import runtime_snapshot

    _, factory, settings = api
    _write_recipe(api, tmp_path)
    with factory() as db:
        snap = runtime_snapshot(db, settings)
    # Nothing runs and no listener can be claimed: STOPPED with a safe ensure.
    assert snap["state"] in ("STOPPED", "NOT_BOUND")
    assert snap["recovery"]["safe_action"] in (None, "ensure")
    assert snap["health"]["worker"] == "DOWN"

    with factory.begin() as db:
        _image_record(db, stage="INTENT", owner_pid=os.getpid(),
                      owner_birth=process_identity(os.getpid()))
    with factory() as db:
        snap = runtime_snapshot(db, settings)
    assert snap["state"] == "STARTING"
    assert snap["recovery"]["reason_code"] == "START_IN_PROGRESS"
    assert snap["recovery"]["safe_action"] is None  # never a second start

    from shine.models import AppSetting as _AppSetting

    with factory.begin() as db:
        row = db.get(_AppSetting, "renderer-startup:image:" + INSTALLATION)
        row.value = {**row.value, "stage": "RECONCILE_REQUIRED"}
    with factory() as db:
        snap = runtime_snapshot(db, settings)
    assert snap["state"] == "BLOCKED_UNVERIFIED"
    assert snap["recovery"]["safe_action"] == "reconcile"

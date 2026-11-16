"""The whole preparation chain behind one click (2026-11-15 plan, WP4).

The browser used to drive reconcile, downloads and the instance ladder before
handing the rest to the Worker.  These tests lock the replacement contract:
one authorization becomes one durable task that starts at reconcile/download,
works on an empty database (machine level), waits on its own downloads without
deadlocking, and names a failed download instead of replaying it forever.
"""

from __future__ import annotations

import pytest

from shine.comfyops import setup_preparation
from shine.comfyops.models import InstallationAttempt, InstallationPlan

LOCAL = {"x-shine-local-action": "1"}


@pytest.fixture
def env(studio):
    client, factory, tmp_path, settings = studio
    return client, factory, tmp_path, settings


def _plan_row(factory, *, status="AWAITING_APPROVAL"):
    with factory.begin() as db:
        plan = InstallationPlan(
            project_id=None,
            payload={"kind": "INSTALL", "packages": [{
                "id": "pkg-1", "hash": "h",
                "spec": {"kind": "model", "license_id": "apache-2.0", "usage": "commercial",
                         "files": []},
            }], "download_bytes": 10},
            sha256="c" * 64, status=status,
        )
        db.add(plan)
        db.flush()
        return plan.id


def _attempt_row(factory, plan_id, *, stage="QUEUED", status="QUEUED", error_code=None):
    with factory.begin() as db:
        attempt = InstallationAttempt(
            plan_id=plan_id, payload={"plan_hash": "c" * 64}, sha256="c" * 64,
            stage=stage, status=status, error_code=error_code,
        )
        db.add(attempt)
        db.flush()
        return attempt.id


def test_machine_level_prepare_needs_no_project(env):
    client, factory, _, _ = env
    response = client.post("/api/v1/setup/prepare/execute",
                           json={"capabilities": ["image"], "verify_runtime": True},
                           headers=LOCAL)
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    again = client.post("/api/v1/setup/prepare/execute",
                        json={"capabilities": ["image"], "verify_runtime": True},
                        headers=LOCAL)
    assert again.json()["task_id"] == task_id, "同一意图必须复用同一个任务"
    with factory() as db:
        task = setup_preparation.read_task(db)
    assert task["project_id"] is None
    ids = [row["id"] for row in task["steps"]]
    assert ids[:2] == ["RECONCILE", "DOWNLOAD"]
    assert "IMAGE_SAMPLES" in ids


def test_project_scoped_steps_skip_on_machine_level(env):
    _, factory, _, settings = env
    task = {"project_id": None, "request": {"capabilities": ["h3"]}}
    profiles = setup_preparation._step_profiles(factory, settings, task)
    assert profiles["status"] == "SKIPPED"
    samples = setup_preparation._step_image_samples(factory, settings, task)
    assert samples["status"] == "SKIPPED"


def test_reconcile_skips_when_no_installation_found(env, monkeypatch):
    _, factory, _, settings = env
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    task = {"project_id": None, "request": {"capabilities": ["h3", "image"]}}
    detail = setup_preparation._step_reconcile(factory, settings, task)
    assert detail["status"] == "DONE"
    assert "实例阶梯" in detail["detail"]


def test_download_step_done_when_nothing_missing(env, monkeypatch):
    _, factory, _, settings = env
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    monkeypatch.setattr("shine.comfyops.setup_summary.capability_report",
                        lambda *a, **k: {"missing_files": [], "catalog": "h3-ref2va-windows-x64"})
    monkeypatch.setattr("shine.comfyops.setup_summary.runtime_closure_gap",
                        lambda *a, **k: {"missing_components": []})
    task = {"project_id": None, "request": {"capabilities": ["h3"]}, "downloads": {}}
    detail = setup_preparation._step_download(factory, settings, task)
    assert detail["status"] == "DONE"
    assert "无需下载" in detail["detail"]


def test_download_step_queues_once_then_waits_then_finishes(env, monkeypatch):
    _, factory, _, settings = env
    plan_id = _plan_row(factory)
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    monkeypatch.setattr("shine.comfyops.setup_summary.capability_report",
                        lambda *a, **k: {"missing_files": [{"component_id": "minimax-h3-diffusion"}],
                                         "catalog": "h3-ref2va-windows-x64"})
    monkeypatch.setattr("shine.comfyops.setup_summary.runtime_closure_gap",
                        lambda *a, **k: {"missing_components": []})
    monkeypatch.setattr("shine.api_setup.install_plan",
                        lambda body, settings, db: {"plan_id": plan_id})
    queued = []

    def fake_queue(factory, settings, pid, digest, actor, executor):
        queued.append(pid)
        return _attempt_row(factory, pid), False

    monkeypatch.setattr("shine.comfyops.local.queue_install_attempt", fake_queue)
    task = {"project_id": None, "request": {"capabilities": ["h3"]}, "downloads": {}}

    first = setup_preparation._step_download(factory, settings, task)
    assert first["status"] == "PENDING"
    assert queued == [plan_id]
    assert task["downloads"]["h3"]["attempt_id"]

    # Second tick: the attempt is still downloading — no second plan, no replay.
    second = setup_preparation._step_download(factory, settings, task)
    assert second["status"] == "PENDING"
    assert queued == [plan_id]

    # The bytes arrive; the step finishes without queueing anything again.
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, task["downloads"]["h3"]["attempt_id"])
        attempt.stage = "FILES_VERIFIED_PENDING_RUNTIME"
        attempt.status = "FILES_VERIFIED_PENDING_RUNTIME"
    third = setup_preparation._step_download(factory, settings, task)
    assert third["status"] == "DONE"
    assert queued == [plan_id]


def test_download_step_names_a_failed_attempt(env, monkeypatch):
    _, factory, _, settings = env
    plan_id = _plan_row(factory)
    attempt_id = _attempt_row(factory, plan_id, stage="NEEDS_REVIEW",
                              status="FAILED_RECONCILE_REQUIRED", error_code="UNTRUSTED_DOWNLOAD_URL")
    task = {"project_id": None, "request": {"capabilities": ["h3"]},
            "downloads": {"h3": {"attempt_id": attempt_id, "plan_id": plan_id}}}
    detail = setup_preparation._step_download(factory, settings, task)
    assert detail["status"] == "FAILED"
    assert detail["code"] == "UNTRUSTED_DOWNLOAD_URL"


def test_deep_scan_step_is_part_of_the_same_authorization(env):
    client, factory, _, _ = env
    response = client.post("/api/v1/setup/prepare/execute",
                           json={"capabilities": ["image"], "verify_runtime": False,
                                 "deep_scan": True},
                           headers=LOCAL)
    assert response.status_code == 200, response.text
    with factory() as db:
        task = setup_preparation.read_task(db)
    assert task["steps"][0]["id"] == "SCAN"
    assert task["request"]["deep_scan"] is True


def test_scan_step_advances_the_durable_scan(env):
    _, factory, _, settings = env
    task = {"project_id": None, "request": {}}
    detail = setup_preparation._step_scan(factory, settings, task)
    # On a machine with no queued roots the scan finishes immediately; either
    # way the step reports a real state, never a fake ready.
    assert detail["status"] in {"DONE", "PENDING"}


def test_validate_delegates_to_supervisor_and_resumes(env, monkeypatch):
    _, factory, _, settings = env
    task = {"project_id": "proj-1", "request": {"capabilities": ["h3"]}}
    # A candidate recipe exists but is not yet active/verified.
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report",
                        lambda db, settings: {"status": "NOT_ACTIVATED",
                                              "candidate": {"project_id": "proj-1",
                                                            "recipe_sha256": "r" * 64}})
    calls = []
    monkeypatch.setattr(setup_preparation, "_delegate_supervisor_validation",
                        lambda settings: calls.append(1) or {"status": "VALIDATING"})
    detail = setup_preparation._step_validate(factory, settings, task)
    assert detail["status"] == "PENDING"
    assert calls == [1]
    # After the supervisor validated and activated, the resumed step sees the
    # active contract AND a validated project profile, so it finishes without
    # delegating again.
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report",
                        lambda db, settings: {"status": "ACTIVE", "services_verified": True,
                                              "candidate": {"project_id": "proj-1",
                                                            "recipe_sha256": "r" * 64}})
    from shine.models import Project, WorkflowProfile
    with factory.begin() as db:
        db.add(Project(id="proj-1", title="核验测试"))
        db.add(WorkflowProfile(project_id="proj-1", name="p", mode="Ref2VA", graph={},
                               graph_sha256="g" * 64, validated_against={"complete": True}))
    resumed = setup_preparation._step_validate(factory, settings, task)
    assert resumed["status"] == "DONE"
    assert calls == [1], "合同与方案均已核验时不应再次委托"


def test_validate_without_candidate_recipe_is_named(env, monkeypatch):
    _, factory, _, settings = env
    task = {"project_id": "proj-1", "request": {}}
    monkeypatch.setattr("shine.comfyops.runtime_configuration.runtime_report",
                        lambda db, settings: {"status": "NOT_ACTIVATED", "candidate": None})
    detail = setup_preparation._step_validate(factory, settings, task)
    assert detail["status"] == "FAILED"
    assert detail["code"] == "GENERATE_RUNTIME_CONFIGURATION_FIRST"
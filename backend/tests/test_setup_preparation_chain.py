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
from shine.models import AppSetting

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
    assert ids[:3] == ["TOOL_REPAIR", "RECONCILE", "DOWNLOAD"]
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


def test_tool_repair_is_one_step_of_the_single_preparation_task(env, monkeypatch):
    """补工具折进一键整备链：工具达标跳过，缺工具走同一个 execute_repair。

    页面不再直连一个长请求补工具；它与下载/实例/核验共用一个后台任务。
    """
    _, factory, _, settings = env
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_environment",
                        lambda *a, **k: {"tools": {}})
    monkeypatch.setattr("shine.comfyops.environment_repair.build_repair_plan",
                        lambda settings, report, **k: {"plan_id": "p", "packages": []})
    detail = setup_preparation._step_tool_repair(factory, settings, {"request": {}})
    assert detail["status"] == "SKIPPED"

    monkeypatch.setattr("shine.comfyops.environment_repair.build_repair_plan",
                        lambda settings, report, **k: {"plan_id": "p",
                                                       "packages": [{"package_id": "ffmpeg"}]})
    called = {}

    def fake_execute(db, settings, actor, plan_id, **k):
        called["plan_id"] = plan_id
        called["actor"] = actor
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr("shine.comfyops.environment_repair.execute_repair", fake_execute)
    detail = setup_preparation._step_tool_repair(factory, settings, {"request": {}})
    assert detail["status"] == "DONE"
    assert called["plan_id"] == "p"
    assert called["actor"] == "worker:setup"
    assert "TOOL_REPAIR" in setup_preparation._steps_for(["h3"], verify_runtime=True)


def test_stale_claim_of_same_task_cannot_overwrite_new_claim(env):
    """R5 CE-12: Stale worker claim of the same task fails ClaimLostError and never overwrites new lease."""
    from datetime import timedelta

    from shine.models import utcnow

    _client, factory, _, settings = env
    with factory() as db:
        setup_preparation.enqueue(db, project_id=None, capabilities=["h3"], settings=settings)

    claim1 = setup_preparation._claim(factory, "worker-1")
    assert claim1 is not None and claim1.get("claim_token")
    # Expire lease of worker-1 so worker-2 steals the claim with a fresh claim_token.
    with factory.begin() as db:
        row = db.get(setup_preparation.AppSetting, setup_preparation.TASK_KEY)
        val = dict(row.value)
        val["lease_expires_at"] = (utcnow() - timedelta(minutes=10)).isoformat()
        row.value = val

    claim2 = setup_preparation._claim(factory, "worker-2")
    assert claim2 is not None
    assert claim2["claim_token"] != claim1["claim_token"]

    claim1["message"] = "stale overwrite attempt"
    setup_preparation._save(factory, claim1)
    with pytest.raises(setup_preparation.ClaimLostError):
        setup_preparation._assert_claim(factory, claim1)
    with factory() as db:
        latest = setup_preparation.read_task(db)
        assert latest["claim_token"] == claim2["claim_token"]
        assert latest["lease_owner"] == "worker-2"


def test_superseded_step_cannot_publish_candidate(env, monkeypatch):
    """R5 CE-13: Superseded _step_recipe aborts before publishing CANDIDATE_KEY."""
    from shine.comfyops import deployment, runtime_configuration

    _client, factory, tmp_path, settings = env
    root = tmp_path / "comfy"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("# test\n", encoding="utf-8")
    with factory() as db:
        setup_preparation.enqueue(db, project_id=None, capabilities=["h3"],
                                  installation_path=str(root), settings=settings)
    claimed = setup_preparation._claim(factory, "worker-1")

    orig_gen = deployment.generate_configuration

    def wrapped_gen(db, *args, claim_guard=None, **kwargs):
        # Simulate another request superseding the task right before the claim gate commits.
        with factory() as other_db:
            setup_preparation.enqueue(other_db, project_id=None, capabilities=["image"], settings=settings)
        if claim_guard is not None:
            claim_guard(db)
        return orig_gen(db, *args, claim_guard=claim_guard, **kwargs)

    monkeypatch.setattr(deployment, "generate_configuration", wrapped_gen)
    claimed["target"] = {"root": str(root), "port": 8188, "instance_id": "i1"}
    with pytest.raises(setup_preparation.ClaimLostError):
        setup_preparation._step_recipe(factory, settings, claimed)
    with factory() as db:
        assert db.get(setup_preparation.AppSetting, runtime_configuration.CANDIDATE_KEY) is None


def test_bare_image_download_and_instance_steps_avoid_h3_qualification_gate(env, monkeypatch):
    """R5 CE-9: Standalone image DOWNLOAD -> INSTANCE runs without H3 variant qualification."""
    from shine import api_setup
    from shine.comfyops import deployment, host_env, variants

    _client, factory, tmp_path, settings = env
    monkeypatch.setattr("shine.comfyops.storage.on_system_drive", lambda *_a, **_k: False)
    monkeypatch.setattr("shine.comfyops.storage.disk_info", lambda *_a, **_k: {"free_bytes": 10**12})
    monkeypatch.setattr(
        host_env, "probe_host_facts",
        lambda *_a, **_k: {
            "platform": {"os": "windows", "arch": "x64"},
            "gpus": [{"name": "RTX 4070", "vram_mb": 12288, "compute_capability": "8.9"}],
            "ram": {"ram_mb": 32768},
            "tools": {"ffmpeg": True, "git": True, "uv": True},
        },
    )
    monkeypatch.setattr(
        variants, "qualify",
        lambda *_a, **_k: pytest.fail("H3 host qualification must not run for standalone image"),
    )
    queued_plans: list[dict] = []
    orig_install_plan = api_setup.install_plan

    def spy_install_plan(*, body, settings, db):
        queued_plans.append({"catalog": body.catalog, "components": list(body.components)})
        return orig_install_plan(body=body, settings=settings, db=db)

    monkeypatch.setattr(api_setup, "install_plan", spy_install_plan)
    monkeypatch.setattr("shine.comfyops.local.queue_install_attempt", lambda *_a, **_k: ("attempt-1", False))
    task = {"project_id": None, "task_id": "t-img", "request": {"capabilities": ["image"]}, "downloads": {}}
    dl = setup_preparation._step_download(factory, settings, task)
    assert dl["status"] == "PENDING", dl
    assert "runtime" in task["downloads"]
    assert any({"comfyui", deployment.WHEEL_COMPONENT_ID} <= set(p["components"]) for p in queued_plans)

    seen_inst_catalogs: list[dict] = []
    monkeypatch.setattr(
        "shine.comfyops.deployment.next_instance_plan",
        lambda _db, _s, catalog, _pid, **_k: seen_inst_catalogs.append(catalog) or (None, "READY"),
    )
    inst = setup_preparation._step_instance(factory, settings, task)
    assert inst["status"] == "DONE"
    assert seen_inst_catalogs
    ids = {c["component_id"] for c in seen_inst_catalogs[0]["components"]}
    assert "comfyui" in ids

def test_an_identical_request_after_failure_resumes_from_the_first_unfinished_step(env):
    """同一授权在 RECIPE 失败后重发必须续跑，而不是从 SCAN 整单重来。

    页面与取消路径都承诺「前面完成的步骤会保留」（2026-11-18 复现：整备停在
    RECIPE 后再点「核对运行合同」，得到新 task_id、已完成步骤数归零）。同一
    请求身份恢复同一任务，旧 claim/委托写回必须失效。
    """
    client, factory, _, _ = env
    body = {"capabilities": ["h3"], "verify_runtime": True}
    first = client.post("/api/v1/setup/prepare/execute", json=body, headers=LOCAL).json()
    assert first["status"] == "QUEUED"
    assert "RECIPE" in first["steps"]

    with factory.begin() as db:
        task = setup_preparation.read_task(db)
        value = dict(task)
        value["status"] = "FAILED"
        value["current"] = "RECIPE"
        value["error"] = "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK"
        value["claim_token"] = "stale"
        steps = [dict(row) for row in value["steps"]]
        for step in steps:
            step["status"] = "FAILED" if step["id"] == "RECIPE" else "DONE"
        value["steps"] = steps
        db.get(AppSetting, setup_preparation.TASK_KEY).value = value

    again = client.post("/api/v1/setup/prepare/execute", json=body, headers=LOCAL).json()
    assert again["task_id"] == first["task_id"], "同一授权是同一个任务身份，不是一条新任务"
    assert again["status"] == "QUEUED"
    with factory() as db:
        task = setup_preparation.read_task(db)
        assert task["current"] == "RECIPE", "必须从第一个未完成步骤继续"
        statuses = {row["id"]: row["status"] for row in task["steps"]}
        assert all(status == "DONE" for step_id, status in statuses.items() if step_id != "RECIPE"), statuses
        assert statuses["RECIPE"] == "PENDING", statuses
        assert task.get("claim_token") is None
        assert task.get("lease_owner") is None


def test_recipe_waits_for_a_live_lease_and_continues_without_another_click(env, monkeypatch):
    from datetime import timedelta

    from shine.comfyops import deployment, runtime_configuration
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.models import utcnow

    client, factory, _, settings = env
    first = client.post("/api/v1/setup/prepare/execute", headers=LOCAL,
                        json={"capabilities": ["h3"]}).json()
    with factory.begin() as db:
        row = db.get(AppSetting, setup_preparation.TASK_KEY)
        task = dict(row.value)
        task["target"] = {"instance_id": "configured", "port": 8189}
        for step in task["steps"]:
            if step["id"] == "RECIPE":
                break
            step["status"] = "DONE"
        row.value = task
        lease = ResourceLease(owner="h3:in-flight", status="ACTIVE", exclusive=True,
                              priority=10, budget_mb=1, expires_at=utcnow() + timedelta(minutes=1))
        db.add(lease)
        db.flush()
        lease_id = lease.id

    configured = []

    def generate(db, *_args, **_kwargs):
        runtime_configuration.require_idle_configuration(db)
        configured.append(True)
        return {"recipe_sha256": "configured"}

    monkeypatch.setattr(deployment, "generate_configuration", generate)
    assert setup_preparation.advance(factory, settings, "test-worker") == "setup:RECIPE:waiting"
    with factory.begin() as db:
        task = setup_preparation.read_task(db)
        assert task["status"] == "RUNNING"
        assert task["task_id"] == first["task_id"]
        assert "h3:in-flight" in task["message"]
        assert configured == []
        ResourceScheduler().release(db, lease_id, "h3:in-flight")

    assert setup_preparation.advance(factory, settings, "test-worker") == "setup:RECIPE"
    with factory() as db:
        task = setup_preparation.read_task(db)
        assert task["task_id"] == first["task_id"]
        assert task["result"]["recipe_sha256"] == "configured"
    assert configured == [True]

"""一键整备的 INSTANCE 步：裸机阶梯、单授权、等待下载、对账不盲重试。"""

from __future__ import annotations

from dataclasses import replace

from shine.comfy.workflow import graph_hash
from shine.comfyops import setup_preparation
from shine.comfyops.models import InstallationPlan
from tests.conftest import create_project


def _plan_row(db, project_id, status="AWAITING_APPROVAL", files=None):
    payload = {
        "kind": "INSTANCE_LIFECYCLE",
        "packages": [{"id": "pkg-1", "hash": "h", "spec": {
            "license_id": "MIT", "usage": "research",
            "files": files or [],
        }}],
        "operation": {"action": "CREATE"},
    }
    row = InstallationPlan(project_id=project_id, payload=payload,
                           sha256=graph_hash(payload), status=status)
    db.add(row)
    db.commit()
    return row


def test_steps_include_instance_first():
    steps = setup_preparation._steps_for(["h3"], verify_runtime=True)
    assert steps[0] == "INSTANCE"
    assert "TARGET" in steps and "VALIDATE" in steps


def test_instance_step_skips_when_installation_exists(studio, monkeypatch):
    _client, factory, _tmp, settings = studio
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {"instance_id": "i-1"}})
    task = {"project_id": "proj-1", "request": {}}
    detail = setup_preparation._step_instance(factory, settings, task)
    assert detail["status"] == "DONE"


def test_instance_step_waits_for_missing_bytes(studio, monkeypatch):
    client, factory, tmp_path, settings = studio
    project_id = create_project(client)
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    with factory.begin() as db:
        row = _plan_row(db, project_id, files=[{"sha256": "a" * 64, "size": 10, "relative_path": "f",
                                    "url": "https://example.invalid/f"}])
        plan_id = row.id
    monkeypatch.setattr("shine.comfyops.deployment.next_instance_plan",
                        lambda *a, **k: (type("P", (), {"id": plan_id})(), "CREATE"))
    settings = replace(settings, data_dir=tmp_path / "data")
    detail = setup_preparation._step_instance(factory, settings, {"project_id": project_id, "request": {}})
    assert detail["status"] == "PENDING"
    assert "等待" in detail["detail"]
    with factory() as db:
        assert db.get(InstallationPlan, plan_id).status == "AWAITING_APPROVAL"


def test_instance_step_approves_under_task_authorization_and_executes(studio, monkeypatch):
    client, factory, tmp_path, settings = studio
    project_id = create_project(client)
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    with factory.begin() as db:
        row = _plan_row(db, project_id)
        plan_id = row.id
    monkeypatch.setattr("shine.comfyops.deployment.next_instance_plan",
                        lambda *a, **k: (type("P", (), {"id": plan_id})(), "CREATE"))
    settings = replace(settings, data_dir=tmp_path / "data")
    executed = {}

    def fake_execute(factory_, settings_, identity, digest, actor, **kw):
        executed.update(identity=identity, digest=digest, actor=actor)

    monkeypatch.setattr("shine.comfyops.instances.execute_operation", fake_execute)
    task = {"project_id": project_id, "task_id": "setup-test", "request": {}}
    detail = setup_preparation._step_instance(factory, settings, task)
    assert detail["status"] == "PENDING"
    assert executed["actor"] == "worker:setup"
    with factory() as db:
        row = db.get(InstallationPlan, plan_id)
        assert row.status == "APPROVED"
        assert row.payload["approved_by"] == "worker:setup"
        assert row.payload["approval_evidence"] == "setup-preparation:setup-test"
        assert executed["digest"] == row.sha256


def test_instance_step_needs_review_stops_named(studio, monkeypatch):
    client, factory, _tmp, settings = studio
    project_id = create_project(client)
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    with factory.begin() as db:
        row = _plan_row(db, project_id, status="NEEDS_REVIEW")
        plan_id = row.id
    monkeypatch.setattr("shine.comfyops.deployment.next_instance_plan",
                        lambda *a, **k: (type("P", (), {"id": plan_id})(), "CREATE"))
    detail = setup_preparation._step_instance(factory, settings, {"project_id": project_id, "request": {}})
    assert detail["status"] == "FAILED"
    assert detail["code"] == "INSTANCE_REQUIRES_RECONCILIATION"


def test_runtime_closure_gap_bare_machine(studio):
    _client, factory, _tmp, settings = studio
    from shine.comfyops.setup_summary import runtime_closure_gap
    with factory() as db:
        gap = runtime_closure_gap(db, settings, None)
    assert gap["instance_present"] is False
    assert gap["instance_ladder"] == ["CREATE", "INSTALL_NODE", "BIND_MODELS"]
    assert "comfyui" in gap["missing_components"]


def test_prepare_plan_reports_runtime_gap_and_closure(studio, fake_deployment_host):
    client, _factory, _tmp, _settings = studio
    project_id = create_project(client)
    response = client.post("/api/v1/setup/prepare-plan",
                           json={"project_id": project_id, "capabilities": ["h3"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_gap"]["instance_ladder"][0] == "CREATE"
    h3 = next(row for row in body["plans"] if row["capability"] == "h3")
    assert "comfyui" in h3["components"]
    assert h3["download_bytes"] > 0
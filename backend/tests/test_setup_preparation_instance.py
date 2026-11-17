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
    # 一次授权从补工具开始,核验已有字节,下载缺项,然后才创建实例。
    assert steps[:4] == ["TOOL_REPAIR", "RECONCILE", "DOWNLOAD", "INSTANCE"]
    assert "TARGET" in steps and "VALIDATE" in steps


def test_instance_step_skips_when_installation_exists(studio, monkeypatch):
    # 外部复用（adopted）的安装只读：实例阶梯不得改动它，直接跳过。
    # 受管半成品安装不在此列——它交回同一架阶梯继续判断（见 test_setup_machine_reuse）。
    _client, factory, _tmp, settings = studio
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {"instance_id": "i-1", "adopted": True}})
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


def test_enqueue_includes_instance_only_on_bare_machine(studio, monkeypatch):
    client, factory, _tmp, settings = studio
    project_id = create_project(client)
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    with factory() as db:
        bare = setup_preparation.enqueue(db, project_id=project_id,
                                         capabilities=["h3"], settings=settings)
    assert [row["id"] for row in bare["steps"]][:4] == ["TOOL_REPAIR", "RECONCILE", "DOWNLOAD", "INSTANCE"]
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {"instance_id": "i-1", "adopted": True}})
    project2 = create_project(client, title="已有安装")
    with factory() as db:
        existing = setup_preparation.enqueue(db, project_id=project2,
                                             capabilities=["h3"], settings=settings)
    assert existing["steps"][0]["id"] == "TOOL_REPAIR"
    assert "INSTANCE" not in [row["id"] for row in existing["steps"]]


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


def test_instance_step_on_empty_database_passes_no_project(studio, monkeypatch):
    """空库整备：实例是机器级依赖，创建不该报 Project not found。

    旧代码把 None 项目转成字符串 "None" 传给实例阶梯，项目门禁对着不存在的
    "None" 作品报错，整备受理后卡在实例创建。机器级依赖必须在建作品前就能整备。
    """
    _client, factory, _tmp, settings = studio
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})
    captured = {}

    def fake_next(db, settings, catalog, project_id, **kw):
        captured["project_id"] = project_id
        return None, None

    monkeypatch.setattr("shine.comfyops.deployment.next_instance_plan", fake_next)
    detail = setup_preparation._step_instance(factory, settings, {"project_id": None, "request": {}})
    assert detail["status"] == "DONE"
    assert captured["project_id"] is None, "机器级实例计划不得把 None 变成字符串 'None'"


def test_machine_level_instance_plan_does_not_require_a_project(studio):
    """项目门禁对机器级（None）放行；后续拒绝只能是权重/实例原因，不是没有作品。"""
    from fastapi import HTTPException

    from shine.comfyops import deployment

    _client, factory, _tmp, settings = studio
    with factory() as db:
        assert deployment._require_project(db, None) is None
        try:
            deployment.next_instance_plan(
                db, settings, deployment.load_default_catalog(), None,
                instance_name="shine-h3", port=8188, mode="local")
        except HTTPException as exc:
            detail = exc.detail
            text = detail.get("code") if isinstance(detail, dict) else str(detail)
            assert "Project not found" not in str(text)
            assert "PROJECT" not in str(text).upper()


def test_optional_review_download_does_not_block_the_production_chain(studio, monkeypatch):
    """H3 文件齐备时，可选 ASR 下载失败或下载中都不卡住后续整备。

    审片沿用自己的安装记录在后台推进；生产链只按自己的实际依赖推进。旧代码
    把 ASR 的 FAILED/PENDING 当成整个 DOWNLOAD 步的结论，H3 链被无关下载卡死。
    """
    from shine.comfyops.models import InstallationAttempt, InstallationPlan

    _client, factory, _tmp, settings = studio
    monkeypatch.setattr("shine.comfyops.setup_summary.resolve_installation",
                        lambda db, settings, path=None: {"selected": {}})

    def _asr_attempt(status, stage):
        with factory.begin() as db:
            plan = InstallationPlan(project_id=None, payload={"packages": [{"id": "qwen3-asr-0.6b"}]},
                                    sha256="x" * 64, status="APPROVED")
            db.add(plan)
            db.flush()
            attempt = InstallationAttempt(plan_id=plan.id, status=status, stage=stage,
                                          payload={}, sha256="y" * 64,
                                          error_code="UNTRUSTED_DOWNLOAD_URL")
            db.add(attempt)
            db.flush()
            return attempt.id

    for status, stage in [("FAILED_RECONCILE_REQUIRED", "NEEDS_REVIEW"), ("INSTALLING", "DOWNLOADING")]:
        attempt_id = _asr_attempt(status, stage)
        task = {"project_id": None, "request": {"capabilities": ["h3", "asr"]},
                "downloads": {"h3": {"done": True}, "asr": {"attempt_id": attempt_id}}}
        detail = setup_preparation._step_download(factory, settings, task)
        assert detail["status"] == "DONE", f"ASR {status} 不应卡住生产链：{detail}"
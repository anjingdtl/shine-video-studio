"""任务异常的自动断点续跑（穿测发现的可用性缺口）。

子调用失败后任务停在 NEEDS_REVIEW/FAILED 等人点重试，普通用户只能发呆。
补法落在三原则内：一个执行者（prepare_scenes 既有退休重发）、一个裁决
（既有 external_outcome 分类）、一套事实（task trace）。可证明安全的失败
（NOT_SENT / RESPONSE_RECEIVED，没有待决的外部答案）在预算内自动重新触发
同一断点；UNKNOWN（可能已发出）绝不自动重放，仍等人授权。
"""
from __future__ import annotations

from sqlalchemy import select

from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _prepare, _responder


def _failed_scene_task(factory, project, client, doc, *, external_outcome,
                       status="FAILED", auto_retired_before=0):
    """Seed the real scene task as a settled failure with a classified outcome."""
    client.app.state.mock_llm_responder = _responder
    _prepare(client, project, doc)
    with factory.begin() as db:
        real = db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "scriptwriter")
            .order_by(AgentTaskRecord.created_at.desc())
        ).first()
        db.query(AgentArtifact).filter(AgentArtifact.source_task_id == real.id).delete()
        for _ in range(auto_retired_before):
            db.add(AgentTaskRecord(
                project_id=project, role_id="scriptwriter", input_hash=real.input_hash,
                request_identity=None, asset_manifest_hash=real.asset_manifest_hash,
                rulepack_id="material-script-prepare", rulepack_hash="x",
                model_profile_id="x", model_id="x", schema_version="1.0", status="SUPERSEDED",
                trace={**real.trace, "external_outcome": "NOT_SENT",
                       "reconciliation": {"outcome": "NOT_SENT_PROVEN", "auto_retry": True}},
            ))
        real.status = status
        real.created_at = utcnow()
        real.trace = {**real.trace, "phase": "FAILED", "external_outcome": external_outcome}
        failed_id = real.id
    return failed_id


def test_a_not_sent_failure_is_re_triggered_without_a_person(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    failed_id = _failed_scene_task(factory, project, client, doc, external_outcome="NOT_SENT")
    client.app.state.mock_llm_responder = counted
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "PREPARED", result
    assert len(calls) == 1, "the safe breakpoint is re-triggered automatically"
    with factory() as db:
        old = db.get(AgentTaskRecord, failed_id)
        assert old.status == "SUPERSEDED"
        assert old.request_identity is None
        assert old.trace["reconciliation"]["outcome"] == "NOT_SENT_PROVEN"
        assert old.trace["reconciliation"]["auto_retry"] is True


def test_a_response_received_failure_is_re_triggered_within_budget(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    failed_id = _failed_scene_task(factory, project, client, doc, external_outcome="RESPONSE_RECEIVED")
    client.app.state.mock_llm_responder = _responder
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "PREPARED", result
    with factory() as db:
        old = db.get(AgentTaskRecord, failed_id)
        assert old.status == "SUPERSEDED"
        assert old.trace["reconciliation"]["outcome"] == "RESPONSE_RECEIVED_NO_ANSWER"
        assert old.trace["reconciliation"]["auto_retry"] is True


def test_an_unknown_failure_is_never_auto_replayed(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def forbidden(messages, schema):
        calls.append(1)
        raise AssertionError("an unknown outcome must never be auto-replayed")

    failed_id = _failed_scene_task(factory, project, client, doc,
                                   external_outcome="UNKNOWN", status="NEEDS_REVIEW")
    client.app.state.mock_llm_responder = forbidden
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "NEEDS_REVIEW", result
    assert result["external_outcome"] == "UNKNOWN"
    assert calls == [], "the provider must not be called again automatically"
    with factory() as db:
        old = db.get(AgentTaskRecord, failed_id)
        assert old.status == "NEEDS_REVIEW"
        assert old.request_identity is not None, "an unknown outcome keeps its identity"


def test_the_auto_retry_budget_is_bounded(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    failed_id = _failed_scene_task(factory, project, client, doc,
                                   external_outcome="NOT_SENT", auto_retired_before=2)
    client.app.state.mock_llm_responder = counted
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "FAILED", result
    assert calls == [], "the budget stops the automatic re-trigger"
    with factory() as db:
        old = db.get(AgentTaskRecord, failed_id)
        assert old.status == "FAILED"
        assert old.request_identity is not None
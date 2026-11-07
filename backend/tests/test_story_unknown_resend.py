"""A person may ask one unknown scene again - never by itself.

The old request may still own an answer upstream, so the retry only happens
when a person confirms it, the decision is audited, and the scene is asked
exactly once more.  The automatic path stays closed.
"""
from __future__ import annotations

import time

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentTaskRecord
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _prepare, _responder
from tests.test_story_outline_unblock import _blocked_outline
from tests.test_story_preparation import LOCAL, responder


def test_a_person_may_ask_one_unknown_scene_again_and_the_decision_is_audited(api, monkeypatch):
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, status="NEEDS_REVIEW", phase="WAITING_PROVIDER",
        external_outcome="UNKNOWN", error_code="LLM_TIMEOUT")
    calls = []

    def counted(messages, schema):
        calls.append(schema.get("title"))
        return responder(messages, schema)

    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=counted)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    # The block still holds: a resume is refused until a person decides.
    refused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": version}, headers=LOCAL)
    assert refused.status_code == 409
    assert calls == []

    decision = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resend-unknown", json={
        "expected_state_version": version, "subtask_id": stuck_id,
        "evidence": "我确认重新提问这一场"}, headers=LOCAL)
    assert decision.status_code == 200, decision.text
    assert decision.json()["decision"]["resend_allowed"] is True
    assert calls == [], "the decision itself never calls the provider"
    with factory() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        assert stuck.status == "SUPERSEDED"
        assert stuck.request_identity is None, "the identity is released for the new request"
        assert stuck.trace["reconciliation"]["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"
        # The truth is preserved: the old request was never called "not sent".
        assert stuck.trace["reconciliation"].get("outcome") != "NOT_SENT_PROVEN"
        blocked = db.get(AgentTaskRecord, task_id).trace["blocked_subtasks"]
        assert blocked[0]["status"] == "RETRY_AUTHORIZED"

    resumed = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": decision.json()["state_version"]}, headers=LOCAL)
    assert resumed.status_code == 200, resumed.text
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "restarted-worker", gateway=gateway):
            break
    assert calls, "the scene is asked again with a new request"


def test_the_same_scene_may_not_be_asked_again_twice(api, monkeypatch):
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, status="NEEDS_REVIEW", phase="WAITING_PROVIDER",
        external_outcome="UNKNOWN", error_code="LLM_TIMEOUT")
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    first = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resend-unknown", json={
        "expected_state_version": version, "subtask_id": stuck_id,
        "evidence": "我确认重新提问这一场"}, headers=LOCAL)
    assert first.status_code == 200, first.text
    # A second decision for the same scene is refused: one retry, one scene.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.status = "NEEDS_REVIEW"
        row.trace = {**row.trace, "blocked_subtasks": [{
            "scene_key": "S2", "status": "NEEDS_REVIEW", "task_id": stuck_id,
            "error_code": "LLM_TIMEOUT", "external_outcome": "UNKNOWN"}]}
        second_version = row.state_version
    second = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resend-unknown", json={
        "expected_state_version": second_version, "subtask_id": stuck_id,
        "evidence": "再确认一次"}, headers=LOCAL)
    assert second.status_code == 409, second.text
    assert "RESEND_ALREADY_USED" in second.text


def test_two_scene_calls_keep_a_gap_between_them(api, monkeypatch):
    """One outline makes one call per scene; the calls must not be adjacent."""
    client, factory, project, doc = setup_scene(
        api, monkeypatch, "场景一：房间\n阿德：出发。\n场景二：码头\n阿德：等船。")
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "300")
    stamps = []

    def timed(messages, schema):
        stamps.append(time.monotonic())
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = timed
    _prepare(client, project, doc)
    assert len(stamps) == 2, stamps
    assert stamps[1] - stamps[0] >= 0.3, "two provider calls must keep a real gap"

"""Durable outline orchestration with a mock text transport, never H3/Qwen evidence."""
import json
from datetime import timedelta

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _responder

LOCAL = {"x-shine-local-action": "1"}


def responder(messages, schema):
    if "scene_key" in schema.get("properties", {}):
        return _responder(messages, schema)
    payload = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
    assert "parts" in payload
    return {"schema_version": "1.0", "summary": "主角通过章节中的行动推进剧情。",
            "story_line": ["开端", "发展"], "timeline": ["按原文顺序"], "relationships": [], "questions": []}


def _queue(client, project, doc):
    response = client.post(f"/api/v1/projects/{project}/story-preparation/outlines", json={
        "document_id": doc["id"], "expected_document_hash": doc["sha256"], "locked_facts": ["保留前言事实"]},
        headers=LOCAL)
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


def test_outline_is_queued_claimed_resumed_and_merged_with_complete_source(api, monkeypatch):
    text = "前言事实\n第一章 起点\n" + "剧情。" * 3100 + "\n第二章 回来\n主角回来。"
    client, factory, project, doc = setup_scene(api, monkeypatch, text)
    calls = []
    def transport(messages, schema):
        calls.append(schema.get("title"))
        return responder(messages, schema)
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=transport)
    task_id = _queue(client, project, doc)
    assert _queue(client, project, doc) == task_id and calls == []
    assert story.run_pending_story_task(factory, api[2], "worker-before-restart", gateway=gateway)
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        cursor, version = task.trace["cursor"], task.state_version
    assert cursor == 1
    paused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/pause", json={
        "expected_state_version": version}, headers=LOCAL).json()
    assert paused["status"] == "PAUSED"
    assert not story.run_pending_story_task(factory, api[2], "worker-restarted", gateway=gateway)
    assert client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": paused["state_version"]}, headers=LOCAL).status_code == 200
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "worker-restarted", gateway=gateway):
            break
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "SUCCEEDED", task.trace
        artifact = db.get(AgentArtifact, task.trace["outline_artifact_id"])
        assert not artifact.approved_at
        sections = artifact.payload_json["production_sections"]
        assert sections[0]["start_offset"] == 0 and sections[-1]["end_offset"] == len(text)
        assert artifact.payload_json["locked_facts"] == ["保留前言事实"]
        assert len(task.trace["leaf_artifact_ids"]) > 3
        assert "OutlinePart" in calls
        assert task.trace["level"] >= 1
    assert not story.run_pending_story_task(factory, api[2], "another-worker", gateway=gateway)
    assert _queue(client, project, doc) == task_id


def test_unknown_extraction_survives_worker_restart_and_cannot_resume_into_replay(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []
    def lost(*args):
        calls.append(1)
        raise TimeoutError("lost response")
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=lost)
    task_id = _queue(client, project, doc)
    assert story.run_pending_story_task(factory, api[2], "first-worker", gateway=gateway)
    assert not story.run_pending_story_task(factory, api[2], "restarted-worker", gateway=gateway)
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "NEEDS_REVIEW"
        version = row.state_version
    refused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": version}, headers=LOCAL)
    assert refused.status_code == 409
    assert len(calls) == 1
    assert _queue(client, project, doc) == task_id


def test_expired_root_claim_never_replays_a_running_external_child(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    task_id = _queue(client, project, doc)
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=responder)
    assert story.run_pending_story_task(factory, api[2], "first", gateway=gateway)
    with factory.begin() as db:
        root = db.get(AgentTaskRecord, task_id)
        leaf = db.get(AgentArtifact, root.trace["leaf_artifact_ids"][0])
        db.get(AgentTaskRecord, leaf.source_task_id).status = "RUNNING"
        root.trace = {**root.trace, "cursor": 0, "leaf_artifact_ids": []}
        root.status, root.lease_owner = "RUNNING", "dead-worker"
        root.lease_expires_at = utcnow() - timedelta(seconds=1)
    calls = []
    def forbidden(*args):
        calls.append(1)
        raise AssertionError("must not call again")
    restarted = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=forbidden)
    assert story.run_pending_story_task(factory, api[2], "new-worker", gateway=restarted)
    assert calls == []
    with factory() as db:
        assert db.get(AgentTaskRecord, task_id).status == "NEEDS_REVIEW"


def test_stale_video_configuration_does_not_block_text_queue_or_bypass_h3(api, monkeypatch):
    import pytest
    from fastapi import HTTPException

    from shine import worker
    from shine.comfyops import runtime_configuration

    def stale(*args):
        raise HTTPException(409, "ACTIVE_RUNTIME_CONFIGURATION_CHANGED")
    monkeypatch.setattr(runtime_configuration, "resolve_runtime", stale)
    calls = []
    def text_only(*args, **kwargs):
        calls.append(kwargs["heartbeat_fn"])
        return True
    monkeypatch.setattr(story, "run_pending_story_task", text_only)
    monkeypatch.setattr(worker, "_run_configured_once", lambda *a, **k: pytest.fail("H3 guard bypass"))
    assert worker.run_configured_once(api[1], None, "text-worker", settings=api[2]) == "story-preparation"
    assert len(calls) == 1 and callable(calls[0])
    monkeypatch.setattr(story, "run_pending_story_task", lambda *a, **k: False)
    with pytest.raises(HTTPException, match="ACTIVE_RUNTIME_CONFIGURATION_CHANGED"):
        worker.run_configured_once(api[1], None, "text-worker", settings=api[2])


def test_story_claim_heartbeat_is_fenced_by_pause(api, monkeypatch):
    from threading import Event
    client, factory, project, doc = setup_scene(api, monkeypatch)
    task_id = _queue(client, project, doc)
    with factory.begin() as db:
        root = db.get(AgentTaskRecord, task_id)
        root.status, root.lease_owner, root.state_version = "RUNNING", "owner", 10
        root.lease_expires_at = utcnow() + timedelta(seconds=1)
    beat = Event()
    with story._renew_story_claim(factory, task_id, 10, "owner", beat.set, interval=0.02):
        assert beat.wait(1)
        with factory() as db:
            root = db.get(AgentTaskRecord, task_id)
            assert root.lease_expires_at > utcnow() + timedelta(minutes=20)
        with factory.begin() as db:
            root = db.get(AgentTaskRecord, task_id)
            root.status, root.state_version, root.lease_expires_at = "PAUSED", 11, None
        # A second renewal observes the changed version rather than reviving it.
        with story._renew_story_claim(factory, task_id, 10, "owner", interval=0.02):
            with factory() as db:
                root = db.get(AgentTaskRecord, task_id)
                assert root.status == "PAUSED" and root.lease_expires_at is None

"""Durable outline orchestration with a mock text transport, never H3/Qwen evidence."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _responder

pytestmark = pytest.mark.integration

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
        # The window is measured from the provider's budget now: a chapter that
        # fits is read once instead of being cut up and glued back together.
        # What must never change is that every produced section traces back to a
        # leaf and that the outline covers all source bytes.  The multi-leaf
        # merge path is exercised by the resumed case and by the unit tests in
        # test_literary_beats.
        leaves = task.trace["leaf_artifact_ids"]
        assert leaves, "the outline must record the leaves it was built from"
        # One read may yield several sections: the leaf records the source it
        # covered, the sections record the scenes it found in it.  A one-to-one
        # count is a chunking accident, not a contract.
        assert sections, "a merged outline must produce at least one section"
        assert "OutlinePart" in calls
        # The leaves are merged into a single outline.  A second merge round is
        # only needed when the first one produced more than one output, so the
        # level counter belongs to the chunking policy, not to the contract.
        assert len(task.trace["level_outputs"]) == 1
        assert artifact.kind == "StoryOutline"
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
    # The text queues run before the video runtime is resolved, so a stale
    # renderer configuration cannot freeze them; the tick reports the merged
    # text-preparation stage that actually ran.
    assert worker.run_configured_once(api[1], None, "text-worker", settings=api[2]) == "text-preparation"
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


def _blocked_outline(api, monkeypatch, phase):
    """Reproduce the state the interrupted round left behind.

    A scene subcall is created (its own identity, its own RUNNING status) and
    the parent records it as the blocking subtask; the Worker died before the
    subcall decided anything, so the only evidence of how far it got is the
    phase it last wrote.
    """
    from shine.materialprep.source_spans import segment_document
    from shine.models import ProjectDocument

    client, factory, project, doc = setup_scene(
        api, monkeypatch, "场景一：房间\n阿德：出发。\n场景二：码头\n阿德：等船。")
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=responder)
    task_id = _queue(client, project, doc)
    assert story.run_pending_story_task(factory, api[2], "first-worker", gateway=gateway)
    with factory() as db:
        text = db.get(ProjectDocument, doc["id"]).text
    source = segment_document(text)
    assert len(source) >= 2
    with factory.begin() as db:
        root = db.get(AgentTaskRecord, task_id)
        stuck = AgentTaskRecord(
            project_id=project, role_id="scriptwriter", input_hash="interrupted-" + source[1].key,
            request_identity="interrupted-" + source[1].key, asset_manifest_hash="x",
            rulepack_id="shine-material-prep/1", rulepack_hash="x", model_profile_id="x", model_id="x",
            schema_version="1.0", status="RUNNING",
            trace={"tool": "script_prepare", "scene_key": source[1].key, "document_id": doc["id"],
                   "document_sha256": doc["sha256"], "phase": phase, "external_outcome": "UNKNOWN",
                   "automatic_replay": False},
        )
        db.add(stuck)
        db.flush()
        root.trace = {**root.trace, "cursor": 1,
                      "blocked_subtasks": [{"scene_key": source[1].key, "status": "RUNNING",
                                            "task_id": stuck.id, "error_code": None,
                                            "external_outcome": "UNKNOWN", "automatic_replay": False}]}
        root.status, root.lease_owner, root.lease_expires_at = "NEEDS_REVIEW", None, None
        root.state_version += 1
        stuck_id = stuck.id
    return client, factory, project, doc, task_id, stuck_id


def test_interrupted_before_send_reconciles_as_not_sent_and_resumes(api, monkeypatch):
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, "WAITING_RESOURCE")
    calls = []

    def counted(messages, schema):
        calls.append(schema.get("title"))
        return responder(messages, schema)

    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=counted)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    # Resume is refused before the block is decided, and nothing is called.
    assert client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": version}, headers=LOCAL).status_code == 409
    assert calls == []
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile", json={
        "expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["reconciliation"]["outcomes"][0]["outcome"] == "NOT_SENT_PROVEN"
    assert body["reconciliation"]["resumable"] is True
    assert body["resubmitted"] is False
    assert calls == []
    with factory() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        assert stuck.status == "SUPERSEDED"
        assert stuck.trace["external_outcome"] == "NOT_SENT"
        # The identity was never consumed, so it is released for the new request
        # instead of holding the project-wide unique claim.
        assert stuck.request_identity is None
        assert stuck.trace["previous_request_identity"] == "interrupted-S2"
        assert db.get(AgentTaskRecord, task_id).trace["blocked_subtasks"][0]["status"] == "NOT_SENT"
    resumed = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": body["state_version"]}, headers=LOCAL)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "QUEUED" and resumed.json()["progress"]["blocked_subtasks"] == []
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "restarted-worker", gateway=gateway):
            break
    with factory() as db:
        assert db.get(AgentTaskRecord, task_id).status == "SUCCEEDED"
        # The retired identity is never reused: the scene got a new request.
        succeeded = [row.id for row in db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.status == "SUCCEEDED",
            AgentTaskRecord.role_id == "scriptwriter"))]
        assert stuck_id not in succeeded
    assert calls


def test_reconcile_never_resumes_a_child_that_may_have_been_sent(api, monkeypatch):
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, "WAITING_PROVIDER")
    calls = []

    def forbidden(messages, schema):
        calls.append(1)
        raise AssertionError("must not call again")

    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=forbidden)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    # A NEEDS_REVIEW parent is not claimable, so no tick can reach the model.
    assert story.run_pending_story_task(factory, api[2], "restarted-worker", gateway=gateway) is False
    assert calls == []
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile", json={
        "expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["reconciliation"]["outcomes"][0]["outcome"] == "UNCONFIRMABLE"
    assert body["reconciliation"]["resumable"] is False
    with factory() as db:
        assert db.get(AgentTaskRecord, stuck_id).status == "RUNNING"
    refused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume", json={
        "expected_state_version": body["state_version"]}, headers=LOCAL)
    assert refused.status_code == 409
    assert calls == []


def test_outline_questions_need_an_explicit_acknowledgement(api, monkeypatch):
    from shine.h3.rulebook import digest

    client, factory, project, doc = setup_scene(api, monkeypatch)
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=responder)
    task_id = _queue(client, project, doc)
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "worker", gateway=gateway):
            break
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        outline = db.get(AgentArtifact, task.trace["outline_artifact_id"])
        payload = {**outline.payload_json, "questions": ["老周到底有没有把表交出去？"]}
        outline.payload_json = payload
        outline.sha256 = digest(payload)
    view = client.get(f"/api/v1/projects/{project}/story-preparation", headers=LOCAL).json()[0]["outline"]
    assert view["questions_sha256"]
    silent = client.post(f"/api/v1/projects/{project}/story-preparation/outlines/{view['artifact_id']}/confirm",
                         json={"expected_sha256": view["sha256"]}, headers=LOCAL)
    assert silent.status_code == 409
    wrong = client.post(f"/api/v1/projects/{project}/story-preparation/outlines/{view['artifact_id']}/confirm",
                        json={"expected_sha256": view["sha256"],
                              "acknowledge_questions_sha256": "0" * 64}, headers=LOCAL)
    assert wrong.status_code == 409
    accepted = client.post(f"/api/v1/projects/{project}/story-preparation/outlines/{view['artifact_id']}/confirm",
                           json={"expected_sha256": view["sha256"],
                                 "acknowledge_questions_sha256": view["questions_sha256"]}, headers=LOCAL)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["approved"] is True
    with factory() as db:
        assert db.get(AgentArtifact, view["artifact_id"]).approved_at is not None
        assert db.get(AgentArtifact, view["artifact_id"]).sha256 == digest(
            db.get(AgentArtifact, view["artifact_id"]).payload_json)


def test_only_not_sent_proven_scene_failure_is_retried_with_a_bound(api, monkeypatch):
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(api, monkeypatch, "FAILED")
    with factory.begin() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        stuck.status = "FAILED"
        stuck.error_code = "LLM_FORMAT"
        stuck.trace = {**stuck.trace, "external_outcome": "NOT_SENT"}
        db.get(AgentTaskRecord, task_id).trace = {
            **db.get(AgentTaskRecord, task_id).trace,
            "blocked_subtasks": [{**db.get(AgentTaskRecord, task_id).trace["blocked_subtasks"][0],
                                  "status": "FAILED", "error_code": "LLM_FORMAT"}],
        }
        version = db.get(AgentTaskRecord, task_id).state_version
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile", json={
        "expected_state_version": version}, headers=LOCAL).json()
    outcome = checked["reconciliation"]["outcomes"][0]
    assert outcome["outcome"] == "NOT_SENT_PROVEN" and outcome["attempt"] == 1
    assert checked["reconciliation"]["resumable"] is True
    # The bound is per scene: after the budget a person has to look at it.
    for attempt in (2, 3):
        with factory.begin() as db:
            again = _blocked_outline_state(db, project, doc, task_id, stuck_id, attempt)
        result = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile", json={
            "expected_state_version": again}, headers=LOCAL).json()
        if attempt == 2:
            assert result["reconciliation"]["outcomes"][0]["outcome"] == "NOT_SENT_PROVEN"
        else:
            assert result["reconciliation"]["outcomes"][0]["outcome"] == "RETRIES_EXHAUSTED"
            assert result["reconciliation"]["resumable"] is False


def _blocked_outline_state(db, project, doc, task_id, stuck_id, attempt):
    """Re-block the parent on the same scene for the next retry attempt."""
    from shine.materialprep.source_spans import segment_document

    text = db.get(__import__("shine.models", fromlist=["ProjectDocument"]).ProjectDocument, doc["id"]).text
    scene_key = segment_document(text)[1].key
    stuck = db.get(AgentTaskRecord, stuck_id)
    stuck.status = "FAILED"
    stuck.error_code = "LLM_FORMAT"
    stuck.trace = {**stuck.trace, "external_outcome": "NOT_SENT"}
    root = db.get(AgentTaskRecord, task_id)
    root.status = "NEEDS_REVIEW"
    root.trace = {**root.trace,
                  "blocked_subtasks": [{"scene_key": scene_key, "status": "FAILED", "task_id": stuck_id,
                                        "error_code": "LLM_FORMAT", "external_outcome": "NOT_SENT"}]}
    root.state_version += 1
    return root.state_version

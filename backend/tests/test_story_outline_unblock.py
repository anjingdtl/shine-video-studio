"""A blocked outline must not be a dead end when the evidence says the provider
answered without producing a usable answer.

The unknown-outcome gate stays exactly as strict as before: a request that may
have been answered upstream is still never replayed, never replaced and never
released.  What changes is that a *decided* failure is no longer filed as
"unknown", because a decided failure owns no answer.
"""
from __future__ import annotations

from sqlalchemy import select

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentTaskRecord
from tests.test_material_scene_safety import setup_scene
from tests.test_story_preparation import LOCAL, _queue, responder


def _blocked_outline(api, monkeypatch, *, status, phase, external_outcome, error_code):
    """Reproduce the state a stuck outline leaves behind.

    A scene subcall carries its own identity and its own status; the parent
    records it as the blocking subtask.  The evidence in the trace is what the
    reconciliation may rely on - nothing else.
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
            project_id=project, role_id="scriptwriter", input_hash="answered-" + source[1].key,
            request_identity="answered-" + source[1].key, asset_manifest_hash="x",
            rulepack_id="shine-material-prep/1", rulepack_hash="x", model_profile_id="x", model_id="x",
            schema_version="1.0", status=status,
            trace={"tool": "script_prepare", "scene_key": source[1].key, "document_id": doc["id"],
                   "document_sha256": doc["sha256"], "phase": phase,
                   "external_outcome": external_outcome, "error_code": error_code,
                   "response_status": 200, "automatic_replay": False},
        )
        db.add(stuck)
        db.flush()
        root.trace = {**root.trace, "cursor": 1,
                      "blocked_subtasks": [{"scene_key": source[1].key, "status": status,
                                            "task_id": stuck.id, "error_code": error_code,
                                            "external_outcome": external_outcome,
                                            "automatic_replay": False}]}
        root.status, root.lease_owner, root.lease_expires_at = "NEEDS_REVIEW", None, None
        root.state_version += 1
        stuck_id = stuck.id
    return client, factory, project, doc, task_id, stuck_id


def test_an_answered_but_unusable_scene_is_released_and_the_outline_finishes(api, monkeypatch):
    """The provider answered and the answer was unusable: the request is
    finished, owns no answer, and may be asked again once."""
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, status="REJECTED", phase="FAILED",
        external_outcome="RESPONSE_RECEIVED", error_code="SCHEMA_VALIDATION_FAILED")
    calls = []

    def counted(messages, schema):
        calls.append(schema.get("title"))
        return responder(messages, schema)

    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=counted)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile",
                          json={"expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["reconciliation"]["outcomes"][0]["outcome"] == "RESPONSE_RECEIVED_NO_ANSWER"
    assert body["reconciliation"]["resumable"] is True
    assert body["resubmitted"] is False
    assert calls == [], "the reconciliation must never contact the provider"
    with factory() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        assert stuck.status == "SUPERSEDED"
        assert stuck.request_identity is None, "the finished request releases its identity"
        assert stuck.trace["previous_request_identity"] == "answered-S2"
        assert db.get(AgentTaskRecord, task_id).trace["blocked_subtasks"][0]["status"] == "REJECTED"
    # Reconciling twice keeps the verdict instead of blocking the task again.
    again = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile",
                        json={"expected_state_version": body["state_version"]}, headers=LOCAL)
    assert again.status_code == 200, again.text
    assert again.json()["reconciliation"]["outcomes"][0]["outcome"] == "RESPONSE_RECEIVED_NO_ANSWER"
    assert again.json()["reconciliation"]["resumable"] is True
    resumed = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume",
                          json={"expected_state_version": again.json()["state_version"]}, headers=LOCAL)
    assert resumed.status_code == 200, resumed.text
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "restarted-worker", gateway=gateway):
            break
    with factory() as db:
        assert db.get(AgentTaskRecord, task_id).status == "SUCCEEDED"
        succeeded = [row.id for row in db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.status == "SUCCEEDED",
            AgentTaskRecord.role_id == "scriptwriter"))]
        assert stuck_id not in succeeded, "the released scene is asked again with a new request"
    assert calls


def test_a_cancelled_outline_is_never_handed_back_and_a_new_attempt_is_queued(api, monkeypatch):
    """The extract button must return work that can move, not a dead task.

    The task was reused by identity alone, so a cancelled outline was handed
    back forever and the journey could never continue.
    """
    from tests.test_material_scene_safety import setup_scene

    client, factory, project, doc = setup_scene(
        api, monkeypatch, "场景一：房间\n阿德：出发。\n场景二：码头\n阿德：等船。")

    def queue():
        return client.post(f"/api/v1/projects/{project}/story-preparation/outlines", json={
            "document_id": doc["id"], "expected_document_hash": doc["sha256"],
            "locked_facts": []}, headers=LOCAL)

    first = queue()
    assert first.status_code == 202, first.text
    first_id = first.json()["task_id"]
    with factory() as db:
        version = db.get(AgentTaskRecord, first_id).state_version
    cancelled = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{first_id}/cancel",
                            json={"expected_state_version": version}, headers=LOCAL)
    assert cancelled.status_code == 200, cancelled.text

    again = queue()
    assert again.status_code == 202, again.text
    second_id = again.json()["task_id"]
    assert second_id != first_id, "a cancelled task must never be handed back"
    with factory() as db:
        assert db.get(AgentTaskRecord, second_id).status == "QUEUED"
        assert db.get(AgentTaskRecord, first_id).status == "CANCELLED", "the old attempt stays as history"

    # While the task is alive, asking again returns the same one.
    assert queue().json()["task_id"] == second_id


def test_a_legacy_cancelled_outline_is_not_handed_back_either(api, monkeypatch):
    """The stuck rows of other users were written by the old code.

    The old row carries the base identity in input_hash and has no
    base_identity in its trace, so both lookup branches have to find it.
    """
    from tests.test_material_scene_safety import setup_scene

    client, factory, project, doc = setup_scene(
        api, monkeypatch, "场景一：房间\n阿德：出发。\n场景二：码头\n阿德：等船。")

    def queue():
        return client.post(f"/api/v1/projects/{project}/story-preparation/outlines", json={
            "document_id": doc["id"], "expected_document_hash": doc["sha256"],
            "locked_facts": []}, headers=LOCAL)

    first = queue()
    assert first.status_code == 202, first.text
    first_id = first.json()["task_id"]
    with factory() as db:
        row = db.get(AgentTaskRecord, first_id)
        version = row.state_version
        legacy_identity = row.trace["base_identity"]
    cancelled = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{first_id}/cancel",
                            json={"expected_state_version": version}, headers=LOCAL)
    assert cancelled.status_code == 200, cancelled.text
    # Rewrite the row into the shape the old code wrote.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, first_id)
        row.input_hash = legacy_identity
        row.request_identity = legacy_identity
        row.trace = {key: value for key, value in row.trace.items() if key != "base_identity"}

    again = queue()
    assert again.status_code == 202, again.text
    second_id = again.json()["task_id"]
    assert second_id != first_id, "a legacy cancelled task must never be handed back"
    with factory() as db:
        assert db.get(AgentTaskRecord, second_id).status == "QUEUED"
        assert db.get(AgentTaskRecord, first_id).status == "CANCELLED"


def test_an_unknown_outcome_stays_blocked_and_is_never_replayed(api, monkeypatch):
    """The gate is unchanged: an unknown outcome owns no verdict and no release."""
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, status="NEEDS_REVIEW", phase="FAILED",
        external_outcome="UNKNOWN", error_code="LLM_TIMEOUT")
    calls = []

    def forbidden(messages, schema):
        calls.append(1)
        raise AssertionError("the provider must not be called again")

    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile",
                          json={"expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["reconciliation"]["outcomes"][0]["outcome"] == "UNCONFIRMABLE"
    assert body["reconciliation"]["resumable"] is False
    refused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume",
                          json={"expected_state_version": body["state_version"]}, headers=LOCAL)
    assert refused.status_code == 409
    assert calls == []
    with factory() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        assert stuck.status == "NEEDS_REVIEW"
        assert stuck.request_identity is not None, "an unknown outcome keeps its identity"


def test_the_release_budget_stops_a_scene_the_model_can_never_satisfy(api, monkeypatch):
    """Bounded releases: a scene that never works stops and waits for a person."""
    client, factory, project, doc, task_id, stuck_id = _blocked_outline(
        api, monkeypatch, status="REJECTED", phase="FAILED",
        external_outcome="RESPONSE_RECEIVED", error_code="SCHEMA_VALIDATION_FAILED")
    with factory.begin() as db:
        root = db.get(AgentTaskRecord, task_id)
        root.trace = {**root.trace,
                      "subtask_retries": {"S2": story.MAX_SUBTASK_RETRIES},
                      "blocked_subtasks": [{"scene_key": "S2", "status": "REJECTED",
                                            "task_id": stuck_id, "error_code": "SCHEMA_VALIDATION_FAILED",
                                            "external_outcome": "RESPONSE_RECEIVED"}]}
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    checked = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/reconcile",
                          json={"expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["reconciliation"]["outcomes"][0]["outcome"] == "RETRIES_EXHAUSTED"
    assert body["reconciliation"]["resumable"] is False
    refused = client.post(f"/api/v1/projects/{project}/story-preparation/tasks/{task_id}/resume",
                          json={"expected_state_version": body["state_version"]}, headers=LOCAL)
    assert refused.status_code == 409
    with factory() as db:
        stuck = db.get(AgentTaskRecord, stuck_id)
        assert stuck.request_identity is not None, "an exhausted budget keeps the identity"

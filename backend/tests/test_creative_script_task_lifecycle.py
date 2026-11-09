"""The brief→script task as a durable, leased, fenced unit of work.

Before this round the task was claimed without a lease: a worker that died
left it RUNNING under a dead owner forever, and nothing ever reconciled it.
The rules these tests pin down:

* the claim carries a lease that is renewed while the work runs;
* an expired claim means *check*, not *never sent*: the durable stage decides;
* a send intent is durable before the request leaves the machine;
* a saved answer is reused instead of bought twice;
* a late worker whose lease was taken over writes nothing at all.

The recovery always runs through the resident Worker's own dispatch entry,
never through a private helper, so the real restart path is what is tested.
"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.director import creative
from shine.llm.gateway import LLMGateway
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, utcnow
from tests.conftest import create_project
from tests.test_director_entries_and_post import (
    LOCAL,
    SCRIPT_ANSWER,
    _brief,
    _confirm_brief,
    _scriptwriter,
)

pytestmark = pytest.mark.integration


def _queued_task(client, project: str):
    """A confirmed brief with one queued brief→script task."""
    _scriptwriter(client)
    brief = _confirm_brief(client, project, _brief(client, project))
    queued = client.post(
        f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script",
        json={}, headers=LOCAL,
    )
    assert queued.status_code == 202, queued.text
    return brief, queued.json()["task_id"]


def _task(factory, task_id: str) -> AgentTaskRecord:
    with factory() as db:
        return db.get(AgentTaskRecord, task_id)


def _documents(factory, project: str) -> list[ProjectDocument]:
    with factory() as db:
        return list(db.scalars(
            select(ProjectDocument).where(ProjectDocument.project_id == project)
        ).all())


def test_a_creative_task_is_claimed_under_a_lease(api, monkeypatch):
    """The claim carries an owner and an expiry, so a lost worker is visible."""
    from shine.materialprep import agent_call

    client, factory, settings = api
    project = create_project(client, "租约")
    _brief_id, task_id = _queued_task(client, project)

    def crash(*_args, **_kwargs):
        raise SystemExit("controlled worker loss")

    monkeypatch.setattr(agent_call, "call_material_agent", crash)
    with pytest.raises(SystemExit):
        creative.run_pending_creative_task(factory, settings, "worker-a")

    task = _task(factory, task_id)
    assert task.status == "RUNNING"
    assert task.lease_owner == "worker-a"
    assert task.lease_expires_at is not None, "a claim without an expiry is an orphan"
    assert task.lease_expires_at > utcnow()


def test_a_worker_that_dies_before_sending_leaves_a_reconcilable_task(api, monkeypatch):
    """An expired claim is reconciled through the Worker, never replayed."""
    from shine import worker
    from shine.materialprep import agent_call

    client, factory, settings = api
    project = create_project(client, "发送前中断")
    _brief_id, task_id = _queued_task(client, project)

    def crash(*_args, **_kwargs):
        raise SystemExit("controlled worker loss before the provider call")

    monkeypatch.setattr(agent_call, "call_material_agent", crash)
    with pytest.raises(SystemExit):
        creative.run_pending_creative_task(factory, settings, "dead-worker")

    # The restart runs through the resident Worker's own dispatch entry.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.lease_expires_at = utcnow() - timedelta(seconds=60)

    calls = []

    def counted(self, db, role, **kwargs):
        calls.append(role)
        raise AssertionError("an unknown request must never be replayed")

    monkeypatch.setattr(LLMGateway, "complete_for_role", counted)
    for _ in range(3):
        worker.run_configured_once(factory, None, "new-worker", settings=settings)

    task = _task(factory, task_id)
    assert calls == [], "a request whose outcome is unknown must not be sent again"
    assert task.status == "NEEDS_REVIEW", task.status
    assert task.lease_owner is None, "a dead owner must not keep the task"
    assert task.lease_expires_at is None
    assert task.trace["external_outcome"] == "UNKNOWN"
    assert _documents(factory, project) == [], "no script was written"


def test_a_saved_answer_is_reused_instead_of_bought_twice(api, monkeypatch):
    """A response that is durable but not yet written is reused after a crash."""
    from shine import worker

    client, factory, settings = api
    project = create_project(client, "响应已存")
    _brief_id, task_id = _queued_task(client, project)

    calls = []

    def complete(self, db, role, **kwargs):
        calls.append(role)
        from shine.llm.base import LLMResponse

        return LLMResponse("", copy.deepcopy(SCRIPT_ANSWER), "test-double", 0)

    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)

    original_write = creative._write_script_document

    def die_before_the_document(*_args, **_kwargs):
        raise SystemExit("controlled worker loss after the answer was stored")

    monkeypatch.setattr(creative, "_write_script_document", die_before_the_document)
    with pytest.raises(SystemExit):
        creative.run_pending_creative_task(factory, settings, "worker-a")
    assert len(calls) == 1
    task = _task(factory, task_id)
    assert task.trace["stage"] == creative.STAGE_RESPONSE
    assert task.trace["draft_artifact_id"], "the answer must be durable before the document"
    assert _documents(factory, project) == []

    # The restarted Worker finishes the job from the stored answer and does
    # not spend a second model call.
    monkeypatch.setattr(creative, "_write_script_document", original_write)

    def refuse_a_second_call(self, db, role, **kwargs):
        raise AssertionError("a stored answer must never be paid for twice")

    monkeypatch.setattr(LLMGateway, "complete_for_role", refuse_a_second_call)
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.lease_expires_at = utcnow() - timedelta(seconds=60)
    for _ in range(3):
        worker.run_configured_once(factory, None, "new-worker", settings=settings)

    documents = _documents(factory, project)
    assert len(documents) == 1, "the stored answer becomes the script document"
    assert documents[0].text.startswith("第1场")
    task = _task(factory, task_id)
    assert task.status == "SUCCEEDED"
    assert task.trace["stage"] == creative.STAGE_DOCUMENT
    assert task.trace["document_id"] == documents[0].id


def test_a_written_script_is_never_written_twice(api, monkeypatch):
    """A restart after the document exists closes the task, it does not repeat it."""
    from shine.llm.base import LLMResponse

    client, factory, settings = api
    project = create_project(client, "不重复")
    _brief_id, task_id = _queued_task(client, project)

    calls = []

    def complete(self, db, role, **kwargs):
        calls.append(role)
        return LLMResponse("", copy.deepcopy(SCRIPT_ANSWER), "test-double", 0)

    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)
    assert creative.run_pending_creative_task(factory, settings, "worker-a") is True
    assert len(calls) == 1
    documents = _documents(factory, project)
    assert len(documents) == 1

    # The task somehow looks unfinished again: the restart must reconcile it.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.status = "RUNNING"
        row.lease_owner = "ghost"
        row.lease_expires_at = utcnow() - timedelta(seconds=60)

    assert creative.run_pending_creative_task(factory, settings, "worker-b") is True
    assert len(calls) == 1, "a stored answer is never paid for twice"
    assert len(_documents(factory, project)) == 1, "the document is written exactly once"
    task = _task(factory, task_id)
    assert task.status == "SUCCEEDED"
    assert task.trace["document_id"] == documents[0].id


def test_two_workers_never_both_own_one_creative_task(api, monkeypatch):
    """The conditional claim means exactly one worker owns the task."""
    from shine.materialprep import agent_call

    client, factory, settings = api
    project = create_project(client, "竞争")
    _brief_id, task_id = _queued_task(client, project)

    def crash(*_args, **_kwargs):
        raise SystemExit("controlled worker loss")

    monkeypatch.setattr(agent_call, "call_material_agent", crash)
    with pytest.raises(SystemExit):
        creative.run_pending_creative_task(factory, settings, "worker-a")

    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.lease_expires_at = utcnow() - timedelta(seconds=60)

    # A second worker may take the expired claim; the first worker's late
    # write-back must be refused by the fence.
    assert creative.run_pending_creative_task(factory, settings, "worker-b") is True
    task = _task(factory, task_id)
    assert task.lease_owner is None

    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        before = (row.status, row.state_version)

    assert creative._finish_task(factory, task_id, task.state_version, "worker-a",
                                 {**task.trace, "stage": creative.STAGE_DOCUMENT},
                                 "SUCCEEDED") is False, "a stale owner must not write back"
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert (row.status, row.state_version) == before


def test_a_repeated_request_returns_the_same_task_and_never_replays(api, monkeypatch):
    """A second click is the same request, not a second model call."""
    from shine.llm.base import LLMResponse

    client, factory, settings = api
    project = create_project(client, "重复点击")
    _scriptwriter(client)
    brief = _confirm_brief(client, project, _brief(client, project))
    path = f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script"
    first = client.post(path, json={}, headers=LOCAL).json()
    second = client.post(path, json={}, headers=LOCAL).json()
    assert first["task_id"] == second["task_id"]

    calls = []

    def complete(self, db, role, **kwargs):
        calls.append(role)
        return LLMResponse("", copy.deepcopy(SCRIPT_ANSWER), "test-double", 0)

    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)
    assert creative.run_pending_creative_task(factory, settings, "worker-a") is True
    assert creative.run_pending_creative_task(factory, settings, "worker-a") is False
    assert len(calls) == 1, "one request, one model call"
    assert len(_documents(factory, project)) == 1


def test_the_creative_task_keeps_its_own_draft_artifact(api, monkeypatch):
    """The stored answer is a versioned artifact, not a loose blob."""
    from shine.llm.base import LLMResponse

    client, factory, settings = api
    project = create_project(client, "草稿留痕")
    _brief_id, task_id = _queued_task(client, project)

    def complete(self, db, role, **kwargs):
        return LLMResponse("", copy.deepcopy(SCRIPT_ANSWER), "test-double", 0)

    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)
    assert creative.run_pending_creative_task(factory, settings, "worker-a") is True
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        draft = db.get(AgentArtifact, task.trace["draft_artifact_id"])
        assert draft is not None
        assert draft.kind == creative.DRAFT_KIND
        assert draft.source_task_id == task_id
        assert draft.payload_json["script_text"]

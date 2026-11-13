"""Reverse assertions for the four-stage closeout (F01-F11).

Every test here fails on the reviewed baseline ``7081104`` and passes only when
the invariant it names really holds.  They are the durable form of the
adversarial review's reproduction probes:

* F01 a blueprint input is the current plan's volumes, in order, never "every
  confirmed outline the project happens to hold";
* F02 the queue and the Worker answer the draft-eligibility question with the
  same function;
* F03 a paid answer is adopted, not lost, and a worker that lost its fence
  cannot publish;
* F04 an unknown send outcome is never re-sent by a plain resume, whatever
  error code it carries;
* F06 the delivery scope is the proven scene -> shots mapping, not a count;
* F08 a configured cap is a constraint, never a verified capability;
* F10 a volume is read on its current span, not on a previous adjustment.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from shine.director import pipeline
from shine.director.pipeline import _authoring_task
from shine.h3.rulebook import digest
from shine.llm.base import ProviderConfig
from shine.llm.gateway import LLMGateway
from shine.llm.output_budget import creative_completion_cap
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, ProjectDocument, utcnow
from tests.conftest import create_project
from tests.test_pipeline import (
    LOCAL,
    _make_doc,
    _prepare_handoff_fixture,
    _route_mock,
    _scene_responder,
    _sections_for,
)

BIBLE_PAYLOAD = {
    "schema_version": "1.0", "mainline": "渔村少年的成长", "characters": [], "world": "",
    "timeline": [], "foreshadowing": [],
    "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
    "ending_direction": "Homecoming",
}


def _multi_volume_master() -> str:
    """A master long enough to be planned as several volumes."""
    return "\n".join(f"第{index}章 章名{index}\n" + "浪。" * 4000 for index in range(1, 9))


def _confirm_plan_and_outlines(factory, pid, text, *, materialize_all=True):
    """A confirmed plan plus one confirmed outline per plan volume."""
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        outlines = []
        for volume in plan.payload_json["volumes"]:
            volume_doc = pipeline._volume_document(db, pid, volume["volume_id"])
            outlines.append(pipeline_artifact_outline(db, pid, volume_doc))
    return doc, plan, outlines


def pipeline_artifact_outline(db, pid, doc):
    """A confirmed outline artifact owned by exactly this document."""
    from shine.models import AgentArtifact as _Artifact

    payload = {
        "schema_version": "1.0",
        "document_id": doc.id, "document_sha256": doc.sha256,
        "original_text_sha256": doc.sha256,
        "offset_unit": "unicode_codepoint",
        "production_sections": _sections_for(doc.text),
        "characters": [], "story_line": ["甲"], "timeline": ["乙"], "questions": [],
        "source_locked_facts": [], "locked_facts": ["Deming is a fisherman's son"],
        "leaf_artifact_ids": [], "continuity_status": {"basis": "user_supplied_unverified"},
    }
    task_id = _authoring_task(db, pid, "story_outline_run", {"fixture": doc.id})
    artifact = _Artifact(project_id=pid, kind="StoryOutline", schema_version="1.0",
                         payload_json=payload, sha256=digest(payload),
                         producer_role="story_outline_run", approved_by="local-user",
                         approved_at=utcnow(), source_task_id=task_id)
    db.add(artifact)
    db.flush()
    db.merge(AppSetting(key=f"current-story-outline:{pid}:{doc.id}",
                        value={"artifact_id": artifact.id, "sha256": artifact.sha256}))
    db.flush()
    return artifact


def _bible(db, pid, payload=None, approved=True):
    payload = payload or BIBLE_PAYLOAD
    task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {"fixture": True})
    artifact = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                             payload_json=payload, sha256=digest(payload),
                             producer_role=pipeline.BIBLE_RUN_ROLE,
                             approved_by="local-user" if approved else None,
                             approved_at=utcnow() if approved else None,
                             source_task_id=task_id)
    db.add(artifact)
    db.flush()
    if approved:
        pipeline.confirm_bible(db, pid, artifact.id, artifact.sha256)
    return artifact


# --------------------------------------------------------------------------- F01


def test_f01_bible_input_is_the_current_plans_volumes_in_order(api):
    """An unrelated approved outline can never enter the blueprint input.

    The reviewed baseline scanned every confirmed StoryOutline of the project,
    so an outline from another document entered the trace and a plan missing a
    volume still produced a "complete" blueprint.
    """
    client, factory, settings = api
    pid = create_project(client, "F01")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    doc = _make_doc(factory, pid, _multi_volume_master())
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        assert len(plan.payload_json["volumes"]) >= 2, "the fixture needs a multi-volume plan"
        first = plan.payload_json["volumes"][0]
        first_doc = pipeline._volume_document(db, pid, first["volume_id"])
        first_outline = pipeline_artifact_outline(db, pid, first_doc)
        # an unrelated document with its own confirmed outline, same project
        other = ProjectDocument(project_id=pid, filename="另一部.txt", content_type="text/plain",
                               sha256="b" * 64, source_format="txt",
                               text="第一章 别\n无关内容。")
        db.add(other)
        db.flush()
        unrelated = pipeline_artifact_outline(db, pid, other)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=lambda m, s: BIBLE_PAYLOAD)
    with factory.begin() as db:
        task = pipeline.queue_bible(db, pid, gw)
        trace = dict(task.trace)
    referenced = [ref["id"] for ref in trace["outline_refs"]]
    assert first_outline.id in referenced
    assert unrelated.id not in referenced, "another document's outline must never enter the blueprint"
    coverage = trace["outline_coverage"]
    assert coverage["complete"] is False
    assert [item["order"] for item in coverage["missing_volumes"]] == [
        volume["order"] for volume in plan.payload_json["volumes"][1:]]
    assert trace["input_revision_ids"] == [first_outline.id]


def test_f01_partial_blueprint_stays_a_draft_and_cannot_be_confirmed(api):
    """A partial blueprint is a usable draft, never a confirmable whole."""
    client, factory, settings = api
    pid = create_project(client, "F01b")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    doc = _make_doc(factory, pid, _multi_volume_master())
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        first = plan.payload_json["volumes"][0]
        pipeline_artifact_outline(db, pid, pipeline._volume_document(db, pid, first["volume_id"]))
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=lambda m, s: BIBLE_PAYLOAD)
    with factory.begin() as db:
        task = pipeline.queue_bible(db, pid, gw)
    pipeline.run_pending_pipeline_task(factory, settings, "w-f01", gw)
    with factory() as db:
        task = db.get(AgentTaskRecord, task.id)
        assert task.status == "SUCCEEDED", task.trace
        draft = db.get(AgentArtifact, task.trace["artifact_id"])
        draft_sha = draft.sha256
    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.confirm_bible(db, pid, draft.id, draft_sha)
    assert "PIPELINE_BIBLE_OUTLINE_COVERAGE_INCOMPLETE" in str(err.value)


def test_f01_volume_outline_must_name_its_own_document(api):
    """A project-level pointer to another document's outline is not this volume's."""
    client, factory, settings = api
    pid = create_project(client, "F01c")
    doc = _make_doc(factory, pid, "第一章 甲\n浪。")
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        volume_id = plan.payload_json["volumes"][0]["volume_id"]
        volume_doc = pipeline._volume_document(db, pid, volume_id)
        foreign = ProjectDocument(project_id=pid, filename="别的.txt", content_type="text/plain",
                                 sha256="c" * 64, source_format="txt", text="第一章 丙\n别的。")
        db.add(foreign)
        db.flush()
        foreign_outline = pipeline_artifact_outline(db, pid, foreign)
        # only the project-level pointer exists, and it names the foreign outline
        db.merge(AppSetting(key=f"current-story-outline:{pid}",
                            value={"artifact_id": foreign_outline.id,
                                   "sha256": foreign_outline.sha256}))
        db.flush()
        assert pipeline._volume_outline(db, pid, volume_id) is None
        pipeline_artifact_outline(db, pid, volume_doc)
        assert pipeline._volume_outline(db, pid, volume_id).id != foreign_outline.id


# --------------------------------------------------------------------------- F02


def test_f02_draft_bible_drives_the_worker_like_the_queue_promised(api):
    """A task enqueued on a draft blueprint is not refused by the Worker.

    The reviewed baseline enqueued with ``draft_unconfirmed`` and then refused
    at execution time with ``PIPELINE_BIBLE_CHANGED`` after zero provider calls.
    """
    client, factory, settings = api
    pid = create_project(client, "F02")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, _outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        _bible(db, pid, approved=False)
    calls = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        assert tasks[0].trace["bible_basis"] == "draft_unconfirmed"
    pipeline.run_pending_pipeline_task(factory, settings, "w-f02", gw)
    with factory() as db:
        task = db.get(AgentTaskRecord, tasks[0].id)
        assert task.status == "SUCCEEDED", task.trace
        assert task.trace.get("bible_basis") == "draft_unconfirmed"
    assert calls, "a draft blueprint must really reach the provider"
    # Publishing still needs the confirmed blueprint.
    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.build_volume_handoff(db, pid, volume_id, "profile-x")
    assert "PIPELINE_BIBLE_NOT_CONFIRMED" in str(err.value)


def test_f02_bible_basis_equivalence_is_explicit():
    """Two blueprints are interchangeable only when the decisive facts match."""
    base = {"mainline": "归航", "locked_facts": ["A", "B"], "ending_direction": "home"}
    assert pipeline.bible_basis_equivalent(base, dict(base))
    assert pipeline.bible_basis_equivalent(base, {**base, "locked_facts": ["B", "A"]})
    assert not pipeline.bible_basis_equivalent(base, {**base, "ending_direction": "death"})
    assert not pipeline.bible_basis_equivalent(base, {**base, "locked_facts": ["A"]})


# --------------------------------------------------------------------------- F03


def test_f03_orphan_answer_is_adopted_and_never_rebought(api):
    """An answer written before the pointer was lost is adopted, not resubmitted.

    The reviewed baseline left the task in ``SENDING`` with no pointer, so the
    next Worker marked it ``NEEDS_REVIEW/UNKNOWN`` while the paid answer sat in
    the ledger unreachable.
    """
    client, factory, settings = api
    pid = create_project(client, "F03")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        bible = _bible(db, pid)
        bible_id = bible.id
    outline_id = outlines[0].id
    calls = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
        section = tasks[0].trace["section"]
    # The historical crash window: the answer artifact committed, the task's
    # own pointer never did, and the lease is gone.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        answer = dict(_scene_responder()([], {"title": "SceneScript"}))
        answer["scene_key"] = section.get("scene_key", "S1")
        answer["section_id"] = section["section_id"]
        answer["source_refs"] = {"volume_id": volume_id}
        orphan = AgentArtifact(
            project_id=pid, kind=pipeline.SCENE_SCRIPT_KIND, schema_version="1.0",
            payload_json=answer, sha256=digest(answer),
            producer_role=pipeline.SCENE_SCRIPT_RUN_ROLE, source_task_id=task_id,
            input_revision_ids=[bible_id, outline_id])
        db.add(orphan)
        db.flush()
        orphan_id = orphan.id
        row.status = "RUNNING"
        row.lease_owner = None
        row.lease_expires_at = None
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w-f03", gw)
    assert ran is True
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "SUCCEEDED", row.trace
        assert row.trace["artifact_id"] == orphan_id
        assert row.trace.get("adopted") is True
        assert row.trace["external_outcome"] == "RESPONSE_RECEIVED"
        assert db.get(AgentArtifact, orphan_id) is not None
    assert calls == [], "a persisted answer must never cost a second provider call"


def test_f03_two_matching_orphans_are_ambiguous_and_report_named(api):
    """Two candidate answers are reported, never picked arbitrarily."""
    client, factory, settings = api
    pid = create_project(client, "F03b")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        bible = _bible(db, pid)
        bible_id = bible.id
    outline_id = outlines[0].id
    calls = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
        section = tasks[0].trace["section"]
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        for suffix in ("甲", "乙"):
            answer = dict(_scene_responder()([], {"title": "SceneScript"}))
            answer["scene_key"] = section.get("scene_key", "S1")
            answer["section_id"] = section["section_id"]
            answer["source_refs"] = {"volume_id": volume_id}
            answer["text"] = answer["text"] + suffix
            db.add(AgentArtifact(
                project_id=pid, kind=pipeline.SCENE_SCRIPT_KIND, schema_version="1.0",
                payload_json=answer, sha256=digest(answer),
                producer_role=pipeline.SCENE_SCRIPT_RUN_ROLE, source_task_id=task_id,
                input_revision_ids=[bible_id, outline_id]))
        db.flush()
        row.status = "RUNNING"
        row.lease_owner = None
        row.lease_expires_at = None
    pipeline.run_pending_pipeline_task(factory, settings, "w-f03b", gw)
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "NEEDS_REVIEW"
        assert row.trace["blocked_reason"] == "PIPELINE_AMBIGUOUS_RESULT_CANDIDATES"
        assert len(row.trace["orphan_candidate_ids"]) == 2
    assert calls == []


def test_f03_a_worker_without_the_fence_cannot_publish(api):
    """An expired owner may not write an artifact for a task someone else holds."""
    client, factory, settings = api
    pid = create_project(client, "F03c")
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, _outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        _bible(db, pid)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder())
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.status = "RUNNING"
        row.lease_owner = "w-other"
        row.lease_expires_at = utcnow()
        row.state_version = 7
    committed = pipeline._commit_pipeline_result(
        factory, task_id=task_id, version=3, worker_id="w-late", project_id=pid,
        kind=pipeline.SCENE_SCRIPT_KIND, role=pipeline.SCENE_SCRIPT_RUN_ROLE,
        payload={"text": "late", "section_id": "s"}, input_revision_ids=[],
        trace={"stage": "DOCUMENT"})
    assert committed is None, "a stale worker must not be able to publish a result"
    with factory() as db:
        assert db.scalars(select(AgentArtifact).where(
            AgentArtifact.source_task_id == task_id)).all() == []
        row = db.get(AgentTaskRecord, task_id)
        assert row.state_version == 7


# --------------------------------------------------------------------------- F04


def test_f04_provider_timeout_is_never_resent_by_a_plain_resume(api):
    """A timeout keeps its own error code but its outcome is still unknown."""
    client, factory, settings = api
    pid = create_project(client, "F04")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, _outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        _bible(db, pid)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder())
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.status = "NEEDS_REVIEW"
        row.error_code = "LLM_HTTP"
        row.trace = {**row.trace, "stage": "NEEDS_REVIEW", "blocked_reason": "LLM_HTTP",
                     "external_outcome": "UNKNOWN"}
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
        response = client.post(f"/api/v1/projects/{pid}/pipeline/tasks/{task_id}/resume",
                               json={"expected_state_version": version}, headers=LOCAL)
        assert response.status_code == 422, response.text
        assert "PIPELINE_TASK_OUTCOME_UNKNOWN_RECONCILE_REQUIRED" in response.text
        entry = next(item for item in pipeline.pipeline_task_summary(db, pid)
                     if item["task_id"] == task_id)
        assert set(entry["available_actions"]) == {"reconcile", "new_attempt"}
        assert "resume" not in entry["available_actions"]


def test_f04_an_explicit_new_attempt_is_a_separate_physical_send(api):
    """The authorised retry is a different task; the UNKNOWN one stays history."""
    client, factory, settings = api
    pid = create_project(client, "F04b")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, _outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    with factory.begin() as db:
        _bible(db, pid)
    calls = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.status = "NEEDS_REVIEW"
        row.error_code = "LLM_HTTP"
        row.trace = {**row.trace, "stage": "NEEDS_REVIEW", "blocked_reason": "LLM_HTTP",
                     "external_outcome": "UNKNOWN"}
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
        created = client.post(f"/api/v1/projects/{pid}/pipeline/tasks/{task_id}/new-attempt",
                              json={"expected_state_version": version}, headers=LOCAL)
        assert created.status_code == 202, created.text
        body = created.json()
        new_id = body["task_id"]
        assert new_id != task_id
        assert body["attempt"] == 2
    with factory() as db:
        old = db.get(AgentTaskRecord, task_id)
        new = db.get(AgentTaskRecord, new_id)
        assert old.status == "NEEDS_REVIEW", "the unknown attempt keeps its own outcome"
        assert old.trace["external_outcome"] == "UNKNOWN"
        assert old.trace["superseded_by_task_id"] == new_id
        assert new.trace["retry_of_task_id"] == task_id
        assert new.trace["prior_external_outcome"] == "UNKNOWN"
        assert new.request_identity != old.request_identity
        assert new.trace["base_identity"] == old.trace["base_identity"]
    # The queue must not spawn a third task for the same logical unit.
    with factory() as db:
        again = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        assert [task.id for task in again] == [new_id]
        db.commit()
    pipeline.run_pending_pipeline_task(factory, settings, "w-f04", gw)
    with factory() as db:
        new = db.get(AgentTaskRecord, new_id)
        assert new.status == "SUCCEEDED", new.trace
    assert len(calls) == 1, "exactly one authorised send"


# --------------------------------------------------------------------------- F06


def test_f06_a_scene_without_shots_blocks_the_handoff_by_name(api):
    """Coverage is the scene -> shots mapping, never a shot count."""
    from tests.test_pipeline import _prepare_handoff_fixture
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    # apply only the first scene; the second stays unproven
    applied = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{units[0]['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": units[0]["continuity_artifact_id"]}, headers=LOCAL)
    assert applied.status_code == 200, applied.text
    from tests.conftest import register_workflow_profile
    profile = register_workflow_profile(client)
    response = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                           json={"workflow_profile_id": profile["id"],
                                 "acknowledge_scene_coverage": True}, headers=LOCAL)
    assert response.status_code == 422, response.text
    assert "PIPELINE_STORYBOARD_NOT_APPLIED" in response.text, response.text


# --------------------------------------------------------------------------- F07


def test_f07_an_uncovered_beat_blocks_the_camera_handoff_by_name(api):
    """The last gate before rendering refuses a plan that drops a scripted beat.

    Coverage is read from the two stored artifacts, so the test edits the plan
    the way a broken crew run would leave it: one shot missing, its beat now
    carried by nothing.  The refusal must name the fact and keep the project
    usable - nothing is approved, no shot is created.
    """
    from tests.test_pipeline import _prepare_handoff_fixture

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    camera_id = units[0]["camera_artifact_id"]
    with factory.begin() as db:
        artifact = db.get(AgentArtifact, camera_id)
        payload = dict(artifact.payload_json)
        shots = list(payload["shots"])
        assert len(shots) >= 2, shots
        direction = next(a for a in db.scalars(select(AgentArtifact).where(
            AgentArtifact.id.in_(artifact.input_revision_ids))).all()
            if a.kind == "DirectorialPlan")
        breakdown = next(a for a in db.scalars(select(AgentArtifact).where(
            AgentArtifact.id.in_(direction.input_revision_ids))).all()
            if a.kind == "ScriptBreakdown")
        intents = {str(shot["id"]): shot for shot in direction.payload_json["shots"]}

        def beats_of(camera_shot) -> list[str]:
            # A camera shot carries a beat only through the intent it films.
            intent = intents.get(str(camera_shot.get("intent_id"))) or {}
            found = [str(intent["beat_id"])] if intent.get("beat_id") else []
            found.extend(str(cut["beat_id"]) for cut in intent.get("cuts") or []
                         if cut.get("beat_id"))
            return found

        # Find a beat whose every landed shot can be dropped without emptying
        # the plan: that beat then has no shot at all.
        orphaned = None
        for beat in breakdown.payload_json["beats"]:
            beat_id = str(beat["id"])
            kept = [shot for shot in shots if beat_id not in beats_of(shot)]
            if kept and len(kept) < len(shots):
                orphaned, shots = beat_id, kept
                break
        assert orphaned, "the fixture must have a beat that one shot can carry alone"
        payload["shots"] = shots
        artifact.payload_json = payload
        artifact.sha256 = digest(payload)

    response = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{camera_id}/apply-camera",
        json={"continuity_artifact_id": units[0]["continuity_artifact_id"]}, headers=LOCAL)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STORYBOARD_BEAT_UNCOVERED", detail
    assert detail["next_action"] == "regenerate_storyboard"
    assert detail["details"]["beats_covered"] < detail["details"]["beats_total"]
    assert detail["details"]["beat_ids"], "the refusal names the beats that have no shot"

    with factory() as db:
        artifact = db.get(AgentArtifact, camera_id)
        assert artifact.approved_at is None, "a refused plan is never approved"
        assert db.get(AppSetting, f"film-apply:{camera_id}") is None
    # The project is not stuck: the untouched second scene still lands.
    applied = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{units[1]['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": units[1]["continuity_artifact_id"]}, headers=LOCAL)
    assert applied.status_code == 200, applied.text


# --------------------------------------------------------------------------- F07


# --------------------------------------------------------------------------- F08


def _config(**overrides) -> ProviderConfig:
    base = dict(profile_id="p", name="n", kind="openai_compatible",
                base_url="https://api.example.com/v1", model="test-model", api_key="k",
                timeout_s=30, max_retries=1, temperature=1.0, generation_options={},
                context_window_tokens=None, max_output_tokens=None,
                context_window_source="provider_did_not_declare_limits")
    base.update(overrides)
    return ProviderConfig(**base)


def test_f08_a_configured_cap_never_certifies_capability():
    """128,000 configured alone stays unverified; it constrains, never certifies."""
    cap = creative_completion_cap(_config(generation_options={"max_completion_tokens": 128000}))
    assert cap["output_max_verified"] is False
    assert cap["capability_basis"] == "unverified_conservative_ceiling"
    assert cap["requires_capability_confirmation"] is True
    assert {row["kind"] for row in cap["sources"]} == {"constraint", "fallback"}
    assert cap["completion_cap"] <= 100_000


def test_f08_declared_metadata_is_the_only_capability_source():
    cap = creative_completion_cap(_config(max_output_tokens=131072))
    assert cap["output_max_verified"] is True
    assert cap["capability_basis"] == "capability_min_chain"
    assert cap["completion_cap"] == 78643
    assert [row["kind"] for row in cap["sources"]][0] == "capability"


def test_f08_a_probe_counts_and_records_adapter_semantics():
    cap = creative_completion_cap(_config(
        generation_options={"probed_max_output_tokens": 20000}))
    assert cap["output_max_verified"] is True
    assert cap["completion_cap"] == 12000
    anthropic = creative_completion_cap(_config(kind="anthropic"))
    assert anthropic["capability_class"] == "anthropic"
    assert anthropic["completion_budget_semantics"].startswith("max_tokens caps the whole")


# --------------------------------------------------------------------------- F10


def test_f10_a_volume_reads_its_current_span_after_a_boundary_change(api):
    """Two adjustments of one master: only the current range is readable."""
    client, factory, settings = api
    pid = create_project(client, "F10")
    text = "第一章 甲\n" + "浪。" * 900 + "\n第二章 乙\n" + "浪。" * 900
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        first = plan.payload_json["volumes"][0]
        old_doc_id = pipeline._volume_document(db, pid, first["volume_id"]).id
        old_span = (first["start_offset"], first["end_offset"])
    sections = pipeline.source_sections_safe(pid, db_doc(factory, doc.id))
    split = sections[0]["end_offset"]
    with factory.begin() as db:
        adjusted = pipeline.adjust_volume_boundaries(db, pid, plan.id, [
            {"title": "上卷", "start_offset": 0, "end_offset": split},
            {"title": "下卷", "start_offset": split, "end_offset": len(text)},
        ])
        pipeline.confirm_volume_plan(db, pid, adjusted.id, adjusted.sha256)
        pipeline.materialize_volume_documents(db, pid, adjusted.id)
        new_first = adjusted.payload_json["volumes"][0]
        current = pipeline._volume_document(db, pid, new_first["volume_id"])
        assert current is not None
        assert (current.parsed["start_offset"], current.parsed["end_offset"]) == (
            0, split), "the volume must be read on the adjusted span"
        assert current.id != old_doc_id
        # the confirmed plan now names the new revision, so the old document is
        # never returned for this key again
        assert current.parsed["volume_revision"] == new_first["volume_revision"]
    assert old_span != (0, split)


def db_doc(factory, document_id):
    with factory() as db:
        return db.get(ProjectDocument, document_id)


# --------------------------------------------------------------------------- F05


def rejected_tasks(factory, pid):
    """The crew tasks a refused draft left behind, provably unsent."""
    with factory() as db:
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid)).all()
        return [row for row in rows if row.status in {"REJECTED", "FAILED"}
                and (row.trace or {}).get("external_outcome") == "NOT_SENT"]


def test_f05_the_crew_lane_really_consults_the_persisted_consent(api, monkeypatch):
    """Every crew send is authorised per role, and a refusal sends nothing.

    The reviewed baseline set ``allow_remote_text=True`` inside the volume
    storyboard lane and then dropped the consent fields before the Dispatcher,
    so the per-role document consent guard never ran.  The transport double
    counts what really left the machine.
    """
    import copy
    from types import SimpleNamespace

    from shine.llm.base import LLMResponse
    from shine.llm.gateway import LLMGateway as Gateway

    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    client, factory, settings, pid, plan, volume_id, _units = _prepare_handoff_fixture(api)
    chain = client.app.state.mock_llm_responder
    sent = []

    def complete(self, db, role, **kwargs):
        sent.append(role)
        # The crew lane now sends the Call Governor's compiled request verbatim;
        # read the same bytes from it so the double still sees what left.
        pre = kwargs.get("precompiled")
        system = pre.system if pre is not None else kwargs["system"]
        user = pre.user if pre is not None else kwargs["user"]
        schema = pre.schema if pre is not None else kwargs["schema"]
        value = chain([SimpleNamespace(content=system),
                       SimpleNamespace(content=user)], schema)
        return LLMResponse("", copy.deepcopy(value), "transport-double", 0)

    monkeypatch.setattr(Gateway, "complete_for_role", complete)
    # Every crew role routed to a provider that is not this machine.
    for role in ("scriptwriter", "director", "cinematographer",
                 "continuity_supervisor", "editor"):
        profile = client.post("/api/v1/provider-profiles", json={
            "name": f"remote-{role}", "kind": "openai_compatible",
            "base_url": "https://text.example.invalid/v1", "model": "test-model"})
        assert profile.status_code == 201, profile.text
        routed = client.put("/api/v1/agent-routes", json={
            "role": role, "provider_profile_id": profile.json()["id"]})
        assert routed.status_code == 200, routed.text
    with factory() as db:
        master_id = pipeline.current_volume_plan(db, pid).payload_json["source_master"]["document_id"]
    # 1. no consent at all: refused before any transport call
    refused = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft",
                          json={})
    assert refused.status_code == 201, refused.text
    assert refused.json()["status"] == "INCOMPLETE"
    assert sent == [], "a missing consent must send nothing"
    rejections = refused.json()["rejections"] or []
    assert any("REMOTE_AGENT_CONSENT_REQUIRED" in str(row.get("code")) for row in rejections), rejections
    assert all(row.get("external_outcome") == "NOT_SENT" for row in rejections), rejections
    # 2. the person grants the document scope: the same lane now really sends
    scope = client.get(f"/api/v1/projects/{pid}/material-preparation/text-consent/{master_id}").json()
    granted = client.post(f"/api/v1/projects/{pid}/material-preparation/text-consent",
                          json={"document_id": master_id,
                                "expected_document_hash": scope["document_sha256"],
                                "expected_scope_hash": scope["scope_hash"],
                                "granted": True}, headers=LOCAL)
    assert granted.status_code == 200, granted.text
    # 2a. the refusal was provably unsent, so the person records that and only
    # then may the same request be attempted again - the ledger, not the UI,
    # decides that replay is safe.
    for row in rejected_tasks(factory, pid):
        version = row.state_version
        reconciled = client.post(f"/api/v1/projects/{pid}/film-runs/{row.id}/reconcile",
                                 json={"expected_state_version": version}, headers=LOCAL)
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["outcome"] == "NOT_SENT_PROVEN", reconciled.json()
        assert reconciled.json()["resubmittable"] is True
    allowed_run = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft",
                              json={})
    assert allowed_run.status_code == 201, allowed_run.text
    assert allowed_run.json()["status"] == "PROPOSED", allowed_run.json().get("rejections")
    assert sent, "a granted scope must really reach the transport"
    granted_calls = len(sent)
    # 3. revocation stops future sends without recalling what was sent
    revoked = client.post(f"/api/v1/projects/{pid}/material-preparation/text-consent",
                          json={"document_id": master_id,
                                "expected_document_hash": scope["document_sha256"],
                                "expected_scope_hash": granted.json()["scope_hash"],
                                "granted": False}, headers=LOCAL)
    assert revoked.status_code == 200, revoked.text
    for row in rejected_tasks(factory, pid):
        if row.trace.get("reconciliation"):
            continue
        client.post(f"/api/v1/projects/{pid}/film-runs/{row.id}/reconcile",
                    json={"expected_state_version": row.state_version}, headers=LOCAL)
    after = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft",
                        json={})
    assert after.status_code == 201, after.text
    assert after.json()["status"] == "INCOMPLETE"
    assert len(sent) == granted_calls, "a revoked consent must stop further sends"


def test_a_blueprint_can_be_confirmed_for_one_volume_without_reading_the_rest(api):
    """F12: "先做一卷" must not require outlining the whole book first.

    The whole-plan confirmation is right to demand every volume's outline.  But
    it used to be the *only* way to confirm anything, so a person who wanted to
    finish volume 1 had to buy 83 more extraction calls.  A volume-scoped
    confirmation names exactly one volume, keeps the whole-plan refusal intact,
    and is refused at delivery time for every other volume.
    """
    _client, factory, _settings = api
    pid = create_project(_client, "单卷蓝图")

    doc = _make_doc(factory, pid, _multi_volume_master())
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        volumes = plan.payload_json["volumes"]
        assert len(volumes) >= 2, volumes
        first, second = volumes[0], volumes[1]
        # Only the first volume is read.
        volume_doc = pipeline._volume_document(db, pid, first["volume_id"])
        pipeline_artifact_outline(db, pid, volume_doc)
        payload = dict(BIBLE_PAYLOAD)
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {"fixture": "single-volume"})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=payload, sha256=digest(payload),
                              producer_role=pipeline.BIBLE_RUN_ROLE, source_task_id=task_id)
        db.add(bible)
        db.flush()
        bible_id, bible_sha = bible.id, bible.sha256
        first_id, second_id = first["volume_id"], second["volume_id"]

    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.confirm_bible(db, pid, bible_id, bible_sha)
    assert "PIPELINE_BIBLE_OUTLINE_COVERAGE_INCOMPLETE" in str(err.value)

    with factory.begin() as db:
        pipeline.confirm_bible(db, pid, bible_id, bible_sha, scope_volume_id=first_id)
    with factory() as db:
        pointer = pipeline._read_pointer(db, pipeline._pointer_key(pid, "story-bible"))
        assert pointer["scope"] == {"kind": "volume", "volume_id": first_id}
        assert pipeline.current_bible(db, pid).id == bible_id

    # The scoped blueprint produces its own volume and nothing else.
    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.build_volume_handoff(db, pid, second_id, "wf", settings=None)
    assert "PIPELINE_BIBLE_SCOPE_OTHER_VOLUME" in str(err.value)

"""One broken optional field must not destroy a scene, a volume or a batch.

This is the P0 acceptance loop, offline and with no provider: a scene whose
answer is complete but whose optional field has the wrong type is saved as a
candidate draft and left for review, while the next scene runs normally.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from shine.director import pipeline
from shine.director.pipeline import _authoring_task
from shine.h3.rulebook import digest
from shine.literary.drafts import CANDIDATE_KIND
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, ProjectDocument, utcnow
from tests.conftest import create_project

pytestmark = pytest.mark.integration

LONG_SCENE = "德明站在船头，风把帆吹得鼓起来。\n德明：起风了，收帆！\n阿娘：早去早回。\n" * 4


def _document(factory, project_id: str, text: str) -> ProjectDocument:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with factory.begin() as db:
        doc = ProjectDocument(project_id=project_id, filename="母本.txt", content_type="text/plain",
                              sha256=sha, source_format="txt", text=text)
        db.add(doc)
        db.flush()
        return doc


def _confirmed_outline(db, project_id: str, doc, sections: list[dict]) -> AgentArtifact:
    payload = {
        "schema_version": "1.0", "document_id": doc.id, "document_sha256": doc.sha256,
        "original_text_sha256": doc.sha256, "offset_unit": "unicode_codepoint",
        "production_sections": sections,
        "characters": [{"name": "德明", "stable_identity": "u1", "role": "主角"}],
        "story_line": ["出海"], "timeline": ["第一天"], "questions": [],
        "source_locked_facts": [], "locked_facts": ["德明是渔民"],
        "leaf_artifact_ids": [], "continuity_status": {"basis": "user_supplied_unverified"},
    }
    task_id = _authoring_task(db, project_id, "story_outline_run", {"fixture": True})
    artifact = AgentArtifact(project_id=project_id, kind="StoryOutline", schema_version="1.0",
                             payload_json=payload, sha256=digest(payload),
                             producer_role="story_outline_run", approved_by="local-user",
                             approved_at=utcnow(), source_task_id=task_id)
    db.add(artifact)
    db.flush()
    for key in (f"current-story-outline:{project_id}",
                f"current-story-outline:{project_id}:{doc.id}"):
        db.merge(AppSetting(key=key, value={"artifact_id": artifact.id, "sha256": artifact.sha256}))
    db.flush()
    return artifact


def _confirmed_bible(db, project_id: str) -> AgentArtifact:
    payload = {
        "schema_version": "1.0", "mainline": "渔村少年的成长",
        "characters": [{"name": "德明", "identity": "渔民之子", "arc_summary": "成长"}],
        "world": "渔村", "timeline": ["出海"], "foreshadowing": [],
        "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
        "ending_direction": "Homecoming",
    }
    task_id = _authoring_task(db, project_id, pipeline.BIBLE_RUN_ROLE, {"fixture": True})
    artifact = AgentArtifact(project_id=project_id, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                             payload_json=payload, sha256=digest(payload),
                             producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                             approved_at=utcnow(), source_task_id=task_id)
    db.add(artifact)
    db.flush()
    pipeline.confirm_bible(db, project_id, artifact.id, artifact.sha256)
    return artifact


def _route_mock(client, role: str) -> None:
    profile = client.post("/api/v1/provider-profiles",
                          json={"name": f"m-{role}", "kind": "mock", "model": "mm"})
    assert profile.status_code == 201, profile.text
    route = client.put("/api/v1/agent-routes",
                       json={"role": role, "provider_profile_id": profile.json()["id"]})
    assert route.status_code == 200, route.text


def _prepared(api):
    client, factory, settings = api
    pid = create_project(client, "低阻断")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。"
    doc = _document(factory, pid, text)
    with factory.begin() as db:
        doc_row = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_row)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    volume = plan.payload_json["volumes"][0]
    with factory() as db:
        volume_doc = pipeline._volume_document(db, pid, volume["volume_id"])
        from shine.materialprep.story import source_sections
        sections = source_sections(pid, volume_doc)
        assert len(sections) >= 2, sections
    with factory.begin() as db:
        volume_doc = pipeline._volume_document(db, pid, volume["volume_id"])
        _confirmed_outline(db, pid, volume_doc, sections)
        _confirmed_bible(db, pid)
    return pid, plan, volume, sections, settings


def test_one_correction_repairs_a_wrong_optional_field(api):
    """A single targeted correction is the repair budget - and it is enough."""
    pid, plan, volume, sections, settings = _prepared(api)
    _client, factory, _ = api
    broken = {"text": LONG_SCENE, "continuity_out": 123, "title": "出海"}
    good = {"text": "德明回来了。\n德明：我回来了。", "continuity_out": "德明回家"}
    seen: list[dict] = []

    def responder(messages, schema):
        seen.append(messages)
        # Scene 1: broken, then repaired.  Scene 2: correct on the first try.
        return broken if len(seen) == 1 else good

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    for _ in range(4):
        pipeline.run_pending_pipeline_task(factory, settings, "w1", gateway)

    with factory() as db:
        tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        assert all(task.status == "SUCCEEDED" for task in tasks), [t.trace for t in tasks]
        first = min(tasks, key=lambda task: task.created_at)
        assert len(first.trace.get("attempts") or []) >= 1


def test_a_body_is_kept_as_a_candidate_when_the_correction_also_fails(api):
    """The chapter survives even when its envelope never becomes valid."""
    pid, plan, volume, sections, settings = _prepared(api)
    _client, factory, _ = api
    broken = {"text": LONG_SCENE, "continuity_out": 123, "title": "出海"}
    calls: list[int] = []

    def responder(messages, schema):
        calls.append(1)
        # The first scene's answer stays structurally wrong, including after the
        # one correction; every later scene is fine.
        return broken if len(calls) <= 2 else {"text": "德明回来了。", "continuity_out": "到家"}

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    for _ in range(6):
        pipeline.run_pending_pipeline_task(factory, settings, "w3", gateway)

    assert len(calls) <= len(sections) * 2, "one correction per scene, never a loop"
    with factory() as db:
        tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE,
        ).order_by(AgentTaskRecord.created_at)).all()
        states = [task.status for task in tasks]
        assert states.count("SUCCEEDED") >= 1, states
        reviewed = [task for task in tasks if task.status == "NEEDS_REVIEW"]
        assert len(reviewed) == 1, states
        assert reviewed[0].trace.get("review")
        candidate_id = reviewed[0].trace.get("candidate_artifact_id")
        assert candidate_id, "the complete body must be saved as a candidate"
        candidate = db.get(AgentArtifact, candidate_id)
        assert candidate.kind == CANDIDATE_KIND
        assert candidate.approved_at is None
        assert candidate.payload_json["text"] == LONG_SCENE
        assert candidate.payload_json["accepted"] is False
        assert candidate.payload_json["content_saved"] is True


def test_a_scene_that_says_content_instead_of_text_is_read_locally(api):
    """No second request is bought just because the body had another key."""
    _client, factory, _settings = api
    pid, plan, volume, sections, settings = _prepared(api)
    calls: list[str] = []

    def responder(messages, schema):
        calls.append("call")
        return {"content": "德明：我回来了。", "continuity_out": "到家"}

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    for _ in range(4):
        pipeline.run_pending_pipeline_task(factory, settings, "w2", gateway)

    assert len(calls) == len(sections), "each scene is one request, aliasing is local"
    with factory() as db:
        tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        assert all(task.status == "SUCCEEDED" for task in tasks), [t.trace for t in tasks]
        artifact = db.get(AgentArtifact, tasks[0].trace["artifact_id"])
        assert artifact.payload_json["text"] == "德明：我回来了。"
        assert artifact.payload_json["section_id"] == sections[0]["production_section_id"]


def test_a_long_scene_is_written_beat_by_beat_and_a_failed_beat_keeps_the_rest(api, monkeypatch):
    """The failure of beat 2 must not delete beat 1, nor stop the next scene."""
    from shine.literary import beats as beat_lib
    from shine.literary.models import LiteraryUnit

    pid, plan_artifact, volume, sections, settings = _prepared(api)
    _client, factory, _ = api
    first_section = sections[0]["production_section_id"]
    real = beat_lib.plan_beats

    def fake_plan(db, gateway, section, source_text, schema):
        if section["section_id"] == first_section:
            return real(source_text, section_id=section["section_id"],
                        target_chars=max(1, len(source_text) // 3), min_chars=1)
        return real(source_text, section_id=section["section_id"], target_chars=len(source_text) + 1)

    monkeypatch.setattr(pipeline, "_scene_beat_plan", fake_plan)
    calls: list[int] = []

    def responder(messages, schema):
        calls.append(1)
        if len(calls) == 1:
            return {"text": "德明站在船头。", "continuity_out": "准备出海"}
        if len(calls) <= 3:
            # Beat 2 keeps failing, including its one correction.
            return {"text": "   ", "continuity_out": "无"}
        return {"text": "德明回来了。", "continuity_out": "到家"}

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        tasks = pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()
        assert len(tasks) == len(sections)

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    for _ in range(6):
        pipeline.run_pending_pipeline_task(factory, settings, "w-beats", gateway)

    with factory() as db:
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE,
        ).order_by(AgentTaskRecord.created_at)).all()
        states = [row.status for row in rows]
        assert "SUCCEEDED" in states, states
        reviewed = [row for row in rows if row.status == "NEEDS_REVIEW"]
        assert len(reviewed) == 1, states
        trace = reviewed[0].trace
        assert trace["blocked_reason"] == "SCHEMA_VALIDATION_FAILED"
        assert trace["beat_coverage"]["complete"] is True, "the split itself still covers the source"
        candidate = db.get(AgentArtifact, trace["candidate_artifact_id"])
        assert candidate.payload_json["text"] == "德明站在船头。", \
            "the beat that really arrived is kept verbatim"
        beat_rows = db.scalars(select(AgentArtifact).where(
            AgentArtifact.source_task_id == reviewed[0].id,
            AgentArtifact.kind == pipeline.SCENE_BEAT_KIND)).all()
        assert [row.payload_json["ordinal"] for row in beat_rows] == [1]
        units = sorted(db.scalars(select(LiteraryUnit).where(
            LiteraryUnit.project_id == pid,
            LiteraryUnit.parent_id == first_section)).all(), key=lambda row: row.ordinal)
        statuses = [unit.status for unit in units]
        assert statuses[0] == "PASSED", statuses
        assert statuses[1] == "FAILED", statuses
        assert all(status == "WAITING_DEPENDENCY" for status in statuses[2:]), statuses

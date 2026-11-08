"""The four-stage pipeline: volume planning, bible, scene scripts.

Every stage persists a versioned artifact, is confirmed by a person, and
records provenance.  Unknown provider outcomes stay blocked; an edited
script starts unconfirmed; the volume plan covers the master completely.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest
from sqlalchemy import select

from shine.director import pipeline
from shine.director.pipeline import _authoring_task
from shine.h3.rulebook import digest
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, ProjectDocument, utcnow
from tests.conftest import create_project

LOCAL = {"x-shine-local-action": "1"}


def _make_doc(factory, project_id, text):
    """Insert a master document row directly."""
    import hashlib

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with factory.begin() as db:
        doc = ProjectDocument(project_id=project_id, filename="母本.txt", content_type="text/plain",
                              sha256=sha, source_format="txt", text=text)
        db.add(doc)
        db.flush()
        return doc


def _outline_artifact(db, project_id, doc, sections):
    """A confirmed StoryOutline artifact for the pipeline contract tests."""
    payload = {
        "schema_version": "1.0",
        "document_id": doc.id, "document_sha256": doc.sha256,
        "original_text_sha256": doc.sha256,
        "offset_unit": "unicode_codepoint",
        "production_sections": sections,
        "characters": [{"name": "阿德", "stable_identity": "u1", "role": "主角"}],
        "story_line": ["德明出海", "归来", "团聚"],
        "timeline": ["第一天出海", "第三天归来"],
        "questions": [],
        "source_locked_facts": [], "locked_facts": ["德明是渔民"],
        "leaf_artifact_ids": [],
        "continuity_status": {"basis": "user_supplied_unverified"},
    }
    task_id = _authoring_task(db, project_id, "story_outline_run", {"fixture": True})
    a = AgentArtifact(project_id=project_id, kind="StoryOutline", schema_version="1.0",
                      payload_json=payload, sha256=digest(payload),
                      producer_role="story_outline_run", approved_by="local-user",
                      approved_at=utcnow(), source_task_id=task_id)
    db.add(a)
    db.flush()
    db.merge(AppSetting(key=f"current-story-outline:{project_id}",
                        value={"artifact_id": a.id, "sha256": a.sha256}))
    db.merge(AppSetting(key=f"current-story-outline:{project_id}:{doc.id}",
                        value={"artifact_id": a.id, "sha256": a.sha256}))
    db.flush()
    return a


def _materialize_and_outline(db, project_id, plan, volume_index=0):
    """The real flow: materialize the plan's volumes, then confirm the outline
    of the *volume document*.  The blueprint reads per-volume outlines, so a
    fixture that pins the outline to the master document would not exercise
    the contract the queue and the Worker enforce."""
    pipeline.materialize_volume_documents(db, project_id, plan.id)
    volume = plan.payload_json["volumes"][volume_index]
    volume_doc = pipeline._volume_document(db, project_id, volume["volume_id"])
    assert volume_doc is not None, "the volume document must exist after materialization"
    return _outline_artifact(db, project_id, volume_doc, _sections_for(volume_doc.text))
def _sections_for(doc_text):
    """Two sections over the doc text, mirroring source_sections' contract."""
    from shine.materialprep.story import source_sections
    real = source_sections("p-fix", type("D", (), {"text": doc_text})())
    return real


def test_short_novel_plans_one_volume(api):
    client, factory, settings = api
    pid = create_project(client, "短篇")
    doc = _make_doc(factory, pid, "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。")
    with factory() as db:
        plan = pipeline.plan_volumes(db, pid, doc)
        db.commit()
        assert len(plan.payload_json["volumes"]) == 1
        v = plan.payload_json["volumes"][0]
        assert v["start_offset"] == 0
        assert v["end_offset"] == len(doc.text)
        assert plan.payload_json["coverage"]["complete"] is True


def test_long_novel_groups_by_budget_at_section_boundaries(api):
    """Sections are grouped at boundaries; coverage is complete and ordered."""
    client, factory, settings = api
    pid = create_project(client, "长篇")
    ch = "第一章 出海\n" + "海很深。" * 4000 + "\n"
    doc = _make_doc(factory, pid, ch + ch + ch)
    with factory() as db:
        plan = pipeline.plan_volumes(db, pid, doc)
        db.commit()
        volumes = plan.payload_json["volumes"]
        assert len(volumes) >= 2
        assert volumes[0]["start_offset"] == 0
        for left, right in zip(volumes, volumes[1:], strict=False):
            assert left["end_offset"] == right["start_offset"]
        assert volumes[-1]["end_offset"] == len(doc.text)
        covered = sum(v["char_count"] for v in volumes)
        assert covered == len(doc.text)


def _route_mock(client, role: str) -> None:
    """Create a mock provider profile and route it to the role."""
    profile = client.post("/api/v1/provider-profiles", json={"name": f"m-{role}", "kind": "mock", "model": "mm"})
    assert profile.status_code == 201, profile.text
    route = client.put("/api/v1/agent-routes", json={"role": role, "provider_profile_id": profile.json()["id"]})
    assert route.status_code == 200, route.text


def test_bible_requires_confirmed_plan_and_outline(api):
    client, factory, settings = api
    pid = create_project(client, "蓝图前")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    doc = _make_doc(factory, pid, "第一章\n内容。")
    from shine.llm.secrets import SecretStore
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=lambda m, s: {})
    with factory.begin() as db:
        doc2 = db.get(ProjectDocument, doc.id)
        pipeline.plan_volumes(db, pid, doc2)
        db.commit()
    with factory() as db:
        from sqlalchemy import select as _select
        plan = db.scalars(_select(AgentArtifact).where(
            AgentArtifact.project_id == pid,
            AgentArtifact.kind == pipeline.VOLUME_PLAN_KIND)).all()[-1]
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        db.commit()
    with factory.begin() as db:
        with pytest.raises(ValueError) as err:
            pipeline.queue_bible(db, pid, gw)
    assert "PIPELINE_OUTLINE_NOT_CONFIRMED" in str(err.value)


def _bible_responder():
    """A mock answer for the bible role."""
    def responder(messages, schema):
        return {
            "schema_version": "1.0",
            "mainline": "渔村少年成长为船长的故事",
            "characters": [{"name": "德明", "identity": "渔民之子", "arc_summary": "成长为船长"}],
            "world": "东南沿海渔村",
            "timeline": ["少年出海", "风暴", "归来"],
            "foreshadowing": ["祖传罗盘"],
            "locked_facts": ["德明是渔民之子"],
            "open_questions": [],
            "ending_direction": "归航",
        }
    return responder


def test_bible_task_runs_and_confirms(api):
    client, factory, settings = api
    pid = create_project(client, "蓝图")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    text = "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
    with factory.begin() as db:
        db.commit()
    with factory() as db:
        gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_bible_responder())
        task = pipeline.queue_bible(db, pid, gw)
        db.commit()
        task_id = task.id
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w1", gw)
    assert ran is True
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "SUCCEEDED", task.trace
        assert task.trace.get("usage") is not None
        bible = pipeline.current_bible(db, pid)
        assert bible is None  # not confirmed yet
    with factory.begin() as db:
        artifact = db.get(AgentArtifact, db.get(AgentTaskRecord, task_id).trace["artifact_id"])
        pipeline.confirm_bible(db, pid, artifact.id, artifact.sha256)
    with factory() as db:
        assert pipeline.current_bible(db, pid) is not None


def _scene_responder(scene_text: str = "德明：我要出海。"):
    def responder(messages, schema):
        return {
            "scene_key": "S1", "section_id": "sec-1", "title": "出海",
            "text": scene_text, "characters": ["德明"],
            "dialogue": [{"speaker": "德明", "line": "我要出海。"}],
            "estimated_seconds": 30,
            "source_refs": {}, "continuity_in": "德明在码头",
            "continuity_out": "德明登船离开",
            "questions": [],
        }
    return responder


def test_scene_script_runs_confirms_and_edits(api):
    client, factory, settings = api
    pid = create_project(client, "剧本")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章 出海\n德明出海了。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
        bible_payload = {
            "schema_version": "1.0", "mainline": "渔村少年的成长",
            "characters": [{"name": "德明", "identity": "渔民之子", "arc_summary": "成长"}],
            "world": "渔村", "timeline": ["出海"], "foreshadowing": [],
            "locked_facts": ["Deming is a fisherman's son"], "open_questions": [], "ending_direction": "Homecoming",
        }
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {"fixture": True})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bible_payload, sha256=digest(bible_payload),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    with factory() as db:
        gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder())
        tasks = pipeline.queue_scene_scripts(db, pid, plan.payload_json["volumes"][0]["volume_id"], gw)
        db.commit()
        assert len(tasks) == 1, tasks
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w1", gw)
    assert ran is True
    from sqlalchemy import select as _sel
    with factory() as db:
        task = db.scalars(_sel(AgentTaskRecord).where(
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()[-1]
        assert task.status == "SUCCEEDED", task.trace
        artifact = db.get(AgentArtifact, task.trace["artifact_id"])
        assert artifact.payload_json["text"].startswith("德明：")
    with factory.begin() as db:
        pipeline.confirm_scene_script(db, pid, artifact.id, artifact.sha256)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        assert pipeline.current_scene_script(db, pid, volume_id, artifact.payload_json["section_id"]) is not None
    with factory.begin() as db:
        edited = pipeline.edit_scene_script(db, pid, artifact.id, "德明：风浪更大。")
        assert edited.approved_at is None
    with factory() as db:
        assert pipeline.current_scene_script(db, pid, volume_id, artifact.payload_json["section_id"]) is None


def _prepare_handoff_fixture(api, *, confirm_character_master: bool = True):
    """Scripts confirmed, and one storyboard draft with per-scene units.

    Returns what the handoff tests need: the caller applies the units and
    decides what to assert about the delivery.

    ``confirm_character_master=False`` leaves the character master a candidate:
    the material layer has not confirmed and locked it yet, so the storyboard
    has no approved material to name.  That is the real "the material is not
    ready yet" state, and the chain must not stop because of it.
    """
    from tests.conftest import register_workflow_profile
    from tests.test_film_crew import prepare as crew_prepare
    client, factory, settings = api
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    if confirm_character_master:
        pid, _style, _profile = crew_prepare(client)  # it creates its own project
    else:
        # The master is uploaded but never confirmed: it stays a candidate.
        with mock.patch("tests.test_film_crew.approve_lock", lambda *a, **k: None):
            pid, _style, _profile = crew_prepare(client)
    assets = client.get(f"/api/v1/projects/{pid}/assets").json()
    lin = next(a for a in assets if a["role"] == "character" and a["entity_id"] == "Lin")
    register_workflow_profile(client)
    text = "第一章 出海\nLin 在码头等待。德明出海了。\n第二章 归来\nLin 转身望向门口。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    with factory.begin() as db:
        volume_doc = pipeline._volume_document(db, pid, plan.payload_json["volumes"][0]["volume_id"])
        _outline_artifact(db, pid, volume_doc, _sections_for(volume_doc.text))
        bible_payload = {
            "schema_version": "1.0", "mainline": "渔村少年成长为船长",
            "characters": [{"name": "Lin", "identity": "守港人", "arc_summary": "等待与重逢"}],
            "world": "渔村", "timeline": ["出海", "归来"], "foreshadowing": [],
            "locked_facts": ["Lin is the keeper of the harbour"], "open_questions": [], "ending_direction": "Reunion",
        }
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bible_payload, sha256=digest(bible_payload),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    with factory() as db:
        gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder("Lin：你来了。"))
        tasks = pipeline.queue_scene_scripts(db, pid, plan.payload_json["volumes"][0]["volume_id"], gw)
        db.commit()
        assert len(tasks) == 2
    for _ in range(6):
        if not pipeline.run_pending_pipeline_task(factory, settings, "w1", gw):
            break
    with factory() as db:
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        assert all(r.status == "SUCCEEDED" for r in rows), [r.trace for r in rows]
        for r in rows:
            artifact = db.get(AgentArtifact, r.trace["artifact_id"])
            pipeline.confirm_scene_script(db, pid, artifact.id, artifact.sha256)
        db.commit()
    from tests.test_storyboard import _responder as chain_responder_factory
    original_responder = chain_responder_factory({"lin_asset": lin["id"]})

    def chain_responder(messages, schema):
        value = original_responder(messages, schema)
        if schema.get("title") == "ScriptBreakdown":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            value["locked_story_facts"] = context.get("locked_story_facts", [])
        if schema.get("title") == "DirectorialPlan" and not confirm_character_master:
            # Nothing is confirmed, so the storyboard has no material it may
            # name: it states the requirement instead.  Naming a candidate
            # would be refused by the contract (asset ids must be approved
            # masters), and the requirement is what the material layer works
            # from.
            value["shots"] = [{**shot, "asset_ids": []} for shot in value["shots"]]
        return value

    client.app.state.mock_llm_responder = chain_responder
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    draft = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft", json={})
    assert draft.status_code == 201, draft.text
    body = draft.json()
    assert body["status"] == "PROPOSED", json.dumps(body.get("rejections") or body, ensure_ascii=False)[:600]
    return client, factory, settings, pid, plan, volume_id, body["units"]


def test_volume_storyboard_draft_and_handoff(api):
    """The full volume path: scripts -> per-scene draft -> apply -> handoff."""
    from shine.director.models import DirectorPackageVersion
    from shine.director.package import package_readiness
    from tests.conftest import register_workflow_profile

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    with factory() as db:
        sections = (pipeline._volume_outline(db, pid, volume_id).payload_json
                    .get("production_sections") or [])
        expected_sections = [section["production_section_id"] for section in sections]
    # F06: the draft carries one bounded crew input per scene, in scene order,
    # each with its own apply handle - the mapping the handoff verifies.
    assert len(units) == 2, units
    assert [unit["section_id"] for unit in units] == expected_sections
    shot_total = 0
    for unit in units:
        assert unit["camera_artifact_id"], unit
        applied = client.post(
            f"/api/v1/projects/{pid}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
            json={"continuity_artifact_id": unit["continuity_artifact_id"]}, headers=LOCAL,
        )
        assert applied.status_code == 200, applied.text
        shot_total += len(applied.json()["shot_ids"])
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 201, handoff.text
    body = handoff.json()
    assert body["readiness"]["startable"] is True, body
    assert body["shots"] == shot_total
    assert body["episodes"] >= 1
    assert body["episode_basis"] == "volume_scope"
    # F11: the same confirmed scope hands off to the same receipt, never to a
    # second plan, package or delivery artifact.
    again = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                        json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert again.status_code == 201, again.text
    assert again.json()["package_id"] == body["package_id"]
    assert again.json()["reused"] is True
    with factory() as db:
        packages = db.scalars(select(DirectorPackageVersion).where(
            DirectorPackageVersion.project_id == pid)).all()
        assert len(packages) == 1, "a repeated handoff must not build a second package"
    # F07: editing a confirmed scene makes the delivered package unstartable
    # before any render is spent, and the history stays readable.
    with factory.begin() as db:
        script = pipeline.current_scene_script(db, pid, volume_id, units[0]["section_id"])
        pipeline.edit_scene_script(db, pid, script.id, script.payload_json["text"] + "\n补充一句。")
    with factory() as db:
        stored = db.get(DirectorPackageVersion, body["package_id"])
        assert stored.manifest["creative_lineage"]["volume_id"] == volume_id
        readiness = package_readiness(db, stored)
        codes = {row["code"] for row in readiness["blockers"]}
        assert readiness["startable"] is False, codes
        assert "CREATIVE_LINEAGE_CHANGED" in codes or "CREATIVE_LINEAGE_UNREADABLE" in codes, codes


def test_sending_crash_never_replays_the_provider(api):
    """A task whose send intent was persisted is NEVER asked again."""
    client, factory, settings = api
    pid = create_project(client, "断点")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章\n德明出海。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    calls = []

    def counting_responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=counting_responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.trace = {**row.trace, "stage": "SENDING", "external_outcome": "SENDING",
                     "sent_at": "2026-11-08T00:00:00Z"}
        row.status = "RUNNING"
        row.lease_owner = None
        row.lease_expires_at = None
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w-recovery", gw)
    assert ran is True
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "NEEDS_REVIEW", row.trace
        assert row.trace["external_outcome"] == "UNKNOWN"
        assert row.trace["blocked_reason"] == "PIPELINE_SEND_OUTCOME_UNKNOWN"
    assert calls == [], "the provider must never be contacted after a durable send intent"


def test_persisted_answer_is_adopted_without_a_second_call(api):
    """A task whose answer artifact exists finishes without re-buying the call."""
    client, factory, settings = api
    pid = create_project(client, "采纳")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章\n德明出海。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    calls = []

    def counting_responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=counting_responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    # simulate: the worker died AFTER persisting the answer artifact
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        answer = dict(_scene_responder()([], {"title": "SceneScript"}))
        answer["scene_key"] = row.trace["section"].get("scene_key", "S1")
        answer["section_id"] = row.trace["section"]["section_id"]
        answer["source_refs"] = {"volume_id": volume_id}
        artifact = AgentArtifact(
            project_id=pid, kind=pipeline.SCENE_SCRIPT_KIND, schema_version="1.0",
            payload_json=answer, sha256=digest(answer),
            producer_role=pipeline.SCENE_SCRIPT_RUN_ROLE, source_task_id=task_id)
        db.add(artifact)
        db.flush()
        row.trace = {**row.trace, "stage": "DOCUMENT", "artifact_id": artifact.id,
                     "artifact_sha256": artifact.sha256, "external_outcome": "SENDING"}
        row.status = "RUNNING"
        row.lease_owner = None
        row.lease_expires_at = None
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w-recovery", gw)
    assert ran is True
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "SUCCEEDED", row.trace
        assert row.trace.get("adopted") is True
    assert calls == [], "a persisted answer must never cost a second provider call"


def test_cross_project_artifact_reference_is_refused(api):
    """A trace that names another project's artifact can never be read."""
    client, factory, settings = api
    pid = create_project(client, "本作品")
    other = create_project(client, "别的作品")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章\n德明出海。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    # the OTHER project's outline artifact: same shape, different project
    with factory.begin() as db:
        other_doc = _make_doc(factory, other, "第一章\n别的故事。")
        other_outline = _outline_artifact(db, other, other_doc, _sections_for(other_doc.text))
        other_outline_id, other_outline_sha = other_outline.id, other_outline.sha256
    calls = []

    def counting_responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=counting_responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        task_id = tasks[0].id
    # poison the trace to point at the other project's outline
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        row.trace = {**row.trace, "outline_artifact_id": other_outline_id,
                     "outline_sha256": other_outline_sha}
    ran = pipeline.run_pending_pipeline_task(factory, settings, "w-x", gw)
    assert ran is True
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "NEEDS_REVIEW", row.trace
        assert row.trace["blocked_reason"] == "PIPELINE_OUTLINE_CHANGED"
    assert calls == [], "no provider call may consume another project's context"


def test_manual_volume_adjustment_and_master_isolation(api):
    """Boundaries adjust by new revision; a second master is refused, not mixed."""
    client, factory, settings = api
    pid = create_project(client, "分卷调整")
    doc = _make_doc(factory, pid, "第一章 甲\n内容甲。" * 200 + "\n第二章 乙\n内容乙。" * 200)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        sections = pipeline.source_sections_safe(pid, doc_r)
        assert len(sections) >= 2
        first = sections[0]
    # manual adjustment: two volumes split exactly at the section boundary
    split = first["end_offset"]
    with factory.begin() as db:
        adjusted = pipeline.adjust_volume_boundaries(db, pid, plan.id, [
            {"title": "上卷", "start_offset": 0, "end_offset": split},
            {"title": "下卷", "start_offset": split, "end_offset": len(doc_r.text or "")},
        ])
        assert adjusted.payload_json["coverage"]["complete"] is True
        assert all(v["boundary_reason"] == "manual_adjustment"
                   for v in adjusted.payload_json["volumes"])
    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.adjust_volume_boundaries(db, pid, adjusted.id, [
                {"title": "坏", "start_offset": 0, "end_offset": 10},
                {"title": "坏", "start_offset": 12, "end_offset": len(doc_r.text or "")},
            ])
    assert any(code in str(err.value) for code in
               ("INCOMPLETE_COVERAGE", "INVALID_ORDER", "SPLIT_SECTION"))
    # splitting a section in half is refused
    mid = first["start_offset"] + (first["end_offset"] - first["start_offset"]) // 2
    with pytest.raises(ValueError) as err2:
        with factory.begin() as db:
            pipeline.adjust_volume_boundaries(db, pid, adjusted.id, [
                {"title": "半章", "start_offset": 0, "end_offset": mid},
                {"title": "半章", "start_offset": mid, "end_offset": len(doc_r.text or "")},
            ])
    assert "SPLIT_SECTION" in str(err2.value)
    # a different master on a confirmed pipeline is refused
    other = _make_doc(factory, pid, "另一部作品\n第一章\n内容。")
    with pytest.raises(ValueError) as err3:
        with factory.begin() as db:
            other_r = db.get(ProjectDocument, other.id)
            pipeline.plan_volumes(db, pid, other_r)
    assert "PIPELINE_MASTER_CONFLICT_ALREADY_CONFIRMED" in str(err3.value)


def test_overlong_volume_is_batched_and_the_editor_limit_is_named(api):
    """A project-length volume is batched per scene, not refused by a limit.

    F09: the crew chain runs on bounded scene units, so a volume whose
    serialized script is far past the single-call inline limit still produces
    per-scene units.  F07: a person's edit past the scene contract is refused
    by name with its real length instead of being silently truncated.
    """
    client, factory, settings = api
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    pid = create_project(client, "超长")
    text = "第一章 甲\n浪。\n第二章 乙\n浪。\n第三章 丙\n浪。"
    doc = _make_doc(factory, pid, text)

    def long_responder(messages, schema):
        payload = _scene_responder()(messages, schema)
        payload["text"] = "浪" * 20_000
        return payload
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        _materialize_and_outline(db, pid, plan)
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        task_id = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=task_id)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=long_responder)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
    for _ in range(4):
        if not pipeline.run_pending_pipeline_task(factory, settings, "w1", gw):
            break
    from sqlalchemy import select as _sel
    with factory() as db:
        rows = db.scalars(_sel(AgentTaskRecord).where(
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        for r in rows:
            assert r.status == "SUCCEEDED", r.trace
            artifact = db.get(AgentArtifact, r.trace["artifact_id"])
            pipeline.confirm_scene_script(db, pid, artifact.id, artifact.sha256)
        db.commit()
    with factory() as db:
        script = pipeline.serialize_volume_script(db, pid, volume_id)
        assert len(script) > 60_000, "the fixture must really exceed one inline call"
        units = pipeline.storyboard_units(db, pid, volume_id)
        sections = pipeline._volume_outline(db, pid, volume_id).payload_json["production_sections"]
        assert len(units) > len(sections), "an oversized scene must become labelled chunks"
        assert all(unit["char_count"] <= pipeline.STORYBOARD_UNIT_CHARS for unit in units)
        assert all(unit["char_count"] <= 60_000 for unit in units)
        per_section = {}
        for unit in units:
            per_section.setdefault(unit["section_id"], []).append(unit["chunk_index"])
        assert [unit["section_id"] for unit in units] == [
            section["production_section_id"] for section in sections
            for _ in range(len(per_section[section["production_section_id"]]))]
        assert all(indexes == list(range(1, len(indexes) + 1)) for indexes in per_section.values())
        assert all(unit["script_sha256"] for unit in units)
        confirmed = pipeline.current_scene_script(db, pid, volume_id, sections[0]["production_section_id"])
        with pytest.raises(ValueError) as err:
            pipeline.edit_scene_script(db, pid, confirmed.id, "浪" * 24_003)
    message = str(err.value)
    assert "PIPELINE_SCENE_SCRIPT_TEXT_TOO_LONG" in message
    assert "24003" in message and "24000" in message


def test_oversized_single_chapter_volume_carries_a_warning(api):
    """One chapter past the inline budget makes the plan warn by name."""
    client, factory, settings = api
    pid = create_project(client, "单章超长")
    text = "第一章 巨章\n" + "海。" * 30_000
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        codes = [w["code"] for w in plan.payload_json.get("warnings", [])]
        assert "VOLUME_MAY_EXCEED_STORYBOARD_INLINE_LIMIT" in codes
        assert plan.payload_json["coverage"]["complete"] is True


def test_episode_script_contract_with_continuity_check(api):
    """The episode contract pins script revisions and records continuity issues."""
    client, factory, settings = api
    pid = create_project(client, "分集合同")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    text = "第一章 甲\n浪。\n第二章 乙\n浪。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        doc_r = db.get(ProjectDocument, doc.id)
        _materialize_and_outline(db, pid, plan)
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        tid = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, approved_by="local-user",
                              approved_at=utcnow(), source_task_id=tid)
        db.add(bible)
        db.flush()
        pipeline.confirm_bible(db, pid, bible.id, bible.sha256)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder("Lin：你来了。"))
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
    for _ in range(4):
        if not pipeline.run_pending_pipeline_task(factory, settings, "w1", gw):
            break
    from sqlalchemy import select as _sel
    sections = []
    with factory() as db:
        rows = db.scalars(_sel(AgentTaskRecord).where(
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        for r in rows:
            artifact = db.get(AgentArtifact, r.trace["artifact_id"])
            sections.append(artifact.payload_json["section_id"])
            pipeline.confirm_scene_script(db, pid, artifact.id, artifact.sha256)
        db.commit()
    assert len(sections) == 2
    with factory.begin() as db:
        contract = pipeline.build_episode_script(db, pid, volume_id, "第一集", sections)
        # the mock answers every scene with the same fixed continuity, so the
        # cross-scene check MUST record the mismatch instead of passing it
        assert contract.payload_json["continuity_status"] == "needs_review"
        assert contract.payload_json["continuity_issues"], "the mismatch must be named"
        pipeline.confirm_episode_script(db, pid, contract.id, contract.sha256)
    # a partial contract set blocks the handoff's episode coverage
    with factory.begin() as db:
        partial = pipeline.build_episode_script(db, pid, volume_id, "残集", sections[:1])
        pipeline.confirm_episode_script(db, pid, partial.id, partial.sha256)
    with factory() as db:
        assert len(pipeline.confirmed_episode_scripts(db, pid, volume_id)) == 2


def test_pipeline_overview_names_latest_outline_and_pending(api):
    """The overview is the UI's single read model: plan, bible, outline, scripts."""
    client, factory, settings = api
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    pid = create_project(client, "总览")
    text = "第一章 甲\n浪。\n第二章 乙\n浪。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        volume_doc = pipeline._volume_document(db, pid, plan.payload_json["volumes"][0]["volume_id"])
        outline = _outline_artifact(db, pid, volume_doc, _sections_for(volume_doc.text))
    response = client.get(f"/api/v1/projects/{pid}/pipeline")
    assert response.status_code == 200, response.text
    body = response.json()
    volume = body["volumes"][0]
    assert volume["latest_outline"]["artifact_id"] == outline.id
    assert volume["latest_outline"]["sha256"] == outline.sha256
    assert body["bible_confirmed"] is False
    assert body["pending_bible"] is None


def test_task_summary_and_soft_bible_basis(api):
    """The overview reports live task progress; a bible draft is a usable basis."""
    client, factory, settings = api
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    pid = create_project(client, "进度")
    text = "第一章 甲\n浪。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        volume_doc = pipeline._volume_document(db, pid, plan.payload_json["volumes"][0]["volume_id"])
        _outline_artifact(db, pid, volume_doc, _sections_for(volume_doc.text))
        # an UNCONFIRMED bible draft is enough to start writing scripts
        bp = {"schema_version": "1.0", "mainline": "m", "characters": [], "world": "",
              "timeline": [], "foreshadowing": [],
              "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
              "ending_direction": "Homecoming"}
        tid = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {})
        bible = AgentArtifact(project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
                              payload_json=bp, sha256=digest(bp),
                              producer_role=pipeline.BIBLE_RUN_ROLE, source_task_id=tid)
        db.add(bible)
        db.flush()
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_scene_responder())
    volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory() as db:
        tasks = pipeline.queue_scene_scripts(db, pid, volume_id, gw)
        db.commit()
        assert len(tasks) == 1
        assert tasks[0].trace.get("bible_basis") == "draft_unconfirmed"
    # the summary names the live task with its stage
    with factory() as db:
        summary = pipeline.pipeline_task_summary(db, pid)
        roles = {s["role"] for s in summary}
        assert pipeline.SCENE_SCRIPT_RUN_ROLE in roles
        entry = next(s for s in summary if s["role"] == pipeline.SCENE_SCRIPT_RUN_ROLE)
        assert entry["status"] == "QUEUED"
        assert entry["stage_label"]
    # resume fence: a wrong version is refused, the right one re-queues
    with factory.begin() as db:
        from shine.models import AgentTaskRecord as _R
        row = db.get(_R, tasks[0].id)
        row.status = "NEEDS_REVIEW"
        row.error_code = "REMOTE_AGENT_CONSENT_REQUIRED"
        row.trace = {**row.trace, "external_outcome": "NOT_SENT",
                     "blocked_reason": "REMOTE_AGENT_CONSENT_REQUIRED"}
    with pytest.raises(ValueError) as err:
        with factory.begin() as db:
            pipeline.resume_pipeline_task(db, pid, tasks[0].id, 99999)
    assert "PIPELINE_TASK_STATE_CHANGED" in str(err.value)
    with factory() as db:
        version = db.get(AgentTaskRecord, tasks[0].id).state_version
        result = pipeline.resume_pipeline_task(db, pid, tasks[0].id, version)
        assert result["status"] == "QUEUED"


def test_blocked_runtime_never_starves_the_text_queue(api, monkeypatch):
    """A frozen burst counter must not stall a running outline mid-way."""
    from shine import worker

    client, factory, settings = api
    _route_mock(client, "scriptwriter")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    pid = create_project(client, "不饿死")
    text = "第一章 甲\n浪。\n第二章 乙\n浪。"
    doc = _make_doc(factory, pid, text)
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_bible_responder())
    from shine.materialprep.story import queue_outline
    with factory() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        task = queue_outline(db, pid, doc_r, gw, facts=[])
        db.commit()
        task_id = task.id
    worker._text_preparation_ticks = worker.TEXT_PREPARATION_BURST
    import fastapi

    from shine.comfyops import runtime_configuration
    monkeypatch.setattr(runtime_configuration, "resolve_runtime",
                        lambda *a, **k: (_ for _ in ()).throw(
                            fastapi.HTTPException(409, "ACTIVE_RUNTIME_CONFIGURATION_CHANGED")))
    with pytest.raises(fastapi.HTTPException) as err:
        worker.run_configured_once(factory, None, "w-starve", settings=settings)
    assert "ACTIVE_RUNTIME_" in str(err.value.detail)
    assert worker._text_preparation_ticks == 0, "the blocked tick must reset the burst counter"
    worker._text_preparation_ticks = 0
    ok = worker._advance_text_preparation(factory, settings, "w-starve")
    assert ok is True, "the queued outline must be claimable by the burst"
    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        # the burst really claimed and ran the task (any progressed state
        # proves the counter reset worked; the mock answer's schema fit is
        # not what this regression is about)
        assert row.status in {"RUNNING", "SUCCEEDED", "NEEDS_REVIEW"}, row.trace
        assert row.state_version > 1


def test_bible_queue_only_reads_this_project_outlines(api):
    """The bible queue's outline scan is project-filtered, never global."""
    client, factory, settings = api
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    pid = create_project(client, "本卷")
    other = create_project(client, "别卷")
    doc = _make_doc(factory, pid, "第一章 甲\n浪。")
    other_doc = _make_doc(factory, other, "第一章 别\n浪。")
    with factory.begin() as db:
        doc_r = db.get(ProjectDocument, doc.id)
        plan = pipeline.plan_volumes(db, pid, doc_r)
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
        volume_doc = pipeline._volume_document(db, pid, plan.payload_json["volumes"][0]["volume_id"])
        _outline_artifact(db, pid, volume_doc, _sections_for(volume_doc.text))
    with factory.begin() as db:
        other_row = db.get(ProjectDocument, other_doc.id)
        other_outline = _outline_artifact(db, other, other_row, _sections_for(other_row.text))
    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_bible_responder())
    with factory.begin() as db:
        task = pipeline.queue_bible(db, pid, gw)
        db.commit()
        refs = task.trace["outline_refs"]
    assert [r["id"] for r in refs] != [], "this project's outline must be referenced"
    assert all(r["id"] != other_outline.id for r in refs), \
        "another project's outline must never enter the bible synthesis"

"""The staged long-script production path: only the selected range is produced.

The outline is a proposal until the user confirms it, and a confirmed outline
still does not authorise the whole drama.  This exercises the section run that
the resident Worker drives:

* only the selected production section reaches the crew chain;
* a later section without confirmed carry-over facts is refused instead of
  inventing the previous section's end state;
* the context the crew receives is re-derived on the server, so a forged
  section id or source range is refused;
* a repeated request for the same selection replays instead of re-running.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord, AuditEvent
from tests.test_film_crew import FACTS, prepare
from tests.test_material_scenes import _responder as scene_responder

pytestmark = pytest.mark.integration

LOCAL = {"x-shine-local-action": "1"}

CHAPTER_ONE = "第一章 起点\n林：你来了。\n阿尔法在海边等待落日。\n"
CHAPTER_TWO = "第二章 归来\n贝塔在山谷奔跑过桥。\n"
FIRST_MARKER = "阿尔法在海边等待落日。"
SECOND_MARKER = "贝塔在山谷奔跑过桥。"


def _crew_responder(master_id: str, seen: list[dict]):
    """One transport that answers the crew contracts and the outline merge."""

    def respond(messages, schema):
        title = schema.get("title")
        payload = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
        seen.append({"title": title, "payload": payload})
        if title == "OutlinePart":
            return {"schema_version": "1.0", "summary": "一次离家与归来。",
                    "story_line": ["开端", "发展"], "timeline": ["按原文顺序"],
                    "relationships": [], "questions": []}
        if title == "ScriptBreakdown":
            # The crew contract checks the locked facts it was asked to carry.
            facts = payload.get("locked_story_facts") or FACTS
            # A Chinese locked fact needs its English prompt translation.
            return {"locked_story_facts": facts,
                    "prompt_story_facts": ["A locked fact of the script." for _ in facts],
                    "beats": [{"id": "B1", "description": "She waits beside a sealed letter.",
                               "characters": ["Lin"],
                               "dialogue": [{"speaker_id": "Lin", "text": "你来了。",
                                             "start_s": 1, "end_s": 2}]}]}
        if title == "DirectorialPlan":
            return {"shots": [{"id": "I1", "beat_id": "B1",
                               "visual_description": "A woman waits beside a sealed letter at a wooden table.",
                               "duration_s": 7, "asset_ids": [master_id]}]}
        if title == "CameraPlan":
            return {"shots": [{"intent_id": "I1", "composition": "Medium shot",
                               "movement": "Static", "lighting": "Soft key", "axis": "Keep axis"}]}
        if title == "ContinuityReport":
            return {"issues": [], "recommendation": "proceed_to_preflight"}
        if title == "EditIntentPlan":
            return {"schema_version": "1.2", "intent_id": "D1",
                    "clips": [{"intent_id": "I1", "order": 1, "planned_duration_s": 7.0,
                               "transition": "CUT", "continuity_note": "establishing"}],
                    "rhythm": "steady", "dialogue_pace": "natural Mandarin pace"}
        return scene_responder(messages, schema)

    return respond


def _studio(client, script: str):
    project_id, _style, _profile = prepare(client, character_entity_id="Lin",
                                           character_filename="Lin.png")
    master = next(a for a in client.get(f"/api/v1/projects/{project_id}/assets").json()
                  if a["role"] == "character")
    document = client.post(f"/api/v1/projects/{project_id}/documents",
                           files={"file": ("script.txt", script.encode(), "text/plain")}).json()
    return project_id, master["id"], document


def _confirmed_outline(client, factory, project_id, document, gateway):
    """Run the outline to a confirmed artifact and return its sections."""
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/outlines", json={
        "document_id": document["id"], "expected_document_hash": document["sha256"],
        "locked_facts": ["钥匙在阿德手里"]}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["task_id"]
    for _ in range(80):
        if not story.run_pending_story_task(factory, None, "section-worker", gateway=gateway):
            break
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "SUCCEEDED", task.trace
        outline = db.get(AgentArtifact, task.trace["outline_artifact_id"])
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/story-preparation/outlines/{outline.id}/confirm",
        json={"expected_sha256": outline.sha256}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return outline


def test_only_the_selected_section_is_produced_and_the_context_is_verified(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    script = CHAPTER_ONE + CHAPTER_TWO
    project_id, master_id, document = _studio(client, script)
    seen: list[dict] = []
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_crew_responder(master_id, seen))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    sections = outline.payload_json["production_sections"]
    assert len(sections) == 2, sections
    first, second = sections[0], sections[1]
    assert first["order"] == 1 and second["order"] == 2

    # A later section needs confirmed carry-over facts; without them the
    # previous section's end state would have to be invented.
    refused = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [second["production_section_id"]]}, headers=LOCAL)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "STORY_CONTINUITY_FACTS_REQUIRED"

    # The user selects the first section only.
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [first["production_section_id"]]}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    section_task = queued.json()["task_id"]
    # The same selection again replays the same durable request.
    again = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [first["production_section_id"]]}, headers=LOCAL)
    assert again.json()["task_id"] == section_task and again.json()["replayed"] is True

    for _ in range(80):
        if not story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway):
            break
    with factory() as db:
        task = db.get(AgentTaskRecord, section_task)
        assert task.status == "SUCCEEDED", task.trace.get("blocked_result")
        results = task.trace["results"]
        assert len(results) == 1
        assert results[0]["unit"]["production_section_id"] == first["production_section_id"]
        storyboard = results[0]["storyboard"]
        assert storyboard["status"] == "PROPOSED"

    # The crew chain only ever saw the selected range.
    crew_calls = [entry for entry in seen if entry["title"] == "ScriptBreakdown"]
    assert crew_calls, seen
    for entry in crew_calls:
        assert FIRST_MARKER in json.dumps(entry["payload"], ensure_ascii=False)
        assert SECOND_MARKER not in json.dumps(entry["payload"], ensure_ascii=False)

    # The context is re-derived on the server: a forged section id or a source
    # range outside the approved section is refused.
    with factory() as db:
        from shine.models import ProjectDocument

        real = db.get(ProjectDocument, document["id"])
        unit = results[0]["unit"]
        good = {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                "production_section_id": first["production_section_id"],
                "source_offsets": {"start_offset": unit["start_offset"],
                                   "end_offset": unit["end_offset"]}}
        selected = real.text[unit["start_offset"]:unit["end_offset"]]
        verified = story.verified_production_context(db, project_id, selected, good)
        assert verified["production_section_id"] == first["production_section_id"]
        forged_id = {**good, "production_section_id": "not-a-real-section"}
        with pytest.raises(ValueError, match="STORY_SOURCE_RANGE_INVALID"):
            story.verified_production_context(db, project_id, selected, forged_id)
        forged_range = {**good, "source_offsets": {"start_offset": 0, "end_offset": len(real.text)}}
        with pytest.raises(ValueError, match="STORY_SOURCE_RANGE_INVALID"):
            story.verified_production_context(db, project_id, real.text, forged_range)


def test_unknown_section_role_cannot_be_replaced_without_not_sent_proof(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id, master_id, document = _studio(client, CHAPTER_ONE)
    seen: list[dict] = []
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_crew_responder(master_id, seen))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    section = outline.payload_json["production_sections"][0]
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [section["production_section_id"]]}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    parent_id = queued.json()["task_id"]
    for _ in range(80):
        if not story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway):
            break
    with factory.begin() as db:
        parent = db.get(AgentTaskRecord, parent_id)
        assert parent.status == "SUCCEEDED", parent.trace.get("blocked_result")
        result = parent.trace["results"][0]["storyboard"]
        successful_editor = db.get(AgentArtifact, result["steps"]["editor_edit_intent"]["artifact_id"])
        original_editor_task = db.get(AgentTaskRecord, successful_editor.source_task_id)
        # Model the durable ledger state after the product's local interruption
        # verification and abandon action, without altering the successful row
        # or deleting its immutable artifact.
        unknown_trace = {key: value for key, value in original_editor_task.trace.items()
            if key not in {"received_result_sha256", "output_hash", "schema_valid", "latency_ms", "usage"}}
        unknown_trace.update({"is_mock": False,
            "phase": "WAITING_PROVIDER", "external_outcome": "UNKNOWN", "automatic_replay": False,
            "local_interruption_verified": True,
            "local_interruption_evidence": {"reason": "test-only verified local interruption"}})
        unknown = AgentTaskRecord(
            project_id=project_id, role_id="editor", input_hash=original_editor_task.input_hash,
            style_version_id=original_editor_task.style_version_id,
            asset_manifest_hash=original_editor_task.asset_manifest_hash,
            rulepack_id=original_editor_task.rulepack_id, rulepack_hash=original_editor_task.rulepack_hash,
            model_profile_id=original_editor_task.model_profile_id, model_id=original_editor_task.model_id,
            schema_version=original_editor_task.schema_version, status="ABANDONED", error_code="LLM_TIMEOUT",
            trace=unknown_trace,
        )
        db.add(unknown)
        db.flush()
        prior_steps = {name: step["artifact_id"] for name, step in result["steps"].items()
                       if name in {"scriptwriter", "director", "cinematographer", "continuity_supervisor"}}
        parent.status, parent.error_code = "NEEDS_REVIEW", "REJECTED"
        parent.trace = {**parent.trace, "cursor": 0, "results": [], "failed_role": "editor",
            "resume_steps": prior_steps, "blocked_subtasks": [],
            "blocked_result": {"status": "REJECTED", "steps": {},
                "rejections": [{"role": "editor", "code": "AGENT_REQUEST_REPLAY_BLOCKED", "task_id": unknown.id}]}}
        parent.state_version += 1
        unknown_id = unknown.id
    calls_before_resume = len(seen)

    resumed = client.post(f"/api/v1/projects/{project_id}/story-preparation/tasks/{parent_id}/resume",
        json={"expected_state_version": parent.state_version}, headers=LOCAL)
    assert resumed.status_code == 200, resumed.text
    assert story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway)
    assert len(seen) == calls_before_resume
    with factory() as db:
        parent = db.get(AgentTaskRecord, parent_id)
        assert parent.status == "NEEDS_REVIEW"
        assert parent.trace["failed_role"] == "editor"
        assert parent.trace["resume_steps"] == prior_steps
        expected_state_version = parent.state_version

    replaced = client.post(
        f"/api/v1/projects/{project_id}/story-preparation/tasks/{parent_id}/replace-unknown",
        json={"expected_state_version": expected_state_version, "subtask_id": unknown_id}, headers=LOCAL)
    assert replaced.status_code == 409, replaced.text
    assert replaced.json()["detail"] == "AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION"
    assert len(seen) == calls_before_resume
    with factory() as db:
        parent = db.get(AgentTaskRecord, parent_id)
        unknown = db.get(AgentTaskRecord, unknown_id)
        assert parent.status == "NEEDS_REVIEW"
        assert unknown.status == "ABANDONED"
        assert unknown.trace["external_outcome"] == "UNKNOWN"
        assert unknown.trace["automatic_replay"] is False
        replacement = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == parent_id,
            AuditEvent.event_type == "STORY_UNKNOWN_SUBTASK_REPLACED"))
        assert replacement is None


def test_a_newer_run_that_covered_a_section_does_not_borrow_older_shots(api):
    # 活缺陷（2026-11-16）：新 run 覆盖了某段但产 0 镜（合并标题/尚未落镜）时，
    # 旧逻辑回退借用上一轮 run 的整组镜头，把同一个故事重复挂到多个段上。
    # 最新且覆盖该段的 run 即权威事实：0 镜如实报缺失，绝不回借历史。
    import datetime

    from shine.director.streamline import DIRECTOR_STORYBOARD_RUN
    from shine.materialprep.story import section_storyboard_shot_ids
    client, factory, _settings = api
    project_id, _master, _document = _studio(client, CHAPTER_ONE)
    base = datetime.datetime(2026, 11, 16, 10, 0, 0)

    def _run(input_hash, status, created_at, shot_ids):
        return AgentTaskRecord(
            project_id=project_id, role_id=DIRECTOR_STORYBOARD_RUN, input_hash=input_hash,
            asset_manifest_hash="amh", rulepack_id="rp", rulepack_hash="rh",
            model_profile_id="crew-role-routing", model_id="existing-film-crew",
            schema_version="1.0", status=status, created_at=created_at,
            trace={"units": [{"production_section_id": "sec-X", "start_offset": 0}],
                   "results": [{"unit": {"start_offset": 0}, "apply": {"shot_ids": shot_ids}}]})

    with factory.begin() as db:
        db.add(_run("old", "PAUSED", base, ["shot-old-1", "shot-old-2"]))
        db.add(_run("new", "RUNNING", base + datetime.timedelta(minutes=5), []))
    with factory() as db:
        resolved = section_storyboard_shot_ids(db, project_id, "sec-X")
    assert resolved["shot_ids"] == [], "the newer run covered the section; its empty result is authoritative"
    assert resolved["missing"], "reported as not applied, never borrowed from the old run"


def test_the_episode_shot_table_reads_the_live_run_not_the_frozen_confirmation(api):
    # 活缺陷（2026-11-16）：确认过 v6（17 镜）后重跑拆镜，面板仍显示旧确认的
    # 17 镜——episode_shot_table 优先用冻结快照 target.shot_ids，最新 run 落的
    # 新镜永远不显示，重跑看起来只是在叠加记录。表必须读最新逐段落镜（与确认
    # 同源），并如实报「与已确认快照不同，需重新确认」。
    import datetime

    from shine.director.models import DirectorEpisodePlan
    from shine.director.streamline import DIRECTOR_STORYBOARD_RUN, episode_shot_table
    from shine.models import Shot
    client, factory, _settings = api
    project_id, _master, _document = _studio(client, CHAPTER_ONE)
    base = datetime.datetime(2026, 11, 16, 10, 0, 0)

    def _shot(sid):
        return Shot(id=sid, project_id=project_id, name=sid, revision=1,
                    spec={"story_beat": "beat", "duration_target_s": 10.0,
                          "characters": [], "dialogue": [], "continuity": {"mode": "cut"}})

    with factory.begin() as db:
        for sid in ("shot-old-1", "shot-old-2", "shot-new-1"):
            db.add(_shot(sid))
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=10,
            payload={"mode": "sections", "target_seconds": 60, "tolerance_seconds": 10,
                     "episodes": [{"episode_id": "ep-01", "order": 1, "planned_seconds": 20.0,
                                   "shot_ids": ["shot-old-1", "shot-old-2"], "section_ids": ["sec-X"]}]},
            sha256="x" * 64, source="script_sections_materialized", created_by="local-user"))
        db.add(AgentTaskRecord(
            project_id=project_id, role_id=DIRECTOR_STORYBOARD_RUN, input_hash="new",
            asset_manifest_hash="amh", rulepack_id="rp", rulepack_hash="rh",
            model_profile_id="crew-role-routing", model_id="existing-film-crew",
            schema_version="1.0", status="SUCCEEDED",
            created_at=base + datetime.timedelta(minutes=5),
            trace={"units": [{"production_section_id": "sec-X", "start_offset": 0}],
                   "results": [{"unit": {"start_offset": 0}, "apply": {"shot_ids": ["shot-new-1"]}}]}))
    with factory() as db:
        table = episode_shot_table(db, project_id, "ep-01")
    assert [row["shot_id"] for row in table["shots"]] == ["shot-new-1"], \
        "the live re-run shot, not the frozen v1 pair"
    assert table["confirmed_shot_ids"] == ["shot-old-1", "shot-old-2"]
    assert table["confirmed_version"] == 1
    assert table["confirmation_stale"] is True


def test_section_reconcile_proves_waiting_resource_failure_was_not_sent(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id, master_id, document = _studio(client, CHAPTER_ONE)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_crew_responder(master_id, []))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    section = outline.payload_json["production_sections"][0]
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [section["production_section_id"]]}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    parent_id = queued.json()["task_id"]
    for _ in range(80):
        if not story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway):
            break

    with factory.begin() as db:
        parent = db.get(AgentTaskRecord, parent_id)
        assert parent.status == "SUCCEEDED", parent.trace.get("blocked_result")
        director_artifact = db.get(AgentArtifact,
                                   parent.trace["results"][0]["storyboard"]["steps"]["director"]["artifact_id"])
        original = db.get(AgentTaskRecord, director_artifact.source_task_id)
        unknown = AgentTaskRecord(
            project_id=project_id, role_id="director", input_hash=original.input_hash,
            style_version_id=original.style_version_id, asset_manifest_hash=original.asset_manifest_hash,
            rulepack_id=original.rulepack_id, rulepack_hash=original.rulepack_hash,
            model_profile_id=original.model_profile_id, model_id=original.model_id,
            schema_version=original.schema_version, status="NEEDS_REVIEW", error_code="AGENT_CALL_FAILED",
            request_identity=f"synthetic-waiting-resource:{parent_id}",
            trace={**original.trace, "phase": "WAITING_RESOURCE", "external_outcome": "UNKNOWN",
                   "automatic_replay": False, "is_mock": False},
        )
        db.add(unknown)
        db.flush()
        parent.status, parent.error_code = "NEEDS_REVIEW", "REJECTED"
        parent.trace = {**parent.trace, "blocked_subtasks": [],
                        "blocked_result": {"status": "REJECTED", "rejections": [{
                            "role": "director", "code": "AGENT_CALL_FAILED", "task_id": unknown.id,
                        }]}}
        parent.state_version += 1
        unknown_id, version = unknown.id, parent.state_version

    reconciled = client.post(
        f"/api/v1/projects/{project_id}/story-preparation/tasks/{parent_id}/reconcile",
        json={"expected_state_version": version}, headers=LOCAL,
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["reconciliation"]["outcomes"] == [{
        "subtask_id": unknown_id, "outcome": "NOT_SENT_PROVEN", "last_phase": "WAITING_RESOURCE",
    }]
    with factory() as db:
        unknown = db.get(AgentTaskRecord, unknown_id)
        parent = db.get(AgentTaskRecord, parent_id)
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == parent_id,
            AuditEvent.event_type == "STORY_SUBTASKS_RECONCILED",
        ))
        assert unknown.status == "SUPERSEDED"
        assert unknown.trace["external_outcome"] == "NOT_SENT"
        assert unknown.trace["reconciliation"]["outcome"] == "NOT_SENT_PROVEN"
        assert unknown.request_identity is None
        assert parent.trace["blocked_subtasks"][0]["status"] == "NOT_SENT"
        assert audit is not None and audit.actor == "local-user"

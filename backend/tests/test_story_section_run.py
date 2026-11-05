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

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord
from tests.test_film_crew import FACTS, prepare
from tests.test_material_scenes import _responder as scene_responder

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

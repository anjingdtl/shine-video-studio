"""Versioned section continuity: the carry-over is derived, never asserted.

The long-script production path used to accept free-text carry-over facts and
always told the crew ``previous_render_status=NOT_CLAIMED``.  A real carry-over
now exists as a frozen, immutable end state derived from the accepted media of
the previous production section:

* a later section may be queued without user facts only when the prior
  section's shots were really storyboarded, applied and accepted (a mock
  render is never acceptance);
* the context the crew receives is re-derived on the server, so a forged
  ``previous_render_status`` is overwritten and an end state that no longer
  recomputes identically (a retake passed) refuses the run instead of
  silently dropping the facts the plan consumed;
* freezing is idempotent for the same production and versions forward on a
  retake — the previous frozen state is never rewritten.
"""

from __future__ import annotations

import pytest

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord, Job
from tests.test_generation_segments import _passed
from tests.test_story_section_run import (
    CHAPTER_ONE,
    CHAPTER_TWO,
    LOCAL,
    _confirmed_outline,
    _studio,
)


def _continuity_responder(master_id: str):
    """The crew contracts, honest about dialogue-free source text."""

    def respond(messages, schema):
        import json

        title = schema.get("title")
        payload = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
        if title == "OutlinePart":
            return {"schema_version": "1.0", "summary": "一次离家与归来。",
                    "story_line": ["开端", "发展"], "timeline": ["按原文顺序"],
                    "relationships": [], "questions": []}
        if title == "ScriptStructure":
            return {"character_names": {}, "ranges": [{"id": "B1", "last_fragment": len(payload["fragments"])}],
                    "prompt_story_facts": ["A locked fact of the script." for _ in payload["locked_story_facts"]]}
        if title == "SceneBeat":
            dialogue = [{"speaker_id": "Lin", "text": "你来了。", "start_s": 1, "end_s": 2}
                        ] if "你来了" in payload["beat_text"] else []
            return {"id": payload["beat_id"], "description": "She waits beside a sealed letter.",
                    "characters": ["Lin"], "dialogue": dialogue}
        if title == "DirectorStructure":
            return {"segments": [{"id": "I1", "beat_id": "B1", "duration_s": 7}]}
        if title == "ShotDetails":
            return {"visual_description": "A woman waits beside a sealed letter at a wooden table.",
                    "asset_ids": [master_id], "cut_descriptions": []}
        if title == "CameraShot":
            return {"intent_id": payload["segment"]["id"], "composition": "Medium shot",
                    "movement": "Static", "lighting": "Soft key", "axis": "Keep axis"}
        if title == "ScriptBreakdown":
            brief = payload.get("brief", "")
            dialogue = [{"speaker_id": "Lin", "text": "你来了。",
                         "start_s": 1, "end_s": 2}] if "你来了" in brief else []
            return {"locked_story_facts": payload.get("locked_story_facts") or [],
                    "prompt_story_facts": ["A locked fact of the script."
                                           for _ in (payload.get("locked_story_facts") or [])],
                    "beats": [{"id": "B1", "description": "She waits beside a sealed letter.",
                               "characters": ["Lin"], "dialogue": dialogue}]}
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
        from tests.test_material_scenes import _responder as scene_responder

        return scene_responder(messages, schema)

    return respond


def _produce_section(client, factory, settings, gateway, project_id, outline, section):
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [section["production_section_id"]],
        "continuity_facts": {}}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    for _ in range(80):
        if not story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway):
            break
    with factory() as db:
        task = db.get(AgentTaskRecord, queued.json()["task_id"])
        assert task.status == "SUCCEEDED", task.trace.get("blocked_result")
        results = task.trace["results"]
    camera = results[0]["storyboard"]["steps"]["cinematographer"]["artifact_id"]
    applied = client.post(f"/api/v1/projects/{project_id}/film-artifacts/{camera}/apply-camera", json={
        "continuity_artifact_id": results[0]["storyboard"]["steps"]["continuity_supervisor"]["artifact_id"],
        "arbitration_artifact_id": results[0]["storyboard"]["steps"].get("director_arbitration", {}).get("artifact_id"),
        "name_prefix": "section"}, headers=LOCAL)
    assert applied.status_code == 200, applied.text
    return applied.json()["shot_ids"]


def _accept_shots(client, factory, project_id, shot_ids):
    """Real (non-mock) accepted productions for the section's shots.

    Applying a storyboard only preflights the render request; the jobs are
    queued by the production path, so this fixture stands in for a finished
    real render of every shot.
    """
    from tests.conftest import create_job
    from tests.test_production_series import _register_ref2va_profile

    profile = _register_ref2va_profile(client)
    job_ids = [create_job(client, project_id, shot_id, provider="comfyui",
                          workflow_profile_id=profile["id"])["id"] for shot_id in shot_ids]
    with factory.begin() as db:
        for job_id in job_ids:
            _passed(db, db.get(Job, job_id))
    return job_ids


def test_verified_prior_end_state_replaces_free_text_facts(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id, master_id, document = _studio(client, CHAPTER_ONE + CHAPTER_TWO)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_continuity_responder(master_id))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    first, second = outline.payload_json["production_sections"][:2]

    shots = _produce_section(client, factory, settings, gateway, project_id, outline, first)

    # Before any render is accepted the later section still needs user facts.
    refused = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [second["production_section_id"]]}, headers=LOCAL)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "STORY_CONTINUITY_FACTS_REQUIRED"
    with factory() as db:
        live = story.compute_section_continuity(db, project_id, first["production_section_id"])
    assert live["status"] == "PARTIAL", live

    _accept_shots(client, factory, project_id, shots)

    # The read endpoint reports the verified prior end state without writing.
    view = client.get(f"/api/v1/projects/{project_id}/story-preparation/outlines/{outline.id}/continuity")
    assert view.status_code == 200, view.text
    entry = next(s for s in view.json()["sections"] if s["order"] == 2)
    assert entry["verified_prior_available"] is True
    assert entry["prior_production_status"] == "VERIFIED"

    # A later section may now be queued with no user facts at all: the frozen
    # end state is the carry-over.
    queued = client.post(f"/api/v1/projects/{project_id}/story-preparation/sections", json={
        "outline_artifact_id": outline.id, "expected_outline_hash": outline.sha256,
        "production_section_ids": [second["production_section_id"]]}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    for _ in range(80):
        if not story.run_pending_story_task(factory, settings, "section-worker", gateway=gateway):
            break
    with factory() as db:
        task = db.get(AgentTaskRecord, queued.json()["task_id"])
        assert task.status == "SUCCEEDED", task.trace.get("blocked_result")
        unit = task.trace["results"][0]["unit"]
        section_text = db.get(story.ProjectDocument, document["id"]).text[
            unit["start_offset"]:unit["end_offset"]]
        context = story.verified_production_context(
            db, project_id, section_text,
            {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
             "production_section_id": second["production_section_id"], "source_offsets": unit,
             "continuity": unit["continuity"]})
        assert context["previous_render_status"] == "VERIFIED_FROM_PRIOR_SECTION"
        assert context["continuity"]["basis"] == "verified_section_state"
        assert context["continuity"]["end_state"]["last_shot_id"] == shots[-1]
        assert context["continuity"]["end_state"]["assets_used"]

    # Freezing the same production again reuses the same artifact.
    with factory.begin() as db:
        artifact, _payload = story.freeze_section_continuity(
            db, project_id, first["production_section_id"])
    with factory.begin() as db:
        again, _payload2 = story.freeze_section_continuity(
            db, project_id, first["production_section_id"])
    assert again.id == artifact.id


def test_forged_render_claim_is_overwritten_and_superseded_state_refuses(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id, master_id, document = _studio(client, CHAPTER_ONE + CHAPTER_TWO)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_continuity_responder(master_id))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    first, second = outline.payload_json["production_sections"][:2]
    shots = _produce_section(client, factory, settings, gateway, project_id, outline, first)
    _accept_shots(client, factory, project_id, shots)
    with factory() as db:
        document_row = db.get(story.ProjectDocument, document["id"])
        unit = {"start_offset": first["start_offset"], "end_offset": first["end_offset"]}
        first_text = document_row.text[first["start_offset"]:first["end_offset"]]

        # A caller that asserts a render happened is ignored: without the
        # verified basis the server reports NOT_CLAIMED.
        forged = story.verified_production_context(db, project_id, first_text,
                                                   {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                                                    "production_section_id": first["production_section_id"],
                                                    "source_offsets": unit,
                                                    "previous_render_status": "VERIFIED_FROM_PRIOR_SECTION"})
        assert forged["previous_render_status"] == "NOT_CLAIMED"
        assert forged["continuity"]["basis"] == "user_supplied_unverified"

    # The honest verified basis for the first section against itself.
    with factory.begin() as db:
        frozen, _live = story.freeze_section_continuity(db, project_id, first["production_section_id"])
        assert frozen is not None
        second_text = db.get(story.ProjectDocument, document["id"]).text[
            second["start_offset"]:second["end_offset"]]
        basis = {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                 "production_section_id": second["production_section_id"],
                 "source_offsets": {"start_offset": second["start_offset"], "end_offset": second["end_offset"]},
                 "continuity": {"basis": "verified_section_state", "continuity_artifact_id": frozen.id,
                                "continuity_sha256": frozen.sha256,
                                "prior_production_section_id": first["production_section_id"]}}
        assert (story.verified_production_context(db, project_id, second_text, basis)
                ["previous_render_status"] == "VERIFIED_FROM_PRIOR_SECTION")

    # A retake that passes after the plan was queued changes the end state:
    # the run consumed facts that are no longer true, so it is refused.
    from tests.conftest import create_job, register_workflow_profile

    retake_profile = register_workflow_profile(client, mode="Ref2VA")
    new_job = create_job(client, project_id, shots[-1], provider="comfyui",
                         workflow_profile_id=retake_profile["id"])
    with factory.begin() as db:
        _passed(db, db.get(Job, new_job["id"]))
    with factory() as db:
        with pytest.raises(ValueError, match="STORY_CONTINUITY_SUPERSEDED"):
            story.verified_production_context(db, project_id, second_text, basis)
    # The superseded end state versions forward; the old artifact stays.
    with factory.begin() as db:
        newer, _live = story.freeze_section_continuity(db, project_id, first["production_section_id"])
    with factory() as db:
        assert newer.id != frozen.id
        assert db.get(AgentArtifact, frozen.id).sha256 == frozen.sha256  # never rewritten


def test_changed_outline_section_invalidates_derived_end_state(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id, master_id, document = _studio(client, CHAPTER_ONE + CHAPTER_TWO)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_continuity_responder(master_id))
    outline = _confirmed_outline(client, factory, project_id, document, gateway)
    first, second = outline.payload_json["production_sections"][:2]
    shots = _produce_section(client, factory, settings, gateway, project_id, outline, first)
    _accept_shots(client, factory, project_id, shots)
    with factory.begin() as db:
        frozen, _live = story.freeze_section_continuity(db, project_id, first["production_section_id"])
        assert frozen is not None

        # The document is re-imported with the first section rewritten.
        rewritten = CHAPTER_ONE.replace("林：你来了。", "林：你先到了。") + CHAPTER_TWO
        row = db.get(story.ProjectDocument, document["id"])
        row.text = rewritten
        import hashlib

        row.sha256 = hashlib.sha256(rewritten.encode()).hexdigest()
        import copy

        payload = copy.deepcopy(outline.payload_json)
        payload["document_sha256"] = row.sha256
        for section in payload["production_sections"]:
            section["source_sha256"] = hashlib.sha256(
                rewritten[section["start_offset"]:section["end_offset"]].encode()).hexdigest()
        new_outline = AgentArtifact(project_id=project_id, kind="StoryOutline", schema_version="1.0",
                                    payload_json=payload, sha256=story.digest(payload),
                                    producer_role="scriptwriter", source_task_id=frozen.source_task_id,
                                    approved_at=story.utcnow(), approved_by="local-user")
        db.add(new_outline)
        db.flush()
        changed = story.select_outline_revision(db, project_id, new_outline)
        assert first["production_section_id"] in changed
        assert db.get(AgentArtifact, frozen.id).stale is True

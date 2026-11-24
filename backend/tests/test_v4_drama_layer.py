"""The drama layer of the director lane (V4).

A novel's prose is not a shooting script.  On the director lane nothing reaches
the crew until the scene exists as a drama scene script: the architect ranks
the person's confirmed plan once, then every whole scene is adapted before the
storyboard reads it.  These tests drive the resident worker's own director
step end to end (architecture -> adaptation -> crew -> landing) with the mock
model, and pin what the ledger records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from sqlalchemy import select

from shine.director import streamline
from shine.literary.requests import SCHEMA_INSTRUCTION
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument
from tests.test_director_streamline import (
    LOCAL,
    _approved_outline,
    _chain_responder,
    _document,
    _prepare_project,
    _storyboard_book,
)
from tests.test_film_crew import prepare

CREW = {"ScriptBreakdown", "DirectorialPlan", "CameraPlan", "ContinuityReport", "EditIntentPlan"}


@dataclass
class Call:
    """One model call the mock model answered."""

    title: str
    brief: str
    system: str
    context: dict


def _book_and_queue(client, project, *, acknowledge=False, **book):
    result = _storyboard_book(client, project, **book)
    body = {"plan_version": result["plan_version"], "expected_sha256": result["plan_sha256"],
            "style": {"profile_id": "wong-kar-wai", "film_type": "live_action",
                      "dialogue_ratio_percent": 80}}
    if acknowledge:
        body["acknowledge_deviation"] = True
    confirmed = client.post(f"/api/v1/projects/{project}/director/storyboard-book/confirm",
                            json=body, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    return result


def _master(client, project):
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    return next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])


def _recording(settings, master, *, answer_for=None):
    """A gateway whose model is the chain mock, with every call recorded.

    ``answer_for(call, answer)`` may return a different answer for one call.
    """
    base = _chain_responder({"asset": master})
    calls: list[Call] = []

    def respond(messages, schema):
        context = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
        answer = base(messages, schema)
        call = Call(schema.get("title"), context.get("brief") or "", messages[0].content, context)
        calls.append(call)
        return answer_for(call, answer) if answer_for else answer

    return LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=respond), calls


def _drive(factory, settings, gateway, *, ticks=60):
    for _ in range(ticks):
        if not streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway):
            break


def _artifacts(db, project, kind):
    return list(db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project, AgentArtifact.kind == kind).order_by(AgentArtifact.created_at)))


def _titles(calls):
    return [call.title for call in calls]


def _state(client, project):
    return client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()


def test_the_crew_reads_the_adapted_scene_never_the_novels_prose(api):
    client, factory, settings = api
    project, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    # One scene far past the old 1,200-character slice, carrying prose no
    # camera can record.
    inner = "老周心想这只表根本没坏"
    body = f"第1场\n{inner}。他把怀表贴近耳朵。\n" + "雨下个不停。" * 300 + "\n"
    document = _document(client, project, "心理描写.txt", body)
    outline = _approved_outline(factory, project, document, [{
        "production_section_id": "sec-01", "order": 1, "title": "第1场",
        "estimated_duration_s": 400.0, "start_offset": 0, "end_offset": len(body)}])
    _book_and_queue(client, project, episode_count=1, target_seconds=60, acknowledge=True)
    gateway, calls = _recording(settings, _master(client, project))
    _drive(factory, settings, gateway)

    state = _state(client, project)
    assert state["status"] == "SUCCEEDED", state
    # One whole scene is one unit: the novel was never sliced on its way in.
    assert state["total_units"] == 1
    with factory() as db:
        scenes = _artifacts(db, project, "DramaSceneScript")
        architectures = _artifacts(db, project, "SeriesArchitecture")
        breakdown = _artifacts(db, project, "ScriptBreakdown")[0].payload_json
    assert len(scenes) == 1 and len(architectures) == 1
    payload = scenes[0].payload_json

    # The writer read the whole scene, prose and all.
    writer_calls = [call for call in calls if call.title == "DramaSceneScript"]
    assert len(writer_calls) == 1 and len(writer_calls[0].brief) > 1200
    assert inner in writer_calls[0].brief
    # Every crew role read the adapted scene script, and nothing else.
    crew_calls = [call for call in calls if call.title in CREW]
    assert {call.title for call in crew_calls} == CREW
    assert {call.brief for call in crew_calls} == {payload["text"]}
    assert all(inner not in call.brief for call in crew_calls)
    assert "老周：又停在十二点。" in payload["text"]

    # The ledger says where the scene came from and what it is worth.
    assert payload["production_section_id"] == "sec-01" and payload["episode_id"] == "ep-01"
    assert payload["source_offsets"] == {"start_offset": 0, "end_offset": len(body),
                                         "merged_section_ids": []}
    assert payload["outline_sha256"] == outline["sha256"]
    assert payload["architecture_sha256"] == architectures[0].sha256
    assert payload["scene_budget_seconds"] == 60.0
    assert payload["scene_seconds"] > 0
    # The breakdown is the scene's own numbered beats, with its spoken line.
    assert [beat["id"] for beat in breakdown["beats"]] == ["B1", "B2"]
    assert breakdown["beats"][0]["dialogue"][0]["text"] == "又停在十二点。"


def test_the_architecture_is_written_once_and_gives_every_scene_its_share(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _book_and_queue(client, project, episode_count=2, target_seconds=60)
    gateway, calls = _recording(settings, _master(client, project))
    _drive(factory, settings, gateway)

    assert _state(client, project)["status"] == "SUCCEEDED"
    titles = _titles(calls)
    # One architect call for the whole plan, however many units or episodes
    # follow; one writer call per scene.
    assert titles.count("SeriesArchitecture") == 1
    assert titles.count("DramaSceneScript") == 4
    with factory() as db:
        architectures = _artifacts(db, project, "SeriesArchitecture")
        scenes = _artifacts(db, project, "DramaSceneScript")
    assert len(architectures) == 1 and len(scenes) == 4
    architecture = architectures[0].payload_json
    assert architecture["plan_sha256"] == book["plan_sha256"]
    assert architecture["outline_sha256"] == prepared["outline"]["sha256"]
    assert sorted(slot["production_section_id"] for slot in architecture["scene_slots"]) == [
        "sec-01", "sec-02", "sec-03", "sec-04"]

    # Each episode's confirmed seconds are shared among its scenes: the four
    # scenes weigh alike, so each is written to 30 of its episode's 60 seconds.
    writer_calls = [call for call in calls if call.title == "DramaSceneScript"]
    budgets = sorted((call.context["production_context"]["episode_id"],
                      call.context["production_context"]["scene_budget_seconds"]) for call in writer_calls)
    assert budgets == [("ep-01", 30.0), ("ep-01", 30.0), ("ep-02", 30.0), ("ep-02", 30.0)]
    # A scene that continues its episode is told where the previous scene left
    # its people; the first scene of an episode is told nothing, because
    # nothing is carried across an episode boundary.
    bridges = [(call.context["production_context"]["production_section_id"],
                call.context["production_context"].get("previous_scene"))
               for call in writer_calls]
    assert [section for section, _ in bridges] == ["sec-01", "sec-02", "sec-03", "sec-04"]
    assert bridges[0][1] is None and bridges[2][1] is None
    assert bridges[1][1]["exit_state"] == "灯灭了。"
    assert bridges[1][1]["last_action"] == "客人留下硬币，走进雨里。"
    assert bridges[3][1]["exit_state"] == "灯灭了。"
    assert all(call.context["production_context"]["scene_slot"]["location"] == "钟表铺"
               for call in writer_calls)
    assert {scene.payload_json["architecture_sha256"] for scene in scenes} == {architectures[0].sha256}


def test_a_retry_from_a_later_role_never_buys_the_scene_again(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    _book_and_queue(client, project, episode_count=1, target_seconds=60)

    # The director keeps answering above the frozen ceiling until the person
    # retries: the request's own bounded repair cannot fix it, so the unit is
    # blocked and the episode pauses.
    broken = {"on": True}

    def over_the_ceiling(call, answer):
        if call.title == "DirectorialPlan" and broken["on"]:
            answer["shots"][0]["duration_s"] = 12  # inside H3's 15 s, above the frozen 10 s
        return answer

    gateway, calls = _recording(settings, _master(client, project), answer_for=over_the_ceiling)
    _drive(factory, settings, gateway)
    state = _state(client, project)
    episode = state["episodes"][0]
    assert state["status"] == "NEEDS_REVIEW" and episode["status"] == "BLOCKED"
    # The refusal is the director's own, not the drama layer's.
    blocker = episode["blocker"]
    assert blocker["failed_role"] == "director"
    assert blocker["rejections"][0]["rejection_reason_code"] == "SHOT_DURATION_OVER_LIMIT"
    assert "导演" in blocker["summary"]
    before = _titles(calls)
    assert before.count("SeriesArchitecture") == 1 and before.count("DramaSceneScript") == 1

    # The person retries the episode: the chain continues from the director and
    # reuses the scene that was already written.
    broken["on"] = False
    retry = client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
                        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _drive(factory, settings, gateway)
    assert _state(client, project)["status"] == "SUCCEEDED"
    titles = _titles(calls)
    assert titles.count("SeriesArchitecture") == 1
    assert titles.count("DramaSceneScript") == 2  # one per scene, none re-bought
    with factory() as db:
        assert len(_artifacts(db, project, "DramaSceneScript")) == 2


def test_a_line_a_shot_cannot_hold_is_refused_by_name_and_retried_with_its_numbers(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    _book_and_queue(client, project, episode_count=1, target_seconds=60)

    def too_long_first(call, answer):
        if call.title == "DramaSceneScript" and not too_long_first.done:
            too_long_first.done = True
            return {"location": "钟表铺", "beats": [
                {"visible_action": "老周说话。",
                 "spoken_lines": [{"speaker": "老周", "text": "又停在十二点。" * 6}]}]}
        return answer
    too_long_first.done = False

    gateway, calls = _recording(settings, _master(client, project), answer_for=too_long_first)
    _drive(factory, settings, gateway)
    assert _state(client, project)["status"] == "SUCCEEDED"

    writer_prompts = [call.system for call in calls if call.title == "DramaSceneScript"]
    # The refused answer, its repair, then the second scene: nothing is
    # shortened by the server; the writer is asked again and told the numbers.
    assert len(writer_prompts) == 3
    assert "one line is at most 27 characters" in writer_prompts[0]
    assert "a spoken line of 42 characters (10.0 s) in beat 1, line 1" in writer_prompts[1]
    with factory() as db:
        refused = [row for row in db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "scriptwriter"))
            if row.trace.get("rejection_reason_code") == "DRAMA_LINE_TOO_LONG"]
        assert len(refused) == 1
        assert refused[0].trace["refusal_details"][0] == {
            "code": "DRAMA_LINE_TOO_LONG", "beat": 0, "line": 0, "characters": 42,
            "seconds": 10.0, "max_characters": 27}
        # Every scene that landed keeps every word it was written with.
        for scene in _artifacts(db, project, "DramaSceneScript"):
            assert max(len(line["text"]) for beat in scene.payload_json["beats"]
                       for line in beat["spoken_lines"]) <= 27


def test_an_architecture_that_drops_a_scene_is_refused_by_name(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    _book_and_queue(client, project, episode_count=1, target_seconds=60)

    def drops_a_scene(call, answer):
        if call.title == "SeriesArchitecture":
            answer["scene_slots"] = answer["scene_slots"][:-1]
        return answer

    gateway, calls = _recording(settings, _master(client, project), answer_for=drops_a_scene)
    _drive(factory, settings, gateway)
    state = _state(client, project)
    assert state["status"] == "NEEDS_REVIEW"
    blocker = state["episodes"][0]["blocker"]
    assert blocker["failed_role"] == "series_architecture"
    rejection = blocker["rejections"][0]
    assert rejection["rejection_reason_code"] == "ARCHITECTURE_SCENE_COVERAGE"
    # The person is told what was refused in their own words, and no scene was
    # ever handed to the crew without an architecture that ranks it.
    assert "集纲架构" in blocker["summary"]
    assert "没有给每一场各排一个场次槽位" in blocker["summary"]
    assert "DramaSceneScript" not in _titles(calls)
    with factory() as db:
        refused = [row for row in db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "scriptwriter"))
            if row.trace.get("rejection_reason_code") == "ARCHITECTURE_SCENE_COVERAGE"]
        assert refused and refused[0].trace["refusal_details"][0]["missing"] == ["sec-02"]


def test_the_drama_source_is_verified_against_the_ledger(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    _book_and_queue(client, project, episode_count=1, target_seconds=60)
    gateway, _calls = _recording(settings, _master(client, project))
    _drive(factory, settings, gateway)
    assert _state(client, project)["status"] == "SUCCEEDED"

    with factory() as db:
        architecture = _artifacts(db, project, "SeriesArchitecture")[0]
        first, second = _artifacts(db, project, "DramaSceneScript")
        raw = db.get(ProjectDocument, prepared["document"]["id"]).text[:199].strip()
        claimed = {
            "outline_artifact_id": prepared["outline"]["id"], "outline_sha256": prepared["outline"]["sha256"],
            "plan_sha256": architecture.payload_json["plan_sha256"],
            "architecture_artifact_id": architecture.id, "architecture_sha256": architecture.sha256,
            "production_section_id": "sec-01",
            "source_offsets": {"start_offset": 0, "end_offset": 199},
            "creative_source": "drama_scene_script",
            "script_artifact_id": first.id, "script_sha256": first.sha256,
        }
        # The scene written for this unit is accepted, and its context carries
        # the slot, the budget, the episode and the registered places.
        context = story.verified_production_context(
            db, project, first.payload_json["text"], claimed, require_architecture=True)
        assert context["creative_source"] == "drama_scene_script"
        assert context["scene_slot"]["production_section_id"] == "sec-01"
        # Both scenes share the episode's 60 seconds equally.
        assert context["scene_slot"]["budget_seconds"] == context["scene_budget_seconds"] == 30.0
        assert context["episode_id"] == "ep-01"
        assert context["locations"][0]["name"] == "钟表铺"

        # Another unit's scene never passes for this one.
        with pytest.raises(ValueError, match="STORY_SOURCE_RANGE_INVALID"):
            story.verified_production_context(
                db, project, second.payload_json["text"],
                {**claimed, "script_artifact_id": second.id, "script_sha256": second.sha256},
                require_architecture=True)
        # A word of the scene changed after it was written.
        with pytest.raises(ValueError, match="STORY_SOURCE_RANGE_INVALID"):
            story.verified_production_context(
                db, project, first.payload_json["text"] + "多出来的一句。", claimed, require_architecture=True)
        # A scene may not be adapted without the architecture that ranks it.
        without = {key: value for key, value in claimed.items()
                   if key not in {"architecture_artifact_id", "architecture_sha256",
                                  "creative_source", "script_artifact_id", "script_sha256"}}
        with pytest.raises(ValueError, match="STORY_ARCHITECTURE_REQUIRED"):
            story.verified_production_context(db, project, raw, without, require_architecture=True)
        # A plan the person never confirmed.
        with pytest.raises(ValueError, match="STORY_PLAN_NOT_CONFIRMED"):
            story.confirmed_plan(
                db, project, db.get(AgentArtifact, prepared["outline"]["id"]), {"plan_sha256": "0" * 64})
        # An architecture that went stale no longer ranks anything.
        architecture.stale = True
        db.flush()
        with pytest.raises(ValueError, match="STORY_ARCHITECTURE_SUPERSEDED"):
            story.verified_production_context(db, project, first.payload_json["text"], claimed,
                                              require_architecture=True)
        assert story.current_architecture(
            db, project, prepared["outline"]["sha256"], architecture.payload_json["plan_sha256"]) is None
        # A scene is reused only under the style version it was written in.
        assert story.drama_scene_for(
            db, project, outline_sha256=prepared["outline"]["sha256"], architecture_sha256=architecture.sha256,
            production_section_id="sec-01", source_offsets={"start_offset": 0, "end_offset": 199},
            style_version_id="another-style") is None


def test_the_drama_path_needs_a_confirmed_plan(api):
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    _book_and_queue(client, project, episode_count=1, target_seconds=60)
    # Asking for the drama layer without a confirmed plan is a caller error.
    response = client.post(f"/api/v1/projects/{project}/storyboard/generate",
                           json={"text": "老周修表。", "dramatize": True}, headers=LOCAL)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "STORYBOARD_DRAMA_REQUIRES_PLAN"

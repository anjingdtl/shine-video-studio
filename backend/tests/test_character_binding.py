"""Naming the masters the script names, and resuming a failed crew step.

Two rules are under test:

* A script character and an uploaded master are joined by ``entity_id``.
  Nothing joins them by itself, so the user confirms the link.  The proposal
  only reads the user's own uploads; it never approves or locks anything.
* A crew step that failed is retried from itself.  The roles that already
  produced an artifact are not called again.
"""

from __future__ import annotations

import time

from shine.models import AgentArtifact
from tests.conftest import PNG, approve_lock, upload_asset
from tests.test_film_crew import BRIEF, FACTS, prepare


def _responder(registry, *, characters=("Lin",), fail_director=False):
    def respond(messages, schema):
        title = schema.get("title")
        if title == "ScriptBreakdown":
            return {
                "locked_story_facts": FACTS,
                "beats": [
                    {"id": "B1", "description": "She waits beside a sealed letter.",
                     "characters": list(characters),
                     "dialogue": [
                         {"speaker_id": characters[0], "text": "你来了。", "start_s": 1, "end_s": 2},
                     ]},
                ],
            }
        if title == "DirectorialPlan":
            if fail_director:
                # A plan that does not satisfy the contract: the director
                # step must fail and be reported as the failed role.
                return {"shots": [{"id": "I1"}]}
            return {
                "shots": [
                    {"id": "I1", "beat_id": "B1",
                     "visual_description": "A woman waits beside a sealed letter at a wooden table.",
                     "duration_s": 7, "asset_ids": [registry["master"]]},
                ],
            }
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
        raise AssertionError(f"unexpected schema {title}")

    return respond


def _studio(client, *, entity_id="Lin", filename="Lin.png"):
    project_id, _style, _profile = prepare(
        client, character_entity_id=entity_id, character_filename=filename
    )
    assets = client.get(f"/api/v1/projects/{project_id}/assets").json()
    master = next(a for a in assets if a["role"] == "character")
    return project_id, {"master": master["id"]}


def test_character_binding_proposal_links_a_named_character_to_its_master(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    client.app.state.mock_llm_responder = _responder(registry)

    chain = client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                        json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    assert chain.status_code == 201, chain.text
    assert chain.json()["status"] == "PROPOSED"

    response = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/proposal",
                           json={})
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["characters"]) == 1
    row = body["characters"][0]
    assert row["script_name"] == "Lin"
    assert row["suggested_asset_id"] == registry["master"]
    assert row["confidence"] == "high"
    assert row["needs_real_name"] is False


def test_character_binding_proposal_splits_a_comma_list_of_names(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    client.app.state.mock_llm_responder = _responder(registry, characters=("S1, S2",))

    client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/proposal",
                           json={})
    assert response.status_code == 201, response.text
    names = [row["script_name"] for row in response.json()["characters"]]
    assert names == ["S1", "S2"]
    # A script label names nobody, so the master needs a real name.
    assert all(row["needs_real_name"] for row in response.json()["characters"])


def test_character_binding_proposal_names_a_master_from_its_own_tag(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client, entity_id="001", filename="001.png")
    client.app.state.mock_llm_responder = _responder(registry, characters=("S1",))
    client.patch(f"/api/v1/assets/{registry['master']}/metadata",
                 json={"tags": ["主角，小美"]})

    client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/proposal",
                           json={})
    assert response.status_code == 201, response.text
    row = response.json()["characters"][0]
    # One tag can carry a role and a name; the name is the part that is not a role word.
    assert row["suggested_name"] == "小美"


def test_character_binding_apply_names_the_master_and_rekeys_the_breakdown(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client, entity_id="001", filename="001.png")
    client.app.state.mock_llm_responder = _responder(registry, characters=("S1",))

    client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    proposal = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/proposal",
                           json={}).json()
    breakdown_id = proposal["breakdown_artifact_id"]

    response = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/apply",
                           json={"breakdown_artifact_id": breakdown_id,
                                 "bindings": [{"script_name": "S1", "asset_id": registry["master"],
                                               "name": "阿德"}]})
    assert response.status_code == 201, response.text
    body = response.json()

    assets = client.get(f"/api/v1/projects/{project_id}/assets").json()
    master = next(a for a in assets if a["id"] == registry["master"])
    assert master["entity_id"] == "阿德"

    artifact = client.app.state.session_factory().get(AgentArtifact, body["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "ScriptBreakdown"
    assert artifact.payload_json["beats"][0]["characters"] == ["阿德"]
    assert artifact.payload_json["beats"][0]["dialogue"][0]["speaker_id"] == "阿德"
    # The earlier breakdown stays readable; the new one supersedes it.
    assert client.app.state.session_factory().get(AgentArtifact, breakdown_id) is not None


def test_character_binding_apply_refuses_a_name_that_already_belongs_to_another_master(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    other = upload_asset(client, project_id, content=PNG + b"-chen", filename="Chen.png",
                         role="character", entity_id="Chen")
    approve_lock(client, other["id"])
    client.app.state.mock_llm_responder = _responder(registry, characters=("S1",))

    client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    breakdown_id = client.post(
        f"/api/v1/projects/{project_id}/storyboard/character-binding/proposal", json={}
    ).json()["breakdown_artifact_id"]

    response = client.post(f"/api/v1/projects/{project_id}/storyboard/character-binding/apply",
                           json={"breakdown_artifact_id": breakdown_id,
                                 "bindings": [{"script_name": "S1", "asset_id": registry["master"],
                                               "name": "Chen"}]})
    assert response.status_code == 409
    assert "CHARACTER_NAME_ALREADY_IN_USE" in response.text


def test_storyboard_reports_the_failed_role_and_can_resume_from_it(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    client.app.state.mock_llm_responder = _responder(registry, fail_director=True)

    failed = client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                         json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["status"] == "REJECTED"
    assert body["failed_role"] == "director"
    assert set(body["steps"]) == {"scriptwriter"}
    assert body["resume_steps"]["scriptwriter"] == body["steps"]["scriptwriter"]["artifact_id"]

    # The director works now.  The chain continues from the failed role and
    # the scriptwriter is not called again.
    calls: list[str] = []
    responder = _responder(registry)

    def counting(messages, schema):
        calls.append(schema.get("title"))
        return responder(messages, schema)

    client.app.state.mock_llm_responder = counting
    resumed = client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                          json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS,
                                "start_role": "director",
                                "resume_steps": {"scriptwriter": body["resume_steps"]["scriptwriter"]}})
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["status"] == "PROPOSED"
    assert "ScriptBreakdown" not in calls
    assert "DirectorialPlan" in calls


def test_storyboard_refuses_a_resume_without_the_earlier_artifact(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    client.app.state.mock_llm_responder = _responder(registry)

    response = client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                           json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS,
                                 "start_role": "director", "resume_steps": {}})
    assert response.status_code == 409
    assert "STORYBOARD_RESUME_STEP_MISSING" in response.text


def test_storyboard_leaves_a_gap_between_two_crew_calls(studio, monkeypatch):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)
    client.app.state.mock_llm_responder = _responder(registry)
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "250")

    started = time.monotonic()
    response = client.post(f"/api/v1/projects/{project_id}/storyboard/generate",
                           json={"text": BRIEF, "allow_remote_text": True, "locked_story_facts": FACTS})
    elapsed = time.monotonic() - started
    assert response.status_code == 201, response.text
    # Five crew calls leave four gaps of 250ms.
    assert elapsed >= 0.9


def test_asset_media_serves_the_uploaded_master(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, registry = _studio(client)

    response = client.get(f"/api/v1/assets/{registry['master']}/media")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/")
    assert len(response.content) > 0

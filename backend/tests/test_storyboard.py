"""The complete-script storyboard chain: five real routed roles.

scriptwriter -> director -> cinematographer -> continuity supervisor -> editor
(frozen edit intent).  Offline tests here use the project mock provider; the
real-provider acceptance run records its own evidence.  Media here is never
a character, location or prop master asset.
"""

from __future__ import annotations

import copy
import json

import pytest
from sqlalchemy import select

from shine.agents.editorial_timing import editorial_timing_preview
from shine.models import AgentArtifact, AgentTaskRecord
from tests.conftest import PNG, approve_lock, create_project, upload_asset
from tests.test_film_crew import BRIEF, FACTS, prepare

# A minimal RIFF/WAVE header: the upload verifies the file signature.
WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + b"\x10\x00\x00\x00"


def test_editorial_timing_preview_preserves_eight_editorial_shots_in_three_segments():
    cuts = [0, 2400, 4300, 6000, 7700, 9400, 11100, 12800, 15000]
    table = "模式 **Ref2VA**\n| # | 切点 | 时长 |\n|---|---|---|\n"
    for i, (start, end) in enumerate(zip(cuts[:-1], cuts[1:], strict=True), 1):
        table += f"| {i} | {start / 1000:.3f} | {(end - start) / 1000:.3f}s |\n"
    plan = editorial_timing_preview(table)
    assert plan == {
        "editorial_shot_count": 8,
        "cut_starts_s": [cut / 1000 for cut in cuts[:-1]],
        "total_s": 15.0,
        "segments": [
            {"first_shot": 1, "last_shot": 2, "duration_s": 4.3},
            {"first_shot": 3, "last_shot": 5, "duration_s": 5.1},
            {"first_shot": 6, "last_shot": 8, "duration_s": 5.6},
        ],
    }
    assert editorial_timing_preview(table.replace("4.300", "4.301")) is None


def _responder(registry, *, character_name="Lin"):
    """One responder for the whole chain, dispatched by schema title."""

    def respond(messages, schema):
        title = schema.get("title")
        if title == "ScriptBreakdown":
            return copy.deepcopy({
                "locked_story_facts": FACTS,
                "beats": [
                    {"id": "B1", "description": "The woman waits beside a sealed letter.",
                     "characters": [character_name], "dialogue": [
                         {"speaker_id": character_name, "text": "你来了。", "start_s": 1, "end_s": 2}]},
                    {"id": "B2", "description": "She turns toward the door.",
                     "characters": [character_name], "dialogue": []},
                ],
            })
        if title == "DirectorialPlan":
            return copy.deepcopy({
                "shots": [
                    {"id": "I1", "beat_id": "B1",
                     "visual_description": "A woman waits beside a sealed letter at a wooden table.",
                     "duration_s": 7, "asset_ids": [registry["lin_asset"]]},
                    {"id": "I2", "beat_id": "B2",
                     "visual_description": "She turns her head toward the door.",
                     "duration_s": 6, "asset_ids": [registry["lin_asset"]]},
                ],
            })
        if title == "CameraPlan":
            return copy.deepcopy({
                "shots": [
                    {"intent_id": "I1",
                     "composition": "Medium shot, subject left third",
                     "movement": "Static, preserved axis",
                     "lighting": "Soft key from screen right",
                     "axis": "Preserve the established axis"},
                    {"intent_id": "I2",
                     "composition": "Close-up on the head turn",
                     "movement": "Static",
                     "lighting": "Key follows the turn",
                     "axis": "Same axis, closer framing"},
                ],
            })
        if title == "ContinuityReport":
            return {"issues": [], "recommendation": "proceed_to_preflight"}
        if title == "EditIntentPlan":
            return copy.deepcopy({
                "schema_version": "1.2", "intent_id": "D1",
                "clips": [
                    {"intent_id": "I1", "order": 1, "planned_duration_s": 7.0,
                     "transition": "CUT", "continuity_note": "establishing"},
                    {"intent_id": "I2", "order": 2, "planned_duration_s": 6.0,
                     "transition": "CUT", "continuity_note": "reaction"},
                ],
                "rhythm": "steady, cut after reactions",
                "dialogue_pace": "natural Mandarin pace",
            })
        raise AssertionError(f"unexpected schema {title}")

    return respond


def _studio_with_chain(client, *, extra_character: str | None = None, character_entity_id="Lin"):
    """Frozen style + rulepack + mock routes + one approved Lin master.

    Returns (project_id, style, registry) where registry holds the asset id.
    """
    registry = {}
    filename = "Lin.png" if character_entity_id == "Lin" else f"{character_entity_id}.png"
    project_id, style, profile = prepare(
        client, character_entity_id=character_entity_id, character_filename=filename
    )
    assets = client.get(f"/api/v1/projects/{project_id}/assets").json()
    lin = next(a for a in assets if a["role"] == "character" and a["entity_id"] == character_entity_id)
    registry["lin_asset"] = lin["id"]
    if extra_character:
        extra = upload_asset(client, project_id, filename="Chen.png", role="character", entity_id=extra_character)
        approve_lock(client, extra["id"])
        registry["chen_asset"] = extra["id"]
    return project_id, style, registry


def _storyboard_request(client, project_id, **overrides):
    payload = {
        "text": BRIEF,
        "allow_remote_text": True,
        "locked_story_facts": FACTS,
        **overrides,
    }
    return client.post(f"/api/v1/projects/{project_id}/storyboard/generate", json=payload)


@pytest.mark.parametrize("duration", [4.3, 4.4])
def test_director_groups_editorial_beats_and_applies_timed_cut(studio, duration):
    client, *_ = studio
    project_id, _, registry = _studio_with_chain(client)
    original = _responder(registry)

    def respond(messages, schema):
        value = original(messages, schema)
        title = schema["title"]
        if title == "DirectorialPlan":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            assert context["editorial_timing"]["cut_starts_s"] == [0, 2.4]
            first, second = value["shots"]
            first["duration_s"] = duration
            first["cuts"] = [{"beat_id": "B2", "start_s": 2.4,
                              "visual_description": second["visual_description"]}]
            value["shots"] = [first]
        elif title == "CameraPlan":
            value["shots"] = value["shots"][:1]
        elif title == "EditIntentPlan":
            value["clips"] = value["clips"][:1]
            value["clips"][0]["planned_duration_s"] = 4.3
        return value

    client.app.state.mock_llm_responder = respond
    brief = BRIEF + "\n\n| # | 切点 | 时长 |\n|---|---|---|\n| 1 | 0.000 | 2.40s |\n| 2 | 2.400 | 1.90s |\n"
    response = _storyboard_request(client, project_id, text=brief)
    assert response.status_code == 201, response.text
    if duration != 4.3:
        assert response.json()["status"] == "REJECTED"
        assert response.json()["failed_role"] == "director"
        assert client.get(f"/api/v1/projects/{project_id}/shots").json() == []
        return
    assert response.json()["status"] == "PROPOSED", response.text
    apply = response.json()["next"]["apply_camera"]
    applied = client.post(
        f"/api/v1/projects/{project_id}/film-artifacts/{apply['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": apply["continuity_artifact_id"]},
    )
    assert applied.status_code == 200, applied.text
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert len(shots) == 1
    assert shots[0]["spec"]["duration_target_s"] == 4.3
    assert "At 00:02.400, hard cut." in shots[0]["spec"]["story_beat"]


CONFLICT_ISSUE = ("SHOT_03_CONTINUITY omits the required FRAME_A_LAST reference asset "
                  "from its asset_ids, creating an unresolved last-frame continuity mapping.")


def _with_conflict(original, *, verdict="not_a_conflict", rule_id="H3_LABELS"):
    """A crew that contradicts itself, plus the arbiter that answers it.

    The arbiter echoes the raised issue from the arbitration subject, so the
    ruling is bound to the exact issue the supervisor raised.
    """

    def respond(messages, schema):
        title = schema.get("title")
        if title == "ContinuityReport":
            return {"issues": [CONFLICT_ISSUE], "recommendation": "needs_review"}
        if title == "ContinuityArbitration":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            subject = context["arbitration_subject"]["issues"]
            return {
                "rulings": [
                    {"issue_index": issue["index"], "issue": issue["text"], "verdict": verdict,
                     "rule_id": rule_id,
                     "rationale": "The official criteria do not require this of the plan."}
                    for issue in subject
                ],
                "recommendation": "proceed_to_preflight" if verdict == "not_a_conflict" else "needs_review",
            }
        return original(messages, schema)

    return respond


def test_crew_conflict_is_arbitrated_by_the_director_on_the_official_rules(studio):
    """A contradiction between the roles is arbitrated once, not by a rerun."""
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)
    client.app.state.mock_llm_responder = _with_conflict(original)

    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED", body
    assert body["continuity_issues"] == [CONFLICT_ISSUE]
    arbitration = body["continuity_arbitration"]
    assert arbitration is not None
    assert arbitration["released"] is True
    assert arbitration["rulings"][0]["issue"] == CONFLICT_ISSUE
    assert arbitration["rulings"][0]["rule_id"] == "H3_LABELS"
    # The ruling travels with the storyboard, and the human applies it.
    apply_camera = body["next"]["apply_camera"]
    assert apply_camera["arbitration_artifact_id"] == arbitration["artifact_id"]
    applied = client.post(
        f"/api/v1/projects/{project_id}/film-artifacts/{apply_camera['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": apply_camera["continuity_artifact_id"],
              "arbitration_artifact_id": apply_camera["arbitration_artifact_id"]},
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["shot_ids"]) == 2


def test_a_standing_conflict_keeps_the_storyboard_blocked(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)
    client.app.state.mock_llm_responder = _with_conflict(original, verdict="conflict")

    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW", body
    assert body["continuity_arbitration"]["released"] is False
    apply_camera = body["next"]["apply_camera"]
    applied = client.post(
        f"/api/v1/projects/{project_id}/film-artifacts/{apply_camera['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": apply_camera["continuity_artifact_id"],
              "arbitration_artifact_id": apply_camera["arbitration_artifact_id"]},
    )
    assert applied.status_code == 422
    # The public answer stays generic; the reason is in the server log.
    assert applied.json()["detail"] == "FILM_HANDOFF_REJECTED"
    assert client.get(f"/api/v1/projects/{project_id}/shots").json() == []


def test_an_arbitration_citing_an_unknown_rule_releases_nothing(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)
    client.app.state.mock_llm_responder = _with_conflict(original, rule_id="H3_MADE_UP")

    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    # A rule nobody can verify is a contract violation of the arbiter.
    assert body["status"] == "REJECTED", body
    assert body["rejections"][0]["role"] == "director_arbitration"
    assert client.get(f"/api/v1/projects/{project_id}/shots").json() == []


def test_the_server_binds_the_authoritative_issue_text(studio):
    """A paraphrase by the arbiter can never detach a ruling from its issue."""
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)

    def respond(messages, schema):
        if schema.get("title") == "ContinuityArbitration":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            return {
                "rulings": [
                    {"issue_index": issue["index"], "issue": "a paraphrase of the issue",
                     "verdict": "not_a_conflict", "rule_id": "H3_LABELS",
                     "rationale": "The official criteria do not require this of the plan."}
                    for issue in context["arbitration_subject"]["issues"]
                ],
                "recommendation": "proceed_to_preflight",
            }
        return _with_conflict(original)(messages, schema)

    client.app.state.mock_llm_responder = respond
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED", body
    assert body["continuity_arbitration"]["rulings"][0]["issue"] == CONFLICT_ISSUE


def test_the_arbiter_is_not_called_when_the_crew_agrees(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)
    calls = []

    def respond(messages, schema):
        calls.append(schema.get("title"))
        return original(messages, schema)

    client.app.state.mock_llm_responder = respond
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED"
    assert "ContinuityArbitration" not in calls


def test_the_project_language_must_be_writable_in_the_english_prompt(studio):
    """A language the H3 prompt cannot state is refused with a clear message.

    The prompt is written in English and the official rule pack rejects CJK
    outside the dialogue tags, so the project language is normalised when it is
    saved instead of breaking the job creation later.
    """
    client, _factory, _tmp, _settings = studio
    created = client.post("/api/v1/projects", json={"title": "对白语言", "language": "中文"}).json()
    assert created["language"] == "Chinese"

    updated = client.patch(f"/api/v1/projects/{created['id']}/settings", json={"language": "粤语"}).json()
    assert updated["language"] == "Cantonese"

    refused = client.patch(f"/api/v1/projects/{created['id']}/settings", json={"language": "火星语"})
    assert refused.status_code == 422, refused.text
    assert "H3 prompt" in refused.json()["detail"]


def test_storyboard_chain_generates_all_five_artifacts(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _responder(registry)

    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    assert set(body["steps"]) == {"scriptwriter", "director", "cinematographer",
                                  "continuity_supervisor", "editor_edit_intent"}
    with factory() as db:
        steps = body["steps"]
        camera = db.get(AgentArtifact, steps["cinematographer"]["artifact_id"])
        edit = db.get(AgentArtifact, steps["editor_edit_intent"]["artifact_id"])
        assert set(camera.input_revision_ids) == {
            steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"],
        }
        assert set(edit.input_revision_ids) == {
            steps[key]["artifact_id"] for key in
            ("scriptwriter", "director", "cinematographer", "continuity_supervisor")
        }
        kinds = set(db.scalars(
            select(AgentArtifact.kind).where(AgentArtifact.project_id == project_id)
        ).all())
        assert {"ScriptBreakdown", "DirectorialPlan", "CameraPlan",
                "ContinuityReport", "EditIntentPlan"}.issubset(kinds)
        roles = set(db.scalars(
            select(AgentTaskRecord.role_id).where(AgentTaskRecord.project_id == project_id)
        ).all())
        assert {"scriptwriter", "director", "cinematographer",
                "continuity_supervisor", "editor"}.issubset(roles)
        trace = db.scalars(
            select(AgentTaskRecord.trace).where(AgentTaskRecord.project_id == project_id)
        ).all()
        # Honest model identity: the offline mock is recorded as a mock.
        assert all(row["is_mock"] is True for row in trace)


def test_storyboard_accepts_omitted_redundant_facts_and_optional_camera_details(studio):
    client, factory, _tmp_path, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)

    def minimal(messages, schema):
        result = original(messages, schema)
        if schema.get("title") == "ScriptBreakdown":
            result.pop("locked_story_facts")
        if schema.get("title") == "CameraPlan":
            for shot in result["shots"]:
                for field in ("movement", "lighting", "axis"):
                    shot.pop(field)
        return result

    client.app.state.mock_llm_responder = minimal
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201
    assert response.json()["status"] == "PROPOSED"
    with factory() as db:
        script = db.get(AgentArtifact, response.json()["steps"]["scriptwriter"]["artifact_id"])
        assert script.payload_json["locked_story_facts"] == FACTS


def test_director_is_instructed_to_include_materially_depicted_approved_props(studio):
    client, _factory, _tmp_path, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)
    instructions = []

    def capture(messages, schema):
        if schema.get("title") == "DirectorialPlan":
            instructions.append(messages[0].content)
        return original(messages, schema)

    client.app.state.mock_llm_responder = capture
    assert _storyboard_request(client, project_id).json()["status"] == "PROPOSED"
    assert "approved prop or other master materially depicted" in instructions[0]


def test_chinese_locked_fact_translation_is_shown_and_kept_with_original(studio):
    client, factory, _tmp_path, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    original = _responder(registry)

    def translate(messages, schema):
        result = original(messages, schema)
        if schema.get("title") == "ScriptBreakdown":
            result["locked_story_facts"] = ["怀表停在十二点"]
            result["prompt_story_facts"] = ["The watch is stopped at twelve."]
        return result

    client.app.state.mock_llm_responder = translate
    response = _storyboard_request(client, project_id, locked_story_facts=["怀表停在十二点"])
    assert response.status_code == 201
    body = response.json()
    assert body["prompt_story_facts"] == ["The watch is stopped at twelve."]
    with factory() as db:
        script = db.get(AgentArtifact, body["steps"]["scriptwriter"]["artifact_id"])
        assert script.payload_json["locked_story_facts"] == ["怀表停在十二点"]


def test_unknown_storyboard_result_blocks_exact_replay_before_agent_calls(studio):
    client, factory, _tmp_path, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    calls = []

    def respond(messages, schema):
        calls.append(schema.get("title"))
        return _responder(registry)(messages, schema)

    client.app.state.mock_llm_responder = respond
    original = _storyboard_request(client, project_id)
    assert original.status_code == 201, original.text
    original_body = original.json()
    editor_artifact = original_body["steps"]["editor_edit_intent"]["artifact_id"]

    with factory() as db:
        artifact = db.get(AgentArtifact, editor_artifact)
        task = db.get(AgentTaskRecord, artifact.source_task_id)
        task.status = "NEEDS_REVIEW"
        task.error_code = "LLM_TIMEOUT"
        task.trace = {
            **task.trace,
            "is_mock": False,
            "external_outcome": "UNKNOWN",
            "automatic_replay": False,
        }
        db.commit()

    calls.clear()
    repeated = _storyboard_request(client, project_id)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["rejections"][0]["code"] == "AGENT_REQUEST_RECONCILIATION_REQUIRED"
    assert calls == []

    revised = _storyboard_request(client, project_id, text=BRIEF + "\n修订：第三幕改为窗外雨停。")
    assert revised.status_code == 201, revised.text
    assert revised.json()["status"] == "PROPOSED"


def test_storyboard_blocks_on_missing_character_masters(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _responder(registry)
    # A second beat names a character without an approved master.
    original = _responder(registry)

    def with_chen(messages, schema):
        payload = original(messages, schema)
        if schema.get("title") == "ScriptBreakdown":
            payload["beats"][1]["characters"] = ["Chen"]
            payload["beats"][1]["dialogue"] = [
                {"speaker_id": "Chen", "text": "你来了。", "start_s": 1, "end_s": 2},
            ]
        return payload

    client.app.state.mock_llm_responder = with_chen
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "BLOCKED_MISSING_MASTERS"
    assert body["missing_characters"] == ["Chen"]
    # The chain stopped: no director/camera/continuity/edit artifacts exist.
    assert set(body["steps"]) == {"scriptwriter"}
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert shots == []


def test_storyboard_matches_character_view_suffix_to_approved_master(studio):
    client, _factory, _tmp_path, _settings = studio
    character_entity_id = "R01 杜万山胸上"
    project_id, _style, registry = _studio_with_chain(client, character_entity_id=character_entity_id)
    client.app.state.mock_llm_responder = _responder(registry, character_name="R01 杜万山")

    response = _storyboard_request(client, project_id)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED"
    assert set(response.json()["steps"]) == {
        "scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor_edit_intent"
    }


def test_storyboard_canonicalizes_character_asset_uuids_from_scriptwriter(studio):
    client, factory, _tmp_path, _settings = studio
    entity_id = "R01 杜万山胸上"
    project_id, _style, registry = _studio_with_chain(client, character_entity_id=entity_id)
    asset_id = registry["lin_asset"]
    client.app.state.mock_llm_responder = _responder(registry, character_name=asset_id)

    response = _storyboard_request(client, project_id)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED"
    with factory() as db:
        artifact = db.get(AgentArtifact, response.json()["steps"]["scriptwriter"]["artifact_id"])
        assert artifact.payload_json["beats"][0]["characters"] == [entity_id]
        assert artifact.payload_json["beats"][0]["dialogue"][0]["speaker_id"] == entity_id


def test_agent_contracts_describe_beat_local_dialogue_timecodes():
    from shine.agents.film_contracts import ScriptBreakdown, ShotIntent
    from shine.schemas import DialogueLine

    line_schema = DialogueLine.model_json_schema()["properties"]
    character_description = (
        ScriptBreakdown.model_json_schema()["$defs"]["SceneBeat"]["properties"]["characters"]["description"]
    )
    start_description = line_schema["start_s"]["description"]
    end_description = line_schema["end_s"]["description"]
    shot_description = ShotIntent.model_json_schema()["properties"]["duration_s"]["description"]
    beat_description = ScriptBreakdown.model_json_schema()["$defs"]["SceneBeat"]["description"]

    assert "Reset to zero for each beat" in start_description
    assert "fit inside the planned shot duration" in end_description
    assert "contain every dialogue timecode" in shot_description
    assert "local to this beat" in beat_description
    assert "Never put opaque asset UUIDs here" in character_description


def test_continuity_reviewer_receives_the_checked_reference_set_policy(studio):
    """The reviewer must see the reference-set policy as a checked fact.

    A deliberate exclusion reads exactly like an omission when the policy is
    left to interpretation, and a compliant shot was then reported as an
    unresolved reference mapping.  The server states the policy and checks
    every shot against it.
    """
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    scene = upload_asset(client, project_id, content=PNG + b"scene", filename="Street.png",
                         role="scene", entity_id="STREET")
    approve_lock(client, scene["id"])
    voice = upload_asset(client, project_id, content=WAV + b"voice", filename="Lin.wav", role="voice",
                         entity_id="VOICE_LIN", kind="audio", content_type="audio/wav")
    approve_lock(client, voice["id"])
    frame = upload_asset(client, project_id, content=PNG + b"frame", filename="A_last_frame.png",
                         role="style_reference", entity_id="FRAME_A_LAST")
    approve_lock(client, frame["id"])
    registry["scene_asset"] = scene["id"]
    registry["voice_asset"] = voice["id"]
    registry["frame_asset"] = frame["id"]

    original = _responder(registry)
    captured: dict = {}

    def respond(messages, schema):
        payload = original(messages, schema)
        title = schema.get("title")
        if title == "DirectorialPlan":
            # A compliant working reference set: character, location, voice.
            for shot in payload["shots"]:
                shot["asset_ids"] = [registry["lin_asset"], registry["scene_asset"],
                                     registry["voice_asset"]]
        if title == "ContinuityReport":
            captured["system"] = messages[0].content
            captured["context"] = json.JSONDecoder().raw_decode(messages[1].content)[0]
        return payload

    client.app.state.mock_llm_responder = respond
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PROPOSED"

    policy = captured["context"]["continuity_policy"]
    excluded = {row["id"] for row in policy["policy"]["excluded_from_shot_references"]}
    assert frame["id"] in excluded, "the project-level last-frame master is excluded by policy"
    statuses = policy["reference_set_status"]
    assert statuses and all(row["policy_compliant"] for row in statuses)
    assert all(row["missing_required_roles"] == [] for row in statuses)
    assert all(row["excluded_assets_used"] == [] for row in statuses)
    # The contract and the data must agree, or the fact is ignored again.
    assert "policy_compliant" in captured["system"]
    assert "reference_set_status" in captured["system"]
    assert "last_frame" in captured["system"]


def test_reference_set_policy_marks_a_shot_without_the_voice_master(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    scene = upload_asset(client, project_id, content=PNG + b"scene2", filename="Street.png",
                         role="scene", entity_id="STREET")
    approve_lock(client, scene["id"])
    registry["scene_asset"] = scene["id"]

    original = _responder(registry)
    captured: dict = {}

    def respond(messages, schema):
        payload = original(messages, schema)
        if schema.get("title") == "DirectorialPlan":
            for shot in payload["shots"]:
                shot["asset_ids"] = [registry["lin_asset"], registry["scene_asset"]]
        if schema.get("title") == "ContinuityReport":
            captured["context"] = json.JSONDecoder().raw_decode(messages[1].content)[0]
        return payload

    client.app.state.mock_llm_responder = respond
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    statuses = captured["context"]["continuity_policy"]["reference_set_status"]
    assert statuses and all(row["policy_compliant"] is False for row in statuses)
    assert all("voice" in row["missing_required_roles"] for row in statuses)


def test_storyboard_guards_style_routes_and_consent(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _responder(registry)

    # 1. Unfrozen style: the chain refuses to start.
    project2 = create_project(client, "未冻结风格")
    response = _storyboard_request(client, project2, text=BRIEF)
    assert response.status_code == 409
    assert "STORYBOARD_STYLE_NOT_FROZEN" in response.text

    # 2. A missing route: the chain refuses with the role named.
    from shine.models import AgentRoute
    with factory.begin() as db:
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == "cinematographer"))
        db.delete(route)
    response = _storyboard_request(client, project_id)
    assert response.status_code == 409
    assert "STORYBOARD_AGENT_ROUTE_MISSING:cinematographer" in response.text

    # 3. A non-loopback provider without consent: 403, no call goes out.
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "remote-mock? no remote", "kind": "openai_compatible",
        "base_url": "https://api.minimax.cn/v1", "model": "MiniMax-M3.1-flash",
    })
    assert profile.status_code == 201, profile.text
    route = client.put("/api/v1/agent-routes", json={
        "role": "cinematographer", "provider_profile_id": profile.json()["id"],
    })
    assert route.status_code == 200
    response = _storyboard_request(client, project_id, allow_remote_text=False)
    assert response.status_code == 403
    assert "REMOTE_AGENT_CONSENT_REQUIRED" in response.text


def test_edit_intent_tool_permissions_and_coverage(studio):
    client, factory, tmp_path, settings = studio
    project_id, _style, registry = _studio_with_chain(client)

    from shine.agents.film_contracts import FilmAgentRegistry as Registry

    with pytest.raises(ValueError, match="ROLE_OR_TOOL_DENIED"):
        Registry.get("director", "edit_intent")

    client.app.state.mock_llm_responder = _responder(registry)
    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    edit_intent = body["steps"]["editor_edit_intent"]

    with factory() as db:
        artifact = db.get(AgentArtifact, edit_intent["artifact_id"])
        assert artifact.kind == "EditIntentPlan"
        orders = [clip["order"] for clip in artifact.payload_json["clips"]]
        assert orders == [1, 2]


def test_series_accepts_frozen_edit_intent_and_rejects_invalid(studio):
    client, factory, tmp_path, settings = studio
    project_id, style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _responder(registry)

    response = _storyboard_request(client, project_id)
    assert response.status_code == 201, response.text
    body = response.json()
    edit_intent_id = body["next"]["apply_camera"]["edit_intent_artifact_id"]

    apply_response = client.post(
        f"/api/v1/projects/{project_id}/film-artifacts/{body['next']['apply_camera']['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": body["next"]["apply_camera"]["continuity_artifact_id"]},
    )
    assert apply_response.status_code == 200, apply_response.text
    shot_ids = apply_response.json()["shot_ids"]
    assert len(shot_ids) == 2
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert len(shots) == 2

    from tests.conftest import register_workflow_profile
    profile = register_workflow_profile(client, mode="Ref2VA")
    series = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": shot_ids,
        "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "edit_intent_artifact_id": edit_intent_id,
    })
    assert series.status_code == 201, series.text
    assert series.json()["edit_intent_artifact_id"] == edit_intent_id

    bad = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shots[0]["id"]],
        "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "edit_intent_artifact_id": "00000000-0000-0000-0000-000000000000",
    })
    assert bad.status_code == 409 or bad.status_code == 422
    assert "EDIT_INTENT_ARTIFACT_INVALID" in bad.text or "ALREADY_QUEUED" in bad.text

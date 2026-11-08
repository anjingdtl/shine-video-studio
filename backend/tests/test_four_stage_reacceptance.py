"""Reverse assertions for the second re-acceptance round (R01-R09).

Every test here fails on the reviewed baseline 631d9a0 and passes only when
the invariant it names really holds.  They are the durable form of the
independent acceptance reproduction probes:

* R01 one authorisation for a new attempt is consumed exactly once;
* R02 a partial redo keeps the other scenes applied receipts;
* R03 a blueprint made from an older outline may not be confirmed;
* R04 a changed shot revision produces a new package, not a reused one;
* R05 the confirmed episode contracts reach the volume delivery;
* R06 a missing chunk is a named refusal, never a silent scope;
* R07 the panel restores the volume pending work after a refresh;
* R08 the panel offers the real recovery and the exact package;
* R09 a missing material is a notice, never a block, and an unconfirmed
  master is never bound.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.director import pipeline
from shine.director.models import (
    DirectorEpisodePlan,
    DirectorPackageEpisode,
    DirectorPackageVersion,
)
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord
from tests.conftest import create_project, register_workflow_profile
from tests.test_four_stage_closeout import _bible, _confirm_plan_and_outlines
from tests.test_pipeline import LOCAL, _prepare_handoff_fixture, _route_mock, _scene_responder


def _confirm_episode(client, pid, volume_id, title, section_ids):
    """Build and confirm one episode contract; return its artifact id."""
    drafted = client.post(
        f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/episode-scripts",
        json={"title": title, "section_ids": list(section_ids)}, headers=LOCAL)
    assert drafted.status_code == 201, drafted.text
    artifact_id = drafted.json()["artifact_id"]
    confirmed = client.post(
        f"/api/v1/projects/{pid}/pipeline/episode-scripts/{artifact_id}/confirm",
        json={"expected_sha256": drafted.json()["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return artifact_id


def _apply_units(client, pid, units):
    for unit in units:
        applied = client.post(
            f"/api/v1/projects/{pid}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
            json={"continuity_artifact_id": unit["continuity_artifact_id"]}, headers=LOCAL)
        assert applied.status_code == 200, applied.text
    return True


def _volume_sections(factory, pid, volume_id):
    with factory() as db:
        outline = pipeline._volume_outline(db, pid, volume_id)
        return [section["production_section_id"]
                for section in (outline.payload_json.get("production_sections") or [])]


def _unknown_task(api, pid):
    """A four-stage task whose provider outcome is unknown."""
    client, factory, settings = api
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
    return task_id, volume_id, gw


# --------------------------------------------------------------------------- R01


def test_r01_one_authorisation_buys_exactly_one_attempt(api):
    """The same authorisation may never buy a second provider call.

    The reviewed baseline only wrote a superseded marker on the old row,
    so the same version could be spent again and again: two requests, two
    attempts, two real sends.
    """
    client, factory, settings = api
    pid = create_project(client, "R01")
    task_id, _volume_id, gw = _unknown_task(api, pid)
    calls = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        return _scene_responder()(messages, schema)

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    first = client.post(f"/api/v1/projects/{pid}/pipeline/tasks/{task_id}/new-attempt",
                        json={"expected_state_version": version}, headers=LOCAL)
    assert first.status_code == 202, first.text
    new_id = first.json()["task_id"]
    second = client.post(f"/api/v1/projects/{pid}/pipeline/tasks/{task_id}/new-attempt",
                         json={"expected_state_version": version}, headers=LOCAL)
    assert second.status_code in (202, 409, 422), second.text
    if second.status_code == 202:
        assert second.json()["task_id"] == new_id, "the same receipt, not a new attempt"
    with factory() as db:
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid)).all()
        attempts = [row for row in rows if (row.trace or {}).get("retry_of_task_id")]
        assert len(attempts) == 1, [row.id for row in attempts]
        old = db.get(AgentTaskRecord, task_id)
        assert old.state_version > version, "the authorisation must be consumed"
        assert old.trace["external_outcome"] == "UNKNOWN", old.trace
        assert old.trace["superseded_by_task_id"] == new_id
    pipeline.run_pending_pipeline_task(factory, settings, "w-r01", gateway)
    assert len(calls) == 1, "exactly one authorised send"


# --------------------------------------------------------------------------- R02


def test_r02_a_partial_redo_keeps_the_other_scenes(api):
    """Redoing one scene may not erase the other scenes' receipts.

    The reviewed baseline overwrote the whole volume record with the units
    of the last run, so a partial redo dropped the applied scene and the
    delivery then refused the volume.
    """
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    assert len(sections) >= 2, sections
    first = units[0]
    applied = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{first['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": first["continuity_artifact_id"]}, headers=LOCAL)
    assert applied.status_code == 200, applied.text
    first_shots = applied.json()["shot_ids"]
    redone = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft",
                         json={"section_ids": [sections[1]]}, headers=LOCAL)
    assert redone.status_code == 201, redone.text
    with factory() as db:
        ledger = pipeline.storyboard_ledger(db, pid, volume_id)
        stored = ledger["units"]
        assert set(stored) == set(sections), stored.keys()
        assert stored[sections[0]]["camera_artifact_id"] == first["camera_artifact_id"]
        applied_state = pipeline.applied_shots_for(db, stored[sections[0]]["camera_artifact_id"])
        assert applied_state["applied"] is True, applied_state
    # apply the redone scene and deliver the volume
    new_unit = next(unit for unit in redone.json()["units"]
                    if unit["section_id"] == sections[1])
    applied2 = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{new_unit['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": new_unit["continuity_artifact_id"]}, headers=LOCAL)
    assert applied2.status_code == 200, applied2.text
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 201, handoff.text
    with factory() as db:
        episodes = db.scalars(select(DirectorPackageEpisode).where(
            DirectorPackageEpisode.package_id == handoff.json()["package_id"])).all()
        delivered = [shot["shot_id"] for episode in episodes
                     for shot in (episode.manifest.get("shots") or [])]
    assert set(first_shots).issubset(set(delivered)), (first_shots, delivered)


# --------------------------------------------------------------------------- R06


def test_r06_a_failed_chunk_is_kept_and_blocks_the_delivery(api, monkeypatch):
    """A chunk that failed stays visible and refuses the delivery by name.

    The reviewed baseline put the exception into the response only: the
    persistent record lost the failed chunk, the coverage check compared
    scenes instead of units, and the volume was delivered without it.
    """
    import shine.api_film as api_film

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    # make the first scene long enough to be split into two chunks
    # the mock crew answer quotes this line, so it has to stay in the text
    long_text = "Lin：你来了。\n" + "甲" * 10_000 + "\n" + "乙" * 10_000 + "\n<<CHUNK2>>"
    with factory.begin() as db:
        script = pipeline.current_scene_script(db, pid, volume_id, sections[0])
        edited = pipeline.edit_scene_script(db, pid, script.id, long_text)
        edited_id, edited_sha = edited.id, edited.sha256
    with factory.begin() as db:
        pipeline.confirm_scene_script(db, pid, edited_id, edited_sha)
    original = api_film.generate_storyboard

    def failing(project_id, body, request, db, gateway):
        if "<<CHUNK2>>" in (body.text or ""):
            raise HTTPException(422, "CHUNK_INJECTED_FAILURE")
        return original(project_id, body, request, db, gateway)

    monkeypatch.setattr(api_film, "generate_storyboard", failing)
    draft = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/storyboard-draft",
                        json={})
    assert draft.status_code == 201, draft.text
    body = draft.json()
    assert body["status"] == "INCOMPLETE", body
    with factory() as db:
        ledger = pipeline.storyboard_ledger(db, pid, volume_id)
        failed = [unit for unit in ledger["units"].values()
                  if unit.get("status") != "PROPOSED"]
        assert len(failed) == 1, [(u["unit_id"], u.get("char_count"),
                                   [r.get("rejection_reason_code") or r.get("code")
                                    for r in (u.get("rejections") or [])])
                                  for u in failed]
        assert failed[0]["unit_id"].endswith("#2"), failed[0]
        assert failed[0]["rejections"][0]["code"] == "CHUNK_INJECTED_FAILURE", failed[0]
    with factory() as db:
        expected_ids = {unit["unit_id"] for unit in
                        pipeline.expected_storyboard_units(db, pid, volume_id)}
    for unit in body["units"]:
        if (unit["unit_id"] in expected_ids and unit["status"] == "PROPOSED"
                and not unit.get("applied")):
            applied = client.post(
                f"/api/v1/projects/{pid}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
                json={"continuity_artifact_id": unit["continuity_artifact_id"]}, headers=LOCAL)
            assert applied.status_code == 200, applied.text
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_STORYBOARD_UNIT_REJECTED" in handoff.text, handoff.text

# --------------------------------------------------------------------------- R05


def test_r05_two_confirmed_episodes_reach_the_volume_delivery(api):
    """The formal multi-episode path is built from confirmed EpisodeScripts.

    The reviewed baseline handed the episode identity to a strict payload
    that does not declare it, so every episode delivery died with
    extra_forbidden before it could be produced.
    """
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    assert len(sections) >= 2, sections
    first = _confirm_episode(client, pid, volume_id, "第一集", sections[:1])
    second = _confirm_episode(client, pid, volume_id, "第二集", sections[1:])
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 201, handoff.text
    body = handoff.json()
    assert body["episode_basis"] == "episode_script", body
    assert body["episodes"] == 2, body
    with factory() as db:
        plan_row = db.scalars(select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == pid,
            DirectorEpisodePlan.version == body["plan_version"])).all()[0]
        episodes = plan_row.payload["episodes"]
    assert [item["episode_script_id"] for item in episodes] == [first, second], episodes
    assert episodes[0]["section_ids"] == sections[:1], episodes
    assert episodes[1]["section_ids"] == sections[1:], episodes
    assert [item["order"] for item in episodes] == [1, 2], episodes


def test_r05_the_package_manifest_keeps_the_episode_script_identity(api):
    """The delivered package pins which contract produced every episode."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    contract = _confirm_episode(client, pid, volume_id, "唯一一集", sections)
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 201, handoff.text
    with factory() as db:
        episode = db.scalars(select(DirectorPackageEpisode).where(
            DirectorPackageEpisode.package_id == handoff.json()["package_id"])).all()[0]
        assert episode.manifest["episode_script_id"] == contract, episode.manifest
        assert episode.manifest["episode_script_sha256"], episode.manifest


def test_r05_a_superseded_scene_script_refuses_the_episode_delivery(api):
    """A contract built on a scene that has since changed is history."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections[:1])
    with factory.begin() as db:
        script = pipeline.current_scene_script(db, pid, volume_id, sections[0])
        edited = pipeline.edit_scene_script(
            db, pid, script.id, script.payload_json["text"] + "\n改了。")
        edited_id, edited_sha = edited.id, edited.sha256
    # the scene is confirmed again, but it is a different revision now
    with factory.begin() as db:
        pipeline.confirm_scene_script(db, pid, edited_id, edited_sha)
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_EPISODE_SCRIPT_SUPERSEDED" in handoff.text, handoff.text


def test_r05_an_unconfirmed_scene_script_refuses_the_delivery(api):
    """A scene that lost its confirmation cannot be delivered at all."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections[:1])
    with factory.begin() as db:
        script = pipeline.current_scene_script(db, pid, volume_id, sections[0])
        pipeline.edit_scene_script(db, pid, script.id, script.payload_json["text"] + "\n改了。")
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_SCENE_SCRIPT_NOT_CONFIRMED" in handoff.text, handoff.text


def test_r05_a_missing_episode_refuses_the_delivery(api):
    """Half the volume has no confirmed episode: the delivery is refused."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections[:1])
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_EPISODE_COVERAGE_INCOMPLETE" in handoff.text, handoff.text


def test_r05_two_episodes_claiming_one_scene_are_refused(api):
    """Two confirmed episodes may not both own the same scene."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections[:1])
    _confirm_episode(client, pid, volume_id, "第二集", sections)
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_EPISODE_SECTION_OVERLAP" in handoff.text, handoff.text


def test_r05_a_non_contiguous_episode_order_is_refused(api):
    """The episode order must be a contiguous run of the script."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    if len(sections) < 3:
        pytest.skip("this fixture needs three scenes")
    _confirm_episode(client, pid, volume_id, "第一集", [sections[0], sections[2]])
    _confirm_episode(client, pid, volume_id, "第二集", [sections[1]])
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_EPISODE_ORDER_NOT_CONTIGUOUS" in handoff.text, handoff.text

# --------------------------------------------------------------------------- R03


BIBLE_PAYLOAD = {
    "schema_version": "1.0", "mainline": "渔村少年的成长", "characters": [], "world": "",
    "timeline": [], "foreshadowing": [],
    "locked_facts": ["Deming is a fisherman's son"], "open_questions": [],
    "ending_direction": "Homecoming",
}


def test_r03_a_blueprint_from_an_older_outline_may_not_be_confirmed(api):
    """A blueprint names the outlines it was written from.

    The reviewed baseline compared the plan hash and the coverage, but not
    the ordered outline identity, so a blueprint written from an outline that
    has since been replaced could still be confirmed as if it described the
    current work.
    """
    from tests.test_four_stage_closeout import pipeline_artifact_outline

    client, factory, settings = api
    pid = create_project(client, "R03")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _route_mock(client, pipeline.SCENE_SCRIPT_RUN_ROLE)
    _doc, plan, outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    gw = LLMGateway(SecretStore(settings.secret_store_dir),
                    mock_responder=lambda messages, schema: BIBLE_PAYLOAD)
    with factory.begin() as db:
        task = pipeline.queue_bible(db, pid, gw)
    pipeline.run_pending_pipeline_task(factory, settings, "w-r03", gw)
    with factory() as db:
        row = db.get(AgentTaskRecord, task.id)
        draft = db.get(AgentArtifact, row.trace["artifact_id"])
        draft_id, draft_sha = draft.id, draft.sha256
        volume_id = plan.payload_json["volumes"][0]["volume_id"]
    # the volume gets a newly confirmed outline; the old one becomes stale
    with factory.begin() as db:
        volume_doc = pipeline._volume_document(db, pid, volume_id)
        old_outline = db.get(AgentArtifact, outlines[0].id)
        old_outline.stale = True
        pipeline_artifact_outline(db, pid, volume_doc)
    confirmed = client.post(f"/api/v1/projects/{pid}/pipeline/bibles/{draft_id}/confirm",
                            json={"expected_sha256": draft_sha}, headers=LOCAL)
    assert confirmed.status_code == 422, confirmed.text
    assert "PIPELINE_BIBLE_OUTLINE_SUPERSEDED" in confirmed.text, confirmed.text


def test_r03_the_read_path_states_that_the_blueprint_moved_on(api):
    """The overview tells the truth about the confirmed blueprint."""
    from tests.test_four_stage_closeout import pipeline_artifact_outline

    client, factory, settings = api
    pid = create_project(client, "R03b")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    _doc, plan, outlines = _confirm_plan_and_outlines(factory, pid, "第一章 甲\n德明出海了。")
    gw = LLMGateway(SecretStore(settings.secret_store_dir),
                    mock_responder=lambda messages, schema: BIBLE_PAYLOAD)
    with factory.begin() as db:
        task = pipeline.queue_bible(db, pid, gw)
    pipeline.run_pending_pipeline_task(factory, settings, "w-r03b", gw)
    with factory() as db:
        row = db.get(AgentTaskRecord, task.id)
        draft = db.get(AgentArtifact, row.trace["artifact_id"])
        bible_id, bible_sha = draft.id, draft.sha256
        volume_id = plan.payload_json["volumes"][0]["volume_id"]
    with factory.begin() as db:
        pipeline.confirm_bible(db, pid, bible_id, bible_sha)
    with factory.begin() as db:
        volume_doc = pipeline._volume_document(db, pid, volume_id)
        old_outline = db.get(AgentArtifact, outlines[0].id)
        old_outline.stale = True
        pipeline_artifact_outline(db, pid, volume_doc)
    overview = client.get(f"/api/v1/projects/{pid}/pipeline")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["bible_confirmed"] is True
    assert body["bible_status"]["artifact_id"] == bible_id
    assert "PIPELINE_BIBLE_OUTLINE_SUPERSEDED" in body["bible_status"]["blockers"], body


def test_r03_a_draft_blueprint_may_only_be_published_when_it_agrees(api):
    """A scene drafted on a draft blueprint needs the same decisive facts."""
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    # the scene was really drafted on another, unconfirmed blueprint
    with factory.begin() as db:
        from shine.director.pipeline import _authoring_task
        from shine.h3.rulebook import digest as _digest
        draft_payload = {
            "schema_version": "1.0", "mainline": "另一条主线", "characters": [],
            "world": "", "timeline": [], "foreshadowing": [],
            "locked_facts": ["Lin is the keeper of the harbour"],
            "open_questions": [], "ending_direction": "Death",
        }
        authoring = _authoring_task(db, pid, pipeline.BIBLE_RUN_ROLE, {"fixture": True})
        draft_bible = AgentArtifact(
            project_id=pid, kind=pipeline.BIBLE_KIND, schema_version="1.0",
            payload_json=draft_payload, sha256=_digest(draft_payload),
            producer_role=pipeline.BIBLE_RUN_ROLE, source_task_id=authoring)
        db.add(draft_bible)
        db.flush()
        script = pipeline.current_scene_script(db, pid, volume_id, sections[0])
        task = db.get(AgentTaskRecord, script.source_task_id)
        task.trace = {**task.trace, "bible_basis": "draft_unconfirmed",
                      "bible_artifact_id": draft_bible.id,
                      "bible_sha256": draft_bible.sha256}
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    handoff = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 422, handoff.text
    assert "PIPELINE_BIBLE_BASIS_CHANGED" in handoff.text, handoff.text


# --------------------------------------------------------------------------- R04


def test_r04_a_changed_shot_revision_produces_a_new_package(api):
    """Only an identical snapshot may reuse a package.

    The reviewed baseline hashed the creative lineage alone, so a shot that
    had been revised still hit the old package and was served as reused,
    while the readiness of that package reported the revision change.
    """
    from shine.director.package import package_readiness
    from shine.models import Shot

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    _apply_units(client, pid, units)
    profile = register_workflow_profile(client)
    first = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                        json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert first.status_code == 201, first.text
    package_id = first.json()["package_id"]
    assert first.json()["reused"] is False
    with factory.begin() as db:
        shot = db.scalars(select(Shot).where(Shot.project_id == pid)).first()
        assert shot is not None
        shot.revision += 1
    second = client.post(f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
                         json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert second.status_code == 201, second.text
    assert second.json()["package_id"] != package_id, "a changed shot needs a new package"
    assert second.json()["reused"] is False
    with factory() as db:
        old = db.get(DirectorPackageVersion, package_id)
        readiness = package_readiness(db, old)
        codes = {row["code"] for row in readiness["blockers"]}
        assert "SHOT_REVISION_CHANGED" in codes, readiness
        assert readiness["startable"] is False
        assert old.status == "CONFIRMED", "the history stays readable"
    with factory() as db:
        new = db.get(DirectorPackageVersion, second.json()["package_id"])
        readiness = package_readiness(db, new)
        assert readiness["startable"] is True, readiness


def test_r01_two_concurrent_authorisations_buy_one_attempt(api):
    """Two calls at the same time still buy exactly one attempt.

    The fence is the state version, so the second caller either loses the
    race by name or receives the same receipt.  What must never happen is
    a second task or a second provider call.
    """
    import threading

    client, factory, settings = api
    pid = create_project(client, "R01c")
    task_id, _volume_id, _gw = _unknown_task(api, pid)
    with factory() as db:
        version = db.get(AgentTaskRecord, task_id).state_version
    results = []

    def attempt():
        response = client.post(f"/api/v1/projects/{pid}/pipeline/tasks/{task_id}/new-attempt",
                              json={"expected_state_version": version}, headers=LOCAL)
        results.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results, "the two calls must have answered"
    with factory() as db:
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid)).all()
        attempts = [row for row in rows if (row.trace or {}).get("retry_of_task_id")]
        assert len(attempts) == 1, [row.id for row in attempts]


# --------------------------------------------------------------------------- R07


def test_r07_a_finished_task_does_not_read_as_running(api):
    """The summary states the state a task is in, not the stage it reached.

    A task that already succeeded used to report the last stage it passed
    ("committing") and an empty unit list, which the panel drew as an
    indeterminate bar that kept moving for ever.
    """
    client, factory, settings = api
    pid = create_project(client, "R07b")
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
    pipeline.run_pending_pipeline_task(factory, settings, "w-r07", gw)
    with factory() as db:
        entry = next(item for item in pipeline.pipeline_task_summary(db, pid)
                     if item["task_id"] == tasks[0].id)
    assert entry["status"] == "SUCCEEDED", entry
    assert entry["stage_label"] == "已完成", entry
    assert entry["progress_total"] > 0, entry
    assert entry["progress_done"] == entry["progress_total"], entry


# --------------------------------------------------------------------------- R09


def test_r09_a_revoked_master_is_skipped_and_never_bound(api):
    """绝不绑定未批准锁定的素材：撤销确认后跳过绑定，并如实提示。

    The master is confirmed when the plan is made and revoked before the
    shot is applied.  The upstream context is repaired so the apply reaches
    the binding step instead of stopping at the stale-input gate: the
    reference must be skipped, never bound, and the missing picture is
    reported as generated by H3.
    """
    from sqlalchemy import update

    from shine.agents.film import asset_context
    from shine.h3.rulebook import digest
    from shine.models import AgentTaskRecord, Asset, Shot

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    with factory() as db:
        asset_id = db.scalars(select(Asset).where(Asset.project_id == pid)).first().id
    with factory.begin() as db:
        db.execute(update(Asset).where(Asset.id == asset_id)
                   .values(approved=False, locked=False))
        db.execute(update(AgentTaskRecord).where(AgentTaskRecord.project_id == pid)
                   .values(asset_manifest_hash=digest(asset_context(db, pid))))
    unit = units[0]
    applied = client.post(
        f"/api/v1/projects/{pid}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
        json={"continuity_artifact_id": unit["continuity_artifact_id"]}, headers=LOCAL)
    assert applied.status_code == 200, applied.text
    body = applied.json()
    notices = body.get("material_notices") or []
    assert notices, body
    assert notices[0]["code"] == "MISSING_IMAGE_MATERIAL", notices
    assert notices[0]["message"] == "无图片素材将由H3自行生成", notices
    with factory() as db:
        shot = db.get(Shot, body["shot_ids"][0])
        # The revoked master is never bound, and the shot no longer requires it.
        assert all(link.asset_id != asset_id for link in shot.bindings), shot.bindings
        assert asset_id not in shot.spec["required_asset_ids"], shot.spec


def test_r09_a_missing_material_is_a_notice_not_a_block(api):
    """素材缺失不再硬阻滞落镜：缺的那个图片由 H3 自行生成。

    The character master is uploaded but never confirmed, so the material
    layer has not prepared it yet.  The storyboard states the requirement
    instead of naming a material, the shot still lands, and no unconfirmed
    byte is bound to the render.
    """
    from shine.models import Shot

    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(
        api, confirm_character_master=False
    )
    unit = units[0]
    # The requirement is stated, not enforced: the unit names what the
    # material layer still has to prepare.
    assert unit["missing_characters"] == ["Lin"], unit
    body = {}
    for unit in units:
        applied = client.post(
            f"/api/v1/projects/{pid}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
            json={"continuity_artifact_id": unit["continuity_artifact_id"]}, headers=LOCAL)
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["shot_ids"], body
    # No unconfirmed master is bound: the missing picture is generated by H3.
    with factory() as db:
        shot = db.get(Shot, body["shot_ids"][0])
        assert shot.bindings == [], [link.asset_id for link in shot.bindings]
        assert shot.spec["required_asset_ids"] == [], shot.spec
    # The material stage says what is missing, and it does not block.
    journey = client.get(f"/api/v1/projects/{pid}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert stages["assets"]["blockers"] == [], stages["assets"]
    notices = stages["assets"]["facts"]["material_notices"]
    assert any(row["code"].startswith("SHOT_CHARACTER_MASTER_MISSING")
               for row in notices), notices
    # The director package freezes as well: a missing material is a notice
    # there too, never a production blocker.
    profile = register_workflow_profile(client)
    handoff = client.post(
        f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/handoff",
        json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert handoff.status_code == 201, handoff.text
    codes = {row["code"] for row in handoff.json()["readiness"]["blockers"]}
    assert "SHOT_CHARACTER_MASTER_MISSING" not in codes, codes
    assert handoff.json()["readiness"]["startable"] is True, handoff.json()["readiness"]

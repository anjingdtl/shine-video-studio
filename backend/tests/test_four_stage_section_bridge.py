"""The four-stage storyboard ledger must reach the episode plan and the basis.

The director's ③ stage materialises a script-first plan through
``section_storyboard_shot_ids``; the four-stage draft writes its own per-unit
ledger, not ``story_section_run`` rows.  Before the bridge, applying every
four-stage unit still left ③ permanently incomplete (SECTION_RUN_MISSING)
and the applied shots had no script lineage.
"""
from __future__ import annotations

from shine.director import pipeline
from shine.director.basis import shot_script_evidence
from shine.materialprep.story import section_storyboard_shot_ids
from tests.test_four_stage_reacceptance import _apply_units, _volume_sections
from tests.test_pipeline import LOCAL, _prepare_handoff_fixture


def _sections_plan(client, pid):
    created = client.post(f"/api/v1/projects/{pid}/episode-plans",
                          json={"target_seconds": 600, "tolerance_seconds": 120,
                                "mode": "sections"}, headers=LOCAL)
    assert created.status_code == 201, created.text
    return created.json()


def test_four_stage_applies_materialize_the_episode_plan(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    plan_row = _sections_plan(client, pid)
    version = plan_row["version"]

    before = client.post(f"/api/v1/projects/{pid}/episode-plans/{version}/materialize",
                         headers=LOCAL)
    assert before.status_code == 409, before.text
    assert "EPISODE_SECTION_NOT_STORYBOARDED" in before.text

    _apply_units(client, pid, units)
    with factory() as db:
        for section_id in sections:
            resolved = section_storyboard_shot_ids(db, pid, section_id)
            assert resolved["shot_ids"], (section_id, resolved)
            assert not resolved["missing"], resolved

    after = client.post(f"/api/v1/projects/{pid}/episode-plans/{version}/materialize",
                        headers=LOCAL)
    assert after.status_code == 201, after.text
    body = after.json()
    assert all(episode["shot_ids"] for episode in body["payload"]["episodes"])
    confirmed = client.post(
        f"/api/v1/projects/{pid}/episode-plans/{body['version']}/confirm",
        json={"expected_sha256": body["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    journey = client.get(f"/api/v1/projects/{pid}/journey", headers=LOCAL)
    assert journey.status_code == 200, journey.text
    stages = {stage["id"]: stage for stage in journey.json()["stages"]}
    assert stages["storyboard"]["done"] is True, stages["storyboard"]
    assert stages["assets"]["facts"]["material_shot_count"] > 0


def test_four_stage_shots_keep_their_script_lineage(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    _apply_units(client, pid, units)
    with factory() as db:
        applied_ids = []
        for unit in units:
            applied_ids.extend(pipeline.applied_shots_for(
                db, unit["camera_artifact_id"])["shot_ids"])
        assert applied_ids
        evidence = shot_script_evidence(db, pid, applied_ids)
    assert not evidence["unknown"], evidence
    assert not evidence["ambiguous"], evidence
    assert len(set(evidence["outlines"].values())) == 1, evidence


def test_an_unapplied_four_stage_unit_stays_missing(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _apply_units(client, pid, units[:1])
    with factory() as db:
        first = section_storyboard_shot_ids(db, pid, sections[0])
        rest = section_storyboard_shot_ids(db, pid, sections[1])
    assert first["shot_ids"] and not first["missing"]
    assert not rest["shot_ids"]
    assert rest["missing"] and rest["missing"][0]["reason"] == "STORYBOARD_NOT_APPLIED"
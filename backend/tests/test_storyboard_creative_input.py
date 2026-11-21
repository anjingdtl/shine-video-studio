"""P0-A: the storyboard creative input prefers a confirmed scene script only when
its lineage provably matches the current master document and section span; every
other case falls back to the raw source.  Read-only, no second task/table.
"""
from __future__ import annotations

import hashlib

import pytest

from shine.director.streamline import select_unit_creative_input
from shine.h3.rulebook import digest
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, ProjectDocument, utcnow
from tests.conftest import create_project

pytestmark = pytest.mark.integration

SECTION_TEXT = "她扶住椅背，缓缓起身。"
SCRIPT_TEXT = "她扶住椅背，缓缓起身，望向门口。"


def _build(factory, project_id, *, confirmed=True, master_match=True, stale=False):
    with factory.begin() as db:
        master = ProjectDocument(
            project_id=project_id, filename="master.txt", content_type="text/plain",
            sha256=hashlib.sha256(SECTION_TEXT.encode()).hexdigest(),
            source_format="import", text=SECTION_TEXT,
        )
        db.add(master)
        db.flush()
        volume = ProjectDocument(
            project_id=project_id, filename="volume.txt", content_type="text/plain",
            sha256=hashlib.sha256(SECTION_TEXT.encode()).hexdigest(),
            source_format="volume", text=SECTION_TEXT,
            parsed={"kind": "four_stage_volume", "volume_id": "v1",
                    "master_document_id": master.id,
                    "master_sha256": master.sha256 if master_match else "0" * 64,
                    "start_offset": 0, "end_offset": len(SECTION_TEXT)},
        )
        db.add(volume)
        db.flush()
        task = AgentTaskRecord(
            project_id=project_id, role_id="scene_script_run", input_hash="a" * 64,
            asset_manifest_hash="a" * 64, rulepack_id="r", rulepack_hash="b" * 64,
            model_profile_id="human", model_id="human", status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        payload = {"section_id": "sec-1", "title": "起身", "text": SCRIPT_TEXT,
                   "source_refs": {"volume_id": "v1", "volume_document_id": volume.id,
                                   "start_offset": 0, "end_offset": len(SECTION_TEXT),
                                   "outline_sha256": "c" * 64}}
        script = AgentArtifact(
            project_id=project_id, kind="FourStageSceneScript", payload_json=payload,
            sha256=digest(payload), producer_role="scriptwriter", source_task_id=task.id,
            stale=stale, approved_by="local-user" if confirmed else None,
            approved_at=utcnow() if confirmed else None,
        )
        db.add(script)
        db.flush()
        if confirmed:
            db.add(AppSetting(key=f"four-stage-pointer:{project_id}:scene-script:v1:sec-1",
                              value={"artifact_id": script.id, "sha256": script.sha256}))
        section = {"production_section_id": "sec-1", "order": 1, "title": "起身",
                   "start_offset": 0, "end_offset": len(SECTION_TEXT)}
        unit = {"production_section_id": "sec-1", "start_offset": 0, "end_offset": len(SECTION_TEXT)}
        return master.id, section, unit, script.id, script.sha256


def _select(factory, project_id, master_id, section, unit):
    with factory() as db:
        master = db.get(ProjectDocument, master_id)
        return select_unit_creative_input(db, project_id, document=master, section=section, unit=unit)


def test_confirmed_scene_script_is_preferred(api):
    client, factory, _ = api
    project = create_project(client, "创作输入测试")
    master_id, section, unit, script_id, script_sha = _build(factory, project)
    result = _select(factory, project, master_id, section, unit)
    assert result["source"] == "confirmed_scene_script"
    assert result["script_artifact_id"] == script_id
    assert result["script_sha256"] == script_sha
    assert result["text"] == f"第1场 起身\n{SCRIPT_TEXT}"


def test_unconfirmed_script_falls_back_to_raw(api):
    client, factory, _ = api
    project = create_project(client, "未确认回退")
    master_id, section, unit, *_ = _build(factory, project, confirmed=False)
    result = _select(factory, project, master_id, section, unit)
    assert result["source"] == "raw_document"
    assert result["text"] == SECTION_TEXT
    assert result["script_artifact_id"] is None


def test_stale_script_falls_back_to_raw(api):
    client, factory, _ = api
    project = create_project(client, "失效回退")
    master_id, section, unit, *_ = _build(factory, project, stale=True)
    result = _select(factory, project, master_id, section, unit)
    assert result["source"] == "raw_document"


def test_master_document_mismatch_falls_back_to_raw(api):
    client, factory, _ = api
    project = create_project(client, "谱系不符回退")
    master_id, section, unit, *_ = _build(factory, project, master_match=False)
    result = _select(factory, project, master_id, section, unit)
    assert result["source"] == "raw_document"


def test_subslice_unit_never_takes_the_whole_scene_script(api):
    client, factory, _ = api
    project = create_project(client, "子片段回退")
    master_id, section, unit, *_ = _build(factory, project)
    # A unit covering only part of the section must not be fed the whole scene.
    part = {**unit, "end_offset": len(SECTION_TEXT) // 2}
    result = _select(factory, project, master_id, section, part)
    assert result["source"] == "raw_document"
    assert "UNIT_NOT_WHOLE_SECTION_RAW_FALLBACK" in result["diagnostics"]
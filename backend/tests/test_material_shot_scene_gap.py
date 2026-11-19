"""一份裁决：哪一镜真的缺参考，清单就必须为那一镜给出需求。

同一场里一镜有人物参考、另一镜只有旁白时，旧实现看的是「这一场有没有任何
素材」，于是旁白镜在旅程里报 ``SHOT_REFERENCE_MISSING``，素材清单里却找不到
它的对应需求（23 镜项目 1d14b472 就是这样漏的）。这里固定新口径：派生逐镜
消费 journey 的同一个判断函数，只有真正没有可绑参考的镜头才补真实场景需求，
已有场景主素材时直接复用，绝不按场次序号造无关填充图。
"""

from __future__ import annotations

import hashlib

import pytest

from shine.director.contracts import EpisodePlanPayload, digest_of
from shine.director.models import DirectorEpisodePlan
from shine.materialprep.story import section_storyboard_shot_ids
from tests.conftest import (
    _approve_lock_keeping_operator,
    create_project,
    create_referenced_shot,
    create_shot,
    freeze_style,
    upload_asset,
)

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}
SECTION_TITLE = "第1场：旧物修复铺"
SCRIPT = "第1场：旧物修复铺\n夜。铺子里只亮着一盏工作灯。\n他掀开表盖，看见钟背刻着的名字。\n"


def _document(factory, project_id: str) -> dict:
    from shine.models import ProjectDocument

    with factory.begin() as db:
        doc = ProjectDocument(
            project_id=project_id, filename="母本.txt", content_type="text/plain",
            sha256=hashlib.sha256(SCRIPT.encode("utf-8")).hexdigest(),
            source_format="txt", text=SCRIPT,
        )
        db.add(doc)
        db.flush()
        return {"id": doc.id, "sha256": doc.sha256}


def _outline(factory, project_id: str, document: dict) -> dict:
    from shine.models import AgentArtifact, AgentTaskRecord, utcnow

    rows = [{"production_section_id": "sec-1", "order": 1, "title": SECTION_TITLE,
             "estimated_duration_s": 12, "start_offset": 0, "end_offset": 40}]
    payload = {"production_sections": rows, "document_id": document["id"],
               "document_sha256": document["sha256"]}
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project_id, role_id="story_outline_run",
            request_identity=f"outline-{project_id}", input_hash="0" * 64,
            asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={},
        )
        db.add(source)
        db.flush()
        artifact = AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest_of(payload), producer_role="scriptwriter",
            source_task_id=source.id, approved_by="local-user", approved_at=utcnow(),
        )
        db.add(artifact)
        db.flush()
        return {"id": artifact.id, "sections": rows}


def _apply(factory, project_id: str, outline: dict, shot_ids: list[str]) -> None:
    """The section run and the apply receipt the storyboard apply writes."""
    from shine.models import AgentTaskRecord, AppSetting

    artifact_id = f"camera-{outline['id']}"
    with factory.begin() as db:
        db.add(AgentTaskRecord(
            project_id=project_id, role_id="story_section_run",
            request_identity=f"section-{outline['id']}", input_hash="0" * 64,
            asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED",
            trace={"outline_artifact_id": outline["id"],
                   "units": [{"production_section_id": "sec-1", "start_offset": 0}],
                   "results": [{"unit": {"start_offset": 0},
                                "storyboard": {"steps": {"cinematographer":
                                                        {"artifact_id": artifact_id}}}}]},
        ))
        db.add(AppSetting(key=f"film-apply:{artifact_id}", value={"shot_ids": list(shot_ids)}))


def _plan(factory, project_id: str, shot_ids: list[str]) -> None:
    plan = EpisodePlanPayload.model_validate({
        "mode": "shots", "target_seconds": 60, "tolerance_seconds": 15,
        "episodes": [{"episode_id": "ep-01", "order": 1, "title": "第 1 集",
                      "section_ids": ["sec-1"], "shot_ids": shot_ids, "planned_seconds": 12}],
    })
    with factory.begin() as db:
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=15,
            payload=plan.model_dump(mode="json"),
            sha256=digest_of(plan.model_dump(mode="json")), source="supplied",
        ))


def _derive(client, project_id: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/director/materials/derive",
                           json={}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _prepare(client, factory, title: str) -> tuple[str, dict, dict]:
    project_id = create_project(client, title)
    style = freeze_style(client, project_id)
    document = _document(factory, project_id)
    return project_id, style, document


def test_a_narrator_only_shot_in_a_reference_scene_gets_its_own_requirement(studio):
    client, factory, _tmp, _settings = studio
    project_id, style, document = _prepare(client, factory, "旁白镜素材缺口")

    # 同一场：一镜有已确认的人物主素材，另一镜只有旁白、没有任何参考。
    character_shot = create_referenced_shot(client, project_id, style["id"],
                                            character="沈砚", title="对白镜", duration=6)
    narrator_shot = create_shot(
        client, project_id, style["id"], title="旁白镜", duration=6,
        characters=["旁白"], dialogue=[{"speaker_id": "旁白", "text": "钟背刻着的，是母亲的名字。",
                                       "start_s": 0.8, "end_s": 4.4}],
    )
    outline = _outline(factory, project_id, document)
    shot_ids = [character_shot["id"], narrator_shot["id"]]
    _apply(factory, project_id, outline, shot_ids)
    _plan(factory, project_id, shot_ids)
    with factory() as db:
        resolved = section_storyboard_shot_ids(db, project_id, "sec-1")
    assert resolved["shot_ids"] == shot_ids

    body = _derive(client, project_id)
    by_key = {(row["family"], row["entity_id"]): row for row in body["requirements"]}
    scene_row = by_key.get(("scene", SECTION_TITLE))
    assert scene_row is not None, "旁白镜缺参考，清单必须为它给出场景需求"
    assert scene_row["used_shot_ids"] == [narrator_shot["id"]]
    assert scene_row["critical"] is True
    assert scene_row["strategy"] == "generate"
    assert character_shot["id"] not in scene_row["used_shot_ids"], \
        "已经有人物参考的镜头不需要填充场景图"
    assert ("character", "沈砚") in by_key


def test_each_scene_plate_is_injected_only_into_its_own_shots(studio):
    """逐镜注入：两场两镜，各场底图只进本场镜头，不互相塞、不全塞。"""
    client, factory, _tmp, _settings = studio
    project_id, style, _document_unused = _prepare(client, factory, "逐镜注入")
    from shine.materialprep.source_spans import segment_document
    from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, ProjectDocument, utcnow

    # 第一场足够长，使默认分块把两场切成不同的 scene_key。
    body1 = "夜。铺子里只亮着一盏工作灯。他掀开表盖，看见钟背刻着的名字。" * 150
    script = f"第1场：旧物修复铺\n{body1}\n第2场：码头\n晨。雾里的栈桥很长，望不到头，汽笛声从水面传过来。\n"
    internal = segment_document(script)
    assert len(internal) >= 2
    split = script.index("第2场")
    with factory.begin() as db:
        doc = ProjectDocument(
            project_id=project_id, filename="母本.txt", content_type="text/plain",
            sha256=hashlib.sha256(script.encode("utf-8")).hexdigest(),
            source_format="txt", text=script)
        db.add(doc)
        db.flush()
        document = {"id": doc.id, "sha256": doc.sha256}
    rows = [{"production_section_id": "sec-1", "order": 1, "title": SECTION_TITLE,
             "estimated_duration_s": 12, "start_offset": 0, "end_offset": split},
            {"production_section_id": "sec-2", "order": 2, "title": "第2场：码头",
             "estimated_duration_s": 12, "start_offset": split, "end_offset": len(script)}]
    payload = {"production_sections": rows, "document_id": document["id"],
               "document_sha256": document["sha256"]}
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project_id, role_id="story_outline_run",
            request_identity=f"outline2-{project_id}", input_hash="0" * 64,
            asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={})
        db.add(source)
        db.flush()
        outline_artifact = AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest_of(payload), producer_role="scriptwriter",
            source_task_id=source.id, approved_by="local-user", approved_at=utcnow())
        db.add(outline_artifact)
        db.flush()
        outline_id = outline_artifact.id
        for key, title in ((internal[0].key, SECTION_TITLE), (internal[-1].key, "第2场：码头")):
            prep = {"schema_version": "1.0", "scene_key": key, "summary": title,
                    "locked_facts": [], "questions": [],
                    "requirements": [{"family": "scene", "name": title,
                                      "description": title, "critical": True}]}
            db.add(AgentArtifact(
                project_id=project_id, kind="ScriptPreparation", schema_version="1.0",
                payload_json=prep, sha256=digest_of(prep), producer_role="producer",
                source_task_id=source.id))
    shot_a = create_shot(client, project_id, style["id"], title="场一旁白镜", duration=6,
                         characters=["旁白"], dialogue=[{"speaker_id": "旁白", "text": "他抬起手。",
                                                        "start_s": 0.5, "end_s": 3.0}])
    shot_b = create_shot(client, project_id, style["id"], title="场二旁白镜", duration=6,
                         characters=["旁白"], dialogue=[{"speaker_id": "旁白", "text": "雾散了。",
                                                        "start_s": 0.5, "end_s": 3.0}])
    cam1, cam2 = f"camera-a-{outline_id}", f"camera-b-{outline_id}"
    with factory.begin() as db:
        db.add(AgentTaskRecord(
            project_id=project_id, role_id="story_section_run",
            request_identity=f"section2-{outline_id}", input_hash="0" * 64,
            asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED",
            trace={"outline_artifact_id": outline_id,
                   "units": [{"production_section_id": "sec-1", "start_offset": 0},
                             {"production_section_id": "sec-2", "start_offset": split}],
                   "results": [{"unit": {"start_offset": 0},
                                "storyboard": {"steps": {"cinematographer": {"artifact_id": cam1}}}},
                               {"unit": {"start_offset": split},
                                "storyboard": {"steps": {"cinematographer": {"artifact_id": cam2}}}}]}))
        db.add(AppSetting(key=f"film-apply:{cam1}", value={"shot_ids": [shot_a["id"]]}))
        db.add(AppSetting(key=f"film-apply:{cam2}", value={"shot_ids": [shot_b["id"]]}))
    plan = EpisodePlanPayload.model_validate({
        "mode": "shots", "target_seconds": 60, "tolerance_seconds": 15,
        "episodes": [{"episode_id": "ep-01", "order": 1, "title": "第 1 集",
                      "section_ids": ["sec-1", "sec-2"], "shot_ids": [shot_a["id"], shot_b["id"]],
                      "planned_seconds": 24}],
    })
    with factory.begin() as db:
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=15,
            payload=plan.model_dump(mode="json"),
            sha256=digest_of(plan.model_dump(mode="json")), source="supplied"))
    plate1 = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n-plate-1",
                          filename="铺子.png", role="scene", entity_id=SECTION_TITLE)
    _approve_lock_keeping_operator(client, plate1["id"])
    plate2 = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n-plate-2",
                          filename="码头.png", role="scene", entity_id="第2场：码头")
    _approve_lock_keeping_operator(client, plate2["id"])

    body = _derive(client, project_id)
    by_key = {(row["family"], row["entity_id"]): row for row in body["requirements"]}
    assert by_key[("scene", SECTION_TITLE)]["used_shot_ids"] == [shot_a["id"]]
    assert by_key[("scene", "第2场：码头")]["used_shot_ids"] == [shot_b["id"]]

    from tests.test_material_adoption_one_write import _bindings, _confirm
    _confirm(client, project_id, [
        {"requirement_id": by_key[("scene", SECTION_TITLE)]["requirement_id"],
         "strategy": "reuse_master", "entity_id": SECTION_TITLE, "asset_id": plate1["id"]},
        {"requirement_id": by_key[("scene", "第2场：码头")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "第2场：码头", "asset_id": plate2["id"]},
    ])
    assert _bindings(factory, shot_a["id"]) == [(plate1["id"], "picture", None)]
    assert _bindings(factory, shot_b["id"]) == [(plate2["id"], "picture", None)], \
        "每镜只注入本场场景，不塞别场、不全塞"


def test_the_existing_scene_master_of_that_section_is_reused(studio):
    client, factory, _tmp, _settings = studio
    project_id, style, document = _prepare(client, factory, "旁白镜复用场景")
    scene = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n-scene-1",
                         filename="旧物修复铺.png", role="scene", entity_id=SECTION_TITLE)
    _approve_lock_keeping_operator(client, scene["id"])
    narrator_shot = create_shot(
        client, project_id, style["id"], title="旁白镜", duration=6,
        characters=["旁白"], dialogue=[{"speaker_id": "旁白", "text": "他抬起手。",
                                       "start_s": 0.5, "end_s": 3.0}],
    )
    outline = _outline(factory, project_id, document)
    shot_ids = [narrator_shot["id"]]
    _apply(factory, project_id, outline, shot_ids)
    _plan(factory, project_id, shot_ids)

    body = _derive(client, project_id)
    by_key = {(row["family"], row["entity_id"]): row for row in body["requirements"]}
    scene_row = by_key[("scene", SECTION_TITLE)]
    assert scene_row["strategy"] == "reuse_master"
    assert scene_row["matched_master"]["asset_id"] == scene["id"]

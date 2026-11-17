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

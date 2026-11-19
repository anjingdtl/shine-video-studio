"""一次采纳、一次写入：同一镜的多份确认不能互相覆盖。

复现过的缺陷：同一镜头同时确认两份合法人物主素材时，第二次写入读到的是第
一次写入之前的绑定，于是 ``required_asset_ids`` 里已经有两份、绑定里只有一份，
整笔确认被 422 ``Bindings are missing assets required by the shot`` 回滚。

这里固定新口径：采纳执行者先把这一镜的全部裁决合成为一份目标集合，再写一次
``shot.bindings``、同步一次 ``required_asset_ids``；A 换成 B 只留 B，其他有效
引用照常保留；重复的确认（丢响应后重放）不会再写第二遍。
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    _approve_lock_keeping_operator,
    create_project,
    create_shot,
    freeze_style,
    upload_asset,
)
from tests.test_material_shot_scene_gap import (
    LOCAL,
    SECTION_TITLE,
    _apply,
    _document,
    _outline,
    _plan,
)

pytestmark = pytest.mark.integration


def _derive(client, project_id: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/director/materials/derive",
                           json={}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _progress(client, project_id: str) -> dict:
    response = client.get(f"/api/v1/projects/{project_id}/material-preparation")
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client, project_id: str, decisions: list[dict]) -> dict:
    progress = _progress(client, project_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={"plan_revision_id": progress["plan_revision_id"],
              "expected_snapshot": progress["expected_snapshot"],
              "decisions": decisions,
              "visual_direction": {"era": "旧物修复铺"}},
        headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _master(client, project_id: str, *, role: str, entity: str, tag: str) -> dict:
    asset = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n" + tag.encode(),
                         filename=f"{tag}.png", role=role, entity_id=entity)
    _approve_lock_keeping_operator(client, asset["id"])
    return asset


def _bindings(factory, shot_id: str) -> list[tuple[str, str, str | None]]:
    from sqlalchemy import select

    from shine.models import ShotAssetBinding

    with factory() as db:
        rows = db.scalars(select(ShotAssetBinding).where(
            ShotAssetBinding.shot_id == shot_id).order_by(ShotAssetBinding.ordinal)).all()
        return sorted((row.asset_id, row.use_as, row.role) for row in rows)


def _declared(factory, shot_id: str) -> list[str]:
    from shine.models import Shot

    with factory() as db:
        return list((db.get(Shot, shot_id).spec or {}).get("required_asset_ids") or [])


def _project_with_shots(client, factory, title: str) -> dict:
    project_id = create_project(client, title)
    style = freeze_style(client, project_id)
    document = _document(factory, project_id)
    pair = create_shot(client, project_id, style["id"], title="双人对白镜", duration=6,
                       characters=["沈砚", "委托人"])
    narrator = create_shot(client, project_id, style["id"], title="旁白镜", duration=6,
                           characters=["旁白"],
                           dialogue=[{"speaker_id": "旁白", "text": "钟背刻着的名字。",
                                      "start_s": 0.5, "end_s": 3.0}])
    outline = _outline(factory, project_id, document)
    shot_ids = [pair["id"], narrator["id"]]
    _apply(factory, project_id, outline, shot_ids)
    _plan(factory, project_id, shot_ids)
    return {"project_id": project_id, "pair": pair, "narrator": narrator}


def _requirements(client, project_id: str) -> dict[str, dict]:
    body = _derive(client, project_id)
    return {(row["family"], row["entity_id"]): row for row in body["requirements"]}


def test_two_characters_confirmed_for_one_shot_are_written_in_one_pass(studio):
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "同镜双人物")
    project_id, pair = built["project_id"], built["pair"]
    row = _master(client, project_id, role="character", entity="沈砚", tag="shenyan")
    other = _master(client, project_id, role="character", entity="委托人", tag="client")
    requirements = _requirements(client, project_id)
    assert ("character", "沈砚") in requirements and ("character", "委托人") in requirements

    body = _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": row["id"]},
        {"requirement_id": requirements[("character", "委托人")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "委托人", "asset_id": other["id"]},
    ])
    assert body["bound_shots"] >= 1
    assert _bindings(factory, pair["id"]) == sorted([
        (row["id"], "subject", "沈砚"),
        (other["id"], "subject", "委托人"),
    ])
    assert set(_declared(factory, pair["id"])) == {row["id"], other["id"]}


def test_a_character_and_its_scene_plate_land_in_one_batch(studio):
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "人物与场景同批")
    project_id, pair, narrator = built["project_id"], built["pair"], built["narrator"]
    person = _master(client, project_id, role="character", entity="沈砚", tag="shenyan")
    client_master = _master(client, project_id, role="character", entity="委托人", tag="client")
    plate = _master(client, project_id, role="scene", entity=SECTION_TITLE, tag="plate")
    requirements = _requirements(client, project_id)

    _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": person["id"]},
        {"requirement_id": requirements[("character", "委托人")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "委托人", "asset_id": client_master["id"]},
        {"requirement_id": requirements[("scene", SECTION_TITLE)]["requirement_id"],
         "strategy": "reuse_master", "entity_id": SECTION_TITLE, "asset_id": plate["id"]},
    ])

    assert _bindings(factory, pair["id"]) == sorted([
        (person["id"], "subject", "沈砚"),
        (client_master["id"], "subject", "委托人"),
    ])
    assert _bindings(factory, narrator["id"]) == [(plate["id"], "picture", None)]
    assert _declared(factory, narrator["id"]) == [plate["id"]]


def test_replacing_a_master_keeps_only_the_new_one_and_other_references(studio):
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "素材替换")
    project_id, pair = built["project_id"], built["pair"]
    first = _master(client, project_id, role="character", entity="沈砚", tag="shenyan-a")
    client_master = _master(client, project_id, role="character", entity="委托人", tag="client")
    requirements = _requirements(client, project_id)
    _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": first["id"]},
        {"requirement_id": requirements[("character", "委托人")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "委托人", "asset_id": client_master["id"]},
    ])
    assert len(_bindings(factory, pair["id"])) == 2

    second = _master(client, project_id, role="character", entity="沈砚", tag="shenyan-b")
    requirements = _requirements(client, project_id)
    body = _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": second["id"]},
    ])
    assert body["bound_shots"] >= 1
    bound = _bindings(factory, pair["id"])
    assert (second["id"], "subject", "沈砚") in bound
    assert first["id"] not in {row[0] for row in bound}, "A 换成 B 后只保留 B"
    assert (client_master["id"], "subject", "委托人") in bound, "其他有效引用照常保留"
    assert first["id"] not in _declared(factory, pair["id"])
    assert set(_declared(factory, pair["id"])) == {second["id"], client_master["id"]}


def test_a_replayed_confirmation_does_not_write_a_second_time(studio):
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "重放确认")
    project_id, pair = built["project_id"], built["pair"]
    person = _master(client, project_id, role="character", entity="沈砚", tag="shenyan")
    client_master = _master(client, project_id, role="character", entity="委托人", tag="client")
    requirements = _requirements(client, project_id)
    decisions = [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": person["id"]},
        {"requirement_id": requirements[("character", "委托人")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "委托人", "asset_id": client_master["id"]},
    ]
    # 第一次确认后响应丢失：同一份确认再交一次，绑定集合不变、不重复写。
    progress = _progress(client, project_id)
    payload = {"plan_revision_id": progress["plan_revision_id"],
               "expected_snapshot": progress["expected_snapshot"],
               "decisions": decisions, "visual_direction": {"era": "旧物修复铺"}}
    first = client.post(f"/api/v1/projects/{project_id}/material-preparation/confirm",
                        json=payload, headers=LOCAL)
    assert first.status_code == 200, first.text
    before = _bindings(factory, pair["id"])
    assert len(before) == 2

    with factory() as db:
        from shine.models import Shot

        revision_before = db.get(Shot, pair["id"]).revision

    again = client.post(f"/api/v1/projects/{project_id}/material-preparation/confirm",
                        json=payload, headers=LOCAL)
    # 旧修订的快照已经过期：同一笔确认不会在新修订上再执行一次。
    assert again.status_code in {200, 409}, again.text
    assert _bindings(factory, pair["id"]) == before
    with factory() as db:
        from shine.models import Shot

        assert db.get(Shot, pair["id"]).revision == revision_before

def test_cancelling_an_adoption_unbinds_the_master_from_the_shots(studio):
    """取消采用：改回 AI 制作后，旧主素材由同一执行者解绑，其他引用保留。"""
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "取消采用")
    project_id, pair = built["project_id"], built["pair"]
    person = _master(client, project_id, role="character", entity="沈砚", tag="shenyan")
    client_master = _master(client, project_id, role="character", entity="委托人", tag="client")
    requirements = _requirements(client, project_id)
    _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "沈砚", "asset_id": person["id"]},
        {"requirement_id": requirements[("character", "委托人")]["requirement_id"],
         "strategy": "reuse_master", "entity_id": "委托人", "asset_id": client_master["id"]},
    ])
    assert len(_bindings(factory, pair["id"])) == 2

    requirements = _requirements(client, project_id)
    body = _confirm(client, project_id, [
        {"requirement_id": requirements[("character", "沈砚")]["requirement_id"],
         "strategy": "generate", "entity_id": "沈砚"},
    ])
    assert body["bound_shots"] >= 1
    bound = _bindings(factory, pair["id"])
    assert person["id"] not in {row[0] for row in bound}, "取消采用后旧主素材解绑"
    assert (client_master["id"], "subject", "委托人") in bound, "其他有效引用保留"
    assert person["id"] not in _declared(factory, pair["id"])

    # 重新派生不得复活取消：最新决定（生成）胜过旧修订的绑定与推导建议。
    again = _requirements(client, project_id)
    assert again[("character", "沈砚")]["strategy"] == "generate"
    assert person["id"] not in {row[0] for row in _bindings(factory, pair["id"])}


def test_replacing_a_scene_plate_keeps_only_the_new_one(studio):
    """同一场景确认了第二张主素材：只留新的那张，不给一镜塞两张环境图。"""
    client, factory, _tmp, _settings = studio
    built = _project_with_shots(client, factory, "场景换图")
    project_id, narrator = built["project_id"], built["narrator"]
    first = _master(client, project_id, role="scene", entity=SECTION_TITLE, tag="plate-a")
    requirements = _requirements(client, project_id)
    _confirm(client, project_id, [
        {"requirement_id": requirements[("scene", SECTION_TITLE)]["requirement_id"],
         "strategy": "reuse_master", "entity_id": SECTION_TITLE, "asset_id": first["id"]},
    ])
    assert _bindings(factory, narrator["id"]) == [(first["id"], "picture", None)]

    second = _master(client, project_id, role="scene", entity=SECTION_TITLE, tag="plate-b")
    requirements = _requirements(client, project_id)
    _confirm(client, project_id, [
        {"requirement_id": requirements[("scene", SECTION_TITLE)]["requirement_id"],
         "strategy": "reuse_master", "entity_id": SECTION_TITLE, "asset_id": second["id"]},
    ])
    assert _bindings(factory, narrator["id"]) == [(second["id"], "picture", None)]
    assert _declared(factory, narrator["id"]) == [second["id"]]

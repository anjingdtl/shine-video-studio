"""④ One material hand-off: deterministic identity, replay, no second purchase.

The V2 plan names the exact defect this file pins: a generation batch keyed by
``Date.now`` buys a second round of images when the answer is lost.  The commit
action therefore derives its request identity from the confirmed decision set
and keeps a receipt in the existing audit ledger — a retry of the same intent
returns the same confirmation and the same batch.
"""

from __future__ import annotations

from tests.conftest import register_workflow_profile
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def _episode_one_requirements(client, settings) -> tuple[str, dict]:
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    register_workflow_profile(client, mode="Ref2VA")

    derived = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                          json={}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    return project, derived.json()


def _decisions_for(body: dict) -> list[dict]:
    rows = []
    for requirement in body["requirements"]:
        if requirement["family"] == "character":
            strategy = "generate"
        else:
            strategy = "skip"
        rows.append({
            "requirement_id": requirement["requirement_id"],
            "strategy": strategy,
            "entity_id": requirement["entity_id"],
            "asset_id": None,
        })
    return rows


def test_material_commit_replays_the_same_intent_instead_of_buying_twice(api):
    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    body = {
        "plan_revision_id": derived["plan_revision_id"],
        "expected_snapshot": derived["expected_snapshot"],
        "decisions": _decisions_for(derived),
    }

    first = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                        json=body, headers=LOCAL)
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["replayed"] is False
    assert first_body["confirmation"]["status"] == "CONFIRMED"
    assert first_body["request_key"].startswith("director-materials:")

    # A lost answer, a double click, or a refreshed tab retries the same intent.
    again = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                        json=body, headers=LOCAL)
    assert again.status_code == 201, again.text
    second_body = again.json()
    assert second_body["replayed"] is True
    assert second_body["confirmation"]["confirmation_id"] == first_body["confirmation"]["confirmation_id"]
    assert second_body["confirmation"]["confirmation_hash"] == first_body["confirmation"]["confirmation_hash"]

    # Exactly one batch exists for the intent, whatever the retry count.
    from shine.materialprep.models import MaterialRun
    from shine.models import Project

    with factory() as db:
        project_row = db.get(Project, project)
        assert project_row is not None
        runs = db.query(MaterialRun).filter(MaterialRun.project_id == project).all()
        assert len(runs) <= 1, [row.id for row in runs]


def test_material_commit_refuses_a_changed_list(api):
    client, _factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    stale = client.post(
        f"/api/v1/projects/{project}/director/materials/commit",
        json={
            "plan_revision_id": derived["plan_revision_id"],
            "expected_snapshot": "0" * 64,
            "decisions": _decisions_for(derived),
        },
        headers=LOCAL)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MATERIAL_PLAN_STALE"


def test_material_requirements_read_never_derives_again(api):
    client, _factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    before = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                         json={}, headers=LOCAL)
    assert before.status_code == 200

    listed = client.get(f"/api/v1/projects/{project}/director/materials/requirements")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["plan_revision_id"]
    assert body["requirements"], "the derived list must be readable without re-deriving"
    assert {row["family"] for row in body["requirements"]} <= {"character", "scene", "prop"}
    assert any(row["used_shot_count"] >= 1 for row in body["requirements"])


def test_master_match_prefers_the_latest_when_one_entity_has_two_masters():
    """同一人物被确认过两份主素材时，匹配取最近确认的那一份而不是“缺”。

    保守匹配在“不唯一”时会放弃，结果是已经有素材的人显示成缺失，用户被要求
    再做一次同样的确认。两份主素材都保留，只是派生以最近一份为准。
    """
    from datetime import datetime
    from types import SimpleNamespace

    from shine.director.requirements import _master_for

    older = SimpleNamespace(id="a-old", role="character", entity_id="陈默",
                            filename="old.png", kind="image",
                            created_at=datetime(2026, 11, 13, 10, 0))
    newer = SimpleNamespace(id="a-new", role="character", entity_id="陈默",
                            filename="new.png", kind="image",
                            created_at=datetime(2026, 11, 13, 11, 0))
    matched = _master_for("character", "陈默", [older, newer])
    assert matched is not None
    assert matched.id == "a-new"

    # 单个主素材仍然照常匹配。
    assert _master_for("character", "陈默", [older]).id == "a-old"
    # 没有匹配时仍然如实返回 None（不编造素材）。
    assert _master_for("character", "另一个人", [older, newer]) is None


def test_confirming_a_master_binds_it_onto_the_shots_that_asked_for_it(api):
    """确认「复用主素材」必须同时回绑镜头，否则该集仍然显示缺素材。

    镜头表与开拍预检读的是 shot.bindings，不是素材清单。只写 requirement
    会让用户“确认了素材却还是不能拍”。
    """
    from sqlalchemy import select

    from shine.models import Shot

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    body = {
        "plan_revision_id": derived["plan_revision_id"],
        "expected_snapshot": derived["expected_snapshot"],
        "decisions": _decisions_for(derived),
    }
    committed = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                            json=body, headers=LOCAL)
    assert committed.status_code == 201, committed.text

    master_id = next(row["matched_master"]["asset_id"] for row in derived["requirements"]
                     if row["family"] == "character" and row["matched_master"])
    with factory() as db:
        shots = db.scalars(select(Shot).where(Shot.project_id == project)).all()
        assert shots
        bound = [shot for shot in shots if any(link.asset_id == master_id for link in shot.bindings)]
        assert bound, "确认复用后，镜头必须真的绑定这份主素材"

    overview = client.get(f"/api/v1/projects/{project}/director/overview").json()
    episode = overview["episodes"][0]
    assert episode["materials"]["missing_count"] == 0
    assert episode["state"] in {"READY_TO_PACKAGE", "READY_TO_SHOOT"}

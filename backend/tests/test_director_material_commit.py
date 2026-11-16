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
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
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


def test_a_single_row_commit_keeps_the_whole_requirement_list(api):
    """上传/重新生成只决策一行；清单不能塌缩成只剩这一行。

    需求页读的是当前 plan 修订的行。confirm 若只迁移决策行，其余需求会滞留
    旧修订，单条提交后整张清单消失——正是用户报的“上传后只剩一条”。
    """
    client, _factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    requirements = derived["requirements"]
    assert len(requirements) >= 2, "需要至少两项需求才能验证清单不塌缩"
    target = requirements[0]
    partial = client.post(
        f"/api/v1/projects/{project}/director/materials/commit",
        json={
            "plan_revision_id": derived["plan_revision_id"],
            "expected_snapshot": derived["expected_snapshot"],
            "decisions": [{
                "requirement_id": target["requirement_id"],
                "strategy": "generate",
                "entity_id": target["entity_id"],
                "asset_id": None,
            }],
            "queue_generation": False,
        }, headers=LOCAL)
    assert partial.status_code == 201, partial.text

    listed = client.get(f"/api/v1/projects/{project}/director/materials/requirements")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["plan_revision_id"] != derived["plan_revision_id"], "确认应产生新修订"
    kept = {row["requirement_id"] for row in body["requirements"]}
    original = {row["requirement_id"] for row in requirements}
    assert original.issubset(kept), f"单条提交后清单塌缩：丢失 {original - kept}"


def test_material_preparation_confirm_binds_shots_like_director_commit(api):
    """晋升走的 /material-preparation/confirm 必须与 director commit 一样回绑镜头。

    素材清单读派生匹配（matched_master），journey/overview/导演包读 shot.bindings。
    若 material-prep confirm 不回绑，用户晋升了主素材，清单显示「已匹配」而导演包
    仍报「缺必需素材」——一个事实两处状态。确认必须只有一个执行者。
    """
    from sqlalchemy import select

    from shine.models import Shot

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    matched = next(row for row in derived["requirements"]
                   if row["family"] == "character" and row["matched_master"])
    decisions = []
    for row in derived["requirements"]:
        if row["requirement_id"] == matched["requirement_id"]:
            strategy, asset_id = "reuse_master", matched["matched_master"]["asset_id"]
        elif row["family"] == "character":
            strategy, asset_id = "generate", None
        else:
            strategy, asset_id = "skip", None
        decisions.append({"requirement_id": row["requirement_id"], "strategy": strategy,
                          "entity_id": row["entity_id"], "asset_id": asset_id})
    confirmed = client.post(
        f"/api/v1/projects/{project}/material-preparation/confirm",
        json={"plan_revision_id": derived["plan_revision_id"],
              "expected_snapshot": derived["expected_snapshot"],
              "decisions": decisions}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    master_id = matched["matched_master"]["asset_id"]
    with factory() as db:
        shots = db.scalars(select(Shot).where(Shot.project_id == project)).all()
        bound = [shot for shot in shots if any(link.asset_id == master_id for link in shot.bindings)]
        assert bound, "material-preparation/confirm 必须回绑镜头，与 director commit 同一事实"


def test_promoting_a_candidate_adopts_it_into_requirement_and_shots(api):
    """晋升候选为主素材必须同时回绑需求与镜头（用户实际走的路径）。

    晋升只创建主素材而不回绑时，素材清单显示「已匹配」而导演包仍报「缺必需素材」。
    晋升与确认共用同一个采纳执行者，一个事实通达全流程。
    """
    import hashlib

    from sqlalchemy import select

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement, CandidateAsset
    from shine.materialprep.models import MaterialItem, MaterialRun
    from shine.models import Shot

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    target = next(row for row in derived["requirements"]
                  if row["family"] == "character" and row["used_shot_ids"])
    entity = target["entity_id"]

    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    digest = hashlib.sha256(png).hexdigest()
    storage_path = f"candidates/promote-bind-{target['requirement_id']}.png"
    (settings.data_dir / storage_path).parent.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / storage_path).write_bytes(png)

    with factory.begin() as db:
        run = MaterialRun(
            project_id=project, plan_id=derived["plan_revision_id"],
            confirmation_id="c" * 64, confirmation_hash="h" * 64,
            idempotency_key=f"test-promote-{target['requirement_id']}",
            request_hash="r" * 64, mode="real", status="COMPLETED",
            payload={"kind": "production"}, sha256="s" * 64)
        db.add(run)
        db.flush()
        item = MaterialItem(run_id=run.id, project_id=project,
                            requirement_id=target["requirement_id"],
                            generation_revision=1, status="CANDIDATE_READY",
                            payload={}, sha256="i" * 64)
        db.add(item)
        db.flush()
        payload = {
            "kind": "image", "family": "character", "entity_id": entity,
            "filename": "promote-bind.png", "content_type": "image/png",
            "content_sha256": digest, "storage_path": storage_path,
            "byte_size": len(png), "rights_id": None, "media": {},
            "provenance": {"item_id": item.id, "is_mock": False, "renderer_owned": True},
        }
        candidate = CandidateAsset(project_id=project, status="READY_FOR_REVIEW",
                                   payload=payload, sha256=graph_hash(payload))
        db.add(candidate)
        db.flush()
        candidate_id = candidate.id

    grant = client.post("/api/v1/preparation/approval-grants",
                        json={"action": "promote", "target_id": candidate_id}, headers=LOCAL)
    assert grant.status_code in {200, 201}, grant.text
    promoted = client.post(f"/api/v1/candidate-assets/{candidate_id}/promote",
                           json={"grant_id": grant.json()["grant_id"],
                                 "snapshot": grant.json()["snapshot"],
                                 "offline": False, "evidence": "回归测试晋升"},
                           headers=LOCAL)
    assert promoted.status_code == 200, promoted.text
    formal_id = promoted.json()["formal_asset_id"]
    assert formal_id

    with factory() as db:
        requirement = db.get(AssetRequirement, target["requirement_id"])
        assert requirement.payload.get("strategy") == "reuse_master"
        assert requirement.payload.get("bound_asset_id") == formal_id
        shots = db.scalars(select(Shot).where(Shot.project_id == project)).all()
        bound = [shot for shot in shots if any(link.asset_id == formal_id for link in shot.bindings)]
        assert bound, "晋升必须把主素材回绑到需要它的镜头"


def test_an_interrupt_before_commit_leaves_nothing_and_resubmits_cleanly(api, monkeypatch):
    """确认+入队+回执是一次事务：提交前中断不留半态，重试不撞 MATERIAL_PLAN_STALE。

    旧链路里 confirm 先单独 commit，入队前中断会留下「确认已落库、批次与回执
    都没有」的半态；重试对着已迁移的需求行报 MATERIAL_PLAN_STALE，库里永远没有
    生成任务。原子提交后，中断什么都不留，同一意图可以干净重提。
    """
    from fastapi import HTTPException

    from shine.comfyops.models import PreProductionPlan
    from shine.materialprep import service
    from shine.materialprep.models import MaterialRun
    from shine.models import AuditEvent

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    body = {
        "plan_revision_id": derived["plan_revision_id"],
        "expected_snapshot": derived["expected_snapshot"],
        "decisions": _decisions_for(derived),
    }
    with factory() as db:
        plans_before = db.query(PreProductionPlan).filter(
            PreProductionPlan.project_id == project).count()

    real_create_batch = service.create_batch

    def interrupted(*_args, **_kwargs):
        raise HTTPException(409, "SIMULATED_INTERRUPT_BEFORE_COMMIT")

    monkeypatch.setattr(service, "create_batch", interrupted)
    failed = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                         json=body, headers=LOCAL)
    assert failed.status_code == 409
    monkeypatch.setattr(service, "create_batch", real_create_batch)

    with factory() as db:
        plans_after = db.query(PreProductionPlan).filter(
            PreProductionPlan.project_id == project).count()
        runs = db.query(MaterialRun).filter(MaterialRun.project_id == project).count()
        receipts = db.query(AuditEvent).filter(
            AuditEvent.project_id == project,
            AuditEvent.event_type == "DIRECTOR_MATERIALS_COMMITTED").count()
    assert plans_after == plans_before, "确认不得在批次之前单独落库"
    assert runs == 0
    assert receipts == 0

    ok = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                     json=body, headers=LOCAL)
    assert ok.status_code == 201, ok.text
    assert ok.json()["replayed"] is False
    again = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                        json=body, headers=LOCAL)
    assert again.status_code == 201, again.text
    assert again.json()["replayed"] is True
    assert again.json()["confirmation"]["confirmation_id"] == ok.json()["confirmation"]["confirmation_id"]
    with factory() as db:
        runs = db.query(MaterialRun).filter(MaterialRun.project_id == project).count()
    assert runs == 1, "全程最多生成一个批次"


def test_generation_progress_reads_the_same_run_ledger_as_the_material_page(api):
    """进度条读的是同一份 public_run 台账，不是第二套判定。"""
    client, _factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    committed = client.post(
        f"/api/v1/projects/{project}/director/materials/commit",
        json={
            "plan_revision_id": derived["plan_revision_id"],
            "expected_snapshot": derived["expected_snapshot"],
            "decisions": _decisions_for(derived),
        }, headers=LOCAL)
    assert committed.status_code == 201, committed.text

    progress = client.get(f"/api/v1/projects/{project}/material-preparation/generation-progress")
    assert progress.status_code == 200, progress.text
    progress_runs = progress.json()["runs"]
    full = client.get(f"/api/v1/projects/{project}/material-preparation").json()
    production = [row for row in full["runs"] if row["kind"] == "production"]
    assert [row["run_id"] for row in progress_runs] == [row["run_id"] for row in production]
    for row in progress_runs:
        assert row["kind"] == "production"
        assert {"total", "candidate_ready", "queued", "running", "failed", "needs_review"} <= set(row["counts"])


def _confirm_reuse(client, project, plan_id, snapshot, requirements, target_id, asset_id):
    decisions = []
    for row in requirements:
        if row["requirement_id"] == target_id:
            strategy, aid = "reuse_master", asset_id
        elif row["family"] == "character":
            strategy, aid = "generate", None
        else:
            strategy, aid = "skip", None
        decisions.append({"requirement_id": row["requirement_id"], "strategy": strategy,
                          "entity_id": row["entity_id"], "asset_id": aid})
    response = client.post(
        f"/api/v1/projects/{project}/material-preparation/confirm",
        json={"plan_revision_id": plan_id, "expected_snapshot": snapshot,
              "decisions": decisions}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def test_reconfirming_a_different_master_replaces_the_old_shot_reference(api):
    """同一人物主素材 A→B：镜头只保留 B，不再同时绑 A 和 B。

    旧执行者只“追加”确认的主素材：需求清单改选 B 后，镜头仍留着 A，于是
    一个人物绑了两份主体参考。确认流程必须替换该需求对应的旧主体引用，并同步
    镜头的 required_asset_ids，导演包读取这份已确认绑定。
    """

    from shine.models import Shot
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    target = next(row for row in derived["requirements"]
                  if row["family"] == "character" and row["used_shot_ids"] and row["matched_master"])
    entity = target.get("canonical_entity_id") or target["entity_id"]
    master_a = target["matched_master"]["asset_id"]

    _confirm_reuse(client, project, derived["plan_revision_id"], derived["expected_snapshot"],
                   derived["requirements"], target["requirement_id"], master_a)
    with factory() as db:
        shot = db.get(Shot, target["used_shot_ids"][0])
        assert any(link.asset_id == master_a for link in shot.bindings)

    master_b = upload_asset(client, project, content=PNG + b"-replacement-B",
                            filename="替换乙.png", role="character", entity_id=entity)
    approve_lock(client, master_b["id"])

    listed = client.get(f"/api/v1/projects/{project}/director/materials/requirements").json()
    _confirm_reuse(client, project, listed["plan_revision_id"], listed["expected_snapshot"],
                   listed["requirements"], target["requirement_id"], master_b["id"])

    with factory() as db:
        for shot_id in target["used_shot_ids"]:
            shot = db.get(Shot, shot_id)
            if entity not in list((shot.spec or {}).get("characters") or []):
                continue
            bound_ids = {link.asset_id for link in shot.bindings}
            assert master_b["id"] in bound_ids, "改选 B 后镜头必须绑定 B"
            assert master_a not in bound_ids, "改选 B 后镜头不得再保留旧主素材 A"
            subjects = [link for link in shot.bindings
                        if link.use_as == "subject" and link.role == entity]
            assert len(subjects) == 1 and subjects[0].asset_id == master_b["id"]
            required = set((shot.spec or {}).get("required_asset_ids") or [])
            assert master_b["id"] in required
            assert master_a not in required, "required_asset_ids 必须同步替换"


def test_binding_adoption_never_revises_a_frozen_shot(api):
    """已冻结进确认导演包的镜头，素材改选也不重写它的绑定。

    保护规则只有一个：冻结版本与在途任务不被任何绑定执行者改写。改选 B 时
    冻结镜头仍保留打包时的 A，bound_shots 不为它计数。
    """

    from shine.models import Shot
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    target = next(row for row in derived["requirements"]
                  if row["family"] == "character" and row["used_shot_ids"] and row["matched_master"])
    entity = target.get("canonical_entity_id") or target["entity_id"]
    master_a = target["matched_master"]["asset_id"]
    _confirm_reuse(client, project, derived["plan_revision_id"], derived["expected_snapshot"],
                   derived["requirements"], target["requirement_id"], master_a)

    profiles = client.get("/api/v1/workflow-profiles").json()
    profile = next(row for row in profiles if (row.get("mode") or "").lower() == "ref2va")
    built = client.post(f"/api/v1/projects/{project}/director-packages",
                        json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA",
                              "episode_ids": ["ep-01"]}, headers=LOCAL)
    assert built.status_code == 201, built.text
    package = built.json()
    confirmed = client.post(f"/api/v1/director-packages/{package['id']}/confirm",
                            json={"expected_manifest_sha256": package["manifest_sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    master_b = upload_asset(client, project, content=PNG + b"-frozen-B",
                            filename="冻结乙.png", role="character", entity_id=entity)
    approve_lock(client, master_b["id"])
    listed = client.get(f"/api/v1/projects/{project}/director/materials/requirements").json()
    result = _confirm_reuse(client, project, listed["plan_revision_id"], listed["expected_snapshot"],
                            listed["requirements"], target["requirement_id"], master_b["id"])

    with factory() as db:
        shot = db.get(Shot, target["used_shot_ids"][0])
        bound_ids = {link.asset_id for link in shot.bindings}
        assert master_a in bound_ids, "冻结镜头保留打包时的绑定"
        assert master_b["id"] not in bound_ids, "冻结镜头不被素材改选重写"
    assert result["bound_shots"] == 0


def test_a_narrator_shot_is_carried_by_its_scene_plate_minimum_reference(api):
    """最小参考素材：旁白镜没有人物主体，由确认的场景图承载 Ref2VA 参考。

    一套事实（shot.bindings）、一个裁决（_shot_material_problems 与 package
    readiness 都要求 cut 镜至少一个参考）、一个执行者（bind_adopted 同时绑人物
    主体与场景图）。无场景图时裁决如实报 SHOT_REFERENCE_MISSING；确认场景图后
    同一执行者把它绑成 picture 参考，裁决就绪，不再出现「素材就绪却开不了机」。
    """
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement, PreProductionPlan
    from shine.director.journey import _shot_material_problems
    from shine.materialprep.service import bind_adopted_masters_to_shots
    from shine.models import Shot
    from tests.conftest import PNG, approve_lock, create_project, create_shot, freeze_style, upload_asset

    client, factory, _settings = api
    project = create_project(client, "最小参考")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], title="旁白镜", characters=["旁白"])
    scene = upload_asset(client, project, content=PNG + b"-scene-plate",
                         filename="场景.png", role="scene", entity_id="旧物修复铺")
    approve_lock(client, scene["id"])

    with factory() as db:
        s = db.get(Shot, shot["id"])
        assert "SHOT_REFERENCE_MISSING" in _shot_material_problems(db, s), \
            "无参考的旁白镜必须被裁决报缺参考，而不是假装就绪"

    with factory.begin() as db:
        plan = PreProductionPlan(project_id=project, purpose="asset_preparation",
                                 status="CONFIRMED", payload={}, sha256=graph_hash({}))
        db.add(plan)
        db.flush()
        req = {"family": "scene", "entity_id": "旧物修复铺", "strategy": "reuse_master",
               "bound_asset_id": scene["id"], "bound_asset_sha256": scene["sha256"],
               "used_shot_ids": [shot["id"]]}
        db.add(AssetRequirement(project_id=project, plan_id=plan.id, status="CONFIRMED",
                                payload=req, sha256=graph_hash(req)))
        plan_id = plan.id

    with factory.begin() as db:
        bound = bind_adopted_masters_to_shots(db, project, plan_id, "local-user")
    assert bound >= 1
    with factory() as db:
        s = db.get(Shot, shot["id"])
        assert any(link.asset_id == scene["id"] and link.use_as == "picture"
                   for link in s.bindings), "场景图必须绑成旁白镜的 picture 参考"
        assert _shot_material_problems(db, s) == [], "最小参考满足后裁决就绪"

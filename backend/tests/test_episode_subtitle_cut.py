"""⑦ 字幕与装配读同一张实际剪辑表。

复现过的缺陷：``subtitles._real_clip_durations`` 用 ``scope_id == "ep-01"`` 和
``status == "COMPLETED"`` 查交付单，而真实交付记录的 ``scope_id`` 是运行中的
分集行 ID、成功状态是 ``DELIVERED``——于是永远查不到，字幕静默回落到计划时长。
六镜成片（产物 1a678d1e…）第 6 镜真实起点 49.912s，旧代码给出 49.000s。

新口径：沿当前所选成片版本的 ``ProductionRunEpisode.delivery_order_id`` 读
``DeliveryOrder.edl_payload``，按真实剪辑顺序与 ``in_s``/``out_s`` 计算时码；
换版只跟随指针，重新装配仍复用原镜头产物。
"""

from __future__ import annotations

import pytest

from shine.director.contracts import EpisodePlanPayload, digest_of
from shine.director.models import DirectorEpisodePlan
from tests.conftest import create_project, create_shot, freeze_style

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}

#: 六镜成片里每一镜真实的出入点（与交付单 1a678d1e… 的剪辑表一致）。
REAL_CLIPS = [(0.0, 10.124), (0.0, 10.124), (0.0, 10.124), (0.0, 10.124),
              (0.0, 9.416), (0.0, 8.706)]
PLANNED = 9.8


def _setup(client, factory, durations: list[float] | None = None) -> dict:
    durations = durations or [PLANNED] * len(REAL_CLIPS)
    project_id = create_project(client, "字幕跟随剪辑")
    style = freeze_style(client, project_id)
    shots = [create_shot(client, project_id, style["id"], title=f"第{i + 1}镜",
                         duration=durations[i]) for i in range(len(durations))]
    plan = EpisodePlanPayload.model_validate({
        "mode": "shots", "target_seconds": 60, "tolerance_seconds": 20,
        "episodes": [{"episode_id": "e1", "order": 1, "title": "第 1 集",
                      "shot_ids": [row["id"] for row in shots],
                      "planned_seconds": sum(durations)}],
    })
    with factory.begin() as db:
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=20,
            payload=plan.model_dump(mode="json"),
            sha256=digest_of(plan.model_dump(mode="json")), source="supplied",
        ))
    return {"project_id": project_id, "shots": shots}


def _freeze(client, project_id: str, shot_id: str, lines: list[dict]) -> None:
    response = client.put(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot_id}/subtitles",
        headers=LOCAL, json={"lines": lines, "freeze": True})
    assert response.status_code == 200, response.text


def _episode_with_orders(factory, project_id: str, shot_ids: list[str], *,
                         episode_id: str = "e1", cuts: list[list[tuple[float, float]]] | None = None,
                         selected: int | None = 0) -> dict:
    """A run episode whose delivery pointer names one real delivered cut."""
    from shine.director.models import DirectorPackageVersion
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.models import ProductionRun, ProductionRunEpisode

    cuts = cuts or [REAL_CLIPS]
    with factory.begin() as db:
        package = DirectorPackageVersion(
            project_id=project_id, revision=1, manifest={}, scope_digest="a" * 64,
            manifest_sha256="b" * 64, policy_hash="c" * 64,
        )
        db.add(package)
        db.flush()
        run = ProductionRun(
            project_id=project_id, package_id=package.id, package_revision=1,
            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
            idempotency_key="run-subtitles", request_hash="d" * 64, status="RUNNING",
        )
        db.add(run)
        db.flush()
        episode = ProductionRunEpisode(
            run_id=run.id, project_id=project_id, episode_id=episode_id, position=1,
            manifest_sha256="e" * 64, shot_ids=list(shot_ids), status="DELIVERED",
        )
        db.add(episode)
        db.flush()
        orders = []
        for index, cut in enumerate(cuts):
            order = DeliveryOrder(
                project_id=project_id, series_id=None, scope_kind="episode",
                scope_id=episode.id, origin="run_episode", version=index + 1,
                status="DELIVERED", artifact_id=f"artifact-{index + 1}",
                edl_payload={"schema_version": "1.1", "clips": [
                    {"job_id": f"job-{position}", "attempt_id": f"attempt-{position}",
                     "artifact_id": f"artifact-{position}", "shot_id": shot_id,
                     "in_s": in_s, "out_s": out_s, "transition": "CUT", "reason": "test"}
                    for position, (shot_id, (in_s, out_s)) in enumerate(zip(shot_ids, cut, strict=True))
                ]},
            )
            db.add(order)
            orders.append(order)
        db.flush()
        episode.delivery_order_id = orders[selected].id if selected is not None else None
        return {"run_id": run.id, "episode_id": episode.id,
                "order_ids": [row.id for row in orders]}


def _compile(client, project_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/episodes/e1/subtitles/compile",
        headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def test_subtitles_follow_the_real_cut_instead_of_the_planned_lengths(api):
    client, factory, _settings = api
    built = _setup(client, factory)
    project_id, shots = built["project_id"], built["shots"]
    _episode_with_orders(factory, project_id, [row["id"] for row in shots])
    _freeze(client, project_id, shots[-1]["id"],
            [{"start_s": 0.0, "end_s": 2.0, "text": "钟背刻着的，是母亲的名字。"}])

    body = _compile(client, project_id)
    assert body["basis"] == "actual"
    assert body["cut_version"] == 1
    # 六镜成片：前四镜各 10.124s，第五镜 9.416s，第 6 镜真实起点 49.912s。
    assert body["cues"][0]["start_s"] == 49.912
    assert body["cues"][0]["end_s"] == 51.912
    assert not any("真实成片时长" in warning for warning in body["warnings"])


def test_a_trimmed_clip_start_moves_its_lines_and_drops_the_cut_head(api):
    client, factory, _settings = api
    built = _setup(client, factory, durations=[6.0, 5.0])
    project_id, shots = built["project_id"], built["shots"]
    _episode_with_orders(factory, project_id, [row["id"] for row in shots],
                         cuts=[[(0.0, 6.0), (1.0, 6.0)]])
    _freeze(client, project_id, shots[0]["id"], [{"start_s": 0.0, "end_s": 1.0, "text": "第一镜"}])
    _freeze(client, project_id, shots[1]["id"], [
        {"start_s": 0.0, "end_s": 0.8, "text": "被裁掉的片头"},
        {"start_s": 1.5, "end_s": 3.0, "text": "切点之后"},
    ])

    body = _compile(client, project_id)
    assert body["basis"] == "actual"
    starts = [cue["start_s"] for cue in body["cues"]]
    # 第二镜从成片 6.000s 开始，但片头 1s 被裁掉：1.5s 的字幕落在 6.5s。
    assert starts == [0.0, 6.5]
    assert body["cues"][1]["text"] == "切点之后"


def test_dialogue_generates_the_track_without_manual_authoring(api):
    """⑦ 的唯一生成入口读镜头对白这一唯一事实，不要求先回③手写冻结。"""
    client, factory, _settings = api
    built = _setup(client, factory, durations=[6.0, 5.0])
    project_id, shots = built["project_id"], built["shots"]
    _episode_with_orders(factory, project_id, [row["id"] for row in shots],
                         cuts=[[(0.0, 6.0), (1.0, 6.0)]])
    from shine.models import Shot

    with factory.begin() as db:
        first = db.get(Shot, shots[0]["id"])
        first.spec = {**first.spec, "characters": ["甲"], "dialogue": [
            {"speaker_id": "甲", "text": "殿下，醒醒……", "start_s": 0.8, "end_s": 2.1},
        ]}
        second = db.get(Shot, shots[1]["id"])
        second.spec = {**second.spec, "characters": ["甲"], "dialogue": [
            {"speaker_id": "甲", "text": "被裁掉的片头", "start_s": 0.0, "end_s": 0.8},
            {"speaker_id": "甲", "text": "请宣布裁决吧。", "start_s": 1.5, "end_s": 3.0},
        ]}

    body = _compile(client, project_id)
    assert body["basis"] == "actual"
    assert body["dialogue_shots"] == 2
    assert body["manual_shots"] == 0
    # 对白时码与人工字幕同一套换算：裁掉的片头不出现，切点后的行从片段起点排。
    assert [(cue["start_s"], cue["text"]) for cue in body["cues"]] == [
        (0.8, "殿下，醒醒……"), (6.5, "请宣布裁决吧。")]


def test_the_reel_scope_wins_over_a_live_table_with_newer_shots(api):
    """实障复现：③ 实时镜表已应用 15 镜，交付成片只有 9 镜。

    字幕范围必须跟随所选成片版本的镜头，而不是实时镜表——否则时码
    回落到计划时长，排到成片之外。
    """
    client, factory, _settings = api
    built = _setup(client, factory, durations=[6.0, 5.0, 4.0])
    project_id, shots = built["project_id"], built["shots"]
    # 交付只含前两镜；第三镜是交付后新应用的镜头，只存在于实时镜表。
    _episode_with_orders(factory, project_id, [row["id"] for row in shots[:2]],
                         cuts=[[(0.0, 6.0), (0.0, 5.0)]])
    from shine.models import Shot

    with factory.begin() as db:
        for shot, text in ((shots[0], "第一镜对白"), (shots[2], "成片之外的对白")):
            row = db.get(Shot, shot["id"])
            row.spec = {**row.spec, "characters": ["甲"], "dialogue": [
                {"speaker_id": "甲", "text": text, "start_s": 0.5, "end_s": 2.0}]}

    body = _compile(client, project_id)
    assert body["basis"] == "actual"
    assert [cue["text"] for cue in body["cues"]] == ["第一镜对白"]
    assert body["cues"][0]["end_s"] <= 11.0


def test_manual_frozen_lines_override_the_dialogue_of_that_shot(api):
    client, factory, _settings = api
    built = _setup(client, factory, durations=[6.0, 5.0])
    project_id, shots = built["project_id"], built["shots"]
    _episode_with_orders(factory, project_id, [row["id"] for row in shots],
                         cuts=[[(0.0, 6.0), (0.0, 5.0)]])
    from shine.models import Shot

    with factory.begin() as db:
        for shot, text in ((shots[0], "对白原句"), (shots[1], "第二镜对白")):
            row = db.get(Shot, shot["id"])
            row.spec = {**row.spec, "characters": ["甲"], "dialogue": [
                {"speaker_id": "甲", "text": text, "start_s": 0.5, "end_s": 2.0}]}
    _freeze(client, project_id, shots[0]["id"],
            [{"start_s": 0.0, "end_s": 1.0, "text": "人工改写的字"}])

    body = _compile(client, project_id)
    assert body["manual_shots"] == 1
    assert body["dialogue_shots"] == 1
    assert [cue["text"] for cue in body["cues"]] == ["人工改写的字", "第二镜对白"]


def test_a_recut_keeps_subtitles_on_the_selected_version(api):
    client, factory, _settings = api
    built = _setup(client, factory)
    project_id, shots = built["project_id"], built["shots"]
    recut = [(0.0, 9.0)] * 6
    created = _episode_with_orders(factory, project_id, [row["id"] for row in shots],
                                   cuts=[REAL_CLIPS, recut], selected=0)
    _freeze(client, project_id, shots[-1]["id"], [{"start_s": 0.0, "end_s": 2.0, "text": "结尾"}])

    first = _compile(client, project_id)
    assert first["cue_count"] == 1
    assert first["cues"][0]["start_s"] == 49.912
    assert first["cut_order_id"] == created["order_ids"][0]

    # 同集重剪出一个新版本并把指针移过去：字幕跟随所选版本，装配也没重跑。
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.models import ProductionRunEpisode

    with factory.begin() as db:
        episode = db.get(ProductionRunEpisode, created["episode_id"])
        episode.delivery_order_id = created["order_ids"][1]
        orders = db.query(DeliveryOrder).filter(
            DeliveryOrder.project_id == project_id).all()
        order_count = len(orders)

    second = _compile(client, project_id)
    assert second["cut_order_id"] == created["order_ids"][1]
    assert second["cut_version"] == 2
    assert second["cues"][0]["start_s"] == 45.0
    with factory() as db:
        assert len(db.query(DeliveryOrder).filter(
            DeliveryOrder.project_id == project_id).all()) == order_count, \
            "重新装配字幕不新建交付单，也不重新渲染镜头"


def test_episode_scoped_subtitle_passes_assembly_match_against_episode_key(api):
    """分集字幕按 episode_id（如 e1）生成，装配校验不得拿分集行 UUID 去比对而误报 mismatch。"""
    import hashlib
    from uuid import uuid4

    from shine.director.subtitles import track_matches_episode
    from shine.models import (
        Job,
        JobAttempt,
        MediaArtifact,
        ProductionSeries,
        ProductionSeriesItem,
        QualityGate,
        SubtitleTrack,
    )
    from shine.production.episode_delivery import run_episode_delivery
    from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
    from tests.conftest import make_test_video_bytes

    client, factory, settings = api
    built = _setup(client, factory, durations=[4.0, 4.0])
    project_id, shots = built["project_id"], built["shots"]
    created = _episode_with_orders(factory, project_id, [row["id"] for row in shots],
                                   cuts=[[(0.0, 4.0), (0.0, 4.0)]])
    _freeze(client, project_id, shots[0]["id"], [{"start_s": 0.0, "end_s": 1.0, "text": "第一句"}])
    compiled = _compile(client, project_id)
    rev = compiled["revision"]
    approved = client.post(f"/api/v1/projects/{project_id}/subtitles/{rev}/approve", headers=LOCAL)
    assert approved.status_code == 200, approved.text

    with factory() as db:
        ep = db.get(ProductionRunEpisode, created["episode_id"])
        track = db.query(SubtitleTrack).filter(
            SubtitleTrack.project_id == project_id, SubtitleTrack.revision == rev,
        ).one()
        assert ep.id != ep.episode_id
        assert track.episode_id == "e1"
        assert track_matches_episode(track, ep.episode_id)

    mp4 = make_test_video_bytes(duration_s=4.0, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(mp4).hexdigest()
    with factory.begin() as db:
        run = db.query(ProductionRun).filter(ProductionRun.project_id == project_id).one()
        run_id = run.id
        series = ProductionSeries(project_id=project_id, provider="comfyui", status="COMPLETED")
        db.add(series)
        db.flush()
        series_id = series.id
        db.add(ProductionRunBatch(
            run_id=run_id, episode_row_id=created["episode_id"], episode_id="e1",
            batch_key="e1-b01", position=0, input_digest="f" * 64,
            shot_ids=[s["id"] for s in shots], status="PASSED", series_id=series.id,
        ))
        from shine.models import StyleVersion, utcnow

        style_id = db.query(StyleVersion.id).filter(StyleVersion.project_id == project_id).scalar()
        now = utcnow()
        for idx, shot in enumerate(shots):
            job_id, att_id, art_id = uuid4().hex, uuid4().hex, uuid4().hex
            rel = f"artifacts/ep_sub_{idx}.mp4"
            p = settings.data_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(mp4)
            job = Job(id=job_id, project_id=project_id, shot_id=shot["id"], shot_revision=1,
                      style_version_id=style_id, request={},
                      idempotency_key=f"sub-burn-{idx}", status="PASSED",
                      quality_verification="real", provider="comfyui", generation_mode="Ref2VA",
                      artifact_id=art_id)
            db.add(job)
            db.flush()
            db.add(JobAttempt(id=att_id, job_id=job_id, attempt_no=1,
                              request_fingerprint="a" * 64, status="COMPLETED",
                              lease_owner="test-worker", lease_expires_at=now,
                              output_path=rel, output_sha256=digest, output_bytes=len(mp4)))
            db.add(MediaArtifact(id=art_id, project_id=project_id, job_id=job_id, attempt_id=att_id,
                                 artifact_kind="video", storage_path=rel, sha256=digest,
                                 byte_size=len(mp4), content_type="video/mp4", is_mock=False,
                                 provenance={"duration_s": 4.0}))
            db.add(QualityGate(job_id=job_id, attempt_id=att_id, artifact_id=art_id,
                               gate_name="technical", status="passed", is_mock=False))
            db.add(ProductionSeriesItem(series_id=series.id, position=idx,
                                        shot_id=shot["id"], job_id=job_id, status="PASSED"))

    from shine.media.delivery_models import DeliveryOrder

    with factory.begin() as db:
        track = db.query(SubtitleTrack).filter(
            SubtitleTrack.project_id == project_id, SubtitleTrack.revision == rev,
        ).one()
        order = DeliveryOrder(
            project_id=project_id, series_id=series_id, scope_kind="episode",
            scope_id=created["episode_id"], origin="run_episode", version=99,
            status="PENDING", input_digest="a" * 64, shots_digest="b" * 64,
            subtitle_revision=rev, subtitle_sha256=track.sha256,
        )
        db.add(order)
        db.flush()
        order_id = order.id

    res = run_episode_delivery(
        factory, order_id, settings=settings, asset_root=settings.data_dir, worker_id="test-worker",
    )
    assert res == "DELIVERED"
    with factory() as db:
        order = db.get(DeliveryOrder, order_id)
        art = db.get(MediaArtifact, order.artifact_id)
        assert art is not None and art.provenance.get("subtitles_composited") is True
        assert art.provenance.get("subtitles_sha256") == track.sha256
        from shine.media.delivery_engine import delivery_order_public

        pub = delivery_order_public(order)
        assert pub is not None and pub["status"] == "DELIVERED" and pub["percent"] == 100

    # 验证装配进度投影：ASSEMBLING 期间读写同一张 DeliveryOrder 台账并在 overview 露出。
    from shine.media.delivery_engine import DeliveryClaim, report_assembly_progress

    with factory.begin() as db:
        prog_order = DeliveryOrder(
            project_id=project_id, series_id=series_id, scope_kind="episode",
            scope_id=created["episode_id"], origin="run_episode", version=100,
            status="ASSEMBLING", worker_id="w-prog", state_version=3,
            subtitle_revision=rev, subtitle_sha256=track.sha256,
        )
        db.add(prog_order)
        db.flush()
        prog_id = prog_order.id
        ep_row = db.get(ProductionRunEpisode, created["episode_id"])
        ep_row.delivery_order_id = prog_id
    report_assembly_progress(
        factory, prog_id, DeliveryClaim(owner="w-prog", version=3),
        stage="burning_subtitles", percent=72, detail="正在烧录字幕（36/50 秒）",
    )
    ov = client.get(f"/api/v1/projects/{project_id}/director/overview")
    assert ov.status_code == 200, ov.text
    asm = ov.json()["episodes"][0]["delivery"]["assembly"]
    assert asm["order_id"] == prog_id
    assert asm["status"] == "ASSEMBLING"
    assert asm["stage"] == "burning_subtitles"
    assert asm["percent"] == 72
    assert "36/50" in asm["detail"]

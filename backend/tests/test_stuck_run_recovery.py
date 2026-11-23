"""中断拍摄的恢复不得活锁，且必须把人工决定面露出到台账。

实障（2026-11-21 用户报障）：工作栈在拍摄中途被关闭后重启，Worker 每一拍都把
「重新阻塞一个已经阻塞的 job」记成恢复进展（同一 job 上万条重复审计），tick 永远
停在 reconciled，series/run 执行器拿不到拍子：run 永远显示 RUNNING，⑥ 页没有
任何可操作的恢复入口。修复收敛为「检查过 ≠ 有进展」：阻塞结论已记录且无变化时
恢复返回 0，状态机照常推进到 NEEDS_REVIEW，顶栏裁决携带卡住作品的导航事实。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from shine.comfyops.models import ResourceLease
from shine.models import (
    AppSetting,
    AuditEvent,
    ExternalJobReconciliation,
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    QualityGate,
    utcnow,
)
from shine.production.models import ProductionRun
from shine.production_mode import summarize_production_mode
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes
from tests.test_director_long_form import LOCAL, _advance, _confirm_package, _plan_and_package, _studio


def _uncertain_job(factory, project_id, shot_id, style_id):
    """一个已 ACK 但渲染器随旧栈死去的提交：无产物、租约过期、激活日志在案。"""
    with factory.begin() as db:
        job = Job(project_id=project_id, shot_id=shot_id, shot_revision=1,
                  style_version_id=style_id, request={}, idempotency_key=uuid4().hex,
                  status="SUBMITTING", provider="comfyui", generation_mode="Ref2VA",
                  lease_owner="dead-worker", lease_expires_at=utcnow() - timedelta(seconds=1))
        db.add(job)
        db.flush()
        attempt = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint="a" * 64,
                             status="ACKNOWLEDGED", provider_job_id="prompt-1",
                             prompt_graph_sha256="c" * 64, lease_owner="dead-worker",
                             lease_expires_at=utcnow() - timedelta(seconds=1))
        db.add(attempt)
        db.flush()
        lease = ResourceLease(owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
                              status="ACTIVE", expires_at=utcnow() - timedelta(seconds=1))
        db.add(lease)
        db.flush()
        db.add(AppSetting(key="renderer-activation:" + attempt.id, value={
            "stage": "CONFIRMED", "job_id": job.id, "attempt_id": attempt.id, "lease_id": lease.id,
            "instance_id": "inst-dead", "worker_id": "dead-worker",
            "process": {"pid": 99999999, "birth": "1"},
        }))
        return job.id, attempt.id, lease.id


class NoRendererCalls:
    def __getattr__(self, _name):
        raise AssertionError("uncertain handoff must not contact or restart a renderer")


def _block_audits(factory, job_id):
    with factory() as db:
        return db.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.entity_id == job_id,
            AuditEvent.event_type == "COMFY_HANDOFF_RECOVERY_BLOCKED")) or 0


def test_reasserting_a_recorded_block_is_not_progress(api):
    from shine.worker import recover_expired_jobs

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id, _attempt_id, lease_id = _uncertain_job(factory, project, shot["id"], style["id"])

    first = recover_expired_jobs(factory, "w1", settings=settings, comfy_client=NoRendererCalls())
    assert first == 1
    with factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "NEEDS_REVIEW"
        assert job.failure_code == "COMFY_ACTIVATION_RECONCILIATION_REQUIRED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
    assert _block_audits(factory, job_id) == 1

    # 第二拍及以后：结论已记录，核对是检查不是进展，不得再计数、再写审计。
    for _ in range(3):
        assert recover_expired_jobs(factory, "w1", settings=settings,
                                    comfy_client=NoRendererCalls()) == 0
    assert _block_audits(factory, job_id) == 1


def test_a_stuck_run_reaches_the_human_decision_state(api, monkeypatch):
    """真实 Worker tick：恢复让位后，series/run 必须推进到 NEEDS_REVIEW。"""
    from shine import worker
    from shine.quality import runtime

    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=2)
    _confirm_package(client, package["id"])
    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    _advance(factory, settings, run_id)

    with factory() as db:
        series = db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).first()
        assert series is not None
        items = sorted(series.items, key=lambda row: row.position)
        shot_ids = [item.shot_id for item in items]
    job_id, attempt_id, lease_id = _uncertain_job(factory, studio["project"], shot_ids[0],
                                                 studio["style"]["id"])
    with factory.begin() as db:
        series = db.get(ProductionSeries, series.id)
        series.provider = "comfyui"
        series.status = "RUNNING"
        series.worker_id = "dead-worker"
        series.lease_expires_at = None
        series.lease_id = lease_id
        series.current_job_id = job_id
        items = sorted(series.items, key=lambda row: row.position)
        items[0].job_id = job_id
        items[0].status = "RUNNING"
        # 第二镜依赖第一镜：第一镜待人工决定时它也只能等，不会绕过去偷拍。
        items[1].continuity_mode = "last_frame"

    monkeypatch.setattr(runtime, "configured_reviewers", lambda *a, **k: (None, None))
    # 只替换渲染边界：recipe 形状合法但普通队列无任务可领，不会真的启动渲染器。
    monkeypatch.setattr(worker, "_managed_recipe",
                        lambda *a, **k: {"acceptance": "gate-only",
                                         "renderer": {"instance_id": "inst-dead"}})
    real = replace(settings, render_provider="comfyui")
    # 与 _advance 同一 worker 身份：run 的 claim 租约内允许续租，不允许抢_claim。
    results = [worker._run_configured_once(factory, None, "test-worker", settings=real)
               for _ in range(6)]

    assert "reconciled" in results, "第一拍应记录阻塞结论"
    assert any(str(r).startswith("series:") for r in results), (
        f"恢复让位后 series 执行器必须拿到拍子，实际 tick 结果：{results}")
    with factory() as db:
        assert db.get(ProductionSeries, series.id).status == "NEEDS_REVIEW", results
        run = db.get(ProductionRun, run_id)
        assert run.status == "NEEDS_REVIEW", f"run 必须露出人工决定面：{results}"
        assert run.blocker_count >= 1
        mode = summarize_production_mode(db, settings, effective=None, runtime_error=None,
                                         worker_online=True, renderer_online=False)
    assert mode["mode"] == "RECONCILE_REQUIRED"
    assert mode["focus"] == {"project_id": studio["project"], "run_id": run_id}
    assert _block_audits(factory, job_id) == 1, "重复核对不得再刷审计"


def test_without_unreconciled_leases_the_verdict_has_no_focus(api):
    client, factory, settings = api
    with factory() as db:
        mode = summarize_production_mode(db, settings, effective=None, runtime_error=None,
                                         worker_online=True, renderer_online=False)
    assert mode["focus"] is None

def _pass_job(factory, settings, job_id):
    """给既有 job 落一份真实测试字节的通过版（技术门齐全，非 mock）。"""
    content = make_test_video_bytes(duration_s=2.0, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(bytes(content)).hexdigest()
    relative = f"artifacts/{job_id}/retake-{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with factory.begin() as db:
        job = db.get(Job, job_id)
        attempt = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint=digest,
                             status="COMPLETED", lease_owner="test-worker",
                             lease_expires_at=utcnow(), output_path=relative,
                             output_sha256=digest, output_bytes=len(content))
        db.add(attempt)
        db.flush()
        artifact = MediaArtifact(project_id=job.project_id, job_id=job.id, attempt_id=attempt.id,
                                 artifact_kind="video", storage_path=relative, sha256=digest,
                                 byte_size=len(content), content_type="video/mp4", is_mock=False)
        db.add(artifact)
        db.flush()
        for name in ("semantic", "audio", "style", "final"):
            db.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                               gate_name=name, status="passed", is_mock=False))
        db.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                           gate_name="technical", status="passed", is_mock=False,
                           model="ffprobe", coverage="covered"))
        job.artifact_id = artifact.id
        job.status = "PASSED"
        job.quality_verification = "real"


def test_abandoned_shot_retake_selection_finishes_the_run(api):
    """放弃→⑦重拍→选用 之后，既有执行者必须能把整部运行收敛完成。"""
    from shine.comfyops.production_series import reconcile_series

    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    _advance(factory, settings, run_id)
    with factory() as db:
        series = db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).first()
        series_id = series.id
        shot_id = series.items[0].shot_id
    # 镜头归属本序列：用序列自己的建 job 执行者造源任务（独立入口会被归属裁决拒绝）。
    from shine.comfyops.production_series import _create_item_job
    with factory() as db:
        item_id = db.get(ProductionSeries, series_id).items[0].id
    source_id = _create_item_job(factory, series_id, item_id, settings=settings, offline=True)
    with factory.begin() as db:
        job = db.get(Job, source_id)
        job.status = "FAILED"
        job.failure_code = "USER_ABANDONED_UNRECONCILED"
        job.provider_job_id = "prompt-old"
        job.lease_owner = None
        job.lease_expires_at = None
        db.add(ExternalJobReconciliation(job_id=job.id, provider_job_id="prompt-old",
                                         action="user_abandon", result="abandoned",
                                         evidence={"resubmitted": False}))
        lease = ResourceLease(owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
                              status="RELEASED", expires_at=utcnow() - timedelta(seconds=1))
        db.add(lease)
        db.flush()
        series = db.get(ProductionSeries, series_id)
        series.provider = "comfyui"
        series.status = "NEEDS_REVIEW"
        series.failure_code = "USER_ABANDONED_UNRECONCILED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = lease.id
        series.items[0].job_id = job.id
        series.items[0].status = "BLOCKED"
    _advance(factory, settings, run_id)
    with factory() as db:
        assert db.get(ProductionRun, run_id).status == "NEEDS_REVIEW"

    rows = client.get(f"/api/v1/projects/{studio['project']}/generation-segments",
                      params={"run_id": run_id}).json()["segments"]
    row = next(r for r in rows if r["shot_id"] == shot_id)
    assert row["can_regenerate"] is True, row["blocker"]

    retake = client.post(f"/api/v1/projects/{studio['project']}/generation-segments/{shot_id}/regenerate",
                         json={"source_job_id": source_id,
                               "expected_shot_revision": row["shot_revision"]}, headers=LOCAL)
    assert retake.status_code == 202, retake.text
    retake_id = retake.json()["job_id"]
    assert retake_id
    _pass_job(factory, settings, retake_id)

    selected = client.post(f"/api/v1/projects/{studio['project']}/generation-segments/{series_id}/select",
                           json={"source_job_id": source_id, "replacement_job_id": retake_id},
                           headers=LOCAL)
    assert selected.status_code == 202, selected.text
    with factory() as db:
        assert db.get(ProductionSeries, series_id).items[0].job_id == retake_id

    # 既有恢复执行者：租约已 RELEASED + 全镜通过 → 结论合并，序列完成。
    reconcile_series(factory, "test-worker")
    with factory() as db:
        assert db.get(ProductionSeries, series_id).status == "COMPLETED"
    # NEEDS_REVIEW 的运行等待人工恢复：⑥ 的「继续安全可恢复批次」既有入口。
    resumed = client.post(f"/api/v1/production-runs/{run_id}/resume", json={}, headers=LOCAL)
    assert resumed.status_code in (200, 202), resumed.text
    _advance(factory, settings, run_id)
    with factory() as db:
        from shine.production.models import ProductionRunEpisode
        episode = db.scalars(select(ProductionRunEpisode).where(
            ProductionRunEpisode.run_id == run_id)).first()
        assert episode.status != "BLOCKED"
        assert episode.delivery_order_id, "选用完成后必须创建分集交付单"


def test_resume_run_continues_remaining_pending_shots_after_abandoned_shot(api):
    """复现最终截图：停栈→放弃第1镜未知结果→第2镜仍 PENDING，点「继续安全可恢复批次」必须恢复开拍。"""
    from shine.models import ProductionSeriesItem
    from shine.production.models import ProductionRunBatch, ProductionRunEpisode
    from shine.strategy import GenerationStrategy

    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=2)
    # 模拟新七阶段动态策略导演包（workflow_profile_id=None，frozen strategy 存在）。
    with factory.begin() as db:
        from shine.director.contracts import digest_of
        from shine.director.models import DirectorPackageVersion

        pkg = db.get(DirectorPackageVersion, package["id"])
        manifest = dict(pkg.manifest)
        binding = dict(manifest.get("binding") or {})
        strat = GenerationStrategy()
        binding["workflow_profile_id"] = None
        binding["strategy"] = strat.model_dump(mode="json")
        binding["strategy_sha256"] = strat.strategy_sha256()
        manifest["binding"] = binding
        pkg.manifest = manifest
        pkg.manifest_sha256 = digest_of(manifest)
    _confirm_package(client, package["id"])
    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    _advance(factory, settings, run_id)

    with factory() as db:
        series = db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).first()
        series_id = series.id
        items = db.scalars(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series_id).order_by(ProductionSeriesItem.position)).all()
        assert len(items) == 2
        first_item_id, second_item_id = items[0].id, items[1].id

    from shine.comfyops.production_series import _create_item_job

    source_id = _create_item_job(factory, series_id, first_item_id, settings=settings, offline=True)
    with factory.begin() as db:
        job = db.get(Job, source_id)
        job.status = "FAILED"
        job.failure_code = "USER_ABANDONED_UNRECONCILED"
        job.provider_job_id = "prompt-old"
        job.lease_owner = None
        job.lease_expires_at = None
        db.add(ExternalJobReconciliation(job_id=job.id, provider_job_id="prompt-old",
                                         action="user_abandon", result="abandoned",
                                         evidence={"resubmitted": False}))
        lease = ResourceLease(owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
                              status="RELEASED", expires_at=utcnow() - timedelta(seconds=1))
        db.add(lease)
        db.flush()
        series = db.get(ProductionSeries, series_id)
        series.provider = "comfyui"
        series.workflow_profile_id = None
        series.status = "NEEDS_REVIEW"
        series.failure_code = "USER_ABANDONED_UNRECONCILED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = lease.id
        first_item = db.get(ProductionSeriesItem, first_item_id)
        first_item.job_id = job.id
        first_item.status = "BLOCKED"
        second_item = db.get(ProductionSeriesItem, second_item_id)
        assert second_item.job_id is None and second_item.status == "PENDING"
    _advance(factory, settings, run_id)
    with factory() as db:
        assert db.get(ProductionRun, run_id).status == "NEEDS_REVIEW"

    resumed = client.post(f"/api/v1/production-runs/{run_id}/resume", json={}, headers=LOCAL)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "RUNNING", resumed.json()
    with factory() as db:
        assert db.get(ProductionSeries, series_id).status == "QUEUED"
        assert db.get(ProductionRunBatch, db.scalar(
            select(ProductionRunBatch.id).where(ProductionRunBatch.run_id == run_id))).status == "QUEUED"
        assert db.get(ProductionRunEpisode, db.scalar(
            select(ProductionRunEpisode.id).where(ProductionRunEpisode.run_id == run_id))).status == "PRODUCING"


def test_resume_run_explains_when_no_runnable_shot_remains(api):
    """无剩余可独立续拍镜头时，resume 必须诚实保留 NEEDS_REVIEW 并给出中文说明。"""
    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    run_id = created.json()["id"]
    _advance(factory, settings, run_id)
    with factory() as db:
        series = db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).first()
        series_id = series.id
        item_id = series.items[0].id
    from shine.comfyops.production_series import _create_item_job

    source_id = _create_item_job(factory, series_id, item_id, settings=settings, offline=True)
    with factory.begin() as db:
        job = db.get(Job, source_id)
        job.status = "FAILED"
        job.failure_code = "USER_ABANDONED_UNRECONCILED"
        job.provider_job_id = "prompt-old"
        job.lease_owner = None
        job.lease_expires_at = None
        db.add(ExternalJobReconciliation(job_id=job.id, provider_job_id="prompt-old",
                                         action="user_abandon", result="abandoned",
                                         evidence={"resubmitted": False}))
        series = db.get(ProductionSeries, series_id)
        series.status = "NEEDS_REVIEW"
        series.failure_code = "USER_ABANDONED_UNRECONCILED"
        series.items[0].job_id = job.id
        series.items[0].status = "BLOCKED"
    _advance(factory, settings, run_id)

    resumed = client.post(f"/api/v1/production-runs/{run_id}/resume", json={}, headers=LOCAL)
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "NEEDS_REVIEW"
    assert "重拍" in resumed.json().get("message", "")


def _seed_superseded_pre_submit_series(factory, project_id, package_id, shot_id, style_id):
    """构造上一轮 NEEDS_REVIEW 运行遗留的未开拍排队序列（0 attempt，暂停排队中）。"""
    from shine.models import ProductionSeriesItem
    from shine.production.models import ProductionRunBatch, ProductionRunEpisode

    with factory.begin() as db:
        prior_run = ProductionRun(
            project_id=project_id, package_id=package_id, package_revision=1,
            manifest_sha256="a" * 64, scope_digest="b" * 64, policy_hash="c" * 64,
            scope={"episode_ids": ["ep-01"], "shot_count": 1},
            budget={}, counters={}, status="NEEDS_REVIEW", control_reason="SERIES_INCOMPLETE",
            idempotency_key=uuid4().hex, request_hash=uuid4().hex[:64],
        )
        db.add(prior_run)
        db.flush()
        ep = ProductionRunEpisode(
            run_id=prior_run.id, project_id=project_id, episode_id="ep-01",
            position=0, title="第1集", manifest_sha256="d" * 64,
            shot_ids=[shot_id], status="BLOCKED", blocker_code="SERIES_INCOMPLETE",
        )
        db.add(ep)
        db.flush()
        job = Job(
            project_id=project_id, shot_id=shot_id, shot_revision=1,
            style_version_id=style_id, request={}, idempotency_key=uuid4().hex,
            status="QUEUED", paused=True, provider="comfyui", generation_mode="Ref2VA",
        )
        db.add(job)
        db.flush()
        series = ProductionSeries(
            project_id=project_id, provider="comfyui", status="NEEDS_REVIEW",
            failure_code="SERIES_INCOMPLETE", finalization_state="NONE",
        )
        db.add(series)
        db.flush()
        db.add(ProductionSeriesItem(
            series_id=series.id, position=0, shot_id=shot_id, job_id=job.id, status="QUEUED",
        ))
        db.add(ProductionRunBatch(
            run_id=prior_run.id, episode_row_id=ep.id, episode_id="ep-01",
            batch_key="ep-01-b01", position=0, input_digest=uuid4().hex[:64],
            shot_ids=[shot_id], status="BLOCKED", series_id=series.id,
            failure_code="SERIES_INCOMPLETE",
        ))
        return prior_run.id, series.id, job.id


def test_new_run_settles_superseded_needs_review_pre_submit_series(api):
    """新一轮 run 启动时必须清理已作废 NEEDS_REVIEW run 遗留的未提交 QUEUED 序列。"""
    client, factory, settings = api
    studio = _studio(client, shots=1)
    shot_id = studio["shots"][0]["id"]
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    prior_id, old_series_id, old_job_id = _seed_superseded_pre_submit_series(
        factory, studio["project"], package["id"], shot_id, studio["style"]["id"],
    )

    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    assert run_id != prior_id

    with factory() as db:
        assert db.get(ProductionSeries, old_series_id).status == "CANCELLED"
        assert db.get(Job, old_job_id).stale is True

    _advance(factory, settings, run_id)
    with factory() as db:
        from shine.production.models import ProductionRunBatch

        run = db.get(ProductionRun, run_id)
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id))
        assert batch is not None and batch.series_id is not None and batch.status == "QUEUED"
        assert run.status == "RUNNING"


def test_resume_run_reopens_batch_dispatch_refused_after_superseded_cleared(api):
    """已被遗留排队单挡成 BATCH_DISPATCH_REFUSED 的 run，点恢复可清理旧排队并重新派批。"""
    from shine.production.models import ProductionRunBatch, ProductionRunEpisode

    client, factory, settings = api
    studio = _studio(client, shots=1)
    shot_id = studio["shots"][0]["id"]
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])

    created = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                          json={"package_id": package["id"]}, headers=LOCAL)
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    # 模拟当前 run 的批次在派发前被一笔旧 NEEDS_REVIEW run 的未开拍 QUEUED 占用挡下。
    _prior_id, old_series_id, old_job_id = _seed_superseded_pre_submit_series(
        factory, studio["project"], package["id"], shot_id, studio["style"]["id"],
    )
    _advance(factory, settings, run_id)
    with factory() as db:
        run = db.get(ProductionRun, run_id)
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id))
        ep = db.scalar(select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run_id))
        assert run.status == "NEEDS_REVIEW"
        assert batch.status == "BLOCKED" and batch.series_id is None
        assert batch.failure_code == "BATCH_DISPATCH_REFUSED"
        assert ep.status == "BLOCKED"

    resumed = client.post(f"/api/v1/production-runs/{run_id}/resume", json={}, headers=LOCAL)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "RUNNING", resumed.json()

    with factory() as db:
        assert db.get(ProductionSeries, old_series_id).status == "CANCELLED"
        assert db.get(Job, old_job_id).stale is True
        assert db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id)) is None
        ep = db.scalar(select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run_id))
        assert ep.status == "PLANNED" and ep.blocker_code is None
        assert db.get(ProductionRun, run_id).blocker_count == 0

    _advance(factory, settings, run_id)
    with factory() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id))
        assert batch is not None and batch.series_id is not None and batch.status == "QUEUED"

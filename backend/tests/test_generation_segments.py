"""Scheduling/media-identity fixtures, not real H3 acceptance."""
import hashlib

from shine.director.models import DirectorPackageVersion
from shine.models import (
    AuditEvent,
    ExternalJobReconciliation,
    Job,
    JobAttempt,
    JobDependency,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    QualityGate,
    utcnow,
)
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from tests.conftest import create_job
from tests.test_production_series import _register_ref2va_profile, _studio_with_shots

LOCAL = {"x-shine-local-action": "1"}


def _passed(db, job):
    content = ("synthetic-segment-" + job.id).encode()
    sha = hashlib.sha256(content).hexdigest()
    attempt = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint="0" * 64,
                         status="PASSED", lease_owner="test-fixture", lease_expires_at=utcnow(), output_sha256=sha)
    db.add(attempt)
    db.flush()
    from pathlib import Path
    root = Path(db.get_bind().url.database).parent
    key = f"artifacts/segment-fixture-{job.id}.mp4"
    output = root / key
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    artifact = MediaArtifact(project_id=job.project_id, job_id=job.id, attempt_id=attempt.id,
        artifact_kind="video", storage_path=key, sha256=sha,
        byte_size=len(content), content_type="video/mp4", is_mock=False,
        provenance={"software_test_fixture": True})
    db.add(artifact)
    db.flush()
    job.status = "PASSED"
    job.artifact_id = artifact.id
    for name in ("technical", "semantic", "audio", "style", "final"):
        db.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                          gate_name=name, status="passed", is_mock=False, evidence={"software_test_fixture": True}))
    db.flush()
    return sha


def _three(api):
    client, factory, settings = api
    project, style, shots = _studio_with_shots(client, factory, settings)
    profile = _register_ref2va_profile(client)
    jobs = [create_job(client, project, s["id"], provider="comfyui", workflow_profile_id=profile["id"])
            for s in shots]
    hashes = []
    with factory.begin() as db:
        root = ProductionSeries(project_id=project, status="COMPLETED", provider="comfyui", shot_count=3,
                                completed_count=3, finalization_state="RELEASED", workflow_profile_id=profile["id"])
        db.add(root)
        db.flush()
        for i, j in enumerate(jobs):
            job = db.get(Job, j["id"])
            hashes.append(_passed(db, job))
            db.add(ProductionSeriesItem(series_id=root.id, position=i, shot_id=job.shot_id, job_id=job.id,
                                       status="PASSED", continuity_mode="independent"))
        root_id = root.id
    return client, factory, project, shots, jobs, hashes, root_id


def test_only_second_segment_is_requeued_and_selection_creates_new_delivery(api):
    client, factory, project, shots, jobs, hashes, root = _three(api)
    path = f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate"
    body = {"source_job_id": jobs[1]["id"], "expected_shot_revision": 1}
    first = client.post(path, json=body, headers=LOCAL)
    assert first.status_code == 202, first.text
    child = first.json()["job_id"]
    assert child != jobs[1]["id"]
    assert client.post(path, json=body, headers=LOCAL).json()["job_id"] == child
    with factory() as db:
        assert db.query(Job).count() == 4
        for i in (0, 2):
            original = db.get(Job, jobs[i]["id"])
            assert original.status == "PASSED" and not original.stale
            assert db.get(MediaArtifact, original.artifact_id).sha256 == hashes[i]
    choose = f"/api/v1/projects/{project}/generation-segments/{root}/select"
    selection = {"source_job_id": jobs[1]["id"], "replacement_job_id": child}
    assert client.post(choose, json=selection, headers=LOCAL).status_code == 409
    with factory.begin() as db:
        _passed(db, db.get(Job, child))
        from shine.media.delivery_engine import enqueue_auto_delivery
        assert enqueue_auto_delivery(db, db.get(ProductionSeries, first.json()["series_id"])) is None
    selected = client.post(choose, json=selection, headers=LOCAL)
    assert selected.status_code == 202, selected.text
    result = selected.json()
    assert result["selection"] == {shots[0]["id"]: jobs[0]["id"], shots[1]["id"]: child, shots[2]["id"]: jobs[2]["id"]}
    assert result["series_id"] != root
    assert client.post(choose, json=selection, headers=LOCAL).json()["series_id"] == result["series_id"]
    with factory() as db:
        from shine.media.delivery_models import DeliveryOrder
        assert db.query(DeliveryOrder).count() == 1
        assert db.get(Job, jobs[1]["id"]).status == "PASSED"  # old accepted output retained
        assert db.get(ProductionSeries, root).items[1].job_id == jobs[1]["id"]


def _add_prior_needs_review_job(factory, project, source_job_id, *, proven):
    with factory.begin() as db:
        source = db.get(Job, source_job_id)
        blocker = Job(project_id=project, shot_id=source.shot_id, shot_revision=source.shot_revision,
            style_version_id=source.style_version_id, request=dict(source.request),
            idempotency_key=("c" if proven else "d") * 64, status="NEEDS_REVIEW", provider="comfyui",
            failure_code="PENDING_WORKFLOW_MAPPING" if proven else "EXTERNAL_OUTCOME_UNKNOWN",
            provider_job_id=None if proven else "remote-task-unknown")
        db.add(blocker)
        db.flush()
        attempt = JobAttempt(job_id=blocker.id, attempt_no=1, request_fingerprint="e" * 64,
            status="FAILED" if proven else "NEEDS_REVIEW", failure_code="PENDING_WORKFLOW_MAPPING" if proven
                else "EXTERNAL_OUTCOME_UNKNOWN", lease_owner="historical-worker", lease_expires_at=utcnow(),
            provider_job_id=None if proven else "remote-task-unknown")
        db.add(attempt)
        if proven:
            db.add(AuditEvent(project_id=project, actor="worker", event_type="COMFY_WORKFLOW_MAPPING_PENDING",
                entity_type="job", entity_id=blocker.id, payload={"external_submission": "NOT_SENT_PROVEN"}))
        return blocker.id


def test_proven_pre_submit_mapping_failure_does_not_block_safe_retake(api):
    client, factory, project, shots, jobs, _, _ = _three(api)
    historical_id = _add_prior_needs_review_job(factory, project, jobs[1]["id"], proven=True)
    response = client.post(f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate", json={
        "source_job_id": jobs[1]["id"], "expected_shot_revision": 1,
    }, headers=LOCAL)
    assert response.status_code == 202, response.text
    with factory() as db:
        proof = db.query(ExternalJobReconciliation).filter_by(job_id=historical_id).one()
        assert proof.result == "NOT_SENT_PROVEN"
        assert proof.evidence["external_submission"] == "NOT_SENT_PROVEN"
        assert proof.evidence["resubmitted"] is False


def test_unresolved_unknown_attempt_still_blocks_segment_retake(api):
    client, factory, project, shots, jobs, _, _ = _three(api)
    historical_id = _add_prior_needs_review_job(factory, project, jobs[1]["id"], proven=False)
    response = client.post(f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate", json={
        "source_job_id": jobs[1]["id"], "expected_shot_revision": 1,
    }, headers=LOCAL)
    assert response.status_code == 409
    assert response.json()["detail"] == "GENERATION_RUNNING_OR_RECONCILIATION_REQUIRED"
    with factory() as db:
        assert db.query(ExternalJobReconciliation).filter_by(job_id=historical_id).count() == 0


def test_real_dependency_is_reported_then_invalidated_without_replaying(api):
    client, factory, project, shots, jobs, hashes, root = _three(api)
    with factory.begin() as db:
        db.add(JobDependency(parent_job_id=jobs[1]["id"], child_job_id=jobs[2]["id"], kind="last_frame"))
    path = f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate"
    first = client.post(path, json={"source_job_id": jobs[1]["id"], "expected_shot_revision": 1}, headers=LOCAL)
    assert first.status_code == 202, first.text
    assert first.json()["dependent_job_ids"] == [jobs[2]["id"]]
    with factory.begin() as db:
        _passed(db, db.get(Job, first.json()["job_id"]))
    result = client.post(f"/api/v1/projects/{project}/generation-segments/{root}/select", json={
        "source_job_id": jobs[1]["id"], "replacement_job_id": first.json()["job_id"]}, headers=LOCAL)
    assert result.status_code == 202, result.text
    assert result.json()["assembly_pending_review"] is True
    with factory() as db:
        assert db.get(Job, jobs[2]["id"]).stale
        assert not db.get(Job, jobs[0]["id"]).stale
        assert db.query(Job).count() == 4


def test_generation_segments_follow_latest_run_across_batch_series(api):
    client, factory, project, shots, jobs, _, first_series = _three(api)
    with factory.begin() as db:
        package = DirectorPackageVersion(project_id=project, revision=1, status="CONFIRMED", manifest={},
            scope_digest="a" * 64, manifest_sha256="b" * 64, policy_hash="c" * 64,
            episode_count=2, shot_count=3)
        db.add(package)
        db.flush()
        run = ProductionRun(project_id=project, package_id=package.id, package_revision=1,
            manifest_sha256=package.manifest_sha256, scope_digest=package.scope_digest,
            policy_hash=package.policy_hash, scope={"shot_count": 3}, budget={}, counters={},
            status="COMPLETED", idempotency_key="test-latest-run", request_hash="d" * 64)
        db.add(run)
        db.flush()
        first_episode = ProductionRunEpisode(run_id=run.id, project_id=project, episode_id="ep-01",
            position=0, title="第一集", manifest_sha256="e" * 64,
            shot_ids=[shots[0]["id"], shots[1]["id"]], status="DELIVERED")
        second_episode = ProductionRunEpisode(run_id=run.id, project_id=project, episode_id="ep-02",
            position=1, title="第二集", manifest_sha256="f" * 64,
            shot_ids=[shots[2]["id"]], status="DELIVERED")
        db.add_all([first_episode, second_episode])
        second_series = ProductionSeries(project_id=project, status="COMPLETED", provider="comfyui",
            shot_count=1, completed_count=1, finalization_state="RELEASED")
        db.add(second_series)
        db.flush()
        db.add(ProductionSeriesItem(series_id=second_series.id, position=0, shot_id=shots[2]["id"],
            job_id=jobs[2]["id"], status="PASSED", continuity_mode="independent"))
        db.add_all([
            ProductionRunBatch(run_id=run.id, episode_row_id=first_episode.id, episode_id="ep-01",
                batch_key="ep-01-1", position=0, input_digest="1" * 64,
                shot_ids=[shots[0]["id"], shots[1]["id"]], status="PASSED", series_id=first_series),
            ProductionRunBatch(run_id=run.id, episode_row_id=second_episode.id, episode_id="ep-02",
                batch_key="ep-02-1", position=1, input_digest="2" * 64,
                shot_ids=[shots[2]["id"]], status="PASSED", series_id=second_series.id),
        ])
        run_id = run.id

    response = client.get(f"/api/v1/projects/{project}/generation-segments")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["run_id"] == run_id
    assert result["series_id"] is None
    assert [row["position"] for row in result["segments"]] == [1, 2, 3]
    assert [row["episode_position"] for row in result["segments"]] == [1, 1, 2]
    assert [row["episode_title"] for row in result["segments"]] == ["第一集", "第一集", "第二集"]
    assert [row["series_id"] for row in result["segments"]] == [first_series, first_series, second_series.id]
    assert [row["job_id"] for row in result["segments"]] == [job["id"] for job in jobs]


def test_revision_retake_shoots_the_revised_shot_from_the_retired_take(api):
    """③ 修订（加对白）后，⑦ 的既有重拍入口以退休的通过版为源拍新版本。

    实障：修订把旧 job 标 stale+NEEDS_REVIEW 后，重拍闸门报
    SHOT_REVISION_REPLACED 禁用按钮，单镜入口又自动选窄方案——修订后的
    镜头无处可拍。收敛后同一入口服务「同修订重拍」与「修订后重拍」。
    """
    client, factory, project, shots, jobs, hashes, root = _three(api)
    shot_id = shots[1]["id"]
    live = next(s for s in client.get(f"/api/v1/projects/{project}/shots").json()
                if s["id"] == shot_id)
    spec = live["spec"]
    speaker = (spec.get("characters") or ["CHAR_A"])[0]
    spec["characters"] = list(dict.fromkeys([*spec.get("characters", []), speaker]))
    spec["dialogue"] = [{"speaker_id": speaker, "text": "这不是一个梦？", "start_s": 0.5, "end_s": 2.5}]
    revised = client.post(f"/api/v1/shots/{shot_id}/revise", json=spec, headers=LOCAL)
    assert revised.status_code == 200, revised.text
    assert revised.json()["revision"] == 2

    segments = client.get(f"/api/v1/projects/{project}/generation-segments").json()
    row = next(r for r in segments["segments"] if r["shot_id"] == shot_id)
    assert row["can_regenerate"] is True, row["blocker"]

    retake = client.post(f"/api/v1/projects/{project}/generation-segments/{shot_id}/regenerate",
                         json={"source_job_id": jobs[1]["id"], "expected_shot_revision": 2},
                         headers=LOCAL)
    assert retake.status_code == 202, retake.text
    child = retake.json()["job_id"]
    with factory() as db:
        new_job = db.get(Job, child)
        assert new_job.shot_revision == 2
        assert new_job.request["shot_spec"]["dialogue"][0]["text"] == "这不是一个梦？"
        # 渲染绑定不变：同一风格/规则/方案/主素材，只有镜头规格是用户修订的。
        old = db.get(Job, jobs[1]["id"])
        for key in ("style_hash", "rulepack_hash", "workflow_profile_hash", "reference_manifest"):
            assert old.request.get(key) == new_job.request.get(key)
        assert old.stale and old.artifact_id  # 旧通过版保留为历史
        assert db.get(MediaArtifact, old.artifact_id).sha256 == hashes[1]


def test_episode_current_jobs_overlays_the_assembly_selection(api):
    """选定的重拍必须进入整集 EDL/_digest/交付门禁（同一事实），而非仅run自有。"""
    from shine.models import AppSetting
    from shine.production.runner import episode_current_jobs, run_owned_passed_jobs

    client, factory, project, shots, jobs, hashes, root = _three(api)
    with factory.begin() as db:
        package = DirectorPackageVersion(project_id=project, revision=1, status="CONFIRMED", manifest={},
            scope_digest="a" * 64, manifest_sha256="b" * 64, policy_hash="c" * 64)
        db.add(package)
        db.flush()
        run = ProductionRun(project_id=project, package_id=package.id, package_revision=1,
            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
            status="COMPLETED", idempotency_key="sel-overlay", request_hash="d" * 64)
        db.add(run)
        db.flush()
        episode = ProductionRunEpisode(run_id=run.id, project_id=project, episode_id="ep-01",
            position=0, title="第一集", manifest_sha256="e" * 64,
            shot_ids=[s["id"] for s in shots], status="DELIVERED")
        db.add(episode)
        db.flush()
        db.add(ProductionRunBatch(run_id=run.id, episode_row_id=episode.id, episode_id="ep-01",
            batch_key="ep-01-1", position=0, input_digest="1" * 64,
            shot_ids=[s["id"] for s in shots], status="PASSED", series_id=root))
        # 一个更新的重拍通过版 + 显式装配选择。
        retake = Job(project_id=project, shot_id=shots[1]["id"], shot_revision=1, provider="comfyui",
                     style_version_id=jobs[1]["style_version_id"], request_fingerprint="0" * 64,
                     idempotency_key="retake-overlay",
                     status="QUEUED", lease_owner="t", lease_expires_at=utcnow(), request={})
        db.add(retake)
        db.flush()
        _passed(db, retake)
        db.add(AppSetting(key=f"assembly-selection:{root}",
                          value={shots[1]["id"]: retake.id}))
        run_id, episode_id = run.id, episode.id

    with factory() as db:
        run = db.get(ProductionRun, run_id)
        episode = db.get(ProductionRunEpisode, episode_id)
        owned = run_owned_passed_jobs(db, run_id, [episode])
        current = episode_current_jobs(db, run, episode)
        assert owned[shots[1]["id"]].id == jobs[1]["id"]  # run 自有仍是旧版
        assert current[shots[1]["id"]].id == retake.id    # 当前事实=选定重拍
        assert current[shots[0]["id"]].id == jobs[0]["id"]
        assert current[shots[2]["id"]].id == jobs[2]["id"]


def test_paused_series_and_unrelated_reconcile_lease_do_not_block_retake_of_passed_shot(api):
    """暂停或他镜待对账期间，已完成且本身无未结算任务的镜头仍可排入重拍。"""
    from shine.comfyops.scheduler import ResourceScheduler

    client, factory, project, shots, jobs, _hashes, root = _three(api)
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        other_lease = scheduler.enqueue(db, f"h3:{jobs[2]['id']}", 16384, True, 100)
        other_lease.status = "RECONCILE_REQUIRED"
        active_lease = scheduler.enqueue(db, f"series:{root}", 16384, True, 100)
        active_lease.status = "ACTIVE"
        db.flush()
        series = db.get(ProductionSeries, root)
        series.status = "PAUSED"
        series.lease_id = active_lease.id

    listing = client.get(f"/api/v1/projects/{project}/generation-segments")
    assert listing.status_code == 200, listing.text
    row1 = next(row for row in listing.json()["segments"] if row["shot_id"] == shots[1]["id"])
    assert row1["blocker"] is None
    assert row1["can_regenerate"] is True

    retake = client.post(
        f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate",
        json={"source_job_id": jobs[1]["id"], "expected_shot_revision": 1},
        headers=LOCAL,
    )
    assert retake.status_code == 202, retake.text


def test_last_frame_deferred_retake_and_run_episode_v2_delivery(api):
    """Deferred last_frame retake resolves via series item and assembles Episode v2 across run batches."""
    from shine.media.delivery_models import DeliveryOrder
    from shine.models import Shot
    from shine.schemas import ContinuityMode, ShotSpec

    client, factory, project, shots, jobs, _hashes, root = _three(api)
    with factory.begin() as db:
        shot1 = db.get(Shot, shots[1]["id"])
        spec1 = ShotSpec.model_validate(shot1.spec)
        spec1.continuity = spec1.continuity.model_copy(
            update={"mode": ContinuityMode.LAST_FRAME, "previous_shot_id": shots[0]["id"], "previous_job_id": None}
        )
        shot1.spec = spec1.model_dump(mode="json")

        pkg = DirectorPackageVersion(
            project_id=project, revision=1, status="CONFIRMED", manifest={"policy": {"batch_shot_limit": 5}},
            scope_digest="a" * 64, manifest_sha256="b" * 64, policy_hash="c" * 64,
            episode_count=1, shot_count=3,
        )
        db.add(pkg)
        db.flush()
        run = ProductionRun(
            project_id=project, package_id=pkg.id, package_revision=1,
            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
            status="COMPLETED", idempotency_key="run-ep-v2", request_hash="d" * 64,
        )
        db.add(run)
        db.flush()
        from shine.director.models import DirectorPackageEpisode
        from shine.director.package import digest_of
        ep_manifest = {
            "shots": [
                {
                    "shot_id": s["id"], "name": s["name"], "revision": 1,
                    "spec_sha256": digest_of(s["spec"]),
                    "continuity_mode": "last_frame" if i == 1 else "cut",
                    "bindings": [],
                }
                for i, s in enumerate(shots)
            ]
        }
        ep_sha = digest_of(ep_manifest)
        db.add(DirectorPackageEpisode(
            package_id=pkg.id, project_id=project, episode_id="ep-01", position=0, title="第一集",
            manifest=ep_manifest, manifest_sha256=ep_sha,
        ))
        ep = ProductionRunEpisode(
            run_id=run.id, project_id=project, episode_id="ep-01", position=0, title="第一集",
            manifest_sha256=ep_sha, shot_ids=[s["id"] for s in shots], status="DELIVERED",
        )
        db.add(ep)
        db.flush()
        db.add(ProductionRunBatch(
            run_id=run.id, episode_row_id=ep.id, episode_id="ep-01",
            batch_key="ep-01-b01", position=1, input_digest="1" * 64,
            shot_ids=[s["id"] for s in shots], status="PASSED", series_id=root,
        ))
        old_order = DeliveryOrder(
            project_id=project, series_id=None, scope_kind="episode", scope_id=ep.id,
            origin="run_episode", version=1, status="DELIVERED", input_digest="old" * 20, shots_digest="old" * 20,
        )
        db.add(old_order)
        db.flush()
        ep.delivery_order_id = old_order.id

    retake = client.post(
        f"/api/v1/projects/{project}/generation-segments/{shots[1]['id']}/regenerate",
        json={"source_job_id": jobs[1]["id"], "expected_shot_revision": 1},
        headers=LOCAL,
    )
    assert retake.status_code == 202, retake.text
    assert retake.json()["job_id"] is None and retake.json()["pending_job"] is True
    retake_series_id = retake.json()["series_id"]

    # Simulate Worker creating and passing the deferred replacement job on the retake series.
    with factory.begin() as db:
        series = db.get(ProductionSeries, retake_series_id)
        item = series.items[0]
        src_job = db.get(Job, jobs[1]["id"])
        job_row = Job(
            project_id=project, shot_id=src_job.shot_id, shot_revision=src_job.shot_revision,
            style_version_id=src_job.style_version_id, request=src_job.request,
            idempotency_key=f"retake-deferred-{shots[1]['id']}", status="QUEUED",
            provider="comfyui", workflow_profile_id=src_job.workflow_profile_id,
        )
        db.add(job_row)
        db.flush()
        _passed(db, job_row)
        item.job_id = job_row.id
        item.status = "PASSED"
        series.current_job_id = job_row.id
        series.status = "COMPLETED"
        new_job_id = job_row.id

    segs = client.get(f"/api/v1/projects/{project}/generation-segments").json()["segments"]
    seg1 = next(s for s in segs if s["shot_id"] == shots[1]["id"])
    assert seg1["replacement_job_id"] == new_job_id
    assert seg1["can_select_replacement"] is True

    selected = client.post(
        f"/api/v1/projects/{project}/generation-segments/{root}/select",
        json={"source_job_id": jobs[1]["id"], "replacement_job_id": new_job_id},
        headers=LOCAL,
    )
    assert selected.status_code == 202, selected.text
    assert selected.json()["delivery_version"] == 2
    with factory() as db:
        assert db.query(DeliveryOrder).count() == 2
        v2 = db.query(DeliveryOrder).filter_by(version=2).one()
        assert v2.origin == "segment_revision"
        assert v2.scope_kind == "episode"


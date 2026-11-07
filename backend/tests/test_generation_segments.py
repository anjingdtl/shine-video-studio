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

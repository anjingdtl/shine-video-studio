"""Scheduling/media-identity fixtures, not real H3 acceptance."""
import hashlib

from shine.models import (
    Job,
    JobAttempt,
    JobDependency,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    QualityGate,
    utcnow,
)
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

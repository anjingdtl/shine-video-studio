"""A crash between process side effects must retain an exclusive, auditable intent."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_journal import (
    HandoffHeartbeat,
    begin_handoff,
    fail_handoff,
    pulse_handoff,
    record_handoff_stage,
)
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler
from shine.models import AppSetting, Job, JobAttempt, MediaArtifact, utcnow
from tests.conftest import create_comfy_job, create_project, create_shot, freeze_style


@pytest.fixture
def handoff(api):
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    owner = "h3:" + job["id"]
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status, row.lease_owner, row.lease_expires_at = "QC_PENDING", "worker-a", utcnow() + timedelta(seconds=60)
        attempt = JobAttempt(job_id=row.id, attempt_no=1, request_fingerprint="a" * 64, status="ACKNOWLEDGED",
                             provider_job_id="prompt-1", lease_owner="worker-a",
                             lease_expires_at=utcnow() + timedelta(seconds=60), output_sha256="b" * 64,
                             prompt_graph_sha256="c" * 64, output_path="test/output.mp4", output_bytes=1)
        db.add(attempt)
        db.flush()
        artifact = MediaArtifact(project_id=project, job_id=row.id, attempt_id=attempt.id, artifact_kind="video",
                                 storage_path="test/output.mp4", sha256="b" * 64, byte_size=1,
                                 content_type="video/mp4", is_mock=False)
        db.add(artifact)
        db.flush()
        row.artifact_id = artifact.id
        instance_payload = {"managed": True, "is_mock": False, "url": "http://127.0.0.1:8190",
                            "process": {"pid": 123, "birth": "4"}}
        instance = ComfyInstance(payload=instance_payload, sha256=graph_hash(instance_payload),
                                 status="RUNNING_PENDING_HEALTH")
        lease = ResourceLease(owner=owner, budget_mb=16384, exclusive=True, priority=100, status="ACTIVE",
                              expires_at=utcnow() + timedelta(seconds=60))
        db.add_all([instance, lease])
        db.flush()
        return factory, row.id, attempt.id, lease.id, instance.id, row.state_version


def test_stop_intent_is_durable_and_crash_keeps_lease_blocked(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    record_handoff_stage(factory, attempt_id, "INTENT", "DRAIN_INTENT", {})
    record_handoff_stage(factory, attempt_id, "DRAIN_INTENT", "DRAINED", {"queue_empty": True})
    record_handoff_stage(factory, attempt_id, "DRAINED", "STOP_INTENT", {})
    fail_handoff(factory, attempt_id, "STOP_OUTCOME_UNKNOWN")
    with factory() as db:
        journal = db.get(AppSetting, "handoff:" + attempt_id)
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert journal.value["last_confirmed_stage"] == "STOP_INTENT"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
    with pytest.raises(HTTPException, match="HANDOFF_ALREADY_EXISTS"):
        begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                      "http://127.0.0.1:8190")
    with factory.begin() as db:
        other = ResourceScheduler().enqueue(db, "another-model", 1)
        other_id = other.id
    with factory() as db:
        assert not ResourceScheduler().admit(db, other_id)


def test_failed_old_handoff_cannot_overwrite_newer_job_claim(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    with factory.begin() as db:
        job = db.get(Job, job_id)
        job.lease_owner = "worker-new"
        job.state_version += 1
    fail_handoff(factory, attempt_id, "OLD_WORKER_FAILED")
    with factory() as db:
        job = db.get(Job, job_id)
        assert job.lease_owner == "worker-new" and job.state_version == version + 1
        assert job.status == "QC_PENDING"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


@pytest.mark.parametrize("state", ["valid", "expired_resource", "new_owner", "expired_claim", "expired_attempt",
                                   "shot_changed", "missing_resource"])
def test_worker_heartbeat_never_revives_or_steals_a_resource(handoff, state):
    from sqlalchemy import select

    from shine.models import Shot
    from shine.worker import heartbeat

    factory, job_id, attempt_id, lease_id, _instance_id, version = handoff
    with factory.begin() as db:
        job, lease = db.get(Job, job_id), db.get(ResourceLease, lease_id)
        lease.expires_at = utcnow() + timedelta(seconds=2)
        if state == "expired_resource":
            lease.expires_at = utcnow() - timedelta(seconds=1)
        if state == "new_owner":
            job.lease_owner = "new-worker"
            job.state_version += 1
        if state == "expired_claim":
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        if state == "expired_attempt":
            db.get(JobAttempt, attempt_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        if state == "shot_changed":
            db.get(Shot, job.shot_id).revision += 1
        original_expiry = lease.expires_at
        if state == "missing_resource":
            db.delete(lease)
    heartbeat(factory, "worker-a", current_job_id=job_id, expected_state_version=version, attempt_id=attempt_id)
    with factory() as db:
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        if state == "valid":
            assert lease.status == "ACTIVE" and lease.expires_at > utcnow() + timedelta(seconds=30)
        elif state == "new_owner":
            assert lease.status == "ACTIVE" and lease.expires_at == original_expiry
        elif state == "missing_resource":
            assert lease.id != lease_id and lease.status == "RECONCILE_REQUIRED"
        else:
            assert lease.status == "RECONCILE_REQUIRED" and lease.expires_at == original_expiry


def test_unbound_worker_heartbeat_cannot_revive_expired_resource(handoff):
    from shine.worker import heartbeat

    factory, job_id, _attempt_id, lease_id, _instance_id, _version = handoff
    with factory.begin() as db:
        lease = db.get(ResourceLease, lease_id)
        lease.expires_at = utcnow() - timedelta(seconds=1)
        expired = lease.expires_at
    heartbeat(factory, "worker-a", current_job_id=job_id)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).expires_at == expired


@pytest.mark.parametrize("activity_at", ["before_check", "during_restart"])
def test_restart_does_not_trust_activity_after_release_check(handoff, monkeypatch, activity_at):
    from types import SimpleNamespace

    from sqlalchemy import text

    from shine.comfyops.handoff_preparation import HandoffSession
    from shine.comfyops.resource_epoch import advance_epoch, read_epoch
    from shine.models import Project

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED"),
                          ("STOPPED", "PHYSICAL_READY"), ("PHYSICAL_READY", "REVIEW_INTENT")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    with factory.begin() as db:
        project = db.get(Project, db.get(Job, job_id).project_id)
        project.quality_policy = {"require_semantic_review": False, "require_style_review": False,
                                  "require_audio_review": False}

    def restore(*args, **kwargs):
        if activity_at == "during_restart":
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                advance_epoch(db, lease_id, "model")
        return object(), {"process": {"pid": 456, "birth": "7"}}

    supervisor = SimpleNamespace(restore_after_review=restore)
    session = HandoffSession(factory, None, job_id, attempt_id, lease_id, SimpleNamespace(check=lambda: None),
                             supervisor, "GPU-test", 1, 1)
    session.accepted = {"ASR": {"task_id": "synthetic", "is_mock": True}}
    session.review_requests = {"ASR": {}}
    session.last_request = {}
    with factory() as db:
        session.last_verified_epoch = read_epoch(db)
    if activity_at == "before_check":
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            advance_epoch(db, lease_id, "model")
    monkeypatch.setattr(session, "_check_review_releases", lambda: None)
    with pytest.raises(HTTPException, match="HANDOFF_RESOURCE_ACTIVITY_CHANGED"):
        session.finish(required_kinds={"ASR"})
    with factory() as db:
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == (
            "REVIEW_INTENT" if activity_at == "before_check" else "RESTART_INTENT"
        )


def test_stopped_handoff_releases_the_gpu_without_turning_a_failure_into_a_pass(handoff, monkeypatch):
    """A dead renderer after STOPPED frees the GPU; the failed job stays NEEDS_REVIEW."""
    from shine.comfyops import handoff_startup

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    fail_handoff(factory, attempt_id, "COMFY_RESTART_OUTCOME_UNCONFIRMED")
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: None)
    monkeypatch.setattr(handoff_startup, "windows_port_listeners", lambda port: [])

    findings = handoff_startup.reconcile_stopped_handoffs(factory)

    assert findings == [{"attempt_id": attempt_id, "job_id": job_id, "action": "released"}]
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_COMPLETE"
        job = db.get(Job, job_id)
        assert job.status == "NEEDS_REVIEW"
        assert job.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"


def test_live_renderer_keeps_a_failed_handoff_blocked(handoff, monkeypatch):
    """A renderer whose ownership is still provable must never be released by inference."""
    from shine.comfyops import handoff_startup

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    fail_handoff(factory, attempt_id, "COMFY_RESTART_OUTCOME_UNCONFIRMED")
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: "4")
    monkeypatch.setattr(handoff_startup, "windows_port_listeners", lambda port: [])

    assert handoff_startup.reconcile_stopped_handoffs(factory) == []
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


def test_advisory_only_handoff_restarts_without_a_review_receipt(handoff):
    """A project that requested no model review still restarts from the stopped GPU state."""
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import HandoffSession
    from shine.comfyops.resource_epoch import read_epoch
    from shine.models import Project

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED"),
                          ("STOPPED", "PHYSICAL_READY"), ("PHYSICAL_READY", "REVIEW_INTENT")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    with factory.begin() as db:
        db.get(Project, db.get(Job, job_id).project_id).quality_policy = {
            "require_semantic_review": False, "require_style_review": False,
            "require_audio_review": False, "advisory_model_reviews": True,
        }
    calls = {}

    def restore(review_verification, **kwargs):
        calls["review_verification"] = review_verification
        calls["gpu_uuid"] = kwargs.get("gpu_uuid")
        return object(), {"process": {"pid": 456, "birth": "7"}, "review_stage_id": None,
                          "model_reviewed": False}

    supervisor = SimpleNamespace(restore_after_review=restore)
    session = HandoffSession(factory, None, job_id, attempt_id, lease_id,
                             SimpleNamespace(check=lambda: None), supervisor, "GPU-test", 1, 1)
    with factory() as db:
        session.last_verified_epoch = read_epoch(db)

    _replacement, evidence = session.finish(required_kinds=set())

    assert calls == {"review_verification": None, "gpu_uuid": "GPU-test"}
    assert session.finished is True
    assert evidence["model_reviewed"] is False
    with factory() as db:
        journal = db.get(AppSetting, "handoff:" + attempt_id)
        assert journal.value["stage"] == "RESTARTED"
        assert journal.value["evidence"]["REVIEW_FINISHED"]["model_review_skipped"] == "ADVISORY_ONLY"


def test_user_can_abandon_an_uncertain_render_without_resubmitting(handoff, monkeypatch):
    """A local user ends the wait; the produced artifact and the GPU are handled honestly."""
    from shine.operator_review import abandon_unreconciled_job

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    fail_handoff(factory, attempt_id, "COMFY_RESTART_OUTCOME_UNCONFIRMED")
    # The renderer process is gone and its port is free.
    monkeypatch.setattr("shine.comfyops.installer.process_identity", lambda pid: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_port_listeners", lambda port: [])

    with factory.begin() as db:
        result = abandon_unreconciled_job(db, None, job_id, "local-user", "user stopped waiting")

    assert result["resubmitted"] is False
    with factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "FAILED"
        assert job.failure_code == "USER_ABANDONED_UNRECONCILED"
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_COMPLETE"


def test_review_release_rejects_later_activity_on_same_lease(handoff, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from sqlalchemy import text

    from shine.comfyops.handoff_journal import bind_handoff_review_task, reserve_handoff_review
    from shine.comfyops.handoff_preparation import HandoffSession
    from shine.comfyops.resource_epoch import advance_epoch
    from shine.models import AgentTaskRecord
    from tests.test_review_isolation_receipt import response

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                  "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED"),
                          ("STOPPED", "PHYSICAL_READY"), ("PHYSICAL_READY", "REVIEW_INTENT")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    review_id = reserve_handoff_review(factory, attempt_id, "ASR")
    receipt, url = response()["isolation_receipt"], "http://127.0.0.1:8116/v1"
    with factory.begin() as db:
        task = AgentTaskRecord(project_id=db.get(Job, job_id).project_id, role_id="model_call:ASR:transcribe",
                               input_hash="a" * 64, asset_manifest_hash="b" * 64, rulepack_id="test",
                               rulepack_hash="c" * 64, model_profile_id="test", model_id="test", status="RUNNING",
                               trace={"kind": "ASR", "resource_id": lease_id, "is_mock": True})
        db.add(task)
        db.flush()
        task_id = task.id
    bind_handoff_review_task(factory, attempt_id, review_id, task_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        task = db.get(AgentTaskRecord, task_id)
        own_epoch = advance_epoch(db, lease_id, "model")
        task.status = "COMPLETED"
        task.trace = {**task.trace, "production_review": {"job_id": job_id}, "resource_epoch": own_epoch,
                      "review_provider": {"base_url_sha256": hashlib.sha256(url.encode()).hexdigest()},
                      "isolation_receipt": receipt}
        advance_epoch(db, lease_id, "model")
    session = HandoffSession(factory, SimpleNamespace(offline_simulation=True), job_id, attempt_id, lease_id,
                             SimpleNamespace(check=lambda: None), None, "GPU-test", 1, 1)
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **kw: {"verified": True})
    monkeypatch.setattr("shine.comfyops.handoff_preparation.enforce_job", lambda *args: None)
    request = {"task_id": task_id, "review_id": review_id, "base_url": url, "receipt": receipt,
               "runtime_sha256": receipt["runtime_sha256"], "gpu_uuid": "GPU-test"}
    with pytest.raises(HTTPException, match="HANDOFF_RESOURCE_ACTIVITY_CHANGED"):
        session.accept_review(request)
    with factory() as db:
        assert db.get(AppSetting, "handoff:" + attempt_id).value["review_steps"][-1]["status"] == "INTENT"


def test_recovery_blocks_interrupted_renderer_activation(handoff):
    from shine.worker import _block_interrupted_handoff

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    with factory.begin() as db:
        db.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)
        db.add(AppSetting(key="renderer-activation:" + attempt_id, value={
            "stage": "INTENT", "job_id": job_id, "attempt_id": attempt_id, "lease_id": lease_id,
            "instance_id": instance_id, "worker_id": "worker-a", "state_version": version,
        }))
    assert _block_interrupted_handoff(factory, job_id) is True
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(Job, job_id).status == "NEEDS_REVIEW"
        assert db.get(AppSetting, "renderer-activation:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


def test_review_admission_persists_intent_and_blocks_overlap(handoff):
    from shine.comfyops.handoff_journal import reserve_handoff_review

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED"),
                          ("STOPPED", "PHYSICAL_READY"), ("PHYSICAL_READY", "REVIEW_INTENT")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    reservation = reserve_handoff_review(factory, attempt_id, "ASR")
    with factory() as db:
        row = db.get(AppSetting, "handoff:" + attempt_id)
        assert row.value["review_steps"] == [{"id": reservation, "kind": "ASR", "status": "INTENT"}]
    with pytest.raises(HTTPException, match="HANDOFF_REVIEW_IN_FLIGHT"):
        reserve_handoff_review(factory, attempt_id, "VLM")
    with pytest.raises(HTTPException, match="HANDOFF_REVIEW_IN_FLIGHT"):
        record_handoff_stage(factory, attempt_id, "REVIEW_INTENT", "REVIEW_FINISHED", {})
    fail_handoff(factory, attempt_id, "REVIEW_OUTCOME_UNKNOWN")
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["review_steps"][0]["id"] == reservation
    with pytest.raises(HTTPException):
        reserve_handoff_review(factory, attempt_id, "ASR")


def test_tracked_review_binds_intent_before_provider_call(handoff, monkeypatch):
    from types import SimpleNamespace

    from shine.comfyops.handoff_journal import CURRENT_HANDOFF_REVIEW, reserve_handoff_review
    from shine.comfyops.model_calls import TrackedProvider
    from shine.models import AgentTaskRecord

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    for before, after in (("INTENT", "DRAIN_INTENT"), ("DRAIN_INTENT", "DRAINED"),
                          ("DRAINED", "STOP_INTENT"), ("STOP_INTENT", "STOPPED"),
                          ("STOPPED", "PHYSICAL_READY"), ("PHYSICAL_READY", "REVIEW_INTENT")):
        record_handoff_stage(factory, attempt_id, before, after, {})
    review_id = reserve_handoff_review(factory, attempt_id, "ASR")
    with factory() as db:
        project_id = db.get(Job, job_id).project_id

    class Provider:
        is_mock = True
        config = SimpleNamespace(profile_id="p", kind="local", model="test", base_url="http://127.0.0.1:8116")

        def transcribe(self, _value):
            with factory() as db:
                step = db.get(AppSetting, "handoff:" + attempt_id).value["review_steps"][-1]
                task = db.get(AgentTaskRecord, step["task_id"])
                assert task.trace["handoff_review_id"] == review_id
                assert task.trace["submitted"] is True
            return {"is_mock": True}

    monkeypatch.setattr("shine.comfyops.native.production_review_binding", lambda *args, **kwargs: None)
    resource_token = CURRENT_RESOURCE.set(lease_id)
    review_token = CURRENT_HANDOFF_REVIEW.set((attempt_id, review_id, job_id, "ASR", None))
    try:
        TrackedProvider(Provider(), factory, project_id, reviewer_kind="ASR", production_job_id=job_id).transcribe("x")
    finally:
        CURRENT_HANDOFF_REVIEW.reset(review_token)
        CURRENT_RESOURCE.reset(resource_token)
    with factory() as db:
        step = db.get(AppSetting, "handoff:" + attempt_id).value["review_steps"][-1]
        assert db.get(AgentTaskRecord, step["task_id"]).status == "COMPLETED"
        assert step["status"] == "INTENT"  # Completion alone is not physical release proof.


def test_handoff_rejects_unmanaged_or_expired_identity(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    with factory.begin() as db:
        instance = db.get(ComfyInstance, instance_id)
        instance.payload = {**instance.payload, "managed": False}
        instance.sha256 = graph_hash(instance.payload)
    with pytest.raises(HTTPException, match="OWNERSHIP_UNVERIFIED"):
        begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                      "http://127.0.0.1:8190")
    with factory.begin() as db:
        instance = db.get(ComfyInstance, instance_id)
        instance.payload = {**instance.payload, "managed": True}
        instance.sha256 = graph_hash(instance.payload)
        db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(HTTPException, match="RESOURCE_LEASE_LOST"):
        begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                      "http://127.0.0.1:8190")
    with factory() as db:
        assert not db.scalar(select(AppSetting).where(AppSetting.key == "handoff:" + attempt_id))


def test_heartbeat_extends_both_claims_without_opening_resource_gap(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    with factory() as db:
        before = db.get(ResourceLease, lease_id).expires_at
    pulse_handoff(factory, attempt_id, lease_seconds=120)
    with factory.begin() as db:
        assert db.get(ResourceLease, lease_id).expires_at > before
        assert db.get(Job, job_id).lease_expires_at > before
        assert db.get(JobAttempt, attempt_id).lease_expires_at > before
        other = ResourceScheduler().enqueue(db, "waiting-other-stage", 1)
        other_id = other.id
    with factory() as db:
        assert not ResourceScheduler().admit(db, other_id)


def test_heartbeat_does_not_revive_expired_lease(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    with factory.begin() as db:
        db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(HTTPException, match="HANDOFF_RESOURCE_LEASE_LOST"):
        pulse_handoff(factory, attempt_id, lease_seconds=120)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


def test_long_stage_holds_same_resource_until_exit(handoff):
    import time

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    with HandoffHeartbeat(factory, attempt_id, lease_seconds=2, interval=0.2) as guard:
        assert CURRENT_RESOURCE.get() == lease_id
        time.sleep(2.2)  # Exceeds the initial lease while synthetic work is in progress.
        guard.check()
        with factory.begin() as db:
            other = ResourceScheduler().enqueue(db, "other-review", 1)
            other_id = other.id
        with factory() as db:
            assert db.get(ResourceLease, lease_id).status == "ACTIVE"
            assert not ResourceScheduler().admit(db, other_id)
    assert CURRENT_RESOURCE.get() is None


def test_heartbeat_loss_remains_blocked_after_context_exit(handoff):
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    with pytest.raises(HTTPException, match="HANDOFF_RESOURCE_LEASE_LOST"):
        with HandoffHeartbeat(factory, attempt_id, lease_seconds=5, interval=0.2) as guard:
            with factory.begin() as db:
                db.get(ResourceLease, lease_id).status = "RECONCILE_REQUIRED"
            guard.check()
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


def test_worker_recovery_preserves_interrupted_stop_intent(handoff, api):
    from shine.worker import recover_expired_jobs

    _, _, settings = api
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    record_handoff_stage(factory, attempt_id, "INTENT", "DRAIN_INTENT", {})
    record_handoff_stage(factory, attempt_id, "DRAIN_INTENT", "DRAINED", {"queue_empty": True})
    record_handoff_stage(factory, attempt_id, "DRAINED", "STOP_INTENT", {})
    with factory.begin() as db:
        db.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)

    class NoExternalCalls:
        def __getattr__(self, _name):
            raise AssertionError("uncertain handoff must not contact or restart a renderer")

    assert recover_expired_jobs(factory, "recovery-worker", settings=settings, comfy_client=NoExternalCalls()) == 1
    with factory() as db:
        row = db.get(Job, job_id)
        journal = db.get(AppSetting, "handoff:" + attempt_id).value
        assert row.status == "NEEDS_REVIEW" and row.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
        assert row.artifact_id == journal["artifact_id"]
        assert journal["stage"] == "RECONCILE_REQUIRED" and journal["last_confirmed_stage"] == "STOP_INTENT"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_recovery_does_not_steal_a_live_handoff(handoff):
    from shine.worker import _block_interrupted_handoff

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    assert not _block_interrupted_handoff(factory, job_id)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "ACTIVE"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "INTENT"


def test_stage_cannot_advance_after_shot_revision_changes(handoff):
    from shine.models import Shot

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version, "http://127.0.0.1:8190")
    with factory.begin() as db:
        job = db.get(Job, job_id)
        db.get(Shot, job.shot_id).revision += 1
    with pytest.raises(HTTPException, match="HANDOFF_JOB_CLAIM_LOST"):
        record_handoff_stage(factory, attempt_id, "INTENT", "DRAIN_INTENT", {})
    with factory() as db:
        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "INTENT"


def test_cannot_begin_handoff_for_superseded_shot(handoff):
    from shine.models import Shot

    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    with factory.begin() as db:
        job = db.get(Job, job_id)
        db.get(Shot, job.shot_id).revision += 1
    with pytest.raises(HTTPException, match="HANDOFF_JOB_CLAIM_LOST"):
        begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
                      "http://127.0.0.1:8190")

"""Regression tests for the ComfyUI completion window.

ComfyUI removes a finished prompt from its queue before the matching history
record becomes readable. A worker that samples exactly inside that window sees
neither the queue entry nor the history entry. Treating that single
observation as proof that the render is gone discards a render that actually
succeeded, which is exactly what happened to the first real local H3 shot on
this machine.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from shine.comfy.client import ComfyUIClient
from shine.config import Settings
from shine.models import Job, JobAttempt, utcnow
from shine.worker import recover_expired_jobs, run_one
from tests.conftest import (
    create_comfy_job,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
)
from tests.fake_http import FakeComfyServer


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def worker_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )


def test_worker_waits_out_the_completion_window_instead_of_failing(api, fake_comfy, monkeypatch):
    """The history record appears a moment after the prompt leaves the queue."""
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])

    # The first three history reads behave like the ComfyUI completion window.
    fake_comfy.hide_history_for = 3
    # Exercise the historical polling path behind the separate ownership gate.
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)

    run_one(
        factory,
        None,
        "window-worker",
        settings=worker_settings(settings),
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )

    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["failure_code"] != "COMFY_JOB_NOT_FOUND", result
    assert result["artifact_id"], result
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["is_mock"] is False
    # The external task was submitted exactly once.
    assert fake_comfy.prompt_count == 1


def test_acknowledgement_is_recorded_even_if_worker_claim_is_lost(api, fake_comfy, monkeypatch):
    from sqlalchemy import select

    import shine.worker as worker
    from shine.models import ExternalJobReconciliation

    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id = create_comfy_job(client, project, shot["id"])["id"]
    original, calls = worker._guard_claimed_job, []

    def lose_claim_on_ack(*args, **kwargs):
        calls.append(True)
        return None if len(calls) == 2 else original(*args, **kwargs)

    monkeypatch.setattr(worker, "_guard_claimed_job", lose_claim_on_ack)
    monkeypatch.setattr(worker, "_handoff_preflight_allowed", lambda *_: True)
    run_one(factory, worker_id="losing-worker", settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2))
    assert fake_comfy.prompt_count == 1
    with factory() as db:
        evidence = db.scalar(select(ExternalJobReconciliation).where(
            ExternalJobReconciliation.job_id == job_id,
            ExternalJobReconciliation.action == "ack_after_claim_lost",
        ))
        assert evidence and evidence.provider_job_id == "prompt-1"
        assert evidence.evidence["resubmitted"] is False


def test_unowned_renderer_cannot_handoff_to_review_after_free_ack(api, fake_comfy, monkeypatch):
    """An HTTP unload acknowledgement does not establish process ownership or RAM release."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease

    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    calls = []
    # Exercise recovery/finalization even though the normal preflight also
    # blocks an unowned instance before submission.
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    monkeypatch.setattr("shine.worker._semantic_review", lambda **kwargs: (calls.append(kwargs), None))
    run_one(factory, worker_id="unowned-renderer", settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
            semantic_reviewer=SimpleNamespace(is_mock=True))
    assert not calls
    with factory() as db:
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
        assert lease.status == "RECONCILE_REQUIRED"
        blocked = db.get(Job, job["id"])
        assert blocked.failure_code == "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
        assert blocked.artifact_id  # Preserve the downloaded result before blocking.
        artifact_id = blocked.artifact_id
    recover_expired_jobs(factory, "recovery", settings=worker_settings(settings),
                         comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2))
    with factory() as db:
        assert db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"])).status == (
            "RECONCILE_REQUIRED"
        )
        assert db.get(Job, job["id"]).artifact_id == artifact_id
    assert fake_comfy.prompt_count == 1


def test_managed_review_safety_failure_cannot_be_reclassified_as_ordinary_asr_error():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import HandoffReviewProvider
    from shine.worker import _transcript_review

    class FailingManagedASR(HandoffReviewProvider):
        is_mock = True
        requires_remote_consent = False

        def __init__(self):
            pass

        def transcribe(self, _path):
            raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")

    with pytest.raises(HTTPException, match="HANDOFF_RESOURCE_ACTIVITY_CHANGED"):
        _transcript_review(asr=FailingManagedASR(), artifact_path=None, spec=SimpleNamespace(dialogue=[]))


@pytest.mark.parametrize("state", ["handoff_failed", "new_owner", "known_owned", "unknown_owned",
                                   "shot_changed", "expired_owner"])
def test_old_worker_exception_does_not_destroy_attempt_evidence(api, monkeypatch, caplog, state):
    from dataclasses import replace
    from types import SimpleNamespace

    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease
    from shine.models import AppSetting, Shot

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id = create_comfy_job(client, project, shot["id"])["id"]

    def interrupted(_factory, *, attempt_id, **_kwargs):
        with factory.begin() as db:
            job, attempt = db.get(Job, job_id), db.get(JobAttempt, attempt_id)
            lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
            attempt.status, attempt.provider_job_id = "ACKNOWLEDGED", "preserved-external-id"
            job.provider_job_id = "preserved-external-id"
            if state == "handoff_failed":
                lease.status = "RECONCILE_REQUIRED"
                job.status, job.failure_code = "NEEDS_REVIEW", "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
                job.lease_owner = None
                job.lease_expires_at = None
                db.add(AppSetting(key="handoff:" + attempt_id, value={"stage": "RECONCILE_REQUIRED", "job_id": job_id,
                                                                      "attempt_id": attempt_id}))
            else:
                if state == "new_owner":
                    job.status, job.lease_owner = "RUNNING", "new-worker"
                    job.state_version += 2
                elif state == "known_owned":
                    job.status = "RUNNING"
                    job.state_version += 1
                elif state == "shot_changed":
                    job.status = "RUNNING"
                    job.state_version += 1
                    db.get(Shot, job.shot_id).revision += 1
                elif state == "expired_owner":
                    job.status = "RUNNING"
                    job.state_version += 1
                    job.lease_expires_at = utcnow() - timedelta(seconds=1)
                else:
                    job.provider_job_id, attempt.provider_job_id = None, None
                    attempt.status, attempt.prompt_graph_sha256 = "SUBMIT_INTENT", "c" * 64
        raise RuntimeError("synthetic-sensitive-token-must-not-appear")

    monkeypatch.setattr("shine.worker._run_comfy_job", interrupted)
    run_one(factory, worker_id="old-worker", settings=replace(worker_settings(settings), offline_simulation=True),
            comfy_client=SimpleNamespace(free_models=lambda: None))
    assert "synthetic-sensitive-token-must-not-appear" not in caplog.text
    with factory() as db:
        job = db.get(Job, job_id)
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        if state == "unknown_owned":
            assert attempt.status == "ACK_UNCERTAIN" and attempt.provider_job_id is None
            assert job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN" and job.status == "NEEDS_REVIEW"
            assert db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id)).status == (
                "RECONCILE_REQUIRED"
            )
            return
        assert attempt.status == "ACKNOWLEDGED" and attempt.provider_job_id == "preserved-external-id"
        if state == "handoff_failed":
            assert job.status == "NEEDS_REVIEW" and job.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
        elif state == "new_owner":
            assert job.status == "RUNNING" and job.lease_owner == "new-worker"
            assert db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id)).status == "ACTIVE"
        elif state in {"shot_changed", "expired_owner"}:
            assert job.status == "RUNNING" and job.lease_owner == "old-worker"
            assert db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id)).status == (
                "RECONCILE_REQUIRED"
            )
        else:
            assert job.status == "NEEDS_REVIEW" and job.failure_code == "COMFY_WORKER_EXCEPTION_WITH_EXTERNAL"
            assert db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id)).status == (
                "RECONCILE_REQUIRED"
            )


def test_unowned_renderer_is_blocked_before_h3_submission(api, fake_comfy):
    from types import SimpleNamespace

    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    run_one(factory, worker_id="unowned-preflight", settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
            semantic_reviewer=SimpleNamespace(is_mock=True))
    with factory() as db:
        blocked = db.get(Job, job["id"])
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
        assert blocked.status == "NEEDS_REVIEW"
        assert blocked.failure_code == "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
        assert lease.status == "RECONCILE_REQUIRED"
        assert not blocked.attempts
    assert fake_comfy.prompt_count == 0


def test_unowned_renderer_without_reviewer_is_not_contacted(api):
    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    calls = []

    class UnownedRenderer:
        def free_models(self):
            calls.append("free")

    run_one(factory, worker_id="unowned-no-review", settings=worker_settings(settings),
            comfy_client=UnownedRenderer())
    with factory() as db:
        blocked = db.get(Job, job["id"])
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
        assert blocked.status == "NEEDS_REVIEW"
        assert blocked.failure_code == "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
        assert lease.status == "RECONCILE_REQUIRED"
        assert not blocked.attempts
    assert calls == []


def test_recovery_does_not_infer_physical_release_from_unload(api):
    from types import SimpleNamespace

    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "PASSED"
        db.add(JobAttempt(job_id=row.id, attempt_no=1, request_fingerprint="a" * 64, status="COMPLETED",
                          lease_owner="dead-worker", lease_expires_at=utcnow() - timedelta(seconds=1)))
        db.add(ResourceLease(owner="h3:" + row.id, status="ACTIVE", budget_mb=16384, exclusive=True,
                             priority=100, expires_at=utcnow() - timedelta(seconds=1)))
    calls = []
    recover_expired_jobs(factory, "recovery", settings=worker_settings(settings),
                         comfy_client=SimpleNamespace(free_models=lambda: calls.append("unload")))
    with factory() as db:
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
        assert lease.status == "RECONCILE_REQUIRED"
    assert not calls


def test_completed_render_without_review_still_requires_physical_reconciliation(api, fake_comfy, monkeypatch):
    from sqlalchemy import select

    from shine.comfyops.models import ResourceLease

    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    # Exercise post-submission recovery for a legacy completed render.
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    run_one(factory, worker_id="no-review", settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2))
    with factory() as db:
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
        assert db.get(Job, job["id"]).artifact_id
        assert lease.status == "RECONCILE_REQUIRED"
    assert fake_comfy.prompt_count == 1


@pytest.mark.parametrize("failure_code", ["COMFY_JOB_NOT_FOUND", "COMFY_WORKER_EXCEPTION_WITH_EXTERNAL"])
def test_stranded_job_is_reconciled_against_external_history(api, fake_comfy, failure_code):
    """A job stranded by the completion window is collected, never resubmitted."""
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])

    # Simulate the stranded state: the worker gave up while the render was
    # actually finishing, and the external history now holds the result.
    fake_comfy.prompts["prompt-1"] = {"prompt": {"1": {"class_type": "H3BaseSampler", "inputs": {}}}}
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "NEEDS_REVIEW"
        row.failure_code = failure_code
        row.provider_job_id = "prompt-1"
        row.lease_owner = None
        row.lease_expires_at = None
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="f" * 64,
                status="FAILED" if failure_code == "COMFY_JOB_NOT_FOUND" else "ACKNOWLEDGED",
                failure_code="COMFY_JOB_NOT_FOUND" if failure_code == "COMFY_JOB_NOT_FOUND" else None,
                provider_job_id="prompt-1",
                lease_owner="stopped-worker",
                lease_expires_at=utcnow() - timedelta(seconds=1),
            )
        )

    assert (
        recover_expired_jobs(
            factory,
            "reconciler",
            settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == 1
    )
    # The media was collected from the existing external task, not re-rendered.
    assert fake_comfy.prompt_count == 0
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["artifact_id"], result
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["is_mock"] is False

    events = client.get(f"/api/v1/projects/{project_id}/events?limit=200").json()["items"]
    assert any(event["event_type"] == "COMFY_RECONCILED_HISTORY" for event in events)


def test_activation_uncertainty_adopts_completed_work_without_resubmitting(api, fake_comfy, monkeypatch):
    """An activation journal left after a lost lease must not block a finished prompt.

    The activation was already confirmed and the external prompt completed, so
    the only unresolved fact is the external outcome. Recovery may read it from
    history because the recorded renderer is still the same live, owned process.
    """
    from sqlalchemy import select

    from shine.comfyops import installer, owned_process
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.models import AppSetting, JobAttempt

    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])

    fake_comfy.prompts["prompt-activation"] = {"prompt": {"1": {"class_type": "H3BaseSampler", "inputs": {}}}}
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        lease = ResourceScheduler().enqueue(db, "h3:" + row.id, 16384, True, 100)
        db.flush()
        lease.status = "RECONCILE_REQUIRED"
        attempt = JobAttempt(
            job_id=row.id,
            attempt_no=1,
            request_fingerprint="f" * 64,
            status="ACKNOWLEDGED",
            provider_job_id="prompt-activation",
            prompt_graph_sha256="c" * 64,
            lease_owner="stopped-worker",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(attempt)
        db.flush()
        row.status = "NEEDS_REVIEW"
        row.failure_code = "COMFY_ACTIVATION_RECONCILIATION_REQUIRED"
        row.provider_job_id = "prompt-activation"
        row.lease_owner = None
        row.lease_expires_at = None
        db.add(AppSetting(key="renderer-activation:" + attempt.id, value={
            "stage": "RECONCILE_REQUIRED",
            "last_confirmed_stage": "CONFIRMED",
            "job_id": row.id,
            "attempt_id": attempt.id,
            "lease_id": lease.id,
            "instance_id": "activation-instance",
        }))
        db.add(AppSetting(key="renderer-startup:activation-instance", value={
            "stage": "RUNNING",
            "instance_id": "activation-instance",
            "lease_id": lease.id,
            "port": 0,
            "process": {"pid": 424242, "birth": "1", "job_name": "Local\\ShineHandoff-" + "a" * 32},
        }))
    monkeypatch.setattr(installer, "process_identity", lambda pid: "1")
    monkeypatch.setattr(owned_process, "inspect_named_group", lambda *args, **kwargs: True)

    assert (
        recover_expired_jobs(
            factory,
            "reconciler",
            settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == 1
    )
    assert fake_comfy.prompt_count == 0
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["artifact_id"], result
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    with factory() as db:
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job["id"]))
        assert attempt.reconciliation_state in {"history_found", "collected"}
        # Job recovery adopts the artifact but never releases the physical
        # renderer; that remains the reconciliation of its owning series.
        assert db.get(ResourceLease, lease.id).status == "RECONCILE_REQUIRED"

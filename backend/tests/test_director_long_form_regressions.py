"""Whole-film regressions from the second independent acceptance (2026-11-06).

These tests began life as the independent counterexamples of
``.local-acceptance/longform-recheck-20261106`` (evidence SHA-256
``ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f``) and are
now the permanent regression contract for the closeout fixes F01–F08:

* F01 — an episode is only assembled from *this run's own* series products,
  only after the complete planned batch set passed, and a run never completes
  while a required batch is still queued.
* F02 — a run pause (user or budget) reaches its child series.
* F03 — a stale budget tick cannot revive a cancelled run.
* F04 — a changed workflow or a tampered root manifest blocks dispatch.
* F05 — a frozen policy is typed, range-checked and reaches the real job.
* F06 — a NEEDS_REVIEW child becomes a blocker, and a resolved blocker has a
  supported recovery path.
* F07 — a run does not reach terminal CANCELLED while submitted work is still
  unreconciled.
* F08 — an idle advance does not move the real-progress timestamp.

No real model is called: synthetic test-pattern media is a *source artifact*
only, never a master asset, and never real H3 evidence.
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import pytest
from sqlalchemy import select

from shine.comfyops.models import ResourceLease
from shine.director.contracts import digest_of
from shine.director.models import DirectorPackageVersion
from shine.director.package import package_readiness
from shine.media.delivery_models import DeliveryOrder
from shine.models import (
    AuditEvent,
    Job,
    JobAttempt,
    ProductionSeries,
    ProductionSeriesItem,
    Project,
    WorkflowProfile,
    utcnow,
)
from shine.production import runner
from shine.production.episode_delivery import build_episode_edl
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode, ProductionRunEvent
from tests.test_director_long_form import (
    LOCAL,
    PNG,
    _advance,
    _complete_dispatched_series,
    _confirm_package,
    _plan_and_package,
    _seed_passed_job,
    _studio,
    approve_lock,
    register_workflow_profile,
    upload_asset,
)


def start(api, *, shots=2, batch_limit=1, policy=None):
    client, factory, settings = api
    studio = _studio(client, shots=shots)
    _, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=batch_limit)
    if policy:
        response = client.post(
            f"/api/v1/projects/{studio['project']}/director-packages",
            json={"workflow_profile_id": studio['profile']['id'], "generation_mode":"Ref2VA",
                  "policy": {"batch_shot_limit":batch_limit, **policy}}, headers=LOCAL)
        assert response.status_code == 201, response.text
        package = response.json()
    _confirm_package(client, package['id'])
    response = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                           json={"package_id":package['id']}, headers=LOCAL)
    assert response.status_code == 201, response.text
    return studio, package, response.json()


def test_r1_old_jobs_must_not_allow_early_episode_delivery(api):
    client, factory, settings = api
    studio, package, run = start(api)
    for shot in studio['shots']:
        _seed_passed_job(factory, settings, studio['project'], shot['id'], studio['style']['id'])
    _advance(factory, settings, run['id'])
    _complete_dispatched_series(factory, settings, run['id'])
    _advance(factory, settings, run['id'])
    with factory() as db:
        orders = db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id==studio['project'])).all()
        assert not orders, 'Episode delivery queued while its second required batch has not passed'


def test_r1_run_must_not_complete_with_pending_required_series(api):
    from shine.media.delivery_engine import run_pending_deliveries
    client, factory, settings = api
    studio, package, run = start(api)
    for shot in studio['shots']:
        _seed_passed_job(factory, settings, studio['project'], shot['id'], studio['style']['id'])
    _advance(factory, settings, run['id'])
    _complete_dispatched_series(factory, settings, run['id'])
    _advance(factory, settings, run['id'])
    run_pending_deliveries(factory, settings=settings, asset_root=settings.data_dir, worker_id='independent')
    _advance(factory, settings, run['id'])
    with factory() as db:
        row = db.get(ProductionRun, run['id'])
        assert row.status != 'COMPLETED', 'Run completed while its second batch is QUEUED'


def test_r1_edl_must_select_jobs_owned_by_this_run(api):
    client, factory, settings = api
    studio, package, run = start(api)
    for _ in range(2):
        _advance(factory, settings, run['id'])
        _complete_dispatched_series(factory, settings, run['id'])
    _advance(factory, settings, run['id'])
    unrelated = _seed_passed_job(factory, settings, studio['project'], studio['shots'][1]['id'], studio['style']['id'])
    with factory() as db:
        order = db.scalar(select(DeliveryOrder).where(DeliveryOrder.project_id==studio['project']))
        edl, _, _ = build_episode_edl(db, order, ffprobe_path=settings.ffprobe_path,
                                     ffmpeg_path=settings.ffmpeg_path, asset_root=settings.data_dir)
        selected = [x['job_id'] for x in edl['clips']]
        assert unrelated not in selected, 'A job not in any run series item was selected for final delivery'


def test_r2_pause_must_prevent_queued_series_from_starting(api):
    client, factory, settings = api
    studio, package, run = start(api)
    _advance(factory, settings, run['id'])
    runner.pause_run(factory,run['id'])
    with factory() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series = db.get(ProductionSeries,batch.series_id)
        assert series.status in {'PAUSED','PAUSE_REQUESTED'}, 'Child series remains QUEUED after the run claims PAUSED'


def test_r2_pause_running_series_must_request_safe_boundary(api):
    client, factory, settings = api
    studio, package, run = start(api)
    _advance(factory, settings, run['id'])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        db.get(ProductionSeries,batch.series_id).status='RUNNING'
    runner.pause_run(factory,run['id'])
    with factory() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series=db.get(ProductionSeries,batch.series_id)
        assert series.status=='PAUSE_REQUESTED', 'No safe-boundary pause reached the running child series'


def test_r3_changed_workflow_must_block_dispatch_after_start(api):
    client, factory, settings = api
    studio, package, run = start(api)
    with factory.begin() as db:
        profile=db.get(WorkflowProfile,studio['profile']['id'])
        changed={node_id:{**node,'inputs':dict(node.get('inputs',{}))} for node_id,node in profile.graph.items()}
        changed['1']['inputs']['seed']=42
        profile.graph=changed
        profile.graph_sha256=digest_of(changed)
    with factory() as db:
        readiness=package_readiness(db,db.get(DirectorPackageVersion,package['id']))
        assert not readiness['startable']
    _advance(factory,settings,run['id'])
    with factory() as db:
        batches=db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id'])).all()
        assert not any(x.series_id for x in batches), (
            'Changed workflow still dispatched after frozen package became nonstartable')


def test_r4_zero_repairs_policy_must_reach_job(api):
    client, factory, settings = api
    studio, package, run=start(api,policy={'auto_repair':False,'max_repairs_per_shot':0})
    with factory.begin() as db:
        db.get(Project,studio['project']).auto_repair_enabled=True
    _advance(factory,settings,run['id'])
    with factory() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series=db.get(ProductionSeries,batch.series_id)
        job=db.get(Job,series.items[0].job_id)
        assert job.max_semantic_retries==0, 'Run froze zero retries but actual job allows two'
        assert job.auto_repair is False, 'Run froze auto_repair=false but the job follows the project default'


def test_r5_idle_advance_must_not_change_real_progress_time(api):
    client, factory, settings = api
    studio, package, run=start(api)
    _advance(factory,settings,run['id'])
    with factory() as db:
        first=db.get(ProductionRun,run['id']).last_progress_at
    _advance(factory,settings,run['id'])
    with factory() as db:
        second=db.get(ProductionRun,run['id']).last_progress_at
    assert first==second, 'No batch or output changed, but the real-progress timestamp advanced'


def test_passed_series_shot_updates_run_progress_clock(api):
    client, factory, settings = api
    studio, package, run = start(api, shots=1, batch_limit=1)
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(ProductionSeriesItem.series_id == series.id))
        job = db.get(Job, item.job_id)
        series_id, job_id, shot_id = series.id, job.id, job.shot_id
        series.status = "RUNNING"
        series.worker_id = "worker-progress-test"
        job.status = "PASSED"
        job.stale = False
        run_row = db.get(ProductionRun, run["id"])
        previous = utcnow() - timedelta(minutes=5)
        run_row.last_progress_at = previous

    from shine.comfyops.production_series import _after_shot

    _after_shot(factory, series_id, "worker-progress-test", job_id)
    with factory() as db:
        run_row = db.get(ProductionRun, run["id"])
        event = db.scalars(select(ProductionRunEvent).where(
            ProductionRunEvent.run_id == run["id"],
            ProductionRunEvent.kind == "SHOT_PASSED",
        )).first()
        assert run_row.last_progress_at is not None and run_row.last_progress_at > previous
        assert event and event.payload["shot_id"] == shot_id


def test_r2_budget_pause_must_propagate_to_queued_series(api):
    client, factory, settings = api
    studio, package, run=start(api,policy={'max_wall_seconds':1})
    _advance(factory,settings,run['id'])
    with factory.begin() as db:
        db.get(ProductionRun,run['id']).started_at=utcnow()-timedelta(seconds=10)
    _advance(factory,settings,run['id'])
    with factory() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series=db.get(ProductionSeries,batch.series_id)
        assert series.status in {'PAUSED','PAUSE_REQUESTED'}, 'Budget says paused, but child series can still start'


def test_r5_review_series_must_not_leave_run_spinning_forever(api):
    client, factory, settings=api
    studio, package, run=start(api)
    _advance(factory,settings,run['id'])
    with factory.begin() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series=db.get(ProductionSeries,batch.series_id)
        series.status='NEEDS_REVIEW'
        series.failure_code='MANUAL_REVIEW_REQUIRED'
        series.finalization_state='RELEASED'
    _advance(factory,settings,run['id'])
    with factory() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        row=db.get(ProductionRun,run['id'])
        assert batch.status=='BLOCKED', 'NEEDS_REVIEW child was never propagated into batch/run blockers'
        assert row.status=='NEEDS_REVIEW'


def test_r5_cancel_must_wait_for_submitted_job_reconciliation(api):
    client, factory, settings=api
    studio, package, run=start(api)
    _advance(factory,settings,run['id'])
    with factory.begin() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        series=db.get(ProductionSeries,batch.series_id)
        series.status='RUNNING'
        series.finalization_state='ACTIVE'
        job=db.get(Job,series.items[0].job_id)
        job.status='RUNNING'
        series.items[0].status='RUNNING'
    runner.cancel_run(factory,run['id'])
    with factory() as db:
        row=db.get(ProductionRun,run['id'])
        assert row.status!='CANCELLED', (
            'Run is terminal CANCELLED while submitted work remains RUNNING and unreconciled')


def test_r5_cancel_releases_only_a_settled_pre_submit_waiting_reservation(api):
    from shine.comfyops.scheduler import ResourceScheduler

    client, factory, settings = api
    studio, package, run = start(api, shots=1, batch_limit=1)
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series.id,
        ))
        job = db.get(Job, item.job_id)
        lease = ResourceScheduler().enqueue(db, f"h3:{job.id}", 16384, True, 100)
        assert job.status == "QUEUED" and not job.provider_job_id and not job.artifact_id
        assert not job.lease_owner and not job.lease_expires_at
        assert lease is not None and lease.status == "WAITING"
        assert not db.scalar(select(JobAttempt.id).where(JobAttempt.job_id == job.id).limit(1))
        series_id, item_id, job_id, lease_id = series.id, item.id, job.id, lease.id
        series.status = "NEEDS_REVIEW"
        series.failure_code = "SERIES_INCOMPLETE"
        series.finalization_state = "NONE"
        series.lease_id = None
        series.renderer_identity = None

    with factory() as db:
        assert not runner._series_has_inflight_work(db, db.get(ProductionSeries, series_id))
    cancelled = client.post(f"/api/v1/production-runs/{run['id']}/cancel", headers=LOCAL)
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        job = db.get(Job, job_id)
        lease = db.get(ResourceLease, lease_id)
        run_row = db.get(ProductionRun, run["id"])
        release_audit = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == lease.id,
            AuditEvent.event_type == "RESOURCE_LEASE_RELEASED_PRE_SUBMIT",
        ))
        assert run_row.status == "CANCELLED"
        assert series.status == "CANCELLED"
        assert item.status == "CANCELLED"
        assert job.stale is True and job.status == "QUEUED"
        assert lease.status == "RELEASED"
        assert release_audit is not None and release_audit.actor == "local-user"
        assert release_audit.payload["external_submission"] == "NOT_SENT_PROVEN"
        assert release_audit.payload["gpu_was_acquired"] is False


def test_r2_stale_budget_tick_must_not_overwrite_user_cancellation(api):
    client, factory, settings = api
    studio, package, run=start(api,policy={'max_wall_seconds':1})
    with factory.begin() as db:
        db.get(ProductionRun,run['id']).started_at=utcnow()-timedelta(seconds=10)
    with factory() as stale_db:
        stale=stale_db.get(ProductionRun,run['id'])
        runner.cancel_run(factory,run['id'])
        runner.advance_run(stale_db,stale,worker_id='stale-budget-worker',settings=settings)
        stale_db.commit()
    with factory() as db:
        actual=db.get(ProductionRun,run['id'])
        assert actual.status=='CANCELLED', 'An unfenced budget branch revived a cancelled run as PAUSED'


def test_r5_resolved_blocker_must_have_supported_resume_path(api):
    client, factory, settings=api
    studio, package, run=start(api,shots=1)
    _advance(factory,settings,run['id'])
    _complete_dispatched_series(factory,settings,run['id'])
    with factory.begin() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        db.get(ProductionSeries,batch.series_id).status='FAILED'
    _advance(factory,settings,run['id'])
    with factory.begin() as db:
        batch=db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id==run['id']))
        db.get(ProductionSeries,batch.series_id).status='COMPLETED'
    runner.reconcile_run(factory,run['id'])
    runner.resume_run(factory,run['id'])
    with factory() as db:
        row=db.get(ProductionRun,run['id'])
        assert row.status in {'QUEUED','RUNNING'}, (
            'Resolved batch remains stuck in NEEDS_REVIEW after public reconcile/resume')


def _mark_pre_submit_mapping_failure(factory, run_id, *, external_identity=False):
    """Persist the exact ledger shape created by a failed workflow-map check."""
    with factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series.id,
        ).order_by(ProductionSeriesItem.position))
        job = db.get(Job, item.job_id)
        job.status = "NEEDS_REVIEW"
        job.failure_code = "PENDING_WORKFLOW_MAPPING"
        item.status = "BLOCKED"
        item.failure_code = "PENDING_WORKFLOW_MAPPING"
        batch.status = "BLOCKED"
        batch.failure_code = "SERIES_EXECUTION_FAILED:HTTPException"
        series.status = "NEEDS_REVIEW"
        series.failure_code = "SERIES_EXECUTION_FAILED:HTTPException"
        series.finalization_state = "RELEASED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = None
        run.status = "NEEDS_REVIEW"
        run.control_reason = "SERIES_EXECUTION_FAILED:HTTPException"
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        episode.status = "BLOCKED"
        episode.blocker_code = "SERIES_EXECUTION_FAILED:HTTPException"
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            request_fingerprint=digest_of(job.request),
            status="FAILED" if not external_identity else "NEEDS_REVIEW",
            lease_owner="test-worker",
            lease_expires_at=utcnow(),
            failure_code="PENDING_WORKFLOW_MAPPING",
            provider_job_id="unknown-external-id" if external_identity else None,
            submit_ack_at=utcnow() if external_identity else None,
        )
        db.add(attempt)
        db.add(AuditEvent(
            project_id=run.project_id,
            actor="worker",
            event_type="COMFY_WORKFLOW_MAPPING_PENDING",
            entity_type="job",
            entity_id=job.id,
            payload={"stage": "prepare", "external_submission": "NOT_SENT_PROVEN"},
        ))
        if external_identity:
            db.add(AuditEvent(
                project_id=run.project_id,
                actor="worker",
                event_type="COMFY_SUBMIT_INTENT",
                entity_type="job",
                entity_id=job.id,
                payload={"external_submission": "UNKNOWN"},
            ))
        db.flush()
        return item.shot_id, item.id, job.id, series.id, attempt.id


def _start_subject_picture_mapping_run(api):
    """Use a declared synthetic profile with two subject and one picture slots."""
    client, factory, settings = api
    studio = _studio(client, shots=1)
    second_subject = upload_asset(
        client, studio["project"], content=PNG + b"-second", filename="第二角色.png",
    )
    approve_lock(client, second_subject["id"])
    shot_id = studio["shots"][0]["id"]
    response = client.put(
        f"/api/v1/shots/{shot_id}/bindings",
        headers=LOCAL,
        json={"bindings": [
            {"asset_id": studio["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0},
            {"asset_id": second_subject["id"], "use_as": "subject", "role": "CHAR_B", "ordinal": 1},
        ]},
    )
    assert response.status_code == 200, response.text
    studio["second_subject"] = second_subject
    graph = {
        "1": {"class_type": "H3BaseSampler", "inputs": {
            "positive_prompt": "", "seed": 0, "duration": 6.0,
            "reference_subject_1": "", "reference_subject_2": "", "reference_picture_1": "",
        }},
        "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
    }
    parameter_map = {
        "positive_prompt": "1.inputs.positive_prompt",
        "seed": "1.inputs.seed",
        "duration": "1.inputs.duration",
        "reference_subject_1": "1.inputs.reference_subject_1",
        "reference_subject_2": "1.inputs.reference_subject_2",
        "reference_picture_1": "1.inputs.reference_picture_1",
    }
    studio["profile"] = register_workflow_profile(
        client, name="resume-recovery-subject-picture", mode="Ref2VA",
        graph=graph, parameter_map=parameter_map,
    )
    _, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    response = client.post(
        f"/api/v1/projects/{studio['project']}/production-runs",
        json={"package_id": package["id"]}, headers=LOCAL,
    )
    assert response.status_code == 201, response.text
    return studio, package, response.json()


def test_reconcile_tracks_queued_series_instead_of_stale_running_batch(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])

    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        batch.status = "RUNNING"
        series.status = "QUEUED"
        series.worker_id = None
        series.lease_expires_at = None
        assert runner._batch_public_status(db, batch) == "QUEUED"
        episode = runner.episode_public(
            db, db.get(ProductionRun, run["id"]), runner._episode_rows(db, run["id"]),
        )[0]
        assert episode["batches"][0]["status"] == "QUEUED"
        assert runner._sync_batch_from_series(db, batch)
        assert batch.status == "QUEUED"
        assert batch.failure_code is None

    with factory() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        assert batch.status == "QUEUED"


def test_resume_recovers_only_proven_pre_submit_mapping_failure_as_local_user(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])
    shot_id, item_id, old_job_id, series_id, attempt_id = _mark_pre_submit_mapping_failure(
        factory, run["id"],
    )

    response = client.put(
        f"/api/v1/shots/{shot_id}/bindings",
        headers=LOCAL,
        json={"bindings": [
            {"asset_id": studio["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0},
            {"asset_id": studio["second_subject"]["id"], "use_as": "picture", "role": "CHAR_B", "ordinal": 1},
        ]},
    )
    assert response.status_code == 200, response.text

    assert runner.resume_run(factory, run["id"], actor="local-user") == "RUNNING"
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        old_job = db.get(Job, old_job_id)
        attempt = db.get(JobAttempt, attempt_id)
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "production_series",
            AuditEvent.entity_id == series_id,
            AuditEvent.event_type == "PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT",
        )).all()
        binding_audit = db.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "shot",
            AuditEvent.entity_id == shot_id,
            AuditEvent.event_type == "SHOT_BINDINGS_UPDATED",
        ).order_by(AuditEvent.created_at.desc())).first()
        batch = db.scalar(select(ProductionRunBatch).where(
            ProductionRunBatch.series_id == series_id,
        ))
        assert series.status == "QUEUED" and series.failure_code is None
        assert batch.status == "QUEUED"
        assert item.status == "PENDING" and item.job_id is None
        assert old_job.status == "NEEDS_REVIEW" and old_job.failure_code == "PENDING_WORKFLOW_MAPPING"
        assert attempt.status == "FAILED" and attempt.provider_job_id is None
        assert events and events[-1].actor == "local-user"
        assert events[-1].payload["external_submission"] == "NOT_SENT_PROVEN"
        assert binding_audit and binding_audit.actor == "local-user"


def test_resume_does_not_reopen_mapping_failure_with_external_identity(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])
    shot_id, item_id, old_job_id, series_id, attempt_id = _mark_pre_submit_mapping_failure(
        factory, run["id"], external_identity=True,
    )

    response = client.put(
        f"/api/v1/shots/{shot_id}/bindings",
        headers=LOCAL,
        json={"bindings": [
            {"asset_id": studio["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0},
            {"asset_id": studio["second_subject"]["id"], "use_as": "picture", "role": "CHAR_B", "ordinal": 1},
        ]},
    )
    assert response.status_code == 200, response.text

    assert runner.resume_run(factory, run["id"], actor="local-user") == "NEEDS_REVIEW"
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        attempt = db.get(JobAttempt, attempt_id)
        assert series.status == "NEEDS_REVIEW"
        assert item.status == "BLOCKED" and item.job_id == old_job_id
        assert attempt.provider_job_id == "unknown-external-id"
        assert not db.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "production_series",
            AuditEvent.entity_id == series_id,
            AuditEvent.event_type == "PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT",
        )).first()


def test_resume_requeues_only_series_incomplete_job_without_attempt(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series.id,
        ).order_by(ProductionSeriesItem.position))
        job = db.get(Job, item.job_id)
        series.status = "NEEDS_REVIEW"
        series.failure_code = "SERIES_INCOMPLETE"
        series.finalization_state = "RELEASED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = None
        batch.status = "BLOCKED"
        batch.failure_code = "SERIES_INCOMPLETE"
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        episode.status = "BLOCKED"
        episode.blocker_code = "SERIES_INCOMPLETE"
        db.get(ProductionRun, run["id"]).status = "NEEDS_REVIEW"
        assert job.status == "QUEUED" and not job.attempts
        series_id, item_id, job_id = series.id, item.id, job.id

    assert runner.resume_run(factory, run["id"], actor="local-user") == "RUNNING"
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        job = db.get(Job, job_id)
        event = db.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "production_series",
            AuditEvent.entity_id == series_id,
            AuditEvent.event_type == "PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT",
        )).first()
        assert series.status == "QUEUED" and series.failure_code is None
        assert db.get(ProductionRunBatch, batch.id).status == "QUEUED"
        assert item.status == "QUEUED" and item.job_id == job_id
        assert job.status == "QUEUED" and not job.attempts
        assert event and event.actor == "local-user"
        assert event.payload["external_submission"] == "NOT_SENT_PROVEN"


def test_resume_supersedes_changed_queued_request_without_attempt(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series.id,
        ).order_by(ProductionSeriesItem.position))
        job = db.get(Job, item.job_id)
        lease = ResourceLease(
            owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
            status="WAITING", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        series.status = "NEEDS_REVIEW"
        series.failure_code = "SERIES_INCOMPLETE"
        series.finalization_state = "RELEASED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = None
        batch.status = "BLOCKED"
        batch.failure_code = "SERIES_INCOMPLETE"
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        episode.status = "BLOCKED"
        episode.blocker_code = "SERIES_INCOMPLETE"
        db.get(ProductionRun, run["id"]).status = "NEEDS_REVIEW"
        shot_id, series_id, item_id, old_job_id = item.shot_id, series.id, item.id, job.id
        assert job.status == "QUEUED" and not job.attempts

    response = client.put(
        f"/api/v1/shots/{shot_id}/bindings",
        headers=LOCAL,
        json={"bindings": [
            {"asset_id": studio["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0},
            {"asset_id": studio["second_subject"]["id"], "use_as": "picture", "role": "CHAR_B", "ordinal": 1},
        ]},
    )
    assert response.status_code == 200, response.text

    assert runner.resume_run(factory, run["id"], actor="local-user") == "RUNNING"
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        item = db.get(ProductionSeriesItem, item_id)
        old_job = db.get(Job, old_job_id)
        superseded = db.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "job",
            AuditEvent.entity_id == old_job_id,
            AuditEvent.event_type == "RUN_JOB_SUPERSEDED_PRE_SUBMIT",
        )).first()
        assert series.status == "QUEUED"
        assert db.get(ProductionRunBatch, batch.id).status == "QUEUED"
        assert item.status == "PENDING" and item.job_id is None
        assert old_job.status == "NEEDS_REVIEW" and old_job.stale
        assert old_job.failure_code == "RUN_BINDINGS_REVISED_BEFORE_SUBMIT"
        assert not old_job.attempts and old_job.artifact_id is None and old_job.provider_job_id is None
        assert db.get(ResourceLease, lease.id).status == "RELEASED"
        assert superseded and superseded.actor == "local-user"
        assert superseded.payload["external_submission"] == "NOT_SENT_PROVEN"


def test_resume_releases_prior_proven_superseded_waiting_lease(api):
    client, factory, settings = api
    studio, package, run = _start_subject_picture_mapping_run(api)
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        series = db.get(ProductionSeries, batch.series_id)
        item = db.scalar(select(ProductionSeriesItem).where(
            ProductionSeriesItem.series_id == series.id,
        ).order_by(ProductionSeriesItem.position))
        job = db.get(Job, item.job_id)
        job.status = "NEEDS_REVIEW"
        job.stale = True
        job.failure_code = "RUN_BINDINGS_REVISED_BEFORE_SUBMIT"
        lease = ResourceLease(
            owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
            status="WAITING", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        series.status = "NEEDS_REVIEW"
        series.failure_code = "SERIES_INCOMPLETE"
        series.finalization_state = "RELEASED"
        series.worker_id = None
        series.lease_expires_at = None
        series.lease_id = None
        batch.status = "BLOCKED"
        batch.failure_code = "SERIES_INCOMPLETE"
        episode = db.get(ProductionRunEpisode, batch.episode_row_id)
        episode.status = "BLOCKED"
        episode.blocker_code = "SERIES_INCOMPLETE"
        db.get(ProductionRun, run["id"]).status = "NEEDS_REVIEW"
        series_id, job_id = series.id, job.id
        from shine.services import add_audit

        add_audit(db, "local-user", "RUN_JOB_SUPERSEDED_PRE_SUBMIT", "job", job.id,
                  {"external_submission": "NOT_SENT_PROVEN", "attempt_count": 0,
                   "job_preserved": True}, project_id=studio["project"])

    assert runner.resume_run(factory, run["id"], actor="local-user") == "NEEDS_REVIEW"
    with factory() as db:
        lease_row = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        released = db.scalars(select(AuditEvent).where(
            AuditEvent.project_id == studio["project"],
            AuditEvent.entity_type == "resource_lease",
            AuditEvent.entity_id == lease_row.id,
            AuditEvent.event_type == "RESOURCE_LEASE_RELEASED_PRE_SUBMIT",
        )).first()
        assert lease_row.status == "RELEASED"
        assert released and released.actor == "local-user"
        assert released.payload["owner_job_id"] == job_id
        assert db.get(ProductionSeries, series_id).status == "NEEDS_REVIEW"


@pytest.mark.parametrize('bad_policy', [
    {'batch_shot_limit':'not-a-number'},
    {'max_wall_seconds':-1},
    {'max_repairs_per_shot':-1},
    {'min_free_gb':-20},
])
def test_r4_invalid_frozen_policy_must_be_rejected_at_api(api,bad_policy):
    client, factory, settings = api
    studio=_studio(client,shots=1)
    _plan_and_package(client,studio,target_seconds=60)
    response=client.post(f"/api/v1/projects/{studio['project']}/director-packages",
                         json={'workflow_profile_id':studio['profile']['id'],'generation_mode':'Ref2VA','policy':bad_policy},
                         headers=LOCAL)
    assert response.status_code==422, 'Invalid policy was accepted into a supposedly executable immutable package'


def test_r3_root_manifest_integrity_must_be_checked(api):
    client, factory, settings = api
    studio, package, run=start(api)
    with factory.begin() as db:
        row=db.get(DirectorPackageVersion,package['id'])
        changed=dict(row.manifest)
        changed['policy']={**changed['policy'],'max_repairs_per_shot':50}
        row.manifest=changed
    with factory() as db:
        readiness=package_readiness(db,db.get(DirectorPackageVersion,package['id']))
        assert not readiness['startable'], (
            'Root content changed without updating its pinned hash, but the package is still startable')


# ---------------------------------------------------------------------------
# Closeout-specific additions: control propagation, fencing and recovery
# ---------------------------------------------------------------------------


def test_run_pause_then_resume_continues_the_child_series(api):
    """A paused run's series is not claimable; resume releases exactly it."""
    from shine.comfyops.production_series import claim_series

    client, factory, settings = api
    studio, package, run = start(api, shots=2)
    _advance(factory, settings, run['id'])
    assert runner.pause_run(factory, run['id']) == "PAUSED"
    assert claim_series(factory, "another-worker") is None, "a paused run's series was claimed"
    assert runner.resume_run(factory, run['id']) == "RUNNING"
    with factory() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run['id']))
        series = db.get(ProductionSeries, batch.series_id)
        assert series.status == "QUEUED"
        assert series.control_reason is None


def test_user_cancelled_run_finishes_only_after_external_work_settled(api):
    """CANCEL_REQUESTED is finalised by the beat that observes settled work."""
    client, factory, settings = api
    studio, package, run = start(api)
    _advance(factory, settings, run['id'])
    with factory.begin() as db:
        batch = db.scalar(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run['id']))
        series = db.get(ProductionSeries, batch.series_id)
        series.status = "RUNNING"
        series.finalization_state = "ACTIVE"
        job = db.get(Job, series.items[0].job_id)
        job.status = "RUNNING"
        series.items[0].status = "RUNNING"
        job_id = job.id
    assert runner.cancel_run(factory, run['id']) == "CANCEL_REQUESTED"
    _advance(factory, settings, run['id'])
    with factory() as db:
        assert db.get(ProductionRun, run['id']).status == "CANCEL_REQUESTED", \
            "finalised while a job is still RUNNING"
    with factory.begin() as db:
        db.get(Job, job_id).status = "PASSED"
        db.get(ProductionRun, run['id']).heartbeat_at = utcnow() - timedelta(seconds=600)
        db.get(ProductionRun, run['id']).claim_expires_at = utcnow() - timedelta(seconds=600)
    _advance(factory, settings, run['id'])
    with factory() as db:
        assert db.get(ProductionRun, run['id']).status == "CANCELLED"
        assert db.get(Job, job_id).status == "PASSED", "cancellation must not delete produced work"


def test_a_wrongly_completed_run_is_corrected_by_reconcile_not_rewritten(api):
    """A COMPLETED run with undelivered episodes is corrected to NEEDS_REVIEW."""
    client, factory, settings = api
    studio, package, run = start(api, shots=2)
    _advance(factory, settings, run['id'])
    _complete_dispatched_series(factory, settings, run['id'])
    with factory.begin() as db:
        db.get(ProductionRun, run['id']).status = "COMPLETED"
    result = runner.reconcile_run(factory, run['id'])
    assert result["status"] == "NEEDS_REVIEW"
    with factory() as db:
        events = runner.run_events(db, run['id'])
        assert any(event["kind"] == "RUN_COMPLETION_CORRECTED" for event in events)


# ---------------------------------------------------------------------------
# T01 — a long assembly keeps its claim; a silent one loses it safely
# ---------------------------------------------------------------------------


def _pending_episode_order(api):
    client, factory, settings = api
    studio, package, run = start(api)
    for _ in range(2):
        _advance(factory, settings, run['id'])
        _complete_dispatched_series(factory, settings, run['id'])
    _advance(factory, settings, run['id'])
    with factory() as db:
        order = db.scalar(select(DeliveryOrder).where(DeliveryOrder.project_id == studio['project']))
        assert order is not None and order.status == "PENDING"
        return order.id


def _claim_as(factory, order_id, worker_id):
    from shine.media.delivery_engine import DeliveryClaim
    from shine.production.episode_delivery import _claim_order

    with factory.begin() as db:
        order = db.get(DeliveryOrder, order_id)
        read_version = order.state_version
        if not _claim_order(db, order, worker_id):
            return None
        return DeliveryClaim(owner=worker_id, version=read_version + 1)


def _age_order(factory, order_id, seconds):
    from sqlalchemy import update as sql_update

    with factory.begin() as db:
        db.execute(sql_update(DeliveryOrder).where(DeliveryOrder.id == order_id)
                   .values(updated_at=utcnow() - timedelta(seconds=seconds))
                   .execution_options(synchronize_session=False))


def test_a_long_assembly_that_keeps_beating_is_never_reclaimed(api):
    from shine.media.delivery_engine import ASSEMBLY_LEASE_SECONDS, renew_assembly_claim

    client, factory, settings = api
    order_id = _pending_episode_order(api)
    claim = _claim_as(factory, order_id, "worker-a")
    assert claim is not None
    # FFmpeg has been running longer than the lease, but the owner is alive.
    _age_order(factory, order_id, ASSEMBLY_LEASE_SECONDS + 60)
    assert renew_assembly_claim(factory, order_id, claim) is True
    assert _claim_as(factory, order_id, "worker-b") is None, "a beating assembly was handed to a second worker"
    with factory() as db:
        row = db.get(DeliveryOrder, order_id)
        assert (row.status, row.worker_id, row.state_version) == ("ASSEMBLING", "worker-a", claim.version)


def test_a_silent_assembler_is_reclaimed_and_its_late_write_back_is_refused(api):
    from shine.media.delivery_engine import ASSEMBLY_LEASE_SECONDS, _write_back, renew_assembly_claim

    client, factory, settings = api
    order_id = _pending_episode_order(api)
    first = _claim_as(factory, order_id, "worker-a")
    _age_order(factory, order_id, ASSEMBLY_LEASE_SECONDS + 60)
    second = _claim_as(factory, order_id, "worker-b")
    assert second is not None, "a silent assembler must become reclaimable"
    with factory.begin() as db:
        assert _write_back(db, order_id, first, {"status": "DELIVERED"}) is False
    assert renew_assembly_claim(factory, order_id, first) is False, "the old owner may not renew"
    with factory() as db:
        row = db.get(DeliveryOrder, order_id)
        assert (row.status, row.worker_id) == ("ASSEMBLING", "worker-b")


# ---------------------------------------------------------------------------
# T02 — the first version never splits a media-continuity chain across series
# ---------------------------------------------------------------------------


def test_a_long_continuity_chain_stays_inside_one_batch():
    """A dependent shot needs its source in the same series; the batch plan
    may only cut before a shot that does not continue the previous one, even
    when the chain is longer than the batch limit."""
    from types import SimpleNamespace

    from shine.production.runner import _batch_plan

    shots = [{"shot_id": f"s{i}", "revision": 1, "spec_sha256": "x", "bindings": [],
              "continuity_mode": "cut" if i in (0, 6) else "last_frame"} for i in range(8)]
    manifest = {"episode_id": "ep-01", "shots": shots}
    episode = SimpleNamespace(episode_id="ep-01", manifest=manifest, manifest_sha256=digest_of(manifest))
    batches = _batch_plan(episode, batch_shot_limit=2)
    assert [batch["shot_ids"] for batch in batches] == [
        ["s0", "s1", "s2", "s3", "s4", "s5"],  # the whole chain s0..s5, longer than the limit
        ["s6", "s7"],
    ]
    covered = [shot for batch in batches for shot in batch["shot_ids"]]
    assert covered == [f"s{i}" for i in range(8)], "no shot dropped or repeated"

"""The automatic delivery: from a finished series to a playable reel.

The engine enqueues one durable order per finished series version, builds the
EDL from the confirmed series order and the frozen edit intent, assembles
with FFmpeg, registers the reel on the series, and recovers from a crash at
any point.  Media here is synthetic test-pattern video; nothing here is a
character, location or prop master asset.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import timedelta
from uuid import uuid4

import pytest

from shine.comfyops.production_series import (
    cancel_series,
)
from shine.media.assembly import DeliveryAssembler, DeliveryError
from shine.media.delivery_engine import (
    ASSEMBLY_LEASE_SECONDS,
    DeliveryBlock,
    DeliveryClaim,
    _claim_order,
    _write_back,
    build_auto_edl,
    enqueue_auto_delivery,
    run_delivery_order,
    run_pending_deliveries,
)
from shine.media.delivery_models import DeliveryOrder
from shine.models import (
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    QualityGate,
    utcnow,
)
from tests.conftest import create_project, create_shot, freeze_style, make_test_video_bytes


def _skip_without_ffmpeg(settings) -> None:
    if not shutil.which(settings.ffmpeg_path) or not shutil.which(settings.ffprobe_path):
        pytest.skip("FFmpeg/FFprobe is not installed")


def _seed_passed_job(factory, settings, project_id, shot_id, style_id, *, position=0):
    """A passed job with a real artifact, real gates and a real file."""
    content = make_test_video_bytes(duration_s=2.0, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(bytes(content)).hexdigest()
    relative = f"artifacts/{project_id}/delivery-test/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    session = factory()
    try:
        job = Job(
            project_id=project_id,
            shot_id=shot_id,
            shot_revision=1,
            style_version_id=style_id,
            request={},
            idempotency_key=uuid4().hex,
            status="PASSED",
            quality_verification="real",
            provider="comfyui",
            generation_mode="Ref2VA",
        )
        session.add(job)
        session.flush()
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            request_fingerprint=digest,
            status="COMPLETED",
            lease_owner="test-worker",
            lease_expires_at=utcnow(),
            output_path=relative,
            output_sha256=digest,
            output_bytes=len(content),
        )
        session.add(attempt)
        session.flush()
        artifact = MediaArtifact(
            project_id=project_id,
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_kind="video",
            storage_path=relative,
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
        )
        session.add(artifact)
        session.flush()
        job.artifact_id = artifact.id
        for name in ("semantic", "audio", "style", "final"):
            session.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                    gate_name=name, status="passed", is_mock=False))
        session.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                gate_name="technical", status="passed", is_mock=False,
                                model="ffprobe", coverage="covered"))
        session.commit()
        return {"job": job.id, "attempt": attempt.id, "artifact": artifact.id,
                "relative": relative, "sha": digest, "bytes": len(content)}
    finally:
        session.close()


def _seed_completed_series(factory, project_id, shots_ids, job_ids):
    """A COMPLETED series whose items point at the passed jobs in order."""
    session = factory()
    try:
        series = ProductionSeries(
            project_id=project_id,
            status="COMPLETED",
            provider="comfyui",
            shot_count=len(shots_ids),
            completed_count=len(shots_ids),
            finalization_state="RELEASED",
        )
        session.add(series)
        session.flush()
        for position, (shot_id, job_id) in enumerate(zip(shots_ids, job_ids, strict=False)):
            session.add(ProductionSeriesItem(
                series_id=series.id, shot_id=shot_id, position=position,
                status="PASSED", job_id=job_id,
            ))
        session.commit()
        return series.id
    finally:
        session.close()


def _finished_two_shot_series(api):
    """A finished two-shot series with real artifacts; returns (ids, client)."""
    client, factory, settings = api
    _skip_without_ffmpeg(settings)
    project = create_project(client, "自动交付测试")
    style = freeze_style(client, project)
    shot_a = create_shot(client, project, style["id"])
    shot_b = create_shot(client, project, style["id"])
    job_a = _seed_passed_job(factory, settings, project, shot_a["id"], style["id"])
    job_b = _seed_passed_job(factory, settings, project, shot_b["id"], style["id"])
    series_id = _seed_completed_series(factory, project, [shot_a["id"], shot_b["id"]],
                                       [job_a["job"], job_b["job"]])
    return {
        "client": client, "factory": factory, "settings": settings, "project": project,
        "style": style, "shots": [shot_a["id"], shot_b["id"]],
        "jobs": [job_a, job_b], "series": series_id,
    }


def test_completed_series_auto_delivers_and_is_idempotent(api):
    state = _finished_two_shot_series(api)
    client, factory, settings = state["client"], state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
        assert order is not None and order.version == 1 and order.status == "PENDING"
        again = enqueue_auto_delivery(db, series)
        assert again.id == order.id
    status = run_delivery_order(factory, order.id, settings=settings,
                                asset_root=settings.data_dir, worker_id="w1")
    assert status == "DELIVERED"
    with factory() as db:
        order = db.get(DeliveryOrder, order.id)
        assert order.status == "DELIVERED" and order.artifact_id
        assert order.edl_source == "default_cut_rules"
        assert len(order.edl_payload["clips"]) == 2
        series = db.get(ProductionSeries, state["series"])
        assert series.delivery_result["order_id"] == order.id
        assert series.delivery_result["review_state"] == "all_passed"
    # The web API lists the order and the reel is downloadable.
    orders = client.get(f"/api/v1/projects/{state['project']}/delivery-orders").json()
    assert [row["status"] for row in orders] == ["DELIVERED"]
    reel = client.get(f"/api/v1/deliveries/{order.artifact_id}/media")
    assert reel.status_code == 200


def test_auto_delivery_includes_passed_predecessor_from_interrupted_series(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        interrupted = db.get(ProductionSeries, state["series"])
        interrupted.status = "NEEDS_REVIEW"
        interrupted.items[1].status = "PENDING"
        interrupted.items[1].job_id = None
        successor = db.get(Job, state["jobs"][1]["job"])
        successor.request = {"shot_spec": {"continuity": {
            "mode": "last_frame", "previous_shot_id": state["shots"][0],
            "previous_job_id": state["jobs"][0]["job"],
        }}}
    series_id = _seed_completed_series(factory, state["project"], state["shots"][1:],
                                       [state["jobs"][1]["job"]])
    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        edl, _, _ = build_auto_edl(db, series, asset_root=settings.data_dir)
        assert [clip["job_id"] for clip in edl["clips"]] == [job["job"] for job in state["jobs"]]


def test_rework_produces_a_new_version_and_keeps_history(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "DELIVERED"
    with factory() as db:
        v1_artifact = db.get(DeliveryOrder, order.id).artifact_id
    # Simulate a rework of the second shot: a new attempt, a new real artifact.
    with factory.begin() as db:
        job = db.get(Job, state["jobs"][1]["job"])
        # Distinct bytes: the store is content addressed, so identical bytes
        # would be the same artifact, not a new delivery version.
        content = make_test_video_bytes(duration_s=2.5, width=320, height=240, with_audio=True)
        digest = hashlib.sha256(bytes(content)).hexdigest()
        relative = f"artifacts/{state['project']}/delivery-test/{digest}.mp4"
        target = settings.data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        attempt = JobAttempt(
            job_id=job.id, attempt_no=2, request_fingerprint=digest, status="COMPLETED",
            lease_owner="test-worker", lease_expires_at=utcnow(),
            output_path=relative, output_sha256=digest, output_bytes=len(content),
        )
        session_obj = db
        session_obj.add(attempt)
        session_obj.flush()
        artifact = MediaArtifact(
            project_id=state["project"], job_id=job.id, attempt_id=attempt.id,
            artifact_kind="video", storage_path=relative, sha256=digest,
            byte_size=len(content), content_type="video/mp4", is_mock=False,
        )
        session_obj.add(artifact)
        session_obj.flush()
        job.artifact_id = artifact.id
        for name in ("semantic", "audio", "style", "final"):
            session_obj.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                        gate_name=name, status="passed", is_mock=False))
        session_obj.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                    gate_name="technical", status="passed", is_mock=False,
                                    model="ffprobe", coverage="covered"))
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order2 = enqueue_auto_delivery(db, series)
    assert order2.id != order.id and order2.version == 2 and order2.status == "PENDING"
    assert run_delivery_order(factory, order2.id, settings=settings,
                              asset_root=settings.data_dir) == "DELIVERED"
    with factory() as db:
        v2 = db.get(DeliveryOrder, order2.id)
        assert v2.status == "DELIVERED"
        first = db.get(DeliveryOrder, order.id)
        assert first.status == "DELIVERED" and first.artifact_id == v1_artifact
        assert first.artifact_id != v2.artifact_id


def test_crash_between_edl_and_assembly_recovers(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    # The worker died after claiming the order (ASSEMBLING) but before FFmpeg.
    with factory.begin() as db:
        row = db.get(DeliveryOrder, order.id)
        row.status = "ASSEMBLING"
        row.worker_id = "dead-worker"
        row.updated_at = utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS + 60)
    processed = run_pending_deliveries(factory, settings=settings,
                                       asset_root=settings.data_dir, worker_id="w2")
    assert processed == 1
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).status == "DELIVERED"


def test_ffmpeg_failure_retries_bounded_then_reports(api, monkeypatch):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    import shine.media.assembly as assembly_module

    real_assemble = assembly_module.DeliveryAssembler.assemble
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            from shine.media.assembly import DeliveryError
            raise DeliveryError(500, "DELIVERY_FFMPEG_FAILED: injected")
        return real_assemble(self, *args, **kwargs)

    monkeypatch.setattr(assembly_module.DeliveryAssembler, "assemble", flaky)
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "PENDING"
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "PENDING"
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "DELIVERED"
    assert calls["n"] == 3
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).attempt_count == 3


def test_exhausted_retries_report_failure_not_silence(api, monkeypatch):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    import shine.media.assembly as assembly_module

    def always_fails(self, *args, **kwargs):
        from shine.media.assembly import DeliveryError
        raise DeliveryError(500, "DELIVERY_FFMPEG_FAILED: injected")

    monkeypatch.setattr(assembly_module.DeliveryAssembler, "assemble", always_fails)
    for _ in range(2):
        assert run_delivery_order(factory, order.id, settings=settings,
                                  asset_root=settings.data_dir) == "PENDING"
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "FAILED"
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.attempt_count == 3
        assert "DELIVERY_FFMPEG_FAILED" in (row.failure_code or "")
    # A user retry clears the budget again and is bounded once more.
    listing = state["client"].get(f"/api/v1/projects/{state['project']}/delivery-orders").json()
    assert listing[0]["status"] == "FAILED"
    retry = state["client"].post(f"/api/v1/delivery-orders/{order.id}/retry")
    assert retry.status_code == 202
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).status == "PENDING"
        assert db.get(DeliveryOrder, order.id).attempt_count == 0


def test_corrupted_source_is_blocked_not_delivered(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    source = settings.data_dir / state["jobs"][0]["relative"]
    source.write_bytes(b"corrupted bytes")
    assert run_delivery_order(factory, order.id, settings=settings,
                              asset_root=settings.data_dir) == "BLOCKED"
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.failure_code == "DELIVERY_SOURCE_FILE_HASH_MISMATCH"
        assert row.status == "BLOCKED"
    # No delivery artifact exists for the project.
    listing = state["client"].get(f"/api/v1/projects/{state['project']}/deliveries").json()
    assert listing == []


def test_cancelled_series_cancels_its_pending_order(api):
    state = _finished_two_shot_series(api)
    factory = state["factory"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
        series_row = db.get(ProductionSeries, state["series"])
        series_row.status = "QUEUED"  # make it cancellable again for the test
    cancel_series(factory, state["series"])
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).status == "CANCELLED"
        assert db.get(DeliveryOrder, order.id).failure_code == "SERIES_CANCELLED"


def test_unpassed_shot_never_reaches_delivery(api):
    client, factory, settings = api
    project = create_project(client, "未通过镜头")
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = _seed_passed_job(factory, settings, project, shot["id"], style["id"])
    session = factory()
    try:
        series = ProductionSeries(project_id=project, status="NEEDS_REVIEW",
                                  provider="comfyui", shot_count=1, completed_count=0)
        session.add(series)
        session.flush()
        session.add(ProductionSeriesItem(series_id=series.id, shot_id=shot["id"],
                                         position=0, status="BLOCKED", job_id=job["job"]))
        series_id = series.id
        session.commit()
    finally:
        session.close()
    with factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        with pytest.raises(DeliveryBlock):
            enqueue_auto_delivery(db, series)
    orders = client.get(f"/api/v1/projects/{project}/delivery-orders").json()
    assert orders == []


def test_edl_order_follows_series_positions_not_creation_time(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    # The second created job actually sits at position 0: the series order is
    # authoritative, not the creation time.
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        enqueue_auto_delivery(db, series)
        edl, source, rules = build_auto_edl(
            db, series,
            ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path,
            asset_root=settings.data_dir,
        )
    assert source == "default_cut_rules"
    assert [clip["job_id"] for clip in edl["clips"]] == [state["jobs"][0]["job"], state["jobs"][1]["job"]]
    assert all(clip["in_s"] == 0.0 and clip["out_s"] > 0 for clip in edl["clips"])
    assert rules["rules_version"] == "default-cut-rules-v1"


def test_stale_edit_intent_falls_back_to_confirmed_rules(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    from shine.models import AgentArtifact, AgentTaskRecord
    session = factory()
    try:
        task = AgentTaskRecord(project_id=state["project"], role_id="editor",
                               input_hash="0" * 64, asset_manifest_hash="0" * 64,
                               rulepack_id="h3-prompt-writing", rulepack_hash="0" * 64,
                               model_profile_id="x", model_id="m",
                               schema_version="1.2", status="SUCCEEDED", trace={})
        session.add(task)
        session.flush()
        artifact = AgentArtifact(
            project_id=state["project"], kind="EditIntentPlan", schema_version="1.2",
            payload_json={"schema_version": "1.2", "intent_id": "d1", "clips": [
                {"intent_id": "i1", "order": 1, "planned_duration_s": 7.0},
                {"intent_id": "i2", "order": 2, "planned_duration_s": 7.0},
            ], "rhythm": "steady", "dialogue_pace": "natural"},
            sha256="a" * 64, producer_role="editor", source_task_id=task.id,
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
        session.commit()
    finally:
        session.close()
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        series.edit_intent_artifact_id = artifact_id
        edl, source, rules = build_auto_edl(db, series, ffprobe_path=settings.ffprobe_path,
                                            ffmpeg_path=settings.ffmpeg_path,
                                            asset_root=settings.data_dir)
    assert source == "frozen_edit_intent"
    assert rules["frozen_edit_intent"]["artifact_id"] == artifact_id


def test_worker_loop_runs_pending_deliveries(api):
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    from shine.models import AgentRoute, ProviderProfile
    session = factory()
    try:
        profile = ProviderProfile(
            name="pp", kind="mock", base_url="", model="mock",
            capabilities={}, generation_options={}, timeout_s=5, max_retries=0,
            temperature=0.0, enabled=True,
        )
        session.add(profile)
        session.flush()
        session.add(AgentRoute(role="qc_reviewer", provider_profile_id=profile.id))
        session.commit()
    finally:
        session.close()
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    from shine.worker import MockH3Provider, run_configured_once

    result = run_configured_once(
        factory, MockH3Provider(), "w-delivery",
        settings=settings, comfy_client=None,
    )
    assert result == "delivery:1"
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).status == "DELIVERED"
def test_unexpired_assembly_does_not_starve_other_orders(api):
    """A held assembly lease is excluded; other orders still run."""
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
        order.status = "ASSEMBLING"
        order.worker_id = "live-worker"
    other = _finished_two_shot_series(api)
    with factory.begin() as db:
        other_series = db.get(ProductionSeries, other["series"])
        other_order = enqueue_auto_delivery(db, other_series)
    processed = run_pending_deliveries(factory, settings=settings,
                                       asset_root=settings.data_dir, worker_id="w-recover")
    # Only the other PENDING order progresses. The held assembly is excluded
    # from the scheduling round and burns no attempt budget.
    assert processed == 1
    with factory() as db:
        held = db.get(DeliveryOrder, order.id)
        assert held.status == "ASSEMBLING" and held.worker_id == "live-worker"
        assert held.attempt_count == 0
        assert db.get(DeliveryOrder, other_order.id).status == "DELIVERED"


def test_worker_restart_does_not_busy_loop_and_recovers_on_expiry(api, monkeypatch):
    """A restart over an unexpired lease returns control; expiry still recovers."""
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
        order.status = "ASSEMBLING"
        order.worker_id = "dead-worker"
    import shine.media.delivery_engine as engine_module

    real = engine_module.run_delivery_order
    attempts = []

    def bounded(*args, **kwargs):
        outcome = real(*args, **kwargs)
        attempts.append(outcome)
        return outcome

    monkeypatch.setattr(engine_module, "run_delivery_order", bounded)
    first = run_pending_deliveries(factory, settings=settings,
                                   asset_root=settings.data_dir, worker_id="restart")
    # Occupied leases do not consume a scheduling slot or a claim attempt.
    assert attempts == []
    assert first == 0
    with factory.begin() as db:
        row = db.get(DeliveryOrder, order.id)
        row.updated_at = utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS + 60)
    second = run_pending_deliveries(factory, settings=settings,
                                    asset_root=settings.data_dir, worker_id="restart")
    assert second == 1
    with factory() as db:
        assert db.get(DeliveryOrder, order.id).status == "DELIVERED"
def test_two_stale_sessions_cannot_both_claim_the_same_order(api):
    """Two sessions reading the same PENDING row: exactly one claim wins."""
    state = _finished_two_shot_series(api)
    factory = state["factory"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
    with factory() as first, factory() as second:
        first_view = first.get(DeliveryOrder, order.id)
        first_claim = _claim_order(first, first_view, "worker-A")
        first.commit()
        second_view = second.get(DeliveryOrder, order.id)
        second_claim = _claim_order(second, second_view, "worker-B")
        second.commit()
    assert (first_claim, second_claim) == (True, False)
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.status == "ASSEMBLING" and row.worker_id == "worker-A"
        assert row.state_version == 1
def test_claim_losers_cannot_write_back_over_the_new_owner(api):
    """A lost claim token (owner, state_version) matches zero rows on write-back."""
    state = _finished_two_shot_series(api)
    factory = state["factory"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)
        first_claim = _claim_order(db, order, "worker-A")
        assert first_claim is True
    with factory.begin() as db:
        row = db.get(DeliveryOrder, order.id)
        row.updated_at = utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS + 60)
    with factory.begin() as db:
        row = db.get(DeliveryOrder, order.id)
        second_claim = _claim_order(db, row, "worker-B")
        assert second_claim is True
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.worker_id == "worker-B" and row.state_version == 2

    with factory.begin() as db:
        stale = _write_back(db, order.id, DeliveryClaim(owner="worker-A", version=1),
                            {"status": "DELIVERED"})
        assert stale is False
        fresh = _write_back(db, order.id, DeliveryClaim(owner="worker-B", version=2),
                            {"status": "DELIVERED", "delivered_at": utcnow(),
                             "state_version": DeliveryOrder.state_version + 1})
        assert fresh is True
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.status == "DELIVERED" and row.worker_id == "worker-B"
        assert row.state_version == 3
def test_stolen_lease_during_assembly_is_never_overwritten(api, monkeypatch):
    """A worker that lost its lease mid-assembly cannot write back its failure."""
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        series = db.get(ProductionSeries, state["series"])
        order = enqueue_auto_delivery(db, series)

    def steal_then_fail(self, *args, **kwargs):
        # Worker B legitimately re-leases the expired lease while A assembles;
        # A's later write-back must not overwrite B's state.
        with factory.begin() as db2:
            row = db2.get(DeliveryOrder, order.id)
            row.updated_at = utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS + 60)
        with factory.begin() as db2:
            row = db2.get(DeliveryOrder, order.id)
            assert _claim_order(db2, row, "worker-B") is True
        raise DeliveryError(500, "DELIVERY_FFMPEG_FAILED: injected")

    monkeypatch.setattr(DeliveryAssembler, "assemble", steal_then_fail)
    status = run_delivery_order(factory, order.id, settings=settings,
                                asset_root=settings.data_dir, worker_id="worker-A")
    assert status == "ASSEMBLING"
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert row.status == "ASSEMBLING" and row.worker_id == "worker-B"
        assert row.failure_code is None
        assert row.state_version == 2


@pytest.mark.parametrize("held_count", [64, 65])
def test_held_assemblies_cannot_fill_the_scheduling_window(api, held_count):
    """Filter occupied leases before the batch limit so a pending order runs."""
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    held_ids = []
    with factory.begin() as db:
        for _ in range(held_count):
            series = ProductionSeries(project_id=state["project"], status="COMPLETED")
            db.add(series)
            db.flush()
            order = DeliveryOrder(
                project_id=state["project"], series_id=series.id,
                status="ASSEMBLING", worker_id="live-worker", attempt_count=1,
                updated_at=utcnow() - timedelta(seconds=60),
            )
            db.add(order)
            db.flush()
            held_ids.append(order.id)
        pending = enqueue_auto_delivery(db, db.get(ProductionSeries, state["series"]))

    processed = run_pending_deliveries(
        factory, settings=settings, asset_root=settings.data_dir, worker_id="available-worker",
    )
    assert processed == 1
    with factory() as db:
        assert db.get(DeliveryOrder, pending.id).status == "DELIVERED"
        for identity in held_ids:
            held = db.get(DeliveryOrder, identity)
            assert (held.status, held.worker_id, held.state_version, held.attempt_count) == (
                "ASSEMBLING", "live-worker", 0, 1,
            )


def test_lost_claim_cannot_commit_edl_metadata_after_successful_assembly(api, monkeypatch):
    """A successful stale assembly cannot commit over the new owner's EDL."""
    state = _finished_two_shot_series(api)
    factory, settings = state["factory"], state["settings"]
    with factory.begin() as db:
        order = enqueue_auto_delivery(db, db.get(ProductionSeries, state["series"]))
    original = DeliveryAssembler.assemble
    new_owner_metadata = {
        "edl_source": "frozen_edit_intent",
        "edl_payload": {"owner": "worker-B"},
        "edit_rules": {"owner": "worker-B"},
    }
    snapshot = {}

    def steal_then_assemble(self, *args, **kwargs):
        with factory.begin() as db:
            db.get(DeliveryOrder, order.id).updated_at = (
                utcnow() - timedelta(seconds=ASSEMBLY_LEASE_SECONDS + 1)
            )
        with factory.begin() as db:
            assert _claim_order(db, db.get(DeliveryOrder, order.id), "worker-B")
        with factory.begin() as db:
            row = db.get(DeliveryOrder, order.id)
            for name, value in new_owner_metadata.items():
                setattr(row, name, value)
        with factory() as db:
            snapshot["updated_at"] = db.get(DeliveryOrder, order.id).updated_at
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DeliveryAssembler, "assemble", steal_then_assemble)
    result = run_delivery_order(
        factory, order.id, settings=settings, asset_root=settings.data_dir, worker_id="worker-A",
    )
    assert result == "ASSEMBLING"
    with factory() as db:
        row = db.get(DeliveryOrder, order.id)
        assert (row.worker_id, row.state_version, row.attempt_count) == ("worker-B", 2, 2)
        assert {name: getattr(row, name) for name in new_owner_metadata} == new_owner_metadata
        assert row.updated_at == snapshot["updated_at"]
        assert row.artifact_id is None
        assert db.get(ProductionSeries, state["series"]).delivery_result is None

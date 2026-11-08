"""The director package and the whole-film run: offline, adversarial contracts.

Nothing here touches a GPU or a model.  Synthetic test-pattern media is used as
a *source artifact* only; it never becomes a character, location or prop master
asset.  What these tests prove is the part that a live renderer makes expensive
to observe: batch identity, one-series-at-a-time dispatch, cross-series episode
assembly, honest completion and control.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from urllib.parse import unquote
from uuid import uuid4

import pytest
from sqlalchemy import select

from shine.director.contracts import (
    EpisodePlanPayload,
    derive_episode_plan,
    validate_episode_coverage,
)
from shine.media.delivery_models import DeliveryOrder
from shine.models import (
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    QualityGate,
    utcnow,
)
from shine.production import runner as run_engine
from shine.production.episode_delivery import build_episode_edl
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from shine.schemas import ShotSpec
from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
    register_workflow_profile,
    upload_asset,
)

LOCAL = {"X-Shine-Local-Action": "1"}


def _has_ffmpeg(settings) -> bool:
    return bool(shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path))


# ---------------------------------------------------------------------------
# Episode-plan contracts (pure)
# ---------------------------------------------------------------------------


@dataclass
class _FakeShot:
    id: str
    created_at: object
    spec: dict
    name: str = "镜头"


def _shot(index: int, *, duration: float = 6.0, mode: str = "cut") -> _FakeShot:
    spec = ShotSpec(story_beat=f"节拍{index}", duration_target_s=duration,
                    continuity={"mode": mode})
    return _FakeShot(id=f"shot-{index}", created_at=index, spec=spec.model_dump(mode="json"))


def test_episode_plan_covers_every_shot_exactly_once():
    shots = [_shot(index) for index in range(1, 5)]
    plan = derive_episode_plan(shots, target_seconds=12, tolerance_seconds=0)
    assert [episode.shot_ids for episode in plan.episodes] == [
        ["shot-1", "shot-2"],
        ["shot-3", "shot-4"],
    ]
    assert validate_episode_coverage(plan, [shot.id for shot in shots]) == []
    assert len(plan.shot_order()) == len(set(plan.shot_order())) == 4


def test_a_continuity_chain_is_never_severed_by_an_episode_boundary():
    # shots 2 and 3 continue their predecessor: no cut may fall before them.
    shots = [_shot(1), _shot(2, mode="last_frame"), _shot(3, mode="last_frame"), _shot(4)]
    plan = derive_episode_plan(shots, target_seconds=8, tolerance_seconds=0)
    boundaries = {tuple(episode.shot_ids[:1]) for episode in plan.episodes}
    assert ("shot-2",) not in boundaries
    assert ("shot-3",) not in boundaries
    assert sorted(plan.shot_order()) == ["shot-1", "shot-2", "shot-3", "shot-4"]


def test_a_long_script_stays_complete_across_many_pages():
    shots = [_shot(index) for index in range(1, 251)]
    plan = derive_episode_plan(shots, target_seconds=60, tolerance_seconds=15)
    order = plan.shot_order()
    assert len(order) == 250
    assert len(set(order)) == 250
    assert len(plan.episodes) > 20


def test_a_plan_that_drops_or_repeats_a_shot_is_refused():
    payload = EpisodePlanPayload.model_validate({
        "target_seconds": 30,
        "episodes": [{"episode_id": "ep-01", "order": 1, "shot_ids": ["a", "b"],
                      "planned_seconds": 12}],
    })
    assert validate_episode_coverage(payload, ["a", "b", "c"]) == ["SHOTS_NOT_COVERED:c"]
    with pytest.raises(ValueError, match="SHOT_COVERED_TWICE"):
        EpisodePlanPayload.model_validate({
            "target_seconds": 30,
            "episodes": [
                {"episode_id": "ep-01", "order": 1, "shot_ids": ["a"], "planned_seconds": 6},
                {"episode_id": "ep-02", "order": 2, "shot_ids": ["a"], "planned_seconds": 6},
            ],
        })


# ---------------------------------------------------------------------------
# Studio fixtures
# ---------------------------------------------------------------------------


def _studio(client, *, shots: int, profile_mode: str = "Ref2VA"):
    project_id = create_project(client, "整部作品")
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    created = []
    for index in range(shots):
        created.append(
            create_shot(
                client, project_id, style["id"],
                title=f"镜头{index + 1}",
                beat=f"第{index + 1}个动作",
                asset_ids=[subject["id"]],
                bindings=[{"asset_id": subject["id"], "use_as": "subject",
                           "role": "CHAR_A", "ordinal": 0}],
            )
        )
    profile = register_workflow_profile(client, mode=profile_mode)
    return {"project": project_id, "style": style, "shots": created, "profile": profile,
            "subject": subject}


def _plan_and_package(client, studio, *, target_seconds: int, batch_limit: int = 1):
    plan = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans",
        json={"target_seconds": target_seconds, "tolerance_seconds": 0},
        headers=LOCAL,
    )
    assert plan.status_code == 201, plan.text
    version = plan.json()["version"]
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{version}/confirm", json={},
        headers=LOCAL,
    )
    assert confirmed.status_code == 200, confirmed.text
    package = client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={
            "workflow_profile_id": studio["profile"]["id"],
            "generation_mode": "Ref2VA",
            "policy": {"batch_shot_limit": batch_limit},
        },
        headers=LOCAL,
    )
    assert package.status_code == 201, package.text
    return confirmed.json(), package.json()


def _confirm_package(client, package_id: str) -> dict:
    response = client.post(f"/api/v1/director-packages/{package_id}/confirm", json={}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _seed_passed_job(factory, settings, project_id, shot_id, style_id):
    content = make_test_video_bytes(duration_s=2.0, width=320, height=240, with_audio=True)
    digest = hashlib.sha256(bytes(content)).hexdigest()
    relative = f"artifacts/{project_id}/run-test/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    session = factory()
    try:
        job = Job(project_id=project_id, shot_id=shot_id, shot_revision=1, style_version_id=style_id,
                  request={}, idempotency_key=uuid4().hex, status="PASSED",
                  quality_verification="real", provider="comfyui", generation_mode="Ref2VA")
        session.add(job)
        session.flush()
        attempt = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint=digest,
                             status="COMPLETED", lease_owner="test-worker",
                             lease_expires_at=utcnow(), output_path=relative,
                             output_sha256=digest, output_bytes=len(content))
        session.add(attempt)
        session.flush()
        artifact = MediaArtifact(project_id=project_id, job_id=job.id, attempt_id=attempt.id,
                                 artifact_kind="video", storage_path=relative, sha256=digest,
                                 byte_size=len(content), content_type="video/mp4", is_mock=False)
        session.add(artifact)
        session.flush()
        for name in ("semantic", "audio", "style", "final"):
            session.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                    gate_name=name, status="passed", is_mock=False))
        session.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id,
                                gate_name="technical", status="passed", is_mock=False,
                                model="ffprobe", coverage="covered"))
        job.artifact_id = artifact.id
        session.commit()
        return job.id
    finally:
        session.close()


def _complete_dispatched_series(factory, settings, run_id: str) -> str | None:
    """Simulate the resident Worker finishing whichever series the run dispatched."""
    with factory() as db:
        batch = db.scalars(
            select(ProductionRunBatch)
            .where(ProductionRunBatch.run_id == run_id, ProductionRunBatch.status.in_(["QUEUED", "RUNNING"]))
        ).first()
        if batch is None or not batch.series_id:
            return None
        series = db.get(ProductionSeries, batch.series_id)
        project_id = series.project_id
        shot_ids = list(batch.shot_ids)
        rows = list(series.items)
    job_ids = []
    style_id = _style_id(factory, project_id)
    for shot_id in shot_ids[: len(rows)]:
        job_ids.append(_seed_passed_job(factory, settings, project_id, shot_id, style_id))
    with factory.begin() as db:
        series = db.get(ProductionSeries, batch.series_id)
        for job_id, item in zip(job_ids, sorted(list(series.items), key=lambda row: row.position), strict=False):
            item.job_id = job_id
            item.status = "PASSED"
        series.status = "COMPLETED"
        series.completed_count = len(job_ids)
        series.finalization_state = "RELEASED"
        series.finished_at = utcnow()
        return series.id


def _style_id(factory, project_id: str) -> str:
    from shine.models import Project

    with factory() as db:
        return db.get(Project, project_id).frozen_style_version_id


def _advance(factory, settings, run_id: str, worker_id: str = "test-worker") -> dict:
    with factory.begin() as db:
        run = db.get(ProductionRun, run_id)
        return run_engine.advance_run(db, run, worker_id=worker_id, settings=settings)


def _run(factory, run_id: str) -> dict:
    with factory() as db:
        return run_engine.run_public(db, db.get(ProductionRun, run_id))


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------


def test_a_package_needs_a_confirmed_episode_plan(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    response = client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={"workflow_profile_id": studio["profile"]["id"]},
        headers=LOCAL,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DIRECTOR_EPISODE_PLAN_NOT_CONFIRMED"


def test_a_package_pins_scope_style_and_binding_then_confirms(api):
    client, factory, settings = api
    studio = _studio(client, shots=4)
    plan, package = _plan_and_package(client, studio, target_seconds=12)

    assert package["status"] == "DRAFT"
    assert package["episode_count"] == 2
    assert package["shot_count"] == 4
    assert package["scope_digest"] and package["manifest_sha256"]
    assert package["style"]["sha256"] == studio["style"]["sha256"]
    assert package["binding"]["graph_sha256"] == studio["profile"]["graph_sha256"]
    assert package["episode_plan"]["sha256"] == plan["sha256"]
    assert [episode["episode_id"] for episode in package["episodes"]] == ["ep-01", "ep-02"]

    ready = client.get(f"/api/v1/director-packages/{package['id']}/readiness")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True, ready.json()
    assert ready.json()["startable"] is False, "an unconfirmed package is not startable"

    confirmed = _confirm_package(client, package["id"])
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["confirmed_at"]
    ready = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert ready["startable"] is True, ready


def test_readiness_reports_a_superseded_style_instead_of_starting(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60)
    _confirm_package(client, package["id"])

    from tests.conftest import style_payload

    client.put(f"/api/v1/projects/{studio['project']}/style/draft",
               json={**style_payload(), "lighting_tone": "全逆光剪影"})
    refrozen = client.post(f"/api/v1/projects/{studio['project']}/style/freeze", json={})
    assert refrozen.status_code in (201, 409)

    readiness = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    codes = {blocker["code"] for blocker in readiness["blockers"]}
    assert "STYLE_SUPERSEDED" in codes or refrozen.status_code == 409
    assert readiness["ready"] is False
    assert readiness["startable"] is False


def test_a_tampered_episode_manifest_is_refused(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60)

    from shine.director.models import DirectorPackageEpisode

    with factory.begin() as db:
        row = db.scalars(
            select(DirectorPackageEpisode).where(DirectorPackageEpisode.package_id == package["id"])
        ).first()
        row.manifest = {**row.manifest, "shots": []}
    readiness = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert any(blocker["code"] == "EPISODE_MANIFEST_TAMPERED" for blocker in readiness["blockers"])
    assert readiness["ready"] is False


# ---------------------------------------------------------------------------
# Run creation
# ---------------------------------------------------------------------------


def test_a_run_requires_a_confirmed_package(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60)
    response = client.post(
        f"/api/v1/projects/{studio['project']}/production-runs",
        json={"package_id": package["id"]},
        headers=LOCAL,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DIRECTOR_PACKAGE_NOT_CONFIRMED"


def test_repeating_the_same_start_returns_the_same_run(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60)
    _confirm_package(client, package["id"])

    first = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                        json={"package_id": package["id"]}, headers=LOCAL)
    second = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                         json={"package_id": package["id"]}, headers=LOCAL)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    with factory() as db:
        assert len(db.scalars(select(ProductionRun)).all()) == 1


def test_a_second_active_run_is_refused(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60)
    _confirm_package(client, package["id"])
    first = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                        json={"package_id": package["id"]}, headers=LOCAL)
    assert first.status_code == 201, first.text
    other = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                        json={"package_id": package["id"], "request_key": "another-shoot",
                              "episode_ids": ["ep-01"]}, headers=LOCAL)
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "PRODUCTION_RUN_ALREADY_ACTIVE"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_one_advance_creates_exactly_one_series(api, monkeypatch):
    client, factory, settings = api
    studio = _studio(client, shots=4)
    _plan, package = _plan_and_package(client, studio, target_seconds=12, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()

    _advance(factory, settings, run["id"])
    _advance(factory, settings, run["id"])
    with factory() as db:
        series = db.scalars(select(ProductionSeries).where(ProductionSeries.project_id == studio["project"])).all()
        batches = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).all()
    assert len(series) == 1, "a live series must not be joined by a second one"
    assert len(batches) == 1
    assert batches[0].series_id == series[0].id
    assert batches[0].status in {"QUEUED", "RUNNING"}


def test_a_finished_batch_dispatches_the_next_one_and_builds_the_episode(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()

    _advance(factory, settings, run["id"])
    assert _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    payload = _run(factory, run["id"])
    assert payload["episodes"][0]["batches"][0]["status"] == "PASSED"
    assert len(payload["episodes"][0]["batches"]) == 2, "the second shot needs its own batch"

    assert _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    with factory() as db:
        orders = db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id == studio["project"])).all()
    assert len(orders) == 1
    assert orders[0].scope_kind == "episode"
    assert orders[0].series_id is None
    assert orders[0].scope_id
    assert orders[0].status == "PENDING"


def test_an_episode_with_a_missing_shot_is_never_queued_for_delivery(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=2)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    _advance(factory, settings, run["id"])

    # Only one of the two shots is finished: the series never completes, so the
    # episode must not be published as if it were whole.
    with factory.begin() as db:
        batch = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).first()
        series = db.get(ProductionSeries, batch.series_id)
        first_item = sorted(list(series.items), key=lambda row: row.position)[0]
        project_id = series.project_id
        shot_id = first_item.shot_id
    job_id = _seed_passed_job(factory, settings, project_id, shot_id, studio["style"]["id"])
    with factory.begin() as db:
        batch = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).first()
        series = db.get(ProductionSeries, batch.series_id)
        first_item = sorted(list(series.items), key=lambda row: row.position)[0]
        first_item.job_id = job_id
        first_item.status = "PASSED"
        series.status = "FAILED"
        series.failure_code = "SHOT_EXHAUSTED"
    _advance(factory, settings, run["id"])

    with factory() as db:
        assert db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id == project_id)).all() == []
    payload = _run(factory, run["id"])
    assert payload["episodes"][0]["status"] == "BLOCKED"
    assert payload["status"] == "NEEDS_REVIEW"


def test_a_series_inside_a_run_does_not_publish_its_own_reel(api):
    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    _advance(factory, settings, run["id"])
    _complete_dispatched_series(factory, settings, run["id"])

    from shine.media.delivery_engine import enqueue_auto_delivery

    with factory.begin() as db:
        batch = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).first()
        series = db.get(ProductionSeries, batch.series_id)
        assert enqueue_auto_delivery(db, series) is None


# ---------------------------------------------------------------------------
# Whole loop with real assembly
# ---------------------------------------------------------------------------


def test_the_episode_is_assembled_from_two_series_and_the_run_completes(api):
    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()

    for _ in range(2):
        _advance(factory, settings, run["id"])
        assert _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    with factory() as db:
        order = db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id == studio["project"])).first()
        series_used = {
            batch.series_id
            for batch in db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"]))
        }
        edl, source, _rules = build_episode_edl(
            db, order, ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path,
            asset_root=settings.data_dir,
        )
    assert len(series_used) == 2, "the episode really spans two series"
    assert source == "run_episode_plan"
    assert len(edl["clips"]) == 2

    from shine.media.delivery_engine import run_pending_deliveries

    run_pending_deliveries(factory, settings=settings, asset_root=settings.data_dir, worker_id="test-worker")
    _advance(factory, settings, run["id"])

    payload = _run(factory, run["id"])
    assert payload["status"] == "COMPLETED", payload
    episode = payload["episodes"][0]
    assert episode["status"] == "DELIVERED"
    assert episode["delivered_artifact_id"]
    assert episode["actual_seconds"] and episode["actual_seconds"] > 0
    assert payload["progress"]["delivered_episode_count"] == 1

    with factory() as db:
        artifact = db.get(MediaArtifact, episode["delivered_artifact_id"])
        assert artifact.artifact_kind == "delivery"
        assert artifact.is_mock is False
        assert len(artifact.provenance["clips"]) == 2
    media = client.get(f"/api/v1/deliveries/{episode['delivered_artifact_id']}/media")
    assert media.status_code == 200
    disposition = media.headers["content-disposition"]
    assert "filename*=utf-8''" in disposition
    assert unquote(disposition.split("filename*=utf-8''", 1)[1]) == "整部作品-第1集.mp4"
    assert len(media.content) == artifact.byte_size


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


def test_pause_stops_new_dispatch_and_resume_continues(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    _advance(factory, settings, run["id"])

    paused = client.post(f"/api/v1/production-runs/{run['id']}/pause", headers=LOCAL)
    assert paused.status_code == 202, paused.text
    assert paused.json()["status"] == "PAUSED"

    _advance(factory, settings, run["id"])
    with factory() as db:
        assert len(db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).all()) == 1

    resumed = client.post(f"/api/v1/production-runs/{run['id']}/resume", headers=LOCAL)
    assert resumed.json()["status"] == "RUNNING"
    assert _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])
    with factory() as db:
        assert len(db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).all()) == 2


def test_cancel_keeps_every_produced_artifact(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    _advance(factory, settings, run["id"])
    _complete_dispatched_series(factory, settings, run["id"])

    cancelled = client.post(f"/api/v1/production-runs/{run['id']}/cancel", headers=LOCAL)
    assert cancelled.json()["status"] == "CANCELLED"
    with factory() as db:
        row = db.get(ProductionRun, run["id"])
        assert row.status == "CANCELLED"
        assert row.finished_at is not None
        artifacts = db.scalars(
            select(MediaArtifact).where(MediaArtifact.project_id == studio["project"])
        ).all()
    assert artifacts, "cancelling must never delete what was already produced"


def test_a_run_in_needs_review_can_be_reconciled_without_new_work(api):
    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    _advance(factory, settings, run["id"])
    with factory.begin() as db:
        batch = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).first()
        series = db.get(ProductionSeries, batch.series_id)
        series.status = "FAILED"
        series.failure_code = "RENDERER_LOST"
    _advance(factory, settings, run["id"])
    reconciled = client.post(f"/api/v1/production-runs/{run['id']}/reconcile", headers=LOCAL)
    assert reconciled.status_code == 202
    assert reconciled.json()["reconcile"]["changed"] >= 0
    with factory() as db:
        assert db.get(ProductionRun, run["id"]).status in {"NEEDS_REVIEW", "RUNNING"}


# ---------------------------------------------------------------------------
# Journey
# ---------------------------------------------------------------------------


def test_the_journey_reads_stage_completion_from_the_ledger(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    journey = client.get(f"/api/v1/projects/{studio['project']}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert [stage["id"] for stage in journey["stages"]] == [
        "script", "plan", "storyboard", "assets", "package", "production", "post",
    ]
    assert stages["script"]["done"] is True
    assert stages["plan"]["done"] is False
    assert stages["package"]["done"] is False
    assert journey["next_stage"] == "plan"

    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    journey = client.get(f"/api/v1/projects/{studio['project']}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert stages["plan"]["done"] is True
    assert stages["storyboard"]["done"] is True
    assert stages["assets"]["done"] is True
    assert stages["package"]["done"] is False

    _confirm_package(client, package["id"])
    journey = client.get(f"/api/v1/projects/{studio['project']}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert stages["package"]["done"] is True
    assert stages["production"]["done"] is False
    assert journey["next_stage"] == "production"


def test_journey_counts_only_confirmed_plan_shots_not_other_passed_project_shots(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])

    extra = create_shot(
        client, studio["project"], studio["style"]["id"], title="未纳入本次计划的旧镜头",
        beat="旧镜头不属于本次确认范围", asset_ids=[studio["subject"]["id"]],
        bindings=[{"asset_id": studio["subject"]["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    )
    for shot in [*studio["shots"], extra]:
        _seed_passed_job(factory, settings, studio["project"], shot["id"], studio["style"]["id"])

    journey = client.get(f"/api/v1/projects/{studio['project']}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert stages["storyboard"]["summary"] == "2 个计划内镜头（项目共 3 个）"
    assert stages["storyboard"]["facts"]["planned_shot_ids"] == [shot["id"] for shot in studio["shots"]]
    assert stages["production"]["summary"] == "2 / 2 镜已通过"
    assert stages["production"]["facts"] == {"passed_shot_count": 2, "scope_shot_count": 2}
    assert stages["production"]["done"] is True


def test_a_plan_for_shots_that_do_not_exist_is_refused(api):
    client, factory, settings = api
    studio = _studio(client, shots=1)
    response = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans",
        json={
            "target_seconds": 30,
            "episodes": [{"episode_id": "ep-01", "order": 1, "shot_ids": ["missing-shot"],
                          "planned_seconds": 6}],
        },
        headers=LOCAL,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EPISODE_PLAN_COVERAGE_INVALID"


def test_episode_rows_keep_plan_order(api):
    client, factory, settings = api
    studio = _studio(client, shots=4)
    _plan, package = _plan_and_package(client, studio, target_seconds=12, batch_limit=2)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    with factory() as db:
        rows = db.scalars(
            select(ProductionRunEpisode)
            .where(ProductionRunEpisode.run_id == run["id"])
            .order_by(ProductionRunEpisode.position)
        ).all()
    assert [row.episode_id for row in rows] == ["ep-01", "ep-02"]
    assert rows[0].shot_ids == [studio["shots"][0]["id"], studio["shots"][1]["id"]]
    assert rows[1].shot_ids == [studio["shots"][2]["id"], studio["shots"][3]["id"]]


def test_the_worker_never_dispatches_a_run_it_cannot_really_render(api, monkeypatch):
    """A run must not create real H3 work on a host that cannot execute it."""
    from dataclasses import replace

    from shine.worker import _advance_production_runs

    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()

    # The run layer reports what it really did: a host that cannot render
    # dispatches nothing, and a host that can dispatches exactly one batch.
    blocked = _advance_production_runs(
        factory, "test-worker", settings=replace(settings, render_provider="mock"))
    assert (blocked.checked, blocked.progressed, blocked.dispatched) == (0, 0, 0)
    monkeypatch.setattr("shine.worker._managed_recipe", lambda _settings: None)
    blocked = _advance_production_runs(
        factory, "test-worker", settings=replace(settings, render_provider="comfyui"))
    assert (blocked.checked, blocked.progressed, blocked.dispatched) == (0, 0, 0)
    with factory() as db:
        assert db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).all() == []

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _settings: {"renderer": {"instance_id": "i"}})
    stepped = _advance_production_runs(
        factory, "test-worker", settings=replace(settings, render_provider="comfyui"))
    assert (stepped.checked, stepped.progressed, stepped.dispatched) == (1, 1, 1)
    with factory() as db:
        batches = db.scalars(select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])).all()
    assert len(batches) == 1


def test_the_episode_list_is_paged_in_the_database(api):
    client, factory, settings = api
    studio = _studio(client, shots=4)
    _plan, package = _plan_and_package(client, studio, target_seconds=12, batch_limit=2)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()

    first = client.get(f"/api/v1/production-runs/{run['id']}/episodes?offset=0&limit=1").json()
    second = client.get(f"/api/v1/production-runs/{run['id']}/episodes?offset=1&limit=1").json()
    assert first["total"] == 2
    assert len(first["items"]) == 1
    assert first["items"][0]["episode_id"] == "ep-01"
    assert second["items"][0]["episode_id"] == "ep-02"
    events = client.get(f"/api/v1/production-runs/{run['id']}/events").json()
    assert any(event["kind"] == "RUN_CREATED" for event in events)
    blockers = client.get(f"/api/v1/production-runs/{run['id']}/blockers").json()
    assert blockers["count"] == 0


def test_materials_are_judged_after_the_storyboard(api):
    """素材门禁属于分镜之后，不属于统筹之前。

    What a shot needs is a question the storyboard asks, so the planning
    stage asks for nothing but the plan and the frozen style, and the
    material library is judged in its own stage, right after the storyboard.
    """
    from sqlalchemy import update

    from shine.models import Asset

    client, factory, settings = api
    project_id = create_project(client, "素材后置")
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    create_shot(
        client, project_id, style["id"], title="镜头1", beat="一个动作",
        asset_ids=[subject["id"]],
        bindings=[{"asset_id": subject["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    )
    plan = client.post(f"/api/v1/projects/{project_id}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/episode-plans/{plan.json()['version']}/confirm",
        json={"expected_sha256": plan.json()["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    # the material was uploaded but never confirmed and locked: it is exactly
    # what the material stage has to name.
    with factory.begin() as db:
        db.execute(update(Asset).where(Asset.id == subject["id"])
                   .values(approved=False, locked=False))

    journey = client.get(f"/api/v1/projects/{project_id}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert [stage["id"] for stage in journey["stages"]] == [
        "script", "plan", "storyboard", "assets", "package", "production", "post",
    ]
    # the planning stage never asks for a material
    assert stages["plan"]["done"] is True, stages["plan"]
    assert stages["plan"]["blockers"] == [], stages["plan"]
    # the storyboard design is finished; the material is not
    assert stages["storyboard"]["done"] is True, stages["storyboard"]
    # and the material stage is the one that names what is missing
    assert stages["assets"]["done"] is False, stages["assets"]
    assert any(row["code"] == "SHOT_REQUIRED_ASSET_NOT_MASTER"
               for row in stages["assets"]["blockers"]), stages["assets"]
    assert journey["next_stage"] == "assets"


def test_the_material_stage_names_a_character_without_a_master(api):
    """A character the storyboard names is a material requirement too.

    The name alone is not renderable, so the material step has to name the
    character that still waits for its master.
    """
    client, factory, settings = api
    project_id = create_project(client, "角色素材")
    style = freeze_style(client, project_id)
    create_shot(client, project_id, style["id"], title="镜头1", beat="一个人走过",
                characters=["林岚"])
    plan = client.post(f"/api/v1/projects/{project_id}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/episode-plans/{plan.json()['version']}/confirm",
        json={"expected_sha256": plan.json()["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    journey = client.get(f"/api/v1/projects/{project_id}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert stages["storyboard"]["done"] is True, stages["storyboard"]
    assert stages["assets"]["done"] is False, stages["assets"]
    assert any(row["code"].startswith("SHOT_CHARACTER_MASTER_MISSING")
               for row in stages["assets"]["blockers"]), stages["assets"]

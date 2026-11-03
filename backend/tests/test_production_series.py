"""The resident Worker must really drive multi-shot H3 production.

These tests cover the scheduling layer that the Web workbench reaches:
a production series is created in the browser, the resident Worker claims it,
drives every shot of it in order behind one managed renderer, and releases the
renderer and the exclusive GPU lease exactly once at the end.

The GPU itself is never touched here.  The real H3 path is proven by the
local acceptance run; these tests prove the *scheduling* contract, including
the failure windows that a live renderer makes expensive to reproduce.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.comfyops.models import ResourceLease
from shine.models import (
    Job,
    JobDependency,
    ProductionSeries,
    ProductionSeriesItem,
    utcnow,
)

from .conftest import (
    PNG,
    approve_lock,
    create_job,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)


def _register_ref2va_profile(client):
    """A Ref2VA profile: the shot under test carries an image reference."""
    return register_workflow_profile(client, mode="Ref2VA")


def _studio_with_shots(client, factory, settings, *, count=3, continuity=None):
    """A project with a frozen style, approved assets and ``count`` shots.

    Every shot carries one subject and one picture reference, so the compiled
    H3 mode is Ref2VA — the same mode the real local production uses.
    """
    project_id = create_project(client, "多镜头生产")
    style = freeze_style(client, project_id)
    # Distinct bytes: the asset store is content addressed, so two identical
    # uploads would be one asset, not two references.
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    picture = upload_asset(client, project_id, content=PNG + b"-picture", filename="场景.png")
    approve_lock(client, subject["id"])
    approve_lock(client, picture["id"])
    shots = []
    for index in range(count):
        shot = create_shot(
            client, project_id, style["id"],
            title=f"镜头{index + 1}",
            beat=f"第{index + 1}个动作",
            asset_ids=[subject["id"], picture["id"]],
            bindings=[
                {"asset_id": subject["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0},
                {"asset_id": picture["id"], "use_as": "picture", "ordinal": 1},
            ],
            continuity=continuity(index) if continuity else {},
        )
        shots.append(shot)
    return project_id, style, shots


def _series_body(shots, profile):
    return {
        "shot_ids": [shot["id"] for shot in shots],
        "provider": "comfyui",
        "workflow_profile_id": profile["id"],
    }


def test_an_ordinary_real_job_stays_out_of_a_series(api):
    """The ordinary "queue this shot" button keeps its own semantics.

    A production series is an explicit, ordered run.  An ordinary queued job
    is driven by the resident Worker through the queue path instead, and the
    two never fight over the same job.
    """
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    job = create_job(
        client, project_id, shots[0]["id"], provider="comfyui", workflow_profile_id=profile["id"],
    )
    with factory() as db:
        assert db.scalar(select(ProductionSeries).where(ProductionSeries.project_id == project_id)) is None
        assert db.get(Job, job["id"]).status == "QUEUED"


def test_the_resident_worker_drives_the_ordinary_queue_behind_one_renderer(api, monkeypatch):
    """An ordinary real job is reachable by the resident Worker."""
    from dataclasses import replace

    from shine.worker import run_configured_series_once

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    create_job(client, project_id, shots[0]["id"], provider="comfyui", workflow_profile_id=profile["id"])

    real_settings = replace(settings, render_provider="comfyui")
    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    monkeypatch.setattr("shine.worker._managed_review_specs", lambda *a, **k: {"VLM": {}})

    captured = {}

    def fake_loop(session_factory, worker_id, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    assert run_configured_series_once(
        factory, "worker-queue", settings=real_settings, semantic_reviewer=object(),
    ) is None
    assert captured["exclude_series_jobs"] is True
    assert captured["first_job_id"] is None


def test_a_production_series_records_its_shot_order(api):
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=3)
    profile = _register_ref2va_profile(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["shot_count"] == 3
    assert [item["shot_id"] for item in body["items"]] == [shot["id"] for shot in shots]
    assert [item["position"] for item in body["items"]] == [0, 1, 2]
    assert body["items"][0]["job_id"]
    assert body["items"][1]["job_id"] is None
    assert body["items"][2]["job_id"] is None


def test_a_second_active_series_for_the_same_shots_is_refused(api):
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    first = client.post(
        f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile)
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile)
    )
    assert second.status_code == 409
    assert "PRODUCTION_SERIES_SHOT_ALREADY_QUEUED" in second.text


def test_unrelated_shots_of_one_project_may_form_their_own_series(api):
    """A shot that is already covered must not be produced twice; others may."""
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=3)
    profile = _register_ref2va_profile(client)
    first = client.post(
        f"/api/v1/projects/{project_id}/production-series",
        json={"shot_ids": [shots[0]["id"]], "workflow_profile_id": profile["id"]},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v1/projects/{project_id}/production-series",
        json={"shot_ids": [shots[1]["id"], shots[2]["id"]], "workflow_profile_id": profile["id"]},
    )
    assert second.status_code == 201, second.text
    assert second.json()["shot_count"] == 2


def test_only_one_worker_can_claim_a_series(api):
    from shine.comfyops.production_series import claim_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))

    first = claim_series(factory, "worker-one")
    second = claim_series(factory, "worker-two")
    assert first is not None
    assert second is None
    with factory() as db:
        series = db.get(ProductionSeries, first.id)
        assert series.worker_id == "worker-one"
        assert series.status == "RUNNING"


def test_the_next_shot_is_only_created_after_its_source_passed(api, monkeypatch):
    """A dependent shot may only consume a real, passed source job."""
    from shine.comfyops import production_series as ps
    from shine.worker import run_managed_series  # noqa: F401  (import guard for the loop)

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))

    series = ps.claim_series(factory, "worker-series")
    assert series is not None

    seen = {}

    def fake_loop(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                  comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                  first_job_id=None, next_job_id=None, on_shot_complete=None, series_id=None):
        current = first_job_id
        shots_done = []
        while current and len(shots_done) < max_shots:
            with session_factory.begin() as db:
                job = db.get(Job, current)
                assert job is not None
                job.status = "PASSED"
                job.artifact_id = "artifact-" + current
            shots_done.append(current)
            if on_shot_complete:
                on_shot_complete(current, None)
            # The next shot must not exist yet while its source still runs.
            with session_factory() as db:
                items = db.scalars(
                    select(ProductionSeriesItem)
                    .where(ProductionSeriesItem.series_id == series_id)
                    .order_by(ProductionSeriesItem.position)
                ).all()
                seen.setdefault("pending_without_job", []).append(
                    [item.shot_id for item in items if item.job_id is None]
                )
            current = next_job_id(current) if next_job_id else None
        seen["shots"] = shots_done
        return shots_done

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    result = ps.run_production_series(
        factory, "worker-series", series.id,
        settings=settings,
        recipe={"renderer": {"instance_id": "i"}},
        review_configs={"VLM": {}},
        semantic_reviewer=object(),
    )
    assert result["status"] == "COMPLETED"
    assert len(seen["shots"]) == 2
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all()
        assert [item.status for item in items] == ["PASSED", "PASSED"]
        second_job = db.get(Job, items[1].job_id)
        assert second_job.id == seen["shots"][1]
        # A cut shot is an independent shot: the order comes from the series,
        # not from a dependency edge that would pretend a visual relation.
        assert db.scalar(
            select(JobDependency).where(JobDependency.child_job_id == second_job.id)
        ) is None


def test_a_continuity_shot_binds_the_real_previous_job(api, monkeypatch):
    """A soft continuity shot consumes the passed job of the shot before it."""
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)

    # The second shot declares a soft continuity relation to the first one.
    with factory() as db:
        from shine.models import Shot

        shot = db.get(Shot, shots[1]["id"])
        shot.spec = {**shot.spec, "continuity": {"mode": "last_frame"}}
        db.commit()

    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-series")
    assert series is not None

    def fake_loop(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                  comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                  first_job_id=None, next_job_id=None, on_shot_complete=None, series_id=None):
        current = first_job_id
        done = []
        while current and len(done) < max_shots:
            with session_factory.begin() as db:
                job = db.get(Job, current)
                job.status = "PASSED"
                job.artifact_id = "artifact-" + current
            done.append(current)
            if on_shot_complete:
                on_shot_complete(current, None)
            current = next_job_id(current) if next_job_id else None
        return done

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    result = ps.run_production_series(
        factory, "worker-series", series.id,
        settings=settings,
        recipe={"renderer": {"instance_id": "i"}},
        review_configs={"VLM": {}},
        semantic_reviewer=object(),
    )
    assert result["status"] == "COMPLETED"
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all()
        assert items[1].continuity_mode == "last_frame"
        second = db.get(Job, items[1].job_id)
        assert second.request["shot_spec"]["continuity"]["previous_job_id"] == items[0].job_id
        assert second.request["shot_spec"]["continuity"]["previous_shot_id"] == shots[0]["id"]
        edge = db.scalar(select(JobDependency).where(JobDependency.child_job_id == second.id))
        assert edge.kind == "continuity:last_frame"


def test_a_failed_shot_blocks_only_its_dependents(api, monkeypatch):
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=3)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-series")

    def fake_loop(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                  comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                  first_job_id=None, next_job_id=None, on_shot_complete=None, series_id=None):
        with session_factory.begin() as db:
            job = db.get(Job, first_job_id)
            job.status = "NEEDS_REVIEW"
            job.failure_code = "QC_NEEDS_REVIEW"
        if on_shot_complete:
            on_shot_complete(first_job_id, None)
        # The next shot must not be queued behind a source that did not pass.
        return next_job_id(first_job_id) if next_job_id else None

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    result = ps.run_production_series(
        factory, "worker-series", series.id,
        settings=settings,
        recipe={"renderer": {"instance_id": "i"}},
        review_configs={"VLM": {}},
        semantic_reviewer=object(),
    )
    assert result["status"] == "NEEDS_REVIEW"
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all()
        assert items[0].status == "BLOCKED"
        series_row = db.get(ProductionSeries, series.id)
        assert series_row.status == "NEEDS_REVIEW"


def test_a_dead_worker_leaves_a_claimable_series(api):
    """A worker that stops renewing must not lock its series forever."""
    from shine.comfyops.production_series import claim_series, reconcile_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))

    series = claim_series(factory, "worker-dead")
    assert series is not None
    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        row.lease_expires_at = utcnow() - timedelta(seconds=5)
        db.commit()

    assert reconcile_series(factory, "worker-next") == 1
    resumed = claim_series(factory, "worker-next")
    assert resumed is not None
    assert resumed.worker_id == "worker-next"


def test_an_alive_worker_keeps_its_series(api):
    from shine.comfyops.production_series import claim_series, reconcile_series, renew_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))

    series = claim_series(factory, "worker-alive")
    assert series is not None
    assert renew_series(factory, series.id, "worker-alive") is True
    assert reconcile_series(factory, "worker-other") == 0
    assert claim_series(factory, "worker-other") is None


def test_cancelling_a_series_stops_its_unfinished_shots(api):

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=3)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))

    response = client.post(f"/api/v1/production-series/{_series_id(factory, project_id)}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem).where(
                ProductionSeriesItem.series_id == _series_id(factory, project_id)
            )
        ).all()
        assert all(item.status == "CANCELLED" for item in items)


def _series_id(factory, project_id) -> str:
    with factory() as db:
        return db.scalar(
            select(ProductionSeries.id).where(ProductionSeries.project_id == project_id)
        )


class _Keeper:
    def __init__(self):
        self.finalized = False
        self.renewal_stopped = False
        self.calls = []

    def stop_renewal(self):
        self.calls.append("stop_renewal")
        self.renewal_stopped = True
        return {"renewal_stopped": True}

    def finalize(self):
        self.calls.append("finalize")
        self.finalized = True
        return {"released": True}


class _Supervisor:
    instance_id = "renderer-1"
    _url = "http://127.0.0.1:8188"

    def __init__(self, *, fail_stop=False):
        self.fail_stop = fail_stop
        self.stopped = False
        self.calls = []

    def stop_closed(self):
        self.calls.append("stop_closed")
        if self.fail_stop:
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        self.stopped = True
        return {"pid": 4242, "active_processes": 0}

    def check_memory(self, gpu_uuid, *, min_ram_mb, min_vram_mb):
        return {"gpu_uuid": gpu_uuid}


def _coordinator(factory, *, lease_id, fail_stop=False):
    keeper = _Keeper()
    supervisor = _Supervisor(fail_stop=fail_stop)
    session = type("S", (), {"lease_id": lease_id})()
    return type(
        "C", (), {
            "keeper": keeper, "replacement": supervisor, "gpu_uuid": "GPU-test",
            "min_ram_mb": 8000, "min_vram_mb": 12000, "session": session,
        }
    )()


def test_the_release_and_the_series_state_commit_together(api):
    """The lease release and the series state are one transaction."""
    from shine.comfyops.production_series import claim_series
    from shine.worker import release_managed_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = claim_series(factory, "worker-series")

    with factory.begin() as db:
        lease = ResourceLease(
            owner="h3:job-1", budget_mb=16384, exclusive=True, priority=100,
            status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        lease_id = lease.id

    result = release_managed_series(
        factory, settings, _coordinator(factory, lease_id=lease_id),
        series_id=series.id, worker_id="worker-series",
    )
    assert result["released"] is True
    with factory() as db:
        lease = db.get(ResourceLease, lease_id)
        row = db.get(ProductionSeries, series.id)
        assert lease.status == "RELEASED"
        assert row.finalization_state == "RELEASED"
        assert row.lease_id is None


def test_release_stops_renewal_before_stopping_the_renderer(api):
    """The heartbeat verifies a live drained renderer, so renewal must stop first."""
    from shine.comfyops.production_series import claim_series
    from shine.worker import release_managed_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = claim_series(factory, "worker-series")
    with factory.begin() as db:
        lease = ResourceLease(
            owner="h3:job-order", budget_mb=16384, exclusive=True, priority=100,
            status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        lease_id = lease.id

    coordinator = _coordinator(factory, lease_id=lease_id)
    release_managed_series(
        factory, settings, coordinator, series_id=series.id, worker_id="worker-series",
    )

    assert coordinator.keeper.calls == ["stop_renewal", "finalize"]
    assert coordinator.replacement.calls == ["stop_closed"]


def test_a_release_that_already_committed_is_not_a_second_release(api):
    from shine.comfyops.production_series import claim_series
    from shine.worker import release_managed_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = claim_series(factory, "worker-series")

    with factory.begin() as db:
        lease = ResourceLease(
            owner="h3:job-1", budget_mb=16384, exclusive=True, priority=100,
            status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        lease_id = lease.id

    release_managed_series(
        factory, settings, _coordinator(factory, lease_id=lease_id),
        series_id=series.id, worker_id="worker-series",
    )
    # The caller never saw the first answer and asks again.
    again = release_managed_series(
        factory, settings, _coordinator(factory, lease_id=lease_id),
        series_id=series.id, worker_id="worker-series",
    )
    assert again["idempotent"] is True
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_a_renderer_that_cannot_stop_keeps_the_lease(api):
    from shine.comfyops.production_series import claim_series
    from shine.worker import release_managed_series

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = claim_series(factory, "worker-series")

    with factory.begin() as db:
        lease = ResourceLease(
            owner="h3:job-1", budget_mb=16384, exclusive=True, priority=100,
            status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        lease_id = lease.id

    with pytest.raises(ValueError, match="COMFY_PROCESS_EXIT_UNCONFIRMED"):
        release_managed_series(
            factory, settings, _coordinator(factory, lease_id=lease_id, fail_stop=True),
            series_id=series.id, worker_id="worker-series",
        )
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "ACTIVE"
        row = db.get(ProductionSeries, series.id)
        # The intent to release is durable, so the run is not silently lost.
        assert row.finalization_state == "FINALIZING"
        assert row.lease_id == lease_id


def test_the_resident_worker_ignores_the_series_path_in_mock_mode(api):
    from shine.worker import run_configured_series_once

    client, factory, settings = api
    assert run_configured_series_once(factory, "worker-mock", settings=settings) is None


def test_the_resident_worker_ignores_the_series_path_without_a_managed_recipe(api, monkeypatch):
    from shine.worker import run_configured_series_once

    client, factory, settings = api
    monkeypatch.delenv("SHINE_MANAGED_RECIPE", raising=False)
    monkeypatch.setattr("shine.worker._managed_recipe", lambda _settings: None)
    assert run_configured_series_once(factory, "worker-norecipe", settings=settings) is None


def test_an_independent_shot_continues_after_a_blocked_one(api, monkeypatch):
    """A shot that needs review blocks only the shots that depend on it."""
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=3)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-series")
    assert series is not None

    def fake_loop(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                  comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                  first_job_id=None, next_job_id=None, on_shot_complete=None, series_id=None):
        current = first_job_id
        done = []
        while current and len(done) < max_shots:
            with session_factory.begin() as db:
                job = db.get(Job, current)
                if len(done) == 1:
                    # The second shot needs a human; its artefact is fine.
                    job.status = "NEEDS_REVIEW"
                    job.failure_code = "SEMANTIC_REVIEW_CONTRACT_INVALID: STYLE_INPUT_DECISION_CONTRADICTION"
                else:
                    job.status = "PASSED"
                    job.artifact_id = "artifact-" + current
            done.append(current)
            if on_shot_complete:
                on_shot_complete(current, None)
            current = next_job_id(current) if next_job_id else None
        return done

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    result = ps.run_production_series(
        factory, "worker-series", series.id,
        settings=settings,
        recipe={"renderer": {"instance_id": "i"}},
        review_configs={"VLM": {}},
        semantic_reviewer=object(),
    )
    assert result["status"] == "NEEDS_REVIEW"
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all()
        # The first shot passed, the second needs review, and the independent
        # third shot still ran.
        assert [item.status for item in items] == ["PASSED", "BLOCKED", "PASSED"]
        assert items[1].failure_code == "SEMANTIC_REVIEW_CONTRACT_INVALID: STYLE_INPUT_DECISION_CONTRADICTION"


def test_a_dependent_shot_stays_blocked_when_its_source_did_not_pass(api, monkeypatch):
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    with factory() as db:
        from shine.models import Shot

        shot = db.get(Shot, shots[1]["id"])
        shot.spec = {**shot.spec, "continuity": {"mode": "last_frame",
                                                 "previous_shot_id": shots[0]["id"]}}
        db.commit()
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-series")
    assert series is not None

    def fake_loop(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                  comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                  first_job_id=None, next_job_id=None, on_shot_complete=None, series_id=None):
        current = first_job_id
        done = []
        while current and len(done) < max_shots:
            with session_factory.begin() as db:
                job = db.get(Job, current)
                job.status = "NEEDS_REVIEW"
                job.failure_code = "SEMANTIC_REVIEW_CONTRACT_INVALID: STYLE_INPUT_DECISION_CONTRADICTION"
            done.append(current)
            if on_shot_complete:
                on_shot_complete(current, None)
            current = next_job_id(current) if next_job_id else None
        return done

    monkeypatch.setattr("shine.worker._run_managed_loop", fake_loop)
    result = ps.run_production_series(
        factory, "worker-series", series.id,
        settings=settings,
        recipe={"renderer": {"instance_id": "i"}},
        review_configs={"VLM": {}},
        semantic_reviewer=object(),
    )
    assert result["status"] == "NEEDS_REVIEW"
    with factory() as db:
        items = db.scalars(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == series.id)
            .order_by(ProductionSeriesItem.position)
        ).all()
        assert items[0].status == "BLOCKED"
        # The dependent shot never got a job, because its source did not pass.
        assert items[1].job_id is None
        assert items[1].status == "BLOCKED"
        assert items[1].failure_code == "SERIES_SOURCE_NOT_PASSED"


def test_a_profile_without_the_declared_media_family_is_refused(api):
    """A profile that declares an audio transfer needs an audio reference."""
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = register_workflow_profile(
        client,
        mode="Ref2VA",
        parameter_map={
            "positive_prompt": "1.inputs.positive_prompt",
            "seed": "1.inputs.seed",
            "duration": "1.inputs.duration",
            "reference_picture_1": "1.inputs.reference_picture_1",
            "reference_audio_1": "1.inputs.reference_audio_1",
        },
        graph={
            "1": {
                "class_type": "H3BaseSampler",
                "inputs": {
                    "positive_prompt": "", "seed": 0, "duration": 6.0,
                    "reference_picture_1": "", "reference_audio_1": "",
                },
            },
            "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
        },
        required_nodes=["H3BaseSampler"],
        media_transfers={
            "image": {"upload_path": "/upload/image", "field_name": "image",
                      "subfolder": "shine", "style": "multipart"},
            "audio": {"upload_path": "/upload/image", "field_name": "image",
                      "subfolder": "shine", "style": "multipart"},
        },
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={"shot_id": shots[0]["id"], "provider": "comfyui", "workflow_profile_id": profile["id"]},
    )
    assert response.status_code == 422, response.text
    assert "WORKFLOW_MEDIA_FAMILY_NOT_BOUND" in response.text
    assert "audio" in response.text


def test_a_production_series_needs_a_real_h3_provider(api):
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    response = client.post(
        f"/api/v1/projects/{project_id}/production-series",
        json={"shot_ids": [shots[0]["id"]], "provider": "mock"},
    )
    assert response.status_code == 422


def test_a_production_series_refuses_a_repeated_shot(api):
    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/production-series",
        json={"shot_ids": [shots[0]["id"], shots[0]["id"]], "workflow_profile_id": profile["id"]},
    )
    assert response.status_code == 422


def test_a_paused_series_keeps_its_owner_and_cannot_be_stolen(api):
    """A pause is a request, not a handover.

    The owner is alive while it holds an unexpired lease, so a second worker
    must not be able to claim the series and decide its outcome.
    """
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-alive")
    assert series is not None

    paused = client.post(f"/api/v1/production-series/{series.id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSE_REQUESTED"

    # The owner keeps its ownership proof while the pause is pending.
    assert ps.renew_series(factory, series.id, "worker-alive") is True
    # A second worker cannot take a series whose owner is still alive.
    assert ps.claim_series(factory, "worker-other") is None
    assert ps.reconcile_series(factory, "worker-other") == 0

    resumed = client.post(f"/api/v1/production-series/{series.id}/resume")
    assert resumed.status_code == 200, resumed.text
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        # The living owner keeps the series; it is not handed to another worker.
        assert row.worker_id == "worker-alive"
        assert row.status == "RUNNING"
    assert ps.claim_series(factory, "worker-other") is None


def test_the_series_records_the_gpu_lease_it_holds(api):
    """The lease is the proof of life of a running series.

    A crashed worker must leave the lease id behind, so that the reconciliation
    can tell "the renderer is still alive" from "nothing runs any more".
    """
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-alive")
    assert series is not None

    with factory.begin() as db:
        lease = ResourceLease(
            owner="h3:job-1", budget_mb=16384, exclusive=True, priority=100,
            status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        lease_id = lease.id

    ps.record_series_lease(factory, series.id, "worker-alive", lease_id)
    with factory() as db:
        assert db.get(ProductionSeries, series.id).lease_id == lease_id

    # While the lease is alive the series must not be re-queued by another
    # worker, even though its owner stopped renewing.
    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert ps.claim_series(factory, "worker-other") is None
    assert ps.reconcile_series(factory, "worker-other") == 0


def test_an_interrupted_release_is_not_left_in_finalizing(api):
    """A release that was interrupted must not stay in a state nobody reads."""
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-alive")
    assert series is not None

    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        row.finalization_state = "FINALIZING"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    ps.reconcile_series(factory, "worker-other")
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        assert row.finalization_state == "RECONCILE_REQUIRED"


def test_a_passed_shot_stranded_in_review_finishes_and_releases_its_renderer(api, monkeypatch):
    """A series whose only shot passed must not stay blocked behind its lease.

    The worker lost its claim mid-handoff, but the shot was collected and
    passed. The recovery stops the recorded renderer only after proving its
    ownership, releases the lease and completes the series in one transaction.
    """
    from shine.comfyops import production_series as ps
    from shine.models import AppSetting

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-owner")
    assert series is not None

    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        item = db.scalar(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == row.id)
            .order_by(ProductionSeriesItem.position)
        )
        job = db.get(Job, item.job_id)
        job.status = "PASSED"
        lease = ResourceLease(
            owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
            status="RECONCILE_REQUIRED", expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(lease)
        db.flush()
        row.status = "NEEDS_REVIEW"
        row.failure_code = "SERIES_EXECUTION_FAILED:HTTPException"
        row.lease_id = lease.id
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(AppSetting(key="renderer-startup:stranded-instance", value={
            "stage": "RUNNING", "instance_id": "stranded-instance", "lease_id": lease.id, "port": 0,
            "process": {"pid": 999999, "birth": "1", "job_name": "Local\\ShineHandoff-" + "a" * 32},
        }))
        lease_id = lease.id
    monkeypatch.setattr(ps, "_stop_owned_renderer", lambda record, timeout_s=20.0: (True, "STOPPED"))
    assert ps.reconcile_series(factory, "worker-next") == 1
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        assert row.status == "COMPLETED"
        assert row.finalization_state == "RELEASED"
        assert row.lease_id is None
        assert row.failure_code is None
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def _dead_renderer_series(api, monkeypatch, *, lease_status="RECONCILE_REQUIRED"):
    """A stale series whose only shot passed while its renderer already died.

    The worker lost the run before it could finish, so the series still claims
    the exclusive GPU lease of a renderer that no longer exists.
    """
    from shine.comfyops import production_series as ps
    from shine.models import AppSetting

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-crashed")
    assert series is not None

    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        item = db.scalar(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == row.id)
            .order_by(ProductionSeriesItem.position)
        )
        job = db.get(Job, item.job_id)
        job.status = "PASSED"
        lease = ResourceLease(
            owner="h3:" + job.id, budget_mb=16384, exclusive=True, priority=100,
            status=lease_status, expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(lease)
        db.flush()
        row.status = "RUNNING"
        row.worker_id = "worker-crashed"
        row.lease_id = lease.id
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(AppSetting(key="renderer-startup:dead-instance", value={
            "stage": "RUNNING", "instance_id": "dead-instance", "lease_id": lease.id, "port": 8188,
            "process": {"pid": 999999, "birth": "1", "job_name": "Local\\ShineHandoff-" + "a" * 32},
        }))
        lease_id = lease.id
    # The recorded renderer is gone and nothing answers on its port.
    monkeypatch.setattr("shine.comfyops.installer.process_identity", lambda pid: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_port_listeners", lambda port: [])
    return series, lease_id


def test_a_stale_series_releases_the_lease_of_a_dead_renderer(api, monkeypatch):
    """A crashed series whose shot passed must release the GPU lease it held.

    The renderer that owned the exclusive lease is provably gone, so the lease
    may be released; the release and the ``COMPLETED`` transition commit
    together, so no later job is blocked by a lease nobody owns any more.
    """
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    series, lease_id = _dead_renderer_series(api, monkeypatch)

    assert ps.reconcile_series(factory, "worker-next") >= 1
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        assert row.status == "COMPLETED"
        assert row.finalization_state == "RELEASED"
        assert row.lease_id is None
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_a_stale_series_keeps_the_lease_when_the_renderer_cannot_be_proved_gone(api, monkeypatch):
    """An unprovable renderer keeps the lease; the series is not closed behind it."""
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    series, lease_id = _dead_renderer_series(api, monkeypatch)
    monkeypatch.setattr(
        ps, "_stop_owned_renderer", lambda record, timeout_s=20.0: (False, "RENDERER_OWNERSHIP_UNVERIFIED")
    )

    ps.reconcile_series(factory, "worker-next")
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        assert row.status != "COMPLETED"
        assert row.finalization_state != "RELEASED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_a_completed_series_with_a_blocked_shot_is_corrected_to_review(api):
    """A run with a blocked shot is not complete; the wrong outcome is corrected.

    A terminal series normally keeps its outcome, but a completion that was
    merged before its shots were decided is wrong, not history.  The correction
    must really change the row and must be counted as a change.
    """
    from shine.comfyops import production_series as ps
    from shine.models import Job

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=1)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-crashed")
    assert series is not None

    with factory.begin() as db:
        row = db.get(ProductionSeries, series.id)
        item = db.scalar(
            select(ProductionSeriesItem)
            .where(ProductionSeriesItem.series_id == row.id)
            .order_by(ProductionSeriesItem.position)
        )
        job = db.get(Job, item.job_id)
        job.status = "NEEDS_REVIEW"
        job.failure_code = "SHOT_NEEDS_REVIEW"
        row.status = "COMPLETED"
        row.finalization_state = "RELEASED"
        row.lease_id = None
        row.completed_count = row.shot_count
        series_id = row.id

    assert ps.reconcile_series(factory, "worker-next") >= 1
    with factory() as db:
        row = db.get(ProductionSeries, series_id)
        assert row.status == "NEEDS_REVIEW"
        assert row.failure_code == "SHOT_NEEDS_REVIEW"

    # A second run has nothing left to correct and must not claim a change.
    assert ps.reconcile_series(factory, "worker-next") == 0


def test_a_cancelled_series_keeps_its_outcome(api):
    """A late worker transition must not rewrite a cancellation."""
    from shine.comfyops import production_series as ps

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-alive")
    assert series is not None

    cancelled = client.post(f"/api/v1/production-series/{series.id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    with factory.begin() as db:
        ps._set_series_state(db, series.id, "worker-alive", status="NEEDS_REVIEW",
                             failure_code="SERIES_INCOMPLETE")
    with factory() as db:
        row = db.get(ProductionSeries, series.id)
        assert row.status == "CANCELLED"
        assert row.failure_code == "SERIES_CANCELLED"


def test_a_failing_shot_callback_still_releases_the_renderer(api, monkeypatch):
    """An exception in the completion callback must not leak the renderer.

    The exclusive GPU lease and the managed renderer must be released on every
    exit path of the managed loop, including the ones that raise.
    """
    from shine.comfyops import production_series as ps
    from shine.worker import _run_managed_loop

    client, factory, settings = api
    project_id, style, shots = _studio_with_shots(client, factory, settings, count=2)
    profile = _register_ref2va_profile(client)
    client.post(f"/api/v1/projects/{project_id}/production-series", json=_series_body(shots, profile))
    series = ps.claim_series(factory, "worker-alive")
    assert series is not None

    released: list[str] = []

    class _Coordinator:
        keeper = type("K", (), {"finalize": staticmethod(lambda: {"released": True})})()
        replacement = type("S", (), {
            "instance_id": "renderer-1", "_url": "http://127.0.0.1:8188",
            "stop_closed": staticmethod(lambda: {"pid": 1, "active_processes": 0}),
            "check_memory": staticmethod(lambda gpu_uuid, *, min_ram_mb, min_vram_mb: {"gpu_uuid": gpu_uuid}),
        })()
        gpu_uuid = "GPU-test"
        min_ram_mb = 8000
        min_vram_mb = 12000
        session = type("S", (), {"lease_id": "lease-1"})()

    monkeypatch.setattr(
        "shine.worker.release_managed_series",
        lambda *args, **kwargs: released.append(kwargs.get("series_id") or "none"),
    )

    class _Continuation:
        coordinator = _Coordinator()

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("shine.worker._FirstManagedContinuation", _Continuation)

    # A real queued job of the series, created through the ordinary API.
    queued = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={"shot_id": shots[0]["id"], "provider": "comfyui",
              "workflow_profile_id": profile["id"]},
    )
    assert queued.status_code == 201, queued.text
    job_id = queued.json()["id"]

    def fake_run_one(session_factory, worker_id, *, settings, comfy_service, comfy_client,
                     semantic_reviewer, asr_reviewer, expected_job_id, managed_continuation):
        return expected_job_id

    monkeypatch.setattr("shine.worker._run_one", fake_run_one)

    def failing_callback(job_id, coordinator_):
        raise RuntimeError("SHOT_COMPLETE_CALLBACK_FAILED")

    with pytest.raises(RuntimeError, match="SHOT_COMPLETE_CALLBACK_FAILED"):
        _run_managed_loop(
            factory, "worker-alive",
            settings=settings,
            recipe={"renderer": {"instance_id": "renderer-1"}},
            review_configs={"VLM": {}},
            semantic_reviewer=object(),
            first_job_id=job_id,
            next_job_id=lambda _job_id: None,
            on_shot_complete=failing_callback,
            series_id=series.id,
        )
    # The renderer and its exclusive lease are released even though the run
    # ended with an exception.
    assert released == [series.id]

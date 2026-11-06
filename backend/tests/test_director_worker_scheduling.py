"""The Worker scheduling order: who gets the tick, and who must not be starved.

The defect this file pins down: the whole-film run layer reported progress
every time it merely looked at its own state, so the Worker returned from the
run step and never reached the series executor or the episode assembly.  A
pending delivery order that already existed was counted as new work for the
same reason.

These tests are the formal home of the independent probes that found it; the
original probe files stay untouched and independently re-runnable.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select

from shine.media.delivery_models import DeliveryOrder
from shine.models import Job, ProductionSeries
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from tests.test_director_long_form import (
    LOCAL,
    _advance,
    _complete_dispatched_series,
    _confirm_package,
    _plan_and_package,
    _studio,
)


def _run_for(client, studio, *, target_seconds: int = 60, batch_limit: int = 1) -> dict:
    _plan, package = _plan_and_package(client, studio, target_seconds=target_seconds,
                                       batch_limit=batch_limit)
    _confirm_package(client, package["id"])
    response = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                            json={"package_id": package["id"]}, headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _worker_settings(settings):
    return replace(settings, render_provider="comfyui")


def test_the_worker_serves_the_series_after_advancing_a_run(api, monkeypatch):
    """A run that dispatched a batch must not keep the tick from its own series."""
    from shine import worker
    from shine.media import delivery_engine
    from shine.quality import runtime

    client, factory, settings = api
    studio = _studio(client, shots=2)
    _run_for(client, studio)

    calls: list[str] = []
    monkeypatch.setattr(runtime, "configured_reviewers", lambda *a, **k: (None, None))
    monkeypatch.setattr(worker, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(worker, "_managed_recipe", lambda *a, **k: {"acceptance": "gate-only"})
    monkeypatch.setattr(worker, "run_configured_series_once",
                        lambda *a, **k: calls.append("series") or "series-serviced")
    monkeypatch.setattr(delivery_engine, "run_pending_deliveries",
                        lambda *a, **k: calls.append("delivery") or 0)

    real = _worker_settings(settings)
    for _ in range(4):
        worker._run_configured_once(factory, None, "acceptance-worker", settings=real)

    assert "series" in calls, ("the run layer kept every tick: the queued series never reached "
                                "its executor")
    with factory() as db:
        states = [row.status for row in db.scalars(select(ProductionSeries)).all()]
    assert states == ["QUEUED"], "the series exists and waits for the executor"

def test_the_worker_serves_a_pending_episode_delivery(api, monkeypatch):
    """An existing PENDING order is real work for the assembly executor."""
    from shine import worker
    from shine.media import delivery_engine
    from shine.quality import runtime

    client, factory, settings = api
    studio = _studio(client, shots=2)
    run = _run_for(client, studio)

    for _ in range(2):
        _advance(factory, settings, run["id"])
        _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    calls: list[str] = []
    monkeypatch.setattr(runtime, "configured_reviewers", lambda *a, **k: (None, None))
    monkeypatch.setattr(worker, "recover_expired_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(worker, "_managed_recipe", lambda *a, **k: {"acceptance": "gate-only"})
    monkeypatch.setattr(delivery_engine, "run_pending_deliveries",
                        lambda *a, **k: calls.append("delivery") or 1)

    real = _worker_settings(settings)
    for _ in range(4):
        worker._run_configured_once(factory, None, "test-worker", settings=real)

    assert calls, ("a pending episode order was re-reported as progress every tick and never "
                   "reached the assembly executor")


def test_the_run_layer_reports_a_check_as_a_check(api, monkeypatch):
    """Looking is not advancing: the run layer must say so honestly."""
    from shine.worker import _advance_production_runs

    client, factory, settings = api
    studio = _studio(client, shots=2)
    _run_for(client, studio)

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    real = _worker_settings(settings)

    first = _advance_production_runs(factory, "worker-a", settings=real)
    assert first.checked == 1 and first.progressed == 1 and first.dispatched == 1

    second = _advance_production_runs(factory, "worker-a", settings=real)
    assert second.checked == 1, "the run is still inspected"
    assert second.progressed == 0, "nothing changed, so nothing was advanced"
    assert second.dispatched == 0


def test_an_existing_pending_order_is_not_new_work(api, monkeypatch):
    """A PENDING order that already exists must never look like fresh progress."""
    from shine.worker import _advance_production_runs

    client, factory, settings = api
    studio = _studio(client, shots=2)
    run = _run_for(client, studio)

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    real = _worker_settings(settings)

    for _ in range(2):
        _advance(factory, settings, run["id"])
        _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    with factory() as db:
        orders = db.scalars(select(DeliveryOrder).where(
            DeliveryOrder.project_id == studio["project"])).all()
    assert len(orders) == 1 and orders[0].status == "PENDING"

    # The next beat finds the same order.  It is a check, not new work, so the
    # Worker keeps the turn for the assembly executor.
    step = _advance_production_runs(factory, "worker-a", settings=real)
    assert step.progressed == 0, "a reused pending order must not be reported as progress"
    with factory() as db:
        orders = db.scalars(select(DeliveryOrder).where(
            DeliveryOrder.project_id == studio["project"])).all()
    assert len(orders) == 1, "the pending order was reused, not duplicated"

def test_two_runs_take_turns_instead_of_one_starving_the_other(api, monkeypatch):
    """A busy run must not keep the other run from ever being advanced."""
    from shine.worker import _advance_production_runs

    client, factory, settings = api
    first = _run_for(client, _studio(client, shots=2))
    second = _run_for(client, _studio(client, shots=2))

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    real = _worker_settings(settings)

    for _ in range(4):
        _advance_production_runs(factory, "worker-a", settings=real)

    with factory() as db:
        dispatched = {
            row.run_id for row in db.scalars(select(ProductionRunBatch)).all()
            if row.series_id
        }
    assert first["id"] in dispatched, "the first run was advanced"
    assert second["id"] in dispatched, "the second run was not starved by the first"


def test_the_run_layer_does_not_swallow_the_ordinary_queue(api, monkeypatch):
    """A whole-film run and an ordinary queued job may live side by side."""
    from shine.worker import _advance_production_runs
    from tests.conftest import create_job

    client, factory, settings = api
    studio = _studio(client, shots=2)
    run = _run_for(client, studio)

    # An ordinary queued job of another project must stay ordinary.
    other = _studio(client, shots=1)
    ordinary = create_job(client, other["project"], other["shots"][0]["id"],
                           provider="comfyui", workflow_profile_id=other["profile"]["id"])

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    real = _worker_settings(settings)
    _advance_production_runs(factory, "worker-a", settings=real)

    with factory() as db:
        job = db.get(Job, ordinary["id"])
        batches = db.scalars(select(ProductionRunBatch).where(
            ProductionRunBatch.run_id == run["id"])).all()
    assert job.status == "QUEUED", "the run layer must not consume the ordinary queue"
    assert len(batches) == 1 and batches[0].series_id, "the run still dispatched its own batch"


def test_a_run_is_never_completed_before_every_episode_is_delivered(api, monkeypatch):
    """One finished batch is not a finished film."""
    client, factory, settings = api
    studio = _studio(client, shots=4)
    run = _run_for(client, studio, target_seconds=12, batch_limit=1)

    _advance(factory, settings, run["id"])
    _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])

    with factory() as db:
        row = db.get(ProductionRun, run["id"])
        episodes = db.scalars(select(ProductionRunEpisode).where(
            ProductionRunEpisode.run_id == run["id"])).all()
    assert row.status != "COMPLETED", "a partly produced film is not a completed film"
    assert len(episodes) >= 2
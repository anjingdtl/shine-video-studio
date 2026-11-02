"""Unattended multi-shot production behind one managed renderer.

The series driver is the product path for "queue the shots and let the worker
drive them": the first shot owns a fresh managed startup, every following shot
continues the same fenced renderer through the idle keeper's lease transfer,
and the series ends by stopping the owned renderer and releasing its lease.
"""

import os

import pytest
from fastapi import HTTPException

from shine.comfyops.handoff_idle import IdleHandoffKeeper
from shine.comfyops.renderer_supervisor import RendererSupervisor


class _Group:
    pid = 123
    job_name = "Local\\ShineHandoff-" + "a" * 32
    starts_drained = False

    def owns(self, pid, birth):  # pragma: no cover - identity stub
        return True

    def terminate(self, timeout=10):
        return {"pid": self.pid, "active_processes": 0}


class _DrainedGroup(_Group):
    starts_drained = True


def test_series_rejects_a_missing_recipe_but_allows_advisory_reviews_to_be_absent(api):
    from shine.worker import run_managed_series

    _client, factory, settings = api
    for kwargs in (
        {"recipe": {}, "review_configs": {"VLM": {}}},
        {"recipe": {"renderer": {}}, "review_configs": {"VLM": {}}},
    ):
        with pytest.raises(HTTPException, match="COMFY_STARTUP_ARGUMENTS_INVALID"):
            run_managed_series(factory, "worker-series", settings=settings, **kwargs)

    # The H3 renderer is the production prerequisite. Optional local review
    # services may be absent, and an empty queue must remain a no-op.
    assert run_managed_series(
        factory, "worker-series", settings=settings,
        recipe={"renderer": {"instance_id": "i"}}, review_configs={},
    ) == []


def test_series_rejects_an_invalid_length(api):
    from shine.worker import run_managed_series

    _client, factory, settings = api
    with pytest.raises(HTTPException, match="COMFY_SERIES_LENGTH_INVALID"):
        run_managed_series(factory, "worker-series", settings=settings,
                           recipe={"renderer": {"instance_id": "i"}}, review_configs={"VLM": {}},
                           max_shots=0, semantic_reviewer=object())


def test_series_returns_empty_without_a_queued_comfyui_job(api):
    from shine.worker import run_managed_series

    _client, factory, settings = api
    assert run_managed_series(factory, "worker-series", settings=settings,
                               recipe={"renderer": {"instance_id": "i"}}, review_configs={"VLM": {}},
                               semantic_reviewer=object()) == []


def test_stop_closed_requires_a_closed_start():
    group = _Group()
    supervisor = RendererSupervisor(group, "http://127.0.0.1:8190", "t" * 40, "a" * 64)
    with pytest.raises(ValueError, match="COMFY_CLOSED_START_REQUIRED"):
        supervisor.stop_closed()


def test_stop_closed_proves_the_owned_group_is_empty():
    group = _DrainedGroup()
    supervisor = RendererSupervisor(group, "http://127.0.0.1:8190", "t" * 40, "a" * 64)
    stopped = supervisor.stop_closed()
    assert stopped["active_processes"] == 0
    assert stopped["pid"] == group.pid
    with pytest.raises(ValueError, match="COMFY_PROCESS_EXIT_UNCONFIRMED"):
        supervisor.stop_closed()


def test_idle_keeper_finalize_ends_renewal_without_failing_the_handoff(monkeypatch):
    supervisor = RendererSupervisor(_Group(), "http://127.0.0.1:8190", "t" * 40, "a" * 64)
    keeper = IdleHandoffKeeper(None, "a" * 36, supervisor)
    failed = []
    monkeypatch.setattr(keeper, "_fail", lambda: failed.append(True))
    assert keeper.finalize() == {"released": True}
    keeper.close()
    assert failed == []
    assert keeper._error is None


def test_idle_keeper_close_without_a_release_still_fails(monkeypatch):
    supervisor = RendererSupervisor(_Group(), "http://127.0.0.1:8190", "t" * 40, "a" * 64)
    keeper = IdleHandoffKeeper(None, "a" * 36, supervisor)
    failed = []
    monkeypatch.setattr(keeper, "_fail", lambda: failed.append(True))
    keeper.close()
    assert failed == [True]


def test_release_managed_series_requires_a_live_coordinator():
    from shine.worker import release_managed_series

    with pytest.raises(HTTPException, match="HANDOFF_COORDINATOR_INVALID"):
        release_managed_series(None, None, None)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are required")
def test_release_managed_series_keeps_the_lease_when_the_renderer_cannot_stop():
    from shine.worker import release_managed_series

    class _Coordinator:
        keeper = type("K", (), {
            "stop_renewal": staticmethod(lambda: {"renewal_stopped": True}),
            "finalize": staticmethod(lambda: {"released": True}),
        })()
        replacement = RendererSupervisor(_Group(), "http://127.0.0.1:8190", "t" * 40, "a" * 64)
        gpu_uuid = "GPU-test"
        min_ram_mb = 8000
        min_vram_mb = 12000
        session = type("S", (), {"lease_id": "lease"})()

    with pytest.raises(ValueError, match="COMFY_CLOSED_START_REQUIRED"):
        release_managed_series(None, None, _Coordinator())

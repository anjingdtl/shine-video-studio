"""The whole-film run, driven by the resident Worker's own scheduling entry.

Evidence level
---------------
``OFFLINE_VERIFIED`` for the *scheduling* half of the product: the Worker's
tick order, the run layer's fairness between runs, the series claim, the
batch and shot state machine, the per-episode delivery order and the real
FFmpeg assembly.

``REPLACED`` at the render boundary only.  The physical ComfyUI process (its
GPU lease and its PID/birth/Job-Object ownership proofs) and the H3
inference itself are replaced by a fake ComfyUI server.  Everything the fake
produces is a synthetic test pattern: **no real MiniMax-H3 output exists in
this file** and nothing here may be read as a real video-quality result.

Everything else is the product's own code path: the test never calls
``advance_run`` by hand, never marks a job or a series PASSED and never fills
a delivery in by hand.  The Worker's tick is the only driver.
"""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest
from sqlalchemy import select

from shine.comfy.client import ComfyUIClient
from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler
from shine.media.delivery_models import DeliveryOrder
from shine.models import Job, MediaArtifact, ProductionSeries
from shine.production.episode_delivery import build_episode_edl
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
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
from tests.fake_http import FakeComfyServer
from tests.test_director_long_form import (
    LOCAL,
    _confirm_package,
    _plan_and_package,
)

pytestmark = pytest.mark.integration


def _has_ffmpeg(settings) -> bool:
    return bool(shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path))


REF2VA_GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {
            "positive_prompt": "",
            "seed": 0,
            "duration": 6.0,
            "reference_subject_1": "",
        },
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}

REF2VA_PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_subject_1": "1.inputs.reference_subject_1",
}


def _film_studio(client, *, shots: int):
    """A studio whose workflow profile really maps the bound subject reference.

    A profile without the reference slot is refused by name
    (``PENDING_WORKFLOW_MAPPING``), which is the product's rule: a bound
    master asset is never dropped silently.
    """
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
    profile = register_workflow_profile(
        client, mode="Ref2VA", graph=REF2VA_GRAPH, parameter_map=REF2VA_PARAM_MAP,
    )
    return {"project": project_id, "style": style, "shots": created,
            "profile": profile, "subject": subject}


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def _install_render_boundary(monkeypatch, fake_comfy, calls):
    """Replace the physical renderer only; every other product path stays real.

    The fake honours the same contract as the real managed loop: it renders
    ``first_job_id``, then asks ``next_job_id`` for the following shot and
    reports every finished shot to ``on_shot_complete``.  The series' own
    shot order, its dependent-shot rule and its terminal decision therefore
    stay in the product's code, and the shot itself is produced by the real
    job/attempt/QC/gate path.
    """
    from shine import worker

    def fake_managed_loop(session_factory, worker_id, *, settings, recipe, review_configs,
                          max_shots=8, comfy_service=None, comfy_client=None,
                          semantic_reviewer=None, asr_reviewer=None,
                          first_job_id=None, next_job_id=None, on_shot_complete=None,
                          exclude_series_jobs=False, series_id=None):
        client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
        scheduler = ResourceScheduler()
        shots: list[str] = []
        job_id = first_job_id
        while job_id and len(shots) < max_shots:
            calls.append(job_id)
            # The exclusive GPU admission is the product's own ledger, so the
            # serial model stage is still real in this test.
            owner = "h3:" + job_id
            with session_factory.begin() as db:
                lease = scheduler.enqueue(db, owner, 16384, True, 100)
                if lease.status == "RELEASED":
                    lease.status = "WAITING"
                db.commit()
                lease_id = lease.id
            with session_factory() as db:
                admitted = scheduler.admit(db, lease_id)
            if not admitted:
                break
            token = CURRENT_RESOURCE.set(lease_id)
            try:
                result = worker._run_one(
                    session_factory, worker_id=worker_id, settings=settings,
                    comfy_client=client, semantic_reviewer=semantic_reviewer,
                    asr_reviewer=asr_reviewer, expected_job_id=job_id,
                )
            finally:
                CURRENT_RESOURCE.reset(token)
                with session_factory.begin() as db:
                    scheduler.release(db, lease_id, owner, reconciled=True)
            if result is None:
                break
            shots.append(result)
            if on_shot_complete is not None:
                on_shot_complete(result, None)
            job_id = next_job_id(result) if next_job_id is not None else None
        return shots

    monkeypatch.setattr(worker, "_run_managed_loop", fake_managed_loop)


def test_the_resident_worker_finishes_a_whole_film_without_a_hand(api, monkeypatch, fake_comfy):
    """Three episodes, several batches each, assembled by the Worker alone."""
    from shine import worker

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    real = replace(settings, render_provider="comfyui")
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6.0)

    studio = _film_studio(client, shots=6)
    _plan, package = _plan_and_package(client, studio, target_seconds=12, batch_limit=1)
    _confirm_package(client, package["id"])
    response = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                            json={"package_id": package["id"]}, headers=LOCAL)
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    render_calls: list[str] = []
    _install_render_boundary(monkeypatch, fake_comfy, render_calls)

    # The Worker's tick is the only driver of this run.
    early_completion: list[list[str]] = []
    ticks = 0
    for _ in range(300):
        ticks += 1
        worker.run_configured_once(factory, None, "worker-film", settings=real)
        with factory() as db:
            run = db.get(ProductionRun, run_id)
            episodes = list(db.scalars(
                select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run_id)
            ).all())
            if run.status == "COMPLETED":
                if any(episode.status != "DELIVERED" for episode in episodes):
                    early_completion.append([episode.status for episode in episodes])
                break

    assert early_completion == [], "the run reported COMPLETED before every episode was delivered"

    with factory() as db:
        run = db.get(ProductionRun, run_id)
        episodes = list(db.scalars(
            select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run_id)
        ).all())
        batches = list(db.scalars(
            select(ProductionRunBatch).where(ProductionRunBatch.run_id == run_id)
        ).all())
        series = list(db.scalars(select(ProductionSeries)).all())
        orders = list(db.scalars(
            select(DeliveryOrder).where(DeliveryOrder.project_id == studio["project"])
        ).all())
        jobs = list(db.scalars(select(Job).where(Job.project_id == studio["project"])).all())
        artifacts = list(db.scalars(
            select(MediaArtifact).where(MediaArtifact.project_id == studio["project"])
        ).all())

    assert run.status == "COMPLETED", run.status
    assert len(episodes) == 3, "the whole film must cover three episodes"
    assert all(episode.status == "DELIVERED" for episode in episodes)
    assert all(episode.delivered_artifact_id for episode in episodes)
    # Several batches per episode: one episode really spans more than one series.
    assert len(batches) == 6, f"expected six batches, found {len(batches)}"
    assert len(series) == 6, f"expected six production series, found {len(series)}"
    assert len(orders) == 3, f"expected three episode delivery orders, found {len(orders)}"
    assert all(order.status == "DELIVERED" for order in orders)

    # No shot is missing and none is produced twice.
    planned = [shot_id for episode in episodes for shot_id in episode.shot_ids]
    assert len(planned) == 6 and len(set(planned)) == 6
    rendered = [job.shot_id for job in jobs if job.shot_id]
    assert sorted(rendered) == sorted(planned), "every planned shot is rendered exactly once"
    assert len(render_calls) == 6, f"the renderer was asked for {len(render_calls)} shots"
    assert fake_comfy.prompt_count == 6, "one external submission per shot, no duplicate"

    # Each episode really assembled its own film out of its own two batches.
    with factory() as db:
        for order in orders:
            episode = db.get(ProductionRunEpisode, order.scope_id)
            edl, source, _rules = build_episode_edl(
                db, order, ffprobe_path=settings.ffprobe_path,
                ffmpeg_path=settings.ffmpeg_path, asset_root=settings.data_dir,
            )
            assert source == "run_episode_plan"
            assert [clip["shot_id"] for clip in edl["clips"]] == list(episode.shot_ids), (
                "an episode may only use the takes of its own shots"
            )
            assert len(edl["clips"]) == 2, "each episode is assembled from two batches"

    # The deliveries are real FFmpeg output, not a marked-as-done row.  Three
    # episodes of identical synthetic footage legitimately share one
    # artifact row (the assembly reuses a byte-identical reel), so the
    # evidence is the EDL above plus each episode's own measured length.
    delivered = [artifact for artifact in artifacts if artifact.artifact_kind == "delivery"]
    assert delivered, "the run produced no assembled film"
    for artifact in delivered:
        assert artifact.is_mock is False
        assert artifact.byte_size > 0
        assert len(artifact.provenance["clips"]) == 2
    for episode in episodes:
        assert episode.actual_seconds and episode.actual_seconds > 10, (
            "each episode is about twelve seconds of assembled film"
        )

    # The film is longer than the local three-segment limit.
    assert sum(episode.actual_seconds or 0.0 for episode in episodes) > 30

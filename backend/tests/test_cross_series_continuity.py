"""A later series must continue an earlier one with real derived media.

Evidence level
---------------
``OFFLINE_VERIFIED``.  The physical ComfyUI process and the H3 inference are
replaced by a fake renderer that returns synthetic test footage.  Every other
path is the product's own: the resident Worker's scheduling entry, the series
claim, the batch plan, the source resolution, the FFmpeg derivation of the
preceding shot's final frame, the request manifest, the preproduction gate
and the reference upload to the renderer.

Nothing here may be read as a real H3 result: the bytes the renderer returns
are a test pattern, and the derived frame is a frame of that pattern.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace

import pytest
from sqlalchemy import select

from shine.models import Job, MediaArtifact, ProductionSeries
from shine.production.models import ProductionRun, ProductionRunBatch
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
from tests.test_director_long_form import LOCAL, _confirm_package
from tests.test_director_worker_integration import _install_render_boundary

GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {
            "positive_prompt": "",
            "seed": 0,
            "duration": 6.0,
            "reference_subject_1": "",
            "reference_picture_1": "",
        },
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}

PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_subject_1": "1.inputs.reference_subject_1",
    "reference_picture_1": "1.inputs.reference_picture_1",
}


def _has_ffmpeg(settings) -> bool:
    return bool(shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path))


def _chained_studio(client, *, shots: int):
    """A project whose shots form one continuity chain."""
    project_id = create_project(client, "跨批次续接")
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    created = []
    for index in range(shots):
        continuity = {} if index == 0 else {"mode": "last_frame", "previous_shot_id": created[-1]["id"]}
        created.append(
            create_shot(
                client, project_id, style["id"],
                title=f"镜头{index + 1}",
                beat=f"第{index + 1}个动作",
                asset_ids=[subject["id"]],
                bindings=[{"asset_id": subject["id"], "use_as": "subject",
                           "role": "CHAR_A", "ordinal": 0}],
                continuity=continuity,
            )
        )
    profile = register_workflow_profile(
        client, mode="Ref2VA", graph=GRAPH, parameter_map=PARAM_MAP,
    )
    return {"project": project_id, "style": style, "shots": created,
            "profile": profile, "subject": subject}


def _plan_and_run(client, studio, *, target_seconds: int):
    plan = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans",
        json={"target_seconds": target_seconds, "tolerance_seconds": 0},
        headers=LOCAL,
    )
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers=LOCAL,
    )
    assert confirmed.status_code == 200, confirmed.text
    package = client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={
            "workflow_profile_id": studio["profile"]["id"],
            "generation_mode": "Ref2VA",
            "policy": {"batch_shot_limit": 1, "split_continuity": True},
        },
        headers=LOCAL,
    )
    assert package.status_code == 201, package.text
    _confirm_package(client, package.json()["id"])
    response = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                            json={"package_id": package.json()["id"]}, headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def test_a_later_series_binds_the_earlier_series_real_media(api, monkeypatch, fake_comfy):
    """Four chained shots, one shot per batch: three of them cross a series."""
    from shine import worker

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    real = replace(settings, render_provider="comfyui")
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6.0)

    studio = _chained_studio(client, shots=4)
    run = _plan_and_run(client, studio, target_seconds=24)

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    render_calls: list[str] = []
    _install_render_boundary(monkeypatch, fake_comfy, render_calls)

    for _ in range(300):
        worker.run_configured_once(factory, None, "worker-continuity", settings=real)
        with factory() as db:
            if db.get(ProductionRun, run["id"]).status == "COMPLETED":
                break

    with factory() as db:
        state = db.get(ProductionRun, run["id"])
        batches = list(db.scalars(
            select(ProductionRunBatch).where(ProductionRunBatch.run_id == run["id"])
            .order_by(ProductionRunBatch.position)
        ).all())
        series = list(db.scalars(
            select(ProductionSeries).where(ProductionSeries.project_id == studio["project"])
        ).all())
        jobs = {job.shot_id: job for job in db.scalars(
            select(Job).where(Job.project_id == studio["project"])
        ).all()}
        derivatives = list(db.scalars(
            select(MediaArtifact).where(
                MediaArtifact.project_id == studio["project"],
                MediaArtifact.artifact_kind == "continuity",
            )
        ).all())

    assert state.status == "COMPLETED", state.status
    assert len(batches) == 4, "the chain must be split into one batch per shot"
    assert len(series) == 4, "every batch owns its own production series"
    assert len(render_calls) == 4, "the renderer was asked for one shot per batch"

    shot_ids = [shot["id"] for shot in studio["shots"]]
    for index, shot_id in enumerate(shot_ids):
        job = jobs[shot_id]
        assert job.status == "PASSED", (shot_id, job.status, job.failure_code)
        manifest = job.request.get("reference_manifest") or []
        refs = [ref for ref in manifest if ref.get("role") == "continuity_derivative"]
        if index == 0:
            assert refs == [], "the first shot of the film continues nothing"
            continue
        assert len(refs) == 1, "a continuation carries exactly one derived reference"
        derived = refs[0]
        assert derived["content_type"] == "image/png"
        # The derived reference points at the job that really produced the
        # preceding shot, never at an unrelated or newer take.
        source_job = jobs[shot_ids[index - 1]]
        artifact = next(a for a in derivatives if a.id == derived["asset_id"])
        assert artifact.provenance["source"]["job_id"] == source_job.id
        assert artifact.provenance["source"]["shot_id"] == shot_ids[index - 1]
        assert artifact.provenance["mode"] == "last_frame"
        assert artifact.is_mock is False
        assert derived["label"] in job.request["compiled_prompt"], (
            "the compiled prompt must name the derived reference"
        )

    # The derived bytes are real decoded frames, not the master's image.
    assert len(derivatives) == 3
    for artifact in derivatives:
        payload = (settings.data_dir / artifact.storage_path).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n"), "the derivative must be a real PNG frame"
        assert hashlib.sha256(payload).hexdigest() == artifact.sha256

    # The renderer really received the derived reference bytes.
    uploaded = {upload["filename"] for upload in fake_comfy.uploads}
    for artifact in derivatives:
        assert f"shine_{artifact.id}.png" in uploaded, (
            "the derived reference was never uploaded to the renderer"
        )


def test_a_continuation_without_a_passed_source_never_renders(api, monkeypatch, fake_comfy):
    """A shot whose source never passed is refused, not silently cut."""
    from shine import worker

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    real = replace(settings, render_provider="comfyui")
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6.0)

    studio = _chained_studio(client, shots=2)
    # The first shot never produced anything, so the second shot has no source.
    response = client.post(
        f"/api/v1/projects/{studio['project']}/production-series",
        json={"shot_ids": [studio["shots"][1]["id"]], "provider": "comfyui",
              "workflow_profile_id": studio["profile"]["id"], "generation_mode": "Ref2VA"},
        headers=LOCAL,
    )
    assert response.status_code == 201, response.text
    series_id = response.json()["id"]

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    render_calls: list[str] = []
    _install_render_boundary(monkeypatch, fake_comfy, render_calls)

    for _ in range(10):
        worker.run_configured_once(factory, None, "worker-continuity", settings=real)

    with factory() as db:
        series = db.get(ProductionSeries, series_id)
        jobs = list(db.scalars(
            select(Job).where(Job.project_id == studio["project"])
        ).all())
    assert render_calls == [], "the renderer must never be asked for an unsourced shot"
    assert jobs == [], "no job may exist for a shot whose source never passed"
    assert series.status in {"NEEDS_REVIEW", "QUEUED", "RUNNING"}, series.status
    if series.status == "NEEDS_REVIEW":
        assert "SOURCE" in (series.failure_code or ""), series.failure_code

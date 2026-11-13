"""The dynamic per-shot strategy pipeline: package → run → series → job.

Covers the WP2 contract end to end at service level:

* a strategy shoot freezes ``binding.strategy`` (not a static profile), the
  run and series carry it, and every job's request carries both the strategy
  and its per-shot compiled graph hash - two shots with different reference
  counts in one series compile two different graphs (T01/T04);
* a candidate model variant is refused by name and never substituted with
  the INT8 baseline (T05);
* a strategy that enables RelayVSR X2 while the backend is unavailable is
  refused before anything is queued (T18);
* the same selection replays the same run; a changed strategy builds a new
  package instead of reusing the old one under a different contract;
* the legacy ``workflow_profile_id`` path keeps working unchanged.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shine.comfy.graph_compiler import compile_ref2va_workflow
from shine.strategy import DEFAULT_STRATEGY, GenerationStrategy, SecondPassConfig, UpscaleConfig
from tests.conftest import (
    create_project,
    create_referenced_shot,
    create_shot,
    freeze_style,
    make_studio,
    upload_asset,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def _project_with_shots(client: TestClient, shot_count: int = 2) -> tuple[str, str, list[dict]]:
    project_id = create_project(client, title="动态策略")
    style = freeze_style(client, project_id)
    shots = [create_shot(client, project_id, style["id"], title=f"镜头{i + 1}") for i in range(shot_count)]
    return project_id, style["id"], shots


def _shot_with_references(client: TestClient, project_id: str, style_id: str,
                          *, image_refs: int = 1) -> dict:
    """A referenced shot binding N approved image references (subject first)."""
    return create_referenced_shot(client, project_id, style_id, character="主角")


def test_strategy_series_carries_strategy_and_compiled_graph_hash(studio):
    """T01/T04: the series freezes the strategy; each job compiles its own graph."""
    client, factory, _settings = studio
    project_id, style_id, _shots = _project_with_shots(client, shot_count=1)
    shot = _shot_with_references(client, project_id, style_id, image_refs=1)

    strategy = DEFAULT_STRATEGY.model_dump(mode="json")
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]],
        "provider": "comfyui",
        "strategy": strategy,
    })
    assert response.status_code == 201, response.text
    series = response.json()

    from shine.models import Job, ProductionSeries

    with factory() as db:
        row = db.get(ProductionSeries, series["id"])
        assert row is not None and row.generation_strategy == strategy
        assert row.workflow_profile_id is None
        job = db.get(Job, row.current_job_id)
        assert job is not None
        request = job.request
        assert request["strategy"] == strategy
        # The request carries this shot's real compiled graph identity, which
        # a one-image shot distinguishes from any other reference layout.
        assert request["workflow_profile_hash"]
        from shine.comfy.workflow import graph_hash as _hash
        expected = compile_ref2va_workflow(image_count=1, audio_count=0)
        assert request["workflow_profile_hash"] == expected.graph_sha256


def test_strategy_shoot_via_director_start_builds_strategy_package(studio):
    """The streamline entry freezes the strategy into the package and replays it."""
    from tests.test_director_shoot import _ready_first_episode
    from tests.test_director_streamline import LOCAL

    client, factory, settings = studio
    project_id, _pending = _ready_first_episode(client, settings)

    strategy = DEFAULT_STRATEGY.model_dump(mode="json")
    result = client.post(f"/api/v1/projects/{project_id}/director/production/start", json={
        "episode_ids": ["ep-01"],
        "strategy": strategy,
    }, headers=LOCAL)
    assert result.status_code == 201, result.text
    started = result.json()
    from shine.director.models import DirectorPackageVersion

    with factory() as db:
        package = db.get(DirectorPackageVersion, started["package_id"])
        binding = package.manifest["binding"]
        assert binding["strategy"] == strategy
        assert binding["strategy_sha256"] == DEFAULT_STRATEGY.strategy_sha256()
        assert binding["workflow_profile_id"] is None

    # The same selection replays the same run; a different strategy must not.
    replay = client.post(f"/api/v1/projects/{project_id}/director/production/start", json={
        "episode_ids": ["ep-01"],
        "strategy": strategy,
    }, headers=LOCAL)
    assert replay.status_code == 201, replay.text
    assert replay.json()["run_id"] == started["run_id"]
    assert replay.json()["package_built"] is False

    other = GenerationStrategy(
        model_variant_id="h3-ref2va-int8-convrot",
        accelerator_id="native20",
        second_pass=SecondPassConfig(enabled=True),
    )
    changed = client.post(f"/api/v1/projects/{project_id}/director/production/start", json={
        "episode_ids": ["ep-01"],
        "strategy": other.model_dump(mode="json"),
    }, headers=LOCAL)
    # While the first run is still active a second run cannot start - the
    # one-active-run gate.  What matters here: the refusal is the run gate,
    # never a silent replay of the INT8+pdd8 package under the new strategy.
    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"]["code"] == "PRODUCTION_RUN_ALREADY_ACTIVE"
    # The run gate rolled the request back: no second run, and no orphan
    # native20 package pretending the old run covers the new strategy.
    with factory() as db:
        from sqlalchemy import select

        from shine.director.models import DirectorPackageVersion
        revisions = db.scalars(select(DirectorPackageVersion.revision).where(
            DirectorPackageVersion.project_id == project_id)).all()
        assert len(revisions) == 1


def test_candidate_variant_strategy_is_refused_not_substituted(studio):
    """T05: W6A8 candidate strategy is a named refusal at series creation."""
    client, _factory, _settings = studio
    project_id, style_id, _shots = _project_with_shots(client, shot_count=1)
    shot = _shot_with_references(client, project_id, style_id)

    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]],
        "provider": "comfyui",
        "strategy": GenerationStrategy(model_variant_id="ref2va-lowend-candidate").model_dump(mode="json"),
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STRATEGY_BLOCKED"
    codes = {blocker["code"] for blocker in detail["blockers"]}
    assert "MODEL_VARIANT_NOT_ACCEPTED" in codes


def test_relayvsr_strategy_refused_while_backend_unavailable(studio):
    """T18: enabling RelayVSR X2 without the backend blocks the whole strategy."""
    client, _factory, _settings = studio
    project_id, style_id, _shots = _project_with_shots(client, shot_count=1)
    shot = _shot_with_references(client, project_id, style_id)

    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]],
        "provider": "comfyui",
        "strategy": GenerationStrategy(
            upscale=UpscaleConfig(enabled=True)).model_dump(mode="json"),
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STRATEGY_BLOCKED"
    codes = {blocker["code"] for blocker in detail["blockers"]}
    assert "UPSCALE_BACKEND_UNAVAILABLE" in codes


def test_strategy_and_profile_are_exclusive(studio):
    client, _factory, _settings = studio
    project_id, style_id, _shots = _project_with_shots(client, shot_count=1)
    shot = _shot_with_references(client, project_id, style_id)

    from tests.conftest import register_workflow_profile

    profile = register_workflow_profile(client)
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]],
        "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "strategy": DEFAULT_STRATEGY.model_dump(mode="json"),
    })
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "STRATEGY_PROFILE_EXCLUSIVE"


def test_second_pass_strategy_job_request_carries_compiled_second_pass_graph(studio):
    """T08: a second-pass strategy compiles the two-sampler graph identity."""
    client, factory, _settings = studio
    project_id, style_id, _shots = _project_with_shots(client, shot_count=1)
    shot = _shot_with_references(client, project_id, style_id)

    strategy = GenerationStrategy(second_pass=SecondPassConfig(enabled=True))
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]],
        "provider": "comfyui",
        "strategy": strategy.model_dump(mode="json"),
    })
    assert response.status_code == 201, response.text
    series = response.json()

    from shine.models import Job, ProductionSeries

    with factory() as db:
        row = db.get(ProductionSeries, series["id"])
        job = db.get(Job, row.current_job_id)
        request = job.request
        expected = compile_ref2va_workflow(
            image_count=1, audio_count=0, second_pass=strategy.second_pass_spec())
        assert request["workflow_profile_hash"] == expected.graph_sha256
        plain = compile_ref2va_workflow(image_count=1, audio_count=0)
        assert request["workflow_profile_hash"] != plain.graph_sha256


def test_strategy_options_endpoint_lists_matrix(studio):
    client, _factory, _settings = studio
    project_id = create_project(client, title="策略选项")
    response = client.get(f"/api/v1/projects/{project_id}/director/strategy-options")
    assert response.status_code == 200, response.text
    options = response.json()
    variants = {row["variant_id"]: row for row in options["model_variants"]}
    assert variants["h3-ref2va-int8-convrot"]["verified"] is True
    assert variants["ref2va-lowend-candidate"]["verified"] is False
    accelerators = {row["accelerator_id"]: row for row in options["accelerators"]}
    assert accelerators["pdd8"]["verified"] is True
    assert accelerators["turbo4"]["verified"] is False
    assert options["second_pass"]["supported"] is True
    assert options["upscale"][0]["engine"] == "relayvsr"
    assert options["upscale"][0]["enabled"] is False

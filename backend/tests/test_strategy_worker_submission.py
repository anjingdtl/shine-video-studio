"""The Worker's dynamic per-shot compilation against a fake renderer.

The strategy job reaches the submission path with a graph the Worker
compiled itself from the frozen strategy plus the shot's reference manifest:

* the submitted prompt really is the compiled graph (its nodes and the
  reference loaders appear in what the renderer received);
* the persisted submit-intent graph hash equals the deterministic compiled
  graph hash recorded at request time;
* a live object_info that misses the compiled nodes refuses the job by name
  instead of submitting a graph the renderer cannot run;
* a second-pass strategy submits a graph that carries the two-sampler stage.
"""
from __future__ import annotations

import pytest

from shine.comfy.client import ComfyUIClient
from shine.comfy.graph_compiler import compile_ref2va_workflow
from shine.comfy.workflow import graph_hash
from shine.config import Settings
from shine.models import Job
from shine.strategy import DEFAULT_STRATEGY, GenerationStrategy, SecondPassConfig
from shine.worker import run_one
from tests.conftest import (
    create_project,
    create_referenced_shot,
    create_shot,
    freeze_style,
    make_test_video_bytes,
)
from tests.test_comfy_recovery import fake_comfy, worker_settings  # noqa: F401

pytestmark = pytest.mark.full


def _compiled_object_info(graph) -> dict:
    info: dict = {}
    for node in graph.values():
        cls = node["class_type"]
        required: dict = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                required.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                required.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": required, "optional": {}}})
    return info


def _seed_strategy_job(client, project_id: str, style_id: str, strategy: GenerationStrategy) -> str:
    shot = create_referenced_shot(client, project_id, style_id, character="主角")
    body = {
        "shot_id": shot["id"],
        "provider": "comfyui",
        "strategy": strategy.model_dump(mode="json"),
    }
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_worker_submits_the_compiled_strategy_graph(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    job_id = _seed_strategy_job(client, project_id, style["id"], DEFAULT_STRATEGY)

    comfy_client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    expected = compile_ref2va_workflow(image_count=1, audio_count=0)
    monkeypatch.setattr(
        ComfyUIClient, "object_info", lambda self: _compiled_object_info(expected.graph))

    offline = worker_settings(settings)
    processed = run_one(factory, worker_id="strategy-worker", settings=offline, comfy_client=comfy_client)
    assert processed == job_id

    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["artifact_id"], result
    assert fake_comfy.prompt_count == 1
    submitted = fake_comfy.prompts[list(fake_comfy.prompts)[0]]["prompt"]
    classes = {node["class_type"] for node in submitted.values()}
    assert "MiniMaxH3ReferenceToVideo" in classes
    assert "SamplerCustomAdvanced" in classes
    loaders = [node for node in submitted.values() if node["class_type"] == "LoadImage"]
    assert len(loaders) == 1
    # The persisted intent hash is the deterministic compiled hash the
    # request builder recorded - same recipe in, same graph out.
    with factory() as db:
        row = db.get(Job, job_id)
        request_hash = row.request["workflow_profile_hash"]
    assert request_hash == expected.graph_sha256
    # The submitted prompt is the compiled graph with values filled in: the
    # reference loader carries the uploaded file name and the prompt node
    # carries the compiled text, everything else matches the recipe.
    assert submitted["31"]["inputs"]["prompt"]
    assert submitted["40"]["inputs"]["image"].startswith("shine")


def test_worker_refuses_strategy_graph_missing_live_nodes(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    job_id = _seed_strategy_job(client, project_id, style["id"], DEFAULT_STRATEGY)

    comfy_client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    # A live inventory without any H3 node: the compile succeeds but the
    # validation must refuse before submission.
    monkeypatch.setattr(ComfyUIClient, "object_info", lambda self: {"Note": {}})

    offline = worker_settings(settings)
    processed = run_one(factory, worker_id="strategy-worker", settings=offline, comfy_client=comfy_client)
    assert processed == job_id
    assert fake_comfy.prompt_count == 0
    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["failure_code"] == "STRATEGY_GRAPH_NODE_MISSING"


def test_worker_submits_second_pass_graph(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    strategy = GenerationStrategy(second_pass=SecondPassConfig(enabled=True))
    job_id = _seed_strategy_job(client, project_id, style["id"], strategy)

    comfy_client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    expected = compile_ref2va_workflow(
        image_count=1, audio_count=0, second_pass=strategy.second_pass_spec())
    monkeypatch.setattr(
        ComfyUIClient, "object_info", lambda self: _compiled_object_info(expected.graph))

    offline = worker_settings(settings)
    processed = run_one(factory, worker_id="strategy-worker", settings=offline, comfy_client=comfy_client)
    assert processed == job_id
    submitted = fake_comfy.prompts[list(fake_comfy.prompts)[0]]["prompt"]
    classes = {node["class_type"] for node in submitted.values()}
    assert "MinimaxH3LatentUpscaler3DRefineHandoff" in classes
    assert "MiniMaxH3PDDAccScheduler" in classes
    # Two real sampler stages: the first SamplerCustomAdvanced and the
    # refine handoff carrying its own scheduler + model patch.
    assert sum(1 for node in submitted.values()
               if node["class_type"] == "SamplerCustomAdvanced") == 1
    handoff = next(node for node in submitted.values()
                   if node["class_type"] == "MinimaxH3LatentUpscaler3DRefineHandoff")
    assert handoff["inputs"]["sigmas"] == ["61", 0]
    assert handoff["inputs"]["model"] == ["62", 0]

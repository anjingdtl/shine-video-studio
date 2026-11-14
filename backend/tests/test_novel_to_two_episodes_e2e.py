"""Engineering acceptance round 1: a novel becomes two delivered episodes.

This is the full autopilot closure driven end to end with a mock text model and
a fake render boundary: novel document → confirmed outline → confirmed episode
book + frozen style → five-role storyboard (auto-landing, auto-confirming each
episode) → shot-driven material list reused from an approved master → one shoot
action → the resident Worker's Run/Series/Job/QC/assembly → two episode reels.

Evidence level: OFFLINE_VERIFIED for the whole journey; REPLACED only at the
physical ComfyUI process and H3 inference (a synthetic test pattern).  No real
MiniMax-H3 output exists here.  The Worker's tick is the only production driver;
no shot is hand-marked PASSED and no delivery is filled in by hand.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.media.delivery_models import DeliveryOrder
from shine.models import Job, MediaArtifact
from shine.production.models import ProductionRun, ProductionRunEpisode
from tests.conftest import make_test_video_bytes, register_workflow_profile
from tests.test_director_streamline import (
    LOCAL,
    _chain_responder,
    _confirm_book,
    _prepare_project,
    _storyboard_book,
)
from tests.test_director_worker_integration import (
    REF2VA_GRAPH,
    REF2VA_PARAM_MAP,
    _has_ffmpeg,
    _install_render_boundary,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_comfy():
    from tests.fake_http import FakeComfyServer

    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def _six_second_responder(master: str):
    base = _chain_responder({"asset": master})

    def respond(messages, schema):
        answer = base(messages, schema)
        title = schema.get("title")
        if title == "DirectorialPlan":
            for shot in answer["shots"]:
                shot["duration_s"] = 6.0
        elif title == "EditIntentPlan":
            for clip in answer["clips"]:
                clip["planned_duration_s"] = 6.0
        return answer

    return respond


def test_a_novel_becomes_two_delivered_episodes_without_hand_holding(api, monkeypatch, fake_comfy):
    from shine import worker
    from shine.director import streamline

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    real = replace(settings, render_provider="comfyui")
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6.0)

    # ①② A novel project: two sections, one cross-episode character (老周),
    # an approved+locked master, a confirmed outline.
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    profile = register_workflow_profile(
        client, mode="Ref2VA", graph=REF2VA_GRAPH, parameter_map=REF2VA_PARAM_MAP)

    # ③ The five-role chain storyboards both episodes; each episode self-confirms.
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                         mock_responder=_six_second_responder(master))
    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                         json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    for _ in range(40):
        if not streamline.run_pending_director_storyboard(factory, settings, "w", gateway=gateway):
            break
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    with factory() as db:
        plan = streamline._latest_plan(db, project)
    by_id = {e["episode_id"]: e for e in plan.payload["episodes"]}
    assert len(by_id["ep-01"]["shot_ids"]) >= 2, "episode 1 has at least two shots"
    assert len(by_id["ep-02"]["shot_ids"]) >= 2, "episode 2 has at least two shots"

    # ④ The material list is shot-driven; the cross-episode character reuses the
    # one approved master (no second purchase, no auto-adopted candidate).
    derived = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                          json={"episode_ids": ["ep-01", "ep-02"]}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    body = derived.json()
    decisions = []
    for row in body["requirements"]:
        if row.get("matched_master"):
            decisions.append({"requirement_id": row["requirement_id"],
                              "strategy": "reuse_master", "entity_id": row["entity_id"],
                              "asset_id": row["matched_master"]["asset_id"]})
        elif row.get("optional"):
            decisions.append({"requirement_id": row["requirement_id"], "strategy": "skip",
                              "entity_id": row["entity_id"]})
        else:
            pytest.fail(f"required material has no master: {row}")
    assert any(d["strategy"] == "reuse_master" for d in decisions)
    committed = client.post(f"/api/v1/projects/{project}/director/materials/commit",
                            json={"plan_revision_id": body["plan_revision_id"],
                                  "expected_snapshot": body["expected_snapshot"],
                                  "decisions": decisions, "queue_generation": False},
                            headers=LOCAL)
    assert committed.status_code == 201, committed.text

    # ⑤⑥ One shoot action freezes the package and starts the real run.
    started = client.post(f"/api/v1/projects/{project}/director/production/start",
                          json={"episode_ids": ["ep-01", "ep-02"],
                                "workflow_profile_id": profile["id"]},
                          headers=LOCAL)
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    monkeypatch.setattr("shine.worker._managed_recipe", lambda _s: {"renderer": {"instance_id": "i"}})
    render_calls: list[str] = []
    _install_render_boundary(monkeypatch, fake_comfy, render_calls)

    # ⑥⑦ The Worker's tick is the only driver: Run → Series → Job → QC → assembly.
    for _ in range(300):
        worker.run_configured_once(factory, None, "worker-novel", settings=real)
        with factory() as db:
            run = db.get(ProductionRun, run_id)
            if run.status == "COMPLETED":
                break
    with factory() as db:
        run = db.get(ProductionRun, run_id)
        episodes = list(db.scalars(select(ProductionRunEpisode).where(
            ProductionRunEpisode.run_id == run_id)).all())
        orders = list(db.scalars(select(DeliveryOrder).where(
            DeliveryOrder.project_id == project)).all())
        jobs = list(db.scalars(select(Job).where(Job.project_id == project)).all())
        artifacts = list(db.scalars(select(MediaArtifact).where(
            MediaArtifact.project_id == project)).all())

    assert run.status == "COMPLETED", run.status
    assert len(episodes) == 2
    assert all(e.status == "DELIVERED" for e in episodes)
    assert all(e.delivered_artifact_id for e in episodes)
    assert len(orders) == 2 and all(o.status == "DELIVERED" for o in orders)
    planned = [sid for e in episodes for sid in e.shot_ids]
    rendered = [j.shot_id for j in jobs if j.shot_id]
    assert sorted(rendered) == sorted(planned), "every planned shot rendered exactly once"
    assert len(render_calls) == len(planned)
    delivered = [a for a in artifacts if a.artifact_kind == "delivery"]
    assert delivered and all(a.is_mock is False and a.byte_size > 0 for a in delivered)
    for e in episodes:
        assert e.actual_seconds and e.actual_seconds > 10, "each episode is a real assembled reel"
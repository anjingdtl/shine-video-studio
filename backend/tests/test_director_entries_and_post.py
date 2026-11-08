"""Dual entry, script-first episodes and the episode post-production contract.

* G01 — an idea becomes a versioned, confirmable ``CreativeBrief``; a confirmed
  brief becomes a real project document through the resident Worker, so both
  entries continue in one script pipeline.  Remote text needs consent; an
  uncertain call goes to review and is never replayed.
* G02 — episodes are planned from the confirmed script's sections *before* any
  shot exists, then materialised into exactly the shots each section's applied
  storyboard produced.
* G04 — a cut/subtitle contract reaches the episode EDL and a revision is a new
  delivery version from the same takes, never a re-render.

No real model or renderer is called.  LLM responses are transport doubles and
the media are FFmpeg test patterns used as source artifacts only.
"""

from __future__ import annotations

import copy
import shutil

import pytest
from sqlalchemy import func, select

from shine.llm.base import LLMResponse
from shine.llm.gateway import LLMGateway
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, Job, ProjectDocument, utcnow
from tests.conftest import create_project, create_shot, freeze_style
from tests.test_director_long_form import (
    LOCAL,
    _advance,
    _complete_dispatched_series,
    _confirm_package,
    _plan_and_package,
    _studio,
)

SCRIPT_ANSWER = {
    "title": "雨夜钟表铺",
    "script_text": "第1场：钟表铺 夜\n老周擦拭一只停摆的怀表。\n老周：它停在了十二点。\n"
                   "第2场：街口 夜\n雨中，少女抬头看向钟表铺的灯。",
}


def _scriptwriter(client, *, kind: str = "mock", base_url: str = "") -> str:
    body = {"name": f"writer-{kind}", "kind": kind, "model": "writer"}
    if base_url:
        body["base_url"] = base_url
    profile = client.post("/api/v1/provider-profiles", json=body)
    assert profile.status_code == 201, profile.text
    route = client.put("/api/v1/agent-routes",
                       json={"role": "scriptwriter", "provider_profile_id": profile.json()["id"]})
    assert route.status_code == 200, route.text
    return profile.json()["id"]


def _brief(client, project: str, **extra) -> dict:
    response = client.post(f"/api/v1/projects/{project}/creative-briefs",
                           json={"idea": "雨夜里，一个钟表匠修好了一只停在十二点的怀表。", **extra},
                           headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _confirm_brief(client, project: str, brief: dict) -> dict:
    response = client.post(f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/confirm",
                           json={"expected_sha256": brief["sha256"]}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# G01 — idea entry
# ---------------------------------------------------------------------------


def test_a_brief_is_versioned_validated_and_needs_confirmation(api):
    client, factory, settings = api
    project = create_project(client, "想法入口")
    invalid = client.post(f"/api/v1/projects/{project}/creative-briefs", json={}, headers=LOCAL)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "CREATIVE_BRIEF_INVALID"
    unknown = client.post(f"/api/v1/projects/{project}/creative-briefs",
                          json={"idea": "x", "made_up_field": 1}, headers=LOCAL)
    assert unknown.status_code == 422, "an unknown brief field must be refused, not silently dropped"

    first = _brief(client, project)
    second = _brief(client, project, locked_facts=["钟表匠叫老周"])
    assert (first["version"], second["version"]) == (1, 2)
    assert second["confirmed"] is False

    _scriptwriter(client)
    early = client.post(f"/api/v1/projects/{project}/creative-briefs/{second['id']}/generate-script",
                        json={}, headers=LOCAL)
    assert early.status_code == 409
    assert early.json()["detail"] == "CREATIVE_BRIEF_NOT_CONFIRMED"
    changed = client.post(f"/api/v1/projects/{project}/creative-briefs/{second['id']}/confirm",
                          json={"expected_sha256": first["sha256"]}, headers=LOCAL)
    assert changed.status_code == 409
    assert _confirm_brief(client, project, second)["confirmed"] is True
    listed = client.get(f"/api/v1/projects/{project}/creative-briefs").json()
    assert [row["version"] for row in listed] == [2, 1]


def test_a_confirmed_brief_becomes_a_real_document_through_the_worker(api, monkeypatch):
    from shine.director.creative import run_pending_creative_task

    client, factory, settings = api
    project = create_project(client, "想法成稿")
    _scriptwriter(client)
    brief = _confirm_brief(client, project, _brief(client, project))
    queued = client.post(f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script",
                         json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    again = client.post(f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script",
                        json={}, headers=LOCAL)
    assert again.json()["task_id"] == queued.json()["task_id"], "a repeated request reuses the task"

    calls = []

    def complete(self, db, role, **kwargs):
        calls.append(role)
        return LLMResponse("", copy.deepcopy(SCRIPT_ANSWER), "test-double", 0)

    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)
    assert run_pending_creative_task(factory, settings, "worker-test") is True
    assert calls == ["scriptwriter"]
    assert run_pending_creative_task(factory, settings, "worker-test") is False

    with factory() as db:
        document = db.scalars(select(ProjectDocument).where(ProjectDocument.project_id == project)).one()
        assert document.source_format == "idea"
        assert document.text.startswith("第1场")
        assert document.parsed["brief_artifact_id"] == brief["id"]
        assert document.parsed["brief_sha256"] == brief["sha256"]
        task = db.get(AgentTaskRecord, queued.json()["task_id"])
        assert task.status == "SUCCEEDED"
        assert task.trace["document_id"] == document.id
    # The written script is an ordinary document: the import pipeline sees it.
    documents = client.get(f"/api/v1/projects/{project}/documents").json()
    assert [row["id"] for row in documents] == [document.id]
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    script = journey["stages"][0]
    assert script["done"] is False, "a written script is not a confirmed script"
    assert script["facts"]["document_source"] == "idea"
    assert script["facts"]["brief_confirmed"] is True


def test_a_remote_text_model_needs_consent_for_the_brief(api):
    client, factory, settings = api
    project = create_project(client, "远程文字")
    _scriptwriter(client, kind="openai_compatible", base_url="https://text.example.invalid/v1")
    brief = _confirm_brief(client, project, _brief(client, project))
    path = f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script"
    refused = client.post(path, json={}, headers=LOCAL)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "REMOTE_AGENT_CONSENT_REQUIRED"
    accepted = client.post(path, json={"remote_text_consent": True}, headers=LOCAL)
    assert accepted.status_code == 202, accepted.text
    with factory() as db:
        task = db.get(AgentTaskRecord, accepted.json()["task_id"])
        assert task.trace["consent"] == {"required": True, "granted": True,
                                         "scope": "brief_text_only_no_asset_bytes"}


def test_an_uncertain_script_call_is_reviewed_and_never_replayed(api, monkeypatch):
    from shine.director.creative import run_pending_creative_task

    client, factory, settings = api
    project = create_project(client, "不确定")
    _scriptwriter(client)
    brief = _confirm_brief(client, project, _brief(client, project))
    queued = client.post(f"/api/v1/projects/{project}/creative-briefs/{brief['id']}/generate-script",
                         json={}, headers=LOCAL).json()

    def explode(self, db, role, **kwargs):
        raise RuntimeError("connection reset after the request was written")

    monkeypatch.setattr(LLMGateway, "complete_for_role", explode)
    assert run_pending_creative_task(factory, settings, "worker-test") is True
    with factory() as db:
        task = db.get(AgentTaskRecord, queued["task_id"])
        assert task.status == "NEEDS_REVIEW"
        assert task.trace["external_outcome"] == "UNKNOWN"
        assert task.trace["automatic_replay"] is False
        assert db.scalars(select(ProjectDocument).where(ProjectDocument.project_id == project)).all() == []
    assert run_pending_creative_task(factory, settings, "worker-test") is False, "never replayed"


# ---------------------------------------------------------------------------
# G02 — script first, then shots
# ---------------------------------------------------------------------------


def _outline(factory, project: str, seconds: list[float]) -> tuple[str, list[str]]:
    sections = [
        {"production_section_id": f"sec-{index + 1}", "order": index + 1, "title": f"第{index + 1}场",
         "estimated_duration_s": value, "start_offset": index * 100, "end_offset": index * 100 + 99}
        for index, value in enumerate(seconds)
    ]
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project, role_id="story_outline_run", request_identity=f"outline-{project}-{len(seconds)}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={},
        )
        db.add(source)
        db.flush()
        artifact = AgentArtifact(project_id=project, kind="StoryOutline", schema_version="1.0",
                                 payload_json={"production_sections": sections},
                                 sha256=f"{len(seconds):064d}", producer_role="scriptwriter",
                                 source_task_id=source.id,
                                 approved_by="local-user", approved_at=utcnow())
        db.add(artifact)
        db.flush()
        return artifact.id, [row["production_section_id"] for row in sections]


def _apply_storyboard(factory, project: str, section_id: str, shot_ids: list[str]) -> None:
    """Record the section run and the apply receipt the storyboard apply writes."""
    artifact_id = f"camera-{section_id}"
    with factory.begin() as db:
        db.add(AgentTaskRecord(
            project_id=project, role_id="story_section_run", request_identity=f"section-{section_id}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED",
            trace={"units": [{"production_section_id": section_id, "start_offset": 0}],
                   "results": [{"unit": {"start_offset": 0},
                                "storyboard": {"steps": {"cinematographer": {"artifact_id": artifact_id}}}}]},
        ))
        db.add(AppSetting(key=f"film-apply:{artifact_id}", value={"shot_ids": shot_ids}))


def test_episodes_are_planned_from_the_script_before_any_shot_exists(api):
    client, factory, settings = api
    project = create_project(client, "先分集")
    freeze_style(client, project)
    refused = client.post(f"/api/v1/projects/{project}/episode-plans",
                          json={"target_seconds": 60, "tolerance_seconds": 10}, headers=LOCAL)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "SCRIPT_OUTLINE_NOT_CONFIRMED"

    _outline(factory, project, [25, 25, 25, 25])
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 10}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    payload = plan.json()["payload"]
    assert payload["mode"] == "sections"
    order = [sid for episode in payload["episodes"] for sid in episode["section_ids"]]
    assert order == ["sec-1", "sec-2", "sec-3", "sec-4"], "every scene exactly once, in script order"
    assert all(episode["shot_ids"] == [] for episode in payload["episodes"])
    assert len(payload["episodes"]) == 2
    confirmed = client.post(f"/api/v1/projects/{project}/episode-plans/{plan.json()['version']}/confirm",
                            json={"expected_sha256": plan.json()["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stages = {stage["id"]: stage for stage in journey["stages"]}
    assert not any(b["code"] == "EPISODE_PLAN_NOT_CONFIRMED" for b in stages["plan"]["blockers"])
    assert any(b["code"] == "EPISODE_PLAN_NOT_MATERIALIZED" for b in stages["storyboard"]["blockers"])
    assert stages["storyboard"]["done"] is False


def test_materialising_uses_only_the_shots_each_section_really_applied(api):
    client, factory, settings = api
    project = create_project(client, "物化")
    style = freeze_style(client, project)
    outline_id, sections = _outline(factory, project, [30, 30])
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 30, "tolerance_seconds": 5}, headers=LOCAL).json()
    client.post(f"/api/v1/projects/{project}/episode-plans/{plan['version']}/confirm", json={}, headers=LOCAL)

    pending = client.post(f"/api/v1/projects/{project}/episode-plans/{plan['version']}/materialize",
                          headers=LOCAL)
    assert pending.status_code == 409
    assert pending.json()["detail"]["code"] == "EPISODE_SECTION_NOT_STORYBOARDED"

    shots = [create_shot(client, project, style["id"], title=f"镜{i}", beat=f"动作{i}")["id"] for i in range(4)]
    _apply_storyboard(factory, project, sections[0], shots[:2])
    _apply_storyboard(factory, project, sections[1], shots[2:3])
    materialized = client.post(f"/api/v1/projects/{project}/episode-plans/{plan['version']}/materialize",
                               headers=LOCAL)
    assert materialized.status_code == 201, materialized.text
    episodes = materialized.json()["payload"]["episodes"]
    assert [episode["shot_ids"] for episode in episodes] == [shots[:2], shots[2:3]]
    assert shots[3] not in [s for e in episodes for s in e["shot_ids"]], "an unrelated shot is not in scope"
    assert materialized.json()["version"] == plan["version"] + 1, "materialising is a new plan version"

    client.post(f"/api/v1/projects/{project}/episode-plans/{materialized.json()['version']}/confirm",
                json={}, headers=LOCAL)
    from tests.conftest import register_workflow_profile

    profile = register_workflow_profile(client, mode="Ref2VA")
    package = client.post(f"/api/v1/projects/{project}/director-packages",
                          json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA"},
                          headers=LOCAL)
    assert package.status_code == 201, package.text
    assert package.json()["shot_count"] == 3
    from shine.director.models import DirectorPackageEpisode

    with factory() as db:
        rows = db.scalars(select(DirectorPackageEpisode)
                          .where(DirectorPackageEpisode.package_id == package.json()["id"])
                          .order_by(DirectorPackageEpisode.position)).all()
        assert [row.manifest["section_ids"] for row in rows] == [[sections[0]], [sections[1]]]

    # A newly confirmed script makes the old plan's source stale.
    _outline(factory, project, [10, 10, 10])
    stale = client.post(f"/api/v1/projects/{project}/episode-plans/{materialized.json()['version']}/confirm",
                        json={}, headers=LOCAL)
    assert stale.status_code == 409
    assert "SCRIPT_OUTLINE_CHANGED" in stale.json()["detail"]["problems"]


def test_the_shot_entry_still_groups_an_existing_shot_list(api):
    """The compatible entry: hand-built shots keep the shot-grouping plan."""
    client, factory, settings = api
    studio = _studio(client, shots=2)
    plan = client.post(f"/api/v1/projects/{studio['project']}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    assert plan.json()["payload"]["mode"] == "shots"


# ---------------------------------------------------------------------------
# G04 — episode post-production contract
# ---------------------------------------------------------------------------


def _delivered_run(api) -> tuple[dict, dict]:
    from shine.media.delivery_engine import run_pending_deliveries

    client, factory, settings = api
    studio = _studio(client, shots=2)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    for _ in range(2):
        _advance(factory, settings, run["id"])
        _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])
    run_pending_deliveries(factory, settings=settings, asset_root=settings.data_dir, worker_id="test")
    _advance(factory, settings, run["id"])
    return studio, client.get(f"/api/v1/production-runs/{run['id']}").json()


def _has_ffmpeg(settings) -> bool:
    return bool(shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path))


def test_a_post_contract_is_validated_before_it_is_stored(api):
    client, factory, settings = api
    studio = _studio(client, shots=1)
    _plan, package = _plan_and_package(client, studio, target_seconds=60, batch_limit=1)
    _confirm_package(client, package["id"])
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"]}, headers=LOCAL).json()
    path = f"/api/v1/production-runs/{run['id']}/episodes/ep-01/post-contract"
    shot = studio["shots"][0]["id"]
    audio = client.put(path, json={"audio_gain_db": -3}, headers=LOCAL)
    assert audio.status_code == 422, "an unsupported audio mix must be refused by name"
    assert "EDIT_AUDIO_NOT_SUPPORTED" in " ".join(audio.json()["detail"]["problems"])
    foreign = client.put(path, json={"cuts": [{"shot_id": "not-in-episode", "in_s": 0, "out_s": 1}]},
                         headers=LOCAL)
    assert foreign.status_code == 422
    assert foreign.json()["detail"]["code"] == "EDIT_CUT_SHOT_NOT_IN_EPISODE"
    short = client.put(path, json={"cuts": [{"shot_id": shot, "in_s": 1.0, "out_s": 1.2}]}, headers=LOCAL)
    assert short.status_code == 422
    subtitle = client.put(path, json={"subtitle_revision": 7}, headers=LOCAL)
    assert subtitle.status_code == 409
    assert subtitle.json()["detail"] == "EPISODE_SUBTITLE_NOT_APPROVED"
    ok = client.put(path, json={"cuts": [{"shot_id": shot, "in_s": 0.2, "out_s": 1.5}]}, headers=LOCAL)
    assert ok.status_code == 202, ok.text
    assert ok.json()["post_contract"]["cuts"][0]["in_s"] == 0.2


def test_a_cut_revision_is_a_new_delivery_version_and_never_a_new_render(api):
    from shine.media.delivery_engine import run_pending_deliveries
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.models import ProductionRunEpisode

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    studio, run = _delivered_run(api)
    assert run["status"] == "COMPLETED", run
    with factory() as db:
        jobs_before = db.scalar(select(func.count()).select_from(Job))
    shot = studio["shots"][0]["id"]
    revised = client.put(f"/api/v1/production-runs/{run['id']}/episodes/ep-01/post-contract",
                         json={"cuts": [{"shot_id": shot, "in_s": 0.2, "out_s": 1.5}]}, headers=LOCAL)
    assert revised.status_code == 202, revised.text
    run_pending_deliveries(factory, settings=settings, asset_root=settings.data_dir, worker_id="test")
    with factory() as db:
        orders = db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id == studio["project"])
                            .order_by(DeliveryOrder.version)).all()
        assert [(order.version, order.status) for order in orders] == [(1, "DELIVERED"), (2, "DELIVERED")]
        first_clip = orders[1].edl_payload["clips"][0]
        assert (first_clip["in_s"], first_clip["out_s"]) == (0.2, 1.5)
        assert orders[1].edit_rules["cut_source"] == "post_contract"
        assert orders[0].artifact_id != orders[1].artifact_id, "the published history is kept"
        assert db.scalar(select(func.count()).select_from(Job)) == jobs_before, "no re-render"
        episode = db.scalars(select(ProductionRunEpisode)
                             .where(ProductionRunEpisode.run_id == run["id"])).one()
        assert episode.delivered_artifact_id == orders[1].artifact_id
    from shine.production import runner

    assert runner.reconcile_run(factory, run["id"])["status"] == "COMPLETED", \
        "a post revision does not reopen a completed run"


def test_subtitles_timed_beyond_the_cut_are_refused(api):
    from shine.media.delivery_engine import run_pending_deliveries
    from shine.media.delivery_models import DeliveryOrder

    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("FFmpeg/FFprobe is not installed")
    studio, run = _delivered_run(api)
    created = client.post(f"/api/v1/projects/{studio['project']}/subtitles",
                          json={"cues": [{"index": 1, "start_s": 0.5, "end_s": 600.0, "text": "太长的字幕"}]})
    assert created.status_code == 201, created.text
    revision = created.json()["revision"]
    assert client.post(f"/api/v1/projects/{studio['project']}/subtitles/{revision}/approve").status_code == 200
    response = client.put(f"/api/v1/production-runs/{run['id']}/episodes/ep-01/post-contract",
                          json={"subtitle_revision": revision}, headers=LOCAL)
    assert response.status_code == 202, response.text
    assert response.json()["post_contract"]["subtitle_sha256"] == created.json()["sha256"]
    run_pending_deliveries(factory, settings=settings, asset_root=settings.data_dir, worker_id="test")
    with factory() as db:
        latest = db.scalars(select(DeliveryOrder).where(DeliveryOrder.project_id == studio["project"])
                            .order_by(DeliveryOrder.version.desc())).first()
        assert latest.status == "BLOCKED"
        assert latest.failure_code == "EPISODE_SUBTITLE_EXCEEDS_CUT"

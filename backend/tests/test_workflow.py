from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.models import Job, JobAttempt, utcnow
from shine.providers import MockH3Provider
from shine.schemas import ShotSpec
from shine.services import transition
from shine.worker import recover_expired_jobs, run_one

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-fixture"


@pytest.fixture
def studio(tmp_path):
    db_path = tmp_path / "本地项目" / "shine.sqlite3"
    db_path.parent.mkdir(parents=True)
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    app = create_app(database_url=f"sqlite:///{db_path.as_posix()}", asset_root=tmp_path / "素材库")
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    with TestClient(app) as client:
        yield client, factory, tmp_path
    engine.dispose()


def create_project(client: TestClient, title: str = "雾港") -> str:
    response = client.post("/api/v1/projects", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def style_payload() -> dict[str, str]:
    return {
        "photography_language": "平视中近景，稳定缓推",
        "shot_blocking": "对话保持视线轴线",
        "color_palette": "青灰环境与温暖肤色",
        "lighting_tone": "单侧主光，保留阴影层次",
        "lens_texture": "自然浅景深，轻颗粒",
        "editing_rhythm": "对白停顿后切反应镜头",
        "sound_performance": "普通话自然语速，环境声克制",
    }


def freeze_style(client: TestClient, project_id: str) -> dict:
    draft = client.put(f"/api/v1/projects/{project_id}/style/draft", json=style_payload())
    assert draft.status_code == 200, draft.text
    response = client.post(f"/api/v1/projects/{project_id}/style/freeze")
    assert response.status_code == 201, response.text
    return response.json()


def upload_asset(client: TestClient, project_id: str, content: bytes = PNG, filename: str = "角色.png") -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image"},
        files={"file": (filename, content, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve_lock(client: TestClient, asset_id: str) -> None:
    approved = client.post(f"/api/v1/assets/{asset_id}/approve")
    assert approved.status_code == 200, approved.text
    locked = client.post(f"/api/v1/assets/{asset_id}/lock")
    assert locked.status_code == 200, locked.text


def shot_payload(style_id: str, *, title: str = "对话镜头", beat: str = "两人交换线索", asset_ids=None) -> dict:
    return {
        "name": title,
        "spec": {
            "schema_version": "1.0",
            "story_beat": beat,
            "duration_target_s": 6,
            "shot_priority": "B",
            "style_version_id": style_id,
            "camera": {"shot_size": "medium"},
            "characters": [],
            "required_asset_ids": asset_ids or [],
            "dialogue": [],
            "sound": {},
            "continuity": {},
            "locked_story_facts": [],
        },
    }


def create_shot(client: TestClient, project_id: str, style_id: str, **kwargs) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/shots", json=shot_payload(style_id, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def create_job(client: TestClient, project_id: str, shot_id: str, **kwargs) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": shot_id, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def worker_for(studio):
    _client, factory, _root = studio
    return factory, MockH3Provider()


def test_health_and_production_gate_requires_explicit_style_freeze(studio):
    client, _factory, _root = studio
    assert client.get("/health").json() == {
        "status": "ok",
        "service": "shine-api",
        "render_provider": "mock",
        "h3_connected": False,
        "connection_probe": "not_run_use_system_status",
    }
    project_id = create_project(client)
    response = client.post(f"/api/v1/projects/{project_id}/style/freeze")
    assert response.status_code == 409
    incomplete_style = style_payload()
    incomplete_style["lens_texture"] = ""
    assert client.put(f"/api/v1/projects/{project_id}/style/draft", json=incomplete_style).status_code == 422
    assert client.post(f"/api/v1/projects/{project_id}/shots", json=shot_payload("missing")).status_code == 409


def test_assets_require_user_approval_and_lock_before_shot_or_render(studio):
    client, _factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id)
    body = shot_payload(style["id"], asset_ids=[asset["id"]])
    assert client.post(f"/api/v1/projects/{project_id}/shots", json=body).status_code == 409
    assert client.post(f"/api/v1/assets/{asset['id']}/lock").status_code == 409
    assert client.post(f"/api/v1/assets/{asset['id']}/approve").status_code == 200
    assert client.post(f"/api/v1/projects/{project_id}/shots", json=body).status_code == 409
    assert client.post(f"/api/v1/assets/{asset['id']}/lock").status_code == 200
    shot = create_shot(client, project_id, style["id"], asset_ids=[asset["id"]])
    job = create_job(
        client,
        project_id,
        shot["id"],
        references=[{"asset_id": asset["id"], "use_as": "picture"}],
    )
    assert job["request"]["reference_manifest"][0]["label"] == "<Picture 1>"
    assert job["request"]["reference_manifest"][0]["sha256"] == asset["sha256"]


def test_upload_is_content_addressed_and_filename_cannot_escape_storage(studio):
    client, _factory, root = studio
    project_id = create_project(client)
    first = upload_asset(client, project_id, PNG, r"..\outside\hero.png")
    second = upload_asset(client, project_id, PNG + b"different", r"..\outside\hero.png")
    assert first["filename"] == second["filename"] == "hero.png"
    assert first["sha256"] != second["sha256"]
    first_path = root / "素材库" / "assets" / first["sha256"][:2] / first["sha256"]
    second_path = root / "素材库" / "assets" / second["sha256"][:2] / second["sha256"]
    assert first_path.read_bytes() == PNG
    assert second_path.read_bytes() == PNG + b"different"
    assert (
        client.post(
            f"/api/v1/projects/{project_id}/assets",
            data={"asset_kind": "image"},
            files={"file": ("fake.png", b"not a png", "image/png")},
        ).status_code
        == 415
    )


def test_each_render_request_gets_a_local_reference_manifest(studio):
    client, _factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    first = upload_asset(client, project_id, PNG + b"one")
    second = upload_asset(client, project_id, PNG + b"two")
    approve_lock(client, first["id"])
    approve_lock(client, second["id"])
    shot_a = create_shot(client, project_id, style["id"], title="镜头甲", asset_ids=[first["id"]])
    shot_b = create_shot(client, project_id, style["id"], title="镜头乙", asset_ids=[second["id"]])
    job_a = create_job(
        client,
        project_id,
        shot_a["id"],
        references=[{"asset_id": first["id"], "use_as": "picture"}],
    )
    job_b = create_job(
        client,
        project_id,
        shot_b["id"],
        references=[{"asset_id": second["id"], "use_as": "picture"}],
    )
    ref_a = job_a["request"]["reference_manifest"][0]
    ref_b = job_b["request"]["reference_manifest"][0]
    assert ref_a["label"] == ref_b["label"] == "<Picture 1>"
    assert ref_a["asset_id"] == first["id"] and ref_b["asset_id"] == second["id"]
    assert ref_a["sha256"] != ref_b["sha256"]


def test_mock_worker_runs_auditable_workflow_without_creating_video(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    provider = MockH3Provider()
    assert run_one(factory, provider, "worker-test") == job["id"]
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "PASSED"
    assert result["simulated"] is True and result["video_created"] is False
    assert result["attempts"][0]["status"] == "ACKNOWLEDGED"
    report = client.get(f"/api/v1/jobs/{job['id']}/qc").json()[0]
    assert report["is_mock"] is True
    assert report["report"]["artifact_kind"] == "none"
    assert report["report"]["video_created"] is False


def test_uncertain_submission_is_not_retried_blindly(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="uncertain")
    provider = MockH3Provider()
    run_one(factory, provider, "worker-test")
    assert run_one(factory, provider, "worker-test") is None
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "MOCK_ACK_LOST"
    assert len(result["attempts"]) == 1
    assert provider.submission_count == 1


def test_worker_recovery_marks_expired_submit_intent_for_reconciliation(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "SUBMITTING"
        row.lease_owner = "crashed-worker"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="a" * 64,
                status="SUBMIT_INTENT",
                lease_owner="crashed-worker",
                lease_expires_at=row.lease_expires_at,
            )
        )
    assert recover_expired_jobs(factory, "replacement-worker") == 1
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "SUBMISSION_OUTCOME_UNKNOWN"
    assert run_one(factory, MockH3Provider(), "replacement-worker") is None
    assert len(client.get(f"/api/v1/jobs/{job['id']}").json()["attempts"]) == 1


def test_parent_revision_invalidates_only_dependent_jobs(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    parent_shot = create_shot(client, project_id, style["id"], title="前镜")
    child_shot = create_shot(client, project_id, style["id"], title="后镜")
    independent_shot = create_shot(client, project_id, style["id"], title="獨立镜头")
    parent_job = create_job(client, project_id, parent_shot["id"])
    child_job = create_job(client, project_id, child_shot["id"], depends_on=[parent_job["id"]])
    independent_job = create_job(client, project_id, independent_shot["id"])
    assert child_job["status"] == "PLANNED"
    provider = MockH3Provider()
    run_one(factory, provider, "worker-test")
    run_one(factory, provider, "worker-test")
    run_one(factory, provider, "worker-test")
    replacement = shot_payload(style["id"], title="前镜", beat="前镜动作已替换")["spec"]
    response = client.post(f"/api/v1/shots/{parent_shot['id']}/revise", json=replacement)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert client.get(f"/api/v1/jobs/{parent_job['id']}").json()["status"] == "NEEDS_REVIEW"
    child = client.get(f"/api/v1/jobs/{child_job['id']}").json()
    assert child["status"] == "NEEDS_REVIEW" and child["stale"] is True
    independent = client.get(f"/api/v1/jobs/{independent_job['id']}").json()
    assert independent["status"] == "PASSED" and independent["stale"] is False


def test_repair_budget_allows_at_most_two_semantic_retries(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="fail")
    provider = MockH3Provider()
    plan = {
        "action": "revise_shot_intent",
        "root_cause": "模拟测试失败",
        "changes": ["修正镜头意图"],
        "max_additional_attempts": 2,
        "human_review_required": False,
    }
    run_one(factory, provider, "worker-test")
    for expected_retry in (1, 2):
        response = client.post(f"/api/v1/jobs/{job['id']}/repair?mock_scenario=fail", json=plan)
        assert response.status_code == 200, response.text
        assert response.json()["semantic_retries"] == expected_retry
        run_one(factory, provider, "worker-test")
    exhausted = client.post(f"/api/v1/jobs/{job['id']}/repair", json=plan)
    assert exhausted.status_code == 409
    assert len(client.get(f"/api/v1/jobs/{job['id']}").json()["attempts"]) == 3


def test_pause_resume_and_duplicate_acknowledgement_are_explicit(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="duplicate")
    assert client.post(f"/api/v1/jobs/{job['id']}/pause").status_code == 200
    provider = MockH3Provider()
    assert run_one(factory, provider, "worker-test") is None
    assert client.post(f"/api/v1/jobs/{job['id']}/resume").status_code == 200
    run_one(factory, provider, "worker-test")
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "PASSED"
    assert provider.submission_count == 1


def test_state_machine_rejects_illegal_transitions(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory() as db:
        row = db.get(Job, job["id"])
        with pytest.raises(HTTPException, match="Illegal job transition"):
            transition(row, "PASSED")


def test_structured_agent_runtime_is_schema_bounded_and_style_scoped():
    from shine.agents.runtime import ProducerAgentRuntime

    class GoodModel:
        def complete_json(self, *, system, user, schema):
            assert "cannot approve assets" in system
            assert "style-v1" in user
            return shot_payload("style-v1")["spec"]

    proposal = ProducerAgentRuntime(GoodModel()).propose_shot("two people meet", style_version_id="style-v1")
    assert isinstance(proposal, ShotSpec)
    assert proposal.style_version_id == "style-v1"

    class WrongStyleModel:
        def complete_json(self, *, system, user, schema):
            return shot_payload("style-v2")["spec"]

    with pytest.raises(ValueError, match="cannot select a different style"):
        ProducerAgentRuntime(WrongStyleModel()).propose_shot("brief", style_version_id="style-v1")


def test_ten_independent_mock_jobs_complete_and_remain_separate(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    jobs = []
    for index in range(10):
        shot = create_shot(
            client,
            project_id,
            style["id"],
            title=f"镜头-{index + 1}",
            beat=f"独立动作-{index + 1}",
        )
        jobs.append(create_job(client, project_id, shot["id"]))
    provider = MockH3Provider()
    for _ in range(10):
        assert run_one(factory, provider, "worker-test") is not None
    assert run_one(factory, provider, "worker-test") is None
    rows = client.get(f"/api/v1/projects/{project_id}/jobs").json()
    assert len(rows) == 10
    assert {row["status"] for row in rows} == {"PASSED"}
    assert len({row["request"]["shot_id"] for row in rows}) == 10
    assert len({row["request"]["idempotency_key"] for row in rows}) == 10
    assert provider.submission_count == 10


def test_two_workers_cannot_claim_the_same_queued_job(studio):
    client, factory, _root = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    claimed = threading.Event()
    release = threading.Event()

    class SlowProvider(MockH3Provider):
        def submit(self, idempotency_key, scenario):
            claimed.set()
            assert release.wait(timeout=5)
            return super().submit(idempotency_key, scenario)

    provider = SlowProvider()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_one, factory, provider, "worker-one")
        assert claimed.wait(timeout=5)
        second_result = run_one(factory, provider, "worker-two")
        release.set()
        first_result = first.result(timeout=5)
    assert first_result == job["id"]
    assert second_result is None
    assert provider.submission_count == 1
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "PASSED"


def test_request_content_and_sensitive_sentinels_do_not_enter_application_logs(studio, caplog):
    client, factory, _root = studio
    caplog.set_level(logging.INFO, logger="shine")
    project_id = create_project(client, "synthetic-secret-sentinel-do-not-log")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    run_one(factory, MockH3Provider(), "worker-test")
    assert "synthetic-secret-sentinel-do-not-log" not in caplog.text
    assert job["id"] in caplog.text

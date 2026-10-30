"""Shine Offline Production E2E.

This exercises the whole local production loop with a real FastAPI app, a real
SQLite database, the real worker, the real H3 prompt compiler, a real technical
QC and a controllable mock semantic reviewer. The external renderer is a fake
ComfyUI server, so **no real MiniMax-H3 inference happens here** and nothing in
this test may be read as a real video-quality result.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from shine.comfy.client import ComfyUIClient
from shine.config import Settings
from shine.models import Job, MediaArtifact
from shine.quality.semantic import SemanticReviewResult, Transcript
from shine.schemas import SemanticFinding
from shine.worker import recover_expired_jobs, run_one
from tests.conftest import (
    COMFY_GRAPH,
    PNG,
    create_project,
    freeze_style,
    make_test_video_bytes,
)
from tests.fake_http import FakeComfyServer


@pytest.fixture(autouse=True)
def fake_physical_handoff(monkeypatch):
    """This suite exercises mock QC, not a ComfyUI process lifecycle."""
    monkeypatch.setattr("shine.worker._isolated_mock_handoff", lambda _settings, reviewers: all(
        reviewer is None or reviewer.is_mock for reviewer in reviewers
    ))

SCRIPT = """场景一：雾港码头 夜
林岚：你终于来了。
林岚：码头那边灯塔还亮着。
"""

E2E_GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {
            "positive_prompt": "",
            "seed": 0,
            "duration": 6.0,
            "reference_subject_1": "",
            "reference_picture_1": "",
            "reference_audio_1": "",
        },
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}

E2E_PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_subject_1": "1.inputs.reference_subject_1",
    "reference_picture_1": "1.inputs.reference_picture_1",
    "reference_audio_1": "1.inputs.reference_audio_1",
}


def make_wav_bytes(*, seconds: float = 3.0, rate: int = 48000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class ScriptedSemanticReviewer:
    """A controllable mock semantic reviewer. It never inspects pixels."""

    is_mock = True
    name = "mock-vlm"

    def __init__(self, findings_by_call: list[list[SemanticFinding]]):
        self._calls = list(findings_by_call)
        self.calls = 0

    def review(self, context) -> SemanticReviewResult:
        findings = self._calls.pop(0) if self._calls else []
        self.calls += 1
        return SemanticReviewResult(
            reviewer=self.name,
            model="mock",
            is_mock=True,
            input_sha256=context.artifact_sha256,
            findings=findings,
            overall_confidence=0.95,
            raw_summary="scripted mock semantic review; no pixels were analysed",
        )


class ScriptedASR:
    """A controllable mock speech recognition service for the offline loop."""

    is_mock = True
    name = "mock-asr"

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    def transcribe(self, path) -> Transcript:
        self.calls += 1
        return Transcript(text=self._text, segments=[], is_mock=True, model="mock")


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def worker_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )


def test_offline_production_end_to_end(api, fake_comfy, tmp_path):
    client, factory, settings = api
    offline = worker_settings(settings)
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)

    # 1. Create the film project.
    project_id = create_project(client, title="雾港夜账")

    # 2. Import the test script.
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("script.txt", SCRIPT.encode("utf-8"), "text/plain")},
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]

    # 3. Upload the test master assets (synthetic fixtures, never real user media).
    assets: dict[str, dict] = {}
    for key, filename, kind, content, content_type in (
        ("character", "林岚.png", "image", PNG, "image/png"),
        ("scene", "码头.png", "image", PNG + b"-scene-fixture", "image/png"),
        ("voice", "林岚.wav", "audio", make_wav_bytes(), "audio/wav"),
    ):
        response = client.post(
            f"/api/v1/projects/{project_id}/assets",
            data={"asset_kind": kind},
            files={"file": (filename, content, content_type)},
        )
        assert response.status_code == 201, response.text
        assets[key] = response.json()

    # 4. Human approval and lock. Uploading never approves.
    for asset in assets.values():
        assert asset["approved"] is False
        from tests.conftest import approve_lock

        approve_lock(client, asset["id"])
    locked = client.get(f"/api/v1/projects/{project_id}/assets").json()
    assert all(item["approved"] and item["locked"] for item in locked)

    # The operator names the entities so the planner can match user assets.
    for asset_id, role, entity in (
        (assets["character"]["id"], "character", "林岚"),
        (assets["scene"]["id"], "scene", "码头"),
        (assets["voice"]["id"], "voice", "林岚"),
    ):
        response = client.patch(
            f"/api/v1/assets/{asset_id}/metadata",
            json={"role": role, "entity_id": entity},
        )
        assert response.status_code == 200, response.text

    # 5. Create and freeze the Style Bible.
    style = freeze_style(client, project_id)
    assert style["sha256"]
    assert client.get(f"/api/v1/projects/{project_id}/style/impact").status_code == 200

    # The offline project policy accepts a mock reviewer and an unverified
    # technical toolchain. Both flags stay visible in the gate ledger.
    policy = client.put(
        f"/api/v1/projects/{project_id}/quality-policy",
        json={
            "require_semantic_review": True,
            "require_audio_review": True,
            "require_style_review": True,
            "min_confidence": 0.7,
            "accept_mock_review": True,
            "allow_unverified_technical": True,
            # This run exercises the bounded repair, so the model review
            # decides the production here.
            "advisory_model_reviews": False,
            "priority_thresholds": {"A": 0.85, "B": 0.7, "C": 0.55},
        },
    )
    assert policy.status_code == 200, policy.text
    enabled = client.patch(
        f"/api/v1/projects/{project_id}/settings",
        json={"auto_repair_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text

    # 6. The agent plans the structured shots from the imported script.
    plan = client.post(
        f"/api/v1/projects/{project_id}/agent/plan",
        json={"document_id": document_id, "max_shots": 3, "apply": True},
    )
    assert plan.status_code == 200, plan.text
    plan_body = plan.json()
    assert plan_body["shots"], plan_body
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert len(shots) >= 2

    # 7. The operator binds the approved assets per shot. Each shot keeps its
    #    own independent reference set.
    first, second = shots[0], shots[1]
    use_as_for = {
        assets["character"]["id"]: ("subject", "林岚"),
        assets["scene"]["id"]: ("picture", "码头"),
        assets["voice"]["id"]: ("audio", "林岚"),
    }

    def bindings_for(shot: dict) -> list[dict]:
        required = shot["spec"].get("required_asset_ids", [])
        rows = []
        for ordinal, asset_id in enumerate(required):
            use_as, role = use_as_for[asset_id]
            row = {"asset_id": asset_id, "use_as": use_as, "role": role, "ordinal": ordinal}
            if use_as == "picture":
                row["frame_role"] = "first_frame"
            rows.append(row)
        return rows

    for shot in (first, second):
        response = client.put(
            f"/api/v1/shots/{shot['id']}/bindings",
            json={"bindings": bindings_for(shot)},
        )
        assert response.status_code == 200, response.text
    refreshed = client.get(f"/api/v1/projects/{project_id}/shots").json()
    first, second = refreshed[0], refreshed[1]

    # 8. Each shot has an independent reference manifest.
    manifests = []
    for shot in (first, second):
        compiled = client.post(f"/api/v1/shots/{shot['id']}/compile")
        assert compiled.status_code == 200, compiled.text
        body = compiled.json()
        manifests.append(body["reference_manifest"])
        assert body["compiled_prompt"]
        assert body["prompt_sha256"]
        assert body["rule_version"].startswith("h3-prompt-writing@")
    assert manifests[0] != manifests[1]
    assert {item["asset_id"] for item in manifests[0]} != {item["asset_id"] for item in manifests[1]}

    # 9. Register the workflow profile and create the two dependent jobs.
    profile = client.post(
        "/api/v1/workflow-profiles",
        json={
            "name": "offline-h3-profile",
            "mode": "Ref2VA",
            "graph": E2E_GRAPH,
            "parameter_map": E2E_PARAM_MAP,
            "media_transfers": {
                "audio": {
                    "upload_path": "/upload/image",
                    "field_name": "image",
                    "subfolder": "shine",
                }
            },
            "required_nodes": ["H3BaseSampler"],
        },
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]

    job_first = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": first["id"],
            "provider": "comfyui",
            "workflow_profile_id": profile_id,
        },
    )
    assert job_first.status_code == 201, job_first.text
    job_first = job_first.json()

    job_second = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": second["id"],
            "provider": "comfyui",
            "workflow_profile_id": profile_id,
            "depends_on": [job_first["id"]],
        },
    )
    assert job_second.status_code == 201, job_second.text
    job_second = job_second.json()
    # The dependent job waits for the upstream job.
    assert job_second["status"] == "PLANNED"
    drift = SemanticFinding(
        code="STYLE_DRIFT",
        category="style",
        severity="hard",
        confidence=0.95,
        message="画面配色与冻结风格圣经不一致",
    )
    reviewer = ScriptedSemanticReviewer([[drift], [], []])
    asr = ScriptedASR("你终于来了。码头那边灯塔还亮着。")
    client_kwargs = {"comfy_client": ComfyUIClient(fake_comfy.base_url, timeout_s=2)}

    # 10-13. The first job renders, the fake ComfyUI produces media, the
    #        technical QC runs and the mock semantic review finds a style drift.
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=float(first["spec"]["duration_target_s"]))
    assert (
        run_one(
            factory,
            settings=offline,
            worker_id="worker-e2e",
            semantic_reviewer=reviewer,
            asr_reviewer=asr,
            **client_kwargs,
        )
        == job_first["id"]
    )
    first_state = client.get(f"/api/v1/jobs/{job_first['id']}").json()
    assert first_state["status"] == "QUEUED", (
        first_state,
        client.get(f"/api/v1/projects/{project_id}/events", params={"limit": 50}).json(),
    )
    assert first_state["semantic_retries"] == 1
    assert first_state["repairs"][0]["outcome"] == "auto_queued"
    assert fake_comfy.prompt_count == 1

    # 14-17. The repair produced a materially different request; the new
    #         attempt is executed by the fake ComfyUI and re-checked.
    assert (
        run_one(
            factory,
            settings=offline,
            worker_id="worker-e2e",
            semantic_reviewer=reviewer,
            asr_reviewer=asr,
            **client_kwargs,
        )
        == job_first["id"]
    )
    first_state = client.get(f"/api/v1/jobs/{job_first['id']}").json()
    assert first_state["status"] == "PASSED"
    assert first_state["quality_verification"] == "mock_verified"
    attempts = first_state["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["request_fingerprint"] != attempts[1]["request_fingerprint"]
    assert attempts[0]["prompt_sha256"] != attempts[1]["prompt_sha256"]
    assert attempts[1]["attempt_kind"] == "repair"
    assert attempts[1]["repair_reason"]
    assert fake_comfy.prompt_count == 2

    gates = client.get(f"/api/v1/jobs/{job_first['id']}/quality-gates").json()
    assert {"technical", "semantic", "audio", "style", "final"}.issubset({gate["gate_name"] for gate in gates})
    # The ledger keeps one row per gate and attempt. The first attempt failed
    # the semantic gate, the repaired attempt passed every gate.
    finals = [gate for gate in gates if gate["gate_name"] == "final"]
    assert [gate["status"] for gate in finals] == ["failed", "passed"]
    final_gate = finals[-1]
    assert final_gate["status"] == "passed"
    assert final_gate["evidence"]["verification"] == "mock_verified"

    # The upstream re-render invalidated the dependent job. Its validity is
    # recomputed, never silently reused.
    invalidated = client.get(f"/api/v1/jobs/{job_second['id']}").json()
    assert invalidated["stale"] is True
    assert invalidated["status"] == "NEEDS_REVIEW"
    assert invalidated["failure_code"] == "DEPENDENCY_INVALIDATED"

    # 18-19. The dependent shot is re-queued and runs once its parent passed.
    job_second_retry = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": second["id"],
            "provider": "comfyui",
            "workflow_profile_id": profile_id,
            "depends_on": [job_first["id"]],
        },
    )
    assert job_second_retry.status_code == 201, job_second_retry.text
    job_second = job_second_retry.json()
    assert job_second["status"] == "QUEUED"
    fake_comfy.mode = "accept_then_drop"
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=float(second["spec"]["duration_target_s"]))
    assert (
        run_one(
            factory,
            settings=offline,
            worker_id="worker-e2e",
            semantic_reviewer=reviewer,
            asr_reviewer=asr,
            **client_kwargs,
        )
        == job_second["id"]
    )
    second_state = client.get(f"/api/v1/jobs/{job_second['id']}").json()
    # The acknowledgement was lost, so the job is blocked for reconciliation.
    assert second_state["status"] == "NEEDS_REVIEW"
    assert second_state["failure_code"] == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
    assert fake_comfy.prompt_count == 3

    # 20. The worker restarts and reconciles the lost acknowledgement. The
    #     original external task is collected; nothing is rendered again.
    recovered = recover_expired_jobs(
        factory,
        "worker-e2e-restarted",
        settings=offline,
        semantic_reviewer=reviewer,
        asr_reviewer=asr,
        **client_kwargs,
    )
    assert recovered == 1
    assert fake_comfy.prompt_count == 3
    second_state = client.get(f"/api/v1/jobs/{job_second['id']}").json()
    assert second_state["status"] in {"PASSED", "QC_PENDING", "NEEDS_REVIEW"}
    assert second_state["artifact_id"]
    assert second_state["attempts"][0]["reconciliation_state"] in {
        "history_found",
        "collected",
    }

    # 21. No duplicate external submissions, no wrong asset references and no
    #     stale results entered the library.
    with factory.begin() as db:
        rows = db.query(MediaArtifact).all()
        assert len({(row.job_id, row.attempt_id) for row in rows}) == len(rows)
        assert all(row.is_mock is False for row in rows)
        for row in rows:
            assert (Path(settings.data_dir) / row.storage_path).is_file()
        job_rows = db.query(Job).filter(Job.project_id == project_id).all()
        executed = {job_first["id"], job_second["id"]}
        for job in job_rows:
            if job.id in executed:
                assert job.stale is False
            elif job.stale:
                # An invalidated job never received a result.
                assert job.artifact_id is None
    assert len(fake_comfy.prompts) == 3
    # Every reference upload belongs to the shot that declared it.
    uploaded_names = sorted(upload["filename"] for upload in fake_comfy.uploads)
    assert any("林岚" not in name for name in uploaded_names)
    assert fake_comfy.upload_count == len(fake_comfy.uploads)

    # 22. The complete audit chain, the job versions and the quality report.
    events = client.get(
        f"/api/v1/projects/{project_id}/events",
        params={"limit": 500},
    ).json()
    event_types = [event["event_type"] for event in events["items"]]
    for required in (
        "PROJECT_CREATED",
        "ASSET_UPLOADED",
        "ASSET_APPROVED",
        "ASSET_LOCKED",
        "STYLE_FROZEN",
        "SHOT_CREATED",
        "COMFY_JOB_CREATED",
        "SUBMIT_INTENT",
        "COMFY_ACKNOWLEDGED",
        "COMFY_ARTIFACT_QC",
        "AUTO_REPAIR_QUEUED",
        "SUBMISSION_REQUIRES_RECONCILIATION",
        "COMFY_RECONCILED_HISTORY",
    ):
        assert required in event_types, (required, sorted(set(event_types)))

    jobs = client.get(f"/api/v1/projects/{project_id}/jobs").json()
    report = {
        "jobs": [
            {
                "id": job["id"],
                "status": job["status"],
                "request_revision": job["request_revision"],
                "attempts": len(job["attempts"]),
                "quality_verification": job["quality_verification"],
            }
            for job in jobs
        ],
        "gates": len(gates),
        "external_prompts": fake_comfy.prompt_count,
        "uploads": fake_comfy.upload_count,
        "mock_marker": "mock_verified",
    }
    repaired = next(job for job in report["jobs"] if job["id"] == job_first["id"])
    assert repaired["request_revision"] == 2
    assert repaired["attempts"] == 2
    assert report["external_prompts"] == 3
    assert report["mock_marker"] == "mock_verified"


def test_offline_e2e_survives_a_worker_crash_without_duplicating_the_render(api, fake_comfy):
    client, factory, settings = api
    offline = worker_settings(settings)
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client, title="崩溃恢复")
    style = freeze_style(client, project_id)
    shot = client.post(
        f"/api/v1/projects/{project_id}/shots",
        json={
            "name": "恢复镜头",
            "spec": {
                "story_beat": "雨夜码头交换线索",
                "duration_target_s": 6,
                "shot_priority": "B",
                "style_version_id": style["id"],
                "characters": ["林岚"],
                "dialogue": [{"speaker_id": "林岚", "text": "东西在里面", "start_s": 0.0, "end_s": 2.0}],
            },
        },
    )
    assert shot.status_code == 201, shot.text
    profile = client.post(
        "/api/v1/workflow-profiles",
        json={
            "name": "crash-profile",
            "mode": "T2VA",
            "graph": COMFY_GRAPH,
            "parameter_map": {
                "positive_prompt": "1.inputs.positive_prompt",
                "seed": "1.inputs.seed",
                "duration": "1.inputs.duration",
                "reference_picture_1": "1.inputs.reference_picture_1",
            },
            "required_nodes": ["H3BaseSampler"],
        },
    )
    assert profile.status_code == 201, profile.text
    job = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": shot.json()["id"],
            "provider": "comfyui",
            "workflow_profile_id": profile.json()["id"],
        },
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]

    # The worker dies right after ComfyUI accepted the prompt.
    fake_comfy.mode = "accept_then_drop"
    run_one(
        factory,
        settings=offline,
        worker_id="worker-crash",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert fake_comfy.prompt_count == 1
    blocked = client.get(f"/api/v1/jobs/{job_id}").json()
    assert blocked["status"] == "NEEDS_REVIEW"

    # The restarted worker reconciles and collects the original task.
    recovered = recover_expired_jobs(
        factory,
        "worker-restarted",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert recovered == 1
    assert fake_comfy.prompt_count == 1
    final = client.get(f"/api/v1/jobs/{job_id}").json()
    assert final["artifact_id"]
    assert len(final["attempts"]) == 1

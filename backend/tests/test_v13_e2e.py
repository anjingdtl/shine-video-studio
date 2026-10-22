"""Actual database/API/crew/compiler/worker loop. All synthesis and reviewer quality is FAKE."""

from dataclasses import replace

import pytest

from shine.comfy.client import ComfyUIClient
from shine.schemas import SemanticFinding
from shine.voiceops.providers import LocalVoiceHTTPProvider
from shine.worker import run_one
from tests.conftest import create_project, make_test_video_bytes, register_workflow_profile
from tests.fake_http import FakeComfyServer
from tests.fake_voice import FakeVoiceServer
from tests.test_film_crew import CAMERA, CONTINUITY, ENGLISH_STYLE, call
from tests.test_offline_e2e import E2E_GRAPH, E2E_PARAM_MAP, ScriptedASR, ScriptedSemanticReviewer
from tests.test_v13_preparation import approve, package, post, prep, rights, workflow  # noqa: F401


@pytest.fixture
def fake_voice_server():
    server = FakeVoiceServer().start()
    try:
        yield server
    finally:
        server.stop()


def test_v13_full_preproduction_to_fake_h3_repair_and_archive(prep, fake_voice_server):  # noqa: F811
    client, factory, settings = prep
    client.app.state.voice_provider = LocalVoiceHTTPProvider(fake_voice_server.base_url)
    project = create_project(client, "V1.3 simulated production")
    line = {"speaker_id": "Lin", "text": "你终于来了。", "start_s": 0.5, "end_s": 2}
    script_text = "场景：雨夜主街\n道具：账册\nLin：你终于来了。"
    doc = client.post(
        f"/api/v1/projects/{project}/documents", files={"file": ("script.txt", script_text.encode(), "text/plain")}
    ).json()
    plan = post(client, f"/projects/{project}/preproduction/analyze", {"document_id": doc["id"]})
    blocked = post(client, f"/projects/{project}/preproduction/readiness")
    assert blocked["status"] == "BLOCKED"
    assert any(b.get("category") == "H3_REF2VA" for b in blocked["blockers"])
    post(client, "/comfyops/inventory/refresh")
    model = package(client)
    install = post(
        client, "/comfyops/install-plans", {"project_id": project, "package_ids": [model["id"]], "disk_free": 1000}
    )
    install = approve(client, install, "install", f"/comfyops/install-plans/{install['id']}/approve")
    post(client, f"/comfyops/install-plans/{install['id']}/execute", {"plan_hash": install["sha256"]}, operator=True)
    image = workflow(client, model_ids=[model["id"]])
    for category in ("H3_REF2VA", "VLM_REVIEW", "ASR"):
        workflow(client, category, [model["id"]])
    asset_right = rights(client, project)
    formal = {}
    voice_right = rights(client, project, "voice_design")
    batch_body = {
        "asset_rights_id": asset_right["id"],
        "workflow_id": image["id"],
        "voice_rights_id": voice_right["id"],
    }
    for _ in range(2):
        batch = post(client, f"/projects/{project}/preproduction/run", batch_body)
        assert batch["status"] == "PENDING_HUMAN_APPROVAL"
        assert batch["generated"]
        for task in batch["generated"]:
            candidates = client.get(f"/api/v1/projects/{project}/candidate-assets").json()
            row = next(c for c in candidates if c["id"] == task["candidate_id"])
            row = approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote")
            formal[row["payload"]["family"]] = row["formal_asset_id"]
    assert set(formal) == {"character", "costume", "scene", "prop", "voice"}
    voice = client.get(f"/api/v1/projects/{project}/voice-profiles").json()[0]
    voice = approve(client, voice, "voice_freeze", f"/voice-profiles/{voice['id']}/approve-and-freeze")
    audio = post(
        client,
        f"/voice-profiles/{voice['id']}/dialogue",
        {"character": "Lin", "text": line["text"], "voice_hash": voice["sha256"]},
    )
    assert audio["payload"]["words"] and audio["payload"]["subtitles"]["srt"]
    assert {"/design", "/tts", "/asr", "/align", "/free"} <= set(fake_voice_server.calls)
    assert client.put(f"/api/v1/projects/{project}/style/draft", json=ENGLISH_STYLE).status_code == 200
    post(client, f"/projects/{project}/style/freeze", expected=201)
    post(client, f"/projects/{project}/rulepack/freeze")
    ready = post(client, f"/projects/{project}/preproduction/readiness")
    assert ready["status"] == "SHOOT_READY", ready
    frozen = approve(client, plan, "manifest_freeze", f"/projects/{project}/preproduction/freeze")
    assert frozen["payload"]["manifest"]["offline"]
    assert post(client, f"/projects/{project}/preproduction/readiness", {"offline": False})["status"] == "BLOCKED"

    profile = post(client, "/provider-profiles", {"name": "crew-v13", "kind": "mock", "model": "fake"}, expected=201)
    for role in ("scriptwriter", "director", "cinematographer", "continuity_supervisor", "comfyops"):
        assert (
            client.put("/api/v1/agent-routes", json={"role": role, "provider_profile_id": profile["id"]}).status_code
            == 200
        )
    ops = call(
        client,
        project,
        "comfyops",
        {
            "operations": ["search_capabilities"],
            "capability_requests": ["H3_REF2VA"],
            "rationale": "Use the frozen offline registry.",
        },
    )
    assert ops["kind"] == "ComfyOpsProposal"
    # The crew's shared test helper uses this brief, so keep its Chinese dialogue verbatim.
    from tests.test_film_crew import FACTS, LINE

    script = call(
        client,
        project,
        "scriptwriter",
        {
            "locked_story_facts": FACTS,
            "beats": [{"id": "b1", "description": "Lin waits in the rain.", "characters": ["Lin"], "dialogue": [LINE]}],
        },
    )
    direction = call(
        client,
        project,
        "director",
        {
            "shots": [
                {
                    "id": "I1",
                    "beat_id": "b1",
                    "visual_description": "Lin waits under a street lamp.",
                    "duration_s": 6,
                    "asset_ids": [formal["character"]],
                }
            ]
        },
        inputs=[script["id"]],
    )
    camera = call(client, project, "cinematographer", CAMERA, inputs=[direction["id"]])
    continuity = call(client, project, "continuity_supervisor", CONTINUITY, inputs=[direction["id"], camera["id"]])
    applied = post(
        client,
        f"/projects/{project}/film-artifacts/{camera['id']}/apply-camera",
        {"continuity_artifact_id": continuity["id"]},
    )
    shot = applied["shot_ids"][0]
    # Bind frozen voice sample through the existing H3 media contract.
    assert (
        client.put(
            f"/api/v1/shots/{shot}/bindings",
            json={
                "bindings": [
                    {"asset_id": formal["character"], "use_as": "subject", "ordinal": 0},
                    {"asset_id": formal["voice"], "use_as": "audio", "ordinal": 1},
                ]
            },
        ).status_code
        == 200
    )
    wf = register_workflow_profile(
        client,
        name="v13-fake-ref2va",
        mode="Ref2VA",
        graph=E2E_GRAPH,
        parameter_map=E2E_PARAM_MAP,
        media_transfers={"audio": {"upload_path": "/upload/image", "field_name": "image", "subfolder": "shine"}},
    )
    assert (
        client.put(
            f"/api/v1/projects/{project}/quality-policy",
            json={"accept_mock_review": True, "allow_unverified_technical": True},
        ).status_code
        == 200
    )
    assert client.patch(f"/api/v1/projects/{project}/settings", json={"auto_repair_enabled": True}).status_code == 200
    settings = replace(settings, offline_simulation=True, ffprobe_path="missing-ffprobe", ffmpeg_path="missing-ffmpeg")
    client.app.state.settings = settings
    job = post(
        client,
        f"/projects/{project}/jobs",
        {"shot_id": shot, "provider": "comfyui", "workflow_profile_id": wf["id"]},
        expected=201,
    )
    reviewer = ScriptedSemanticReviewer(
        [
            [
                SemanticFinding(
                    code="STYLE_DRIFT",
                    category="style",
                    severity="hard",
                    confidence=0.95,
                    message="Synthetic style mismatch",
                )
            ],
            [],
        ]
    )
    asr = ScriptedASR(LINE["text"])
    server = FakeComfyServer().start()
    try:
        server.output_bytes = make_test_video_bytes(duration_s=6, width=320, height=240)
        comfy = ComfyUIClient(server.base_url, max_retries=0)
        for _ in range(4):
            run_one(factory, settings=settings, comfy_client=comfy, semantic_reviewer=reviewer, asr_reviewer=asr)
            current = client.get(f"/api/v1/jobs/{job['id']}").json()
            if current["status"] == "PASSED":
                break
        assert current["status"] == "PASSED", current
        assert current["quality_verification"] == "mock_verified"
        assert len(current["attempts"]) == 2
        assert current["attempts"][0]["request_fingerprint"] != current["attempts"][1]["request_fingerprint"]
        gates = client.get(f"/api/v1/jobs/{job['id']}/quality-gates").json()
        assert any(g["is_mock"] for g in gates)
    finally:
        server.stop()
    events = client.get(f"/api/v1/projects/{project}/events?limit=500").json()["items"]
    types = {e["event_type"] for e in events}
    assert {"CANDIDATE_PROMOTED", "VOICE_FROZEN", "PREPRODUCTION_FROZEN", "SUBMIT_INTENT"} <= types
    # Rights withdrawal invalidates the frozen snapshot at both API and worker boundaries.
    post(client, f"/preparation/rights/{asset_right['id']}/revoke", operator=True)
    post(client, f"/projects/{project}/jobs", {"shot_id": shot, "provider": "mock"}, expected=409)

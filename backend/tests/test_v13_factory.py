"""Audited factory contracts, structured requirements, SDK and production adapter paths."""

# ruff: noqa: F811
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from shine.comfyops.assets import fake_image
from shine.comfyops.candidate_quality import FakeCandidateReviewer
from shine.comfyops.images import ComfyImageProvider
from shine.comfyops.registry import assemble_template
from shine.comfyops.requirements import extract_structured
from shine.models import AgentTaskRecord
from shine.voiceops.local_sdk import QwenLocalVoiceProvider
from tests.conftest import create_project
from tests.test_v13_instances import install_fixture
from tests.test_v13_preparation import approve, candidate, post, prep, rights, workflow  # noqa: F401


@pytest.mark.parametrize("changed", [False, True])
def test_legacy_reviewer_release_target_needs_human_grant_and_exact_profile(prep, monkeypatch, changed):
    from shine.comfyops.models import ResourceLease
    from shine.models import ProviderProfile
    from tests.test_v13_preparation import HEADERS

    client, factory, _ = prep
    project = create_project(client)
    profile = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": "local-review-release",
            "kind": "openai_compatible",
            "model": "pinned-vlm",
            "base_url": "http://127.0.0.1:19001/v1",
        },
    ).json()
    with factory.begin() as db:
        from shine.models import utcnow

        lease = ResourceLease(
            owner="vlm:legacy",
            status="RECONCILE_REQUIRED",
            budget_mb=8000,
            exclusive=True,
            priority=10,
            expires_at=utcnow(),
        )
        db.add(lease)
        db.flush()
        task = AgentTaskRecord(
            project_id=project,
            role_id="model_call:VLM:review",
            input_hash="a" * 64,
            asset_manifest_hash="b" * 64,
            rulepack_id="test",
            rulepack_hash="c" * 64,
            model_profile_id="legacy",
            model_id="LocalVisionReviewer",
            status="NEEDS_REVIEW",
            trace={
                "kind": "VLM",
                "is_mock": False,
                "owner_pid": 999999999,
                "owner_birth": "dead-test-owner",
                "resource_id": lease.id,
            },
        )
        db.add(task)
        db.flush()
        task_id, lease_id = task.id, lease.id
    releases = []
    monkeypatch.setattr(
        "shine.quality.semantic.LocalVisionReviewer.release_models", lambda self: releases.append(self.config.model)
    )
    proposed = post(
        client, f"/preparation/model-calls/{task_id}/release-provider-proposal", {"provider_profile_id": profile["id"]}
    )
    assert proposed["trace"]["review_provider"]["legacy_proposal_only"] is True
    assert not releases
    denied = client.post(
        f"/api/v1/preparation/model-calls/{task_id}/abandon",
        json={"grant_id": "forged", "snapshot": "forged", "evidence": "No synthetic operator grant yet"},
        headers=HEADERS,
    )
    assert denied.status_code == 409 and not releases
    grant = post(
        client, "/preparation/approval-grants", {"action": "model_reconcile", "target_id": task_id}, operator=True
    )
    if changed:
        with factory.begin() as db:
            db.get(ProviderProfile, profile["id"]).model = "substituted-model"
    response = client.post(
        f"/api/v1/preparation/model-calls/{task_id}/abandon",
        json={
            **{k: grant[k] for k in ["grant_id", "snapshot"]},
            "evidence": "Explicit synthetic test operator reconciliation",
        },
        headers=HEADERS,
    )
    assert response.status_code == (409 if changed else 200), response.text
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == ("RECONCILE_REQUIRED" if changed else "RELEASED")
    assert releases == ([] if changed else ["pinned-vlm"])


def test_audited_h3_templates_preserve_graph_and_unverified_status():
    from shine.comfy.workflow import graph_hash

    a = assemble_template("h3-ref2va-2img", "audited draft")
    b = assemble_template("h3-ref2va-2img-1aud", "audited audio draft")
    assert graph_hash(a.graph) == "6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112"
    assert len(b.reference_slots) == len(a.reference_slots) + 1
    assert b.template_audit["verification"] == "DRAFT_REQUIRES_LOCAL_VALIDATION"
    with pytest.raises(Exception, match="UNTRUSTED_TEMPLATE"):
        assemble_template("invented-upscaler", "bad")


def test_structured_script_keeps_critical_entities_and_exact_dialogue():
    script = {
        "scenes": [
            {
                "id": "s1",
                "location": "RainStreet",
                "shots": [
                    {
                        "id": "sh1",
                        "characters": [{"id": "Lin", "outfit_id": "coat-v2"}],
                        "props": ["Ledger"],
                        "dialogue": [{"speaker_id": "Lin", "text": "你终于来了。", "start_s": 0, "end_s": 2}],
                    }
                ],
            }
        ]
    }
    reqs, records = extract_structured(script)
    assert all(r["critical"] for r in reqs)
    assert {r["family"] for r in reqs} >= {"scene", "character", "costume", "prop", "voice", "dialogue", "subtitle"}
    assert next(r for r in reqs if r["family"] == "costume")["entity_id"] == "coat-v2"
    assert records["dialogue"][0]["text"] == "你终于来了。"
    assert all(r["shot_ids"] == ["sh1"] for r in reqs if r["family"] != "scene")
    assert next(r for r in reqs if r["family"] == "dialogue")["satisfaction"] == "LOCKED_SCRIPT"


def test_workflow_constraints_and_deprecation_are_human_controlled(prep):
    client, _, _ = prep
    row = workflow(client, generation_modes=["Ref2VA"], duration_range=[4, 8], aspect_ratios=["16:9"], resource_mb=8000)
    matched = post(
        client,
        "/workflow-capabilities/match",
        {
            "category": "IMAGE_GENERATION",
            "generation_mode": "Ref2VA",
            "duration_s": 6,
            "aspect_ratio": "16:9",
            "budget_mb": 9000,
        },
    )
    assert [r["id"] for r in matched["matches"]] == [row["id"]]
    rejected = post(
        client,
        "/workflow-capabilities/match",
        {
            "category": "IMAGE_GENERATION",
            "duration_s": 12,
            "references": {"video": 1},
            "continuity": "hard_frame_anchor",
        },
    )
    assert not rejected["matches"] and len(rejected["rejected"][0]["reasons"]) >= 3
    post(client, f"/workflow-capabilities/{row['id']}/deprecate", expected=403)
    approved = approve(client, row, "workflow_deprecate", f"/workflow-capabilities/{row['id']}/deprecate")
    assert approved["status"] == "DEPRECATED"
    assert (
        client.get(f"/api/v1/workflow-capabilities/{row['id']}").json()["payload"]["graph"] == row["payload"]["graph"]
    )
    assert not post(client, "/workflow-capabilities/match", {"category": "IMAGE_GENERATION"})["matches"]


@pytest.mark.parametrize(
    "result,confidence,status",
    [("FAIL", 0.99, "QC_FAILED"), ("PASS", 0.99, "READY_FOR_REVIEW"), ("UNCERTAIN", 0.3, "READY_FOR_REVIEW")],
)
def test_candidate_reviewer_evidence_and_manual_boundary(prep, result, confidence, status):
    client, factory, _ = prep
    project = create_project(client)
    row = candidate(client, project, rights(client, project), workflow(client))
    client.app.state.candidate_reviewer = FakeCandidateReviewer(result, confidence)
    qc = post(client, f"/candidate-assets/{row['id']}/qc")
    assert qc["status"] == status
    assert qc["payload"]["qc"]["semantic_evidence"]["is_mock"]
    assert qc["formal_asset_id"] is None
    with factory() as db:
        tasks = db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:VLM:review")).all()
        assert tasks and tasks[0].status == "COMPLETED" and tasks[0].trace["resource_id"]


class FakeSDK:
    def generate_voice_design(self, **kwargs):
        assert kwargs["language"] == "Chinese"
        return [[0.1] * 16000], 16000

    def generate_voice_clone(self, **kwargs):
        assert kwargs["language"] == "Chinese" and "ref_audio" in kwargs
        return [[0.1] * 16000], 16000

    def transcribe(self, **kwargs):
        return [SimpleNamespace(text="你好", language="Chinese", speaker="Lin", confidence=0.99)]

    def align(self, **kwargs):
        return [
            [
                SimpleNamespace(text="你", start_time=0, end_time=0.5),
                SimpleNamespace(text="好", start_time=0.5, end_time=1),
            ]
        ]


class FakeSDKLoader:
    is_mock = True

    def __call__(self, role, path):
        assert path.is_dir()
        return FakeSDK()


def test_sdk_voice_design_freeze_tts_asr_alignment_through_real_api(prep):
    client, factory, settings = prep
    packages = {}
    for role, cap in {
        "design": "VOICE_DESIGN",
        "base": "VOICE_CLONE",
        "asr": "ASR",
        "align": "FORCED_ALIGNMENT",
    }.items():
        packages[role] = install_fixture(
            prep, "sdk-" + role, {"fixture.bin": b"synthetic"}, kind="model", capabilities=[cap]
        )["id"]
    provider = QwenLocalVoiceProvider(factory, settings, packages, FakeSDKLoader())
    client.app.state.voice_provider = provider
    project = create_project(client)
    ar, vr = rights(client, project), rights(client, project, "voice_design")
    voice = post(
        client,
        f"/projects/{project}/voice-profiles/design",
        {"character": "Lin", "description": "test", "text": "你好", "rights_id": vr["id"], "asset_rights_id": ar["id"]},
    )
    row = post(client, f"/candidate-assets/{voice['payload']['candidate_id']}/qc")
    approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote")
    voice = approve(client, voice, "voice_freeze", f"/voice-profiles/{voice['id']}/approve-and-freeze")
    render = post(
        client,
        f"/voice-profiles/{voice['id']}/dialogue",
        {"character": "Lin", "text": "你好", "voice_hash": voice["sha256"]},
    )
    assert render["payload"]["errors"] == [] and render["payload"]["is_mock"]
    assert render["payload"]["words"][1]["end_s"] == 1
    assert not provider.models
    with factory() as db:
        roles = {t.role_id for t in db.scalars(select(AgentTaskRecord)) if t.status == "COMPLETED"}
        assert {
            "model_call:TTS:design",
            "model_call:TTS:synthesize",
            "model_call:ASR:transcribe",
            "model_call:ALIGNMENT:align",
        } <= roles
    with pytest.raises(ValueError, match="VOICE_CONDITIONING_HASH_MISMATCH"):
        provider.prepare_conditioning(b"corrupt", "a" * 64, "你好")


def test_production_comfy_image_bridge_uses_official_client_contract():
    class FakeClient:
        def object_info(self):
            return {"ImageSampler": {"input": {"required": {"prompt": ["STRING"], "seed": ["INT"]}}}}

        def queue_prompt(self, graph, **kwargs):
            assert graph["1"]["inputs"]["prompt"] == "A rainy street"
            return {"prompt_id": "fixture"}

        def history(self, pid):
            return {pid: {"outputs": {"1": {"images": [{"filename": "fixture.png", "type": "output"}]}}}}

        def view(self, filename, **kwargs):
            return fake_image(17)

        def free_models(self):
            pass

    provider = ComfyImageProvider(FakeClient(), interval=0)
    result = provider.generate(
        "A rainy street",
        17,
        {
            "name": "fixture",
            "category": "IMAGE_GENERATION",
            "graph": {"1": {"class_type": "ImageSampler", "inputs": {"prompt": "", "seed": 0}}},
            "parameter_map": {"prompt": "1.inputs.prompt", "seed": "1.inputs.seed"},
            "model_ids": ["fixture-package"],
        },
        [],
    )
    assert result["content"] == fake_image(17) and result["prompt_id"] == "fixture"


def test_uncertain_model_call_never_replays_until_explicit_fake_reconciliation(prep):
    from shine.comfyops.model_calls import TrackedProvider

    client, factory, _ = prep
    project = create_project(client)

    class LostProvider:
        is_mock = True
        count = 0

        def design(self, *args):
            self.count += 1
            raise TimeoutError("synthetic lost response")

    raw = LostProvider()
    provider = TrackedProvider(raw, factory, project)
    with pytest.raises(TimeoutError):
        provider.design("same request")
    with pytest.raises(Exception, match="MODEL_CALL_RECONCILIATION_REQUIRED"):
        provider.design("same request")
    assert raw.count == 1
    rows = client.get(
        f"/api/v1/projects/{project}/preparation/model-calls",
        headers=__import__("tests.test_v13_preparation", fromlist=["HEADERS"]).HEADERS,
    ).json()
    assert len(rows) == 1 and rows[0]["status"] == "NEEDS_REVIEW"
    result = approve(client, rows[0], "model_reconcile", f"/preparation/model-calls/{rows[0]['id']}/abandon")
    assert result["status"] == "ABANDONED"
    assert result["trace"]["outcome"] == "LOST_RESULT_NOT_APPROVED"
    with pytest.raises(TimeoutError):
        provider.design("same request")
    assert raw.count == 2


def test_model_proxy_never_calls_provider_when_gpu_exclusive(prep):
    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.voiceops.providers import FakeVoiceProvider

    client, factory, _ = prep
    project = create_project(client)
    with factory.begin() as db:
        lease = ResourceScheduler().enqueue(db, "synthetic-h3", 16384, True, 100)
        identity = lease.id
    with factory() as db:
        assert ResourceScheduler().admit(db, identity)

    class NoCall(FakeVoiceProvider):
        def design(self, *args):
            raise AssertionError("model must never execute while H3 holds the GPU")

    with pytest.raises(Exception, match="GPU_BUSY_RETRY_OR_QUEUE"):
        TrackedProvider(NoCall(), factory, project).design("guarded", "你好", "Lin")


def test_resource_slot_heartbeats_a_long_running_model(prep):
    import time

    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import resource_slot

    _, factory, _ = prep
    with resource_slot(factory, "TTS", 4000) as identity:
        with factory() as db:
            first = db.get(ResourceLease, identity).heartbeat_at
        time.sleep(16)
        with factory() as db:
            lease = db.get(ResourceLease, identity)
            assert lease.status == "ACTIVE" and lease.heartbeat_at > first
    with factory() as db:
        assert db.get(ResourceLease, identity).status == "RELEASED"


def test_image_external_ack_is_durable_before_poll_and_uncertain_replay_blocks(prep):
    from shine.comfyops.model_calls import TrackedProvider

    client, factory, _ = prep
    project = create_project(client)

    class Client:
        is_mock = True
        submissions = 0

        def object_info(self):
            return {"ImageSampler": {"input": {"required": {"prompt": ["STRING"], "seed": ["INT"]}}}}

        def queue_prompt(self, graph, **kwargs):
            self.submissions += 1
            return {"prompt_id": "durable-fixture"}

        def history(self, pid):
            with factory() as db:
                row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:IMAGE:generate"))
                assert row.trace["external_id"] == pid and row.trace["graph_sha256"]
            raise TimeoutError("response lost after acknowledged external id")

        def free_models(self):
            pass

    raw = Client()
    provider = TrackedProvider(ComfyImageProvider(raw, interval=0), factory, project)
    workflow = {
        "name": "fixture",
        "category": "IMAGE_GENERATION",
        "graph": {"1": {"class_type": "ImageSampler", "inputs": {"prompt": "", "seed": 0}}},
        "parameter_map": {"prompt": "1.inputs.prompt", "seed": "1.inputs.seed"},
    }
    with pytest.raises(TimeoutError):
        provider.generate("fixture", 1, workflow, [])
    with pytest.raises(Exception, match="MODEL_CALL_RECONCILIATION_REQUIRED"):
        provider.generate("fixture", 1, workflow, [])
    assert raw.submissions == 1


def test_clone_dialogue_has_separate_request_bound_execution_grant(prep):
    from shine.api_preparation import Dialogue
    from tests.test_v13_adversarial import authorize_clone, clone_request
    from tests.test_v13_preparation import HEADERS

    client, _, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    voice = post(client, f"/projects/{project}/voice-profiles/design", authorize_clone(client, body), operator=True)
    candidate_row = post(client, f"/candidate-assets/{voice['payload']['candidate_id']}/qc")
    approve(client, candidate_row, "promote", f"/candidate-assets/{candidate_row['id']}/promote")
    voice = approve(client, voice, "voice_freeze", f"/voice-profiles/{voice['id']}/approve-and-freeze")
    payload = {"character": "Lin", "text": "你好。", "voice_hash": voice["sha256"]}
    path = f"/voice-profiles/{voice['id']}/dialogue"
    post(client, path, payload, expected=403)
    post(client, path, payload, operator=True, expected=409)
    grant = post(
        client,
        "/preparation/approval-grants",
        {
            "action": "voice_execute",
            "target_id": body["rights_id"],
            "context": {
                "voice_id": voice["id"],
                **Dialogue.model_validate(payload).model_dump(exclude={"execution_decision"}),
            },
        },
        operator=True,
    )
    payload["execution_decision"] = {
        "grant_id": grant["grant_id"],
        "snapshot": grant["snapshot"],
        "evidence": "Human authorized only this dialogue execution",
    }
    post(client, path, {**payload, "text": "changed"}, operator=True, expected=409)
    result = post(client, path, payload, operator=True)
    assert result["status"] == "MOCK_VERIFIED"
    post(client, path, payload, operator=True, expected=409)
    assert client.get(f"/api/v1/voice-renders/{result['id']}/subtitles?format=srt", headers=HEADERS).status_code == 200


def test_unknown_asr_identity_requires_human_listening_without_invented_confidence(prep):
    from shine.voiceops.providers import FakeVoiceProvider

    client, _, _ = prep
    project = create_project(client)
    right = rights(client, project, "voice_design")
    voice = post(
        client,
        f"/projects/{project}/voice-profiles/design",
        {
            "character": "Lin",
            "description": "fixture",
            "text": "你好。",
            "rights_id": right["id"],
            "asset_rights_id": rights(client, project)["id"],
        },
    )
    candidate_row = post(client, f"/candidate-assets/{voice['payload']['candidate_id']}/qc")
    approve(client, candidate_row, "promote", f"/candidate-assets/{candidate_row['id']}/promote")
    voice = approve(client, voice, "voice_freeze", f"/voice-profiles/{voice['id']}/approve-and-freeze")

    class UnknownIdentity(FakeVoiceProvider):
        def transcribe(self, audio):
            return {**super().transcribe(audio), "speaker": "UNKNOWN", "confidence": 0}

    client.app.state.voice_provider = UnknownIdentity()
    result = post(
        client,
        f"/voice-profiles/{voice['id']}/dialogue",
        {"character": "Lin", "text": "今晚我们在街角再见。", "voice_hash": voice["sha256"]},
    )
    assert result["status"] == "PENDING_HUMAN_REVIEW"
    row = next(
        r
        for r in client.get(f"/api/v1/projects/{project}/candidate-assets").json()
        if r["id"] == result["payload"]["candidate_id"]
    )
    assert row["status"] == "READY_FOR_REVIEW" and row["payload"]["asr_evidence"]["confidence"] == 0
    # The grant cannot waive the listening requirement; hard text/alignment errors remain failures.
    grant = post(client, "/preparation/approval-grants", {"action": "promote", "target_id": row["id"]}, operator=True)
    post(
        client,
        f"/candidate-assets/{row['id']}/promote",
        {
            "grant_id": grant["grant_id"],
            "snapshot": grant["snapshot"],
            "offline": True,
            "listened": False,
            "evidence": "Not yet listened",
        },
        operator=True,
        expected=409,
    )
    promoted = approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote", listened=True)
    assert promoted["status"] == "PROMOTED" and promoted["payload"]["human_dialogue_review"]["listened"]


def test_release_failure_and_invalid_reconciliation_grant_never_free_gpu(prep):
    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.models import ResourceLease
    from tests.test_v13_preparation import HEADERS

    client, factory, _ = prep
    project = create_project(client)

    class UncertainProvider:
        is_mock = False
        releases = 0

        def design(self, *args):
            raise TimeoutError("synthetic provider loss")

        def release_models(self):
            self.releases += 1
            raise OSError("synthetic release failure")

    raw = UncertainProvider()
    client.app.state.voice_provider = raw
    with pytest.raises(OSError):
        TrackedProvider(raw, factory, project).design("lost")
    rows = client.get(f"/api/v1/projects/{project}/preparation/model-calls", headers=HEADERS).json()
    row = rows[0]
    before = raw.releases
    post(
        client,
        f"/preparation/model-calls/{row['id']}/abandon",
        {"grant_id": "forged", "snapshot": "forged", "evidence": "Must not call provider without valid grant"},
        operator=True,
        expected=409,
    )
    assert raw.releases == before
    resource_id = row["trace"]["resource_id"]
    with factory() as db:
        lease = db.get(ResourceLease, resource_id)
        owner = lease.owner
        assert lease.status == "RECONCILE_REQUIRED"
    post(
        client,
        f"/comfyops/resource-reservations/{resource_id}/release",
        {"owner": owner, "reconciled": True},
        operator=True,
        expected=409,
    )
    with factory() as db:
        assert db.get(ResourceLease, resource_id).status == "RECONCILE_REQUIRED"


def test_structured_document_analysis_preserves_critical_requirements_at_api(prep):
    import json

    client, _, _ = prep
    project = create_project(client)
    script = {
        "scenes": [
            {
                "id": "s1",
                "location": "Street",
                "shots": [
                    {
                        "id": "shot1",
                        "characters": [{"id": "Lin", "outfit_id": "coat"}],
                        "props": ["Ledger"],
                        "dialogue": [{"speaker_id": "Lin", "text": "你终于来了。"}],
                    }
                ],
            }
        ]
    }
    uploaded = client.post(
        f"/api/v1/projects/{project}/documents",
        files={"file": ("script.json", json.dumps(script, ensure_ascii=False).encode(), "application/json")},
    )
    assert uploaded.status_code == 201, uploaded.text
    plan = post(
        client,
        f"/projects/{project}/preproduction/analyze",
        {
            "document_id": uploaded.json()["id"],
            "requirements": [{"family": "prop", "entity_id": "Ledger", "critical": False}],
        },
    )
    assert plan["payload"]["structured_script"]["dialogue"][0]["text"] == "你终于来了。"
    requirements = plan["payload"]["requirements"]
    assert all(r["critical"] for r in requirements)
    assert next(r for r in requirements if r["family"] == "prop")["shot_ids"] == ["shot1"]
    ready = post(client, f"/projects/{project}/preproduction/readiness")
    assert ready["status"] == "BLOCKED"
    assert any(r["entity_id"] == "coat" and r["depends_on"] for r in ready["requirements"])


def test_model_ledger_keeps_provider_and_result_provenance_separate(prep):
    from shine.comfyops.model_calls import TrackedProvider

    client, factory, _ = prep
    project = create_project(client)

    class SyntheticWireProvider:
        is_mock = False

        def design(self, text):
            return {"is_mock": True, "model": "synthetic-wire-response"}

    assert TrackedProvider(SyntheticWireProvider(), factory, project).design("fixture")["is_mock"]
    with factory() as db:
        row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        assert row.trace["provider_is_mock"] is False
        assert row.trace["result_is_mock"] is True and row.trace["is_mock"] is True

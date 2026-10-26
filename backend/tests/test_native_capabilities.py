"""Isolated API/DB/Mock HTTP contracts only, never real model or human acceptance."""

import hashlib
import json
from dataclasses import asdict, replace

import httpx
import pytest
from sqlalchemy import select

from shine.comfyops.model_calls import TrackedProvider
from shine.comfyops.models import ApprovalGrant, ModelPackage, ResourceLease, WorkflowRevision
from shine.comfyops.native import native_errors
from shine.llm.base import ProviderConfig
from shine.models import AgentTaskRecord, ProviderProfile
from shine.quality.semantic import LocalASRReviewer, LocalVisionReviewer
from tests.conftest import create_project
from tests.test_semantic_qc import context
from tests.test_v13_preparation import HEADERS, approve, package, post, prep  # noqa: F401


@pytest.fixture(params=["ASR", "VLM_REVIEW"])
def native(prep, tmp_path, monkeypatch, request):  # noqa: F811
    client, factory, settings = prep
    category = request.param
    role = "asr_reviewer" if category == "ASR" else "vlm_reviewer"
    project = create_project(client)
    profile = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": "isolated native fixture",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:19001/v1",
            "model": "fixture-only",
        },
    ).json()
    assert (
        client.put("/api/v1/agent-routes", json={"role": role, "provider_profile_id": profile["id"]}).status_code == 200
    )
    files = [
        {
            "relative_path": name,
            "size": 4,
            "sha256": "a" * 64,
            "url": "https://huggingface.co/fixture/resolve/pinned/" + name,
        }
        for name in ["config.json", "model.safetensors", "tokenizer.json"]
    ]
    budget = 4000 if category == "ASR" else 8000
    model = package(client, capabilities=[category], files=files, resource_mb=budget, resource_measurement="ESTIMATED")
    # Fixture state simulates a verified install; no production DB or model is touched.
    with factory.begin() as db:
        db.get(ModelPackage, model["id"]).status = "FILES_VERIFIED_PENDING_RUNTIME"
    descriptors = [{"path": f["relative_path"], "sha256": f["sha256"], "bytes": f["size"]} for f in files]
    identity = dict(
        schema_version="1",
        model_id="fixture-only",
        runtime_sha256="b" * 64,
        files_sha256=hashlib.sha256(
            json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        file_count=3,
        bytes=12,
    )
    binding = dict(
        profile_id=profile["id"],
        kind="openai_compatible",
        model="fixture-only",
        base_url_sha256=hashlib.sha256(profile["base_url"].encode()).hexdigest(),
        executor_identity=identity,
        model_package_id=model["id"],
        runtime_files=[f["relative_path"] for f in files],
    )
    wire_count = [0]
    response_hook = [None]
    health = {
        "status": "ok",
        "is_mock": False,
        "runtime_sha256": identity["runtime_sha256"],
        "verified_models": {
            "fixture-only": {k: v for k, v in identity.items() if k not in {"schema_version", "runtime_sha256"}}
        },
    }

    def respond(req):
        if req.url.path in {"/health", "/identity"}:
            return httpx.Response(200, json=health)
        if req.url.path == "/free":
            return httpx.Response(200, json={"released": True, "allocated_bytes": 0})
        wire_count[0] += 1
        if response_hook[0]:
            response_hook[0]()
        if req.url.path.endswith("/transcriptions"):
            return httpx.Response(
                200,
                json={
                    "text": "你好。",
                    "segments": [],
                    "model": "fixture-only",
                    "is_mock": False,
                    "executor_identity": identity,
                },
            )
        negative = wire_count[0] > 1
        result = dict(
            reviewer="fixture-only",
            is_mock=False,
            input_sha256="a" * 64,
            verdict="failed" if negative else "passed",
            overall_confidence=1,
            findings=[
                dict(
                    code="SCENE_MISMATCH",
                    category="scene",
                    severity="hard",
                    confidence=1,
                    message="Fixture contradiction",
                )
            ]
            if negative
            else [],
        )
        return httpx.Response(
            200,
            json={
                "model": "fixture-only",
                "is_mock": False,
                "executor_identity": identity,
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            },
        )

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    draft = post(
        client,
        "/workflow-capabilities/drafts",
        dict(
            name="Native fixture",
            category=category,
            graph={},
            parameter_map={},
            model_ids=[model["id"]],
            native_provider=binding,
            resource_mb=budget,
        ),
    )
    validated = post(client, f"/workflow-capabilities/{draft['id']}/validate-native", operator=True)
    assert not validated["errors"]
    row = validated["revision"]
    cfg = ProviderConfig(
        profile_id=profile["id"],
        name="fixture-only",
        kind="openai_compatible",
        base_url=profile["base_url"],
        model="fixture-only",
        api_key=None,
    )
    provider = TrackedProvider((LocalASRReviewer if category == "ASR" else LocalVisionReviewer)(cfg), factory, project)
    samples = []
    if category == "ASR":
        audio = tmp_path / "synthetic.wav"
        audio.write_bytes(b"fixture transport only")
        result = provider.transcribe(audio)
        samples.append(dict(result=asdict(result), expected_text="你好。"))
    else:
        ctx = context(tmp_path)
        for expected, beat in [("passed", "Fixture positive"), ("failed", "Fixture contradiction")]:
            result = provider.review(replace(ctx, shot_spec=ctx.shot_spec.model_copy(update={"story_beat": beat})))
            samples.append(dict(result=result.model_dump(), expected_verdict=expected))
    with factory() as db:
        calls = db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.role_id.like("model_call:%"))
            .order_by(AgentTaskRecord.created_at)
        ).all()
        assert len(calls) == len(samples)
        for sample, call in zip(samples, calls, strict=True):
            sample["call_id"] = call.id
    return dict(
        client=client,
        factory=factory,
        settings=settings,
        project=project,
        row=row,
        samples=samples,
        health=health,
        profile=profile,
        category=category,
        wire_count=wire_count,
        wire_identity=identity,
        response_hook=response_hook,
    )


def adopt(native, **extra):
    n = native
    return approve(
        n["client"],
        n["row"],
        "workflow_validate",
        f"/workflow-capabilities/{n['row']['id']}/native-execution-evidence",
        offline=False,
        project_id=n["project"],
        samples=n["samples"],
        **extra,
    )


def test_native_completed_calibration_can_publish_without_comfy_graph_or_new_inference(native):
    count = native["wire_count"][0]
    row = adopt(native)
    assert row["status"] == "REAL_TESTED" and row["sha256"] != native["row"]["sha256"]
    published = approve(
        native["client"], row, "workflow_publish", f"/workflow-capabilities/{row['id']}/publish", offline=False
    )
    assert published["status"] == "PRODUCTION_APPROVED"
    assert native["wire_count"][0] == count
    with native["factory"]() as db:
        assert not native_errors(db, published["payload"], live=True, require_evidence=True)
    legacy = native["client"].post(f"/api/v1/workflow-capabilities/{row['id']}/fake-execute")
    assert legacy.status_code == 409
    assert legacy.json()["detail"] == "NATIVE_PROVIDER_EVIDENCE_REQUIRED"


@pytest.mark.parametrize(
    "damage",
    [
        "call-pending",
        "mock",
        "unreleased",
        "wrong-result",
        "runtime",
        "route-disabled",
        "malformed-url",
        "remote-url",
        "credentials-url",
    ],
)
def test_native_evidence_rejects_uncertainty_mock_changed_runtime_or_result(native, damage):
    n = native
    with n["factory"].begin() as db:
        call = db.get(AgentTaskRecord, n["samples"][0]["call_id"])
        if damage == "call-pending":
            call.status = "NEEDS_REVIEW"
        elif damage == "mock":
            call.trace = {**call.trace, "is_mock": True}
        elif damage == "unreleased":
            db.get(ResourceLease, call.trace["resource_id"]).status = "RECONCILE_REQUIRED"
        elif damage == "route-disabled":
            db.get(ProviderProfile, n["profile"]["id"]).enabled = False
        elif damage in {"malformed-url", "remote-url", "credentials-url"}:
            db.get(ProviderProfile, n["profile"]["id"]).base_url = {
                "malformed-url": "http://[broken/v1",
                "remote-url": "https://example.invalid/v1",
                "credentials-url": "http://user:pass@127.0.0.1:19001/v1",
            }[damage]
    if damage == "wrong-result":
        if n["category"] == "ASR":
            n["samples"][0]["result"]["text"] = "伪造"
            n["samples"][0]["expected_text"] = "伪造"
        else:
            n["samples"][0]["result"]["raw_summary"] = "Altered result not returned by model"
    if damage == "runtime":
        n["health"]["runtime_sha256"] = "c" * 64
    grant = post(
        n["client"],
        "/preparation/approval-grants",
        {"action": "workflow_validate", "target_id": n["row"]["id"]},
        operator=True,
    )
    response = n["client"].post(
        f"/api/v1/workflow-capabilities/{n['row']['id']}/native-execution-evidence",
        json=dict(
            grant_id=grant["grant_id"],
            snapshot=grant["snapshot"],
            offline=False,
            evidence="Isolated fixture adoption",
            project_id=n["project"],
            samples=n["samples"],
        ),
        headers=HEADERS,
    )
    assert response.status_code == 409, response.text
    with n["factory"]() as db:
        assert db.get(WorkflowRevision, n["row"]["id"]).status == "STATIC_VALIDATED"
        assert not db.get(ApprovalGrant, grant["grant_id"]).used


def test_native_published_binding_detects_later_route_and_call_changes(native):
    n = native
    row = adopt(n)
    row = approve(n["client"], row, "workflow_publish", f"/workflow-capabilities/{row['id']}/publish", offline=False)
    before = post(n["client"], "/workflow-capabilities/match", {"category": n["category"], "offline": False})
    assert [match["id"] for match in before["matches"]] == [row["id"]]
    with n["factory"].begin() as db:
        db.get(ProviderProfile, n["profile"]["id"]).model = "different-checkpoint"
    with n["factory"]() as db:
        assert "NATIVE_PROVIDER_BINDING_CHANGED" in native_errors(db, row["payload"], require_evidence=True)
    changed = post(n["client"], "/workflow-capabilities/match", {"category": n["category"], "offline": False})
    assert not changed["matches"]
    assert "NATIVE_PROVIDER_BINDING_CHANGED" in changed["rejected"][0]["reasons"]
    with n["factory"].begin() as db:
        db.get(ProviderProfile, n["profile"]["id"]).model = "fixture-only"
        call = db.get(AgentTaskRecord, n["samples"][0]["call_id"])
        call.trace = {**call.trace, "result_sha256": "c" * 64}
    with n["factory"]() as db:
        assert "NATIVE_EXECUTION_EVIDENCE_CHANGED" in native_errors(db, row["payload"], require_evidence=True)


def test_readiness_checks_live_native_binding_and_rejects_unbound_review_graph(native):
    from shine.comfy.workflow import graph_hash
    from tests.conftest import freeze_style

    n = native
    row = adopt(n)
    approve(n["client"], row, "workflow_publish", f"/workflow-capabilities/{row['id']}/publish", offline=False)
    freeze_style(n["client"], n["project"])
    document = (
        n["client"]
        .post(
            f"/api/v1/projects/{n['project']}/documents",
            files={"file": ("fixture.txt", b"Isolated silent test scene.", "text/plain")},
        )
        .json()
    )
    post(n["client"], f"/projects/{n['project']}/preproduction/analyze", {"document_id": document["id"]})
    other = "ASR" if n["category"] == "VLM_REVIEW" else "VLM_REVIEW"
    # This previously accepted shape does not bind the provider actually used by Worker.
    legacy = dict(
        name="Unbound fixture graph",
        category=other,
        graph={},
        parameter_map={},
        model_ids=[row["payload"]["model_ids"][0]],
    )
    with n["factory"].begin() as db:
        unbound = WorkflowRevision(payload=legacy, sha256=graph_hash(legacy), status="PRODUCTION_APPROVED")
        db.add(unbound)
    before = post(n["client"], f"/projects/{n['project']}/preproduction/readiness", {"offline": False})
    assert any(
        b["code"] == "PRODUCTION_REVIEW_PROVIDER_BINDING_REQUIRED" and b["category"] == other
        for b in before["blockers"]
    )
    assert not any(b.get("workflow") == row["id"] for b in before["blockers"])
    n["health"]["runtime_sha256"] = "d" * 64
    after = post(n["client"], f"/projects/{n['project']}/preproduction/readiness", {"offline": False})
    assert any(
        b["code"] == "NATIVE_EXECUTOR_IDENTITY_CHANGED" and b["workflow"] == row["id"] for b in after["blockers"]
    )


@pytest.mark.parametrize("change", ["h3", "graph", "slots", "files"])
def test_native_draft_cannot_disguise_h3_or_comfy_graph_or_incomplete_files(native, change):
    n = native
    body = dict(n["row"]["payload"])
    body["native_provider"] = dict(body["native_provider"])
    if change == "h3":
        body.update(category="H3_T2VA", model_family="MiniMax-H3")
    elif change == "graph":
        body["graph"] = {"1": {"class_type": "FakeNode", "inputs": {}}}
    elif change == "slots":
        body["reference_slots"] = ["reference_audio_0"]
    else:
        body["native_provider"]["runtime_files"] = ["config.json", "tokenizer.json", "missing.safetensors"]
    response = n["client"].post("/api/v1/workflow-capabilities/drafts", json=body)
    assert response.status_code == 422, response.text


def test_native_budget_cannot_understate_actual_shared_model_admission(native):
    n = native
    body = {**n["row"]["payload"], "resource_mb": 1}
    response = n["client"].post("/api/v1/workflow-capabilities/drafts", json=body)
    assert response.status_code == 409
    assert "NATIVE_RESOURCE_BUDGET_MISMATCH" in response.json()["detail"]["errors"]


@pytest.mark.parametrize(
    "change",
    [
        "stable",
        "before",
        "during-response",
        "during-plan",
        "during-package",
        "during-workflow-payload",
        "during-model-payload",
    ],
)
def test_production_review_checks_frozen_binding_before_and_after_actual_call(native, tmp_path, change):
    from fastapi import HTTPException

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import PreProductionPlan
    from shine.models import Job
    from tests.conftest import create_shot, freeze_style

    n = native
    row = adopt(n)
    row = approve(n["client"], row, "workflow_publish", f"/workflow-capabilities/{row['id']}/publish", offline=False)
    style = freeze_style(n["client"], n["project"])
    shot = create_shot(n["client"], n["project"], style["id"])
    manifest = {"offline": False, "workflows": [{"id": row["id"], "hash": row["sha256"]}]}
    with n["factory"].begin() as db:
        plan = PreProductionPlan(
            project_id=n["project"], status="FROZEN", payload={"manifest": manifest, "offline": False}, sha256="a" * 64
        )
        db.add(plan)
        job = Job(
            project_id=n["project"],
            shot_id=shot["id"],
            shot_revision=1,
            style_version_id=style["id"],
            request={},
            idempotency_key="native-production-fixture",
            provider="comfyui",
            preproduction_manifest_hash=graph_hash(manifest),
        )
        db.add(job)
        db.flush()
        job_id, plan_id = job.id, plan.id
    cfg = ProviderConfig(
        profile_id=n["profile"]["id"],
        name="fixture",
        kind="openai_compatible",
        base_url=n["profile"]["base_url"],
        model="fixture-only",
        api_key=None,
    )
    kind = "ASR" if n["category"] == "ASR" else "VLM"
    provider = (LocalASRReviewer if kind == "ASR" else LocalVisionReviewer)(cfg)
    wrapped = TrackedProvider(provider, n["factory"], n["project"], reviewer_kind=kind, production_job_id=job_id)
    previous = n["wire_count"][0]
    if change == "before":
        n["health"]["runtime_sha256"] = "d" * 64
    elif change == "during-response":
        n["response_hook"][0] = lambda: n["wire_identity"].update(runtime_sha256="d" * 64)
    elif change == "during-plan":

        def mutate_plan():
            with n["factory"].begin() as db:
                plan = db.get(PreProductionPlan, plan_id)
                plan.payload = {**plan.payload, "offline": True}

        n["response_hook"][0] = mutate_plan
    elif change in {"during-package", "during-workflow-payload", "during-model-payload"}:

        def mutate_dependency():
            with n["factory"].begin() as db:
                if change == "during-workflow-payload":
                    workflow = db.get(WorkflowRevision, row["id"])
                    workflow.payload = {**workflow.payload, "name": "Changed without rehash"}
                else:
                    model = db.get(ModelPackage, row["payload"]["model_ids"][0])
                    if change == "during-package":
                        model.status = "UNVERIFIED_ORIGIN"
                    else:
                        model.payload = {**model.payload, "license_id": "unknown"}

        n["response_hook"][0] = mutate_dependency
    audio = tmp_path / "qc-fixture.wav"
    audio.write_bytes(b"Mock HTTP audio only")

    def run():
        return wrapped.transcribe(audio) if kind == "ASR" else wrapped.review(context(tmp_path))

    if change == "stable":
        assert run().is_mock is False
    else:
        with pytest.raises(HTTPException) as caught:
            run()
        assert caught.value.status_code == 409
    assert n["wire_count"][0] == previous + (change != "before")
    with n["factory"]() as db:
        call = db.scalar(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.role_id.like("model_call:%"))
            .order_by(AgentTaskRecord.created_at.desc())
            .limit(1)
        )
        assert call.status == ("COMPLETED" if change == "stable" else "FAILED")
        if change != "before":
            assert call.trace["production_review"]["job_id"] == job_id
            assert len(call.trace["received_result_sha256"]) == 64
        else:
            assert not call.trace.get("submitted")
        assert db.get(ResourceLease, call.trace["resource_id"]).status == "RELEASED"

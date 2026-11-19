"""Direction 4 regressions: cumulative budget and goal-aware success.

Counterexamples from the 2026-11-19 closeout audit:

* ``settle`` compared ONE step's actual cost against the WHOLE cumulative
  usage and overwrote it: a cheap second step erased an expensive first one;
* a replayed settlement refunded twice;
* "one satisfied sub-action + model finish" was reported as SUCCEEDED even
  when the objective asked for a real sample and no attempt/artifact existed;
* registration success never checked whether production actually SELECTED the
  registered profile (GRANT-06).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from shine.copilot import objective_verify
from shine.copilot import task_grant as grant_contract
from shine.models import Job, MediaArtifact, WorkflowProfile, new_id, utcnow
from shine.support import grants as grant_service
from shine.support.models import SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}


def _session_row(factory, *, project_id=None):
    with factory() as db:
        row = SupportSession(
            id=new_id(), project_id=project_id, page_id="copilot", status="OPEN",
            actor="local-user", context_json={}, expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()
        return row


def _grant(factory, session, *, objective="下载缺失权重", limits=None):
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user", objective=objective,
            groups=["inspect", "resources"], limits=limits,
            project_ids=[session.project_id] if session.project_id else [],
        )
        return loaded


def test_two_consecutive_steps_accumulate_and_never_overwrite(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, limits={"max_download_bytes": 10_000})
    with factory() as db:
        grant_service.reserve(db, loaded.id, capability_id="resource_install_plan",
                              cost={"max_download_bytes": 600})
        grant_service.settle(db, loaded.id, reserved={"max_download_bytes": 600},
                             actual={"max_download_bytes": 100}, step_key="step-1")
        assert grant_contract.usage_of(grant_service.load(db, loaded.id).grant)["max_download_bytes"] == 100
        grant_service.reserve(db, loaded.id, capability_id="resource_install_plan",
                              cost={"max_download_bytes": 500})
        # THE counterexample: the old settle wrote 40 here, erasing step 1.
        grant_service.settle(db, loaded.id, reserved={"max_download_bytes": 500},
                             actual={"max_download_bytes": 40}, step_key="step-2")
        usage = grant_contract.usage_of(grant_service.load(db, loaded.id).grant)
        assert usage["max_download_bytes"] == 140


def test_a_replayed_settlement_does_not_refund_twice(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, limits={"max_download_bytes": 10_000})
    with factory() as db:
        grant_service.reserve(db, loaded.id, capability_id="resource_install_plan",
                              cost={"max_download_bytes": 600})
        for _ in range(3):
            grant_service.settle(db, loaded.id, reserved={"max_download_bytes": 600},
                                 actual={"max_download_bytes": 100}, step_key="step-1")
        assert grant_contract.usage_of(grant_service.load(db, loaded.id).grant)["max_download_bytes"] == 100


def test_concurrent_reservations_cannot_both_spend_the_last_budget(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, limits={"max_download_bytes": 1_000})

    def attempt(_):
        with factory() as db:
            try:
                grant_service.reserve(db, loaded.id, capability_id="resource_install_plan",
                                      cost={"max_download_bytes": 600})
                return "ok"
            except grant_contract.GrantError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ["GRANT_BUDGET_EXCEEDED", "ok"]
    with factory() as db:
        assert grant_contract.usage_of(grant_service.load(db, loaded.id).grant)["max_download_bytes"] == 600


def test_revocation_stops_new_budget_reservations(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, limits={"max_download_bytes": 10_000})
    with factory() as db:
        grant_service.revoke(db, loaded.id, actor="local-user")
        with pytest.raises(grant_contract.GrantError) as exc:
            grant_service.reserve(db, loaded.id, capability_id="resource_install_plan",
                                  cost={"max_download_bytes": 10})
        assert exc.value.code == "GRANT_REVOKED"


# ---------------------------------------------------------------------------
# Goal-aware success verification
# ---------------------------------------------------------------------------


def _profile_row(factory, project_id, *, validated=True):
    with factory() as db:
        profile = WorkflowProfile(
            id=new_id(), project_id=project_id, name="验收工作流", mode="Ref2VA",
            graph={"1": {"class_type": "X", "inputs": {}}},
            parameter_map={"positive_prompt": "1.inputs.text"},
            graph_sha256="f" * 64,
            validated_against=(
                {"missing_nodes": [], "missing_inputs": [], "checked_at": utcnow().isoformat()}
                if validated else None
            ),
        )
        db.add(profile)
        db.commit()
        return profile.id


def _ready_summary(monkeypatch, *, files_verified=True):
    import shine.comfyops.setup_summary as setup_summary

    def fake_summary(db, settings, *, project_id=None, variant_id=None, discovered=None):
        return {
            "verdict": {"mode": "READY", "label": "真实制作已就绪", "code": None},
            "capabilities": {
                "h3": {"optional": False, "state": "READY",
                       "facts": {"FILES_VERIFIED": files_verified, "PRODUCTION_READY": True}},
            },
        }

    monkeypatch.setattr(setup_summary, "summary", fake_summary)


def test_registration_objective_is_satisfied_by_registration_and_node_contract(studio, monkeypatch):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "注册验收")
    _profile_row(factory, project_id)
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, objective="装配并注册用户级工作流")
    _ready_summary(monkeypatch)
    steps = [{"capability_id": "workflow_graph_register", "satisfied": True,
              "outcome": "VERIFIED", "action_id": None}]
    # The step's profile is discovered through the project's own ledger.
    with factory() as db:
        result = objective_verify.verify(db, settings, grant=loaded.grant, steps=steps)
    assert result["required"] == ["graph_registered", "nodes_loadable"]
    assert result["results"]["graph_registered"]["status"] == "VERIFIED"
    assert result["results"]["nodes_loadable"]["status"] == "VERIFIED"
    assert result["satisfied"] is True


def test_sample_objective_without_a_real_attempt_is_not_success(studio, monkeypatch):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "小样验收")
    _profile_row(factory, project_id)
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, objective="装配工作流并跑真实小样验收")
    _ready_summary(monkeypatch)
    steps = [{"capability_id": "workflow_graph_register", "satisfied": True,
              "outcome": "VERIFIED", "action_id": None}]
    with factory() as db:
        result = objective_verify.verify(db, settings, grant=loaded.grant, steps=steps)
    assert result["satisfied"] is False
    assert "production_selected" in result["missing"]
    assert "sample_completed" in result["missing"]
    assert result["results"]["graph_registered"]["status"] == "VERIFIED"


def test_sample_objective_reads_the_profile_job_attempt_artifact_chain(studio, monkeypatch):
    client, factory, _, settings = studio
    from shine.models import Shot, StyleVersion
    from tests.conftest import create_project

    project_id = create_project(client, "真实小样")
    profile_id = _profile_row(factory, project_id)
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, objective="装配工作流并跑真实小样验收")
    _ready_summary(monkeypatch)
    with factory() as db:
        # A real (non-mock) PASSED job that carries the registered profile and
        # a real artifact: the exact chain GRANT-06 requires.
        shot = Shot(id=new_id(), project_id=project_id, name="镜1", revision=1,
                    spec={"duration_s": 5})
        db.add(shot)
        db.flush()
        style = StyleVersion(id=new_id(), project_id=project_id, version=1,
                             snapshot={}, sha256="e" * 64, approved_by="local-user")
        db.add(style)
        db.flush()
        artifact = MediaArtifact(
            id=new_id(), project_id=project_id, job_id=None, attempt_id=None,
            artifact_kind="video", sha256="d" * 64, byte_size=1000,
            content_type="video/mp4", is_mock=False, storage_path="artifacts/x.mp4",
        )
        db.add(artifact)
        db.flush()
        job = Job(
            id=new_id(), project_id=project_id, shot_id=shot.id, shot_revision=1,
            style_version_id=style.id, request={}, idempotency_key=new_id(),
            status="PASSED", provider="comfyui", workflow_profile_id=profile_id,
            artifact_id=artifact.id,
        )
        db.add(job)
        db.commit()
        result = objective_verify.verify(db, settings, grant=loaded.grant, steps=[
            {"capability_id": "workflow_graph_register", "satisfied": True,
             "outcome": "VERIFIED", "action_id": None},
        ])
    assert result["results"]["production_selected"]["status"] == "VERIFIED"
    assert result["results"]["sample_completed"]["status"] == "VERIFIED"
    assert result["results"]["sample_completed"]["artifact_sha256"] == "d" * 64
    assert result["satisfied"] is True


def test_a_mock_artifact_never_satisfies_a_real_sample(studio, monkeypatch):
    client, factory, _, settings = studio
    from shine.models import Shot, StyleVersion
    from tests.conftest import create_project

    project_id = create_project(client, "替身小样")
    profile_id = _profile_row(factory, project_id)
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, objective="装配工作流并跑真实小样验收")
    _ready_summary(monkeypatch)
    with factory() as db:
        shot = Shot(id=new_id(), project_id=project_id, name="镜1", revision=1,
                    spec={"duration_s": 5})
        db.add(shot)
        db.flush()
        style = StyleVersion(id=new_id(), project_id=project_id, version=1,
                             snapshot={}, sha256="e" * 64, approved_by="local-user")
        db.add(style)
        db.flush()
        artifact = MediaArtifact(
            id=new_id(), project_id=project_id, artifact_kind="video",
            sha256="c" * 64, byte_size=10, content_type="video/mp4",
            is_mock=True, storage_path="artifacts/mock.mp4",
        )
        db.add(artifact)
        db.flush()
        job = Job(
            id=new_id(), project_id=project_id, shot_id=shot.id, shot_revision=1,
            style_version_id=style.id, request={}, idempotency_key=new_id(),
            status="PASSED", provider="comfyui", workflow_profile_id=profile_id,
            artifact_id=artifact.id,
        )
        db.add(job)
        db.commit()
        result = objective_verify.verify(db, settings, grant=loaded.grant, steps=[
            {"capability_id": "workflow_graph_register", "satisfied": True,
             "outcome": "VERIFIED", "action_id": None},
        ])
    assert result["results"]["sample_completed"]["status"] == "NOT_RUN"
    assert result["satisfied"] is False


def test_the_loop_pauses_when_the_objective_needs_a_sample_that_never_ran(studio):
    """End to end: register succeeds, the model finishes, the server refuses to
    call a registration-only result a completed real-sample objective."""
    from types import SimpleNamespace

    from shine.copilot import tool_loop, tool_registry
    from shine.llm.tool_calls import ToolCall, ToolTurn

    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "目标核验")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="装配工作流并跑真实小样验收",
            groups=["inspect", "workflows", "test"],
            project_ids=[project_id],
        )
        grant_id = loaded.id
        tools = tool_registry.grant_tool_specs(loaded.grant)

    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
        "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    }
    object_info = {
        "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}},
        "EmptyLatentImage": {"input": {"required": {
            "width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}],
            "batch_size": ["INT", {"default": 1}]}}},
    }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def object_info(self):
            return object_info

    import shine.api_comfy as api_comfy

    original = api_comfy.ComfyUIClient
    api_comfy.ComfyUIClient = FakeClient

    class Gateway:
        def __init__(self):
            self.turns = 0
            self.pins = {}

        def profile_for_role_or_inherit(self, db, role):
            return SimpleNamespace(id="p", name="P", kind="mock", base_url="", model="m"), role

        def pin_role(self, role, profile):
            if profile is None:
                self.pins.pop(role, None)
            else:
                self.pins[role] = profile

        def complete_tools_for_role(self, db, role, **kwargs):
            self.turns += 1
            if self.turns == 1:
                return ToolTurn(text="", tool_calls=[ToolCall(
                    id="c1", name="workflow_graph_register",
                    arguments={"name": "目标核验工作流", "mode": "Ref2VA", "graph": graph,
                               "parameter_map": {"positive_prompt": "1.inputs.text",
                                                 "duration": "3.inputs.width"},
                               "required_nodes": ["CLIPTextEncode"]},
                )], native=True)
            return ToolTurn(text="注册完成，目标达成", tool_calls=[], native=False)

        def complete_for_role(self, db, role, **kwargs):
            return SimpleNamespace(text="", parsed={"action": "finish", "final": "完成"},
                                   model="m", latency_ms=1, usage={})

    try:
        with factory() as db:
            result = tool_loop.run(
                db, settings, grant_id=grant_id,
                objective="装配工作流并跑真实小样验收",
                tools=tools, session_id=session.id, gateway=Gateway(),
            )
    finally:
        api_comfy.ComfyUIClient = original
    assert result.status == "PAUSED_USER_DECISION", result.public()
    assert result.stop_reason == "OBJECTIVE_NOT_VERIFIED"
    assert "sample_completed" in result.verification["missing"]
    with factory() as db:
        row = grant_service.find_by_id(db, grant_id)
        persisted = (row.payload or {}).get("objective_verification")
        assert persisted and persisted["satisfied"] is False

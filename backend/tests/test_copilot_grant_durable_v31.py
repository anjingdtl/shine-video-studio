"""Direction 3 regressions: one authorization, one durable execution chain.

Counterexamples from the 2026-11-19 closeout audit:

* the loop lived only inside one HTTP request: a closed page or an API/Worker
  restart lost the transcript, and nothing drove the grant forward afterwards;
* a repeated click could start a second driver beside the first;
* a delegated original task (setup_repair / production_start) was not waited on
  by readback — the loop had no WAITING_EXECUTOR path at all;
* the model menu had no read-only investigation, so every look cost a write;
* several ComfyUI installations were frozen into the grant without the person
  ever selecting one (empty installation scope = no scope check at all).
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from shine.copilot import task_grant as grant_contract
from shine.copilot import tool_loop, tool_registry
from shine.llm.tool_calls import FALLBACK_SCHEMA, ToolCall, ToolTurn
from shine.models import new_id, utcnow
from shine.support import grants as grant_service
from shine.support.models import SupportAction, SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}


class ScriptedGateway:
    def __init__(self, turns, *, native=True, fallback=None):
        self.turns = list(turns)
        self.native = native
        self.fallback = fallback or {"action": "finish", "final": "结束"}
        self.tool_turns = 0
        self.fallback_calls = 0
        self.pins = {}

    def profile_for_role(self, db, role):
        return SimpleNamespace(id="scripted", name="Scripted", kind="mock",
                               base_url="", model="scripted")

    def profile_for_role_or_inherit(self, db, role):
        return self.profile_for_role(db, role), role

    def pin_role(self, role, profile):
        if profile is None:
            self.pins.pop(role, None)
        else:
            self.pins[role] = profile

    def clear_pins(self):
        self.pins.clear()

    def complete_tools_for_role(self, db, role, **kwargs):
        if not self.native:
            return None
        if self.tool_turns >= len(self.turns):
            return ToolTurn(text="没有更多步骤", tool_calls=[], native=False)
        turn = self.turns[self.tool_turns]
        self.tool_turns += 1
        return turn

    def complete_for_role(self, db, role, **kwargs):
        self.fallback_calls += 1
        assert kwargs.get("schema") is FALLBACK_SCHEMA
        return SimpleNamespace(text="", parsed=dict(self.fallback),
                               model="scripted", latency_ms=1, usage={})


def _session_row(factory, *, project_id=None):
    with factory() as db:
        row = SupportSession(
            id=new_id(), project_id=project_id, page_id="copilot", status="OPEN",
            actor="local-user", context_json={}, expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()
        return row


def _grant(factory, session, *, groups=("inspect", "workflows"), objective="装配并验证用户级工作流"):
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user", objective=objective,
            groups=list(groups),
            project_ids=[session.project_id] if session.project_id else [],
        )
        return loaded


SIMPLE_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
}
SIMPLE_PARAMETER_MAP = {"positive_prompt": "1.inputs.text", "duration": "3.inputs.width"}
SIMPLE_OBJECT_INFO = {
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}},
    "EmptyLatentImage": {"input": {"required": {
        "width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}],
        "batch_size": ["INT", {"default": 1}]}}},
}


@pytest.fixture
def live_object_info(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def object_info(self):
            return SIMPLE_OBJECT_INFO

    import shine.api_comfy as api_comfy

    monkeypatch.setattr(api_comfy, "ComfyUIClient", FakeClient)
    return SIMPLE_OBJECT_INFO


def _register_turn(name=" durable-workflow"):
    return ToolTurn(
        text="",
        tool_calls=[ToolCall(id="call-1", name="workflow_graph_register", arguments={
            "name": name, "mode": "Ref2VA", "graph": SIMPLE_GRAPH,
            "parameter_map": SIMPLE_PARAMETER_MAP, "required_nodes": ["CLIPTextEncode"],
        })],
        native=True,
    )


def test_a_closed_page_resumes_the_same_transcript_and_never_repeats_a_step(studio, live_object_info):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "持久授权")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session)
    # Burst 1: one step only (the Worker's per-tick shape).
    first = ScriptedGateway([_register_turn()])
    with factory() as db:
        result1 = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=first, max_steps=1, owner="worker:w1",
        )
    assert result1.stop_reason == "STEP_BUDGET_EXHAUSTED"
    assert len(result1.steps) == 1
    assert result1.steps[0]["satisfied"] is True
    with factory() as db:
        state = grant_service.loop_state(grant_service.load(db, loaded.id).grant)
        assert state.get("messages"), "the transcript must survive the burst"
        assert len(state.get("steps") or []) == 1
        assert state.get("started_at")
    # Burst 2: a NEW driver (restart) finishes from the persisted state. The
    # model only says finish; the registered step is NOT executed again.
    second = ScriptedGateway([ToolTurn(text="已完成", tool_calls=[], native=False)])
    with factory() as db:
        result2 = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=second, max_steps=4, owner="worker:w2",
        )
    assert result2.status == "SUCCEEDED", result2.public()
    assert len(result2.steps) == 1, "the persisted step is reused, not re-executed"
    with factory() as db:
        actions = db.query(SupportAction).filter(SupportAction.grant_id == loaded.id).all()
        assert len(actions) == 1


def test_a_second_driver_reconnects_instead_of_forking(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    with factory() as db:
        assert grant_service.claim_loop(db, loaded.id, owner="worker:w1") is True
        # A live lease held by another owner: the click reconnects.
        assert grant_service.claim_loop(db, loaded.id, owner="api:2") is False
        gateway = ScriptedGateway([_register_turn()])
        result = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=gateway, max_steps=4, owner="api:2",
        )
    assert result.stop_reason == "LOOP_ALREADY_RUNNING"
    assert gateway.tool_turns == 0, "no model turn may run beside the live driver"


def test_an_expired_lease_is_claimable_again(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    with factory() as db:
        assert grant_service.claim_loop(db, loaded.id, owner="worker:dead", lease_seconds=-1)
        assert grant_service.claim_loop(db, loaded.id, owner="worker:w2") is True


def test_read_tools_investigate_without_creating_actions(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect", "workflows"))
    turn = ToolTurn(text="", tool_calls=[
        ToolCall(id="r1", name="inspect_environment", arguments={})
    ], native=True)
    gateway = ScriptedGateway([turn, ToolTurn(text="看过了", tool_calls=[], native=False)])
    with factory() as db:
        result = tool_loop.run(
            db, settings, grant_id=loaded.id, objective="只读检查",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=gateway,
        )
    assert result.stop_reason == "NO_AUTHORITATIVE_EVIDENCE"  # a read is not a write receipt
    with factory() as db:
        actions = db.query(SupportAction).filter(SupportAction.grant_id == loaded.id).all()
        assert actions == [], "a read-only tool never creates a SupportAction"
        grant = grant_service.load(db, loaded.id).grant
        calls = grant.get("tool_calls") or []
        assert any(item.get("capability_id") == "inspect_environment" for item in calls)
        assert grant_contract.usage_of(grant)["max_llm_tool_calls"] >= 1


def test_a_delegated_original_task_is_waited_on_by_readback(studio, monkeypatch):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect", "resources", "runtime"),
                    objective="准备本机环境")

    # The original executor is the real delegation path; only its durable task
    # id is fixed here so the test exercises the LOOP's wait/readback contract.
    def fake_enqueue(db, **kwargs):
        return {"status": "QUEUED", "task_id": "setup-durable-1", "steps": [{"id": "HOST"}]}

    monkeypatch.setattr("shine.comfyops.setup_preparation.enqueue", fake_enqueue)
    # The write step delegates to a durable original task.
    turn = ToolTurn(text="", tool_calls=[
        ToolCall(id="s1", name="setup_repair",
                 arguments={"capabilities": ["h3"], "verify_runtime": False})
    ], native=True)
    gateway = ScriptedGateway([turn])
    with factory() as db:
        result = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=gateway, max_steps=4, owner="worker:w1",
        )
    assert result.status == "WAITING_EXECUTOR", result.public()
    assert result.stop_reason == "ORIGINAL_TASK_PENDING"
    with factory() as db:
        grant = grant_service.load(db, loaded.id).grant
        assert grant_contract.grant_status(grant, now=utcnow()) == "WAITING_EXECUTOR"
        state = grant_service.loop_state(grant)
        pending = state.get("pending_action_id")
        assert pending
        action = db.get(SupportAction, pending)
        assert action.original_task_id, "the action must bind the executor's own task id"

    # While the original task is pending, the next burst reads back and yields
    # WITHOUT buying a model turn.
    reads = {"n": 0}

    def fake_read(db, settings, *, action_id, grant_id):
        reads["n"] += 1
        return {
            "status": "DELEGATED", "action_id": action_id,
            "original_task_id": "task-1",
            "observed_facts": {"verification": {"outcome": "PENDING", "task_status": "RUNNING"}},
            "authority": "/api/v1/setup/verdict", "error_code": None, "message": "",
        }

    import shine.support.actions as action_service

    monkeypatch.setattr(action_service, "read_under_grant", fake_read)
    idle_gateway = ScriptedGateway([ToolTurn(text="不该被调用", tool_calls=[], native=False)])
    with factory() as db:
        waiting = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=idle_gateway, max_steps=4, owner="worker:w1",
        )
    assert waiting.status == "WAITING_EXECUTOR"
    assert reads["n"] == 1
    assert idle_gateway.tool_turns == 0 and idle_gateway.fallback_calls == 0

    # Once the original task really finished, the loop resumes and finishes.
    def fake_read_done(db, settings, *, action_id, grant_id):
        return {
            "status": "SUCCEEDED", "action_id": action_id,
            "original_task_id": "task-1",
            "observed_facts": {"verification": {"outcome": "VERIFIED"}},
            "authority": "/api/v1/setup/verdict", "error_code": None, "message": "",
        }

    monkeypatch.setattr(action_service, "read_under_grant", fake_read_done)
    # The repair really made the machine ready: the single verdict now says so.
    import shine.comfyops.setup_summary as setup_summary

    def ready_summary(db, settings, *, project_id=None, variant_id=None, discovered=None):
        return {
            "verdict": {"mode": "READY", "label": "真实制作已就绪", "code": None},
            "capabilities": {"h3": {"optional": False, "state": "READY",
                                    "facts": {"FILES_VERIFIED": True, "PRODUCTION_READY": True}}},
        }

    monkeypatch.setattr(setup_summary, "summary", ready_summary)
    finish_gateway = ScriptedGateway([ToolTurn(text="环境已就绪", tool_calls=[], native=False)])
    with factory() as db:
        done = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=finish_gateway, max_steps=4, owner="worker:w1",
        )
    assert done.status == "SUCCEEDED", done.public()
    assert any(step.get("capability_id") == "read_original_task" for step in done.steps)


def test_ambiguous_installations_must_be_selected_once_before_authorizing(studio, monkeypatch):
    client, factory, _, settings = studio
    # A real installation has a configured text model the copilot inherits.
    created_profile = client.post("/api/v1/provider-profiles", json={
        "name": "grant-text", "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:9/v1", "model": "local-text", "enabled": True,
    }, headers=HEADERS)
    assert created_profile.status_code == 201, created_profile.text
    routed = client.put("/api/v1/agent-routes", json={
        "role": "comfyops", "provider_profile_id": created_profile.json()["id"],
    }, headers=HEADERS)
    assert routed.status_code == 200, routed.text
    created = client.post("/api/v1/support/sessions",
                          json={"page_id": "setup", "question": ""}, headers=HEADERS)
    session_id = created.json()["session"]["id"]
    candidates = [
        {"instance_id": "inst-a", "root": "D:/ComfyUI-A", "managed": True, "adopted": False},
        {"instance_id": "inst-b", "root": "E:/ComfyUI-B", "managed": False, "adopted": True},
    ]

    import shine.comfyops.setup_summary as setup_summary

    def fake_resolve(db, settings, *, path=None, discovered=None):
        return {"selected": {}, "choice_required": True, "candidates": candidates}

    monkeypatch.setattr(setup_summary, "resolve_installation", fake_resolve)
    preview = client.post("/api/v1/support/task-grants/preview", json={
        "session_id": session_id, "objective": "准备本机 H3 环境",
    }, headers=HEADERS)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["can_authorize"] is False
    assert body["selection_required"] == ["INSTALLATION_SELECTION_REQUIRED"]
    assert len(body["installation"]["candidates"]) == 2
    refused = client.post("/api/v1/support/task-grants/authorize", json={
        "session_id": session_id, "preview_id": body["preview_id"],
        "grant_digest": body["grant_digest"], "confirm": True, "run": False,
    }, headers=HEADERS)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "INSTALLATION_SELECTION_REQUIRED"
    # Selecting ONE installation re-previews and freezes it into the scope.
    picked = client.post("/api/v1/support/task-grants/preview", json={
        "session_id": session_id, "objective": "准备本机 H3 环境",
        "installation_id": "inst-a",
    }, headers=HEADERS)
    assert picked.status_code == 200, picked.text
    assert picked.json()["can_authorize"] is True
    authorized = client.post("/api/v1/support/task-grants/authorize", json={
        "session_id": session_id, "preview_id": picked.json()["preview_id"],
        "grant_digest": picked.json()["grant_digest"], "confirm": True, "run": False,
    }, headers=HEADERS)
    assert authorized.status_code == 200, authorized.text
    grant_id = authorized.json()["grant_id"]
    with factory() as db:
        grant = grant_service.load(db, grant_id).grant
        assert grant["scope"]["installation_ids"] == ["inst-a"]


def test_a_terminal_needs_review_readback_consumes_pending_and_resume_gets_a_model_turn(studio, monkeypatch):
    """A NEEDS_REVIEW readback must not deadlock the grant: pending is consumed,
    the receipt stays in the transcript, and the next resume gives the model a
    turn to act on the named next step."""
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect", "resources", "runtime"),
                    objective="准备本机环境")

    def fake_enqueue(db, **kwargs):
        return {"status": "QUEUED", "task_id": "setup-nr-1", "steps": [{"id": "HOST"}]}

    monkeypatch.setattr("shine.comfyops.setup_preparation.enqueue", fake_enqueue)
    turn = ToolTurn(text="", tool_calls=[
        ToolCall(id="s1", name="setup_repair",
                 arguments={"capabilities": ["h3"], "verify_runtime": False})
    ], native=True)
    with factory() as db:
        first = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=ScriptedGateway([turn]),
            max_steps=4, owner="worker:w1",
        )
    assert first.status == "WAITING_EXECUTOR"
    import shine.support.actions as action_service

    def fake_read_needs_review(db, settings, *, action_id, grant_id):
        return {
            "status": "NEEDS_REVIEW", "action_id": action_id,
            "original_task_id": "setup-nr-1",
            "observed_facts": {"verification": {
                "outcome": "NOT_VERIFIED",
                "targets": {"h3": {"state": "BOUND", "next_step": "WORKFLOW_NOT_VALIDATED"}},
            }},
            "authority": "/api/v1/setup/verdict",
            "error_code": None, "message": "原执行器已完成，但本次恢复目标的权威能力裁决仍未就绪。",
        }

    monkeypatch.setattr(action_service, "read_under_grant", fake_read_needs_review)
    with factory() as db:
        paused = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=ScriptedGateway([]),
            max_steps=4, owner="worker:w1",
        )
    assert paused.status == "PAUSED_USER_DECISION"
    with factory() as db:
        state = grant_service.loop_state(grant_service.load(db, loaded.id).grant)
        assert state.get("pending_action_id") is None, "pending must be consumed"
        assert any("WORKFLOW_NOT_VALIDATED" in str(m.get("content") or "")
                   for m in state.get("messages") or [])
    # The resume gives the model a real turn (it can act on the named step).
    resumed_gateway = ScriptedGateway([
        ToolTurn(text="", tool_calls=[ToolCall(id="r2", name="inspect_environment", arguments={})],
                 native=True),
        ToolTurn(text="结束", tool_calls=[], native=False),
    ])
    with factory() as db:
        grant_service.set_status(db, loaded.id, "RUNNING")
        resumed = tool_loop.advance(
            db, settings, grant_id=loaded.id, objective="准备本机环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=resumed_gateway,
            max_steps=4, owner="worker:w1",
        )
    assert resumed_gateway.tool_turns >= 1, "the resumed model must get a turn"
    assert resumed.stop_reason != "GRANT_NOT_RUNNING"


def test_narrated_finish_after_a_successful_read_gets_a_corrective_continuation(studio):
    """MiniMax sometimes narrates a tool call in prose instead of emitting it.
    After successful reads the loop corrects once (bounded), not pauses mid-
    investigation; repeated narration still ends in the named pause."""
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect", "workflows"))
    read_turn = ToolTurn(text="", tool_calls=[
        ToolCall(id="r1", name="inspect_environment", arguments={})
    ], native=True)
    narrated = ToolTurn(text="调用 workflow_graph_register，参数 {...}", tool_calls=[], native=False)
    gateway = ScriptedGateway([read_turn, narrated, narrated])
    with factory() as db:
        result = tool_loop.run(
            db, settings, grant_id=loaded.id, objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id, gateway=gateway,
        )
    assert result.stop_reason == "NO_AUTHORITATIVE_EVIDENCE"
    assert gateway.tool_turns >= 3, "the loop must continue past a narrated finish"


def test_an_explicit_click_resumes_a_user_decision_pause_but_the_worker_does_not(studio, monkeypatch):
    client, factory, _, settings = studio
    created = client.post("/api/v1/support/sessions",
                          json={"page_id": "setup", "question": ""}, headers=HEADERS)
    session_id = created.json()["session"]["id"]
    preview = client.post("/api/v1/support/task-grants/preview", json={
        "session_id": session_id, "objective": "检查本机环境",
        "capability_groups": ["inspect"],
    }, headers=HEADERS).json()
    authorized = client.post("/api/v1/support/task-grants/authorize", json={
        "session_id": session_id, "preview_id": preview["preview_id"],
        "grant_digest": preview["grant_digest"], "confirm": True, "run": False,
    }, headers=HEADERS).json()
    grant_id = authorized["grant_id"]
    with factory() as db:
        grant_service.set_status(db, grant_id, "PAUSED_USER_DECISION", reason="NO_AUTHORITATIVE_EVIDENCE")
    import shine.worker as worker

    monkeypatch.setattr(worker, "_text_gateway", lambda s: ScriptedGateway([]))
    # The persistent driver never auto-resumes a pause that asked for a person.
    assert worker._advance_task_grants(factory, settings, "w1") is None
    # The explicit click IS the decision: the loop runs again.
    resumed = client.post(f"/api/v1/support/task-grants/{grant_id}/run",
                          json={"session_id": session_id, "max_steps": 2}, headers=HEADERS)
    assert resumed.status_code == 200, resumed.text
    # The loop really ran after the resume (any model-stage outcome proves the
    # transition; GRANT_NOT_RUNNING would mean the pause was never lifted).
    assert resumed.json()["stop_reason"] in {
        "NO_AUTHORITATIVE_EVIDENCE", "LLM_NOT_CONFIGURED", "LLM_UNAVAILABLE",
    }
    with factory() as db:
        from shine.models import AuditEvent

        event = db.query(AuditEvent).filter(
            AuditEvent.event_type == "SUPPORT_TASK_GRANT_RESUMED",
            AuditEvent.entity_id == grant_service.find_by_id(db, grant_id).id,
        ).first()
        assert event is not None
    # A budget pause is NOT resumable by a click.
    with factory() as db:
        grant_service.set_status(db, grant_id, "PAUSED_BUDGET", reason="GRANT_TOOL_CALL_BUDGET_EXHAUSTED")
    refused = client.post(f"/api/v1/support/task-grants/{grant_id}/run",
                          json={"session_id": session_id, "max_steps": 2}, headers=HEADERS)
    assert refused.json()["stop_reason"] == "GRANT_NOT_RUNNING"


def test_state_summary_exposes_the_real_scope_ids():
    """A model that cannot see the frozen scope invents project slugs and is
    refused by SCOPE_EXCEEDED; the state summary must carry the real ids."""
    from shine.copilot.tool_loop import _state_summary

    grant = {
        "scope": {"project_ids": ["p-real-uuid"], "installation_ids": ["i-real-uuid"]},
        "permissions": {"read_project": True}, "limits": {}, "usage": {},
    }
    state = _state_summary(objective="x", grant=grant, steps=[])
    assert state["scope"]["project_ids"] == ["p-real-uuid"]
    assert state["scope"]["installation_ids"] == ["i-real-uuid"]


def test_production_start_is_offered_only_through_the_test_group(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    with_test = _grant(factory, session, groups=("inspect", "test"))
    without_test = _grant(factory, session, groups=("inspect", "workflows"),
                          objective="另一个目标")
    names_with = {spec.name for spec in tool_registry.grant_tool_specs(with_test.grant)}
    names_without = {spec.name for spec in tool_registry.grant_tool_specs(without_test.grant)}
    assert "production_start" in names_with
    assert "production_start" not in names_without
    assert grant_contract.CAPABILITY_PERMISSIONS["production_start"] == "run_test_inference"


def test_worker_tick_drives_one_grant_step_and_a_yield_is_not_a_pause(studio, monkeypatch):
    _, factory, _, settings = studio
    import shine.worker as worker

    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect", "workflows"))
    gateway = ScriptedGateway([
        ToolTurn(text="", tool_calls=[ToolCall(id="r1", name="inspect_environment", arguments={})],
                 native=True),
        ToolTurn(text="结束", tool_calls=[], native=False),
    ])
    monkeypatch.setattr(worker, "_text_gateway", lambda settings: gateway)
    with factory() as db:
        grant_service.set_status(db, loaded.id, "RUNNING")
    step1 = worker._advance_task_grants(factory, settings, "w1")
    assert step1 == f"grant:{loaded.id}:waiting", "a step-budget yield keeps the task live"
    with factory() as db:
        grant = grant_service.load(db, loaded.id).grant
        assert grant_contract.grant_status(grant, now=utcnow()) == "RUNNING"
    # The read produced no write receipt. The model's first narrated finish
    # gets a bounded corrective continuation (still a yield, not a pause);
    # repeated narration ends in the REAL terminal pause for a person.
    step2 = worker._advance_task_grants(factory, settings, "w1")
    assert step2 == f"grant:{loaded.id}:waiting"
    step3 = worker._advance_task_grants(factory, settings, "w1")
    assert step3 == f"grant:{loaded.id}:waiting"
    step4 = worker._advance_task_grants(factory, settings, "w1")
    assert step4 == f"grant:{loaded.id}:PAUSED_USER_DECISION"
    with factory() as db:
        grant = grant_service.load(db, loaded.id).grant
        assert grant_contract.grant_status(grant, now=utcnow()) == "PAUSED_USER_DECISION"

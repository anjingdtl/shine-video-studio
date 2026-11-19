"""V3.1 model protocol: native tool calling, validated fallback, bounded loop.

The loop tests use a scripted gateway: the *model* is scripted, everything else
(grants, capability contracts, the original preview/approve chain, the original
executor) is real.  That is the point - a model proposal can be arbitrary, and
the server must still be the thing that decides.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from shine.copilot import task_grant as grant_contract
from shine.copilot import tool_loop, tool_registry
from shine.llm.tool_calls import (
    FALLBACK_SCHEMA,
    ToolCall,
    ToolTurn,
    parse_anthropic_turn,
    parse_fallback_reply,
    parse_openai_turn,
)
from shine.models import new_id, utcnow
from shine.support import grants as grant_service
from shine.support.models import SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}

SIMPLE_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
}
SIMPLE_PARAMETER_MAP = {"positive_prompt": "1.inputs.text", "duration": "3.inputs.width"}
SIMPLE_OBJECT_INFO = {
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}},
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512}],
                "height": ["INT", {"default": 512}],
                "batch_size": ["INT", {"default": 1}],
            }
        }
    },
}


class ScriptedGateway:
    """A gateway whose model turns are fixed, and whose transport is real enough.

    ``native`` selects the transport: ``True`` returns turns from
    ``complete_tools_for_role``, ``False`` pretends the provider has no native
    tool support so the validated fallback path is exercised.
    """

    def __init__(self, turns: list[ToolTurn], *, native: bool = True, fallback: dict | None = None):
        self.turns = list(turns)
        self.native = native
        self.fallback = fallback or {"action": "finish", "final": "结束"}
        self.tool_turns = 0
        self.fallback_calls = 0
        self.pins: dict[str, object] = {}

    def profile_for_role(self, db, role):
        return SimpleNamespace(
            id="scripted", name="Scripted", kind="mock", base_url="", model="scripted"
        )

    def profile_for_role_or_inherit(self, db, role):
        return self.profile_for_role(db, role), role

    def pin_role(self, role, profile):
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
        return SimpleNamespace(
            text="", parsed=dict(self.fallback), model="scripted", latency_ms=1, usage={}
        )


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


def _session_row(factory, *, project_id=None) -> SupportSession:
    from datetime import timedelta

    with factory() as db:
        row = SupportSession(
            id=new_id(),
            project_id=project_id,
            page_id="copilot",
            status="OPEN",
            actor="local-user",
            context_json={},
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()
        return row


def _grant(factory, session, *, groups=("inspect", "workflows"), limits=None):
    with factory() as db:
        loaded, _ = grant_service.create(
            db,
            session=session,
            actor="local-user",
            objective="装配并验证用户级工作流",
            groups=list(groups),
            limits=limits,
            project_ids=[session.project_id] if session.project_id else [],
        )
        return loaded


def _register_arguments(name: str) -> dict:
    return {
        "name": name,
        "mode": "Ref2VA",
        "graph": SIMPLE_GRAPH,
        "parameter_map": SIMPLE_PARAMETER_MAP,
        "required_nodes": ["CLIPTextEncode"],
    }


# --------------------------------------------------------------------------
# L1: the two transports produce the same shape
# --------------------------------------------------------------------------


def test_openai_tool_turn_is_parsed():
    turn = parse_openai_turn(
        {
            "model": "local-text",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "workflow_graph_register",
                                    "arguments": '{"name": "x"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"total_tokens": 10},
        },
        latency_ms=5,
        model="local-text",
    )
    assert turn.native is True
    assert turn.tool_calls[0].name == "workflow_graph_register"
    assert turn.tool_calls[0].arguments == {"name": "x"}


def test_anthropic_tool_turn_is_parsed():
    turn = parse_anthropic_turn(
        {
            "model": "claude",
            "content": [
                {"type": "text", "text": "先检查"},
                {"type": "tool_use", "id": "toolu_1", "name": "setup_reconcile", "input": {"capability": "h3"}},
            ],
            "stop_reason": "tool_use",
        },
        latency_ms=7,
        model="claude",
    )
    assert turn.text == "先检查"
    assert turn.tool_calls[0].name == "setup_reconcile"
    assert turn.tool_calls[0].arguments == {"capability": "h3"}


def test_fallback_reply_refuses_a_tool_that_was_not_offered():
    with pytest.raises(Exception) as exc:
        parse_fallback_reply(
            {"action": "call_tool", "tool": "run_shell", "arguments": {}},
            tool_names={"setup_reconcile"},
        )
    assert "not offered" in str(exc.value)


def test_fallback_reply_finish_carries_no_calls():
    turn = parse_fallback_reply({"action": "finish", "final": "已核对"}, tool_names=set())
    assert turn.tool_calls == []
    assert turn.text == "已核对"


def test_tool_menu_only_lists_declared_capabilities():
    specs = tool_registry.tool_specs(tool_registry.AUTONOMOUS_CAPABILITIES)
    names = {spec.name for spec in specs}
    assert "run_shell" not in names
    assert "write_source" not in names
    for spec in specs:
        # Every offered tool has a real, non-permissive parameter schema.
        assert spec.parameters["type"] == "object"
        assert spec.parameters.get("additionalProperties") is False


def test_grant_menu_excludes_ungranted_capabilities():
    grant = grant_contract.build_proposed_grant(
        grant_id="g",
        session_id="s",
        actor_id="local-user",
        objective="只看已有文件",
        project_ids=[],
        installation_ids=[],
        allowed_resource_roots=[],
        groups=["inspect"],
        limits=None,
        created_at=utcnow(),
    )
    names = {spec.name for spec in tool_registry.grant_tool_specs(grant)}
    assert "setup_reconcile" in names
    assert "workflow_graph_register" not in names
    assert "resource_install_plan" not in names


# --------------------------------------------------------------------------
# L2: the loop is bounded and evidence-based
# --------------------------------------------------------------------------


def test_finishing_without_a_receipt_is_not_success(studio):
    """The model cannot declare victory; the server needs an original receipt."""
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    gateway = ScriptedGateway([ToolTurn(text="已经全部完成", tool_calls=[], native=False)])
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
        )
    assert outcome.status == "PAUSED_USER_DECISION"
    assert outcome.stop_reason == "NO_AUTHORITATIVE_EVIDENCE"


def test_a_verified_step_then_finish_is_success(studio, live_object_info):
    _, factory, _, settings = studio
    from tests.conftest import create_project

    client, _, _, _ = studio
    project_id = create_project(client, "循环成功")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session)
    gateway = ScriptedGateway(
        [
            ToolTurn(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="workflow_graph_register",
                        arguments=_register_arguments("循环产出工作流"),
                    )
                ],
                native=True,
            ),
            ToolTurn(text="工作流已注册并通过节点校验", tool_calls=[], native=False),
        ]
    )
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="装配并验证用户级工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
        )
    assert outcome.status == "SUCCEEDED", outcome.public()
    assert outcome.steps[0]["satisfied"] is True
    assert outcome.steps[0]["original_task_id"] is None  # EXECUTED synchronously
    assert outcome.steps[0]["action_id"]


def test_an_undeclared_tool_is_refused_and_bounded(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    invented = ToolTurn(
        text="",
        tool_calls=[ToolCall(id="c", name="run_shell", arguments={"command": "dir"})],
        native=True,
    )
    gateway = ScriptedGateway([invented] * 5)
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="随便试试",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
            limits=tool_loop.LoopLimits(max_steps=8, max_no_progress=2),
        )
    assert outcome.status == "FAILED"
    assert outcome.stop_reason == "TOOL_NOT_DECLARED"
    assert all(step["error_code"] == "TOOL_NOT_DECLARED" for step in outcome.steps)


def test_the_step_budget_is_enforced(studio, live_object_info):
    _, factory, _, settings = studio
    from tests.conftest import create_project

    client, _, _, _ = studio
    project_id = create_project(client, "循环步数")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, limits={"max_new_workflow_profiles": 20})
    # Always proposes a new registration, never finishes.
    turns = [
        ToolTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id=f"call-{index}",
                    name="workflow_graph_register",
                    arguments=_register_arguments(f"步数工作流 {index}"),
                )
            ],
            native=True,
        )
        for index in range(10)
    ]
    gateway = ScriptedGateway(turns)
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="反复注册工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
            limits=tool_loop.LoopLimits(max_steps=3),
        )
    assert outcome.status == "PAUSED_BUDGET"
    assert outcome.stop_reason == "STEP_BUDGET_EXHAUSTED"
    assert len(outcome.steps) == 3


def test_the_tool_call_budget_stops_the_loop(studio, live_object_info):
    _, factory, _, settings = studio
    from tests.conftest import create_project

    client, _, _, _ = studio
    project_id = create_project(client, "循环工具预算")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session, limits={"max_llm_tool_calls": 1, "max_new_workflow_profiles": 10})
    turns = [
        ToolTurn(
            text="",
            tool_calls=[
                ToolCall(
                    id=f"call-{index}",
                    name="workflow_graph_register",
                    arguments=_register_arguments(f"预算工作流 {index}"),
                )
            ],
            native=True,
        )
        for index in range(4)
    ]
    gateway = ScriptedGateway(turns)
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="反复注册工作流",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
            limits=tool_loop.LoopLimits(max_steps=6),
        )
    assert outcome.status == "PAUSED_BUDGET"
    assert outcome.stop_reason == "GRANT_TOOL_CALL_BUDGET_EXHAUSTED"
    assert len(outcome.steps) == 1


def test_a_revoked_grant_stops_the_loop_before_any_call(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    gateway = ScriptedGateway([ToolTurn(text="随便", tool_calls=[], native=False)])
    with factory() as db:
        grant_service.revoke(db, loaded.id, actor="local-user", reason="用户撤销")
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="随便",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
        )
    assert outcome.status == "PAUSED_USER_DECISION"
    assert outcome.stop_reason == "GRANT_NOT_RUNNING"
    assert gateway.tool_turns == 0


def test_no_model_configured_is_a_named_stop(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session)
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="检查环境",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=None,
        )
    assert outcome.status == "PAUSED_USER_DECISION"
    assert outcome.stop_reason == "LLM_UNAVAILABLE"


def test_fallback_transport_is_used_when_native_is_unavailable(studio):
    """A model without native tool calls still works, through the schema check."""
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect",))
    gateway = ScriptedGateway([], native=False, fallback={"action": "finish", "final": "看完了"})
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="只读检查",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
        )
    assert gateway.fallback_calls == 1
    assert outcome.llm["native_tool_calling"] is False
    assert outcome.llm["degraded_reason"] == "PROVIDER_WITHOUT_NATIVE_TOOL_CALLS"


def test_the_menu_offered_to_the_model_is_the_grant_menu(studio):
    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect",))
    captured: dict = {}

    class RecordingGateway(ScriptedGateway):
        def complete_tools_for_role(self, db, role, **kwargs):
            captured["tools"] = [spec.name for spec in kwargs["tools"]]
            return ToolTurn(text="结束", tool_calls=[], native=False)

    gateway = RecordingGateway([])
    with factory() as db:
        tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="只读检查",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=gateway,
        )
    assert "setup_reconcile" in captured["tools"]
    assert "workflow_graph_register" not in captured["tools"]


def test_a_gateway_with_no_configured_model_is_a_named_stop(studio):
    """A real gateway with no route must not become a 500 mid-task."""
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore

    _, factory, _, settings = studio
    session = _session_row(factory)
    loaded = _grant(factory, session, groups=("inspect",))
    with factory() as db:
        outcome = tool_loop.run(
            db, settings,
            grant_id=loaded.id,
            objective="只读检查",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session.id,
            gateway=LLMGateway(SecretStore(settings.secret_store_dir)),
        )
    assert outcome.status == "PAUSED_USER_DECISION"
    assert outcome.stop_reason == "LLM_NOT_CONFIGURED"

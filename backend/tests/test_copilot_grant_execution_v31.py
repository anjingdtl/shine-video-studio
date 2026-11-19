"""V3.1 execution: the grant permits, the original executor performs.

The point of these tests is that an autonomous call is *not* a shortcut.  Every
step still runs ``preview_action`` (snapshot, target fingerprint, capability
contract, original Pydantic parameters) and ``approve_action`` (the same checks
again plus the atomic claim), and the receipt is the original executor's own
state.  A permission the grant did not freeze is refused by name; a repeated
tool call returns the same original action instead of doing the work twice.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from shine.models import new_id, utcnow
from shine.support import actions as action_service
from shine.support import grants as grant_service
from shine.support.models import SupportAction, SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}

#: A minimal but structurally valid API workflow.  It is *not* a real H3 graph;
#: the tests that need live node validation install a fake ``object_info`` that
#: matches this exact shape.
SIMPLE_GRAPH = {
    "1": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 0]},
    },
    "2": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "model.safetensors"},
    },
    "3": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 1},
    },
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


@pytest.fixture
def live_object_info(monkeypatch):
    """A renderer that really knows this graph's node classes.

    This is a stand-in for a reachable ComfyUI, not a bypass: the production
    code path (``create_workflow_profile`` → ``validate_against_object_info``)
    runs unchanged and persists ``validated_against`` from it.
    """

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def object_info(self):
            return SIMPLE_OBJECT_INFO

    import shine.api_comfy as api_comfy

    monkeypatch.setattr(api_comfy, "ComfyUIClient", FakeClient)
    return SIMPLE_OBJECT_INFO


def _session_row(factory, *, project_id: str | None = None) -> SupportSession:
    with factory() as db:
        row = SupportSession(
            id=new_id(),
            project_id=project_id,
            page_id="copilot",
            status="OPEN",
            actor="local-user",
            context_json={},
            expires_at=utcnow() + __import__("datetime").timedelta(days=1),
        )
        db.add(row)
        db.commit()
        return row


def _grant(factory, *, session, groups=("inspect", "workflows"), limits=None, project_id=None):
    with factory() as db:
        loaded, _ = grant_service.create(
            db,
            session=session,
            actor="local-user",
            objective="装配并验证用户级工作流",
            groups=list(groups),
            limits=limits,
            project_ids=[project_id] if project_id else [],
        )
        return loaded


def _register_params(name: str) -> dict:
    return {
        "name": name,
        "mode": "Ref2VA",
        "graph": SIMPLE_GRAPH,
        "parameter_map": SIMPLE_PARAMETER_MAP,
        "required_nodes": ["CLIPTextEncode"],
    }


def test_execute_under_grant_registers_a_real_user_profile(studio, live_object_info):
    """The step goes through the original register route and reads it back."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权注册工作流")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)

    with factory() as db:
        receipt = action_service.execute_under_grant(
            db,
            settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("AI 装配的高级工作流"),
            tool_call_id="call-register-1",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "EXECUTED", receipt
    facts = receipt["observed_facts"]
    assert facts["verification_outcome"] == "VERIFIED"
    assert receipt["action_id"]

    from shine.models import WorkflowProfile

    with factory() as db:
        profile = db.scalar(
            select(WorkflowProfile).where(WorkflowProfile.name == "AI 装配的高级工作流")
        )
        assert profile is not None
        assert profile.project_id == project_id
        assert profile.graph_sha256
        assert profile.validated_against["missing_nodes"] == []
        # Built-in templates are untouched: this is a user-level row only.
        assert profile.project_id is not None

        action = db.get(SupportAction, receipt["action_id"])
        assert action.grant_id == loaded.id
        assert action.tool_call_id == "call-register-1"
        assert action.grant_tool_key


def test_the_same_tool_call_is_one_action(studio, live_object_info):
    """A retried HTTP request or a resumed worker cannot register twice."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权幂等动作")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)

    with factory() as db:
        first = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("重复调用只注册一次"),
            tool_call_id="call-same",
            actor="local-user",
            session_id=session.id,
        )
        second = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("重复调用只注册一次"),
            tool_call_id="call-same",
            actor="local-user",
            session_id=session.id,
        )
    assert first["action_id"] == second["action_id"]
    assert second["observed_facts"]["reused_existing_action"] is True

    from shine.models import WorkflowProfile

    with factory() as db:
        rows = db.scalars(
            select(WorkflowProfile).where(WorkflowProfile.name == "重复调用只注册一次")
        ).all()
        assert len(rows) == 1, "one tool call must not create two profiles"


def test_permission_outside_the_grant_is_refused_by_name(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权越权")
    session = _session_row(factory, project_id=project_id)
    # Only inspection and resources were authorized: workflow writes are not.
    loaded = _grant(
        factory, session=session, groups=("inspect", "resources"), project_id=project_id
    )
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("越权注册"),
            tool_call_id="call-denied",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "GRANT_PERMISSION_NOT_GRANTED"
    assert receipt["action_id"] is None

    from shine.models import WorkflowProfile

    with factory() as db:
        assert db.scalar(
            select(WorkflowProfile).where(WorkflowProfile.name == "越权注册")
        ) is None


def test_another_project_is_out_of_scope(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_a = create_project(client, "授权项目 A")
    project_b = create_project(client, "授权项目 B")
    session = _session_row(factory, project_id=project_a)
    loaded = _grant(factory, session=session, project_id=project_a)
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params={**_register_params("跨项目注册"), "project_id": project_b},
            tool_call_id="call-cross",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "SCOPE_EXCEEDED"


def test_an_unknown_capability_never_executes(studio):
    """A model-invented name has no contract, no executor and no permission."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权未知能力")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="run_shell",
            params={"command": "rm -rf /"},
            tool_call_id="call-shell",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "CAPABILITY_NOT_IN_GRANT_CATALOG"


def test_a_revoked_grant_cannot_start_a_new_step(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权撤销后")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    with factory() as db:
        grant_service.revoke(db, loaded.id, actor="local-user", reason="用户撤销")
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("撤销后注册"),
            tool_call_id="call-after-revoke",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] in {"GRANT_REVOKED", "GRANT_NOT_RUNNING"}


def test_a_different_actor_cannot_use_the_grant(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权身份")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("别人来注册"),
            tool_call_id="call-other-actor",
            actor="someone-else",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "GRANT_ACTOR_MISMATCH"


def test_graph_with_an_invented_node_is_refused_before_registration(studio):
    """GRANT-05: a fabricated class_type never reaches the profile table."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权臆造节点")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    graph = {
        "1": {"class_type": "ImaginaryH3SuperSampler", "inputs": {"strength": 2}},
    }
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params={
                "name": "臆造节点工作流",
                "mode": "Ref2VA",
                "graph": graph,
                "parameter_map": {"positive_prompt": "1.inputs.strength"},
            },
            tool_call_id="call-fake-node",
            actor="local-user",
            session_id=session.id,
        )
    # Either the graph validator or the parameter contract refuses it; both are
    # the *original* contract speaking, and neither creates a profile.
    assert receipt["status"] == "BLOCKED"
    from shine.models import WorkflowProfile

    with factory() as db:
        assert db.scalar(
            select(WorkflowProfile).where(WorkflowProfile.name == "臆造节点工作流")
        ) is None


def test_registration_without_positive_prompt_mapping_is_refused(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权缺映射")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params={
                "name": "缺映射工作流",
                "mode": "Ref2VA",
                "graph": SIMPLE_GRAPH,
                "parameter_map": {"duration": "3.inputs.width"},
            },
            tool_call_id="call-no-prompt",
            actor="local-user",
            session_id=session.id,
        )
    assert receipt["status"] == "BLOCKED"
    assert "positive_prompt" in (receipt["message"] or "")


def test_the_grant_records_the_identity_chain(studio, live_object_info):
    """GRANT identity and EXECUTION identity stay separately auditable."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权身份链")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id)
    with factory() as db:
        receipt = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("身份链工作流"),
            tool_call_id="call-chain",
            actor="local-user",
            session_id=session.id,
        )
        refreshed = grant_service.load(db, loaded.id)
        assert refreshed.grant["sub_actions"], "the sub-action reference is kept"
        entry = refreshed.grant["sub_actions"][-1]
        assert entry["action_id"] == receipt["action_id"]
        assert entry["tool_call_id"] == "call-chain"
        assert entry["capability_id"] == "workflow_graph_register"
        assert refreshed.grant["tool_calls"][-1]["tool_call_id"] == "call-chain"


def test_two_tool_calls_for_one_workflow_name_reuse_the_business_identity(studio, live_object_info):
    """GRANT-18: repeating the same registration does not create a second copy."""
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权重复注册")
    session = _session_row(factory, project_id=project_id)
    loaded = _grant(factory, session=session, project_id=project_id, limits={"max_new_workflow_profiles": 5})
    with factory() as db:
        first = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("同一目标工作流"),
            tool_call_id="call-a",
            actor="local-user",
            session_id=session.id,
        )
        second = action_service.execute_under_grant(
            db, settings,
            grant_id=loaded.id,
            capability_id="workflow_graph_register",
            params=_register_params("同一目标工作流"),
            tool_call_id="call-b",
            actor="local-user",
            session_id=session.id,
        )
    assert first["status"] == "EXECUTED"
    # Each tool call keeps its own action row, but the *work* is reused: the
    # same name + same graph bytes are the same registration.
    assert second["status"] == "EXECUTED"
    assert second["observed_facts"]["reused_existing_profile"] is True
    assert second["observed_facts"]["created_profile_id"] == first["observed_facts"]["created_profile_id"]

    from shine.models import WorkflowProfile

    with factory() as db:
        rows = db.scalars(
            select(WorkflowProfile).where(WorkflowProfile.name == "同一目标工作流")
        ).all()
        assert len(rows) == 1

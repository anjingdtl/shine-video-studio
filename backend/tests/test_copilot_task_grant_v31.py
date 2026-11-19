"""V3.1 task-level grant: one click, a frozen boundary, server-enforced limits.

These tests pin the *authorization* layer, not prose: a grant cannot be widened
by the caller, cannot be replayed into a second installation, cannot outlive its
validity, and cannot authorize a capability that is out of scope, over budget or
not in the reviewed catalog.  The execution layer is exercised by
``test_copilot_grant_execution_v31.py``.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from shine.copilot import task_grant as grant_contract
from shine.models import new_id, utcnow
from shine.support import grants as grant_service
from shine.support.models import SupportDelegation, SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}


def _proposed(**overrides):
    base = dict(
        grant_id="grant-unit",
        session_id="session-unit",
        actor_id="local-user",
        objective="装配 H3 + 加速 LoRA + 二次采样 + RelayVSR X2",
        project_ids=["project-1"],
        installation_ids=["instance-1"],
        allowed_resource_roots=["D:/ComfyUI"],
        groups=["inspect", "resources", "workflows"],
        limits={"max_download_bytes": 5_000},
        created_at=utcnow(),
    )
    base.update(overrides)
    return grant_contract.build_proposed_grant(**base)


# --------------------------------------------------------------------------
# L1: the contract itself is a pure function
# --------------------------------------------------------------------------


def test_grant_digest_ignores_the_running_ledger():
    """Status, usage and tool calls change; the authorization does not."""
    grant = _proposed()
    before = grant["grant_digest"]
    grant["status"] = "RUNNING"
    grant["usage"] = {**grant_contract.usage_of(grant), "max_download_bytes": 100}
    grant["tool_calls"] = [{"tool_call_id": "x", "capability_id": "setup_repair"}]
    assert grant_contract.compute_grant_digest(grant) == before


def test_grant_digest_changes_when_scope_changes():
    first = _proposed()
    second = _proposed(project_ids=["project-1", "project-2"])
    assert first["grant_digest"] != second["grant_digest"]


def test_permanently_forbidden_permissions_are_never_granted():
    """Asking for a wider boundary cannot produce it."""
    grant = _proposed(groups=["inspect", "resources", "nodes", "workflows", "runtime", "test"])
    for permission in grant_contract.PERMANENTLY_FORBIDDEN:
        assert grant["permissions"][permission] is False
    assert grant["permissions"]["download_models"] is True


def test_unknown_capability_group_is_refused_by_name():
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.normalize_groups(["inspect", "install_anything"])
    assert exc.value.code == "UNKNOWN_CAPABILITY_GROUP"


def test_unknown_limit_and_ceiling_are_refused():
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.normalize_limits({"max_shell_commands": 5})
    assert exc.value.code == "UNKNOWN_LIMIT"
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.normalize_limits({"max_download_bytes": 10**15})
    assert exc.value.code == "LIMIT_ABOVE_CEILING"
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.normalize_limits({"max_gpu_minutes": -1})
    assert exc.value.code == "INVALID_LIMIT"


def test_project_scope_is_enforced():
    grant = _proposed()
    grant_contract.assert_project_scope(
        grant, project_id="project-1", capability_id="workflow_graph_register"
    )
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_project_scope(
            grant, project_id="project-2", capability_id="workflow_graph_register"
        )
    assert exc.value.code == "SCOPE_EXCEEDED"


def test_installation_scope_is_enforced():
    grant = _proposed()
    grant_contract.assert_installation_scope(grant, installation_id="instance-1")
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_installation_scope(grant, installation_id="someone-elses")
    assert exc.value.code == "SCOPE_EXCEEDED"


def test_budget_and_tool_call_counts_are_enforced():
    grant = _proposed()
    grant_contract.assert_budget(grant, cost={"max_download_bytes": 4_000})
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_budget(grant, cost={"max_download_bytes": 6_000})
    assert exc.value.code == "GRANT_BUDGET_EXCEEDED"

    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_budget(grant, counters={"max_llm_tool_calls": 10_000})
    assert exc.value.code == "GRANT_BUDGET_EXCEEDED"


def test_expiry_and_revocation_are_named_refusals():
    now = utcnow()
    grant = _proposed(created_at=now, validity=timedelta(minutes=5))
    grant_contract.assert_usable(grant, now=now)
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_usable(grant, now=now + timedelta(minutes=10))
    assert exc.value.code == "GRANT_EXPIRED"

    revoked = {**grant, "revoked_at": now.isoformat()}
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_usable(revoked, now=now)
    assert exc.value.code == "GRANT_REVOKED"


def test_tool_intent_validation_is_strict():
    good = grant_contract.validate_intent(
        {
            "schema": grant_contract.TOOL_INTENT_SCHEMA,
            "tool_call_id": "call-1",
            "capability_id": "setup_reconcile",
            "params": {"capability": "h3"},
        }
    )
    assert good["capability_id"] == "setup_reconcile"

    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.validate_intent(
            {"schema": "something-else", "tool_call_id": "c", "capability_id": "setup_reconcile"}
        )
    assert exc.value.code == "INTENT_SCHEMA_MISMATCH"

    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.validate_intent(
            {
                "schema": grant_contract.TOOL_INTENT_SCHEMA,
                "capability_id": "setup_reconcile",
            }
        )
    assert exc.value.code == "INTENT_TOOL_CALL_ID_REQUIRED"


def test_no_capability_maps_onto_a_shell_or_source_write():
    """The grant catalog can only name reviewed business capabilities."""
    forbidden_permissions = {"modify_source_code", "arbitrary_shell", "arbitrary_http"}
    for capability_id, permission in grant_contract.CAPABILITY_PERMISSIONS.items():
        assert permission not in forbidden_permissions, capability_id
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.permission_for_capability("run_shell")
    assert exc.value.code == "CAPABILITY_NOT_IN_GRANT_CATALOG"


# --------------------------------------------------------------------------
# L2: one click, one grant, enforced by the database
# --------------------------------------------------------------------------


def _session_row(factory, *, project_id: str | None = None) -> SupportSession:
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


def test_repeated_authorization_returns_the_same_grant(studio):
    """GRANT-01: a double click cannot create a second authorization."""
    _, factory, _, _ = studio
    session = _session_row(factory, project_id="project-grant")
    with factory() as db:
        first, created_first = grant_service.create(
            db,
            session=session,
            actor="local-user",
            objective="装配 H3 高级工作流",
            groups=["inspect", "resources"],
            limits={"max_download_bytes": 1_000},
        )
        second, created_second = grant_service.create(
            db,
            session=session,
            actor="local-user",
            objective="装配 H3 高级工作流",
            groups=["inspect", "resources"],
            limits={"max_download_bytes": 1_000},
        )
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        rows = db.query(SupportDelegation).filter(
            SupportDelegation.grant_id == first.id
        ).count()
        assert rows == 1


def test_a_finished_grant_allows_a_new_attempt(studio):
    """After a task really ended, the same objective is a new authorization."""
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        first, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="把本机环境补齐", groups=["inspect"],
        )
        grant_service.set_status(db, first.id, "SUCCEEDED")
        second, created = grant_service.create(
            db, session=session, actor="local-user",
            objective="把本机环境补齐", groups=["inspect"],
        )
        assert created is True
        assert second.id != first.id


def test_revoke_stops_new_writes_and_keeps_the_original_task_note(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="装配工作流", groups=["inspect", "workflows"],
        )
        revoked = grant_service.revoke(db, loaded.id, actor="local-user", reason="用户撤销")
        assert grant_contract.grant_status(revoked.grant, now=utcnow()) == "REVOKED"
        with pytest.raises(grant_contract.GrantError) as exc:
            grant_contract.assert_usable(revoked.grant, now=utcnow())
        assert exc.value.code == "GRANT_REVOKED"


def test_budget_reservation_is_monotonic_and_bounded(studio):
    """A reservation is written before the call and never silently re-spent."""
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="下载缺失权重", groups=["inspect", "resources"],
            limits={"max_download_bytes": 1_000},
        )
        first = grant_service.reserve(
            db, loaded.id, capability_id="resource_install_plan",
            cost={"max_download_bytes": 600},
        )
        assert grant_contract.usage_of(first.grant)["max_download_bytes"] == 600
        with pytest.raises(grant_contract.GrantError) as exc:
            grant_service.reserve(
                db, loaded.id, capability_id="resource_install_plan",
                cost={"max_download_bytes": 600},
            )
        assert exc.value.code == "GRANT_BUDGET_EXCEEDED"
        # A settlement may only shrink THIS step's reservation.
        settled = grant_service.settle(
            db, loaded.id,
            reserved={"max_download_bytes": 600}, actual={"max_download_bytes": 100},
        )
        assert grant_contract.usage_of(settled.grant)["max_download_bytes"] == 100


def test_settle_never_widens_a_frozen_boundary(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="下载缺失权重", groups=["inspect", "resources"],
            limits={"max_download_bytes": 1_000},
        )
        grant_service.reserve(
            db, loaded.id, capability_id="resource_install_plan",
            cost={"max_download_bytes": 300},
        )
        settled = grant_service.settle(
            db, loaded.id,
            reserved={"max_download_bytes": 300}, actual={"max_download_bytes": 9_000},
        )
        assert grant_contract.usage_of(settled.grant)["max_download_bytes"] == 300


def test_tool_call_is_recorded_once(studio):
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="检查环境", groups=["inspect"],
        )
        grant_service.record_tool_call(
            db, loaded.id, tool_call_id="call-1", capability_id="setup_reconcile",
            status="SUBMITTED",
        )
        again = grant_service.record_tool_call(
            db, loaded.id, tool_call_id="call-1", capability_id="setup_reconcile",
            status="SUBMITTED",
        )
        assert len(again.grant["tool_calls"]) == 1
        assert grant_contract.usage_of(again.grant)["max_llm_tool_calls"] == 1


def test_contract_change_after_authorization_pauses_the_step(studio):
    """A frozen capability contract that changed is a named pause."""
    _, factory, _, _ = studio
    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="装配工作流", groups=["workflows"],
        )
        tampered = {
            **loaded.grant,
            "frozen_capability_contracts": {
                **loaded.grant["frozen_capability_contracts"],
                "workflow_graph_register": "0" * 64,
            },
        }
        with pytest.raises(grant_contract.GrantError) as exc:
            grant_contract.assert_contract_frozen(tampered, "workflow_graph_register")
        assert exc.value.code == "GRANT_CONTRACT_CHANGED"


# --------------------------------------------------------------------------
# L2 (API): the one click through HTTP
# --------------------------------------------------------------------------


def _session(client, project_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "copilot", "project_id": project_id, "question": "帮我装配 H3 工作流"},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


def _configure_text_model(client, *, role: str = "comfyops") -> str:
    """Give the workbench one configured text model, as a real installation has.

    GRANT-15 says the copilot inherits it; this helper makes that inheritance
    observable instead of assuming a model exists out of the box.
    """
    created = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": f"grant-{role}-model",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:9/v1",
            "model": "local-text",
            "enabled": True,
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    routed = client.put(
        "/api/v1/agent-routes",
        json={"role": role, "provider_profile_id": profile_id},
        headers=HEADERS,
    )
    assert routed.status_code == 200, routed.text
    return profile_id


def test_preview_inherits_the_configured_text_model(studio):
    """GRANT-15: no second API key, no second provider profile."""
    client, _, _, _ = studio
    from tests.conftest import create_project

    _configure_text_model(client, role="comfyops")
    project_id = create_project(client, "继承模型")
    session = _session(client, project_id)
    preview = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "检查本机环境并登记已有文件",
            "project_id": project_id,
            "capability_groups": ["inspect"],
        },
        headers=HEADERS,
    ).json()
    assert preview["model"]["inherited"] is True, preview["model"]
    assert preview["model"]["source_role"] == "comfyops", preview["model"]
    assert preview["model"]["model"] == "local-text"
    assert preview["can_authorize"] is True, preview["blocked_capabilities"]


def test_preview_creates_no_task_and_no_grant(studio):
    client, factory, _, _ = studio
    from tests.conftest import create_project

    _configure_text_model(client)
    project_id = create_project(client, "授权预览")
    session = _session(client, project_id)
    response = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "装配 H3 高级工作流并验证",
            "project_id": project_id,
            "capability_groups": ["inspect", "workflows"],
            "limits": {"max_new_workflow_profiles": 2},
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == grant_contract.SCHEMA
    assert body["can_authorize"] is True
    assert body["preview_id"].startswith("grantpreview-")
    assert "THIRD_PARTY_CODE" not in body["risks"]  # nodes were not requested
    with factory() as db:
        assert db.query(SupportDelegation).filter(
            SupportDelegation.grant_id.is_not(None)
        ).count() == 0


def test_authorize_requires_the_previewed_digest(studio):
    client, _, _, _ = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权摘要")
    session = _session(client, project_id)
    preview = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "检查本机环境",
            "project_id": project_id,
            "capability_groups": ["inspect"],
        },
        headers=HEADERS,
    ).json()
    wrong = client.post(
        "/api/v1/support/task-grants/authorize",
        json={
            "session_id": session["id"],
            "preview_id": preview["preview_id"],
            "grant_digest": "0" * 64,
            "confirm": True,
            "run": False,
        },
        headers=HEADERS,
    )
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "GRANT_DIGEST_MISMATCH"


def test_authorize_requires_explicit_confirm(studio):
    client, _, _, _ = studio
    from tests.conftest import create_project

    project_id = create_project(client, "授权确认")
    session = _session(client, project_id)
    preview = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "检查本机环境",
            "project_id": project_id,
            "capability_groups": ["inspect"],
        },
        headers=HEADERS,
    ).json()
    response = client.post(
        "/api/v1/support/task-grants/authorize",
        json={
            "session_id": session["id"],
            "preview_id": preview["preview_id"],
            "grant_digest": preview["grant_digest"],
            "confirm": False,
        },
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CONFIRM_REQUIRED"


def test_authorize_twice_over_http_returns_one_grant(studio):
    """GRANT-01 at the transport level: replay is idempotent."""
    client, factory, _, _ = studio
    from tests.conftest import create_project

    _configure_text_model(client)
    project_id = create_project(client, "授权幂等")
    session = _session(client, project_id)
    preview = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "检查本机环境并登记已有文件",
            "project_id": project_id,
            "capability_groups": ["inspect"],
        },
        headers=HEADERS,
    ).json()
    payload = {
        "session_id": session["id"],
        "preview_id": preview["preview_id"],
        "grant_digest": preview["grant_digest"],
        "confirm": True,
        "run": False,
    }
    first = client.post("/api/v1/support/task-grants/authorize", json=payload, headers=HEADERS)
    second = client.post("/api/v1/support/task-grants/authorize", json=payload, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["grant_id"] == second.json()["grant_id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    with factory() as db:
        assert db.query(SupportDelegation).filter(
            SupportDelegation.grant_id == first.json()["grant_id"]
        ).count() == 1


def test_revoke_over_http_reports_the_safe_stop(studio):
    client, _, _, _ = studio
    from tests.conftest import create_project

    _configure_text_model(client)
    project_id = create_project(client, "撤销授权")
    session = _session(client, project_id)
    preview = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "objective": "检查本机环境",
            "project_id": project_id,
            "capability_groups": ["inspect"],
        },
        headers=HEADERS,
    ).json()
    granted = client.post(
        "/api/v1/support/task-grants/authorize",
        json={
            "session_id": session["id"],
            "preview_id": preview["preview_id"],
            "grant_digest": preview["grant_digest"],
            "confirm": True,
            "run": False,
        },
        headers=HEADERS,
    ).json()
    revoked = client.post(
        f"/api/v1/support/task-grants/{granted['grant_id']}/revoke", headers=HEADERS
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert "不会被强制取消" in revoked.json()["note"]
    detail = client.get(
        f"/api/v1/support/task-grants/{granted['grant_id']}", headers=HEADERS
    ).json()
    assert detail["status"] == "REVOKED"

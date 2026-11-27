"""V4: LLM-native copilot — no fake conversation, full-chain read-only tools.

What these tests pin (the V4 plan, modules 1 and 2):

* without a configured text model there is no conversation: a message is
  refused with ``409 COPILOT_LLM_REQUIRED`` and nothing is stored, while the
  deterministic health check still answers;
* the keyword planner, the keyword Q&A templates and the plan-delegation
  chain are gone (not merely unused);
* the read-only menu is exactly 14 tools, each reading an existing authority;
* a configured model investigates the real ledgers through those tools,
  answers from what it saw, and proposes changes as cards — never executes;
* every read is scoped to the session's project.
"""
from __future__ import annotations

import importlib

import pytest

from shine.copilot import chat_investigator, read_tools
from shine.copilot import task_grant as grant_contract
from shine.llm.base import LLMNotConfigured
from shine.models import new_id, utcnow
from shine.support import agent as agent_service
from shine.support.models import SupportAction, SupportMessage, SupportSession
from tests.copilot_v4_support import (
    HEADERS,
    ScriptedGateway,
    call,
    configure_mock_text_model,
    final,
    finish_reply,
    scripted_responder,
    tool_reply,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Module 1: no LLM → deterministic health check, no conversation
# --------------------------------------------------------------------------


def test_keyword_planner_nlu_and_plan_delegation_are_gone():
    for module in ("shine.support.planner", "shine.support.nlu", "shine.support.delegation"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
    from shine.support import diagnostics

    assert not hasattr(diagnostics, "_keyword_rule")
    assert not hasattr(diagnostics, "_KEYWORD_RULES")
    assert not hasattr(agent_service, "_compose_answer")
    assert not hasattr(agent_service, "_offline_direct_answer")


def test_plan_routes_are_retired(studio):
    client, _, _, _ = studio
    assert client.post("/api/v1/support/plans/preview", json={"request": "准备"},
                       headers=HEADERS).status_code == 404
    assert client.post("/api/v1/support/plans/delegate", json={}, headers=HEADERS).status_code == 404


def test_health_check_without_a_project_is_read_only_and_grouped(studio):
    client, factory, _, _ = studio
    response = client.get("/api/v1/support/health-check")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert [group["key"] for group in body["groups"]] == [
        "environment", "storyboard", "materials", "tasks", "gpu",
    ]
    assert body["summary"]["status"] in {"OK", "WARNING", "BLOCKED"}
    # No text model is configured in a fresh workbench: named, with the entry.
    environment = body["groups"][0]
    llm_items = [row for row in environment["items"] if row["code"] == "COPILOT_LLM_REQUIRED"]
    assert llm_items and llm_items[0]["jump"] == "#d/advanced"
    # Nothing was written by a read.
    with factory() as db:
        assert db.query(SupportAction).count() == 0


def _failed_job(client, factory, project_id: str, *, failure_code: str, request: dict | None = None) -> str:
    """A real shot + job through the real routes, then failed in the ledger."""
    from tests.conftest import create_job, create_shot, freeze_style

    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory() as db:
        from shine.models import Job

        row = db.get(Job, job["id"])
        row.status = "FAILED"
        row.failure_code = failure_code
        if request is not None:
            row.request = request
        db.commit()
    return job["id"]


def test_health_check_names_a_real_failed_job_for_a_project(studio):
    client, factory, _, _ = studio
    from tests.conftest import create_project

    project_id = create_project(client, "体检项目")
    _failed_job(client, factory, project_id, failure_code="CUDA_OUT_OF_MEMORY")
    body = client.get("/api/v1/support/health-check", params={"project_id": project_id}).json()
    tasks = next(group for group in body["groups"] if group["key"] == "tasks")
    failed = [row for row in tasks["items"] if row["error_code"] == "CUDA_OUT_OF_MEMORY"]
    assert failed, tasks
    assert failed[0]["status"] == "BLOCKED"
    assert failed[0]["jump"] == "#d/run"
    assert failed[0]["entity_type"] == "job"
    assert any(row["error_code"] == "CUDA_OUT_OF_MEMORY" for row in body["blockers"])
    assert body["project_id"] == project_id


def test_health_check_reports_an_unreadable_project_by_name(studio):
    client, _, _, _ = studio
    response = client.get("/api/v1/support/health-check", params={"project_id": "no-such-project"})
    assert response.status_code in {400, 404}
    assert response.json()["detail"]


def test_message_without_a_model_is_refused_and_nothing_is_stored(studio):
    client, factory, _, _ = studio
    created = client.post("/api/v1/support/sessions",
                          json={"page_id": "copilot", "question": ""}, headers=HEADERS)
    assert created.status_code == 201, created.text
    assert created.json()["llm"]["configured"] is False
    session_id = created.json()["session"]["id"]
    reply = client.post(f"/api/v1/support/sessions/{session_id}/messages",
                        json={"content": "帮我检查环境"}, headers=HEADERS)
    assert reply.status_code == 409
    detail = reply.json()["detail"]
    assert detail["code"] == "COPILOT_LLM_REQUIRED"
    assert detail["href"] == "#d/advanced"
    with factory() as db:
        assert db.query(SupportMessage).filter(SupportMessage.session_id == session_id).count() == 0


def test_handle_message_without_a_model_has_no_template_fallback(studio):
    _, factory, _, settings = studio
    with factory() as db:
        session = SupportSession(id=new_id(), page_id="copilot", status="OPEN", actor="local-user",
                                 context_json={}, expires_at=utcnow())
        db.add(session)
        db.commit()
        session_id = session.id
    with factory() as db:
        session = db.get(SupportSession, session_id)
        with pytest.raises(LLMNotConfigured):
            agent_service.handle_message(db, settings, session, "参考图最多几张", gateway=None)
        db.rollback()
    with factory() as db:
        assert db.query(SupportMessage).filter(SupportMessage.session_id == session_id).count() == 0


def test_policy_refusal_is_answered_without_a_model(studio):
    _, factory, _, settings = studio
    with factory() as db:
        session = SupportSession(id=new_id(), page_id="copilot", status="OPEN", actor="local-user",
                                 context_json={}, expires_at=utcnow())
        db.add(session)
        db.commit()
        result = agent_service.handle_message(
            db, settings, session, "帮我执行 git push 把代码推上去", gateway=None
        )
        assert "已拒绝越权操作" in result["message"]["content"]
        assert result["diagnosis"]["llm"]["status"] == "POLICY_REFUSAL"
        assert result["message"]["payload"]["proposals"] == []


# --------------------------------------------------------------------------
# Module 2: the 14 read-only tools
# --------------------------------------------------------------------------


def test_read_menu_is_exactly_the_fourteen_full_chain_tools():
    assert sorted(read_tools.READ_TOOL_DEFINITIONS) == sorted([
        "inspect_environment", "read_resource_inventory", "read_node_inventory",
        "inspect_project_overview", "inspect_literary_and_story", "inspect_shots_and_bindings",
        "inspect_shot_prompt_and_evidence", "inspect_materials_and_loras", "inspect_asset_center",
        "read_production_run", "inspect_deliveries_and_library", "inspect_leases_and_workers",
        "read_recent_audit_events", "read_original_task",
    ])
    specs = read_tools.read_tool_specs()
    assert len(specs) == 14
    assert all(spec.parameters.get("additionalProperties") is False for spec in specs)
    assert read_tools.is_read_tool("inspect_shots_and_bindings")
    assert not read_tools.is_read_tool("production_start")


def test_every_read_tool_executes_against_a_real_project_without_writing(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "只读查证项目")
    executed = []
    with factory() as db:
        before = db.query(SupportAction).count()
        for name in read_tools.READ_TOOL_DEFINITIONS:
            if name in {"read_original_task", "read_production_run", "read_node_inventory",
                        "inspect_shot_prompt_and_evidence"}:
                continue
            arguments = {"project_id": project_id}
            receipt = read_tools.execute(db, settings, name=name, arguments=arguments,
                                         grant=None, scope_project_id=project_id)
            assert receipt["status"] == "OK", (name, receipt)
            assert receipt["read_only"] is True
            assert isinstance(receipt["elapsed_ms"], int)
            assert receipt["authority"]
            executed.append(name)
        db.rollback()
        assert db.query(SupportAction).count() == before
    assert len(executed) == 10


def test_read_tools_refuse_by_name(studio):
    _, factory, _, settings = studio
    with factory() as db:
        with pytest.raises(grant_contract.GrantError) as exc:
            read_tools.execute(db, settings, name="inspect_nothing", arguments={})
        assert exc.value.code == "TOOL_NOT_DECLARED"
        with pytest.raises(grant_contract.GrantError) as exc:
            read_tools.execute(db, settings, name="inspect_project_overview", arguments={})
        assert exc.value.code == "PROJECT_REQUIRED"
        with pytest.raises(grant_contract.GrantError) as exc:
            read_tools.execute(db, settings, name="read_original_task", arguments={})
        assert exc.value.code == "INTENT_PARAMS_MALFORMED"
        db.rollback()


def test_a_read_cannot_look_at_another_project_from_the_chat(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    first = create_project(client, "会话项目")
    second = create_project(client, "别的项目")
    with factory() as db:
        with pytest.raises(grant_contract.GrantError) as exc:
            read_tools.execute(db, settings, name="inspect_project_overview",
                               arguments={"project_id": second}, grant=None, scope_project_id=first)
        assert exc.value.code == "SCOPE_EXCEEDED"
        db.rollback()


def test_shot_prompt_evidence_reads_the_archived_request(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "提示词证据")
    job_id = _failed_job(client, factory, project_id, failure_code="EDITORIAL_TIMING_CONFLICT", request={
        "generation_mode": "REF2VA", "compiler_version": "2.2.0", "rule_version": "h3-1.3.0",
        "compiled_prompt": "【Subject】a pilgrim holding two lanterns",
        "reference_manifest": [
            {"label": "image_1", "asset_id": "asset-scene-temple", "use_as": "picture", "ordinal": 0},
            {"label": "image_2", "asset_id": "asset-character-pilgrim", "use_as": "subject", "ordinal": 1},
        ],
    })
    with factory() as db:
        from shine.models import Job

        shot_id = db.get(Job, job_id).shot_id
        receipt = read_tools.execute(db, settings, name="inspect_shot_prompt_and_evidence",
                                     arguments={"shot_id": shot_id}, grant=None, scope_project_id=project_id)
        facts = receipt["observed_facts"]
        assert facts["job_id"] == job_id
        assert facts["compiler_version"] == "2.2.0"
        assert "lanterns" in facts["compiled_prompt"]
        assert [row["label"] for row in facts["reference_slots"]] == ["image_1", "image_2"]
        assert facts["reference_slots"][1]["use_as"] == "subject"
        assert facts["failure_code"] == "EDITORIAL_TIMING_CONFLICT"
        db.rollback()


# --------------------------------------------------------------------------
# Module 2: the chat investigation loop
# --------------------------------------------------------------------------


def _session_row(factory, *, project_id=None) -> str:
    with factory() as db:
        row = SupportSession(id=new_id(), project_id=project_id, page_id="copilot", status="OPEN",
                             actor="local-user", context_json={}, expires_at=utcnow())
        db.add(row)
        db.commit()
        return row.id


def test_the_model_investigates_then_answers_from_real_ids(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "查证项目")
    session_id = _session_row(factory, project_id=project_id)
    with factory() as db:
        session = db.get(SupportSession, session_id)
        gateway = ScriptedGateway([
            call("inspect_project_overview", {"project_id": project_id}, call_id="c1"),
            call("inspect_leases_and_workers", {}, call_id="c2"),
            final("项目《查证项目》还没有导入原文；下一步是导入小说或剧本。"),
        ])
        result = agent_service.handle_message(db, settings, session, "项目走到哪一步了？", gateway=gateway)
        payload = result["message"]["payload"]
        assert result["message"]["content"].startswith("项目《查证项目》")
        tools = payload["read_tools"]
        assert [row["tool"] for row in tools] == ["inspect_project_overview", "inspect_leases_and_workers"]
        assert all(row["status"] == "OK" for row in tools)
        assert tools[0]["ids"] and "project_id=" in tools[0]["ids"][0]
        assert isinstance(tools[0]["elapsed_ms"], int)
        assert payload["llm"]["status"] == "OK"
        assert payload["llm"]["tool_calls_used"] == 2
        assert payload["llm"]["tool_call_budget"] == chat_investigator.MAX_CHAT_TOOL_CALLS
        assert "intent" not in payload and "plan" not in payload


def test_a_proposal_is_a_card_never_an_execution(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "建议卡片")
    session_id = _session_row(factory, project_id=project_id)
    with factory() as db:
        session = db.get(SupportSession, session_id)
        before = db.query(SupportAction).count()
        gateway = ScriptedGateway([
            call("inspect_project_overview", {"project_id": project_id}, call_id="c1"),
            call("propose_action", {
                "action_id": "document_import_text",
                "params": {"project_id": project_id, "text": "第一行正文。"},
                "reason": "项目还没有原文", "summary": "导入这段原文",
            }, call_id="p1"),
            call("propose_project_grant", {
                "objective": "把第一集从素材到成片全部推进", "capability_groups": ["inspect", "test"],
            }, call_id="p2"),
            final("建议先导入原文。"),
        ])
        result = agent_service.handle_message(db, settings, session, "帮我把项目推进到出片", gateway=gateway)
        proposals = result["message"]["payload"]["proposals"]
        assert [row["type"] for row in proposals] == ["action", "project_grant"]
        card = proposals[0]
        assert card["valid"] is True and card["error"] is None
        assert card["action_id"] == "document_import_text"
        assert card["params"]["project_id"] == project_id
        assert card["params"]["text"] == "第一行正文。"
        assert card["project_id"] == project_id
        grant = proposals[1]
        assert grant["valid"] is True
        assert grant["objective"].startswith("把第一集")
        assert grant["capability_groups"] == ["inspect", "test"]
        assert db.query(SupportAction).count() == before, "a proposal must never create an action"


def test_an_invalid_proposal_is_refused_by_name_and_not_recorded_as_valid(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "非法建议")
    other = create_project(client, "另一个项目")
    session_id = _session_row(factory, project_id=project_id)
    with factory() as db:
        session = db.get(SupportSession, session_id)
        gateway = ScriptedGateway([
            call("propose_action", {"action_id": "shell", "params": {"command": "rm -rf /"}}, call_id="x1"),
            call("propose_action", {
                "action_id": "document_import_text",
                "params": {"project_id": other, "text": "x"},
            }, call_id="x2"),
            call("propose_action", {"action_id": "document_import_text", "params": {"text": 123}}, call_id="x3"),
            call("propose_project_grant", {"objective": "x"}, call_id="x4"),
            final("没有可执行的建议。"),
        ])
        result = agent_service.handle_message(db, settings, session, "随便做点什么", gateway=gateway)
        proposals = result["message"]["payload"]["proposals"]
        assert [row["valid"] for row in proposals] == [False, False, False, False]
        assert proposals[0]["error"] == "TOOL_NOT_DECLARED"
        assert proposals[1]["error"] == "SCOPE_EXCEEDED"
        assert proposals[2]["error"] and "text" in proposals[2]["error"]
        assert proposals[3]["error"] == "OBJECTIVE_REQUIRED"


def test_the_chat_tool_budget_is_bounded(studio):
    client, factory, _, settings = studio
    from tests.conftest import create_project

    project_id = create_project(client, "预算项目")
    session_id = _session_row(factory, project_id=project_id)
    with factory() as db:
        session = db.get(SupportSession, session_id)
        seven_reads = [call("inspect_leases_and_workers", {}, call_id=f"c{i}") for i in range(7)]
        gateway = ScriptedGateway([*seven_reads, final("查完了。")])
        result = agent_service.handle_message(db, settings, session, "反复查", gateway=gateway)
        payload = result["message"]["payload"]
        assert len(payload["read_tools"]) == 6
        assert payload["llm"]["tool_calls_used"] == 6
        assert payload["llm"]["status"] == "OK"
        assert result["message"]["content"] == "查完了。"


def test_an_unknown_tool_from_the_model_is_refused_not_executed(studio):
    client, factory, _, settings = studio
    session_id = _session_row(factory)
    with factory() as db:
        session = db.get(SupportSession, session_id)
        gateway = ScriptedGateway([
            call("run_shell", {"command": "dir"}, call_id="u1"),
            final("没有这个工具。"),
        ])
        result = agent_service.handle_message(db, settings, session, "列出目录", gateway=gateway)
        assert result["message"]["payload"]["read_tools"] == []
        assert result["message"]["content"] == "没有这个工具。"


def test_over_http_the_fallback_transport_investigates_the_real_ledger(studio):
    client, factory, _, _ = studio
    from tests.conftest import create_project

    project_id = create_project(client, "HTTP 查证")
    seen: list[str] = []
    configure_mock_text_model(
        client,
        scripted_responder([
            tool_reply("inspect_project_overview", {"project_id": project_id}),
            finish_reply("已查证：项目尚未导入原文。"),
        ], record=seen),
    )
    created = client.post("/api/v1/support/sessions",
                          json={"page_id": "copilot", "project_id": project_id, "question": ""},
                          headers=HEADERS)
    assert created.status_code == 201, created.text
    assert created.json()["llm"]["configured"] is True
    session_id = created.json()["session"]["id"]
    reply = client.post(f"/api/v1/support/sessions/{session_id}/messages",
                        json={"content": "这个项目现在什么状态？"}, headers=HEADERS)
    assert reply.status_code == 200, reply.text
    payload = reply.json()["message"]["payload"]
    assert payload["read_tools"][0]["tool"] == "inspect_project_overview"
    assert payload["read_tools"][0]["status"] == "OK"
    assert payload["llm"]["native_tool_calling"] is False
    assert payload["llm"]["degraded_reason"] == "PROVIDER_WITHOUT_NATIVE_TOOL_CALLS"
    # The model's second turn saw the real receipt of the first.
    assert any("IMPORT_SCRIPT" in text for text in seen[1:])


def test_an_unreachable_model_is_named_not_faked(studio):
    client, factory, _, _ = studio
    created = client.post("/api/v1/provider-profiles", json={
        "name": "unreachable", "kind": "openai_compatible", "base_url": "http://127.0.0.1:9/v1",
        "model": "x", "enabled": True, "timeout_s": 1, "max_retries": 0,
    }, headers=HEADERS)
    assert created.status_code == 201, created.text
    assert client.put("/api/v1/agent-routes",
                      json={"role": "comfyops", "provider_profile_id": created.json()["id"]},
                      headers=HEADERS).status_code in {200, 201}
    session = client.post("/api/v1/support/sessions",
                          json={"page_id": "copilot", "question": ""}, headers=HEADERS).json()["session"]
    reply = client.post(f"/api/v1/support/sessions/{session['id']}/messages",
                        json={"content": "参考图最多几张"}, headers=HEADERS)
    assert reply.status_code == 200, reply.text
    llm = reply.json()["message"]["payload"]["llm"]
    assert llm["used"] is False
    assert llm["status"] in {"UNAVAILABLE", "TRUNCATED"}
    assert "【结论】" not in reply.json()["message"]["content"] or "不可用" in reply.json()["message"]["content"]


def test_the_system_prompt_demands_evidence_before_answers():
    prompt = chat_investigator.CHAT_SYSTEM_PROMPT
    assert agent_service.SYSTEM_PROMPT is prompt
    assert "先查证" in prompt
    assert "propose_action" in prompt and "propose_project_grant" in prompt
    assert "绝不执行命令" in prompt
    assert chat_investigator.DEFAULT_COMPLETION_TOKENS >= 2000

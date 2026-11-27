"""Remote-acceptance fixes: target project, parameter contracts, NL, delegation.

These tests are deliberately end-to-end at the API level: every executable step
still goes through preview → approval → the original executor → read-back.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select

from shine.models import Project, WorkflowProfile, utcnow
from shine.support import capability_catalog, contracts, project_knowledge
from shine.support.models import SupportAction, SupportSession
from tests.conftest import create_project

HEADERS = {"x-shine-local-action": "1"}


def _session(client, project_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project_id, "question": ""},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


def _preview(
    client,
    kind: str,
    params: dict,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
):
    return client.post(
        "/api/v1/support/actions/preview",
        json={"kind": kind, "params": params, "session_id": session_id, "project_id": project_id},
        headers=HEADERS,
    )


def _approve(client, action: dict):
    return client.post(
        f"/api/v1/support/actions/{action['id']}/approve",
        json={"preview_hash": action["preview_hash"], "confirm": True},
        headers=HEADERS,
    )


# ---------------------------------------------------------------------------
# P0-01 — one canonical target project
# ---------------------------------------------------------------------------


def test_p0_01_session_project_and_top_level_project_must_match(api):
    client, _, _ = api
    a = create_project(client, "项目甲")
    b = create_project(client, "项目乙")
    session = _session(client, a)
    response = _preview(
        client, "project_settings_update", {"language": "en"}, session_id=session["id"], project_id=b
    )
    assert response.status_code == 400
    assert "不一致" in response.json()["detail"]["message"]


def test_p0_01_top_level_project_never_wins_over_params_project(api):
    client, _, _ = api
    a = create_project(client, "项目甲")
    b = create_project(client, "项目乙")
    response = _preview(client, "project_settings_update", {"language": "en", "project_id": b}, project_id=a)
    assert response.status_code == 400
    assert "不一致" in response.json()["detail"]["message"]


def test_p0_01_matching_target_is_normalized_and_scoped(api):
    client, _, _ = api
    a = create_project(client, "项目甲")
    session = _session(client, a)
    response = _preview(
        client, "project_settings_update", {"language": "en", "project_id": a}, session_id=session["id"]
    )
    assert response.status_code == 200, response.text
    action = response.json()["action"]
    assert action["project_id"] == a
    assert action["preview"]["kind"] == "project_settings_update"


def test_p0_01_create_does_not_bind_the_current_project(api):
    client, factory, _ = api
    current = create_project(client, "当前项目")
    session = _session(client, current)
    response = _preview(
        client, "project_create", {"title": "雾港回声"}, session_id=session["id"], project_id=current
    )
    assert response.status_code == 200, response.text
    action = response.json()["action"]
    assert action["project_id"] is None
    approved = _approve(client, action)
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    titles = [row["title"] for row in client.get("/api/v1/projects").json()]
    assert "雾港回声" in titles
    with factory() as db:
        new = db.scalar(select(Project).where(Project.title == "雾港回声"))
        assert new is not None and new.id != current


def test_p0_01_historical_authorization_cannot_be_replayed_elsewhere(api):
    client, factory, _ = api
    a = create_project(client, "授权项目甲")
    b = create_project(client, "授权项目乙")
    session = _session(client, a)
    preview = _preview(
        client, "project_settings_update", {"language": "en"}, session_id=session["id"]
    ).json()["action"]
    with factory() as db:
        row = db.get(SupportSession, session["id"])
        row.project_id = b
        db.commit()
    response = _approve(client, preview)
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PROJECT_CONFLICT"
    with factory() as db:
        row = db.get(SupportAction, preview["id"])
        assert row.status == "EXPIRED"
        project = db.get(Project, a)
        assert project.language != "en"


def test_p0_01_document_actions_refuse_cross_project_targets(api):
    client, _, _ = api
    a = create_project(client, "文档项目甲")
    b = create_project(client, "文档项目乙")
    imported = _preview(
        client, "document_import_text", {"project_id": b, "filename": "乙.txt", "text": "乙的正文"}
    )
    assert imported.status_code == 200, imported.text
    document_id = _approve(client, imported.json()["action"]).json()["action"]["original_task_id"]
    response = _preview(client, "document_switch_basis", {"project_id": a, "document_id": document_id})
    assert response.status_code == 400
    assert "不属于" in response.json()["detail"]["message"]


def test_p0_01_private_workflow_copy_refuses_another_project(api):
    client, factory, _ = api
    a = create_project(client, "项目甲")
    b = create_project(client, "项目乙")
    with factory() as db:
        db.add(
            WorkflowProfile(
                id="wf-private-b",
                project_id=b,
                name="乙的私有工作流",
                mode="Ref2VA",
                graph={"1": {}},
                parameter_map={"positive_prompt": "1.inputs.text"},
                media_transfers=None,
                required_nodes=[],
                checkpoint_id="MiniMax-H3-Base",
                graph_sha256="a" * 64,
                model_identity={},
                validated_against=None,
            )
        )
        db.commit()
    response = _preview(
        client,
        "workflow_user_copy",
        {"source_profile_id": "wf-private-b", "name": "副本", "project_id": a},
        project_id=a,
    )
    assert response.status_code == 400
    assert "私有工作流" in response.json()["detail"]["message"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowProfile).where(WorkflowProfile.name == "副本")) == 0


# ---------------------------------------------------------------------------
# P0-02 — the registry is the parameter contract
# ---------------------------------------------------------------------------


def test_p0_02_every_registered_action_advertises_its_real_parameters():
    payload = capability_catalog.catalog_payload()
    by_id = {row["action_id"]: row for row in payload["capabilities"]}
    assert by_id["managed_release"]["params"] == ("only",)
    assert by_id["managed_release"]["scope"] == "MACHINE"
    assert "text" in by_id["document_import_text"]["params"]
    assert by_id["project_create"]["params"] == ("title", "description", "language")
    assert by_id["production_control"]["params"] == ("run_id", "operation")
    assert by_id["document_switch_basis"]["params"] == ("project_id", "document_id")
    assert by_id["literary_scripts_generate"]["params"] == ("project_id", "volume_id")
    assert by_id["production_start"]["scope"] == "PROJECT"
    statuses = {row["operation"]: row["ai_status"] for row in payload["matrix"]}
    assert statuses["切换正式底稿"] == "GUARDED"
    assert statuses["生成分集剧本"] == "GUARDED"
    assert statuses["暂停/继续/取消生产"] == "GUARDED"
    assert payload["matrix_totals"]["by_status"]["NOT_SUPPORTED"] >= 3


# ---------------------------------------------------------------------------
# Natural language → registered action → real preview
# ---------------------------------------------------------------------------


def test_p1_nl_creates_a_real_action_with_the_spoken_title(api):
    from tests.copilot_v4_support import configure_mock_text_model, finish_reply, scripted_responder, tool_reply

    client, factory, _ = api
    configure_mock_text_model(
        client,
        scripted_responder([
            tool_reply("propose_action", {
                "action_id": "project_create",
                "params": {"title": "雾港回声"},
                "reason": "用户要求创建短剧项目",
            }),
            finish_reply("已提出创建《雾港回声》的建议卡片。"),
        ]),
    )
    res = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "question": "建立一个名叫《雾港回声》的短剧项目。"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    proposal = res.json()["session"]["messages"][-1]["payload"]["proposals"][0]
    assert proposal["valid"] is True
    assert proposal["action_id"] == "project_create"
    assert proposal["params"]["title"] == "雾港回声"
    with factory() as db:
        assert db.query(SupportAction).count() == 0


def test_p1_nl_never_defaults_to_all_episodes(api):
    from tests.copilot_v4_support import configure_mock_text_model, finish_reply, scripted_responder, tool_reply

    client, _, _ = api
    project = create_project(client, "空项目")
    configure_mock_text_model(
        client,
        scripted_responder([
            tool_reply("propose_action", {
                "action_id": "production_start",
                "params": {"project_id": project},
                "reason": "未指定集数",
            }),
            finish_reply("请先指定要开拍的集数。"),
        ]),
    )
    res = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project, "question": "开始生成"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    proposal = res.json()["session"]["messages"][-1]["payload"]["proposals"][0]
    assert proposal["valid"] is False
    assert "episode_ids" in (proposal["error"] or "")


def test_p1_nl_language_and_title_are_not_invented(api):
    from tests.copilot_v4_support import configure_mock_text_model, finish_reply, scripted_responder, tool_reply

    client, _, _ = api
    configure_mock_text_model(
        client,
        scripted_responder([
            tool_reply("propose_action", {
                "action_id": "project_create",
                "params": {"language": "yue"},
                "reason": "用户没有说项目名",
            }),
            finish_reply("还需要你提供项目名称。"),
        ]),
    )
    res = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "question": "新建一个粤语项目"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    proposal = res.json()["session"]["messages"][-1]["payload"]["proposals"][0]
    assert proposal["valid"] is False
    assert "title" in (proposal["error"] or "")


def test_p1_import_plan_asks_for_the_users_own_text(api):
    from tests.copilot_v4_support import configure_mock_text_model, finish_reply, scripted_responder, tool_reply

    client, _, _ = api
    project = create_project(client, "导入项目")
    configure_mock_text_model(
        client,
        scripted_responder([
            tool_reply("propose_action", {
                "action_id": "document_import_text",
                "params": {"project_id": project},
                "reason": "缺少正文文本",
            }),
            finish_reply("请粘贴你要导入的正文文本。"),
        ]),
    )
    res = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project, "question": "把我刚才选择的文本导入这个项目"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    proposal = res.json()["session"]["messages"][-1]["payload"]["proposals"][0]
    assert proposal["valid"] is False
    assert "text" in (proposal["error"] or "")


def test_p1_single_project_ledger_resolves_this_project(api):
    from shine.copilot import read_tools

    client, factory, settings = api
    project = create_project(client, "唯一项目")
    with factory() as db:
        receipt = read_tools.execute(
            db, settings, name="inspect_project_overview",
            arguments={"project_id": project}, grant=None, scope_project_id=project,
        )
        assert receipt["status"] == "OK"
        assert receipt["observed_facts"]["project_id"] == project


def test_p1_two_projects_stay_ambiguous(api):
    from shine.copilot import read_tools
    from shine.copilot import task_grant as grant_contract
    import pytest

    client, factory, settings = api
    first = create_project(client, "项目甲")
    second = create_project(client, "项目乙")
    with factory() as db:
        with pytest.raises(grant_contract.GrantError) as exc:
            read_tools.execute(
                db, settings, name="inspect_project_overview",
                arguments={"project_id": second}, grant=None, scope_project_id=first,
            )
        assert exc.value.code == "SCOPE_EXCEEDED"


def test_p1_import_uses_the_formal_import_executor(api):
    client, _, _ = api
    project = create_project(client, "长篇小说")
    preview = _preview(
        client,
        "document_import_text",
        {"project_id": project, "filename": "雾港回声.txt", "text": "第一章 雾港\n\n夜色压在海面上。"},
    )
    assert preview.status_code == 200, preview.text
    result = _approve(client, preview.json()["action"]).json()["action"]
    assert result["status"] == "SUCCEEDED"
    documents = client.get(f"/api/v1/projects/{project}/documents").json()
    assert [row["filename"] for row in documents] == ["雾港回声.txt"]


def test_p1_named_episode_in_an_empty_project_is_reported_as_a_gate(api):
    from shine.copilot import read_tools

    client, factory, settings = api
    project = create_project(client, "空项目")
    with factory() as db:
        receipt = read_tools.execute(
            db, settings, name="inspect_project_overview",
            arguments={"project_id": project}, grant=None, scope_project_id=project,
        )
    facts = receipt["observed_facts"]
    assert facts["episodes"] == []
    assert facts["next_action"]["kind"] == "IMPORT_SCRIPT"
    health = client.get("/api/v1/support/health-check", params={"project_id": project}).json()
    storyboard = next(g for g in health["groups"] if g["key"] == "storyboard")
    assert any(item["code"] == "SCRIPT_MISSING" for item in storyboard["items"])


# ---------------------------------------------------------------------------
# Controlled multi-step authorization via V4 task_grants + tool_loop
# ---------------------------------------------------------------------------


def _grant_preview(client, session_id: str, objective: str, *, groups: list[str] | None = None,
                   project_id: str | None = None):
    return client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session_id,
            "objective": objective,
            "project_id": project_id,
            "capability_groups": groups or ["inspect", "workflows"],
        },
        headers=HEADERS,
    )


def test_p1_delegation_creates_the_project_then_stops(api):
    """V4: retired /plans/delegate stays 404; single-step action creates the project once and stops."""
    client, _, _ = api
    session = _session(client)
    assert client.post("/api/v1/support/plans/delegate", json={"session_id": session["id"]}, headers=HEADERS).status_code == 404
    preview = _preview(client, "project_create", {"title": "雾港回声"}, session_id=session["id"])
    assert preview.status_code == 200, preview.text
    approved = _approve(client, preview.json()["action"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    titles = [row["title"] for row in client.get("/api/v1/projects").json()]
    assert "雾港回声" in titles


def test_p1_delegation_requires_explicit_confirmation(api):
    client, _, _ = api
    session = _session(client)
    preview = _grant_preview(client, session["id"], "检查环境并装配工作流").json()
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


def test_p1_high_cost_production_pauses_without_a_budget(api):
    from shine.support import actions as action_service
    from shine.support import grants as grant_service

    client, factory, settings = api
    project = create_project(client, "预算项目")
    session = _session(client, project)
    with factory() as db:
        session_row = db.get(SupportSession, session["id"])
        loaded, _ = grant_service.create(
            db, session=session_row, actor="local-user",
            objective="拍摄第一集", groups=["inspect"], project_ids=[project],
        )
        receipt = action_service.execute_under_grant(
            db, settings, grant_id=loaded.id, capability_id="production_start",
            params={"project_id": project, "episode_ids": ["ep-1"]},
            tool_call_id="tc-prod-1", actor="local-user", session_id=session["id"],
        )
        assert receipt["status"] == "BLOCKED"
        assert receipt["error_code"] == "GRANT_PERMISSION_NOT_GRANTED"


def test_p1_delegation_scope_cannot_be_widened(api):
    from shine.support import actions as action_service
    from shine.support import grants as grant_service

    client, factory, settings = api
    a = create_project(client, "范围项目甲")
    b = create_project(client, "范围项目乙")
    session = _session(client, a)
    with factory() as db:
        session_row = db.get(SupportSession, session["id"])
        loaded, _ = grant_service.create(
            db, session=session_row, actor="local-user",
            objective="检查项目甲", groups=["inspect", "workflows"], project_ids=[a],
        )
        receipt = action_service.execute_under_grant(
            db, settings, grant_id=loaded.id, capability_id="workflow_user_copy",
            params={"project_id": b, "source_profile_id": "wf-x", "name": "越权副本"},
            tool_call_id="tc-scope-1", actor="local-user", session_id=session["id"],
        )
        assert receipt["status"] == "BLOCKED"
        assert receipt["error_code"] == "SCOPE_EXCEEDED"


def test_p1_cancel_does_not_touch_submitted_steps(api):
    client, factory, _ = api
    session = _session(client)
    preview = _preview(client, "project_create", {"title": "已提交项目"}, session_id=session["id"]).json()["action"]
    assert _approve(client, preview).json()["action"]["status"] == "SUCCEEDED"
    grant_prev = _grant_preview(client, session["id"], "检查环境").json()
    auth = client.post(
        "/api/v1/support/task-grants/authorize",
        json={
            "session_id": session["id"],
            "preview_id": grant_prev["preview_id"],
            "grant_digest": grant_prev["grant_digest"],
            "confirm": True,
            "run": False,
        },
        headers=HEADERS,
    ).json()
    revoked = client.post(f"/api/v1/support/task-grants/{auth['grant_id']}/revoke", headers=HEADERS)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    with factory() as db:
        row = db.get(SupportAction, preview["id"])
        assert row.status == "SUCCEEDED"


def test_p1_delegation_resumes_from_the_stored_envelope(api):
    from shine.copilot import tool_loop, tool_registry
    from shine.llm.tool_calls import ToolTurn
    from shine.support import grants as grant_service
    from tests.copilot_v4_support import ScriptedGateway

    client, factory, settings = api
    session = _session(client)
    with factory() as db:
        session_row = db.get(SupportSession, session["id"])
        loaded, _ = grant_service.create(
            db, session=session_row, actor="local-user",
            objective="只读恢复测试", groups=["inspect"],
        )
        grant_id = loaded.id
    first = ScriptedGateway([
        ToolTurn(text="", tool_calls=[], native=False),
    ])
    with factory() as db:
        # Seed a real persisted transcript step and resume from another owner.
        loaded = grant_service.load(db, grant_id)
        grant_service.save_loop(db, grant_id, {
            "messages": [{"role": "user", "content": "继续"}],
            "steps": [{"capability_id": "inspect_environment", "satisfied": True}],
            "started_at": utcnow().isoformat(),
        })
        res = tool_loop.advance(
            db, settings, grant_id=grant_id, objective="只读恢复测试",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session["id"], gateway=first, max_steps=4, owner="worker:resumed",
        )
    assert res.stop_reason == "MODEL_FINISHED_WITH_EVIDENCE"
    with factory() as db:
        state = grant_service.loop_state(grant_service.load(db, grant_id).grant)
        assert len(state["steps"]) == 1


def test_p1_unknown_task_is_reported_but_never_resubmitted(api):
    client, factory, _ = api
    project = create_project(client, "未知项目")
    session = _session(client, project)
    seeded = _preview(client, "project_settings_update", {"language": "en"}, session_id=session["id"]).json()["action"]
    with factory() as db:
        row = db.get(SupportAction, seeded["id"])
        row.kind = "story_outline_generate"
        row.status = "DELEGATED"
        row.original_task_id = "task-that-is-gone"
        row.params = {"project_id": project}
        db.commit()
        before = db.scalar(select(func.count()).select_from(SupportAction))
    refreshed = client.get(f"/api/v1/support/actions/{seeded['id']}").json()["action"]
    assert refreshed["status"] == "NEEDS_REVIEW"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(SupportAction)) == before
        row = db.get(SupportAction, seeded["id"])
        assert row.status == "NEEDS_REVIEW"
        assert row.original_task_id == "task-that-is-gone"


def test_p1_duplicate_preview_is_adopted_not_duplicated(api):
    client, factory, _ = api
    payload = {"kind": "project_create", "params": {"title": "幂等项目"}}
    first = client.post("/api/v1/support/actions/preview", json=payload, headers=HEADERS)
    second = client.post("/api/v1/support/actions/preview", json=payload, headers=HEADERS)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["reused"] is True
    assert first.json()["action"]["id"] == second.json()["action"]["id"]
    with factory() as db:
        assert db.scalar(
            select(func.count()).select_from(SupportAction).where(SupportAction.kind == "project_create")
        ) == 1


# ---------------------------------------------------------------------------
# Parameter tuning vs production
# ---------------------------------------------------------------------------


def test_p1_tuning_never_becomes_a_production_start(api):
    client, _, _ = api
    project = create_project(client, "调参项目")
    response = client.post(
        "/api/v1/support/parameters/preview",
        json={"target": "production_strategy:", "values": {"second_pass.enabled": True}, "project_id": project},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    preview = response.json()["parameter"]["preview"]
    assert preview["proposed_action"] is None
    assert response.json()["action"] is None
    assert preview["apply_via"] == "production_start"
    assert preview["scope"] == "NEXT_PRODUCTION_ONLY"
    assert "待使用策略" in preview["notes"]


# ---------------------------------------------------------------------------
# Knowledge build binding + new actions
# ---------------------------------------------------------------------------


def _run_manifest(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(repo / "backend")}
    return subprocess.run(
        [sys.executable, str(repo / "backend/scripts/generate_copilot_knowledge.py"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_p1_knowledge_manifest_is_bound_by_the_build_script(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    manifest = tmp_path / "knowledge_manifest.json"
    commit = "a" * 40
    written = _run_manifest(repo, "--commit", commit, "--output", str(manifest))
    assert written.returncode == 0, written.stderr
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_commit"] == commit
    assert payload["content_sha256"] == project_knowledge._content_digest()

    fresh = project_knowledge.index_metadata(commit, manifest_path=manifest)
    assert fresh["state"] == "FRESH"
    stale = project_knowledge.index_metadata("b" * 40, manifest_path=manifest)
    assert stale["state"] == "STALE"
    unknown = project_knowledge.index_metadata(commit, manifest_path=tmp_path / "missing.json")
    assert unknown["state"] == "UNKNOWN"

    checked = _run_manifest(repo, "--commit", commit, "--output", str(manifest), "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    broken = json.loads(manifest.read_text(encoding="utf-8"))
    broken["content_sha256"] = "0" * 64
    manifest.write_text(json.dumps(broken), encoding="utf-8")
    assert _run_manifest(repo, "--commit", commit, "--output", str(manifest), "--check").returncode == 1


def test_p1_new_actions_are_registered_and_bounded(api):
    client, _, _ = api
    for kind in ("document_switch_basis", "production_control", "literary_scripts_generate"):
        assert kind in {row.action_id for row in capability_catalog.CAPABILITIES}
    project = create_project(client, "新动作项目")
    response = _preview(client, "production_control", {"run_id": "missing", "operation": "pause"}, project_id=project)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_ACTION_PARAMS"

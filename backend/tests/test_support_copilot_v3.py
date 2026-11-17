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
from shine.support import capability_catalog, project_knowledge
from shine.support.models import SupportAction, SupportSession
from tests.conftest import create_project

HEADERS = {"x-shine-local-action": "1"}


def _session(client, project_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project_id, "question": "你好"},
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


def _plan(client, request: str, *, project_id: str | None = None, session_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/support/plans/preview",
        json={"request": request, "project_id": project_id, "session_id": session_id},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["plan"]


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
    client, _, _ = api
    plan = _plan(client, "建立一个名叫《雾港回声》的短剧项目。")
    assert plan["intent"] == "CREATE_PROJECT"
    assert plan["requires_input"] == []
    step = plan["steps"][0]
    assert step["state"] == "READY"
    assert step["action"]["kind"] == "project_create"
    assert step["action"]["params"]["title"] == "雾港回声"


def test_p1_nl_never_defaults_to_all_episodes(api):
    client, _, _ = api
    project = create_project(client, "空项目")
    plan = _plan(client, "开始生成", project_id=project)
    assert plan["requires_input"] == ["episode_ids"]
    assert not any((step.get("action") or {}).get("kind") == "production_start" for step in plan["steps"])


def test_p1_nl_language_and_title_are_not_invented(api):
    client, _, _ = api
    plan = _plan(client, "新建一个粤语项目")
    step = plan["steps"][0]
    assert step["action"]["params"]["language"] == "yue"
    assert "title" not in step["action"]["params"]
    assert plan["requires_input"] == ["title"]


def test_p1_import_plan_asks_for_the_users_own_text(api):
    client, _, _ = api
    project = create_project(client, "导入项目")
    session = _session(client, project)
    plan = _plan(client, "把我刚才选择的文本导入这个项目", session_id=session["id"])
    assert plan["intent"] == "IMPORT_DOCUMENT"
    assert plan["requires_input"] == ["text"]
    step = plan["steps"][0]
    assert step["state"] == "HUMAN_REQUIRED"
    assert step["action"]["params"]["project_id"] == project


def test_p1_single_project_ledger_resolves_this_project(api):
    client, _, _ = api
    project = create_project(client, "唯一项目")
    session = _session(client)  # created before/without a project binding
    plan = _plan(client, "把这个项目里我选的文本导入", session_id=session["id"])
    step = plan["steps"][0]
    assert plan["project_id"] == project
    assert step["action"]["params"]["project_id"] == project
    assert step["state"] == "HUMAN_REQUIRED"


def test_p1_two_projects_stay_ambiguous(api):
    client, _, _ = api
    create_project(client, "项目甲")
    create_project(client, "项目乙")
    session = _session(client)
    plan = _plan(client, "把这个项目里我选的文本导入", session_id=session["id"])
    assert plan["requires_input"] == ["project_id", "text"]
    assert plan["steps"][0]["state"] == "BLOCKED"


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
    client, _, _ = api
    project = create_project(client, "空项目")
    plan = _plan(client, "检查第一集是否可以开拍。", project_id=project)
    assert plan["requires_input"] == []
    assert "还没有分集" in plan["summary"]
    step = plan["steps"][-1]
    assert step["state"] == "BLOCKED"
    assert "先" in step["reason"]
    assert not any(item.get("action") for item in plan["steps"])


# ---------------------------------------------------------------------------
# Controlled multi-step delegation (no second executor)
# ---------------------------------------------------------------------------


def _delegate(client, session_id: str, orders: list[int], budget: dict | None = None):
    return client.post(
        "/api/v1/support/plans/delegate",
        json={
            "session_id": session_id,
            "approved_orders": orders,
            "budget": budget or {"max_episodes": 1, "max_gpu_actions": 0, "max_paid_actions": 1},
            "confirm": True,
        },
        headers=HEADERS,
    )


def test_p1_delegation_creates_the_project_then_stops(api):
    client, _, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《雾港回声》的短剧项目。", session_id=session["id"])
    response = _delegate(client, session["id"], [plan["steps"][0]["order"]])
    assert response.status_code == 200, response.text
    delegation = response.json()["delegation"]
    assert delegation["status"] == "DONE"
    assert delegation["steps"][0]["state"] == "SUCCEEDED"
    titles = [row["title"] for row in client.get("/api/v1/projects").json()]
    assert "雾港回声" in titles


def test_p1_delegation_requires_explicit_confirmation(api):
    client, _, _ = api
    session = _session(client)
    _plan(client, "建立一个名叫《雾港回声》的短剧项目。", session_id=session["id"])
    response = client.post(
        "/api/v1/support/plans/delegate",
        json={"session_id": session["id"], "approved_orders": [1], "confirm": False},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"


def _seed_plan(factory, session_id: str, steps: list[dict], *, project_id: str | None = None):
    with factory() as db:
        row = db.get(SupportSession, session_id)
        row.context_json = {
            **(row.context_json or {}),
            "last_plan": {
                "intent": "PRODUCTION",
                "summary": "seeded",
                "project_id": project_id,
                "episode_ids": [],
                "steps": steps,
                "requires_input": [],
                "notice": "seeded",
                "bounded": True,
            },
        }
        db.commit()


def test_p1_high_cost_production_pauses_without_a_budget(api):
    client, factory, _ = api
    project = create_project(client, "预算项目")
    session = _session(client, project)
    _seed_plan(
        factory,
        session["id"],
        [
            {
                "order": 1,
                "title": "开始真实拍摄",
                "state": "READY",
                "action": {
                    "kind": "production_start",
                    "params": {"project_id": project, "episode_ids": ["ep-1"]},
                },
            }
        ],
        project_id=project,
    )
    response = _delegate(client, session["id"], [1], {"max_episodes": 1, "max_gpu_actions": 0})
    assert response.status_code == 200, response.text
    delegation = response.json()["delegation"]
    assert delegation["status"] == "PAUSED"
    assert delegation["stop_reason"] == "BUDGET_REQUIRED"
    assert delegation["steps"][0]["action_id"] is None


def test_p1_delegation_scope_cannot_be_widened(api):
    client, factory, _ = api
    a = create_project(client, "范围项目甲")
    b = create_project(client, "范围项目乙")
    session = _session(client, a)
    _seed_plan(
        factory,
        session["id"],
        [
            {
                "order": 1,
                "title": "改设置",
                "state": "READY",
                "action": {"kind": "project_settings_update", "params": {"project_id": b, "language": "en"}},
            }
        ],
        project_id=a,
    )
    response = _delegate(client, session["id"], [1])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SCOPE_EXCEEDED"


def test_p1_cancel_does_not_touch_submitted_steps(api):
    client, factory, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《雾港回声》的短剧项目。", session_id=session["id"])
    created = _delegate(client, session["id"], [plan["steps"][0]["order"]]).json()["delegation"]
    response = client.post(f"/api/v1/support/sessions/{session['id']}/delegation/cancel", headers=HEADERS)
    assert response.status_code == 200, response.text
    cancelled = response.json()["delegation"]
    assert cancelled["status"] == "CANCELLED"
    with factory() as db:
        row = db.get(SupportAction, created["steps"][0]["action_id"])
        assert row.status == "SUCCEEDED"


def _write_delegation(factory, session_id, *, steps, waiting_action_id=None, status="WAITING"):
    with factory() as db:
        row = db.get(SupportSession, session_id)
        row.context_json = {
            **(row.context_json or {}),
            "delegation": {
                "id": "d-resume",
                "status": status,
                "created_at": utcnow().isoformat(),
                "updated_at": utcnow().isoformat(),
                "project_id": None,
                "episode_ids": [],
                "budget": {"max_episodes": 1, "max_gpu_actions": 0, "max_paid_actions": 0},
                "gpu_actions_used": 0,
                "waiting_action_id": waiting_action_id,
                "stop_reason": None,
                "steps": steps,
            },
        }
        db.commit()


def test_p1_delegation_resumes_from_the_stored_envelope(api):
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "已完成的创建"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=seeded["id"],
        steps=[
            {
                "order": 1,
                "title": "已完成的创建",
                "kind": "project_create",
                "params": {"title": "已完成的创建"},
                "plan_state": "READY",
                "state": "WAITING",
                "action_id": seeded["id"],
                "message": "",
            },
            {
                "order": 2,
                "title": "续跑创建",
                "kind": "project_create",
                "params": {"title": "续跑项目"},
                "plan_state": "READY",
                "state": "PENDING",
                "action_id": None,
                "message": "",
            },
        ],
    )
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    delegation = response.json()["delegation"]
    assert delegation["status"] == "DONE"
    assert [step["state"] for step in delegation["steps"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert "续跑项目" in [row["title"] for row in client.get("/api/v1/projects").json()]


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
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=seeded["id"],
        steps=[
            {
                "order": 1,
                "title": "生成故事大纲",
                "kind": "story_outline_generate",
                "params": {"project_id": project},
                "plan_state": "READY",
                "state": "WAITING",
                "action_id": seeded["id"],
                "message": "",
            }
        ],
    )
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    delegation = response.json()["delegation"]
    assert delegation["status"] == "PAUSED"
    assert delegation["stop_reason"] == "NEEDS_REVIEW"
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
    plan = _plan(client, "暂停当前生产任务")
    assert plan["requires_input"] == []
    assert plan["intent"] == "CONTROL"

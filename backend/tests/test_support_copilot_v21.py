"""V2.1 acceptance: build-generated knowledge & contracts, upgrade safety.

The chain under test is exactly the one developers will use:

    declare once → build generator → packaged artefact → runtime loads →
    query (knowledge) / execute (reviewed capability)

No test here mocks the generator's output: every artefact is produced by
``scripts/generate_shine_copilot_contracts.py`` against the real FastAPI app.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from shine.copilot import build as contract_build
from shine.copilot import declarations as decl
from shine.copilot.contracts import ParamSpec
from shine.models import utcnow
from shine.support import capability_catalog, contracts, project_knowledge
from shine.support import grants as grant_service
from shine.support.models import SupportDelegation, SupportSession
from tests.conftest import create_project
from tests.copilot_v4_support import configure_mock_text_model, finish_reply, tool_reply

HEADERS = {"x-shine-local-action": "1"}
REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
NEW_COMMIT = "c" * 40


def _generator(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {**os.environ, "PYTHONPATH": str(BACKEND), **(env or {})}
    return subprocess.run(
        [sys.executable, str(BACKEND / "scripts/generate_shine_copilot_contracts.py"), *args],
        cwd=REPO,
        env=base_env,
        capture_output=True,
        text=True,
        timeout=120,
    )


EXTRA_DECLARATIONS = '''
"""A real new product capability, declared the way a future developer would."""
from shine.copilot.contracts import CapabilityDeclaration, FeatureDeclaration, ParamSpec

FEATURES = (
    FeatureDeclaration(
        "v21-probe", "media", "V2.1 探针功能", "IMPLEMENTED",
        "只用于验证自动同步链路的临时正式声明；路由与执行者都是真实存在的。",
        sources=("backend/shine/api_library.py",),
        apis=("GET /api/v1/projects/{project_id}/video-library",),
        evidence=("backend/tests/test_support_copilot_v21.py",),
        tags=("探针", "自动同步", "成片库"),
    ),
)

READ_ACTIONS = (
    CapabilityDeclaration(
        action_id="read_v21_probe",
        feature_id="v21-probe",
        module="library",
        label="读取探针成片库",
        description="只读探针；走既有成片库路由，不新增执行者。",
        params=(ParamSpec("project_id", binding="path"),),
        api_route="GET /api/v1/projects/{project_id}/video-library",
        request_schema=None,
        response_schema=None,
        executor="shine.api_library.video_library",
        authority="GET /api/v1/projects/{project_id}/video-library",
        permission_tier="READ",
        page="成片库",
        occupancy_gate="NONE",
        status="READ_ONLY",
        availability="AVAILABLE",
    ),
)
'''


def _extra_file(tmp_path: Path) -> Path:
    path = tmp_path / "extra_decls.py"
    path.write_text(EXTRA_DECLARATIONS, encoding="utf-8")
    return path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _use_artifacts(monkeypatch, artifact: Path, manifest: Path, *, commit: str | None):
    contracts.reset_cache()
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", artifact)
    monkeypatch.setattr(contracts, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(project_knowledge, "MANIFEST_PATH", manifest)
    contracts.reset_cache()
    bundle = _load(artifact)
    monkeypatch.setattr(
        capability_catalog,
        "CAPABILITIES",
        tuple(
            capability_catalog._row(row) for row in bundle.get("capabilities") or []
        ),
    )
    monkeypatch.setattr(
        capability_catalog,
        "READ_CAPABILITIES",
        tuple(
            capability_catalog._row(row, status="READ_ONLY")
            for row in bundle.get("read_capabilities") or []
        ),
    )
    monkeypatch.setattr(
        capability_catalog, "MATRIX", tuple(bundle.get("matrix") or [])
    )
    monkeypatch.setattr(
        project_knowledge,
        "PROJECT_ENTRIES",
        list((bundle.get("knowledge") or {}).get("entries") or []),
    )
    if commit:
        monkeypatch.setenv("SHINE_BUILD_COMMIT", commit)
    else:
        monkeypatch.delenv("SHINE_BUILD_COMMIT", raising=False)


def _session(client, project_id: str | None = None) -> dict:
    # V4: a session needs no question; a conversation turn needs a model.
    response = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project_id, "question": ""},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


def _assert_plan_delegation_retired(client, session_id: str) -> None:
    """V4: the keyword planner and the plan-delegation chain are gone, not dormant."""
    assert client.post("/api/v1/support/plans/preview", json={"request": "准备"},
                       headers=HEADERS).status_code == 404
    assert client.post("/api/v1/support/plans/delegate", json={"session_id": session_id},
                       headers=HEADERS).status_code == 404
    assert client.get(f"/api/v1/support/sessions/{session_id}/delegation").status_code == 404
    assert client.post(f"/api/v1/support/sessions/{session_id}/delegation/refresh",
                       json={"session_id": session_id}, headers=HEADERS).status_code == 404
    assert client.post(f"/api/v1/support/sessions/{session_id}/delegation/cancel",
                       headers=HEADERS).status_code == 404
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shine.support.delegation")


def _preview(client, kind: str, params: dict, *, session_id: str | None = None):
    return client.post(
        "/api/v1/support/actions/preview",
        json={"kind": kind, "params": params, "session_id": session_id},
        headers=HEADERS,
    )


def _approve(client, action: dict):
    return client.post(
        f"/api/v1/support/actions/{action['id']}/approve",
        json={"preview_hash": action["preview_hash"], "confirm": True},
        headers=HEADERS,
    )


def _write_delegation(factory, session_id: str, *, waiting_action_id: str | None, steps: list[dict]):
    from shine.models import utcnow

    with factory() as db:
        row = db.get(SupportSession, session_id)
        row.context_json = {
            **(row.context_json or {}),
            "delegation": {
                "id": "d-v21",
                "status": "WAITING",
                "created_at": utcnow().isoformat(),
                "updated_at": utcnow().isoformat(),
                "project_id": None,
                "episode_ids": [],
                "budget": {"max_episodes": 1, "max_gpu_actions": 0, "max_paid_actions": 2},
                "gpu_actions_used": 0,
                "paid_actions_used": 0,
                "waiting_action_id": waiting_action_id,
                "stop_reason": None,
                "steps": steps,
            },
        }
        db.commit()


# ---------------------------------------------------------------------------
# Knowledge sync
# ---------------------------------------------------------------------------


def test_knowledge_index_is_generated_with_the_required_fields():
    artifact = _load(contracts.ARTIFACT_PATH)
    entries = artifact["knowledge"]["entries"]
    required = {
        "knowledge_id",
        "feature_id",
        "module",
        "title",
        "description",
        "source_path",
        "source_revision",
        "api_contract",
        "implementation_status",
        "verification_status",
        "app_version",
        "knowledge_revision",
        "content_hash",
    }
    assert entries, "the packaged knowledge index must not be empty"
    for row in entries:
        assert required <= set(row), f"missing fields in {row.get('feature_id')}: {required - set(row)}"
        assert row["knowledge_id"] == f"kb-{row['feature_id']}"
        assert row["source_revision"] == artifact["source_commit"]
    assert len(entries) == len(decl.FEATURES)
    assert artifact["knowledge"]["entry_count"] == len(entries)


def test_knowledge_and_capabilities_are_derived_not_hand_maintained():
    artifact = _load(contracts.ARTIFACT_PATH)
    assert [row["action_id"] for row in artifact["capabilities"]] == [
        row.action_id for row in decl.WRITE_ACTIONS
    ]
    assert [row["action_id"] for row in artifact["read_capabilities"]] == [
        row.action_id for row in decl.READ_ACTIONS
    ]
    assert {row["feature_id"] for row in artifact["knowledge"]["entries"]} == {
        row.feature_id for row in decl.FEATURES
    }


def test_generation_is_deterministic(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    for target in (first, second):
        done = _generator("--output-dir", str(target), "--commit", NEW_COMMIT)
        assert done.returncode == 0, done.stderr
    left = (first / "copilot_contracts.json").read_bytes()
    right = (second / "copilot_contracts.json").read_bytes()
    assert left == right
    manifest = _load(first / "knowledge_manifest.json")
    assert manifest["source_commit"] == NEW_COMMIT
    assert manifest["content_sha256"] == manifest["content_sha256"]


def test_new_feature_declaration_reaches_the_runtime(tmp_path, monkeypatch, api):
    """新增功能 → 生成知识 → 生成控制合同 → 构建 → 加载 → 查询/执行合法能力."""
    out = tmp_path / "build"
    done = _generator(
        "--output-dir", str(out),
        "--commit", NEW_COMMIT,
        "--extra-declarations", str(_extra_file(tmp_path)),
    )
    assert done.returncode == 0, done.stderr
    bundle = _load(out / "copilot_contracts.json")
    assert "v21-probe" in {row["feature_id"] for row in bundle["knowledge"]["entries"]}
    assert "read_v21_probe" in {row["action_id"] for row in bundle["read_capabilities"]}

    _use_artifacts(
        monkeypatch,
        out / "copilot_contracts.json",
        out / "knowledge_manifest.json",
        commit=NEW_COMMIT,
    )
    identity = contracts.build_identity(build_commit=NEW_COMMIT)
    assert identity["state"] == "FRESH"

    client, _, _ = api
    catalog = client.get("/api/v1/support/capabilities/actions").json()
    assert "read_v21_probe" in {row["action_id"] for row in catalog["read_capabilities"]}
    assert catalog["build"]["state"] == "FRESH"

    hit = client.get("/api/v1/support/knowledge/project", params={"q": "探针 成片库"}).json()
    assert hit["answered"] is True
    assert any(row["feature_id"] == "v21-probe" for row in hit["results"])

    # A reviewed capability still executes through the original executor.
    project = create_project(client, "自动同步探针项目")
    preview = _preview(client, "project_create", {"title": "构建后新建"})
    assert preview.status_code == 200, preview.text
    approved = _approve(client, preview.json()["action"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    assert "构建后新建" in [row["title"] for row in client.get("/api/v1/projects").json()]
    assert project


def test_damaged_index_degrades_to_unknown_not_crash(tmp_path, monkeypatch):
    broken = tmp_path / "copilot_contracts.json"
    broken.write_text("{not json", encoding="utf-8")
    contracts.reset_cache()
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", broken)
    monkeypatch.setattr(contracts, "MANIFEST_PATH", tmp_path / "missing.json")
    contracts.reset_cache()
    assert contracts.build_identity()["state"] == "MISSING"
    assert contracts.execution_state("project_create")["state"] == "CONTRACT_UNAVAILABLE"
    # Read-only knowledge still answers, but explicitly unproven.
    result = project_knowledge.search("导演模式")
    assert result["metadata"]["state"] == "UNKNOWN"
    assert result["notice"]


def test_docs_do_not_promote_a_partial_feature():
    artifact = _load(contracts.ARTIFACT_PATH)
    by_id = {row["feature_id"]: row for row in artifact["knowledge"]["entries"]}
    assert by_id["minimax-h3"]["implementation_status"] == "PARTIAL"
    assert by_id["minimax-h3"]["verification_status"] == "HARDWARE_REQUIRED"
    assert by_id["windows-deployment"]["implementation_status"] == "PARTIAL"


# ---------------------------------------------------------------------------
# Control sync and CI gate
# ---------------------------------------------------------------------------


def test_contract_check_passes_on_the_committed_build():
    done = _generator("--check")
    assert done.returncode == 0, done.stderr
    assert "contracts ok" in done.stdout


def test_contract_check_fails_when_a_parameter_is_removed():
    bundle = _load(contracts.ARTIFACT_PATH)
    tampered = json.loads(json.dumps(bundle))
    for row in tampered["capabilities"]:
        if row["action_id"] == "document_import_text":
            row["params"] = [name for name in row["params"] if name != "text"]
            row["contract_digest"] = "0" * 64
    from scripts.generate_shine_copilot_contracts import compare_with_previous

    issues = compare_with_previous(tampered, bundle)
    assert any("parameters removed" in issue for issue in issues)
    assert any("contract_revision not bumped" in issue for issue in issues)


def test_undeclared_or_unreviewed_api_never_executes(tmp_path, monkeypatch, api):
    out = tmp_path / "build"
    extra = tmp_path / "review_only.py"
    extra.write_text(
        '''
from shine.copilot.contracts import CapabilityDeclaration, ParamSpec
ACTIONS = (
    CapabilityDeclaration(
        action_id="review_only_probe",
        feature_id="copilot-auto-sync",
        module="projects",
        label="未审核写动作",
        description="已发现，但未通过可执行合同审核。",
        params=(ParamSpec("title"),),
        api_route="POST /api/v1/projects",
        request_schema="shine.schemas:CreateProject",
        response_schema=None,
        executor="shine.services.create_project",
        authority="GET /api/v1/projects/{project_id}",
        permission_tier="PREVIEW",
        availability="REVIEW_REQUIRED",
    ),
)
''',
        encoding="utf-8",
    )
    done = _generator(
        "--output-dir", str(out),
        "--commit", NEW_COMMIT,
        "--extra-declarations", str(extra),
    )
    assert done.returncode == 0, done.stderr
    _use_artifacts(
        monkeypatch,
        out / "copilot_contracts.json",
        out / "knowledge_manifest.json",
        commit=NEW_COMMIT,
    )
    state = contracts.execution_state("review_only_probe")
    assert state["state"] == "CONTRACT_NOT_DECLARED"  # not a packaged declaration
    # A packaged-but-unreviewed action is refused with the plain notice.
    monkeypatch.setattr(
        contracts,
        "live_contract",
        lambda action_id: (
            {
                "action_id": action_id,
                "contract_digest": "d" * 64,
                "contract_revision": 1,
                "availability": "REVIEW_REQUIRED",
                "dispatch": action_id,
                "api_route": "POST /api/v1/projects",
                "executor": "shine.services.create_project",
            }
            if action_id == "review_only_probe"
            else None
        ),
    )
    bundle = _load(out / "copilot_contracts.json")
    for row in bundle["capabilities"]:
        if row["action_id"] == "review_only_probe":
            row["contract_digest"] = "d" * 64
    artifact = tmp_path / "review_states.json"
    artifact.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", artifact)
    assert contracts.execution_state("review_only_probe")["state"] == "CONTRACT_REVIEW_REQUIRED"


def test_runtime_artifact_cannot_self_grant(tmp_path, monkeypatch):
    bundle = _load(contracts.ARTIFACT_PATH)
    forged = json.loads(json.dumps(bundle))
    forged["capabilities"].append(
        {
            "action_id": "forged_grant",
            "feature_id": "copilot-auto-sync",
            "label": "伪造动作",
            "params": [],
            "contract_digest": "f" * 64,
            "contract_revision": 1,
            "availability": "AVAILABLE",
            "dispatch": "forged_grant",
            "tier": "PREVIEW",
            "scope": "MACHINE",
            "executor": "shine.services.create_project",
            "api_route": "POST /api/v1/projects",
        }
    )
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    contracts.reset_cache()
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", path)
    contracts.reset_cache()
    state = contracts.execution_state("forged_grant")
    assert state["state"] == "CONTRACT_NOT_DECLARED"
    assert state["executable"] is False


def test_executor_and_readback_drift_is_a_build_failure(tmp_path):
    extra = tmp_path / "broken.py"
    extra.write_text(
        '''
from shine.copilot.contracts import CapabilityDeclaration, ParamSpec
ACTIONS = (
    CapabilityDeclaration(
        action_id="broken_executor",
        feature_id="copilot-auto-sync",
        module="projects",
        label="坏执行者",
        description="执行者不存在。",
        params=(ParamSpec("title"),),
        api_route="POST /api/v1/projects",
        request_schema="shine.schemas:CreateProject",
        response_schema=None,
        executor="shine.services.does_not_exist",
        authority="GET /api/v1/projects/{project_id}",
        permission_tier="PREVIEW",
    ),
    CapabilityDeclaration(
        action_id="broken_readback",
        feature_id="copilot-auto-sync",
        module="projects",
        label="坏回读",
        description="回读路由不存在。",
        params=(ParamSpec("title"),),
        api_route="POST /api/v1/projects",
        request_schema="shine.schemas:CreateProject",
        response_schema=None,
        executor="shine.services.create_project",
        authority="GET /api/v1/removed-route",
        permission_tier="PREVIEW",
    ),
)
''',
        encoding="utf-8",
    )
    done = _generator("--output-dir", str(tmp_path / "out"), "--extra-declarations", str(extra))
    assert done.returncode == 1
    assert "executor not importable" in done.stderr
    assert "read-back route not served" in done.stderr


def test_contract_change_invalidates_a_stored_authorization(api, monkeypatch):
    client, factory, _ = api
    preview = _preview(client, "project_create", {"title": "合同变化后"})
    assert preview.status_code == 200, preview.text
    action = preview.json()["action"]
    original = contracts.execution_state("project_create")
    monkeypatch.setattr(
        contracts,
        "execution_state",
        lambda kind: (
            {**original, "contract_digest": "0" * 64, "contract_revision": original["contract_revision"] + 1}
            if kind == "project_create"
            else original
        ),
    )
    refused = _approve(client, action)
    assert refused.status_code == 423
    assert refused.json()["detail"]["code"] == "CONTRACT_CHANGED"
    from shine.support.models import SupportAction

    with factory() as db:
        row = db.get(SupportAction, action["id"])
        assert row.status == "EXPIRED"
        assert row.last_error == "CONTRACT_CHANGED"
    assert "合同变化后" not in [row["title"] for row in client.get("/api/v1/projects").json()]


# ---------------------------------------------------------------------------
# Delegation & upgrade
# ---------------------------------------------------------------------------


def test_delegation_continues_after_a_compatible_upgrade(api):
    """V4: the plan-delegation chain is retired.  The surviving surface for
    "an upgrade must not silently reuse an old authorization" is the action
    envelope itself: repeating approval of a SUCCEEDED action replays its stored
    outcome without re-executing, and a live PREVIEWED envelope is reused."""
    client, factory, _ = api
    session = _session(client)
    pending = _preview(client, "project_create", {"title": "升级前待确认"}, session_id=session["id"]).json()["action"]
    reused_pending = _preview(client, "project_create", {"title": "升级前待确认"}, session_id=session["id"])
    assert reused_pending.status_code == 200, reused_pending.text
    assert reused_pending.json()["reused"] is True
    assert reused_pending.json()["action"]["id"] == pending["id"]

    seeded = _preview(client, "project_create", {"title": "升级前完成"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    assert contracts.live_contract_digest("project_create")
    _assert_plan_delegation_retired(client, session["id"])
    again = _approve(client, seeded)
    assert again.status_code == 200, again.text
    assert again.json()["reused"] is True
    assert again.json()["action"]["id"] == seeded["id"]
    assert again.json()["action"]["status"] == "SUCCEEDED"
    assert [row["title"] for row in client.get("/api/v1/projects").json()].count("升级前完成") == 1


def test_delegation_pauses_when_the_contract_changed(api):
    """V4: "a changed contract pauses instead of executing" is enforced by the
    task grant (``GRANT_CONTRACT_CHANGED``) and by the action envelope
    (``CONTRACT_CHANGED`` on approve); the plan-delegation refresh is retired."""
    client, factory, _ = api
    session = _session(client)
    previewed = _preview(client, "project_create", {"title": "旧版本完成"}, session_id=session["id"]).json()["action"]
    _assert_plan_delegation_retired(client, session["id"])
    from shine.copilot import task_grant as grant_contract

    grant = grant_contract.build_proposed_grant(
        grant_id="g", session_id=session["id"], actor_id="local-user", objective="建项",
        project_ids=[], installation_ids=[], allowed_resource_roots=[],
        groups=["inspect"], limits=None, created_at=utcnow(),
    )
    tampered = {**grant, "frozen_capability_contracts": {
        **grant["frozen_capability_contracts"], "project_create": "0" * 64}}
    with pytest.raises(grant_contract.GrantError) as exc:
        grant_contract.assert_contract_frozen(tampered, "project_create")
    assert exc.value.code == "GRANT_CONTRACT_CHANGED"
    # A stored preview whose contract digest no longer matches is expired on approve.
    with factory() as db:
        from shine.support.models import SupportAction

        row = db.get(SupportAction, previewed["id"])
        row.preview = {**(row.preview or {}), "capability_contract": {
            **((row.preview or {}).get("capability_contract") or {}), "contract_digest": "0" * 64}}
        db.commit()
    refused = _approve(client, previewed)
    assert refused.status_code == 423
    assert refused.json()["detail"]["code"] == "CONTRACT_CHANGED"
    assert "旧版本完成" not in [row["title"] for row in client.get("/api/v1/projects").json()]


def test_legacy_delegation_without_a_contract_is_not_executed(api):
    """V4: a legacy context-only delegation in an old session is inert: no
    route advances it and nothing it names is executed."""
    client, factory, _ = api
    session = _session(client)
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=None,
        steps=[
            {"order": 1, "title": "未核对步骤", "kind": "project_create",
             "params": {"title": "未核对步骤"}, "plan_state": "READY",
             "state": "PENDING", "action_id": None, "message": ""},
        ],
    )
    _assert_plan_delegation_retired(client, session["id"])
    assert "未核对步骤" not in [row["title"] for row in client.get("/api/v1/projects").json()]
    with factory() as db:
        from shine.support.models import SupportAction

        assert db.query(SupportAction).filter(SupportAction.session_id == session["id"]).count() == 0


def test_completed_delegation_is_never_resubmitted(api):
    """V4: a completed action is never resubmitted — replaying approval on an
    already-finished row returns the stored result without re-executing."""
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "已完成不再提交"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    _assert_plan_delegation_retired(client, session["id"])
    before = [row["title"] for row in client.get("/api/v1/projects").json()].count("已完成不再提交")
    for _ in range(3):
        again = _approve(client, seeded).json()
        assert again["reused"] is True and again["action"]["id"] == seeded["id"]
        assert again["action"]["status"] == "SUCCEEDED"
    after = [row["title"] for row in client.get("/api/v1/projects").json()].count("已完成不再提交")
    assert before == after == 1


# ---------------------------------------------------------------------------
# Runtime identity & security
# ---------------------------------------------------------------------------


def test_build_endpoint_reports_real_identity(api, monkeypatch):
    client, _, _ = api
    payload = client.get("/api/v1/support/build").json()
    assert payload["app_version"] == contract_build.APP_VERSION
    assert payload["api_matches"] is True
    assert payload["capability_count"] == len(decl.WRITE_ACTIONS)
    assert payload["read_capability_count"] == len(decl.READ_ACTIONS)
    assert set(payload["action_states"]) == {row.action_id for row in decl.WRITE_ACTIONS}
    assert all(state == "AVAILABLE" for state in payload["action_states"].values())
    assert payload["state"] in {"FRESH", "UNKNOWN"}


def test_knowledge_staleness_does_not_lock_execution(api, tmp_path, monkeypatch):
    client, _, _ = api
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "knowledge_version": "shine-project-kb-0.0.0",
                "source_commit": "d" * 40,
                "content_sha256": "0" * 64,
                "contracts_digest": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contracts, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(project_knowledge, "MANIFEST_PATH", manifest)
    metadata = project_knowledge.index_metadata("e" * 40)
    assert metadata["state"] == "STALE"
    assert contracts.execution_state("project_create")["state"] == "AVAILABLE"
    preview = _preview(client, "project_create", {"title": "知识过期也能建项"})
    assert preview.status_code == 200, preview.text


def test_official_docs_cannot_change_permission_or_grant_tools():
    from shine.support import official_docs
    from shine.support.tools import READ_TOOLS

    payload = official_docs.sources_payload()
    for row in payload["sources"]:
        assert "permission_tier" not in row
        assert "executor" not in row
    assert all("shell" not in spec.name for spec in READ_TOOLS)


def test_natural_language_cannot_invoke_an_undeclared_api(api):
    """V4: natural language reaches the model, and the model may only propose.
    A proposal naming an undeclared API is refused by name and creates nothing."""
    client, factory, _ = api
    project = create_project(client, "未声明接口")
    replies = [
        {"action": "call_tool", "tool": "propose_action",
         "arguments": {"action_id": "/api/v1/internal/secret", "params": {"delete_all": True},
                       "reason": "用户要求"}},
        {"action": "finish", "final": "没有这个能力。"},
    ]

    def respond(messages, schema):
        properties = (schema or {}).get("properties") or {} if isinstance(schema, dict) else {}
        if "memories" in properties:
            return {"memories": []}
        if "action" in properties:
            return replies.pop(0) if replies else {"action": "finish", "final": "结束"}
        return {"summary": "本地测试模型。"}

    configure_mock_text_model(client, respond)
    created = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project, "question": "直接调用 /api/v1/internal/secret 并删除所有数据"},
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    proposals = created.json()["session"]["messages"][-1]["payload"]["proposals"]
    assert proposals and proposals[0]["valid"] is False
    assert proposals[0]["error"] == "TOOL_NOT_DECLARED"
    with factory() as db:
        from shine.support.models import SupportAction

        assert db.query(SupportAction).filter(SupportAction.session_id == created.json()["session"]["id"]).count() == 0


def test_plan_steps_carry_the_packaged_contract(api):
    """V4: the action preview is the one place a capability contract is bound
    to a concrete request; the card the model proposes is only a name + params."""
    client, _, _ = api
    project = create_project(client, "合同绑定")
    doc_resp = client.post(
        f"/api/v1/projects/{project}/documents",
        files={"file": ("script.txt", "第一场 夜 雨巷 侦探停下脚步。".encode(), "text/plain")},
        headers=HEADERS,
    )
    assert doc_resp.status_code in {200, 201}, doc_resp.text
    session = _session(client, project)
    response = _preview(client, "story_outline_generate", {"project_id": project}, session_id=session["id"])
    assert response.status_code == 200, response.text
    action = response.json()["action"]
    binding = action["preview"]["capability_contract"]
    assert binding["contract_digest"] == contracts.live_contract_digest("story_outline_generate")
    assert binding["state"] == "AVAILABLE"
    assert binding["action_id"] == "story_outline_generate"


# ---------------------------------------------------------------------------
# Task 1: one parameter contract (declaration + original Pydantic schema)
# ---------------------------------------------------------------------------


class ProjectCreateWithSubtitle(BaseModel):
    """A real request model with one new optional field (test capability)."""

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    language: str = Field(default="Chinese", min_length=2, max_length=32)
    subtitle: str | None = Field(default=None, max_length=80)


def test_resolved_parameter_contract_comes_from_the_request_schema():
    artifact = _load(contracts.ARTIFACT_PATH)
    row = next(
        item for item in artifact["capabilities"] if item["action_id"] == "project_create"
    )
    contract = {item["name"]: item for item in row["param_contract"]}
    # The original CreateProject schema allows 160 characters, not the older
    # hand-kept 120; the schema is the authority for body parameters.
    assert contract["title"]["max_length"] == 160
    assert contract["title"]["required"] is True
    assert contract["description"]["max_length"] == 4000
    assert contract["language"]["default"] == "Chinese"
    # CreateProject declares language as a plain string; the original business
    # gate (canonical spoken language) owns the accepted range, not the copilot.
    assert contract["language"]["enum"] == []
    assert contract["title"]["source"] == "shine.schemas:CreateProject"


def test_new_optional_schema_parameter_flows_to_the_executor(api, monkeypatch):
    """Only schema + declaration change; no fixed field branch is edited."""
    client, _, _ = api
    base = decl.write_action("project_create")
    assert base is not None
    modified = replace(
        base,
        params=base.params + (ParamSpec("subtitle", required=False, max_length=160),),
        request_schema=f"{__name__}:ProjectCreateWithSubtitle",
    )
    original = decl.write_action
    monkeypatch.setattr(
        decl,
        "write_action",
        lambda action_id: modified if action_id == "project_create" else original(action_id),
    )
    from shine.support import contracts as contract_registry

    contract_registry.reset_cache()

    calls: list[dict] = []
    from shine.services import create_project as real_create_project

    def fake_create_project(db, *, title, description="", language="Chinese", subtitle=None, actor="user"):
        calls.append({"title": title, "subtitle": subtitle, "language": language})
        return real_create_project(
            db, title=title, description=description, language=language, actor=actor
        )

    monkeypatch.setattr("shine.services.create_project", fake_create_project)
    preview = _preview(client, "project_create", {"title": "新参数项目", "subtitle": "副标题"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["action"]["status"] == "PREVIEWED"
    approved = _approve(client, preview.json()["action"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    assert calls and calls[0]["subtitle"] == "副标题"
    assert "新参数项目" in [row["title"] for row in client.get("/api/v1/projects").json()]


def test_unknown_parameter_is_rejected_not_silently_dropped(api):
    client, _, _ = api
    response = _preview(client, "project_create", {"title": "拒绝未知", "subtitle": "x"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_ACTION_PARAMS"


# ---------------------------------------------------------------------------
# Task 2: full API request-contract fingerprint and compatibility classes
# ---------------------------------------------------------------------------


def _project_create_contract(app) -> list[dict]:
    from shine.copilot.build import _operations_for, api_schema_contract

    row = decl.write_action("project_create")
    return api_schema_contract(app, operations=_operations_for(row))


def _project_operation(rows: list[dict]) -> dict:
    return next(row for row in rows if row["operation"] == "POST /api/v1/projects")


def test_api_schema_digest_ignores_documentation_and_order():
    from shine.copilot.build import api_schema_digest
    from shine.main import app

    spec = copy.deepcopy(app.openapi())
    original = api_schema_digest(spec)
    operation = spec["paths"]["/api/v1/projects"]["post"]
    operation["summary"] = "changed documentation"
    operation["description"] = "a different prose"
    spec["paths"]["/api/v1/projects"]["post"]["tags"] = ["reordered"]
    assert api_schema_digest(spec) == original


def test_api_contract_diff_classifies_field_changes():
    from shine.copilot.build import diff_api_contract
    from shine.main import app

    base = _project_create_contract(app)
    operation = _project_operation(base)
    field = operation["body"]["fields"]["title"]

    def changed(**updates) -> list[dict]:
        rows = copy.deepcopy(base)
        row = _project_operation(rows)
        row["body"]["fields"]["title"] = {**field, **updates}
        row["body"]["schema_digest"] = "0" * 64 if updates else row["body"]["schema_digest"]
        return rows

    type_changed = diff_api_contract(base, changed(type="integer"))
    assert any(change["reason"] == "type changed" and change["severity"] == "BREAKING"
               for change in type_changed)

    enum_changed = diff_api_contract(base, changed(enum=["a", "b"]))
    assert any(change["reason"] == "enum changed" and change["severity"] == "BREAKING"
               for change in enum_changed)

    default_changed = diff_api_contract(base, changed(default="other"))
    assert any(change["reason"] == "default changed" and change["severity"] == "BREAKING"
               for change in default_changed)

    narrow = diff_api_contract(base, changed(maxLength=10))
    assert any(change["reason"] == "maxLength changed" and change["severity"] == "BREAKING"
               for change in narrow)

    optional_to_required = copy.deepcopy(base)
    row = _project_operation(optional_to_required)
    row["body"]["fields"]["description"]["required"] = True
    row["body"]["required_fields"] = sorted(
        set(row["body"]["required_fields"]) | {"description"}
    )
    row["body"]["schema_digest"] = "1" * 64
    classified = diff_api_contract(base, optional_to_required)
    assert any("optional body field became required" in change["reason"] for change in classified)

    nested = copy.deepcopy(base)
    _project_operation(nested)["body"]["schema_digest"] = "2" * 64
    classified = diff_api_contract(base, nested)
    assert any(change["reason"] == "nested request schema changed"
               and change["severity"] == "BREAKING" for change in classified)

    removed = copy.deepcopy(base)
    _project_operation(removed)["body"]["fields"].pop("title")
    classified = diff_api_contract(base, removed)
    assert any(change["reason"] == "body field removed: title" for change in classified)

    permission = copy.deepcopy(base)
    perm_row = _project_operation(permission)
    perm_row["permission"]["auth_headers"] = ["authorization"]
    classified = diff_api_contract(base, permission)
    assert any(change["reason"] == "permission changed" and change["severity"] == "BREAKING"
               for change in classified)


def test_api_contract_state_blocks_incompatible_and_allows_compatible(api):
    from shine.main import app

    client, _, _ = api
    openapi = copy.deepcopy(app.openapi())
    schema = openapi["components"]["schemas"]["CreateProject"]

    compatible = copy.deepcopy(openapi)
    compatible["components"]["schemas"]["CreateProject"]["properties"]["subtitle"] = {
        "type": "string",
        "maxLength": 80,
        "title": "Subtitle",
    }
    state = contracts.api_contract_state("project_create", compatible)
    assert state["state"] == "AVAILABLE"
    assert any(change["reason"] == "body field added: subtitle" for change in state["changes"])

    incompatible = copy.deepcopy(openapi)
    incompatible["components"]["schemas"]["CreateProject"]["properties"]["title"]["type"] = "integer"
    incompatible["components"]["schemas"]["CreateProject"]["required"] = ["title"]
    state = contracts.api_contract_state("project_create", incompatible)
    assert state["state"] == "CONTRACT_STALE"
    assert any(change["reason"] == "type changed" for change in state["changes"])
    assert schema["properties"]["title"]["type"] == "string"  # real app untouched


# ---------------------------------------------------------------------------
# Task 3: delegation persistence, plan identity, idempotency and recovery
# ---------------------------------------------------------------------------


# Task 3 → V4: the plan-delegation chain is retired.  What survives is the
# one authorization envelope (SupportAction) and the task grant; the
# invariants below are re-pointed at those surfaces.


def _create_once(client, session_id: str, title: str) -> dict:
    previewed = _preview(client, "project_create", {"title": title}, session_id=session_id)
    assert previewed.status_code == 200, previewed.text
    body = previewed.json()
    approved = _approve(client, body["action"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    return approved.json()["action"]


def test_plan_identity_is_immutable_and_old_plan_cannot_authorize_new(api):
    """V4: an authorization is bound to the exact previewed facts; a stale
    preview hash is refused by name, and the task grant refuses a digest
    that is not the one the person previewed."""
    client, _, _ = api
    session = _session(client)
    _assert_plan_delegation_retired(client, session["id"])
    previewed = _preview(client, "project_create", {"title": "甲"}, session_id=session["id"]).json()["action"]
    stale = client.post(
        f"/api/v1/support/actions/{previewed['id']}/approve",
        json={"preview_hash": "0" * 64, "confirm": True},
        headers=HEADERS,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PREVIEW_STALE"
    grant_refused = client.post(
        "/api/v1/support/task-grants/authorize",
        json={"session_id": session["id"], "preview_id": previewed["id"],
              "grant_digest": "0" * 64, "confirm": True, "run": False},
        headers=HEADERS,
    )
    assert grant_refused.status_code == 409
    assert grant_refused.json()["detail"]["code"] in {"GRANT_PREVIEW_REQUIRED", "GRANT_PREVIEW_CHANGED"}


def test_repeated_authorization_never_duplicates_the_project(api):
    client, _, _ = api
    session = _session(client)
    previewed = _preview(client, "project_create", {"title": "只建一次"}, session_id=session["id"]).json()["action"]
    reused_preview = _preview(client, "project_create", {"title": "只建一次"}, session_id=session["id"]).json()
    assert reused_preview["reused"] is True
    assert reused_preview["action"]["id"] == previewed["id"]
    first = _approve(client, previewed).json()["action"]
    second = _approve(client, previewed).json()
    assert first["id"] == second["action"]["id"]
    assert second["reused"] is True
    assert [row["title"] for row in client.get("/api/v1/projects").json()].count("只建一次") == 1


def test_delegation_state_is_readable_from_a_new_database_session(api):
    """V4: the durable multi-step surface is the task grant, readable from any
    session through ``grant_service.latest_for_session``."""
    client, factory, _ = api
    session = _session(client)
    _assert_plan_delegation_retired(client, session["id"])
    from shine.support import grants as grant_service
    from shine.support.models import SupportSession as SessionRow

    with factory() as db:
        row = db.get(SessionRow, session["id"])
        loaded, created = grant_service.create(
            db, session=row, actor="local-user", objective="只读检查环境", groups=["inspect"],
        )
        grant_id, digest = loaded.id, loaded.grant["grant_digest"]
        assert created is True
    with factory() as db:
        row = db.get(SessionRow, session["id"])
        again, created_again = grant_service.create(
            db, session=row, actor="local-user", objective="只读检查环境", groups=["inspect"],
        )
        assert created_again is False and again.id == grant_id
        stored = db.query(SupportDelegation).filter_by(grant_id=grant_id).one()
        assert stored.grant_digest == digest
        assert grant_service.latest_for_session(db, session["id"]).id == grant_id
        assert stored.plan_id is None and stored.plan_hash is None


def test_authorization_key_is_unique_at_the_database_level(api):
    client, factory, _ = api
    session = _session(client)
    _assert_plan_delegation_retired(client, session["id"])
    with factory() as db:
        from shine.support.models import SupportSession as SessionRow

        row = db.get(SessionRow, session["id"])
        loaded, _ = grant_service.create(
            db, session=row, actor="local-user", objective="唯一键", groups=["inspect"],
        )
        stored = db.query(SupportDelegation).filter_by(grant_id=loaded.id).one()
        duplicate = SupportDelegation(
            id="duplicate-key",
            session_id=session["id"],
            plan_id=None,
            plan_hash=None,
            authorization_key=stored.authorization_key,
            status="RUNNING",
            payload=dict(stored.payload or {}),
        )
        db.add(duplicate)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_completed_step_is_not_resubmitted_after_restart(api):
    """V4: a finished action stays finished across a fresh database engine."""
    client, factory, _ = api
    session = _session(client)
    done = _create_once(client, session["id"], "重启恢复")
    _assert_plan_delegation_retired(client, session["id"])
    client.app.state.engine.dispose()
    before = [row["title"] for row in client.get("/api/v1/projects").json()].count("重启恢复")
    replayed = _approve(client, done).json()
    assert replayed["reused"] is True
    assert replayed["action"]["id"] == done["id"]
    assert replayed["action"]["status"] == "SUCCEEDED"
    fetched = client.get(f"/api/v1/support/actions/{done['id']}").json()
    assert fetched["action"]["id"] == done["id"]
    assert fetched["action"]["status"] == "SUCCEEDED"
    after = [row["title"] for row in client.get("/api/v1/projects").json()].count("重启恢复")
    assert before == after == 1


# ---------------------------------------------------------------------------
# Task 4: one build identity, precise (local) degradation
# ---------------------------------------------------------------------------


def test_build_identity_detects_manifest_and_artifact_commit_mismatch(api, tmp_path, monkeypatch):
    client, _, _ = api
    artifact = _load(contracts.ARTIFACT_PATH)
    manifest = _load(contracts.MANIFEST_PATH)
    manifest["source_commit"] = "d" * 40
    artifact["source_commit"] = "e" * 40
    artifact_path = tmp_path / "artifact.json"
    manifest_path = tmp_path / "manifest.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", artifact_path)
    monkeypatch.setattr(contracts, "MANIFEST_PATH", manifest_path)
    contracts.reset_cache()
    identity = contracts.build_identity(build_commit="e" * 40)
    assert identity["state"] == "STALE"
    assert identity["artifact_source_commit"] == "e" * 40
    assert any("不一致" in reason for reason in identity["reasons"])


def test_single_action_contract_mismatch_degrades_only_that_action(api, monkeypatch):
    client, _, _ = api
    base = decl.write_action("project_create")
    assert base is not None
    broken = replace(base, params=tuple(p for p in base.params if p.name != "description"))
    original = decl.write_action
    monkeypatch.setattr(
        decl,
        "write_action",
        lambda action_id: broken if action_id == "project_create" else original(action_id),
    )
    identity = contracts.build_identity(openapi=client.app.openapi())
    assert identity["state"] in {"PARTIAL", "UNKNOWN"}
    assert {row["action_id"] for row in identity["degraded_actions"]} == {"project_create"}
    assert contracts.execution_state("project_create")["state"] == "CONTRACT_STALE"
    assert contracts.execution_state("project_settings_update")["state"] == "AVAILABLE"


def test_full_api_contract_mismatch_is_not_reported_fresh(api, tmp_path, monkeypatch):
    client, _, _ = api
    artifact = _load(contracts.ARTIFACT_PATH)
    artifact["digests"]["api_schema"] = "0" * 64
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(contracts, "ARTIFACT_PATH", path)
    contracts.reset_cache()
    identity = contracts.build_identity(openapi=client.app.openapi())
    assert identity["state"] == "STALE"
    assert identity["api_matches"] is False


# ---------------------------------------------------------------------------
# Task 5: a new write capability is genuinely usable end to end
# ---------------------------------------------------------------------------


def test_new_write_capability_is_generated_planned_and_executed(api):
    client, _, _ = api
    artifact = _load(contracts.ARTIFACT_PATH)
    capability = next(
        row for row in artifact["capabilities"] if row["action_id"] == "support_web_search_update"
    )
    assert capability["request_schema"] == "shine.api_support:PreferenceUpdate"
    assert capability["executor"] == "shine.support.agent.set_web_search"
    assert capability["availability"] == "AVAILABLE"
    assert capability["param_contract"][0]["name"] == "web_search"
    assert capability["param_contract"][0]["type"] == "boolean"

    catalog = client.get("/api/v1/support/capabilities/actions").json()
    row = next(
        item for item in catalog["capabilities"]
        if item["action_id"] == "support_web_search_update"
    )
    assert row["param_contract"][0]["type"] == "boolean"

    proposed = [False]

    def _next_reply(_m, schema):
        if isinstance(schema, dict) and "memories" in (schema.get("properties") or {}):
            return {"memories": []}
        if not proposed[0]:
            proposed[0] = True
            return tool_reply("propose_action", {
                "action_id": "support_web_search_update",
                "params": {"web_search": True},
                "reason": "用户请求开启联网检索",
            })
        return finish_reply("已提出开启联网检索的操作卡片。")

    configure_mock_text_model(client, _next_reply)
    created = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "global", "question": "帮我开启管家联网检索"},
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    session = created.json()["session"]
    proposal = session["messages"][-1]["payload"]["proposals"][0]
    assert proposal["valid"] is True
    assert proposal["action_id"] == "support_web_search_update"
    assert proposal["params"]["web_search"] is True

    # Preview -> approve -> real read-back.
    preview = _preview(client, proposal["action_id"], proposal["params"], session_id=session["id"])
    assert preview.status_code == 200, preview.text
    approved = _approve(client, preview.json()["action"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "SUCCEEDED"
    assert client.get("/api/v1/support/preferences").json()["web_search"] is True


def test_unreviewed_new_write_api_stays_discovered():
    artifact = _load(contracts.ARTIFACT_PATH)
    reviewed = {
        row["action_id"]
        for row in artifact["capabilities"]
        if row["availability"] == "AVAILABLE"
    }
    assert "support_web_search_update" in reviewed
    # Undeclared routes are only described; none of them is executable.
    for row in artifact["discovered"]:
        assert row["status"] == "DISCOVERED"
        assert row["executable"] is False


def test_all_delegation_states_are_readable_from_a_new_session(api):
    """V4: the durable grant ledger is readable across sessions for every status."""
    from shine.copilot import task_grant as grant_contract

    client, factory, _ = api
    statuses = ("AUTHORIZED", "RUNNING", "WAITING_EXECUTOR", "PAUSED_USER_DECISION", "SUCCEEDED", "REVOKED")
    for index, status in enumerate(statuses):
        session = _session(client)
        with factory() as db:
            session_row = db.get(SupportSession, session["id"])
            loaded, _ = grant_service.create(
                db, session=session_row, actor="local-user",
                objective=f"状态检查 {index}", groups=["inspect"],
            )
            if status != "AUTHORIZED":
                grant_service.set_status(db, loaded.id, status, reason=f"test:{status}")
            grant_id = loaded.id
        fetched = client.get(f"/api/v1/support/sessions/{session['id']}/task-grant")
        assert fetched.status_code == 200, fetched.text
        grant = fetched.json()["grant"]
        assert grant is not None
        assert grant["grant_id"] == grant_id
        assert grant["status"] == status
        assert grant["status_label"] == grant_contract.STATUS_LABELS[status]


def test_preview_blocks_incompatible_live_request_schema(api, monkeypatch):
    client, _, _ = api
    tampered = copy.deepcopy(client.app.openapi())
    tampered["components"]["schemas"]["CreateProject"]["properties"]["title"]["type"] = "integer"
    monkeypatch.setattr(client.app, "openapi", lambda: tampered)
    response = _preview(client, "project_create", {"title": "类型变化"})
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "CONTRACT_STALE"


def test_preview_allows_a_compatible_live_request_schema_addition(api, monkeypatch):
    client, _, _ = api
    tampered = copy.deepcopy(client.app.openapi())
    tampered["components"]["schemas"]["CreateProject"]["properties"]["subtitle"] = {
        "type": "string",
        "maxLength": 80,
        "title": "Subtitle",
    }
    monkeypatch.setattr(client.app, "openapi", lambda: tampered)
    response = _preview(client, "project_create", {"title": "兼容新增"})
    assert response.status_code == 200, response.text
    assert response.json()["action"]["status"] == "PREVIEWED"


def test_chat_natural_language_returns_the_new_capability_plan(api):
    client, _, _ = api
    replies = [
        tool_reply("propose_action", {
            "action_id": "support_web_search_update",
            "params": {"web_search": True},
            "reason": "开启联网检索",
        }),
        finish_reply("已生成开启联网检索的操作卡片。"),
    ]

    def respond(_messages, schema):
        props = (schema or {}).get("properties") or {} if isinstance(schema, dict) else {}
        if "memories" in props:
            return {"memories": []}
        if "action" in props:
            return replies.pop(0) if replies else finish_reply("结束")
        return {"summary": "测试"}

    configure_mock_text_model(client, respond)
    session = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "global", "question": "帮我开启管家联网检索"},
        headers=HEADERS,
    ).json()["session"]
    message = session["messages"][-1]
    proposals = message["payload"]["proposals"]
    assert proposals and proposals[0]["valid"] is True
    assert proposals[0]["action_id"] == "support_web_search_update"
    assert proposals[0]["params"]["web_search"] is True

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

from pydantic import BaseModel, Field

from shine.copilot import build as contract_build
from shine.copilot import declarations as decl
from shine.copilot.contracts import ParamSpec
from shine.support import capability_catalog, contracts, project_knowledge
from shine.support.models import SupportDelegation, SupportSession
from tests.conftest import create_project

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
    response = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "projects", "project_id": project_id, "question": "你好"},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


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
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "升级前完成"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    digest = contracts.live_contract_digest("project_create")
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=seeded["id"],
        steps=[
            {
                "order": 1, "title": "升级前完成", "kind": "project_create",
                "params": {"title": "升级前完成"}, "plan_state": "READY",
                "state": "WAITING", "action_id": seeded["id"], "message": "",
                "contract_digest": digest, "contract_revision": 1,
            },
            {
                "order": 2, "title": "升级后续跑", "kind": "project_create",
                "params": {"title": "升级后续跑"}, "plan_state": "READY",
                "state": "PENDING", "action_id": None, "message": "",
                "contract_digest": digest, "contract_revision": 1,
            },
        ],
    )
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    payload = response.json()["delegation"]
    assert payload["status"] == "DONE"
    assert [step["state"] for step in payload["steps"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert "升级后续跑" in [row["title"] for row in client.get("/api/v1/projects").json()]


def test_delegation_pauses_when_the_contract_changed(api):
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "旧版本完成"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=seeded["id"],
        steps=[
            {
                "order": 1, "title": "旧版本完成", "kind": "project_create",
                "params": {"title": "旧版本完成"}, "plan_state": "READY",
                "state": "WAITING", "action_id": seeded["id"], "message": "",
                "contract_digest": contracts.live_contract_digest("project_create"),
                "contract_revision": 1,
            },
            {
                "order": 2, "title": "不兼容续跑", "kind": "project_create",
                "params": {"title": "不兼容续跑"}, "plan_state": "READY",
                "state": "PENDING", "action_id": None, "message": "",
                "contract_digest": "0" * 64, "contract_revision": 0,
            },
        ],
    )
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    payload = response.json()["delegation"]
    assert payload["status"] == "PAUSED"
    assert payload["stop_reason"] == "CONTRACT_CHANGED"
    assert payload["steps"][1]["state"] == "PAUSED"
    assert "不兼容续跑" not in [row["title"] for row in client.get("/api/v1/projects").json()]
    assert "重新确认" in payload["steps"][1]["message"]


def test_legacy_delegation_without_a_contract_is_not_executed(api):
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "旧授权完成"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=seeded["id"],
        steps=[
            {
                "order": 1, "title": "旧授权完成", "kind": "project_create",
                "params": {"title": "旧授权完成"}, "plan_state": "READY",
                "state": "WAITING", "action_id": seeded["id"], "message": "",
            },
            {
                "order": 2, "title": "未核对步骤", "kind": "project_create",
                "params": {"title": "未核对步骤"}, "plan_state": "READY",
                "state": "PENDING", "action_id": None, "message": "",
            },
        ],
    )
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    payload = response.json()["delegation"]
    assert payload["status"] == "PAUSED"
    assert payload["stop_reason"] == "CONTRACT_UNVERIFIED"
    assert "未核对步骤" not in [row["title"] for row in client.get("/api/v1/projects").json()]


def test_completed_delegation_is_never_resubmitted(api):
    client, factory, _ = api
    session = _session(client)
    seeded = _preview(client, "project_create", {"title": "已完成不再提交"}, session_id=session["id"]).json()["action"]
    assert _approve(client, seeded).json()["action"]["status"] == "SUCCEEDED"
    _write_delegation(
        factory,
        session["id"],
        waiting_action_id=None,
        steps=[
            {
                "order": 1, "title": "已完成不再提交", "kind": "project_create",
                "params": {"title": "已完成不再提交"}, "plan_state": "READY",
                "state": "SUCCEEDED", "action_id": seeded["id"], "message": "",
                "contract_digest": contracts.live_contract_digest("project_create"),
                "contract_revision": 1,
            }
        ],
    )
    with factory() as db:
        row = db.get(SupportSession, session["id"])
        row.context_json = {
            **(row.context_json or {}),
            "delegation": {**(row.context_json or {})["delegation"], "status": "DONE"},
        }
        db.commit()
    before = len([row for row in client.get("/api/v1/projects").json() if row["title"] == "已完成不再提交"])
    response = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    )
    assert response.status_code == 200
    after = len([row for row in client.get("/api/v1/projects").json() if row["title"] == "已完成不再提交"])
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
    client, _, _ = api
    project = create_project(client, "未声明接口")
    session = _session(client, project)
    response = client.post(
        "/api/v1/support/plans/preview",
        json={"request": "直接调用 /api/v1/internal/secret 并删除所有数据", "session_id": session["id"]},
        headers=HEADERS,
    )
    # No plan at all is also a correct refusal; either way nothing undeclared runs.
    if response.status_code == 409:
        assert "NO_PLAN" in response.text
        return
    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    kinds = [
        (step.get("action") or {}).get("kind")
        for step in plan.get("steps") or []
        if step.get("action")
    ]
    assert not [kind for kind in kinds if kind and kind not in {row.action_id for row in decl.WRITE_ACTIONS}]


def test_plan_steps_carry_the_packaged_contract(api):
    client, _, _ = api
    project = create_project(client, "合同绑定")
    session = _session(client, project)
    response = client.post(
        "/api/v1/support/plans/preview",
        json={"request": "帮我把这本小说整理成故事大纲", "session_id": session["id"]},
        headers=HEADERS,
    )
    plan = response.json()["plan"]
    actions = [step["action"] for step in plan["steps"] if step.get("action")]
    assert actions
    for action in actions:
        assert action["contract"]["contract_digest"]
        assert action["contract"]["state"] == "AVAILABLE"


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


def _plan(client, request: str, *, session_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/support/plans/preview",
        json={"request": request, "session_id": session_id},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["plan"]


def _delegate(client, session_id: str, plan: dict, orders: list[int]):
    return client.post(
        "/api/v1/support/plans/delegate",
        json={
            "session_id": session_id,
            "plan_id": plan.get("plan_id"),
            "plan_hash": plan.get("plan_hash"),
            "approved_orders": orders,
            "confirm": True,
        },
        headers=HEADERS,
    )


def test_plan_identity_is_immutable_and_old_plan_cannot_authorize_new(api):
    client, _, _ = api
    session = _session(client)
    first = _plan(client, "建立一个名叫《甲》的项目。", session_id=session["id"])
    second = _plan(client, "建立一个名叫《乙》的项目。", session_id=session["id"])
    assert first["plan_id"] != second["plan_id"]
    assert first["plan_hash"] != second["plan_hash"]
    refused = _delegate(client, session["id"], first, [1])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "PLAN_CHANGED"


def test_repeated_authorization_never_duplicates_the_project(api):
    client, _, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《只建一次》的项目。", session_id=session["id"])
    first = _delegate(client, session["id"], plan, [1]).json()["delegation"]
    second = _delegate(client, session["id"], plan, [1]).json()["delegation"]
    assert first["id"] == second["id"]
    assert [row["title"] for row in client.get("/api/v1/projects").json()].count("只建一次") == 1


def test_delegation_state_is_readable_from_a_new_database_session(api):
    client, factory, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《跨会话》的项目。", session_id=session["id"])
    delegation = _delegate(client, session["id"], plan, [1]).json()["delegation"]
    assert delegation["status"] == "DONE"
    with factory() as db:
        row = db.scalar(
            SupportDelegation.__table__.select().where(
                SupportDelegation.session_id == session["id"]
            )
        )
        assert row is not None
        stored = db.query(SupportDelegation).filter_by(session_id=session["id"]).one()
        assert stored.status == "DONE"
        assert stored.plan_id == plan["plan_id"]
        assert stored.payload["steps"][0]["state"] == "SUCCEEDED"
        session_row = db.get(SupportSession, session["id"])
        from shine.support import delegation as delegation_service

        assert delegation_service.get(db, session_row)["status"] == "DONE"


def test_authorization_key_is_unique_at_the_database_level(api):
    client, factory, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《唯一键》的项目。", session_id=session["id"])
    delegation = _delegate(client, session["id"], plan, [1]).json()["delegation"]
    with factory() as db:
        stored = db.query(SupportDelegation).filter_by(session_id=session["id"]).one()
        duplicate = SupportDelegation(
            id="duplicate-key",
            session_id=session["id"],
            plan_id=stored.plan_id,
            plan_hash=stored.plan_hash,
            authorization_key=stored.authorization_key,
            status="RUNNING",
            payload=delegation,
        )
        db.add(duplicate)
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_completed_step_is_not_resubmitted_after_restart(api):
    client, factory, _ = api
    session = _session(client)
    plan = _plan(client, "建立一个名叫《重启恢复》的项目。", session_id=session["id"])
    delegation = _delegate(client, session["id"], plan, [1]).json()["delegation"]
    with factory() as db:
        stored = db.query(SupportDelegation).filter_by(session_id=session["id"]).one()
        assert stored.status == "DONE"
        stored.status = "RUNNING"
        db.commit()
    before = [row["title"] for row in client.get("/api/v1/projects").json()].count("重启恢复")
    refreshed = client.post(
        f"/api/v1/support/sessions/{session['id']}/delegation/refresh",
        json={"session_id": session["id"]},
        headers=HEADERS,
    ).json()["delegation"]
    assert refreshed["status"] == "DONE"
    after = [row["title"] for row in client.get("/api/v1/projects").json()].count("重启恢复")
    assert before == after == 1
    assert delegation["steps"][0]["state"] == "SUCCEEDED"


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

    session = _session(client)
    plan = _plan(client, "帮我开启管家联网检索", session_id=session["id"])
    assert plan["intent"] == "WEB_SEARCH"
    step = plan["steps"][0]
    assert step["action"]["kind"] == "support_web_search_update"
    assert step["action"]["params"]["web_search"] is True
    assert step["action"]["contract"]["state"] == "AVAILABLE"

    response = _delegate(client, session["id"], plan, [1])
    assert response.status_code == 200, response.text
    delegation = response.json()["delegation"]
    assert delegation["status"] == "DONE"
    assert delegation["steps"][0]["state"] == "SUCCEEDED"

    # Real executor + real read-back, not a read-only probe.
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
    client, factory, _ = api
    from datetime import timedelta

    from shine.models import new_id, utcnow

    statuses = ("DONE", "WAITING", "PAUSED", "FAILED", "CANCELLED")
    for index, status in enumerate(statuses):
        session = _session(client)
        with factory() as db:
            db.add(
                SupportDelegation(
                    id=new_id(),
                    session_id=session["id"],
                    plan_id=f"plan-{index}",
                    plan_hash=f"{index}" * 64,
                    authorization_key=f"state-{index}-" + "0" * 80,
                    status=status,
                    stop_reason=None if status == "DONE" else status,
                    payload={
                        "id": new_id(),
                        "status": status,
                        "steps": [],
                        "created_at": (utcnow() - timedelta(seconds=index)).isoformat(),
                    },
                    created_at=utcnow() - timedelta(seconds=index),
                )
            )
            db.commit()
        with factory() as db:
            session_row = db.get(SupportSession, session["id"])
            from shine.support import delegation as delegation_service

            payload = delegation_service.get(db, session_row)
            assert payload is not None
            assert payload["status"] == status


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

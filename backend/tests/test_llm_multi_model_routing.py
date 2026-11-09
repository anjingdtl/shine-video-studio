from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfyops.llm_readiness import TEXT_ROLES
from shine.director.pipeline import BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute, AgentTaskRecord, AuditEvent, ProviderProfile
from shine.schemas import AgentRole
from tests.conftest import create_project


def create_profile(client, *, name: str, model: str = "model-one", **patch) -> dict:
    payload = {
        "name": name,
        "kind": "openai_compatible",
        "base_url": "https://provider.example/v1",
        "model": model,
        "api_key": "secret-for-tests",
        "timeout_s": 60,
        "max_retries": 0,
    }
    payload.update(patch)
    response = client.post("/api/v1/provider-profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def route_profile(factory, role: str) -> str | None:
    with factory() as db:
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
        return route.provider_profile_id if route else None


def test_authoritative_text_role_catalog_matches_backend_roles_and_four_stage_pipeline(api):
    client, _, _ = api
    expected = {
        role.value for role in AgentRole
        if role not in {AgentRole.VLM_REVIEWER, AgentRole.ASR_REVIEWER}
    }
    assert set(TEXT_ROLES) == expected
    assert BIBLE_RUN_ROLE in TEXT_ROLES
    assert SCENE_SCRIPT_RUN_ROLE in TEXT_ROLES

    catalog = client.get("/api/v1/llm/roles").json()
    assert {row["role"] for row in catalog["roles"]} == expected
    assert {row["role"] for row in catalog["roles"] if row["group"] == "长文生产"} >= {
        BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE,
    }
    readiness = client.get("/api/v1/setup/llm-readiness").json()
    assert readiness["role_count"] == 14
    assert {row["role"] for row in readiness["roles"]} == expected


def test_workflow_readiness_can_scope_to_the_four_stage_text_roles(api):
    client, _, _ = api
    response = client.get("/api/v1/llm/readiness?workflow=four_stage")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["workflow"] == "four_stage"
    assert {row["role"] for row in report["roles"]} == {
        "scriptwriter", BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE,
    }


def test_duplicate_copies_credentials_to_an_independent_secret_and_never_exposes_it(api):
    client, factory, settings = api
    source = create_profile(client, name="source")
    duplicate_response = client.post(
        f"/api/v1/provider-profiles/{source['id']}/duplicate",
        json={"name": "copy", "model": "model-two", "copy_credential": True},
    )
    assert duplicate_response.status_code == 201, duplicate_response.text
    duplicate = duplicate_response.json()
    assert duplicate["model"] == "model-two"
    assert duplicate["secret_status"] == "stored"
    assert "secret-for-tests" not in json.dumps(duplicate)
    assert "api_key_ref" not in duplicate
    assert "api_key" not in duplicate

    with factory() as db:
        source_row = db.get(ProviderProfile, source["id"])
        copy_row = db.get(ProviderProfile, duplicate["id"])
        assert source_row.api_key_ref != copy_row.api_key_ref
        source_ref = source_row.api_key_ref
        copy_ref = copy_row.api_key_ref
        audit = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "PROVIDER_PROFILE_DUPLICATED")
        ).all()
        assert len(audit) == 1
        assert "secret-for-tests" not in json.dumps(audit[0].payload)
        assert "api_key_ref" not in json.dumps(audit[0].payload)

    store = SecretStore(settings.secret_store_dir)
    assert store.load(source_ref) == "secret-for-tests"
    assert store.load(copy_ref) == "secret-for-tests"
    source_file = settings.secret_store_dir / source_ref.split(":", 1)[1]
    assert client.delete(f"/api/v1/provider-profiles/{source['id']}").status_code == 204
    assert not source_file.exists()
    assert store.load(copy_ref) == "secret-for-tests"


def test_duplicate_rejects_missing_source_credentials_without_creating_a_profile(api):
    client, factory, _ = api
    source = create_profile(client, name="without-key", api_key=None)
    before = client.get("/api/v1/provider-profiles").json()
    response = client.post(
        f"/api/v1/provider-profiles/{source['id']}/duplicate",
        json={"name": "should-not-exist", "model": "model-two", "copy_credential": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROVIDER_CREDENTIAL_NOT_AVAILABLE"
    assert len(client.get("/api/v1/provider-profiles").json()) == len(before)
    with factory() as db:
        assert db.scalar(select(ProviderProfile).where(ProviderProfile.name == "should-not-exist")) is None


def test_duplicate_commit_failure_removes_the_unreferenced_secret(api, monkeypatch):
    client, _, settings = api
    source = create_profile(client, name="source")
    before_files = {path.name for path in settings.secret_store_dir.iterdir()}

    def failed_commit(self, *args, **kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(Session, "commit", failed_commit)
    response = client.post(
        f"/api/v1/provider-profiles/{source['id']}/duplicate",
        json={"name": "copy-fails", "model": "model-two", "copy_credential": True},
    )
    assert response.status_code == 500
    monkeypatch.undo()
    assert {path.name for path in settings.secret_store_dir.iterdir()} == before_files
    assert all(profile["name"] != "copy-fails" for profile in client.get("/api/v1/provider-profiles").json())


def test_preview_is_read_only_and_batch_assigns_heterogeneous_models_atomically(api):
    client, factory, _ = api
    first = create_profile(client, name="model one", model="model-one")
    second = create_profile(client, name="model two", model="model-two", base_url="https://other.example/v1")
    updates = [
        {"role": "scriptwriter", "provider_profile_id": first["id"]},
        {"role": BIBLE_RUN_ROLE, "provider_profile_id": second["id"]},
    ]
    preview_response = client.post("/api/v1/agent-routes/preview", json={"updates": updates})
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["can_apply"] is True
    assert preview["changed_role_count"] == 2
    assert route_profile(factory, "scriptwriter") is None

    applied = client.put("/api/v1/agent-routes/batch", json={
        "updates": updates,
        "expected_revision": preview["expected_revision"],
    })
    assert applied.status_code == 200, applied.text
    assert route_profile(factory, "scriptwriter") == first["id"]
    assert route_profile(factory, BIBLE_RUN_ROLE) == second["id"]
    assert set(applied.json()["changed_roles"][0]) == {"role", "provider_profile_id"}


def test_batch_revision_conflict_writes_nothing(api):
    client, factory, _ = api
    first = create_profile(client, name="first")
    second = create_profile(client, name="second", model="model-two")
    preview = client.post("/api/v1/agent-routes/preview", json={"updates": [
        {"role": "director", "provider_profile_id": first["id"]},
    ]}).json()
    set_route = client.put("/api/v1/agent-routes", json={
        "role": "editor", "provider_profile_id": second["id"],
    })
    assert set_route.status_code == 200
    result = client.put("/api/v1/agent-routes/batch", json={
        "updates": [{"role": "director", "provider_profile_id": first["id"]}],
        "expected_revision": preview["expected_revision"],
    })
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "LLM_ROUTE_REVISION_CONFLICT"
    assert route_profile(factory, "director") is None
    assert route_profile(factory, "editor") == second["id"]


def test_preview_blocks_disabled_models_without_writing_a_route(api):
    client, factory, _ = api
    disabled = create_profile(client, name="disabled", enabled=False)
    preview = client.post("/api/v1/agent-routes/preview", json={"updates": [
        {"role": "director", "provider_profile_id": disabled["id"]},
    ]})
    assert preview.status_code == 200
    assert preview.json()["can_apply"] is False
    assert preview.json()["blockers"][0]["code"] == "PROVIDER_PROFILE_DISABLED"
    assert route_profile(factory, "director") is None


def test_in_flight_agent_task_is_shown_in_preview_and_blocks_route_change(api):
    client, factory, _ = api
    project_id = create_project(client, "LLM 路由任务保护")
    current = create_profile(client, name="current")
    target = create_profile(client, name="target", model="model-two")
    assert client.put("/api/v1/agent-routes", json={
        "role": "director", "provider_profile_id": current["id"],
    }).status_code == 200
    with factory() as db:
        db.add(AgentTaskRecord(
            project_id=project_id,
            role_id="director",
            input_hash="a" * 64,
            asset_manifest_hash="b" * 64,
            rulepack_id="test",
            rulepack_hash="c" * 64,
            model_profile_id=current["id"],
            model_id=current["model"],
            status="RUNNING",
        ))
        db.commit()

    updates = [{"role": "director", "provider_profile_id": target["id"]}]
    preview = client.post("/api/v1/agent-routes/preview", json={"updates": updates}).json()
    assert preview["can_apply"] is False
    assert preview["affected_tasks"][0]["role"] == "director"
    assert preview["affected_tasks"][0]["status"] == "RUNNING"
    result = client.put("/api/v1/agent-routes/batch", json={
        "updates": updates, "expected_revision": preview["expected_revision"],
    })
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "LLM_ROUTE_PREVIEW_BLOCKED"
    assert route_profile(factory, "director") == current["id"]


def test_batch_commit_failure_rolls_back_every_route_and_audit(api, monkeypatch):
    client, factory, _ = api
    first = create_profile(client, name="first")
    second = create_profile(client, name="second", model="model-two")
    updates = [
        {"role": "scriptwriter", "provider_profile_id": first["id"]},
        {"role": "director", "provider_profile_id": second["id"]},
    ]
    preview = client.post("/api/v1/agent-routes/preview", json={"updates": updates}).json()

    def failed_commit(self, *args, **kwargs):
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(Session, "commit", failed_commit)
    result = client.put("/api/v1/agent-routes/batch", json={
        "updates": updates, "expected_revision": preview["expected_revision"],
    })
    assert result.status_code == 500
    monkeypatch.undo()
    with factory() as db:
        assert db.scalars(select(AgentRoute)).all() == []
        assert db.scalars(select(AuditEvent).where(AuditEvent.event_type == "AGENT_ROUTES_BATCH_UPDATED")).all() == []


def test_legacy_bulk_endpoint_still_binds_all_four_stage_roles(api):
    client, db_factory, _ = api
    profile = create_profile(client, name="legacy bulk")
    response = client.put("/api/v1/agent-routes/bulk", json={
        "provider_profile_id": profile["id"], "roles": list(TEXT_ROLES),
    })
    assert response.status_code == 200, response.text
    assert route_profile(db_factory, BIBLE_RUN_ROLE) == profile["id"]
    assert route_profile(db_factory, SCENE_SCRIPT_RUN_ROLE) == profile["id"]

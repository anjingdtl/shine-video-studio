"""Persisted consent authorises necessary text, never asset bytes."""
import copy

import pytest

from shine.director.pipeline import BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE
from shine.llm.base import LLMResponse
from shine.llm.gateway import LLMGateway
from shine.materialprep import brief, text_consent
from shine.models import AgentRoute, MaterialTextConsent, ProjectDocument, ProviderProfile
from tests.test_material_brief import BRIEF_ANSWER, _item, _project, _route

LOCAL = {"x-shine-local-action": "1"}


def test_four_stage_roles_get_document_scoped_consent(api):
    client, factory, _ = api
    project = _project(client)
    profile_id = _route(client, kind="openai_compatible", base_url="https://text.example.invalid/v1")
    roles = {BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE}
    with factory.begin() as db:
        db.add_all([AgentRoute(role=role, provider_profile_id=profile_id) for role in roles])
    document = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": ("novel.txt", b"Chapter one")}).json()
    path = f"/api/v1/projects/{project}/material-preparation/text-consent"
    scope = client.get(path + "/" + document["id"]).json()
    assert roles <= {row["role"] for row in scope["roles"] if row["configured"] and not row["allowed"]}
    response = client.post(path, headers=LOCAL, json={
        "document_id": document["id"], "expected_document_hash": document["sha256"],
        "expected_scope_hash": scope["scope_hash"], "granted": True,
    })
    assert response.status_code == 200
    assert roles <= {row["role"] for row in response.json()["roles"] if row.get("allowed")}


def test_remote_worker_consent_revocation_scope_and_provider_change(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project = _project(client)
    profile_id = _route(client, kind="openai_compatible", base_url="https://text.example.invalid/v1")
    with factory.begin() as db:
        db.add(AgentRoute(role="scriptwriter", provider_profile_id=profile_id))
    with factory() as db:
        item = _item(db, project)
        item_id, version = item.id, item.state_version
        from shine.comfyops.models import PreProductionPlan
        from shine.materialprep.models import MaterialRun
        plan = db.get(PreProductionPlan, db.get(MaterialRun, item.run_id).plan_id)
        document_id = plan.payload["document_id"]
    scope_path = f"/api/v1/projects/{project}/material-preparation/text-consent/{document_id}"
    scope = client.get(scope_path).json()
    assert not any(r.get("allowed") for r in scope["roles"])
    body = {"document_id": document_id, "expected_document_hash": scope["document_sha256"],
            "expected_scope_hash": scope["scope_hash"], "granted": True}
    decision_path = f"/api/v1/projects/{project}/material-preparation/text-consent"
    assert client.post(decision_path, json=body, headers=LOCAL).status_code == 200
    calls = []
    def complete(self, db, role, **kwargs):
        calls.append(kwargs["user"])
        return LLMResponse("", copy.deepcopy(BRIEF_ANSWER), "test-double", 0)
    monkeypatch.setattr(LLMGateway, "complete_for_role", complete)
    # This is a transport double: it proves Worker enforcement, not a real remote call.
    result = brief.ensure_brief(factory, settings, item_id, "worker-test", version=version)
    assert result["ready"] and len(calls) == 1
    assert "base64" not in calls[0] and "storage_path" not in calls[0]
    with factory() as db:
        doc = db.get(ProjectDocument, document_id)
        profile = db.get(ProviderProfile, profile_id)
        expected = text_consent.fingerprint(profile)
        gateway = LLMGateway(None)
        check = text_consent.guard(project, doc.id, doc.sha256, "producer", expected, gateway)
        check(db)
    body["granted"] = False
    assert client.post(decision_path, json=body, headers=LOCAL).status_code == 200
    with factory() as db:
        assert not text_consent.allowed(db, project, db.get(ProjectDocument, document_id), "producer",
                                        db.get(ProviderProfile, profile_id))
        with pytest.raises(text_consent.TextConsentRequired):
            check(db)
        assert db.query(MaterialTextConsent).count() == 2  # audit history not deleted
    body["granted"] = True
    assert client.post(decision_path, json=body, headers=LOCAL).status_code == 200
    with factory.begin() as db:
        db.get(ProviderProfile, profile_id).model = "different-model"
    with factory() as db:
        with pytest.raises(text_consent.TextConsentRequired):
            check(db)
    assert client.post(decision_path, json=body, headers=LOCAL).status_code == 409

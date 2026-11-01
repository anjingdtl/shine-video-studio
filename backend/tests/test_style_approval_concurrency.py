"""A style approval must bind the displayed content, including before grant issuance."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import select

from shine.comfyops.models import ApprovalGrant
from shine.models import StyleVersion
from tests.conftest import create_project, freeze_style, style_payload
from tests.test_v13_preparation import TOKEN

HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def approval_api(api):
    client, factory, settings = api
    client.app.state.settings = replace(
        settings, operator_id="concurrency-test-operator",
        operator_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    return client, factory


def grant_for(client, project):
    response = client.post("/api/v1/preparation/approval-grants", headers=HEADERS,
                           json={"action": "style_freeze", "target_id": project})
    assert response.status_code == 200, response.text
    return {key: response.json()[key] for key in ("grant_id", "snapshot")}


def test_protected_freeze_cannot_omit_displayed_content(approval_api):
    client, factory = approval_api
    project = create_project(client)
    own = style_payload() | {"photography_language": "Displayed A"}
    other = own | {"photography_language": "Other tab B"}
    client.put(f"/api/v1/projects/{project}/style/draft", json=own).raise_for_status()
    client.put(f"/api/v1/projects/{project}/style/draft", json=other).raise_for_status()
    grant = grant_for(client, project)
    response = client.post(f"/api/v1/projects/{project}/style/freeze", headers=HEADERS, json=grant)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "STYLE_APPROVAL_CONTENT_REQUIRED"
    with factory() as db:
        assert db.scalar(select(StyleVersion).where(StyleVersion.project_id == project)) is None


@pytest.mark.parametrize("other_edit_before_grant", [True, False])
def test_other_tab_change_before_or_after_grant_cannot_be_approved(approval_api, other_edit_before_grant):
    client, factory = approval_api
    project = create_project(client)
    own = style_payload() | {"photography_language": "Displayed A"}
    other = own | {"photography_language": "Other tab B"}
    client.put(f"/api/v1/projects/{project}/style/draft", json=own).raise_for_status()
    if other_edit_before_grant:
        client.put(f"/api/v1/projects/{project}/style/draft", json=other).raise_for_status()
    grant = grant_for(client, project)
    if not other_edit_before_grant:
        client.put(f"/api/v1/projects/{project}/style/draft", json=other).raise_for_status()
    response = client.post(f"/api/v1/projects/{project}/style/freeze", headers=HEADERS,
                           json={**grant, "expected_style": own})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "STYLE_APPROVAL_CONTENT_CHANGED"
    with factory() as db:
        assert db.scalar(select(StyleVersion).where(StyleVersion.project_id == project)) is None
        assert not db.get(ApprovalGrant, grant["grant_id"]).used
    assert client.get(f"/api/v1/projects/{project}").json()["style_draft"] == other


def test_exact_displayed_content_freezes_once_and_preserves_history(approval_api):
    client, factory = approval_api
    project = create_project(client)
    previous = freeze_style(client, project)
    own = style_payload() | {"photography_language": "Explicitly approved new content"}
    client.put(f"/api/v1/projects/{project}/style/draft", json=own).raise_for_status()
    decision = {**grant_for(client, project), "expected_style": own}
    response = client.post(f"/api/v1/projects/{project}/style/freeze", headers=HEADERS, json=decision)
    assert response.status_code == 201, response.text
    assert response.json()["snapshot"] == own
    assert response.json()["version"] == previous["version"] + 1
    assert client.post(f"/api/v1/projects/{project}/style/freeze",
                       headers=HEADERS, json=decision).status_code == 409
    with factory() as db:
        assert db.get(StyleVersion, previous["id"]).snapshot == previous["snapshot"]
        assert len(db.scalars(select(StyleVersion).where(StyleVersion.project_id == project)).all()) == 2


def test_concurrent_freezes_share_one_grant_and_create_one_version(approval_api):
    client, factory = approval_api
    project = create_project(client)
    own = style_payload()
    client.put(f"/api/v1/projects/{project}/style/draft", json=own).raise_for_status()
    decision = {**grant_for(client, project), "expected_style": own}
    barrier = threading.Barrier(2)

    def freeze(_):
        barrier.wait(timeout=10)
        return client.post(f"/api/v1/projects/{project}/style/freeze", headers=HEADERS, json=decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(freeze, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    with factory() as db:
        assert len(db.scalars(select(StyleVersion).where(StyleVersion.project_id == project)).all()) == 1

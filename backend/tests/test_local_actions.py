"""Passwordless personal desktop actions retain origin and revision boundaries."""

import pytest

from tests.conftest import create_project, style_payload, upload_asset

LOCAL = {"X-Shine-Local-Action": "1"}


def test_local_action_grant_needs_no_provisioned_password(api):
    client, _, _ = api
    project = create_project(client)
    # A real project draft is needed before a style snapshot can be granted.
    style = style_payload()
    assert client.put(f"/api/v1/projects/{project}/style/draft", json=style).status_code == 200
    body = {"action": "style_freeze", "target_id": project}
    response = client.post("/api/v1/preparation/approval-grants", json=body, headers=LOCAL)
    assert response.status_code == 200, response.text
    grant = response.json()
    decision = {**grant, "expected_style": style, "approval_note": ""}
    # Do not echo unrelated grant response fields into the strict schema.
    decision = {k: decision[k] for k in ("grant_id", "snapshot", "expected_style", "approval_note")}
    frozen = client.post(f"/api/v1/projects/{project}/style/freeze", json=decision, headers=LOCAL)
    assert frozen.status_code == 201, frozen.text
    assert frozen.json()["snapshot"] == style
    replay = client.post(f"/api/v1/projects/{project}/style/freeze", json=decision, headers=LOCAL)
    assert replay.status_code == 409


@pytest.mark.parametrize("headers", [
    {},
    {**LOCAL, "Origin": "https://foreign.example"},
    {**LOCAL, "Sec-Fetch-Site": "cross-site"},
    {**LOCAL, "Authorization": "Bearer invalid"},
])
def test_local_action_rejects_missing_marker_cross_site_and_invalid_explicit_token(api, headers):
    client, _, _ = api
    response = client.get("/api/v1/operator/jobs/missing", headers=headers)
    assert response.status_code == 403


def test_local_asset_confirmation_checks_real_bytes_and_consumes_separate_grants(api):
    client, _, settings = api
    project = create_project(client)
    asset = upload_asset(client, project)
    for action in ("approve", "lock"):
        response = client.post("/api/v1/preparation/approval-grants", headers=LOCAL,
                               json={"action": f"asset_{action}", "target_id": asset["id"]})
        assert response.status_code == 200, response.text
        grant = response.json()
        body = {"grant_id": grant["grant_id"], "snapshot": grant["snapshot"], "offline": False,
                "evidence": "User confirmed asset contents and rights on this machine"}
        if action == "lock":
            # File tampering after the click must still block the fixed version.
            from shine.models import Asset

            with client.app.state.session_factory() as db:
                path = settings.data_dir / db.get(Asset, asset["id"]).storage_path
            original = path.read_bytes()
            path.write_bytes(b"changed")
            refused = client.post(f"/api/v1/assets/{asset['id']}/{action}", headers=LOCAL, json=body)
            assert refused.status_code == 409
            assert refused.json()["detail"] == "ASSET_HASH_CHANGED"
            path.write_bytes(original)
        result = client.post(f"/api/v1/assets/{asset['id']}/{action}", headers=LOCAL, json=body)
        assert result.status_code == 200, result.text
    assert result.json()["approved"] is True
    assert result.json()["locked"] is True

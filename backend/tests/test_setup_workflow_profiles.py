"""Project-scoped H3 Ref2VA workflow registration and live verification.

Registration alone proves nothing: a profile that names a node or an input the
installed renderer does not have only fails at render time.  The setup
capability therefore registers *this project's* profiles and verifies them
against the real renderer, and it must report the reason a verification could
not run instead of claiming a pass.
"""

from __future__ import annotations

from tests.conftest import create_project


def _profile_names(response_json: dict) -> set[str]:
    return {row["name"] for row in response_json["profiles"]}


def test_setup_registers_the_projects_own_ref2va_profiles(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client, "苏镇歌舞团")

    response = client.post("/api/v1/setup/profiles", json={"project_id": project_id})
    assert response.status_code == 200, response.text
    names = _profile_names(response.json())
    assert "local-h3-ref2va-pdd8-2img-1aud" in names
    assert "local-h3-ref2va-pdd8-2img-1aud-portrait" in names

    # The registration is project-scoped: another project gets its own copy.
    other = create_project(client, "另一个项目")
    other_response = client.post("/api/v1/setup/profiles", json={"project_id": other})
    assert other_response.status_code == 200
    assert _profile_names(other_response.json()) == names


def test_setup_registers_the_same_profile_once_per_project(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client, "苏镇歌舞团")

    first = client.post("/api/v1/setup/profiles", json={"project_id": project_id}).json()
    second = client.post("/api/v1/setup/profiles", json={"project_id": project_id}).json()
    assert {row["status"] for row in first["profiles"]} == {"REGISTERED"}
    assert {row["status"] for row in second["profiles"]} == {"ALREADY_REGISTERED"}
    assert {row["profile_id"] for row in first["profiles"]} == {row["profile_id"] for row in second["profiles"]}


def test_setup_profile_validation_reports_the_reason_instead_of_claiming_a_pass(studio):
    """Without an activated runtime the check cannot run, and must say so."""
    client, _factory, _root, _settings = studio
    project_id = create_project(client, "苏镇歌舞团")

    response = client.post("/api/v1/setup/profiles/validate", json={"project_id": project_id})
    assert response.status_code == 200, response.text
    profiles = response.json()["profiles"]
    assert profiles, "the profiles must still be registered"
    for row in profiles:
        assert row["validated"] is False
        assert row["validated_against"] is None
        assert row["code"], "a failed verification must carry a machine-readable reason"


def test_setup_profile_validation_requires_a_project(studio):
    client, _factory, _root, _settings = studio
    assert client.post("/api/v1/setup/profiles/validate", json={"project_id": "missing"}).status_code == 404

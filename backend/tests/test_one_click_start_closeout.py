"""J08: one-click start sends one plan; empty picture slots are capacity."""

from __future__ import annotations

from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)

LOCAL = {"X-Shine-Local-Action": "1"}


def _two_shots(client):
    project = create_project(client, "一键开拍")
    style = freeze_style(client, project)
    first = upload_asset(client, project, content=PNG + b"-a", filename="甲.png",
                         role="character", entity_id="Lin")
    second = upload_asset(client, project, content=PNG + b"-b", filename="乙.png",
                          role="character", entity_id="Lin")
    approve_lock(client, first["id"])
    approve_lock(client, second["id"])
    shots = [create_shot(
        client, project, style["id"], title=f"镜头{index + 1}", characters=["Lin"],
        asset_ids=[first["id"]],
        bindings=[{"asset_id": first["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    ) for index in range(2)]
    profile = register_workflow_profile(client, mode="Ref2VA")
    return project, shots, first, second, profile


def test_an_empty_optional_picture_slot_does_not_block_a_bound_subject(api):
    """The profile declares a picture slot; the shot only needs its character."""
    client, _factory, _settings = api
    project, shots, first, _second, profile = _two_shots(client)
    response = client.post(f"/api/v1/projects/{project}/production-series", headers=LOCAL, json={
        "shot_ids": [shots[0]["id"]], "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "bindings": {shots[0]["id"]: [{
            "asset_id": first["id"], "use_as": "subject",
            "role": "CHAR_A", "ordinal": 0,
        }]},
    })
    assert response.status_code == 201, response.text
    assert response.json()["shot_count"] == 1


def test_a_refused_series_leaves_no_half_written_binding_plan(api):
    """One invalid shot refuses the whole start; the earlier shot is untouched."""
    client, _factory, _settings = api
    project, shots, first, second, profile = _two_shots(client)
    unapproved = upload_asset(client, project, content=PNG + b"-c", filename="丙.png",
                              role="character", entity_id="Lin")

    response = client.post(f"/api/v1/projects/{project}/production-series", headers=LOCAL, json={
        "shot_ids": [shots[0]["id"], shots[1]["id"]], "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "bindings": {
            # A valid change for the first shot ...
            shots[0]["id"]: [{"asset_id": second["id"], "use_as": "subject",
                              "role": "CHAR_A", "ordinal": 0}],
            # ... and an asset the person never approved for the second.
            shots[1]["id"]: [{"asset_id": unapproved["id"], "use_as": "subject",
                              "role": "CHAR_A", "ordinal": 0}],
        },
    })
    assert response.status_code == 422, response.text
    for shot in shots:
        bindings = client.get(f"/api/v1/shots/{shot['id']}/bindings").json()
        assert [row["asset_id"] for row in bindings] == [first["id"]], (
            "a refused plan must not leave the first shot rewritten")


def test_a_named_character_without_a_subject_is_refused_by_name(api):
    """The person gets the missing identity, not a generic render failure."""
    client, _factory, _settings = api
    project, shots, first, second, profile = _two_shots(client)
    # A shot whose only binding is a picture while its spec names a character.
    style = client.get(f"/api/v1/projects/{project}/styles").json()[0]
    bare = create_shot(client, project, style["id"], title="缺人物", characters=["Lin"])
    picture = upload_asset(client, project, content=PNG + b"-p", filename="场景.png",
                           role="scene", entity_id="码头")
    approve_lock(client, picture["id"])
    response = client.post(f"/api/v1/projects/{project}/production-series", headers=LOCAL, json={
        "shot_ids": [bare["id"]], "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "bindings": {bare["id"]: [
            {"asset_id": picture["id"], "use_as": "picture", "role": "SCENE", "ordinal": 0},
        ]},
    })
    assert response.status_code in {409, 422}, response.text
    text = response.text
    assert "H3_CHARACTER_REFERENCE_MISSING" in text or "CHARACTER" in text.upper()

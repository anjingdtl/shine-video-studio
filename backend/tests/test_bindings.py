from __future__ import annotations

from tests.conftest import (
    PNG,
    approve_lock,
    create_job,
    create_project,
    create_shot,
    freeze_style,
    shot_payload,
    upload_asset,
)


def test_each_shot_keeps_its_own_binding_and_job_manifest(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    hero = upload_asset(client, project_id, PNG + b"hero", "hero.png", role="character", entity_id="CHAR_A")
    scene = upload_asset(client, project_id, PNG + b"scene", "scene.png", role="scene", entity_id="SCENE_A")
    approve_lock(client, hero["id"])
    approve_lock(client, scene["id"])

    shot_a = create_shot(
        client,
        project_id,
        style["id"],
        title="镜头甲",
        asset_ids=[hero["id"]],
        bindings=[{"asset_id": hero["id"], "use_as": "subject"}],
    )
    shot_b = create_shot(
        client,
        project_id,
        style["id"],
        title="镜头乙",
        asset_ids=[scene["id"]],
        bindings=[{"asset_id": scene["id"], "use_as": "picture", "frame_role": "first_frame"}],
    )
    job_a = create_job(client, project_id, shot_a["id"])
    job_b = create_job(client, project_id, shot_b["id"])
    manifest_a = job_a["request"]["reference_manifest"]
    manifest_b = job_b["request"]["reference_manifest"]
    assert manifest_a[0]["label"] == "<Subject 1>" and manifest_a[0]["asset_id"] == hero["id"]
    assert manifest_b[0]["label"] == "<Picture 1>" and manifest_b[0]["asset_id"] == scene["id"]
    assert job_a["request"]["shot_id"] != job_b["request"]["shot_id"]
    assert client.get(f"/api/v1/shots/{shot_a['id']}/bindings").json()[0]["asset_id"] == hero["id"]


def test_mixed_image_bindings_share_presentation_picture_numbering(studio):
    """Official Ref2VA labels every reference image <Picture i> in request order.

    A shot bound as [subject, subject, picture] must number the standalone
    picture by its shared image position (<Picture 3>), not a picture-only
    counter, so the compiled prompt matches the tags the H3 text encoder
    actually splices around the reference images.
    """
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    hero = upload_asset(client, project_id, PNG + b"hero", "hero.png", role="character", entity_id="CHAR_A")
    foil = upload_asset(client, project_id, PNG + b"foil", "foil.png", role="character", entity_id="CHAR_B")
    scene = upload_asset(client, project_id, PNG + b"scene", "scene.png", role="scene", entity_id="SCENE_A")
    for asset in (hero, foil, scene):
        approve_lock(client, asset["id"])
    shot = create_shot(
        client,
        project_id,
        style["id"],
        asset_ids=[hero["id"], foil["id"], scene["id"]],
        bindings=[
            {"asset_id": hero["id"], "use_as": "subject", "ordinal": 0},
            {"asset_id": foil["id"], "use_as": "subject", "ordinal": 1},
            {"asset_id": scene["id"], "use_as": "picture", "frame_role": "keyframe", "ordinal": 2},
        ],
    )
    job = create_job(client, project_id, shot["id"])
    labels = [entry["label"] for entry in job["request"]["reference_manifest"]]
    assert labels == ["<Subject 1>", "<Subject 2>", "<Picture 3>"]
    prompt = client.post(f"/api/v1/shots/{shot['id']}/compile").json()["compiled_prompt"]
    assert (
        "<Subject 1> is the approved, locked reference for the first listed performer (CHAR_A), "
        "shown in <Picture 1>;"
    ) in prompt
    assert (
        "<Subject 2> is the approved, locked reference for the second listed performer (CHAR_B), "
        "shown in <Picture 2>;"
    ) in prompt
    assert "<Picture 3> is the keyframe reference for [Shot 1]." in prompt


def test_job_references_must_match_saved_bindings(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    first = upload_asset(client, project_id, PNG + b"one")
    second = upload_asset(client, project_id, PNG + b"two")
    approve_lock(client, first["id"])
    approve_lock(client, second["id"])
    shot = create_shot(
        client,
        project_id,
        style["id"],
        asset_ids=[first["id"]],
        bindings=[{"asset_id": first["id"], "use_as": "picture"}],
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "shot_id": shot["id"],
            "references": [{"asset_id": second["id"], "use_as": "picture"}],
        },
    )
    assert response.status_code == 409, response.text
    assert "bindings" in response.text


def test_unapproved_asset_cannot_be_bound(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/shots",
        json=shot_payload(
            style["id"],
            asset_ids=[asset["id"]],
        )
        | {
            "bindings": [{"asset_id": asset["id"], "use_as": "picture"}],
        },
    )
    assert response.status_code == 409, response.text
    assert "approved" in response.text


def test_binding_rejects_cross_project_duplicate_and_media_mismatch(studio):
    client, _factory, _root, _settings = studio
    first = create_project(client, "甲")
    second = create_project(client, "乙")
    first_style = freeze_style(client, first)
    freeze_style(client, second)
    foreign = upload_asset(client, second, PNG + b"foreign")
    approve_lock(client, foreign["id"])
    shot = create_shot(client, first, first_style["id"])
    response = client.put(
        f"/api/v1/shots/{shot['id']}/bindings",
        json={"bindings": [{"asset_id": foreign["id"], "use_as": "picture"}]},
    )
    assert response.status_code == 422

    local = upload_asset(client, first, PNG + b"local")
    approve_lock(client, local["id"])
    duplicate = client.put(
        f"/api/v1/shots/{shot['id']}/bindings",
        json={
            "bindings": [
                {"asset_id": local["id"], "use_as": "picture"},
                {"asset_id": local["id"], "use_as": "subject"},
            ]
        },
    )
    assert duplicate.status_code == 422

    audio = upload_asset(
        client,
        first,
        b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 40,
        "voice.wav",
        kind="audio",
        content_type="audio/wav",
    )
    approve_lock(client, audio["id"])
    mismatch = client.put(
        f"/api/v1/shots/{shot['id']}/bindings",
        json={"bindings": [{"asset_id": audio["id"], "use_as": "picture"}]},
    )
    assert mismatch.status_code == 422


def test_binding_count_limits_are_enforced(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    ids = []
    for index in range(10):
        asset = upload_asset(client, project_id, PNG + f"unique-{index}".encode())
        approve_lock(client, asset["id"])
        ids.append(asset["id"])
    response = client.put(
        f"/api/v1/shots/{shot['id']}/bindings",
        json={
            "bindings": [
                {"asset_id": asset_id, "use_as": "picture", "ordinal": index}
                for index, asset_id in enumerate(ids)
            ]
        },
    )
    assert response.status_code == 422
    assert "limit" in response.text.lower()


def test_compile_preview_uses_saved_bindings_without_creating_a_job(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id, PNG + b"preview", role="character", entity_id="CHAR_A")
    approve_lock(client, asset["id"])
    shot = create_shot(
        client,
        project_id,
        style["id"],
        asset_ids=[asset["id"]],
        characters=["CHAR_A"],
        dialogue=[{"speaker_id": "CHAR_A", "text": "账册在哪儿？", "start_s": 0.4, "end_s": 2.0}],
        bindings=[{"asset_id": asset["id"], "use_as": "subject"}],
    )
    response = client.post(f"/api/v1/shots/{shot['id']}/compile")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["mode"] == "Ref2VA"
    assert "<Subject 1>" in preview["compiled_prompt"]
    assert preview["labels"]["<Subject 1>"] == asset["id"]
    assert preview["prompt_sha256"]
    assert client.get(f"/api/v1/projects/{project_id}/jobs").json() == []

"""Stage C: the server prepares one script scene at a time.

The material flow needs the requirement list before anything is frozen, so the
pre-production scriptwriter answers per scene.  The server owns the
segmentation, the numbering and the citation catalogue; the model may only
cite a fragment it was given.
"""

from __future__ import annotations

import json

from shine.models import AgentArtifact, AgentTaskRecord

SCRIPT = "场景一：\n林知远：你来了。\n\n场景二：\n林知远转身离开。\n"


def _responder(messages, _schema):
    """Answer with a preparation for the scene the server asked about."""
    user = messages[-1].content
    payload = json.loads(user.split("\n\nReturn one JSON object")[0])
    scene_key = payload["scene_key"]
    span = payload["source_spans"][0]["id"]
    return {
        "schema_version": "1.0",
        "scene_key": scene_key,
        "summary": "码头的一场戏。",
        "locked_facts": [],
        "requirements": [
            {
                "family": "character",
                "name": "林知远",
                "description": "三十岁上下，长风衣。",
                "source_span_ids": [span],
                "questions": [],
            }
        ],
        "questions": [],
    }


def _prepare(client, project_id: str, document: dict, **body):
    return client.post(
        f"/api/v1/projects/{project_id}/material-preparation/scenes/prepare",
        json={
            "document_id": document["id"],
            "expected_document_hash": document["sha256"],
            **body,
        },
        headers={"x-shine-local-action": "1"},
    )


def test_the_server_prepares_every_scene_and_reuses_the_answer(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id = client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]
    profile = client.post(
        "/api/v1/provider-profiles", json={"name": "writer-mock", "kind": "mock", "model": "writer"}
    )
    assert profile.status_code == 201, profile.text
    route = client.put(
        "/api/v1/agent-routes",
        json={"role": "scriptwriter", "provider_profile_id": profile.json()["id"]},
    )
    assert route.status_code == 200, route.text
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("剧本.txt", SCRIPT.encode("utf-8"), "text/plain")},
    ).json()

    calls: list = []

    def responder(messages, schema):
        calls.append(messages)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = responder

    response = _prepare(client, project_id, document)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["scenes"]) == 2, body
    assert [scene["scene_key"] for scene in body["scenes"]] == ["S1", "S2"]
    assert all(scene["status"] == "PREPARED" for scene in body["scenes"]), body
    assert len(body["preparations"]) == 2
    assert body["source_span_ids"], body
    assert len(calls) == 2

    # The second request reuses the stored answers instead of paying again.
    again = _prepare(client, project_id, document)
    assert again.status_code == 200, again.text
    assert all(scene["status"] == "REUSED" for scene in again.json()["scenes"])
    assert len(calls) == 2, "a repeated preparation must not call the provider again"
    with factory() as db:
        artifacts = db.query(AgentArtifact).filter(AgentArtifact.kind == "ScriptPreparation").all()
        assert len(artifacts) == 2


def test_a_scene_that_invents_a_span_is_refused(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id = client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]
    profile = client.post(
        "/api/v1/provider-profiles", json={"name": "writer-mock", "kind": "mock", "model": "writer"}
    )
    assert profile.status_code == 201, profile.text
    route = client.put(
        "/api/v1/agent-routes",
        json={"role": "scriptwriter", "provider_profile_id": profile.json()["id"]},
    )
    assert route.status_code == 200, route.text
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("剧本.txt", SCRIPT.encode("utf-8"), "text/plain")},
    ).json()

    def responder(messages, _schema):
        payload = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
        return {
            "schema_version": "1.0",
            "scene_key": payload["scene_key"],
            "summary": "x",
            "locked_facts": [],
            "requirements": [
                {
                    "family": "character",
                    "name": "林知远",
                    "description": "三十岁上下。",
                    "source_span_ids": ["S1#p99"],
                    "questions": [],
                }
            ],
            "questions": [],
        }

    client.app.state.mock_llm_responder = responder

    response = _prepare(client, project_id, document)
    assert response.status_code == 200, response.text
    scenes = response.json()["scenes"]
    assert all(scene["status"] == "FAILED" for scene in scenes), scenes
    assert all(scene["error_code"] == "MATERIAL_SOURCE_SPAN_UNKNOWN" for scene in scenes), scenes
    assert response.json()["preparations"] == []


def test_one_failed_scene_does_not_stop_the_others(api, monkeypatch):
    """A long script must not lose the scenes that did work."""
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id = client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]
    profile = client.post(
        "/api/v1/provider-profiles", json={"name": "writer-mock", "kind": "mock", "model": "writer"}
    )
    assert profile.status_code == 201, profile.text
    route = client.put(
        "/api/v1/agent-routes",
        json={"role": "scriptwriter", "provider_profile_id": profile.json()["id"]},
    )
    assert route.status_code == 200, route.text
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={
            "file": (
                "剧本.txt",
                "场景一：\n甲。\n\n场景二：\n乙。\n\n场景三：\n丙。\n".encode(),
                "text/plain",
            ),
        },
    ).json()

    def responder(messages, schema):
        payload = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
        if payload["scene_key"] == "S2":
            raise RuntimeError("the provider fell over")
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = responder

    response = _prepare(client, project_id, document)
    assert response.status_code == 200, response.text
    scenes = response.json()["scenes"]
    assert [scene["status"] for scene in scenes] == ["PREPARED", "NEEDS_REVIEW", "PREPARED"], scenes
    assert len(response.json()["preparations"]) == 2, response.json()


def test_a_new_scene_input_does_not_inherit_an_unknown_from_a_previous_input(api, monkeypatch):
    client, factory, settings = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project_id = client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]
    profile = client.post(
        "/api/v1/provider-profiles", json={"name": "writer-mock", "kind": "mock", "model": "writer"}
    )
    assert profile.status_code == 201, profile.text
    route = client.put(
        "/api/v1/agent-routes",
        json={"role": "scriptwriter", "provider_profile_id": profile.json()["id"]},
    )
    assert route.status_code == 200, route.text
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("剧本.txt", SCRIPT.encode("utf-8"), "text/plain")},
    ).json()

    calls: list = []

    def responder(messages, schema):
        calls.append(messages)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = responder
    first = _prepare(client, project_id, document)
    assert first.status_code == 200, first.text
    assert len(calls) == 2
    with factory() as db:
        task = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id == "scriptwriter").first()
        assert task.request_identity
        task.status = "NEEDS_REVIEW"
        task.error_code = "LLM_TIMEOUT"
        task.trace = {**task.trace, "phase": "FAILED", "external_outcome": "UNKNOWN"}
        db.commit()

    # A fresh submission with a new locked fact is a different input.  The old
    # UNKNOWN belongs to the old input and must not silence the new request.
    second = _prepare(client, project_id, document,
                      locked_story_facts=["The film uses cel animation."])
    assert second.status_code == 200, second.text
    assert len(calls) > 2, "the new input must actually be sent to the provider"
    with factory() as db:
        rows = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id == "scriptwriter").all()
        assert len(rows) > 2

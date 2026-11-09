"""J05: a queued text task continues on the model it was queued with."""

from __future__ import annotations

from shine.llm.gateway import LLMGateway
from shine.llm.mock import MockLLMProvider
from shine.llm.secrets import SecretStore
from shine.materialprep import story
from shine.models import AgentTaskRecord

from .test_material_scene_safety import setup_scene
from .test_story_preparation import _queue, responder

LOCAL = {"X-Shine-Local-Action": "1"}


def _second_profile(client, project: str, name: str, model: str) -> str:
    response = client.post("/api/v1/provider-profiles", json={
        "name": name, "kind": "openai_compatible",
        "base_url": "https://provider.example/v1", "model": model,
        "api_key": "secret-for-tests",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_an_edited_route_does_not_stop_the_queued_outline(api, monkeypatch):
    """The route preview names the queued task and the worker keeps its model."""
    client, factory, project, doc = setup_scene(api, monkeypatch, "场景一：房间\n阿德：出发。")
    task_id = _queue(client, project, doc)
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        pinned_profile = task.model_profile_id
        pinned_model = task.model_id
    replacement = _second_profile(client, project, "new-writer", "writer-b")

    preview = client.post("/api/v1/agent-routes/preview", json={
        "updates": [{"role": "scriptwriter", "provider_profile_id": replacement}],
    }, headers=LOCAL)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["can_apply"] is True, body
    outline_task = next(row for row in body["affected_tasks"] if row["task_id"] == task_id)
    assert outline_task["continuity"] == "PINNED_MODEL"
    assert outline_task["consumes_role"] == "scriptwriter"

    saved = client.put("/api/v1/agent-routes", json={
        "role": "scriptwriter", "provider_profile_id": replacement,
    })
    assert saved.status_code == 200, saved.text

    seen: list[str] = []

    def factory_for(config, _responder):
        seen.append(config.model)
        return MockLLMProvider(config, responder)

    gateway = LLMGateway(
        SecretStore(api[2].secret_store_dir),
        provider_factory=factory_for,
    )
    for _ in range(70):
        if not story.run_pending_story_task(factory, api[2], "pinned-worker", gateway=gateway):
            break

    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "SUCCEEDED", (task.status, task.error_code, task.trace)
        assert task.model_profile_id == pinned_profile
        assert task.model_id == pinned_model
    assert seen, "the queued task must really call its pinned model"
    assert set(seen) == {pinned_model}, seen


def test_a_pinned_model_that_is_gone_stops_by_name_and_keeps_the_task(api, monkeypatch):
    """An unusable snapshot is a named review, never a silent provider switch."""
    client, factory, project, doc = setup_scene(api, monkeypatch, "场景一：房间\n阿德：出发。")
    task_id = _queue(client, project, doc)
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        task.model_profile_id = "profile-that-was-deleted"
        task.model_id = "writer-that-was-deleted"

    seen: list[str] = []

    def factory_for(config, _responder):
        seen.append(config.model)
        return MockLLMProvider(config, responder)

    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), provider_factory=factory_for)
    assert story.run_pending_story_task(factory, api[2], "pinned-worker", gateway=gateway)
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "NEEDS_REVIEW"
        assert task.error_code == "STORY_PROVIDER_PIN_INVALID"
        # Its own identity is still there, so the person can decide what next.
        assert task.request_identity
    assert seen == [], "an unusable pin must not call another provider"

"""No external replay and no loss of the immutable script source."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from shine.materialprep.source_spans import segment_document
from shine.models import AgentTaskRecord
from tests.test_material_scenes import _prepare, _responder


def setup_scene(api, monkeypatch, script="场景一：房间\n阿德：出发。"):
    client, factory, _ = api
    monkeypatch.setenv("SHINE_AGENT_STEP_DELAY_MS", "0")
    project = client.post("/api/v1/projects", json={"title": "safety"}).json()["id"]
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "safety-writer", "kind": "mock", "model": "writer"}).json()["id"]
    client.put("/api/v1/agent-routes", json={"role": "scriptwriter", "provider_profile_id": profile})
    doc = client.post(f"/api/v1/projects/{project}/documents", files={
        "file": ("script.txt", script.encode(), "text/plain")}).json()
    return client, factory, project, doc


def test_unknown_timeout_refresh_and_new_gateway_never_replay(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []
    def lost(*args):
        calls.append(1)
        raise TimeoutError("response lost")
    client.app.state.mock_llm_responder = lost
    first = _prepare(client, project, doc).json()["scenes"][0]
    second = _prepare(client, project, doc).json()["scenes"][0]
    assert first["status"] == second["status"] == "NEEDS_REVIEW"
    assert second["task_id"]
    assert second["external_outcome"] == "UNKNOWN"
    assert len(calls) == 1
    # A fresh session/Gateway after a process restart reads the same DB claim.
    from shine.api_common import llm_gateway
    # HTTP dependency builds a new gateway for every request.
    assert callable(llm_gateway)
    assert _prepare(client, project, doc).json()["scenes"][0]["task_id"] == second["task_id"]
    with factory() as db:
        rows = list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project)))
        assert len(rows) == 1 and rows[0].request_identity


def test_two_independent_sessions_claim_once_and_running_returns_original(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def held(messages, schema):
        calls.append(1)
        entered.set()
        assert finish.wait(10)
        return _responder(messages, schema)
    client.app.state.mock_llm_responder = held
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_prepare, client, project, doc)
        try:
            assert entered.wait(10)
            second = pool.submit(_prepare, client, project, doc).result(10)
            original = second.json()["scenes"][0]
            assert original["status"] == "RUNNING"
            assert original["task_id"]
            assert len(calls) == 1
        finally:
            finish.set()
        assert first.result(10).json()["scenes"][0]["status"] == "PREPARED"
    assert len(calls) == 1


@pytest.mark.parametrize("script", [
    "前言锁定事实：钥匙在阿德手里\n人物：阿德\n\n场景一：房间\n阿德：出发。",
    "场景一：\n" + "事件\n" * 230,
    "场景一：\n" + "字" * 9000,
    "字" * 15001,
    "\r\n前言\r\n" + "SCENE 1\n动作\n" * 250,
], ids=["preamble", "many_lines", "heading_long_line", "long_line", "chapters"])
def test_source_coverage_offsets_and_limits(script):
    scenes = segment_document(script)
    assert "".join(scene.text for scene in scenes) == script
    spans = [span for scene in scenes for span in scene.spans]
    assert "".join(span.text for span in spans) == script
    assert spans[0].start_offset == 0 and spans[-1].end_offset == len(script)
    for left, right in zip(spans, spans[1:], strict=False):
        assert left.end_offset == right.start_offset
    assert all(span.text == script[span.start_offset:span.end_offset] for span in spans)
    assert all(len(scene.text) <= 4000 and len(scene.spans) <= 200 for scene in scenes)
    assert scenes == segment_document(script)


@pytest.mark.parametrize("forgery", ["scene_key", "cross_scene"])
def test_answer_cannot_cite_another_scene(api, monkeypatch, forgery):
    client, factory, project, doc = setup_scene(api, monkeypatch, "场景一：甲\n场景二：乙")
    def responder(messages, schema):
        result = _responder(messages, schema)
        other = "S2" if result["scene_key"] == "S1" else "S1"
        if forgery == "scene_key":
            result["scene_key"] = other
        else:
            result["requirements"][0]["source_span_ids"] = [f"{other}#p1"]
        return result
    client.app.state.mock_llm_responder = responder
    result = _prepare(client, project, doc).json()
    assert not result["preparations"]
    assert all(scene["status"] == "FAILED" for scene in result["scenes"])



def test_explicit_replacement_preserves_unknown_and_is_idempotent(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []
    def lost(*args):
        calls.append(1)
        raise TimeoutError("response lost")
    client.app.state.mock_llm_responder = lost
    _prepare(client, project, doc)
    original = _prepare(client, project, doc).json()["scenes"][0]["task_id"]
    replacement = {"replace_unknown_task_id": original, "replacement_key": "explicit-new-request"}
    _prepare(client, project, doc, **replacement)
    _prepare(client, project, doc, **replacement)
    assert len(calls) == 2
    with factory() as db:
        tasks = list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project)))
        assert len(tasks) == 2
        assert db.get(AgentTaskRecord, original).trace["external_outcome"] == "UNKNOWN"
        assert sum(t.trace.get("replaces_unknown_task_id") == original for t in tasks) == 1


def test_long_scene_fits_real_context_budget_without_losing_source(api, monkeypatch):
    import json
    client, factory, project, doc = setup_scene(api, monkeypatch, "场景一：房间\n" + "长剧情" * 3100)
    contexts = []
    def respond(messages, schema):
        context = json.loads(messages[-1].content.split("\n\nReturn one JSON object")[0])
        contexts.append(context)
        return _responder(messages, schema)
    client.app.state.mock_llm_responder = respond
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.materialprep.scenes import prepare_scenes
    from shine.models import ProjectDocument
    with factory() as db:
        document = db.get(ProjectDocument, doc["id"])
    gateway = LLMGateway(SecretStore(api[2].secret_store_dir), mock_responder=respond)
    result = prepare_scenes(gateway, factory, project, document)
    assert _prepare(client, project, doc).status_code == 409
    assert len(contexts) > 3
    assert all(r["status"] == "PREPARED" for r in result["scenes"])
    spans = [span for c in contexts for span in c["source_spans"]]
    assert spans[0]["start_offset"] == 0
    assert spans[-1]["end_offset"] == len("场景一：房间\n" + "长剧情" * 3100)
    assert all(a["end_offset"] == b["start_offset"] for a, b in zip(spans, spans[1:], strict=False))
    analyzed = client.post(f"/api/v1/projects/{project}/material-preparation/analyze", json={
        "document_id": doc["id"], "expected_document_hash": doc["sha256"],
        "idempotency_key": "budgeted-source", "scene_preparations": result["preparations"],
    }, headers={"x-shine-local-action": "1"})
    assert analyzed.status_code == 202, analyzed.text

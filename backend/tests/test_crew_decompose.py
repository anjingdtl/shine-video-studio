"""WP7: per-beat decomposition of the scriptwriter (small focused calls).

Proves the helper assembles the original ScriptBreakdown from one globals call
plus per-beat calls, each owning a distinct sub-task record, without changing
the downstream contract.  Uses a schema-aware mock responder (the decomposition
sends small per-beat schemas, not the whole breakdown).
"""

from __future__ import annotations

from sqlalchemy import select

from shine.agents.crew_decompose import _split_beat_candidates, decompose_scriptwriter
from shine.agents.film_contracts import FilmRunRequest, ScriptBreakdown
from shine.h3.rulebook import digest
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentTaskRecord, Project
from tests.conftest import create_project, freeze_style

SCENE = (
    "老周推开钟表铺的门，雨水顺着檐角滴落。\n\n"
    "灰雨衣女人站在柜台前，手里攥着一块停摆的怀表。\n\n"
    "「修好它，」她说，「无论代价。」"
)


def _responder(messages, schema):
    props = schema.get("properties") or {}
    if "character_names" in props:
        return {
            "character_names": {"S1": "老周", "S2": "灰雨衣女人"},
            "prompt_story_facts": ["The watch has stopped."],
            "prompt_style": None,
        }
    # SceneBeat schema: one beat per call, id derived from the requested index.
    user = " ".join(str(m.content) for m in messages if m.role == "user")
    import json
    data, _ = json.JSONDecoder().raw_decode(user.lstrip())
    idx = data.get("beat_index", 1)
    return {
        "id": f"b{idx}",
        "description": f"beat {idx} visual action",
        "characters": ["老周"] if idx == 1 else ["灰雨衣女人"],
        "dialogue": [],
    }


def test_split_beat_candidates_keeps_paragraphs():
    parts = _split_beat_candidates(SCENE)
    assert len(parts) == 3
    assert all(parts)


def test_decompose_scriptwriter_assembles_breakdown_from_small_calls(api):
    client, factory, settings = api
    project_id = create_project(client, "雨夜钟表铺")
    freeze_style(client, project_id)
    assert client.post(f"/api/v1/projects/{project_id}/rulepack/freeze", json={}).status_code == 200
    profile = client.post("/api/v1/provider-profiles",
                          json={"name": "decompose-mock", "kind": "mock", "model": "crew"}).json()
    assert client.put("/api/v1/agent-routes",
                      json={"role": "scriptwriter", "provider_profile_id": profile["id"]}).status_code == 200

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=_responder)
    from shine.h3.rulebook import H3RulebookRegistry
    with factory() as db:
        project = db.get(Project, project_id)
        pack = H3RulebookRegistry(settings.data_dir / "h3-source-cache").for_project(db, project_id)
        parent = AgentTaskRecord(
            project_id=project_id, role_id="scriptwriter", input_hash=digest({"scene": SCENE}),
            style_version_id=project.frozen_style_version_id, asset_manifest_hash=digest([]),
            rulepack_id=pack.id, rulepack_hash=pack.sha256,
            model_profile_id=profile["id"], model_id="crew", schema_version="1.1",
            status="RUNNING", trace={"phase": "PREPARING"},
        )
        db.add(parent)
        db.commit()
        body = FilmRunRequest(brief=SCENE, locked_story_facts=["The watch has stopped."])
        context = {"assets": [], "style": None}
        payload, usage, latency, sub_roles = decompose_scriptwriter(
            db, gateway, project_id, body=body, context=context,
            system_prompt="You are the scriptwriter.", parent_task=parent,
            output_ceiling=8192,
        )
        breakdown = ScriptBreakdown.model_validate(payload)
        assert len(breakdown.beats) == 3
        assert breakdown.character_names == {"S1": "老周", "S2": "灰雨衣女人"}
        assert breakdown.prompt_story_facts == ["The watch has stopped."]
        assert breakdown.locked_story_facts == ["The watch has stopped."]
        # one globals call + three beat calls, each its own sub-task role
        assert sub_roles == ["scriptwriter_globals"] + ["scriptwriter_beat"] * 3
        assert usage.get("completion_tokens", 0) >= 0
        sub_tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id.in_(["scriptwriter_globals", "scriptwriter_beat"]))).all()
        assert len(sub_tasks) == 4
        assert all(t.status == "SUCCEEDED" for t in sub_tasks)
        assert all(t.trace.get("parent_task_id") == parent.id for t in sub_tasks)
        assert all(t.trace.get("decomposed") is True for t in sub_tasks)
        # the parent role's own record is untouched by the sub-calls
        assert parent.role_id == "scriptwriter"
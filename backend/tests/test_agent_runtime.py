from __future__ import annotations

import io
import json
import zipfile

import pytest

from shine.agents.planner import HeuristicScriptPlanner, ShotPlanner, build_context
from shine.agents.runtime import ProducerAgentRuntime
from shine.agents.script import ScriptParseError, parse_document
from shine.agents.tools import AssetView
from shine.schemas import ScriptPlan, StyleBible
from tests.conftest import PNG, approve_lock, create_project, freeze_style, upload_asset


def test_txt_markdown_and_json_scripts_are_parsed():
    text = parse_document("剧本.txt", "雾港\n韩魁：账册在哪儿？\n".encode())
    assert text.title == "雾港"
    assert "韩魁" in text.text and text.source_format == "text"
    markdown = parse_document("剧本.md", "# 雾港\n\n韩魁：账册在哪儿？\n".encode())
    assert markdown.title == "雾港" and markdown.source_format == "markdown"
    structured = parse_document(
        "剧本.json",
        json.dumps({"title": "雾港", "script": "韩魁：账册在哪儿？", "characters": ["韩魁"]}).encode(),
    )
    assert structured.source_format == "json"
    assert structured.parsed["characters"] == ["韩魁"]
    assert structured.text == "韩魁：账册在哪儿？"


def test_docx_script_is_parsed_without_external_office_runtime():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>雾港</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>韩魁：账册在哪儿？</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    parsed = parse_document("剧本.docx", buffer.getvalue())
    assert parsed.source_format == "docx"
    assert "账册在哪儿" in parsed.text


def test_pdf_parser_is_wired_and_rejects_empty_documents():
    from pypdf import PdfWriter

    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    with pytest.raises(ScriptParseError) as exc:
        parse_document("空白.pdf", buffer.getvalue())
    assert exc.value.code == "SCRIPT_EMPTY"


def test_unsupported_script_format_is_rejected():
    with pytest.raises(ScriptParseError) as exc:
        parse_document("恶意.exe", b"MZ")
    assert exc.value.code == "SCRIPT_FORMAT_UNSUPPORTED"


def test_heuristic_planner_builds_scenes_beats_and_characters():
    result = HeuristicScriptPlanner().plan("场景一：码头\n韩魁：账册在哪儿？\n阿德：杜万山带上钟楼了。", "雾港")
    assert result.planner == "heuristic"
    assert result.plan.title == "雾港"
    assert result.plan.episodes[0].scenes
    scene = result.plan.episodes[0].scenes[0]
    assert scene.beats
    assert any("韩魁" in beat.characters for beat in scene.beats)


def test_llm_script_planner_validates_the_contract():
    class GoodModel:
        def complete_json(self, *, system, user, schema):
            return {
                "schema_version": "1.0",
                "title": "雾港",
                "episodes": [
                    {
                        "name": "第一集",
                        "scenes": [
                            {
                                "name": "码头",
                                "location": "夜",
                                "beats": [{"summary": "交换线索", "characters": []}],
                            }
                        ],
                    }
                ],
            }

    plan = ProducerAgentRuntime(GoodModel()).analyze_script("text", title="雾港")
    assert isinstance(plan, ScriptPlan)
    assert plan.episodes[0].scenes[0].beats[0].summary == "交换线索"

    class BadModel:
        def complete_json(self, *, system, user, schema):
            return {"schema_version": "1.0", "title": "雾港", "episodes": "not-a-list"}

    with pytest.raises(ValueError):
        ProducerAgentRuntime(BadModel()).analyze_script("text")


def test_shot_planner_uses_only_approved_assets_and_keeps_the_frozen_style():
    assets = (
        AssetView("a1", "韩魁.png", "image", "character", "韩魁", ("主角",), None, True, True),
        AssetView("a2", "未批准.png", "image", "character", "阿德", (), None, False, False),
        AssetView("a3", "码头.png", "image", "scene", "码头", (), None, True, True),
    )
    context = build_context(
        project_id="p",
        title="雾港",
        language="Chinese",
        style_version_id="style-v1",
        style=StyleBible(
            photography_language="平视",
            shot_blocking="轴线",
            color_palette="青灰",
            lighting_tone="侧光",
            lens_texture="浅景深",
            editing_rhythm="停顿后切",
            sound_performance="自然语速",
        ),
        assets=list(assets),
    )
    plan = HeuristicScriptPlanner().plan("场景一：码头\n韩魁：账册在哪儿？", "雾港").plan
    proposals, kind, _warnings = ShotPlanner().propose(
        plan, style_version_id="style-v1", style=context.style, context=context, max_shots=3
    )
    assert kind == "heuristic"
    assert proposals
    proposal = proposals[0]
    assert proposal.spec.style_version_id == "style-v1"
    assert "a2" not in proposal.spec.required_asset_ids
    assert "a1" in proposal.spec.required_asset_ids
    assert proposal.spec.dialogue[0].speaker_id == "韩魁"
    assert not hasattr(ProducerAgentRuntime(None), "approve_asset")


def test_agent_plan_api_uses_heuristic_when_no_route_is_configured(api):
    client, _factory, _settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id, PNG + b"agent", "韩魁.png", role="character", entity_id="韩魁")
    approve_lock(client, asset["id"])
    response = client.post(
        f"/api/v1/projects/{project_id}/agent/plan",
        json={"text": "场景一：码头\n韩魁：账册在哪儿？", "max_shots": 3, "apply": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planner"] == "heuristic"
    assert body["applied_shot_ids"]
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert shots[0]["spec"]["style_version_id"] == style["id"]
    assert any(asset["id"] in shot["spec"]["required_asset_ids"] for shot in shots)


def test_agent_plan_api_rejects_invalid_json_and_wrong_style(api):
    client, _factory, _settings = api
    project_id = create_project(client)
    freeze_style(client, project_id)
    asset = upload_asset(client, project_id, PNG + b"agent2", "韩魁.png", role="character", entity_id="韩魁")
    approve_lock(client, asset["id"])
    profile = client.post(
        "/api/v1/provider-profiles",
        json={"name": "mock-agent", "kind": "mock", "model": "mock"},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]
    for role in ("script_planner", "shot_planner"):
        assert (
            client.put(
                "/api/v1/agent-routes",
                json={"role": role, "provider_profile_id": profile_id},
            ).status_code
            == 200
        )

    valid_plan = {
        "schema_version": "1.0",
        "title": "雾港",
        "episodes": [
            {
                "name": "第一集",
                "scenes": [{"name": "码头", "location": "夜", "beats": [{"summary": "交换线索", "characters": []}]}],
            }
        ],
    }
    wrong_style = {
        "shots": [
            {
                "name": "错误风格",
                "spec": {
                    "story_beat": "交换线索",
                    "duration_target_s": 6,
                    "style_version_id": "another-style",
                    "required_asset_ids": [asset["id"]],
                    "characters": [],
                    "dialogue": [],
                },
            }
        ]
    }

    def responder(messages, schema):
        if "episodes" in (schema.get("properties") or {}):
            return valid_plan
        return wrong_style

    client.app.state.mock_llm_responder = responder
    rejected = client.post(
        f"/api/v1/projects/{project_id}/agent/plan",
        json={"text": "韩魁：账册在哪儿？", "apply": True},
    )
    assert rejected.status_code == 422, rejected.text
    assert "style" in rejected.text

    client.app.state.mock_llm_responder = lambda messages, schema: "not-json"
    invalid = client.post(
        f"/api/v1/projects/{project_id}/agent/plan",
        json={"text": "韩魁：账册在哪儿？"},
    )
    assert invalid.status_code == 502, invalid.text
    assert invalid.json()["detail"]["code"] == "LLM_FORMAT"


def test_agent_plan_api_creates_shots_from_structured_llm_output(api):
    client, _factory, _settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id, PNG + b"agent3", "韩魁.png", role="character", entity_id="韩魁")
    approve_lock(client, asset["id"])
    profile = client.post(
        "/api/v1/provider-profiles",
        json={"name": "mock-agent-2", "kind": "mock", "model": "mock"},
    )
    profile_id = profile.json()["id"]
    for role in ("script_planner", "shot_planner"):
        client.put("/api/v1/agent-routes", json={"role": role, "provider_profile_id": profile_id})

    def responder(messages, schema):
        if "episodes" in (schema.get("properties") or {}):
            return {
                "schema_version": "1.0",
                "title": "雾港",
                "episodes": [
                    {
                        "name": "第一集",
                        "scenes": [
                            {
                                "name": "码头",
                                "location": "雨夜码头",
                                "beats": [{"summary": "韩魁追问账册", "characters": ["韩魁"]}],
                            }
                        ],
                    }
                ],
            }
        return {
            "shots": [
                {
                    "name": "码头追问",
                    "spec": {
                        "story_beat": "韩魁在雨夜码头追问账册下落",
                        "duration_target_s": 6,
                        "style_version_id": style["id"],
                        "required_asset_ids": [asset["id"]],
                        "characters": ["韩魁"],
                        "dialogue": [
                            {"speaker_id": "韩魁", "text": "账册在哪儿？", "start_s": 0.4, "end_s": 2.0}
                        ],
                        "sound": {"environment": "雨夜码头", "music_policy": "low_or_none"},
                    },
                }
            ]
        }

    client.app.state.mock_llm_responder = responder
    response = client.post(
        f"/api/v1/projects/{project_id}/agent/plan",
        json={"text": "韩魁：账册在哪儿？", "max_shots": 2, "apply": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planner"] == "llm"
    assert len(body["shots"]) == 1 and len(body["applied_shot_ids"]) == 1
    shot = client.get(f"/api/v1/projects/{project_id}/shots").json()[0]
    assert shot["spec"]["dialogue"][0]["speaker_id"] == "韩魁"
    assert shot["bindings"][0]["asset_id"] == asset["id"]

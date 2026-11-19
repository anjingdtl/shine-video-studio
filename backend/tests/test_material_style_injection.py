"""素材出图必须继承导演风格/影片类型与剧本时代（三原则：一套事实、一个composer）。

2026-11-19 穿测：用户选动画模式，素材中心却出仿真人、现代人。根因是素材中心
T2I 用原文直送、负向为空，不读任何项目事实；brief producer 也拿不到全局世界观。
本文件固定：唯一 composer 注入影片媒介+世界/时代，素材中心与素材准备同源。
"""
from __future__ import annotations

import pytest

from shine.h3.rulebook import digest
from shine.materialprep import image_prompts
from shine.materialprep.contracts import MaterialImageBrief
from shine.materialprep.prompt_source import (
    compose_user_image_prompt,
    film_style_for,
    project_visual_facts,
)
from shine.models import AgentArtifact

from .conftest import create_project, style_payload

pytestmark = pytest.mark.integration


def _freeze_animation(client, project_id: str) -> None:
    draft = client.put(f"/api/v1/projects/{project_id}/style/draft",
                       json={**style_payload(), "film_type": "animation"})
    assert draft.status_code == 200, draft.text
    frozen = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={})
    assert frozen.status_code == 201, frozen.text


def _add_bible(factory, project_id: str, world: str) -> None:
    from shine.models import AgentTaskRecord
    payload = {"world": world, "characters": []}
    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=project_id, role_id="producer", input_hash=digest(payload),
            asset_manifest_hash=digest(payload), rulepack_id="test", rulepack_hash=digest("test"),
            model_profile_id="test", model_id="test", schema_version="1.0",
            status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        db.add(AgentArtifact(
            project_id=project_id, kind="FourStageGlobalStoryBible", schema_version="1.0",
            payload_json=payload, sha256=digest(payload), producer_role="producer",
            source_task_id=task.id, input_revision_ids=[],
        ))


def test_user_prompt_compose_injects_medium_and_world_and_allows_chinese():
    facts = {"film_style": film_style_for({"film_type": "animation"}),
             "world": "中世纪欧洲奇幻，巫师与教会并存，服饰为粗麻长袍与皮甲。"}
    composed = compose_user_image_prompt(
        user_prompt="罗兰，青年男性，短发，神情疲惫", family="character", facts=facts)
    assert "cel-shaded" in composed.prompt          # 动画媒介注入
    assert "中世纪欧洲奇幻" in composed.prompt        # 时代/世界注入
    assert "罗兰" in composed.prompt                # 用户原文保留（中文不拒）
    assert "photorealistic" in composed.negative_prompt  # 动画排除仿真人


def test_producer_compose_still_refuses_chinese():
    brief = MaterialImageBrief.model_construct(appearance="中文描述", must_keep=[], avoid=[], questions=[])
    with pytest.raises(image_prompts.ImagePromptError) as exc:
        image_prompts.compose("character", brief, film_style="")
    assert exc.value.code == "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE"


def test_project_visual_facts_reads_frozen_type_and_world(api):
    client, factory, _settings = api
    project_id = create_project(client)
    _freeze_animation(client, project_id)
    _add_bible(factory, project_id, "古代东方，城镇以木石建筑为主。")
    with factory() as db:
        facts = project_visual_facts(db, project_id)
    assert facts["film_type"] == "animation"
    assert "cel-shaded" in facts["film_style"]
    assert "古代东方" in facts["world"]


def test_image_job_with_project_inherits_style_and_negative(api):
    client, factory, _settings = api
    project_id = create_project(client)
    _freeze_animation(client, project_id)
    _add_bible(factory, project_id, "中世纪欧洲，人物着长袍皮甲，不得现代服装。")
    response = client.post("/api/v1/asset-center/image-jobs", json={
        "idempotency_key": "style-inject-0001", "mode": "T2I", "category": "character",
        "prompt": "罗兰，青年男性，短发", "project_id": project_id,
    })
    assert response.status_code == 202, response.text
    task_id = response.json()["items"][0]["id"]
    from shine.asset_center.models import LibraryRenderTask
    with factory() as db:
        row = db.get(LibraryRenderTask, task_id)
        payload = dict(row.payload or {})
    assert payload["project_visual_facts"]["film_type"] == "animation"
    assert "cel-shaded" in payload["prompt"]
    assert "中世纪欧洲" in payload["prompt"]
    assert "photorealistic" in payload["negative_prompt"]
    assert payload["user_prompt"] == "罗兰，青年男性，短发"

    facts_view = client.get(f"/api/v1/asset-center/visual-facts?project_id={project_id}")
    assert facts_view.status_code == 200
    assert facts_view.json()["film_type"] == "animation"


def test_image_job_without_project_keeps_user_prompt_verbatim(api):
    client, factory, _settings = api
    response = client.post("/api/v1/asset-center/image-jobs", json={
        "idempotency_key": "style-inject-0002", "mode": "T2I", "category": "character",
        "prompt": "a quiet street after rain",
    })
    assert response.status_code == 202, response.text
    task_id = response.json()["items"][0]["id"]
    from shine.asset_center.models import LibraryRenderTask
    with factory() as db:
        payload = dict(db.get(LibraryRenderTask, task_id).payload or {})
    assert payload["prompt"] == "a quiet street after rain"
    assert "project_visual_facts" not in payload


def _add_outline(factory, project_id: str, summary: str, *, confirmed: bool) -> None:
    from shine.models import AgentTaskRecord, utcnow
    payload = {"schema_version": "1.0", "summary": summary}
    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=project_id, role_id="producer", input_hash=digest(payload),
            asset_manifest_hash=digest(payload), rulepack_id="test", rulepack_hash=digest("test"),
            model_profile_id="test", model_id="test", schema_version="1.0",
            status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        db.add(AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest(payload), producer_role="producer",
            source_task_id=task.id, input_revision_ids=[],
            approved_by="local-user" if confirmed else None,
            approved_at=utcnow() if confirmed else None,
        ))


def test_streamline_world_falls_back_to_the_confirmed_outline(api):
    """无全局圣经的作品：已确认大纲的梗概是世界事实；未确认草稿不得顶替。"""
    client, factory, _settings = api
    project_id = create_project(client)
    _add_outline(factory, project_id, "中世纪欧洲奇幻，女巫与教会并存，城堡与边陲镇。", confirmed=True)
    _add_outline(factory, project_id, "现代都市重写版。", confirmed=False)
    with factory() as db:
        facts = project_visual_facts(db, project_id)
    assert "中世纪欧洲奇幻" in facts["world"]
    assert "现代都市" not in facts["world"]


def test_requirement_prompt_composes_world_note(api):
    from shine.materialprep.prompt_source import compose_requirement_prompt
    brief = MaterialImageBrief.model_construct(
        appearance="a young man with short black hair in a worn wool coat",
        must_keep=[], avoid=[], questions=[])
    composed = compose_requirement_prompt(
        family="character", brief=brief, visual_direction=None,
        film_style=film_style_for({"film_type": "animation"}),
        world_note="中世纪欧洲奇幻，女巫与教会并存。")
    assert "cel-shaded" in composed.prompt
    assert "中世纪欧洲奇幻" in composed.prompt
    assert "photorealistic" in composed.negative_prompt


def test_brief_context_carries_story_world(api):
    from shine.materialprep.brief import build_context
    client, factory, _settings = api
    project_id = create_project(client)
    _add_bible(factory, project_id, "古代东方，木石建筑。")
    with factory() as db:
        world = project_visual_facts(db, project_id)["world"]
    context = build_context(requirement_payload={"family": "character", "entity_id": "罗兰"},
                            visual_direction={}, locked_facts=[], project_language="zh",
                            story_world=world)
    assert context["story_world"] == "古代东方，木石建筑。"
    assert any("era" in rule or "period" in rule for rule in context["rules"])

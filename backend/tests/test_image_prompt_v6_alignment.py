from copy import deepcopy

from sqlalchemy import select

from shine.agents.crew_prompts import material_image_brief_prompt
from shine.comfyops.models import AssetRequirement
from shine.h3.rulebook import digest
from shine.materialprep import brief, image_prompts, qwen_workflow, runtime
from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection
from shine.materialprep.models import MaterialItem
from shine.materialprep.prompt_source import apply_image_lora_prompts, compose_requirement_prompt
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.conftest import create_project
from tests.test_material_brief import _item


def test_v6_prompt_keeps_identity_before_framing_and_composes_triggers_last():
    source = MaterialImageBrief(
        appearance="A 43-year-old court archivist with a white forelock in a late Song dynasty silk robe",
        must_keep=["white forelock", "burn scar over the right eyebrow", "Burn scar over the right eyebrow"],
        avoid=["modern dress", "watermark"],
    )
    args = dict(family="character", brief=source, visual_direction=MaterialVisualDirection(era="late Song China"),
                world_note="南宋，圆领袍。")
    baseline = compose_requirement_prompt(**args)
    assert baseline.prompt.startswith(source.appearance)
    assert baseline.prompt.count("burn scar over the right eyebrow") == 1
    assert baseline.prompt.index("burn scar over the right eyebrow") < baseline.prompt.index("waist up")
    assert "Even soft diffused lighting" in baseline.prompt
    assert "南宋" not in baseline.prompt
    style = {"style_loras": [{"trigger_word": "<mva>", "trigger_position": "prefix", "negative_trigger_word": "blur"}],
             "style_preset_snapshot": {"positive_prompt": "masterpiece, ray tracing, volumetric rim lighting",
                                       "negative_prompt": "WATERMARK, blur"}}
    styled = compose_requirement_prompt(**args, image_style=style)
    assert styled.prompt.startswith("<mva> " + source.appearance)
    assert styled.prompt.endswith("masterpiece, ray tracing, volumetric rim lighting")
    assert "soft diffused lighting" not in styled.prompt
    assert "grey-white" in styled.prompt
    assert styled.negative_prompt.lower().split(", ").count("blur") == 1
    assert styled.sha256 != baseline.sha256
    repeated = apply_image_lora_prompts(styled.prompt, styled.negative_prompt, style["style_loras"],
                                        style_preset=style["style_preset_snapshot"])
    assert repeated == (styled.prompt, styled.negative_prompt)


def test_dossier_alias_selection_confirmed_sources_and_cache_invalidation(api):
    client, factory, _settings = api
    project_id = create_project(client)
    with factory() as db:
        item = _item(db, project_id)
        item_id = item.id
        requirement = db.get(AssetRequirement, item.requirement_id)
        requirement.payload = {**requirement.payload, "entity_id": "林知远", "scene_keys": ["S1"]}
        task = AgentTaskRecord(project_id=project_id, role_id="producer", input_hash="a" * 64,
                               asset_manifest_hash="a" * 64, rulepack_id="test", rulepack_hash="a" * 64,
                               model_profile_id="test", model_id="test", schema_version="1.0",
                               status="SUCCEEDED", trace={})
        db.add(task)
        db.flush()
        for kind, payload, approved in [
            ("MaterialEntityCatalog", {"characters": [
                {"name": "林先生", "aliases": ["林知远"], "identity": "四十三岁，白色额发，南宋丝绸圆领袍"},
                {"name": "邻人", "identity": "未选人物"},
            ]}, False),
            ("FourStageGlobalStoryBible", {"world": "南宋", "characters": [
                {"name": "林先生", "identity": "江南人，右眉有烫伤疤"}]}, True),
            ("StoryOutline", {"characters": [{"name": "林知远", "introduction": "冷静的档案官"}]}, True),
        ]:
            db.add(AgentArtifact(project_id=project_id, kind=kind, schema_version="1.0", payload_json=payload,
                                 sha256=digest(payload), producer_role="producer", source_task_id=task.id,
                                 input_revision_ids=[], approved_at=utcnow() if approved else None))
        db.commit()

    before = brief._load_context(factory, item_id)
    dossier = before["script_entity_dossier"]
    assert "四十三岁" in dossier["catalog"]["identity"]
    assert "右眉" in dossier["story_bible"]["identity"]
    assert dossier["story_outline"]["introduction"] == "冷静的档案官"
    assert [row["scene_key"] for row in dossier["related_scenes"]] == ["S1"]
    assert "未选人物" not in str(dossier)
    assert "must_keep" in material_image_brief_prompt()
    with factory.begin() as db:
        catalog = db.scalar(select(AgentArtifact).where(AgentArtifact.kind == "MaterialEntityCatalog"))
        updated = deepcopy(catalog.payload_json)
        updated["characters"][0]["identity"] = "四十四岁，白色额发，南宋丝绸圆领袍"
        catalog.payload_json, catalog.sha256 = updated, digest(updated)
    after = brief._load_context(factory, item_id)
    assert before["brief_basis"] != after["brief_basis"]
    with factory.begin() as db:
        item = db.get(MaterialItem, item_id)
        item.payload = {**item.payload, "style_preset": "cinematic_photorealism", "style_loras": [
            {"lora_id": "model-a", "sha256": "a" * 64, "strength_model": .75, "trigger_word": "natural exposure"}]}
    styled = brief._load_context(factory, item_id)
    assert styled["brief_basis"] != after["brief_basis"]
    assert styled["active_style_lora_context"]["positive_triggers"] == ["natural exposure"]


def test_same_v6_version_refreshes_fingerprint_from_composition_contract(monkeypatch):
    config = runtime.ImageRuntimeConfig()
    fresh = runtime.current_fingerprint(config)
    original = image_prompts.template_evidence
    monkeypatch.setattr(runtime, "template_evidence", lambda: {
        **original(), "composition_rules_sha256": "f" * 64,
    })
    assert runtime.current_fingerprint(config) != fresh
    assert qwen_workflow.BASE_GRAPH_SHA256 == "83a2bafb67af29f3a8a5a4b4572f19f940ff5f2c31b64b0cef024eb726d42d85"

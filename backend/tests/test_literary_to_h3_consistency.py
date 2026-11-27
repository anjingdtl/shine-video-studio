"""端到端回归：文学 → 分镜 → 素材生图 → 道具回绑 → H3 提示词一致性防退化。

覆盖 2026-11-25 《星雨茶馆LoRA穿测》核查出的六类系统性缺陷：
1. 空场景/道具生图不再混入 "real human performers" 或整段中文人物剧情摘要，且负向强制排除人物/手部；
2. 大纲角色 ``introduction`` 完整传入素材需求，不再漏读；缺参考补场景不再抄入含人物剧本原文；
3. 戏剧剧本节拍 ``visible_action`` 点名已知角色却漏列 ``characters`` 时被拒止；
4. 分镜板角色占位符 ``{{Ck}}`` 与单人镜双人正文由唯一裁决口静态拦截，并检测镜内 inward/outward 矛盾；
5. H3 编译器 v2.1.0 将正文 ``{{Ck}}`` 确定性编译为本镜 ``<Subject n>``、强化服装一致性、
   区分场景/道具/连续性 ``<Picture k>``，同时 v2.0.0 历史字节精确重放不变；
6. 核心道具按镜精准推导并经唯一采纳执行者 ``bind_adopted_masters_to_shots`` 回绑至镜头与 manifest。
"""

from __future__ import annotations

import pytest

from shine.agents import continuity_engine as engine
from shine.agents.crew_prompts import drama_screenwriter_prompt
from shine.agents.scene_director import scene_director_prompt
from shine.h3.compiler import (
    COMPILER_VERSION,
    LEGACY_ANCHORED_TOKENS_COMPILER_VERSION,
    LEGACY_HANDOFF_V23_COMPILER_VERSION,
    LEGACY_SCOPED_FACTS_COMPILER_VERSION,
    LEGACY_THIN_SHELL_COMPILER_VERSION,
    H3PromptCompiler,
)
from shine.h3.film_type import film_type_sentence, material_film_type_sentence
from shine.h3.rulebook import digest
from shine.literary import drama
from shine.materialprep import brief as material_brief
from shine.materialprep.contracts import MaterialImageBrief
from shine.materialprep.prompt_source import (
    compose_requirement_prompt,
    compose_user_image_prompt,
    film_style_for,
)
from shine.models import AgentArtifact, AgentTaskRecord, Shot, ShotAssetBinding, utcnow
from shine.schemas import (
    FilmType,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from tests.conftest import (
    _approve_lock_keeping_operator,
    create_project,
    create_shot,
    freeze_style,
    upload_asset,
)
from tests.test_material_shot_scene_gap import (
    LOCAL,
    _apply,
    _document,
    _outline,
    _plan,
)

STYLE = StyleBible(
    photography_language="Medium eye-level framing with restrained movement.",
    shot_blocking="Preserve 180-degree axis and sightlines.",
    color_palette="Natural neutral tones with warm practical lamps.",
    lighting_tone="Cool rain window light against warm amber lamp key.",
    lens_texture="Clean realistic 50mm lens with natural film texture.",
    editing_rhythm="Clean cuts following action and reaction.",
    sound_performance="Clear natural Chinese dialogue with restrained rain ambience.",
    film_type=FilmType.LIVE_ACTION,
)


# ---------------------------------------------------------------------------
# P0-1: 空场景与道具生图净化
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scene_and_prop_prompts_never_carry_human_performers_or_outline_plot():
    outline_summary = (
        "雨夜，空荡的老茶馆内，林晚独自坐在桌旁，用软布擦拭一枚停走十年的铜怀表。"
        "陈默收起湿雨伞推门而入，从衣内取出缺失的半张旧照片放到林晚面前。"
    )
    scene_brief = MaterialImageBrief(
        appearance=(
            "An empty old wooden teahouse interior on a rainy night "
            "with warm amber lamplight and lattice windows."
        ),
        must_keep=["Empty teahouse interior", "Rainy night atmosphere"],
        avoid=["Modern signage"],
        questions=[],
    )
    scene_prompt = compose_requirement_prompt(
        family="scene",
        brief=scene_brief,
        visual_direction=None,
        requirement_name="雨夜老茶馆",
        film_style=film_type_sentence(FilmType.LIVE_ACTION),
        world_note=outline_summary,
        world_source="outline_summary",
        style_snapshot={"film_type": "live_action"},
    )
    assert "real human performers" not in scene_prompt.prompt.lower()
    assert "林晚" not in scene_prompt.prompt
    assert "陈默" not in scene_prompt.prompt
    assert "铜怀表" not in scene_prompt.prompt
    assert "live-action film set plate" in scene_prompt.prompt.lower()
    for term in ("people", "person", "human", "man", "woman", "silhouette"):
        assert term in scene_prompt.negative_prompt.lower()

    # 用户资产中心手写场景提示词同样不能被 outline_summary 人物剧情污染
    user_scene = compose_user_image_prompt(
        user_prompt="雨夜空荡老茶馆内景，木桌暖灯",
        family="scene",
        facts={
            "film_style": film_style_for({"film_type": "live_action"}),
            "snapshot": {"film_type": "live_action"},
            "world": outline_summary,
            "world_source": "outline_summary",
        },
    )
    assert "林晚" not in user_scene.prompt
    assert "陈默" not in user_scene.prompt
    assert "real human performers" not in user_scene.prompt.lower()
    assert "people" in user_scene.negative_prompt.lower()

    # 道具同样剥离真人演员句与剧情摘要，并在负向排除手部/持有人
    prop_brief = MaterialImageBrief(
        appearance="An antique weathered brass pocket watch with oxidized case and Roman numerals.",
        must_keep=["Intact antique brass case"],
        avoid=[],
        questions=[],
    )
    prop_prompt = compose_requirement_prompt(
        family="prop",
        brief=prop_brief,
        visual_direction=None,
        requirement_name="铜怀表",
        carrier="object",
        film_style=material_film_type_sentence(FilmType.LIVE_ACTION, "prop"),
        world_note=outline_summary,
        world_source="outline_summary",
        style_snapshot={"film_type": "live_action"},
    )
    assert "real human performers" not in prop_prompt.prompt.lower()
    assert "林晚" not in prop_prompt.prompt
    assert "hands" in prop_prompt.negative_prompt.lower()
    assert "holding" in prop_prompt.negative_prompt.lower()

    # 人物仍保留真人皮肤质感句和世界时代背景
    char_brief = MaterialImageBrief(
        appearance="A poised woman in her late twenties with tied-back dark hair in a tailored dark wool coat.",
        must_keep=[],
        avoid=[],
        questions=[],
    )
    char_prompt = compose_requirement_prompt(
        family="character",
        brief=char_brief,
        visual_direction=None,
        requirement_name="林晚",
        film_style=film_style_for({"film_type": "live_action"}, family="character"),
        world_note=outline_summary,
        world_source="outline_summary",
        style_snapshot={"film_type": "live_action"},
    )
    assert "real human performers" in char_prompt.prompt.lower()
    assert char_prompt.prompt.startswith(char_brief.appearance)
    assert outline_summary not in char_prompt.prompt


@pytest.mark.unit
def test_scene_brief_context_isolates_character_costume_rules():
    ctx = material_brief.build_context(
        requirement_payload={"family": "scene", "entity_id": "雨夜老茶馆", "description": "空荡老茶馆"},
        visual_direction={},
        locked_facts=[],
        project_language="Chinese",
        film_style=material_film_type_sentence(FilmType.LIVE_ACTION, "scene"),
        story_world="雨夜老茶馆里林晚与陈默对视。",
    )
    rules_joined = " ".join(ctx["rules"])
    assert "EMPTY environment plate" in rules_joined
    assert "NEVER mention, imply or depict any person" in rules_joined
    assert "dress and build" not in rules_joined


@pytest.mark.unit
def test_scene_brief_context_keeps_handheld_story_props_out_of_the_environment_plate():
    """场景底图不得把角色携带的道具烤进环境（会发光也不行）。

    2026-11-26 真机复现：剧本里黄铜提灯是莫瑞甘手里的道具，场景简报却把它当成
    实用光源固定在祭坛台阶上；她出场的每一镜又都绑定了提灯道具，同一物体就会在
    画面里出现两次。道具是按镜绑定的独立素材，这条边界写在场景简报的规则处。
    """
    ctx = material_brief.build_context(
        requirement_payload={"family": "scene", "entity_id": "圣殿残垣祭坛",
                             "description": "薄雾弥漫的废墟石砌圣殿内部"},
        visual_direction={},
        locked_facts=["莫瑞甘手提燃烧琥珀火光的黄铜提灯走向祭坛，提灯在石壁投下长影。"],
        project_language="Chinese",
    )
    rules_joined = " ".join(ctx["rules"])
    assert "separate prop materials" in rules_joined
    assert ("NEVER place, mention or depict them in the environment plate, even when they give "
            "off light") in rules_joined
    assert "wall torches" in rules_joined
    # 人物边界不变；道具规则只出现在场景族，不进人物/道具族的简报。
    assert "NEVER mention, imply or depict any person" in rules_joined
    prop_ctx = material_brief.build_context(
        requirement_payload={"family": "prop", "entity_id": "黄铜提灯"},
        visual_direction={}, locked_facts=[], project_language="Chinese",
    )
    assert "separate prop materials" not in " ".join(prop_ctx["rules"])


# ---------------------------------------------------------------------------
# P1: 戏剧剧本在场人物校验 & 提示词约束
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_drama_verdict_refuses_omitted_visible_cast_and_prompt_states_rule():
    assert "silent, seated, listening" in drama_screenwriter_prompt()
    verdict = drama.check_drama_scene(
        {
            "location": "雨夜老茶馆",
            "beats": [
                {
                    "beat_type": "action",
                    "visible_action": "木门被推开，陈默收拢湿伞走进茶馆，停在坐在桌旁的林晚面前。",
                    "characters": ["陈默"],
                    "spoken_lines": [],
                }
            ],
        },
        cast=("林晚", "陈默"),
        locations=("雨夜老茶馆",),
    )
    assert not verdict.accepted
    assert verdict.refusals == [
        {"code": "DRAMA_VISIBLE_CHARACTER_OMITTED", "beat": 0, "character": "林晚"}
    ]


# ---------------------------------------------------------------------------
# P0-2: 分镜主体占位符裁决 + 单人镜双人正文拦截 + H3 编译器 v2.1.0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scene_director_prompt_forbids_costume_invention_and_requires_tokens():
    prompt = scene_director_prompt(
        max_shot_seconds=8.0,
        continuity_predecessor=False,
        translation_supplied=False,
        narration_allowed=False,
    )
    assert "grey cloak" not in prompt
    assert "{{C1}}" in prompt
    assert "Never invent, alter or vary a character's clothing" in prompt


@pytest.mark.unit
def test_continuity_engine_refuses_off_cast_token_and_single_cast_two_person_prose():
    scene = {
        "location": "雨夜老茶馆",
        "beats": [
            {"beat_type": "action", "visible_action": "林晚独自擦怀表。", "characters": ["林晚"], "spoken_lines": []},
            {"beat_type": "action", "visible_action": "陈默推门而入。", "characters": ["陈默"], "spoken_lines": []},
        ],
    }
    tokens = [{"token": "{{C1}}", "character": "林晚"}, {"token": "{{C2}}", "character": "陈默"}]
    ctx = {"max_shot_seconds": 8.0, "continuity_predecessor": False, "character_tokens": tokens}

    def _shot(sid: str, beat: str, character: str, visual: str, composition: str, timeline_action: str):
        return {
            "id": sid,
            "beat_ids": [beat],
            "duration_s": 6.0,
            "visual_description": visual,
            "asset_ids": [],
            "location": "雨夜老茶馆",
            "dialogue": [],
            "camera": {
                "composition": composition,
                "move": "static",
                "motive": "",
                "movement": "The camera holds still.",
                "lighting": "Warm practical lamp.",
            },
            "ambience": "Rain taps the window.",
            "music": "none",
            "blocking": {
                "axis": "group_1",
                "placements": [
                    {"character": character, "zone": "left_mg" if character == "林晚" else "right_bg",
                     "facing": "facing_right" if character == "林晚" else "facing_left",
                     "posture": "seated", "props": []},
                ],
            },
            "environment": {"depth": "Wooden table.", "light": "Warm lamp.", "micro_motion": "Rain."},
            "timeline": [{"start_s": 0.0, "end_s": 6.0, "action": timeline_action,
                          "expression": "Calm.", "camera": "The camera holds still."}],
            "entry_state": "Quiet interior.",
            "exit_state": "Still frame.",
            "transition_motivation": "establishing_reset" if sid == "S1" else "time_or_space_jump",
            "handoff_cue": "",
            "internal_cuts": [],
        }

    # 1) 占位符越界：S2 cast 只有陈默({{C2}})，但正文引用了 {{C1}}(林晚)
    bad_token = {
        "shots": [
            _shot("S1", "B1", "林晚", "{{C1}} polishes the brass watch.", "Medium shot of {{C1}}.",
                  "{{C1}} wipes the watch."),
            _shot("S2", "B2", "陈默", "{{C2}} enters the doorway.", "{{C2}} approaches {{C1}} at the table.",
                  "{{C2}} looks at {{C1}}."),
        ]
    }
    v_token = engine.review_scene_board(bad_token, scene, ctx)
    assert any(r["code"] == "PROSE_CHARACTER_NOT_IN_CAST" and r["token"] == "{{C1}}" for r in v_token.refusals)

    # 2) 复现《星雨茶馆》shot_02：未用占位符，但单人镜正文同时写了 entering man + seated woman，且含 inward/outward 矛盾
    xingyu_s2 = {
        "shots": [
            _shot("S1", "B1", "林晚", "{{C1}} polishes the watch.", "Medium shot of {{C1}}.",
                  "{{C1}} wipes the watch."),
            _shot(
                "S2", "B2", "陈默",
                "The wooden door swings inward while a man carrying a wet umbrella enters.",
                "Wide doorway view; the man enters right, with the woman at left foreground.",
                "The door opens outward and the man gazes at the seated woman.",
            ),
        ]
    }
    v_xingyu = engine.review_scene_board(xingyu_s2, scene, ctx)
    assert any(r["code"] == "SINGLE_CAST_MULTI_PERSON_PROSE" and r["shot_id"] == "S2" for r in v_xingyu.refusals)
    assert any(w["code"] == "SHOT_DIRECTION_CONTRADICTION" and w["shot_id"] == "S2" for w in v_xingyu.warnings)

    # 3) 合规双占位符单人镜放行
    good = {
        "shots": [
            _shot("S1", "B1", "林晚", "{{C1}} polishes the brass watch.", "Medium shot of {{C1}}.",
                  "{{C1}} wipes the watch."),
            _shot("S2", "B2", "陈默", "The wooden door swings inward as {{C2}} enters with a closed wet umbrella.",
                  "Medium doorway shot of {{C2}} stepping inside.",
                  "{{C2}} folds the umbrella and pauses near the threshold."),
        ]
    }
    assert engine.review_scene_board(good, scene, ctx).accepted


@pytest.mark.unit
def test_h3_compiler_v21_resolves_character_tokens_anchors_outfit_and_distinguishes_pictures():
    compiler = H3PromptCompiler()
    # 单人镜：林晚={{C1}}, 陈默={{C2}}，本镜只有陈默，因此 {{C2}} 必须编译为 <Subject 1>
    shot = ShotSpec(
        story_beat="{{C2}} enters the wooden doorway carrying a wet umbrella.",
        duration_target_s=6.5,
        style_version_id="style-1",
        characters=["陈默"],
        character_tokens={"{{C1}}": "林晚", "{{C2}}": "陈默"},
        camera={"composition": "Medium shot of {{C2}} stepping across the wet threshold.",
                "movement": "The camera holds still."},
        blocking={"axis": "group_1", "placements": [
            {"character": "陈默", "zone": "right_bg", "facing": "facing_left",
             "posture": "standing", "props": ["wet umbrella"]}
        ]},
        timeline=[{"start_s": 0.0, "end_s": 6.5,
                   "action": "{{C2}} steps inside and stops beside the wooden table.",
                   "expression": "{{C2}} keeps a guarded gaze.",
                   "camera": "The camera holds still."}],
        entry_state="Rain outside the doorway as {{C2}} pushes it open.",
        exit_state="{{C2}} stands beside the table holding the wet umbrella.",
    )
    manifest = [
        ReferenceManifestItem(
            label="<Subject 1>", asset_id="a-cm", sha256="1" * 64, storage_key="cm.png",
            content_type="image/png", use_as=ReferenceUse.SUBJECT, role="陈默",
            entity_id="陈默", asset_role="character",
        ),
        ReferenceManifestItem(
            label="<Picture 2>", asset_id="a-scene", sha256="2" * 64, storage_key="scene.png",
            content_type="image/png", use_as=ReferenceUse.PICTURE,
            entity_id="雨夜老茶馆", asset_role="scene",
        ),
        ReferenceManifestItem(
            label="<Picture 3>", asset_id="a-prop", sha256="3" * 64, storage_key="watch.png",
            content_type="image/png", use_as=ReferenceUse.PICTURE, role="铜怀表",
            entity_id="铜怀表", asset_role="prop",
        ),
        ReferenceManifestItem(
            label="<Picture 4>", asset_id="a-cont", sha256="4" * 64, storage_key="last.png",
            content_type="image/png", use_as=ReferenceUse.PICTURE, role="continuity_derivative",
        ),
    ]

    compiled = compiler.compile(
        spec=shot, style=STYLE, manifest=manifest, mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_ANCHORED_TOKENS_COMPILER_VERSION,
    )
    assert compiled.compiler_version == LEGACY_ANCHORED_TOKENS_COMPILER_VERSION == "shine-h3-compiler/2.1.0"
    # 2.2.0 / 2.3.0 / 2.4.0 继承 2.1.0 的全部锚定与图片角色行为：对于 cut 且无锁定故事事实的镜头逐字节相同。
    scoped_220 = compiler.compile(
        spec=shot, style=STYLE, manifest=manifest, mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_SCOPED_FACTS_COMPILER_VERSION,
    )
    assert scoped_220.compiler_version == LEGACY_SCOPED_FACTS_COMPILER_VERSION == "shine-h3-compiler/2.2.0"
    assert scoped_220.prompt == compiled.prompt
    handoff_230 = compiler.compile(
        spec=shot, style=STYLE, manifest=manifest, mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_HANDOFF_V23_COMPILER_VERSION,
    )
    assert handoff_230.compiler_version == LEGACY_HANDOFF_V23_COMPILER_VERSION == "shine-h3-compiler/2.3.0"
    assert handoff_230.prompt == compiled.prompt
    current = compiler.compile(spec=shot, style=STYLE, manifest=manifest, mode_hint=GenerationMode.REF2VA)
    assert current.compiler_version == COMPILER_VERSION == "shine-h3-compiler/2.4.0"
    assert current.prompt == compiled.prompt
    desc = compiled.sections["detailed_description"]
    assert "{{C2}}" not in desc and "{{C1}}" not in desc
    assert "<Subject 1> enters the wooden doorway" in desc
    assert "Medium shot of <Subject 1> stepping across" in desc
    assert "<Subject 1> steps inside and stops" in desc
    assert "The shot settles on <Subject 1> stands beside" in desc

    defs = compiled.sections["subject_definitions"]
    ret = compiled.sections["retention_analysis"]
    assert "face, hairstyle and outfit throughout" in defs
    assert "in the same outfit in every frame" in ret
    assert "<Picture 2> is the empty environment and lighting reference for [Shot 1]" in defs
    assert "<Picture 3> is a prop appearance reference for [Shot 1]" in defs
    assert "<Picture 4> is the preceding-shot final-frame continuity reference for [Shot 1]" in defs
    assert "<Picture 2> ([Shot 1] environment reference): partially_preserved" in ret
    assert "<Picture 3> ([Shot 1] prop reference): fully_preserved" in ret
    assert "<Picture 4> ([Shot 1] continuity reference): partially_preserved" in ret

    # 历史 2.0.0 字节原样重放不受 2.1.0 影响
    legacy_spec = shot.model_copy(update={"story_beat": "The man enters.", "character_tokens": {},
                                          "entry_state": "Open door.", "exit_state": "Man stops.",
                                          "camera": {"composition": "Medium shot.", "movement": "Still."},
                                          "timeline": []})
    v200 = compiler.compile(
        spec=legacy_spec, style=STYLE, manifest=manifest,
        mode_hint=GenerationMode.REF2VA, compiler_version=LEGACY_THIN_SHELL_COMPILER_VERSION,
    )
    assert v200.compiler_version == LEGACY_THIN_SHELL_COMPILER_VERSION
    assert "keep that exact reference appearance throughout." in v200.sections["subject_definitions"]
    assert "<Picture 2> is the composition reference for [Shot 1]." in v200.sections["subject_definitions"]
    assert "<Picture 3> is the composition reference for [Shot 1]." in v200.sections["subject_definitions"]


# ---------------------------------------------------------------------------
# P1 + P2 集成：大纲 introduction 透传 + 核心道具按镜精准推导与唯一采纳回绑
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_outline_introduction_and_per_shot_prop_adoption_end_to_end(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "星雨道具回绑验收")
    style = freeze_style(client, project_id)
    document = _document(factory, project_id)

    s1 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-shot_01", duration=6,
                     characters=["林晚"])
    s2 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-shot_02", duration=6,
                     characters=["林晚", "陈默"])
    outline = _outline(factory, project_id, document)

    # 给大纲写入带 introduction 的角色，以及写入 ScriptPreparation / DramaSceneScript / SceneShotBoard
    with factory.begin() as db:
        outline_art = db.get(AgentArtifact, outline["id"])
        section_id = outline_art.payload_json["production_sections"][0]["production_section_id"]
        outline_art.payload_json = {
            **outline_art.payload_json,
            "characters": [
                {"name": "林晚", "entity_id": "林晚", "introduction": "穿深青色粗呢大衣的女子，长发低马尾。"},
                {"name": "陈默", "entity_id": "陈默", "introduction": "穿深灰色雨衣的男子，短发。"},
            ],
        }
        outline_art.sha256 = digest(outline_art.payload_json)
        task = AgentTaskRecord(
            project_id=project_id, role_id="scriptwriter", input_hash=digest("prep"),
            asset_manifest_hash=digest("prep"), rulepack_id="test", rulepack_hash=digest("test"),
            model_profile_id="test", model_id="test", schema_version="1.0", status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        prep_payload = {
            "schema_version": "1.0",
            "scene_key": "S1",
            "summary": "雨夜老茶馆",
            "locked_facts": [],
            "requirements": [
                {"family": "prop", "name": "铜怀表", "description": "停走十年的铜怀表",
                 "carrier": "object", "core_prop_reason": "核心线索"},
                {"family": "prop", "name": "半张旧照片", "description": "边缘撕裂的半张旧照片",
                 "carrier": "paper", "core_prop_reason": "核心线索"},
            ],
            "questions": [],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="ScriptPreparation", schema_version="1.0",
            payload_json=prep_payload, sha256=digest(prep_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[],
        ))
        drama_payload = {
            "schema_version": "1.0",
            "location": "雨夜老茶馆",
            "beats": [
                {"beat_type": "action", "visible_action": "林晚独自擦拭铜怀表。",
                 "characters": ["林晚"], "spoken_lines": []},
                {"beat_type": "action", "visible_action": "陈默拿出半张旧照片放到林晚面前。",
                 "characters": ["林晚", "陈默"], "spoken_lines": []},
            ],
        }
        drama_art = AgentArtifact(
            project_id=project_id, kind="DramaSceneScript", schema_version="1.0",
            payload_json=drama_payload, sha256=digest(drama_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[],
        )
        db.add(drama_art)
        db.flush()
        board_payload = {
            "schema_version": "1.0",
            "production_section_id": section_id,
            "drama_scene_artifact_id": drama_art.id,
            "shots": [
                {"id": "shot_01", "beat_ids": ["B1"]},
                {"id": "shot_02", "beat_ids": ["B2"]},
            ],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="SceneShotBoard", schema_version="1.0",
            payload_json=board_payload, sha256=digest(board_payload), producer_role="director",
            source_task_id=task.id, input_revision_ids=[],
        ))

    shot_ids = [s1["id"], s2["id"]]
    _apply(factory, project_id, outline, shot_ids)
    _plan(factory, project_id, shot_ids)

    # 上传并锁定角色和道具主素材
    def _master(role: str, entity: str, tag: str) -> dict:
        asset = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n" + tag.encode(),
                             filename=f"{tag}.png", role=role, entity_id=entity)
        _approve_lock_keeping_operator(client, asset["id"])
        return asset

    lw = _master("character", "林晚", "lw")
    cm = _master("character", "陈默", "cm")
    watch = _master("prop", "铜怀表", "watch")
    photo = _master("prop", "半张旧照片", "photo")

    derived = client.post(f"/api/v1/projects/{project_id}/director/materials/derive", json={}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    reqs = {(row["family"], row["entity_id"]): row for row in derived.json()["requirements"]}

    # 1) 验证大纲 introduction 完整透传，不再丢失
    assert "深青色粗呢大衣" in reqs[("character", "林晚")]["description"]
    assert "深灰色雨衣" in reqs[("character", "陈默")]["description"]

    # 2) 验证道具按镜精准推导：铜怀表只在 shot_01，半张旧照片只在 shot_02
    assert reqs[("prop", "铜怀表")]["used_shot_ids"] == [s1["id"]]
    assert reqs[("prop", "半张旧照片")]["used_shot_ids"] == [s2["id"]]

    # 3) 确认采用后，唯一执行者 bind_adopted_masters_to_shots 自动将道具按镜回绑，且 manifest 携带 asset_role="prop"
    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    confirm = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": progress["plan_revision_id"],
            "expected_snapshot": progress["expected_snapshot"],
            "decisions": [
                {"requirement_id": reqs[("character", "林晚")]["requirement_id"],
                 "strategy": "reuse_master", "entity_id": "林晚", "asset_id": lw["id"]},
                {"requirement_id": reqs[("character", "陈默")]["requirement_id"],
                 "strategy": "reuse_master", "entity_id": "陈默", "asset_id": cm["id"]},
                {"requirement_id": reqs[("prop", "铜怀表")]["requirement_id"],
                 "strategy": "reuse_master", "entity_id": "铜怀表", "asset_id": watch["id"]},
                {"requirement_id": reqs[("prop", "半张旧照片")]["requirement_id"],
                 "strategy": "reuse_master", "entity_id": "半张旧照片", "asset_id": photo["id"]},
            ],
            "visual_direction": {"era": "民国雨夜茶馆"},
        },
        headers=LOCAL,
    )
    assert confirm.status_code == 200, confirm.text

    from sqlalchemy import select

    from shine.services import manifest_from_shot_bindings

    with factory() as db:
        s1_links = list(db.scalars(select(ShotAssetBinding).where(ShotAssetBinding.shot_id == s1["id"])).all())
        s2_links = list(db.scalars(select(ShotAssetBinding).where(ShotAssetBinding.shot_id == s2["id"])).all())
        assert {link.asset_id for link in s1_links} == {lw["id"], watch["id"]}
        assert {link.asset_id for link in s2_links} == {lw["id"], cm["id"], photo["id"]}

        m1 = manifest_from_shot_bindings(db, db.get(Shot, s1["id"]))
        prop_entry = next(item for item in m1 if item.asset_id == watch["id"])
        assert prop_entry.use_as == ReferenceUse.PICTURE
        assert prop_entry.asset_role == "prop"
        assert prop_entry.label == "<Picture 2>"


def test_solo_closeup_in_two_person_beat_narrows_cast_and_blocks_off_placement_token():
    """两人同屋对白节拍中，单人特写只放 1 人到 blocking.placements：
    verdict.cast 仅收该 1 人（不带未出镜角色），且正文偷写另一人的 {{C2}} 立即被拒。"""
    scene = {
        "location": "周记钟表铺",
        "beats": [
            {
                "beat_type": "dialogue_exchange",
                "visible_action": "老周擦镜片回应，陈默站在柜台外。",
                "characters": ["老周", "陈默"],
                "spoken_lines": [{"speaker": "老周", "text": "不是修不了。"}],
            },
            {
                "beat_type": "action",
                "visible_action": "陈默低头看表链，老周在柜台后看着。",
                "characters": ["老周", "陈默"],
                "spoken_lines": [],
            },
        ],
    }
    ctx = {
        "max_shot_seconds": 10.0,
        "continuity_predecessor": False,
        "character_tokens": {"{{C1}}": "老周", "{{C2}}": "陈默"},
    }
    clean_board = {
        "shots": [
            {
                "id": "s1",
                "beat_ids": ["B1"],
                "duration_s": 5.0,
                "visual_description": "{{C1}} looks off-frame across the counter.",
                "asset_ids": [],
                "location": "周记钟表铺",
                "dialogue": [{"speaker_id": "老周", "text": "不是修不了。", "start_s": 0.4, "end_s": 2.4}],
                "camera": {
                    "composition": "Medium close on {{C1}} at frame right.",
                    "move": "static",
                    "motive": "",
                    "movement": "The camera holds still.",
                    "lighting": "Warm lamp.",
                },
                "ambience": "Clock ticking.",
                "music": "none",
                "blocking": {
                    "axis": "counter",
                    "placements": [
                        {"character": "老周", "zone": "right_mg", "facing": "three_quarter_left",
                         "posture": "seated", "props": ["silver pocket watch"]},
                    ],
                },
                "environment": {"depth": "Counter", "light": "Warm", "micro_motion": "Pendulums."},
                "timeline": [{"start_s": 0.0, "end_s": 5.0, "action": "{{C1}} pauses.",
                              "expression": "Calm.", "camera": "Static."}],
                "entry_state": "{{C1}} at counter.",
                "exit_state": "{{C1}} looks off-frame.",
                "transition_motivation": "establishing_reset",
                "handoff_cue": "",
                "internal_cuts": [],
            },
            {
                "id": "s2",
                "beat_ids": ["B2"],
                "duration_s": 5.0,
                "visual_description": "{{C2}} bows his head over the broken chain.",
                "asset_ids": [],
                "location": "周记钟表铺",
                "dialogue": [],
                "camera": {
                    "composition": "Close on {{C2}} at frame left.",
                    "move": "static",
                    "motive": "",
                    "movement": "The camera holds still.",
                    "lighting": "Warm lamp.",
                },
                "ambience": "Rain outside.",
                "music": "none",
                "blocking": {
                    "axis": "counter",
                    "placements": [
                        {"character": "陈默", "zone": "left_mg", "facing": "three_quarter_right",
                         "posture": "standing", "props": []},
                    ],
                },
                "environment": {"depth": "Counter", "light": "Warm", "micro_motion": "Rain."},
                "timeline": [{"start_s": 0.0, "end_s": 5.0, "action": "{{C2}} looks down.",
                              "expression": "Tense.", "camera": "Static."}],
                "entry_state": "{{C2}} bowed.",
                "exit_state": "{{C2}} still.",
                "transition_motivation": "eyeline_match",
                "handoff_cue": "",
                "internal_cuts": [],
            },
        ],
    }
    verdict = engine.review_scene_board(clean_board, scene, ctx)
    assert verdict.accepted, verdict.refusals
    assert verdict.cast == {"s1": ["老周"], "s2": ["陈默"]}

    leaked_board = {
        **clean_board,
        "shots": [
            {**clean_board["shots"][0], "visual_description": "{{C1}} lifts his eyes to {{C2}} across the counter."},
            clean_board["shots"][1],
        ],
    }
    refused = engine.review_scene_board(leaked_board, scene, ctx)
    assert "PROSE_CHARACTER_NOT_IN_CAST" in [r["code"] for r in refused.refusals]


@pytest.mark.integration
def test_derive_skips_out_of_scope_and_unmatched_props_and_prefers_arch_scene(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "钟表铺分场道具隔离验收")
    style = freeze_style(client, project_id)

    raw_text = (
        "第1场 雨夜钟表铺\n"
        "老周坐在柜台后用镊子给黄铜怀表上油。\n"
        "陈默推门拿出银壳怀表。\n\n"
        "第2场 停走的十二点\n"
        "老周拿出泛黄的牛皮纸旧账本翻找。\n"
    )
    from shine.materialprep.source_spans import segment_document
    from shine.models import ProjectDocument

    doc_scenes = segment_document(raw_text)
    s1_spans = [sp.id for sp in doc_scenes[0].spans if "黄铜怀表" in sp.text or "银壳怀表" in sp.text]
    ledger_spans = [sp.id for sp in doc_scenes[0].spans if "牛皮纸旧账本" in sp.text]
    split_pos = raw_text.index("第2场")

    with factory.begin() as db:
        doc = ProjectDocument(
            project_id=project_id, filename="watch.txt", content_type="text/plain",
            sha256=digest(raw_text), source_format="txt", text=raw_text, parsed={},
        )
        db.add(doc)
        db.flush()
        doc_id = doc.id

    s1 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-s1", duration=5, characters=["老周"])
    s2 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-s2", duration=5, characters=["陈默"])

    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=project_id, role_id="scriptwriter", input_hash=digest("scope-prep"),
            asset_manifest_hash=digest("scope-prep"), rulepack_id="test", rulepack_hash=digest("test"),
            model_profile_id="test", model_id="test", schema_version="1.0", status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        outline_payload = {
            "document_id": doc_id,
            "characters": [
                {"name": "老周", "entity_id": "老周", "introduction": "花白头发、戴老花镜的钟表匠。"},
                {"name": "陈默", "entity_id": "陈默", "introduction": "穿湿透灰色风衣的年轻人。"},
            ],
            "production_sections": [
                {"production_section_id": "sec-1", "order": 1, "title": "第1场 雨夜钟表铺",
                 "start_offset": 0, "end_offset": split_pos},
                {"production_section_id": "sec-2", "order": 2, "title": "第2场 停走的十二点",
                 "start_offset": split_pos, "end_offset": len(raw_text)},
            ],
        }
        outline_art = AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=outline_payload, sha256=digest(outline_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[], approved_by="local-user", approved_at=utcnow(),
        )
        db.add(outline_art)
        arch_payload = {
            "schema_version": "1.0",
            "locations": [{"name": "周记钟表铺", "description": "老城区街尾木柜台钟表铺，墙上挂满旧钟。"}],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="SeriesArchitecture", schema_version="1.0",
            payload_json=arch_payload, sha256=digest(arch_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[],
        ))
        prep_payload = {
            "schema_version": "1.0",
            "scene_key": "S1",
            "summary": "雨夜钟表铺",
            "locked_facts": [],
            "requirements": [
                {"family": "scene", "name": "周记钟表铺",
                 "description": "雨夜钟表铺，全场唯一的戏剧空间", "source_span_ids": s1_spans},
                {"family": "prop", "name": "银壳兰草怀表",
                 "description": "刻半开兰草的银壳怀表", "carrier": "object",
                 "core_prop_reason": "核心怀表", "source_span_ids": s1_spans},
                {"family": "prop", "name": "牛皮纸旧账本",
                 "description": "第二场才翻出的旧账本", "carrier": "paper",
                 "core_prop_reason": "账本线索", "source_span_ids": ledger_spans},
            ],
            "questions": [],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="ScriptPreparation", schema_version="1.0",
            payload_json=prep_payload, sha256=digest(prep_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[],
        ))
        drama_payload = {
            "schema_version": "1.0",
            "location": "周记钟表铺",
            "beats": [
                {"beat_type": "action", "visible_action": "老周给黄铜怀表上油。",
                 "characters": ["老周"], "spoken_lines": []},
                {"beat_type": "action", "visible_action": "陈默摊开绒布露出银壳怀表。",
                 "characters": ["陈默"], "spoken_lines": []},
            ],
        }
        drama_art = AgentArtifact(
            project_id=project_id, kind="DramaSceneScript", schema_version="1.0",
            payload_json=drama_payload, sha256=digest(drama_payload), producer_role="scriptwriter",
            source_task_id=task.id, input_revision_ids=[],
        )
        db.add(drama_art)
        db.flush()
        board_payload = {
            "schema_version": "1.0",
            "production_section_id": "sec-1",
            "drama_scene_artifact_id": drama_art.id,
            "shots": [{"id": "s1", "beat_ids": ["B1"]}, {"id": "s2", "beat_ids": ["B2"]}],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="SceneShotBoard", schema_version="1.0",
            payload_json=board_payload, sha256=digest(board_payload), producer_role="director",
            source_task_id=task.id, input_revision_ids=[],
        ))
        db.flush()
        outline_dict = {"id": outline_art.id, "sha256": outline_art.sha256}

    _apply(factory, project_id, outline_dict, [s1["id"], s2["id"]])
    _plan(factory, project_id, [s1["id"], s2["id"]])

    derived = client.post(f"/api/v1/projects/{project_id}/director/materials/derive", json={}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    reqs = {(row["family"], row["entity_id"]): row for row in derived.json()["requirements"]}

    assert ("prop", "牛皮纸旧账本") not in reqs
    assert reqs[("prop", "银壳兰草怀表")]["used_shot_ids"] == [s2["id"]]
    assert reqs[("scene", "周记钟表铺")]["description"] == "老城区街尾木柜台钟表铺，墙上挂满旧钟。"


@pytest.mark.integration
def test_prop_matching_uses_english_fact_aliases_and_skips_unmentioned_front_matter_props(studio):
    """A prop listed in merged front-matter spans must not blanket-bind onto a scene
    whose shots never mention it, and a shot's English blocking.props ('brass lantern')
    matches its Chinese prop requirement via locked_story_facts/prompt_story_facts."""
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "前导道具与英文匹配验收")
    style = freeze_style(client, project_id)
    script = (
        "【关键道具】\n1. 黄铜提灯\n\n"
        "第1场 雨夜庭院\n老周在雨里独自站立。\n\n"
        "第2场 废墟圣殿\n陈默走近石台。\n"
    )
    sec1_end = script.index("第2场")
    s1 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-sec-1-s1", duration=5, characters=["老周"])
    s2 = create_shot(client, project_id, style["id"], title="导演分镜-ep-01-sec-2-s2", duration=5, characters=["陈默"])
    with factory.begin() as db:
        from shine.materialprep.source_spans import segment_document
        from shine.models import AppSetting, ProjectDocument, Shot
        doc = ProjectDocument(
            project_id=project_id,
            filename="script.txt",
            content_type="text/plain",
            source_format="txt",
            text=script,
            sha256=digest(script),
            parsed={},
        )
        db.add(doc)
        db.flush()
        scenes = segment_document(script)
        all_spans = [sp.id for sc in scenes for sp in sc.spans]
        task = AgentTaskRecord(
            project_id=project_id, role_id="scriptwriter", input_hash="h",
            asset_manifest_hash="a", rulepack_id="r", rulepack_hash="rh",
            model_profile_id="p", model_id="m", schema_version="1.0",
            status="SUCCEEDED", trace={},
        )
        db.add(task)
        db.flush()
        sec1_id = "sec-1"
        sec2_id = "sec-2"
        outline_payload = {
            "schema_version": "1.0", "document_id": doc.id, "document_sha256": doc.sha256,
            "summary": "s", "story_line": ["l"], "timeline": ["t"], "relationships": [],
            "questions": [], "characters": [{"name": "老周"}, {"name": "陈默"}],
            "production_sections": [
                {"production_section_id": sec1_id, "order": 1, "title": "第1场",
                 "start_offset": 0, "end_offset": sec1_end, "estimated_duration_s": 10.0},
                {"production_section_id": sec2_id, "order": 2, "title": "第2场",
                 "start_offset": sec1_end, "end_offset": len(script), "estimated_duration_s": 10.0},
            ],
            "locked_facts": [],
        }
        outline_art = AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=outline_payload, sha256=digest(outline_payload),
            producer_role="scriptwriter", source_task_id=task.id, input_revision_ids=[],
            approved_by="local-user", approved_at=utcnow(),
        )
        db.add(outline_art)
        prep_payload = {
            "schema_version": "1.0", "scene_key": "S1", "summary": "s", "locked_facts": [],
            "requirements": [
                {"family": "prop", "name": "黄铜提灯", "description": "六角提灯",
                 "carrier": "object", "core_prop_reason": "照明",
                 "source_span_ids": all_spans},
            ],
            "questions": [],
        }
        db.add(AgentArtifact(
            project_id=project_id, kind="ScriptPreparation", schema_version="1.0",
            payload_json=prep_payload, sha256=digest(prep_payload),
            producer_role="scriptwriter", source_task_id=task.id, input_revision_ids=[],
        ))
        for idx, (sec_id, b_text, s_obj, shot_key, props) in enumerate([
            (sec1_id, "老周独自站在雨里。", s1, "s1", []),
            (sec2_id, "陈默走近石台。", s2, "s2", ["brass lantern"]),
        ], 1):
            drama = AgentArtifact(
                project_id=project_id, kind="DramaSceneScript", schema_version="1.0",
                payload_json={"schema_version": "1.0", "location": "庭院" if idx == 1 else "圣殿",
                              "beats": [{"beat_type": "action", "visible_action": b_text,
                                         "characters": ["老周" if idx == 1 else "陈默"], "spoken_lines": []}]},
                sha256=digest({"idx": idx, "b": b_text}), producer_role="scriptwriter",
                source_task_id=task.id, input_revision_ids=[],
            )
            db.add(drama)
            db.flush()
            board = AgentArtifact(
                project_id=project_id, kind="SceneShotBoard", schema_version="1.0",
                payload_json={"schema_version": "1.0", "production_section_id": sec_id,
                              "drama_scene_artifact_id": drama.id,
                              "shots": [{"id": shot_key, "beat_ids": ["B1"]}]},
                sha256=digest({"idx": idx, "k": shot_key}), producer_role="director",
                source_task_id=task.id, input_revision_ids=[],
            )
            db.add(board)
            db.flush()
            shot_row = db.get(Shot, s_obj["id"])
            shot_row.name = f"导演分镜-ep-01-{sec_id}-{shot_key}"
            shot_row.spec = {
                **shot_row.spec,
                "locked_story_facts": ["道具：符文长剑（魔纹剑）、黄铜提灯（六角提灯）"],
                "prompt_story_facts": ["Props: rune sword (runed blade), brass lantern (hexagonal lantern)."],
                "blocking": {"axis": "g1", "placements": [
                    {"character": "老周" if idx == 1 else "陈默", "zone": "center_mg",
                     "facing": "facing_camera", "posture": "standing", "props": props}
                ]},
            }
            apply_p = {"shot_ids": [s_obj["id"]]}
            db.add(AppSetting(key=f"film-apply:{board.id}", value=apply_p))
            db.add(AgentTaskRecord(
                project_id=project_id, role_id="director_storyboard_run",
                input_hash=f"run-{idx}", asset_manifest_hash="a", rulepack_id="r",
                rulepack_hash="rh", model_profile_id="p", model_id="m",
                schema_version="1.0", status="SUCCEEDED",
                trace={"units": [{"episode_id": "ep-01", "production_section_id": sec_id,
                                  "start_offset": 0 if idx == 1 else sec1_end}],
                       "results": [{"unit": {"start_offset": 0 if idx == 1 else sec1_end},
                                    "apply": {"shot_ids": [s_obj["id"]]}}]},
            ))
        outline_dict = {"id": outline_art.id, "sha256": outline_art.sha256}
    from shine.director.models import DirectorEpisodePlan
    with factory.begin() as db:
        plan_p = {
            "schema_version": "1.0", "mode": "sections", "target_seconds": 60, "tolerance_seconds": 15,
            "episodes": [{"episode_id": "ep-01", "order": 1, "title": "第1集",
                          "shot_ids": [s1["id"], s2["id"]],
                          "section_ids": [sec1_id, sec2_id], "planned_seconds": 12.0}],
            "allowed_repeats": [], "out_of_scope_section_ids": [],
            "source": {"outline_artifact_id": outline_dict["id"], "outline_sha256": outline_dict["sha256"]},
        }
        db.add(DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED", target_seconds=60,
            tolerance_seconds=15, payload=plan_p, sha256=digest(plan_p),
            source="script_sections_materialized", created_by="local-user",
        ))
    derived = client.post(f"/api/v1/projects/{project_id}/director/materials/derive", json={}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    reqs = {(row["family"], row["entity_id"]): row for row in derived.json()["requirements"]}
    assert reqs[("prop", "黄铜提灯")]["used_shot_ids"] == [s2["id"]]

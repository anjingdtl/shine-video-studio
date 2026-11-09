"""Authoritative directory of text Agent routes exposed to the workbench.

The API enum remains the route contract.  This module adds the product-facing
grouping and descriptions without turning VLM/ASR reviewer routes into text
agents or creating a second execution registry.
"""
from __future__ import annotations

from dataclasses import dataclass

from shine.schemas import AgentRole


@dataclass(frozen=True)
class TextRoleDefinition:
    role: AgentRole
    label_zh: str
    group: str
    business_entry: str
    required: bool
    capability_type: str
    dependency_stages: tuple[str, ...]
    description: str


TEXT_ROLE_CATALOG: tuple[TextRoleDefinition, ...] = (
    TextRoleDefinition(
        AgentRole.SCRIPTWRITER, "故事大纲", "长文生产", "剧本与故事准备", True,
        "structured_text", ("script", "story"), "整理故事大纲、人物和剧本结构。",
    ),
    TextRoleDefinition(
        AgentRole.FOUR_STAGE_BIBLE, "全剧蓝图", "长文生产", "四阶段创作 · 全剧蓝图", True,
        "structured_text", ("long_form",), "根据分卷大纲整理全书故事蓝图。",
    ),
    TextRoleDefinition(
        AgentRole.FOUR_STAGE_SCENE_SCRIPT, "分场剧本", "长文生产", "四阶段创作 · 分场剧本", True,
        "structured_text", ("long_form",), "按已确认蓝图编写单场剧本。",
    ),
    TextRoleDefinition(
        AgentRole.SCRIPT_PLANNER, "剧本规划", "剧本与镜头规划", "剧本分析与规划", True,
        "structured_text", ("script",), "分析剧本并形成可供后续制作使用的规划。",
    ),
    TextRoleDefinition(
        AgentRole.SHOT_PLANNER, "镜头规划", "剧本与镜头规划", "分镜规划", True,
        "structured_text", ("storyboard",), "按剧本、风格和镜头约束整理镜头规划。",
    ),
    TextRoleDefinition(
        AgentRole.DIRECTOR, "导演", "影视制作团队", "导演分镜链", True,
        "structured_text", ("storyboard",), "提出场面调度、人物关系和镜头意图。",
    ),
    TextRoleDefinition(
        AgentRole.CINEMATOGRAPHER, "摄影指导", "影视制作团队", "导演分镜链", True,
        "structured_text", ("storyboard",), "将导演意图转为摄影与画面方案。",
    ),
    TextRoleDefinition(
        AgentRole.CONTINUITY_SUPERVISOR, "场记与连续性", "影视制作团队", "导演分镜链", True,
        "structured_text", ("storyboard",), "检查人物、道具、空间和镜头连续性。",
    ),
    TextRoleDefinition(
        AgentRole.SOUND_DIRECTOR, "声音导演", "影视制作团队", "导演分镜链", True,
        "structured_text", ("storyboard",), "整理对白、声音场景与声音执行建议。",
    ),
    TextRoleDefinition(
        AgentRole.EDITOR, "剪辑", "影视制作团队", "导演分镜链与后期", True,
        "structured_text", ("storyboard", "post"), "提出剪辑顺序、节奏和时间范围。",
    ),
    TextRoleDefinition(
        AgentRole.PRODUCER, "制片统筹", "影视制作团队", "生产规划", True,
        "structured_text", ("production",), "为任务编排与制片规划提供结构化建议。",
    ),
    TextRoleDefinition(
        AgentRole.COMFYOPS, "制作环境助理", "工程与审查", "本机制作环境", True,
        "structured_text", ("setup",), "解释本机制作配置与明确的环境问题。",
    ),
    TextRoleDefinition(
        AgentRole.REPAIR_PLANNER, "修复规划", "工程与审查", "质量问题处理", True,
        "structured_text", ("repair",), "为失败任务提出有界、可审计的修复建议。",
    ),
    TextRoleDefinition(
        AgentRole.QC_REVIEWER, "文本质量审核", "工程与审查", "结构化文本审核", True,
        "structured_text", ("quality",), "检查结构化文本是否满足对应业务合同。",
    ),
)

TEXT_ROLES: tuple[str, ...] = tuple(item.role.value for item in TEXT_ROLE_CATALOG)
TEXT_ROLE_BY_ID: dict[str, TextRoleDefinition] = {
    item.role.value: item for item in TEXT_ROLE_CATALOG
}

MULTIMODAL_ROLE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "role": AgentRole.VLM_REVIEWER.value,
        "label_zh": "画面审片",
        "capability_type": "local_or_advisory_vlm",
        "description": "独立画面审片能力；不由文本模型路由或文本就绪状态代替。",
    },
    {
        "role": AgentRole.ASR_REVIEWER.value,
        "label_zh": "语音核对",
        "capability_type": "local_or_advisory_asr",
        "description": "独立语音核对能力；不由文本模型路由或文本就绪状态代替。",
    },
)

#: Parent task roles that execute the *output* of one text role.
#:
#: A route change has to see the work that will actually call the role, not only
#: rows whose ``role_id`` equals the role name.  Tasks here carry a durable
#: ``model_profile_id`` snapshot, so a queued one continues on its original
#: model; a new one uses the new route (J05).
TASK_ROLE_CONSUMERS: dict[str, str] = {
    "story_outline_run": AgentRole.SCRIPTWRITER.value,
    "story_section_run": AgentRole.SCRIPTWRITER.value,
    "creative_script_run": AgentRole.SCRIPTWRITER.value,
    "four_stage_bible_run": AgentRole.FOUR_STAGE_BIBLE.value,
    "four_stage_scene_script_run": AgentRole.FOUR_STAGE_SCENE_SCRIPT.value,
}


def task_roles_for(changed_roles: set[str]) -> list[str]:
    """Every task role that can call one of the changed roles."""
    parents = {parent for parent, consumed in TASK_ROLE_CONSUMERS.items()
               if consumed in changed_roles}
    return sorted({*changed_roles, *parents})


WORKFLOW_ROLE_IDS: dict[str, tuple[str, ...]] = {
    "script_and_storyboard": (
        AgentRole.SCRIPTWRITER.value,
        AgentRole.SCRIPT_PLANNER.value,
        AgentRole.SHOT_PLANNER.value,
    ),
    "four_stage": (
        AgentRole.SCRIPTWRITER.value,
        AgentRole.FOUR_STAGE_BIBLE.value,
        AgentRole.FOUR_STAGE_SCENE_SCRIPT.value,
    ),
    "film_crew": tuple(
        role.value for role in (
            AgentRole.DIRECTOR,
            AgentRole.CINEMATOGRAPHER,
            AgentRole.CONTINUITY_SUPERVISOR,
            AgentRole.SOUND_DIRECTOR,
            AgentRole.EDITOR,
            AgentRole.PRODUCER,
        )
    ),
    "real_production": TEXT_ROLES,
    "all_text": TEXT_ROLES,
}


def role_payload(definition: TextRoleDefinition) -> dict[str, object]:
    return {
        "role": definition.role.value,
        "label_zh": definition.label_zh,
        "group": definition.group,
        "business_entry": definition.business_entry,
        "required": definition.required,
        "capability_type": definition.capability_type,
        "dependency_stages": list(definition.dependency_stages),
        "description": definition.description,
    }


def workflow_roles(workflow: str | None) -> tuple[str, ...]:
    if workflow is None:
        return TEXT_ROLES
    try:
        return WORKFLOW_ROLE_IDS[workflow]
    except KeyError as exc:
        raise ValueError(f"Unsupported LLM readiness workflow: {workflow}") from exc

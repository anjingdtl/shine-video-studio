# ruff: noqa: E501 - curated data tables keep one row per real operation
"""Canonical capability registry for the Shine copilot.

The registry is the *only* list of things the copilot may ask the product to do.
It is not a generic HTTP client: every entry names one existing executor, one
parameter schema, one business gate and one read-back authority.  Anything the
UI can do but the registry cannot is recorded as READ_ONLY, HUMAN_REQUIRED or
NOT_SUPPORTED together with the reason - a missing mapping is never disguised
as coverage.

The second half of this module is the audited UI operation matrix.  It is the
acceptance artefact: one row per real user operation, mapped to the real route,
the real executor and the honest AI status.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from shine.support.actions import _ALLOWED_PARAMS
from shine.support.tools import ACTION_SPECS, READ_TOOLS

CATALOG_VERSION = "shine-copilot-capabilities-2026.11.16-v2"

#: Permission tiers, enforced server-side by ``support.actions``.
TIERS = {
    "READ": "只读，无需确认",
    "PREVIEW": "先预览差异，再由用户明确确认",
    "EXECUTE_GUARDED": "真实资源消耗，显示影响后由用户明确确认",
    "HUMAN_REQUIRED": "必须由用户在原页面亲自完成",
    "FORBIDDEN": "运行时管家永久不具备",
}

#: AI status of a real user operation.
STATUSES = {
    "COVERED": "可由自然语言委托",
    "READ_ONLY": "可查询，不可修改",
    "GUARDED": "需预览并明确授权后委托",
    "HUMAN_REQUIRED": "必须人工确认（AI 只解释）",
    "NOT_SUPPORTED": "当前未接入，附工程原因",
}


@dataclass(frozen=True)
class Capability:
    action_id: str
    label: str
    module: str
    page: str
    tier: str
    executor: str | None
    params: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    readback: str | None = None
    reversible: str = "NO"
    gpu: bool = False
    paid: bool = False
    scope: str = "PROJECT"
    status: str = "COVERED"
    examples: tuple[str, ...] = ()
    note: str = ""
    gates: tuple[str, ...] = field(default_factory=tuple)


_EXISTING_ACTIONS: tuple[Capability, ...] = tuple(
    Capability(
        action_id=spec.kind,
        label=spec.label,
        module=spec.executor.split(".")[1] if spec.executor else "support",
        page="基础配置",
        tier="PREVIEW",
        executor=spec.executor,
        params=tuple(sorted(_ALLOWED_PARAMS.get(spec.kind, ("project_id",)))),
        readback="/api/v1/setup/verdict",
        reversible="NO",
        status="GUARDED",
        note=spec.description,
    )
    for spec in ACTION_SPECS.values()
)

_EXPLICIT_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        action_id="managed_release",
        label="停止本工作台拥有的渲染服务并释放显存",
        module="comfyops",
        page="基础配置",
        tier="PREVIEW",
        executor="shine.comfyops.renderer_release.release_managed_processes_guarded",
        params=("only",),
        readback="/api/v1/setup/verdict",
        reversible="NO",
        scope="MACHINE",
        status="GUARDED",
        note="只释放有归属证明的服务；不传项目，也不会强停外部 ComfyUI。",
    ),
    Capability(
        action_id="project_create",
        label="创建影视项目",
        module="projects",
        page="导演模式",
        tier="PREVIEW",
        executor="shine.services.create_project",
        params=("title", "description", "language"),
        preconditions=("标题非空",),
        readback="GET /api/v1/projects/{project_id}",
        reversible="NO",
        scope="MACHINE",
        status="GUARDED",
        examples=("帮我建立一个短剧项目，名字叫《雾港回声》",),
        note="只创建空项目；不会导入原文、不会替用户选择风格。",
    ),
    Capability(
        action_id="document_import_text",
        label="导入原文文本",
        module="documents",
        page="导演模式",
        tier="PREVIEW",
        executor="shine.services.import_project_document",
        params=("project_id", "filename", "text"),
        preconditions=("项目存在", "文本非空且不超过 20 万字符"),
        readback="GET /api/v1/projects/{project_id}/script-basis",
        reversible="NO",
        scope="PROJECT",
        status="GUARDED",
        examples=("把这段小说正文导入当前项目",),
        note="与界面导入同样解析、去重并落库；更长的原文请在界面选择文件上传。",
    ),
    Capability(
        action_id="project_settings_update",
        label="修改项目语言等用户级设置",
        module="projects",
        page="设置",
        tier="PREVIEW",
        executor="shine.services.update_project_settings",
        params=("project_id", "language"),
        preconditions=("项目存在", "语言属于既有口播语言清单"),
        readback="GET /api/v1/projects/{project_id}",
        reversible="YES",
        scope="PROJECT",
        status="GUARDED",
        examples=("把项目配音语言改成粤语",),
        note="不修改已冻结风格、主资产或任何生产合同。",
    ),
    Capability(
        action_id="story_outline_generate",
        label="生成故事大纲",
        module="story",
        page="导演模式",
        tier="EXECUTE_GUARDED",
        executor="shine.materialprep.story.queue_outline",
        params=("project_id", "document_id", "locked_facts", "pacing"),
        preconditions=("项目已有底稿", "底稿非空", "故事大纲角色已配置"),
        readback="GET /api/v1/projects/{project_id}/story-preparation",
        reversible="NO",
        gpu=False,
        paid=True,
        scope="PROJECT",
        status="GUARDED",
        examples=("帮我把这本小说整理成故事大纲",),
        note="复用持久大纲任务；相同底稿与参数返回同一个任务，不会重复调用模型。"
        "大纲确认仍由用户亲自完成。",
        gates=("SCRIPT_DOCUMENT",),
    ),
    Capability(
        action_id="storyboard_generate",
        label="生成导演分镜",
        module="storyboard",
        page="导演模式",
        tier="EXECUTE_GUARDED",
        executor="shine.director.streamline.queue_director_storyboard",
        params=("project_id", "plan_version"),
        preconditions=("风格已冻结", "大纲已确认", "分集方案已确认"),
        readback="GET /api/v1/projects/{project_id}/director/storyboard/state",
        reversible="NO",
        gpu=False,
        paid=True,
        scope="PROJECT",
        status="GUARDED",
        examples=("把第三集的分镜全部拆出来",),
        note="复用一个持久分镜任务；逐集落镜头，失败按集隔离。",
        gates=("STYLE_FROZEN", "OUTLINE_CONFIRMED", "EPISODE_PLAN_CONFIRMED"),
    ),
    Capability(
        action_id="production_start",
        label="开始真实拍摄选中的集",
        module="production",
        page="导演模式",
        tier="EXECUTE_GUARDED",
        executor="shine.director.streamline.start_director_production",
        params=("project_id", "episode_ids", "workflow_profile_id", "strategy"),
        preconditions=("选中集可开拍", "导演包已就绪", "GPU 调度可用"),
        readback="GET /api/v1/production-runs/{run_id}",
        reversible="CONDITIONAL",
        gpu=True,
        paid=False,
        scope="PROJECT",
        status="GUARDED",
        examples=("检查当前 H3 配置，然后帮我开始生成第一集", "开始拍摄第 1、2 集"),
        note="唯一主生产动作；复用导演包与 Run 引擎，相同选择返回同一次运行。"
        "不替用户确认风格、主资产或审片。",
        gates=("STYLE_FROZEN", "EPISODE_PLAN_CONFIRMED", "PACKAGE_READY"),
    ),
    Capability(
        action_id="document_switch_basis",
        label="切换正式原文底稿",
        module="documents",
        page="导演模式",
        tier="PREVIEW",
        executor="shine.services.switch_script_basis",
        params=("project_id", "document_id"),
        preconditions=("项目存在", "文档已导入且属于该项目"),
        readback="GET /api/v1/projects/{project_id}/script-basis",
        reversible="YES",
        scope="PROJECT",
        status="GUARDED",
        examples=("把第二版原文切换成正式底稿",),
        note="只切换底稿指针；不改写原文、不删除其它文档，旧包仍可按 id 读取。",
    ),
    Capability(
        action_id="production_control",
        label="暂停/继续/取消生产任务",
        module="production",
        page="导演模式",
        tier="EXECUTE_GUARDED",
        executor="shine.director.run_engine",
        params=("run_id", "operation"),
        preconditions=("Run 存在", "操作属于 pause/resume/cancel"),
        readback="GET /api/v1/production-runs/{run_id}",
        reversible="CONDITIONAL",
        gpu=True,
        scope="RUN",
        status="GUARDED",
        examples=("暂停当前生产任务", "取消第二集的生产"),
        note="只操作一个已存在的 Run；复用原状态机与版本校验，不新建队列。",
    ),
    Capability(
        action_id="literary_scripts_generate",
        label="生成文学分集剧本",
        module="pipeline",
        page="导演模式",
        tier="EXECUTE_GUARDED",
        executor="shine.director.pipeline.queue_scene_scripts",
        params=("project_id", "volume_id"),
        preconditions=("分集方案已确认", "大纲与圣经已就绪", "分集剧本角色已配置"),
        readback="GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}",
        reversible="NO",
        paid=True,
        scope="PROJECT",
        status="GUARDED",
        examples=("把第一集的文学剧本生成出来",),
        note="复用原流水线任务；只排队还没有确认剧本的分节，不覆盖已确认稿。",
        gates=("VOLUME_PLAN_CONFIRMED", "OUTLINE_CONFIRMED", "BIBLE_CONFIRMED"),
    ),
)

# Explicit rows override the generic fallback (same action_id), so every action
# advertises its real parameter contract instead of a blanket project_id.
CAPABILITIES: tuple[Capability, ...] = tuple(
    {row.action_id: row for row in (_EXISTING_ACTIONS + _EXPLICIT_CAPABILITIES)}.values()
)


READ_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        action_id="read_project_journey",
        label="读取项目旅程与每集状态",
        module="overview",
        page="导演模式",
        tier="READ",
        executor="shine.director.overview.build_overview",
        params=("project_id",),
        readback="GET /api/v1/projects/{project_id}/journey",
        status="READ_ONLY",
        note="只读聚合；不写第二套状态。",
    ),
    Capability(
        action_id="read_missing_materials",
        label="检查缺失素材",
        module="materials",
        page="导演模式",
        tier="READ",
        executor="shine.director.overview.build_overview",
        params=("project_id",),
        readback="GET /api/v1/projects/{project_id}/director/overview",
        status="READ_ONLY",
        note="缺失清单来自既有备料台账；不能替用户决定复用或生成。",
    ),
    Capability(
        action_id="read_production_run",
        label="读取真实生产进度",
        module="production",
        page="导演模式",
        tier="READ",
        executor="shine.api_director.get_run",
        params=("run_id",),
        readback="GET /api/v1/production-runs/{run_id}",
        status="READ_ONLY",
    ),
    Capability(
        action_id="read_task_failure",
        label="读取失败任务与尝试",
        module="diagnosis",
        page="导演模式",
        tier="READ",
        executor="shine.support.context.build_evidence_snapshot",
        params=("job_id", "attempt_id", "task_id"),
        readback="jobs/job_attempts ledger",
        status="READ_ONLY",
    ),
    Capability(
        action_id="search_project_knowledge",
        label="检索版本化项目百科",
        module="knowledge",
        page="全局",
        tier="READ",
        executor="shine.support.project_knowledge.search",
        params=("q",),
        readback="backend/shine/support/knowledge_manifest.json",
        status="READ_ONLY",
    ),
    Capability(
        action_id="search_official_docs",
        label="检索 ComfyUI / H3 官方摘要",
        module="knowledge",
        page="全局",
        tier="READ",
        executor="shine.support.official_docs.search",
        params=("q",),
        readback="官方来源索引（参考证据）",
        status="READ_ONLY",
    ),
    Capability(
        action_id="read_parameter_contracts",
        label="读取可调参数合同",
        module="parameters",
        page="设置",
        tier="READ",
        executor="shine.support.parameters.contracts",
        params=("project_id",),
        readback="workflow_profiles / GenerationStrategy",
        status="READ_ONLY",
    ),
)

#: Operations that exist in the UI but the copilot must not perform.
_HUMAN: tuple[tuple[str, str, str, str], ...] = (
    ("确认故事大纲", "POST /api/v1/projects/{project_id}/director/outline/confirm", "用户对创作方向的确认，AI 只能准备与解释", "HUMAN_REQUIRED"),
    ("确认并冻结导演风格", "POST /api/v1/projects/{project_id}/style/freeze", "冻结是不可逆的艺术裁决", "HUMAN_REQUIRED"),
    ("确认分集镜头", "POST /api/v1/projects/{project_id}/director/storyboard/episodes/{episode_id}/confirm", "镜头取舍属于导演判断", "HUMAN_REQUIRED"),
    ("确认主资产/候选图", "POST /api/v1/candidate-assets/{identity}/promote", "主资产必须由用户确认", "HUMAN_REQUIRED"),
    ("备料决策（复用/生成/跳过）", "POST /api/v1/projects/{project_id}/director/materials/commit", "涉及素材版权与创作选择", "HUMAN_REQUIRED"),
    ("审片与成片验收", "GET/POST /api/v1/projects/{project_id}/deliveries", "验收是人的裁决，AI 不宣称通过", "HUMAN_REQUIRED"),
    ("删除历史成片", "POST /api/v1/projects/{project_id}/video-library/delete", "破坏性操作，必须在界面逐次确认", "HUMAN_REQUIRED"),
    ("安装计划授权与执行", "POST /api/v1/comfyops/install-plans/{identity}/execute", "下载/写入用户安装目录需逐次授权", "HUMAN_REQUIRED"),
    ("修改节点源码/内置模板", "worktree files", "运行时管家永久禁止", "FORBIDDEN"),
    ("任意 Shell / Git / pip", "—", "运行时管家没有这些工具", "FORBIDDEN"),
)

_NOT_SUPPORTED: tuple[tuple[str, str, str], ...] = (
    ("单镜头逐字改写与拆分合并", "POST /api/v1/projects/{project_id}/director/storyboard/shots/{shot_id}",
     "API 存在，但没有稳定的自然语言参数合同与回读校验；V2 不代理，请在导演页编辑。"),
    ("素材候选图批量生成与 QC", "POST /api/v1/projects/{project_id}/candidate-assets/generate",
     "涉及付费/GPU 与人工选片；当前只做缺失清单与结果查询，不代提交。"),
    ("字幕逐句编辑与导出审批", "PUT /api/v1/projects/{project_id}/director/storyboard/shots/{shot_id}/subtitles",
     "文本级编辑缺少幂等键与差异回读合同；保留人工。"),
    ("工作流节点图数值级改写", "—",
     "工作流合同没有节点级参数 schema；只能创建参数映射副本，不能改图。"),
    ("外部发布/分享", "—", "当前产品没有对外发布 API；不伪造能力。"),
    ("任意内部 HTTP 调用", "—", "运行时管家没有通用 HTTP 工具；能力必须逐项注册。"),
)

#: The audited UI operation matrix (one row per real operation).
MATRIX: tuple[dict[str, str], ...] = (
    {"area": "全局", "operation": "新建项目", "api": "POST /api/v1/projects", "executor": "shine.services.create_project", "ai_status": "GUARDED", "ai_action": "project_create"},
    {"area": "全局", "operation": "切换/查看项目", "api": "GET /api/v1/projects, GET /api/v1/projects/{id}", "executor": "projects ledger", "ai_status": "READ_ONLY", "ai_action": "read_project_journey"},
    {"area": "全局", "operation": "导入小说/剧本", "api": "POST /api/v1/projects/{id}/documents", "executor": "shine.services.import_project_document", "ai_status": "GUARDED", "ai_action": "document_import_text"},
    {"area": "全局", "operation": "切换正式底稿", "api": "POST /api/v1/projects/{id}/script-basis", "executor": "shine.services.switch_script_basis", "ai_status": "GUARDED", "ai_action": "document_switch_basis"},
    {"area": "全局", "operation": "查看项目设置", "api": "GET /api/v1/projects/{id}", "executor": "projects ledger", "ai_status": "READ_ONLY", "ai_action": "read_project_journey"},
    {"area": "全局", "operation": "修改项目语言", "api": "PATCH /api/v1/projects/{id}/settings", "executor": "shine.services.update_project_settings", "ai_status": "GUARDED", "ai_action": "project_settings_update"},
    {"area": "导演模式", "operation": "七阶段旅程", "api": "GET /api/v1/projects/{id}/journey", "executor": "director.overview.build_overview", "ai_status": "READ_ONLY", "ai_action": "read_project_journey"},
    {"area": "导演模式", "operation": "生成故事大纲", "api": "POST /api/v1/projects/{id}/story-preparation/outlines", "executor": "materialprep.story.queue_outline", "ai_status": "GUARDED", "ai_action": "story_outline_generate"},
    {"area": "导演模式", "operation": "修订/确认故事大纲", "api": "POST /api/v1/projects/{id}/director/outline/revision, /confirm", "executor": "api_streamline", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "导演模式", "operation": "生成分集剧本", "api": "POST /api/v1/projects/{id}/pipeline/volumes/{volume_id}/scripts", "executor": "shine.director.pipeline.queue_scene_scripts", "ai_status": "GUARDED", "ai_action": "literary_scripts_generate"},
    {"area": "导演模式", "operation": "生成/确认分镜书", "api": "POST /api/v1/projects/{id}/director/storyboard-book[/confirm]", "executor": "api_streamline", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "导演模式", "operation": "生成导演分镜", "api": "POST /api/v1/projects/{id}/director/storyboard/generate", "executor": "director.streamline.queue_director_storyboard", "ai_status": "GUARDED", "ai_action": "storyboard_generate"},
    {"area": "导演模式", "operation": "查看逐集镜头", "api": "GET /api/v1/projects/{id}/director/storyboard/episodes/{episode_id}/shots", "executor": "director.streamline.episode_shot_table", "ai_status": "READ_ONLY", "ai_action": "read_project_journey"},
    {"area": "导演模式", "operation": "逐镜头行编辑", "api": "PATCH /api/v1/projects/{id}/director/storyboard/shots/{shot_id}", "executor": "reshape_shot", "ai_status": "NOT_SUPPORTED", "ai_action": "—"},
    {"area": "导演模式", "operation": "确认单集镜头", "api": "POST /api/v1/projects/{id}/director/storyboard/episodes/{episode_id}/confirm", "executor": "director.streamline.confirm_episode", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "导演模式", "operation": "查看缺失素材", "api": "GET /api/v1/projects/{id}/director/overview", "executor": "director.overview.build_overview", "ai_status": "READ_ONLY", "ai_action": "read_missing_materials"},
    {"area": "导演模式", "operation": "备料决策与提交", "api": "POST /api/v1/projects/{id}/director/materials/commit", "executor": "materialprep.service", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "导演模式", "operation": "确认导演包", "api": "POST /api/v1/director-packages/{package_id}/confirm", "executor": "director.package.confirm_package", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "导演模式", "operation": "开始真实拍摄", "api": "POST /api/v1/projects/{id}/director/production/start", "executor": "director.streamline.start_director_production", "ai_status": "GUARDED", "ai_action": "production_start"},
    {"area": "导演模式", "operation": "暂停/继续/取消生产", "api": "POST /api/v1/production-runs/{run_id}/pause|resume|cancel", "executor": "shine.director.run_engine", "ai_status": "GUARDED", "ai_action": "production_control"},
    {"area": "导演模式", "operation": "查看生产进度/阻塞", "api": "GET /api/v1/production-runs/{run_id}[/blockers]", "executor": "production ledger", "ai_status": "READ_ONLY", "ai_action": "read_production_run"},
    {"area": "导演模式", "operation": "审片与交付", "api": "GET/POST /api/v1/projects/{id}/deliveries", "executor": "media.delivery", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "素材中心", "operation": "搜索/复用素材", "api": "GET /api/v1/projects/{id}/asset-center/search", "executor": "asset_center search", "ai_status": "READ_ONLY", "ai_action": "read_missing_materials"},
    {"area": "素材中心", "operation": "候选图生成", "api": "POST /api/v1/projects/{id}/candidate-assets/generate", "executor": "candidate generation", "ai_status": "NOT_SUPPORTED", "ai_action": "—"},
    {"area": "素材中心", "operation": "候选图人工确认", "api": "POST /api/v1/candidate-assets/{identity}/promote", "executor": "asset promote", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "素材中心", "operation": "素材任务进度/失败原因", "api": "GET /api/v1/projects/{id}/material-tasks", "executor": "material ledger", "ai_status": "READ_ONLY", "ai_action": "read_task_failure"},
    {"area": "成片库", "operation": "字幕逐句编辑与导出审批", "api": "PUT /api/v1/projects/{id}/director/storyboard/shots/{shot_id}/subtitles", "executor": "api_streamline", "ai_status": "NOT_SUPPORTED", "ai_action": "—"},
    {"area": "成片库", "operation": "对外发布/分享", "api": "—", "executor": "—", "ai_status": "NOT_SUPPORTED", "ai_action": "—"},
    {"area": "设置", "operation": "工作流节点图数值级改写", "api": "workflow JSON", "executor": "—", "ai_status": "NOT_SUPPORTED", "ai_action": "—"},
    {"area": "设置", "operation": "查看环境裁决", "api": "GET /api/v1/setup/verdict", "executor": "api_setup.setup_verdict", "ai_status": "READ_ONLY", "ai_action": "—"},
    {"area": "设置", "operation": "一键整备缺失文件", "api": "POST /api/v1/setup/prepare/execute", "executor": "comfyops.setup_preparation.enqueue", "ai_status": "GUARDED", "ai_action": "setup_repair"},
    {"area": "设置", "operation": "核验/登记已有文件", "api": "POST /api/v1/setup/reconcile", "executor": "comfyops.setup_reconcile.reconcile_installation", "ai_status": "GUARDED", "ai_action": "setup_reconcile"},
    {"area": "设置", "operation": "安装计划授权与执行", "api": "POST /api/v1/comfyops/install-plans/{identity}/execute", "executor": "comfyops approval", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "设置", "operation": "释放受管渲染服务", "api": "POST /api/v1/setup/managed-processes/release", "executor": "comfyops.renderer_release", "ai_status": "GUARDED", "ai_action": "managed_release"},
    {"area": "设置", "operation": "选择 ComfyUI 实例", "api": "POST /api/v1/setup/installation/select", "executor": "setup summary", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "设置", "operation": "LLM Provider / 角色路由", "api": "GET/POST /api/v1/llm/providers, /llm/routes", "executor": "llm gateway", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "设置", "operation": "创建用户工作流副本", "api": "POST /api/v1/workflow-profiles", "executor": "api_comfy.create_workflow_profile", "ai_status": "GUARDED", "ai_action": "workflow_user_copy"},
    {"area": "设置", "operation": "工作流参数映射调整", "api": "POST /api/v1/workflow-profiles", "executor": "api_comfy.create_workflow_profile", "ai_status": "GUARDED", "ai_action": "workflow_user_copy"},
    {"area": "设置", "operation": "生成策略（变体/加速/二次采样/RelayVSR）", "api": "POST /api/v1/projects/{id}/director/production/start", "executor": "strategy.GenerationStrategy", "ai_status": "GUARDED", "ai_action": "production_start"},
    {"area": "设置", "operation": "联网检索开关（管家自身）", "api": "POST /api/v1/support/preferences", "executor": "support.agent.set_web_search", "ai_status": "GUARDED", "ai_action": "—"},
    {"area": "成片库", "operation": "浏览/下载成片", "api": "GET /api/v1/projects/{id}/video-library", "executor": "media.video_library", "ai_status": "READ_ONLY", "ai_action": "—"},
    {"area": "成片库", "operation": "删除历史视频", "api": "POST /api/v1/projects/{id}/video-library/delete", "executor": "media.video_library", "ai_status": "HUMAN_REQUIRED", "ai_action": "—"},
    {"area": "全局", "operation": "任务失败诊断", "api": "jobs/job_attempts + /setup/verdict", "executor": "support.diagnostics", "ai_status": "READ_ONLY", "ai_action": "read_task_failure"},
    {"area": "全局", "operation": "生成工程问题报告", "api": "POST /api/v1/support/reports", "executor": "support.reports.generate_report", "ai_status": "GUARDED", "ai_action": "generate_support_report"},
)


def _capability(row: Capability) -> dict[str, Any]:
    payload = asdict(row)
    payload["tier_label"] = TIERS.get(row.tier, row.tier)
    payload["status_label"] = STATUSES.get(row.status, row.status)
    payload["requires_confirmation"] = row.tier in {"PREVIEW", "EXECUTE_GUARDED"}
    return payload


def catalog_payload() -> dict[str, Any]:
    """Everything the UI/API needs, with the honest matrix alongside."""
    matrix_counts = {status: 0 for status in STATUSES}
    for row in MATRIX:
        matrix_counts[row["ai_status"]] = matrix_counts.get(row["ai_status"], 0) + 1
    return {
        "version": CATALOG_VERSION,
        "principles": {
            "facts": "所有状态读取既有权威台账，不新建第二套事实。",
            "verdict": "环境/生产/冻结/交付仍由原业务门禁裁决，AI 不宣布 READY。",
            "executor": "每个写动作只有一个既有执行者。",
            "source_write": "运行时管家没有源码/Shell/Git/任意文件工具。",
        },
        "tiers": TIERS,
        "statuses": STATUSES,
        "capabilities": [_capability(row) for row in CAPABILITIES],
        "read_capabilities": [_capability(row) for row in READ_CAPABILITIES],
        "matrix": list(MATRIX),
        "matrix_totals": {
            "operations": len(MATRIX),
            "by_status": matrix_counts,
        },
        "human_required": [
            {"operation": operation, "api": api, "reason": reason, "status": status}
            for operation, api, reason, status in _HUMAN
        ],
        "not_supported": [
            {"operation": operation, "api": api, "reason": reason}
            for operation, api, reason in _NOT_SUPPORTED
        ],
        "forbidden": {
            name: "运行时管家不具备"
            for name in (
                "shell",
                "python_exec",
                "git",
                "pip_install",
                "write_source",
                "write_node_source",
                "delete_user_data",
                "network_script",
                "bypass_gate",
                "generic_http",
            )
        },
        "read_tools": [spec.name for spec in READ_TOOLS],
        "note": "未列入矩阵的操作不计入覆盖率；不得把 NOT_SUPPORTED 表述为可用。",
    }


def action_ids() -> list[str]:
    return [row.action_id for row in CAPABILITIES]


def capability(action_id: str) -> Capability | None:
    for row in CAPABILITIES:
        if row.action_id == action_id:
            return row
    return None


def plan_step_for(next_action_kind: str) -> dict[str, str]:
    """Map a director overview ``next_action`` to an honest plan step.

    Used by the bounded planner; it never invents a capability.
    """
    mapping = {
        "RUN_STORYBOARD": {"ai_action": "storyboard_generate", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "RETRY_STORYBOARD": {"ai_action": "storyboard_generate", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "START_PRODUCTION": {"ai_action": "production_start", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "BUILD_PACKAGE": {"ai_action": "production_start", "status": "COVERED", "tier": "EXECUTE_GUARDED"},
        "PREPARE_MATERIALS": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "CONFIRM_EPISODE_SHOTS": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "CONFIRM_BOOK": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "IMPORT_SCRIPT": {"ai_action": "document_import_text", "status": "COVERED", "tier": "PREVIEW"},
        "REVIEW_EPISODE": {"ai_action": None, "status": "HUMAN_REQUIRED", "tier": "HUMAN_REQUIRED"},
        "WAIT": {"ai_action": None, "status": "READ_ONLY", "tier": "READ"},
        "COMPLETE": {"ai_action": None, "status": "READ_ONLY", "tier": "READ"},
    }
    return mapping.get(
        next_action_kind,
        {"ai_action": None, "status": "NOT_SUPPORTED", "tier": "HUMAN_REQUIRED"},
    )

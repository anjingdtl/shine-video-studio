"""Server-side tool registry and permission tiers.

Permissions are enforced here, not in a system prompt.  A tool that is not in
the registry cannot be invoked, and the only write path the runtime agent owns
is the controlled Markdown report writer.  Shells, source edits, Git and package
installs are not merely discouraged: they do not exist as callable tools.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ToolDenied(RuntimeError):
    """The requested capability is not registered or not permitted."""


class ToolTier(StrEnum):
    READ = "READ"
    CONFIRM = "CONFIRM"
    FORBIDDEN = "FORBIDDEN"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: ToolTier
    description: str
    #: The existing executor a CONFIRM tool delegates to (never a new one).
    executor: str | None = None


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    label: str
    tier: ToolTier
    executor: str
    mutates: bool
    description: str


MAX_TOOL_STEPS = 8

READ_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("read_support_context", ToolTier.READ, "读取当前页面的只读诊断快照"),
    ToolSpec("read_setup_verdict", ToolTier.READ, "读取 /setup/verdict 唯一环境裁决"),
    ToolSpec("read_resource_inventory", ToolTier.READ, "读取持久资源索引与缺失能力"),
    ToolSpec("read_comfy_runtime", ToolTier.READ, "读取受管渲染器与运行配置状态"),
    ToolSpec("read_task_failure", ToolTier.READ, "读取具名任务/尝试的失败事实"),
    ToolSpec("read_quality_gates", ToolTier.READ, "读取该任务的质检门记录与错误码"),
    ToolSpec("read_workflow_contract", ToolTier.READ, "读取任务绑定的工作流合同与节点清单"),
    ToolSpec("read_installation_receipts", ToolTier.READ, "读取原有安装尝试的成功/失败回执"),
    ToolSpec("read_runtime_environment", ToolTier.READ, "读取 Worker 与 GPU/服务摘要"),
    ToolSpec("read_gpu_occupancy", ToolTier.READ, "读取既有 GPU 租约/生产占用裁决"),
    ToolSpec("search_local_knowledge", ToolTier.READ, "检索版本化本地知识库"),
    ToolSpec("search_official_web", ToolTier.READ, "检索官方文档与上游 Issue（默认关闭，需用户开启）"),
    ToolSpec("read_action_progress", ToolTier.READ, "读取原执行器任务进度"),
    ToolSpec("generate_support_report", ToolTier.CONFIRM, "在受控目录生成脱敏 Markdown 报告"),
)

ACTION_SPECS: dict[str, ActionSpec] = {
    "setup_repair": ActionSpec(
        kind="setup_repair",
        label="补齐真实制作环境",
        tier=ToolTier.CONFIRM,
        executor="shine.comfyops.setup_preparation.enqueue",
        mutates=True,
        description="复用统一安装器的持久准备任务，不会启动第二套安装器。",
    ),
    "setup_reconcile": ActionSpec(
        kind="setup_reconcile",
        label="重新核验并登记已存在的本机文件",
        tier=ToolTier.CONFIRM,
        executor="shine.comfyops.setup_reconcile.reconcile_installation",
        mutates=True,
        description="复用现有回执、只对变化文件计算哈希，不修改别人的 ComfyUI。",
    ),
    "managed_release": ActionSpec(
        kind="managed_release",
        label="停止本工作台拥有的渲染服务并释放显存",
        tier=ToolTier.CONFIRM,
        executor="shine.comfyops.renderer_release.release_managed_processes_guarded",
        mutates=True,
        description="仅释放有归属证明且没有生产任务的服务；外部 ComfyUI 不会被强停。",
    ),
    "workflow_user_copy": ActionSpec(
        kind="workflow_user_copy",
        label="创建用户级工作流副本",
        tier=ToolTier.CONFIRM,
        executor="shine.api_comfy.create_workflow_profile",
        mutates=True,
        description="复制工作流为用户级副本并走既有校验；不修改 Git 跟踪的内置模板。",
    ),
    "project_create": ActionSpec(
        kind="project_create",
        label="创建影视项目",
        tier=ToolTier.CONFIRM,
        executor="shine.services.create_project",
        mutates=True,
        description="只创建一个空项目，不导入原文、不冻结风格。",
    ),
    "document_import_text": ActionSpec(
        kind="document_import_text",
        label="导入原文文本",
        tier=ToolTier.CONFIRM,
        executor="shine.services.import_project_document",
        mutates=True,
        description="与界面导入同一解析、去重与落库；不自动切换正式底稿。",
    ),
    "project_settings_update": ActionSpec(
        kind="project_settings_update",
        label="修改项目用户级设置",
        tier=ToolTier.CONFIRM,
        executor="shine.services.update_project_settings",
        mutates=True,
        description="走与 PATCH 设置路由同一个服务；不触碰冻结风格与生产合同。",
    ),
    "story_outline_generate": ActionSpec(
        kind="story_outline_generate",
        label="生成故事大纲",
        tier=ToolTier.CONFIRM,
        executor="shine.materialprep.story.queue_outline",
        mutates=True,
        description="复用持久大纲任务与既有角色路由；大纲确认仍由用户亲自完成。",
    ),
    "storyboard_generate": ActionSpec(
        kind="storyboard_generate",
        label="生成导演分镜",
        tier=ToolTier.CONFIRM,
        executor="shine.director.streamline.queue_director_storyboard",
        mutates=True,
        description="复用持久分镜任务；缺风格/大纲/分集方案时由原门禁具名拒绝。",
    ),
    "production_start": ActionSpec(
        kind="production_start",
        label="开始真实拍摄选定集",
        tier=ToolTier.CONFIRM,
        executor="shine.director.streamline.start_director_production",
        mutates=True,
        description="复用导演包与 Run 引擎；相同选择返回同一运行，不另建生产队列。",
    ),
}

FORBIDDEN_CAPABILITIES: dict[str, str] = {
    "shell": "运行 PowerShell / cmd / 任意系统命令",
    "python_exec": "执行 Python eval/exec",
    "git": "git commit / push / PR / 修改历史",
    "pip_install": "执行 pip install 或修改依赖",
    "write_source": "写入或覆盖 Git 跟踪的工程源码、测试、标准模板",
    "write_node_source": "修改 ComfyUI 自定义节点源码或启动脚本",
    "delete_user_data": "删除模型、缓存、成片或用户素材",
    "network_script": "下载并执行任意 URL 脚本",
    "bypass_gate": "绕过 Style Bible、主资产确认、Ref2VA 或最终验收门禁",
    "force_reconcile": "直接补发 UNKNOWN 任务替代原 reconcile 流程",
}

_TOOL_INDEX: dict[str, ToolSpec] = {spec.name: spec for spec in READ_TOOLS}

_FORBIDDEN_INTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shell", re.compile(r"(?i)\b(powershell|cmd\.exe|bash|shell|终端命令|系统命令)\b")),
    ("git", re.compile(r"(?i)\bgit\s+(commit|push|merge|rebase|reset)\b|提交\s*PR|创建\s*PR")),
    ("pip_install", re.compile(r"(?i)\bpip\s+install\b|\buv\s+pip\s+install\b")),
    ("python_exec", re.compile(r"(?i)\bpython\s+-c\b|\beval\(|\bexec\(")),
    ("write_source", re.compile(r"(?i)(修改|编辑|写入|覆盖|删除).{0,8}(源码|源代码|代码|模板|\.py\b|\.ts\b|\.tsx\b)")),
    ("delete_user_data", re.compile(r"(?i)(删除|清空|移除).{0,8}(模型|权重|素材|成片|缓存)")),
    ("bypass_gate", re.compile(r"(?i)(跳过|绕过|关闭).{0,8}(审核|验收|门禁|质检|风格冻结)")),
)


def tool_spec(name: str) -> ToolSpec:
    spec = _TOOL_INDEX.get(name)
    if spec is None:
        raise ToolDenied(f"未知或未授权的工具：{name}")
    return spec


def action_spec(kind: str) -> ActionSpec:
    spec = ACTION_SPECS.get(kind)
    if spec is None:
        raise ToolDenied(f"未知或未授权的配置动作：{kind}")
    return spec


def read_tool_names() -> list[str]:
    return [spec.name for spec in READ_TOOLS]


def forbidden_intent(text: str) -> list[str]:
    """Named dangerous intents in user/evidence text.

    Detection never executes anything and never grants a tool.  The caller
    records the refusal and answers with a report path instead.
    """
    matches = [name for name, pattern in _FORBIDDEN_INTENT_PATTERNS if pattern.search(text or "")]
    return matches


def clamp_tool_steps(steps: list[object]) -> list[object]:
    return steps[:MAX_TOOL_STEPS]

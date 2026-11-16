"""Versioned local knowledge and optional official-source lookup.

Local entries are bound to a build and describe this product's own contracts.
They are evidence for explanations, never executable repair instructions.
Web retrieval is opt-in (default off), allow-listed to official sources and
returned as references with a URL and date.  No embedding model and no GPU are
used, so a diagnosis never competes with a render for VRAM.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from shine.support.privacy import redact_text

KNOWLEDGE_VERSION = "shine-support-kb-2026.11.16"

OFFICIAL_HOSTS: tuple[str, ...] = (
    "docs.comfy.org",
    "github.com",
    "pytorch.org",
    "docs.nvidia.com",
    "developer.nvidia.com",
)

#: Curated official references per topic.  These are documentation pointers,
#: not fetched pages: the agent links the person to the official text and never
#: turns external prose into an instruction to run something.
OFFICIAL_REFERENCES: dict[str, tuple[dict[str, str], ...]] = {
    "comfyui": (
        {
            "title": "ComfyUI 官方文档",
            "url": "https://docs.comfy.org/",
            "source": "Comfy-Org",
            "note": "安装、模型目录与节点行为的官方说明。",
        },
        {
            "title": "ComfyUI 仓库与 Issue",
            "url": "https://github.com/comfyanonymous/ComfyUI/issues",
            "source": "GitHub",
            "note": "上游运行问题与已知缺陷。",
        },
    ),
    "nodes": (
        {
            "title": "ComfyUI Manager（节点管理）",
            "url": "https://github.com/Comfy-Org/ComfyUI-Manager",
            "source": "Comfy-Org",
            "note": "节点安装与依赖问题的上游说明；其安装器不与 Shine 并行执行。",
        },
    ),
    "cuda": (
        {
            "title": "NVIDIA CUDA 文档",
            "url": "https://docs.nvidia.com/cuda/",
            "source": "NVIDIA",
            "note": "驱动与 CUDA 兼容性参考。",
        },
    ),
    "pytorch": (
        {
            "title": "PyTorch 官方安装与兼容矩阵",
            "url": "https://pytorch.org/get-started/locally/",
            "source": "PyTorch",
            "note": "Python / CUDA wheel 组合的官方来源。",
        },
    ),
    "shine": (
        {
            "title": "Shine Video Studio 仓库",
            "url": "https://github.com/anjingdtl/shine-video-studio",
            "source": "GitHub",
            "note": "本产品的公开代码与问题入口。",
        },
    ),
}


def _entry(entry_id: str, title: str, tags: list[str], body: str, source: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "title": title,
        "tags": tags,
        "body": body,
        "source": source,
        "knowledge_version": KNOWLEDGE_VERSION,
        "evidence_level": "LOCAL_KB",
    }


LOCAL_ENTRIES: tuple[dict[str, Any], ...] = (
    _entry(
        "setup-verdict",
        "环境裁决只有一个",
        ["setup", "verdict", "ready", "环境", "就绪"],
        "基础配置、顶部状态与 AI 管家读取同一个 /api/v1/setup/verdict。"
        "任何一处显示“未就绪”，都表示真实制作门禁尚未通过；AI 的解释不能替代它。",
        "docs/ACCEPTANCE_20261116_DIRECTOR_SETUP_CONSOLIDATION.md",
    ),
    _entry(
        "setup-prepare",
        "一键整备是可恢复的持久任务",
        ["prepare", "install", "下载", "安装", "准备"],
        "环境整备 POST /api/v1/setup/prepare/execute 由常驻 Worker 推进。"
        "关闭页面或重启后会继续；相同请求会返回同一个任务，不会重复下载。",
        "backend/shine/comfyops/setup_preparation.py",
    ),
    _entry(
        "reconcile",
        "本机已有文件先核验再复用",
        ["reconcile", "核验", "登记", "回执", "模型已存在"],
        "模型文件存在不等于可用。系统用固定清单的 SHA-256 回执确认文件，"
        "未通过核验的文件不会被登记或替换。",
        "backend/shine/comfyops/setup_reconcile.py",
    ),
    _entry(
        "unknown-outcome",
        "提交结果未知的任务必须核对",
        ["unknown", "reconcile", "重试", "提交", "任务"],
        "外部任务返回 UNKNOWN 时，系统不会自动重发。必须先走原有 reconcile/人工裁决流程，"
        "确认远端是否已经执行，避免重复扣费或重复占用 GPU。",
        "backend/shine/models.py",
    ),
    _entry(
        "gpu-lease",
        "GPU 使用权是互斥的",
        ["gpu", "lease", "显存", "忙", "租约"],
        "真实生成、图片模型和审片共享同一块显卡。GPU Lease 未释放或有生产任务在跑时，"
        "新的安装、验证或修复必须等待；AI 管家不会强杀任务或抢占显存。",
        "backend/shine/comfyops/models.py",
    ),
    _entry(
        "workflow-template",
        "内置工作流模板不可修改",
        ["workflow", "工作流", "模板", "节点", "源码"],
        "仓库内的标准工作流是受 Git 管理的产品合同。用户级调整应先创建项目级副本并重新验证；"
        "修改内置模板或节点源码属于工程问题，AI 管家只会生成报告。",
        "backend/shine/api_comfy.py",
    ),
    _entry(
        "llm-role",
        "AI 管家使用独立的文本角色",
        ["llm", "模型", "provider", "路由", "support_agent"],
        "AI 管家使用 support_agent 角色，复用现有的 ProviderProfile、密钥托管与 LLM Gateway。"
        "没有配置或服务欠费时，本地规则诊断和报告仍可用。",
        "backend/shine/llm/role_catalog.py",
    ),
    _entry(
        "image-capabilities",
        "出图的六种能力彼此独立",
        ["image", "出图", "t2i", "i2i", "inpaint", "multiref", "wardrobe", "rgba"],
        "Qwen 出图的六种能力按项目资源单独核验。一种能力缺模型时，"
        "只影响该能力，不应阻断其它已核验能力。",
        "backend/shine/comfyops/setup_summary.py",
    ),
    _entry(
        "h3-variant",
        "H3 变体不能静默替换",
        ["h3", "ref2va", "变体", "variant", "视频"],
        "真实视频只使用 MiniMax-H3 / Ref2VA 正式链路。变体与工作流不匹配时，"
        "系统应拒绝而不是改用别的视频模型。",
        "backend/shine/comfyops/variant_recipe.py",
    ),
    _entry(
        "report-boundary",
        "工程问题只能移交",
        ["report", "报告", "bug", "blocker", "需求", "开发者"],
        "需要修改源码的问题由 AI 管家生成 BLOCKER / BUG / REQUIREMENT Markdown，"
        "存放在本机 support-reports 目录，由用户自行提交开发者；不会自动建 Issue 或 PR。",
        "docs/SHINE_AI_COPILOT_SUPPORT_AGENT_V1_BUILD_PLAN_20261116.md",
    ),
)


def _tokenize(query: str) -> list[str]:
    lowered = (query or "").casefold()
    tokens = [token for token in urllib.parse.unquote(lowered).replace("，", " ").replace(",", " ").split() if token]
    if not tokens and lowered:
        tokens = [lowered]
    return tokens[:12]


def search_local(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    tokens = _tokenize(query)
    if not tokens:
        return [dict(entry, score=0) for entry in LOCAL_ENTRIES[:limit]]
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in LOCAL_ENTRIES:
        haystack = " ".join(
            [entry["title"], entry["body"], " ".join(entry["tags"]), entry["id"]]
        ).casefold()
        score = sum(3 if token in entry["id"].casefold() else 1 for token in tokens if token in haystack)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [dict(entry, score=score) for score, entry in scored[:limit]]


def _topic_for(query: str) -> str:
    lowered = (query or "").casefold()
    if any(word in lowered for word in ("节点", "node", "manager")):
        return "nodes"
    if any(word in lowered for word in ("cuda", "驱动", "nvidia")):
        return "cuda"
    if any(word in lowered for word in ("pytorch", "torch", "wheel")):
        return "pytorch"
    if any(word in lowered for word in ("shine", "工作台")):
        return "shine"
    return "comfyui"


def _allowed(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in OFFICIAL_HOSTS


def search_official_web(query: str, *, enabled: bool = False, timeout_s: float = 4.0) -> dict[str, Any]:
    """Official-source references, disabled unless the person opted in.

    The query is redacted before it can leave the machine.  Results are marked
    ``REFERENCE_ONLY``: they cannot change local facts or grant a tool.
    """
    safe_query = redact_text(query, limit=200)
    if not enabled:
        return {
            "enabled": False,
            "query": safe_query,
            "results": [],
            "notice": "联网检索默认关闭；打开后只查询官方文档与上游仓库。",
        }
    references = OFFICIAL_REFERENCES.get(_topic_for(query), OFFICIAL_REFERENCES["comfyui"])
    results = []
    for reference in references:
        if not _allowed(reference["url"]):
            continue
        item = {**reference, "retrieved_at": None, "availability": "REFERENCE_ONLY"}
        try:
            request = urllib.request.Request(
                reference["url"],
                method="HEAD",
                headers={"User-Agent": "Shine-Copilot/1.0 (+local diagnosis)"},
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - allow-listed hosts
                item["availability"] = f"HTTP_{response.status}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            item["availability"] = "UNREACHABLE"
        results.append(item)
    return {
        "enabled": True,
        "query": safe_query,
        "results": results,
        "notice": "外部资料只作为参考证据，不能作为本机修复指令。",
    }


def dump_local_index() -> str:
    return json.dumps(
        {"version": KNOWLEDGE_VERSION, "entries": LOCAL_ENTRIES},
        ensure_ascii=False,
        indent=2,
    )

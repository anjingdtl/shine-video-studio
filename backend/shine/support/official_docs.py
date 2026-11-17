"""Official upstream knowledge: ComfyUI and MiniMax-H3, reference-only.

Two rules shape this module:

* **Licence-safe by default.**  The copilot keeps short, self-written summaries
  and source links.  It never bundles upstream full text, images or model
  weights into the installer; whether a page may be redistributed is not
  asserted unless the upstream licence is explicitly verified.
* **Official capability is not local capability.**  MiniMax publishes Ref2VA,
  FL2VA and 2K modules; this Shine build only runs the audited Ref2VA chain.
  The boundary below is part of the answer, so a person never hears "H3 can do
  X" and concludes "this machine can do X".

Nothing here performs a network request.  Live upstream lookups stay in
``support.knowledge.search_official_web`` behind the explicit user toggle.
"""
from __future__ import annotations

from typing import Any

OFFICIAL_DOCS_VERSION = "shine-official-docs-2026.11.16"
EVIDENCE_LEVEL = "OFFICIAL_REFERENCE"
LICENCE_POLICY = (
    "仅保存自编短摘要、来源地址、版本与抓取说明；不整库复制官方文档、模型权重或图片。"
    "上游许可未逐条核实前不打包分发；需要原文时跳转官方链接。"
)

SOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "comfy-docs",
        "title": "ComfyUI 官方文档",
        "url": "https://docs.comfy.org/",
        "repo": "https://github.com/comfy-org/docs",
        "publisher": "Comfy-Org",
        "topics": ["startup", "workflow", "nodes", "models", "api", "compatibility", "errors"],
        "reuse": "SUMMARY_ONLY",
        "licence_note": "官方文档站点与文档仓库；再分发条款以官方许可为准，未核实前不复制全文。",
    },
    {
        "id": "comfy-repo",
        "title": "ComfyUI 代码仓库",
        "url": "https://github.com/comfyanonymous/ComfyUI",
        "repo": "https://github.com/comfyanonymous/ComfyUI",
        "publisher": "Comfy Org",
        "topics": ["server", "api", "custom_nodes", "compatibility"],
        "reuse": "LINK_ONLY",
        "licence_note": "代码与 Issue 仅作官方参考；不把源码复制进本产品。",
    },
    {
        "id": "h3-repo",
        "title": "MiniMax-H3 官方仓库",
        "url": "https://github.com/MiniMax-AI/MiniMax-H3",
        "repo": "https://github.com/MiniMax-AI/MiniMax-H3",
        "publisher": "MiniMax-AI",
        "topics": ["architecture", "ref2va", "fl2va", "2k", "prompt", "comfyui"],
        "reuse": "SUMMARY_ONLY",
        "licence_note": "官方仓库与中文 README；许可与再分发条件以仓库声明为准。",
    },
    {
        "id": "h3-zh-readme",
        "title": "MiniMax-H3 中文说明",
        "url": "https://github.com/MiniMax-AI/MiniMax-H3/blob/main/README.zh-CN.md",
        "repo": "https://github.com/MiniMax-AI/MiniMax-H3",
        "publisher": "MiniMax-AI",
        "topics": ["architecture", "usage", "variants"],
        "reuse": "SUMMARY_ONLY",
        "licence_note": "官方中文 README；只引用摘要与链接。",
    },
    {
        "id": "h3-prompt-skill",
        "title": "H3 官方提示词 Skill（h3-prompt-writing）",
        "url": "https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing",
        "repo": "https://github.com/MiniMax-AI/MiniMax-H3",
        "publisher": "MiniMax-AI",
        "topics": ["prompt", "shot_language", "camera"],
        "reuse": "SUMMARY_ONLY",
        "licence_note": "官方提示词规范；本机只保存规则版本摘要与引用。",
    },
    {
        "id": "h3-huggingface",
        "title": "MiniMax-H3 模型权重与许可",
        "url": "https://huggingface.co/MiniMaxAI/MiniMax-H3",
        "repo": "https://huggingface.co/MiniMaxAI/MiniMax-H3",
        "publisher": "MiniMaxAI",
        "topics": ["weights", "licence", "variants"],
        "reuse": "LINK_ONLY",
        "licence_note": "权重与许可由 MiniMax 发布；本产品不打包权重，下载走固定清单与用户授权。",
    },
)

#: Self-written summaries, each with a stable id and its upstream source id.
SUMMARIES: tuple[dict[str, Any], ...] = (
    {
        "id": "comfy-startup",
        "source_id": "comfy-docs",
        "topic": "startup",
        "title": "ComfyUI 启动与服务",
        "summary": "ComfyUI 是本地服务：启动后通过 HTTP 提供队列与对象信息。"
        "Shine 的受管渲染器由工作台启动并持有 GPU 租约；外部实例不会被强停。",
        "apply": "本机是否可用只看 /api/v1/setup/verdict 与受管进程台账。",
    },
    {
        "id": "comfy-workflow-json",
        "source_id": "comfy-docs",
        "topic": "workflow",
        "title": "工作流 JSON 与节点输入",
        "summary": "工作流是节点图 JSON；参数通过节点 inputs 传入。"
        "Shine 的工作流合同要求显式 parameter_map 与节点校验，缺失映射会阻断提交。",
        "apply": "内置模板不可改；用户级调整创建副本并重新校验。",
    },
    {
        "id": "comfy-models",
        "source_id": "comfy-docs",
        "topic": "models",
        "title": "模型目录与加载",
        "summary": "模型按目录（checkpoints/loras 等）被发现并由加载节点引用。"
        "Shine 用固定清单与 SHA-256 回执登记文件，文件存在不等于可用。",
        "apply": "模型是否可用以能力裁决与核验回执为准，不以文件存在推断。",
    },
    {
        "id": "comfy-api",
        "source_id": "comfy-docs",
        "topic": "api",
        "title": "ComfyUI HTTP/WebSocket API",
        "summary": "官方 API 支持提交 prompt、查询队列与历史，并通过 WebSocket 推送进度。"
        "Shine 不向运行时 AI 管家开放通用 HTTP 工具，只允许已注册能力委托原执行器。",
        "apply": "AI 管家不能直接调用 ComfyUI，也不能绕过调度器提交任务。",
    },
    {
        "id": "comfy-custom-nodes",
        "source_id": "comfy-docs",
        "topic": "nodes",
        "title": "自定义节点与依赖",
        "summary": "自定义节点可能引入 Python 依赖；版本冲突表现为导入失败或节点缺失。"
        "Shine 的节点包白名单与合同校验在提交前阻断缺节点的工作流。",
        "apply": "缺节点是工程/安装问题；AI 管家只能生成报告，不能改节点源码。",
    },
    {
        "id": "comfy-compat",
        "source_id": "comfy-docs",
        "topic": "compatibility",
        "title": "Python / PyTorch / CUDA 兼容",
        "summary": "ComfyUI 的可用组合由 Python、PyTorch wheel 与 CUDA/驱动共同决定。"
        "本机版本只能从现有运行时与安装回执观测，不能凭推测补全。",
        "apply": "观测不到版本时回答 UNKNOWN，不宣称兼容。",
    },
    {
        "id": "h3-architecture",
        "source_id": "h3-repo",
        "topic": "architecture",
        "title": "MiniMax-H3 架构与能力范围",
        "summary": "官方 H3 提供基础视频生成、上下文/IR、Ref2VA/FL2VA 与 2K 再生等模块；"
        "这些是官方发布的能力，不等同于当前 Shine 本机已安装且已验收的能力。",
        "apply": "以本机变体清单与工作流合同为准。",
    },
    {
        "id": "h3-ref2va",
        "source_id": "h3-repo",
        "topic": "ref2va",
        "title": "Ref2VA（参考驱动视频与音频）",
        "summary": "Ref2VA 以参考图/视频/音频驱动镜头，是 Shine 唯一允许的真实视频链路；"
        "缺少可用参考时按名拒绝（PROJECT_REQUIRES_REF2VA），不会退回纯文本生成。",
        "apply": "Shine 当前支持。",
    },
    {
        "id": "h3-fl2va",
        "source_id": "h3-repo",
        "topic": "fl2va",
        "title": "FL2VA（首尾帧驱动）",
        "summary": "官方提供的首尾帧模式。Shine 的模式策略明确拒绝该模式。",
        "apply": "当前 Shine 不支持；不能因为官方存在就宣称可用。",
    },
    {
        "id": "h3-2k",
        "source_id": "h3-repo",
        "topic": "2k",
        "title": "H3-Regenerate-2K 再生模块",
        "summary": "官方 2K 再生模块；Shine 当前没有安装/登记该模块，也不静默替换视频核心。",
        "apply": "当前 Shine 不支持。",
    },
    {
        "id": "h3-prompt",
        "source_id": "h3-prompt-skill",
        "topic": "prompt",
        "title": "H3 官方提示词规范",
        "summary": "官方提示词 Skill 规定镜头语言、参考标签与段落顺序。Shine 的编译器固定版本与哈希，"
        "只由 H3PromptCompiler 产出最终提示词，禁止自由文本直接提交。",
        "apply": "Shine 已接入固定规则版本；请按导演页提示词规则填写。",
    },
    {
        "id": "h3-inputs",
        "source_id": "h3-zh-readme",
        "topic": "inputs",
        "title": "参考输入约束",
        "summary": "参考图、参考视频与音频各有格式与数量约束；缺映射或媒体转运适配器时提交被阻断。",
        "apply": "以工作流合同与参考槽校验为准。",
    },
)

#: Official capability vs what this build really runs.  Answers must show both.
_H3_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "capability": "H3-Base Ref2VA",
        "official": True,
        "shine_supported": True,
        "evidence": "backend/shine/h3/mode_policy.py（Ref2VA 唯一真实链路）",
    },
    {
        "capability": "H3-Base FL2VA",
        "official": True,
        "shine_supported": False,
        "evidence": "backend/shine/h3/mode_policy.py（拒绝首尾帧模式）",
    },
    {
        "capability": "H3-Regenerate-2K",
        "official": True,
        "shine_supported": False,
        "evidence": "本机变体清单未登记该模块；不得静默替换视频核心",
    },
    {
        "capability": "H3-Context-IR",
        "official": True,
        "shine_supported": False,
        "evidence": "当前构建未接入该上下文模块",
    },
    {
        "capability": "h3-prompt-writing（官方提示词规范）",
        "official": True,
        "shine_supported": True,
        "evidence": "backend/shine/h3/compiler.py（固定规则版本与哈希）",
    },
    {
        "capability": "T2VA（纯文本生成视频）",
        "official": False,
        "shine_supported": False,
        "evidence": "Shine 要求参考驱动；无参考按名拒绝",
    },
)


def sources_payload() -> dict[str, Any]:
    return {
        "version": OFFICIAL_DOCS_VERSION,
        "licence_policy": LICENCE_POLICY,
        "sources": [
            {
                **source,
                "evidence_level": EVIDENCE_LEVEL,
                "verified_at": "2026-11-16",
            }
            for source in SOURCES
        ],
    }


def h3_boundary() -> dict[str, Any]:
    return {
        "official_supported": [row for row in _H3_CAPABILITIES if row["official"]],
        "shine_supported": [row for row in _H3_CAPABILITIES if row["shine_supported"]],
        "notice": (
            "官方 H3 的全部能力不等于当前 Shine 本机已具备的能力；"
            "真实可用性以本机已装变体、工作流合同与硬件准入为准。"
        ),
        "evidence_level": EVIDENCE_LEVEL,
    }


_H3_KEYWORDS = ("h3", "ref2va", "fl2va", "2k", "minimax", "视频模型", "首尾帧", "再生")


def _matches(query: str) -> list[dict[str, Any]]:
    lowered = (query or "").casefold()
    tokens = {token for token in lowered.replace("，", " ").split() if token}
    hits: list[dict[str, Any]] = []
    for summary in SUMMARIES:
        haystack = " ".join(
            [summary["title"], summary["summary"], summary["topic"], summary["apply"]]
        ).casefold()
        score = sum(1 for token in tokens if token in haystack)
        if any(word in lowered for word in _H3_KEYWORDS) and summary["id"].startswith("h3-"):
            score += 3
        if any(word in lowered for word in ("comfy", "节点", "工作流", "模型目录", "cuda", "torch")):
            if summary["id"].startswith("comfy-"):
                score += 3
        if score:
            hits.append({**summary, "score": score})
    hits.sort(key=lambda row: (-row["score"], row["id"]))
    return hits


def search(query: str, *, limit: int = 4, include_h3_boundary: bool = True) -> dict[str, Any]:
    hits = _matches(query)[: max(1, min(limit, 6))]
    source_index = {source["id"]: source for source in SOURCES}
    results = [
        {
            **hit,
            "url": source_index.get(hit["source_id"], {}).get("url"),
            "publisher": source_index.get(hit["source_id"], {}).get("publisher"),
            "reuse": source_index.get(hit["source_id"], {}).get("reuse"),
            "evidence_level": EVIDENCE_LEVEL,
            "availability": "REFERENCE_ONLY",
        }
        for hit in hits
    ]
    boundary = h3_boundary() if include_h3_boundary else None
    return {
        "version": OFFICIAL_DOCS_VERSION,
        "answered": bool(results),
        "results": results,
        "boundary": boundary,
        "licence_policy": LICENCE_POLICY,
        "unknown_reason": None if results else "官方知识摘要中没有与问题匹配的条目。",
        "notice": "官方资料只作参考，不是本机修复指令；本机是否可用以权威裁决为准。",
    }

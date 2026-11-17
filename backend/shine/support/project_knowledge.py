# ruff: noqa: E501 - curated data tables keep one row per real operation
"""Versioned, offline project knowledge for the Shine copilot.

The index answers "what does *this* build of Shine really do?" from a curated
catalogue of the product's own modules.  Every entry names its source files,
its public API and the automated evidence behind it, and carries an explicit
status that separates *implemented*, *partially implemented*, *planned*,
*blocked* and *not supported*.  A design document never becomes "already
shipped" because it is in the same repository.

The index is bound to the build: ``support/knowledge_manifest.json``
records the commit and the content hash it was generated from.  When the
running build cannot prove it matches, the answer is still produced but is
explicitly marked UNKNOWN/STALE - the copilot never presents outdated prose as
current fact.

Retrieval is a small deterministic BM25-like score over the catalogue.  It uses
no embedding model, no GPU and no network, so opening the copilot never steals
VRAM from a render and never scans the worktree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

KNOWLEDGE_VERSION = "shine-project-kb-2026.11.16-v2"
MANIFEST_PATH = Path(__file__).with_name("knowledge_manifest.json")

#: Evidence levels, best first.  The answer shows this so a person can tell a
#: product fact from an official upstream reference.
EVIDENCE_LEVELS = (
    "PRODUCT_FACT",
    "VERSIONED_PROJECT_DOCS",
    "OFFICIAL_REFERENCE",
    "COMMUNITY_REFERENCE",
)

_STATUS_LABELS = {
    "IMPLEMENTED": "已实现",
    "PARTIAL": "部分实现",
    "PLANNED": "仅有方案",
    "BLOCKED": "受阻",
    "NOT_SUPPORTED": "当前版本不支持",
}

_VERIFICATION_LABELS = {
    "AUTOMATED": "有自动化测试",
    "HARDWARE_REQUIRED": "需要真机验证",
    "NOT_RUN": "未执行",
    "BLOCKED": "受阻",
}


def _entry(
    entry_id: str,
    title: str,
    module: str,
    status: str,
    summary: str,
    *,
    sources: list[str],
    apis: list[str] | None = None,
    evidence: list[str] | None = None,
    verification: str = "AUTOMATED",
    tags: list[str] | None = None,
    not_supported: str | None = None,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "title": title,
        "module": module,
        "status": status,
        "status_label": _STATUS_LABELS[status],
        "summary": summary,
        "sources": sources,
        "apis": apis or [],
        "evidence": evidence or [],
        "verification": verification,
        "verification_label": _VERIFICATION_LABELS.get(verification, verification),
        "tags": tags or [],
        "not_supported": not_supported,
        "knowledge_version": KNOWLEDGE_VERSION,
        "evidence_level": "VERSIONED_PROJECT_DOCS",
    }


#: Curated product facts.  These describe the checked-in code, not the wish list.
PROJECT_ENTRIES: tuple[dict[str, Any], ...] = (
    _entry(
        "web-workbench",
        "全局 Web 工作台与导航",
        "workbench",
        "IMPLEMENTED",
        "FastAPI 本机服务 + React 单页工作台，hash 路由，默认只绑定 localhost。"
        "导演模式与素材中心共用一套项目与任务台账；不另开独立 AI 控制台。",
        sources=["frontend/src/App.tsx", "frontend/src/useStudio.ts", "backend/shine/main.py"],
        apis=["GET /api/v1/projects", "POST /api/v1/projects", "GET /api/v1/system/status"],
        evidence=["backend/tests/test_package_entrypoints.py", "frontend/src/test"],
        tags=["工作台", "导航", "项目", "web"],
    ),
    _entry(
        "director-journey",
        "导演模式七阶段",
        "director",
        "IMPLEMENTED",
        "从导入原文、大纲、分镜书、镜头确认、备料、开拍到审片/交付的七阶段旅程。"
        "每一集的状态、阻塞与下一步都由既有台账聚合，单集失败不冻结其它集。",
        sources=[
            "backend/shine/api_streamline.py",
            "backend/shine/api_director.py",
            "backend/shine/director/overview.py",
        ],
        apis=[
            "GET /api/v1/projects/{project_id}/journey",
            "GET /api/v1/projects/{project_id}/director/overview",
            "POST /api/v1/projects/{project_id}/director/production/start",
        ],
        evidence=["backend/tests/test_director_journey_closeout.py", "backend/tests/test_director_overview.py"],
        tags=["导演模式", "七阶段", "分集", "开拍", "审片", "交付"],
    ),
    _entry(
        "novel-import",
        "小说导入与多集流水线",
        "pipeline",
        "IMPLEMENTED",
        "支持导入原文文档（TXT/MD/DOCX 等），按卷/章切分并生成分集方案与分集剧本；"
        "再次上传只成为候选，切换正式底稿必须显式选择。",
        sources=["backend/shine/api_agent.py", "backend/shine/api_pipeline.py", "backend/shine/api_story.py"],
        apis=[
            "POST /api/v1/projects/{project_id}/documents",
            "POST /api/v1/projects/{project_id}/pipeline/sources",
            "POST /api/v1/projects/{project_id}/script-basis",
        ],
        evidence=["backend/tests/test_novel_to_two_episodes_e2e.py", "backend/tests/test_novel_multiepisode_autopilot.py"],
        tags=["小说", "导入", "多集", "原文", "切分"],
    ),
    _entry(
        "literary-four-stages",
        "文学四阶段与五维故事记忆",
        "literary",
        "IMPLEMENTED",
        "文学阶段按既有任务台账推进（大纲、分节、剧本、分镜），"
        "故事记忆与人物/场景连续性来自持久记录，不由模型临时编造。",
        sources=["backend/shine/literary", "backend/shine/materialprep/story.py"],
        apis=["GET /api/v1/projects/{project_id}/story-preparation", "POST /api/v1/projects/{project_id}/story-preparation/outlines"],
        evidence=["backend/tests/test_literary_beats.py", "backend/tests/test_story_continuity.py"],
        tags=["文学", "四阶段", "故事记忆", "连续性", "大纲"],
    ),
    _entry(
        "director-style",
        "导演风格引擎与冻结",
        "director_style",
        "IMPLEMENTED",
        "风格草稿、预览、应用与冻结走既有 StyleVersion；冻结后的风格版本被每一条镜头引用，"
        "AI 管家不能替用户确认或解冻。",
        sources=["backend/shine/api_director_styles.py", "backend/shine/api_freezes.py", "backend/shine/models.py"],
        apis=[
            "PUT /api/v1/projects/{project_id}/style/draft",
            "POST /api/v1/projects/{project_id}/style/freeze",
            "POST /api/v1/projects/{project_id}/director-style/preview",
        ],
        evidence=["backend/tests/test_director_styles.py", "backend/tests/test_style_versions.py"],
        tags=["风格", "冻结", "Style Bible", "摄影语言"],
    ),
    _entry(
        "storyboard-agents",
        "剧本与分镜 Agent",
        "agents",
        "IMPLEMENTED",
        "多工种 Agent 产出类型化、版本化提案；镜头行、提示词与分镜书都落库，"
        "不直接把自由文本送进视频模型。",
        sources=["backend/shine/agents", "backend/shine/director/streamline.py", "backend/shine/api_film.py"],
        apis=["POST /api/v1/projects/{project_id}/director/storyboard/generate", "GET /api/v1/projects/{project_id}/director/storyboard/state"],
        evidence=["backend/tests/test_film_crew.py", "backend/tests/test_storyboard.py"],
        tags=["剧本", "分镜", "Agent", "镜头"],
    ),
    _entry(
        "asset-center",
        "素材中心",
        "assets",
        "IMPLEMENTED",
        "素材检索、候选图、人工确认与主资产绑定；主资产只能由用户确认，"
        "技术衍生品不会自动升级为主资产。",
        sources=["backend/shine/api_asset_center.py", "backend/shine/asset_center", "backend/shine/comfyops/assets.py"],
        apis=[
            "GET /api/v1/projects/{project_id}/assets",
            "GET /api/v1/projects/{project_id}/asset-center/search",
            "POST /api/v1/candidate-assets/{identity}/promote",
        ],
        evidence=["backend/tests/test_asset_center.py", "backend/tests/test_asset_identity.py"],
        tags=["素材", "资产", "主资产", "版权", "检索"],
    ),
    _entry(
        "qwen-image",
        "Qwen-Image 出图能力",
        "image",
        "IMPLEMENTED",
        "文生图、图生图、局部修订、多参考图、换装与透明底六种能力按项目资源独立核验；"
        "一种能力缺模型不会阻断其它已核验能力。",
        sources=["backend/shine/comfyops/setup_summary.py", "backend/shine/comfyops/capability.py", "backend/shine/materialprep"],
        apis=["POST /api/v1/projects/{project_id}/candidate-assets/generate", "GET /api/v1/setup/summary"],
        evidence=["backend/tests/test_image_managed_renderer.py", "backend/tests/test_material_image_jobs.py"],
        tags=["出图", "Qwen", "t2i", "i2i", "inpaint", "多参考", "RGBA"],
    ),
    _entry(
        "minimax-h3",
        "MiniMax-H3 视频生成（Ref2VA）",
        "h3",
        "PARTIAL",
        "代码只允许 Reference-to-Video-and-Audio（Ref2VA）一条真实视频链路；"
        "T2VA 与首尾帧模式由唯一模式策略拒绝。云端测试环境没有真实 GPU/H3，"
        "因此真实出片属于需要真机验收的能力。",
        sources=["backend/shine/h3/mode_policy.py", "backend/shine/h3/compiler.py", "backend/shine/comfy/graph_compiler.py"],
        apis=["POST /api/v1/projects/{project_id}/director/production/start"],
        evidence=["backend/tests/test_ref2va_only.py", "backend/tests/test_h3_compiler.py"],
        verification="HARDWARE_REQUIRED",
        tags=["H3", "Ref2VA", "视频", "MiniMax", "真机"],
        not_supported="FL2VA、T2VA、H3-Regenerate-2K、H3-Context-IR 不是当前 Shine 本机链路；不能静默切换。",
    ),
    _entry(
        "variants-quantization",
        "模型变体与量化版本",
        "variants",
        "IMPLEMENTED",
        "变体、量化与加速配方来自受审计的变体清单和编译器配方表；"
        "候选或未验证配方会被具名阻断，不会静默替换成另一套量化。",
        sources=["backend/shine/comfyops/variants.py", "backend/shine/strategy.py", "backend/shine/comfy/graph_compiler.py"],
        apis=["GET /api/v1/projects/{project_id}/director/strategy-options"],
        evidence=["backend/tests/test_variant_binding_and_gate.py", "backend/tests/test_variant_manifest.py"],
        tags=["变体", "量化", "INT8", "strategy", "加速"],
    ),
    _entry(
        "workflow-assembly",
        "动态工作流装配与合同",
        "workflow",
        "IMPLEMENTED",
        "工作流按策略 + 每条镜头的参考清单在提交前编译，缺参数映射/媒体转运会具名阻断；"
        "内置模板受 Git 管理，用户级调整只能创建副本。",
        sources=["backend/shine/comfy/workflow.py", "backend/shine/comfy/graph_compiler.py", "backend/shine/api_comfy.py"],
        apis=["GET /api/v1/workflow-profiles", "POST /api/v1/workflow-profiles", "POST /api/v1/workflow-capabilities/{identity}/validate"],
        evidence=["backend/tests/test_graph_compiler.py", "backend/tests/test_setup_workflow_profiles.py"],
        tags=["工作流", "合同", "节点", "模板", "副本"],
    ),
    _entry(
        "second-pass-upscale",
        "二次采样与 RelayVSR X2",
        "strategy",
        "IMPLEMENTED",
        "生产策略合同可选真实二次采样与 RelayVSR X2 超分；"
        "可用性来自配方表与 RelayVSR 能力记录，未验证配方会被具名阻断。",
        sources=["backend/shine/strategy.py", "backend/shine/comfy/graph_compiler.py"],
        apis=["GET /api/v1/projects/{project_id}/director/strategy-options"],
        evidence=["backend/tests/test_relay_upscale_stage.py", "backend/tests/test_graph_compiler.py"],
        verification="HARDWARE_REQUIRED",
        tags=["二次采样", "RelayVSR", "超分", "X2", "调参"],
    ),
    _entry(
        "comfy-scheduler",
        "ComfyUI 统一调度与 GPU 租约",
        "comfyops",
        "IMPLEMENTED",
        "受管渲染器、串行模型阶段、租约转移与释放共用一个调度；"
        "提交结果不确定的任务必须核对，不能自动重发。",
        sources=["backend/shine/comfyops", "backend/shine/production/runner.py", "backend/shine/comfyops/renderer_release.py"],
        apis=["GET /api/v1/setup/managed-processes", "GET /api/v1/system/status"],
        evidence=["backend/tests/test_managed_process_release.py", "backend/tests/test_gpu_release_confirmation.py"],
        tags=["ComfyUI", "调度", "GPU", "租约", "显存", "UNKNOWN"],
    ),
    _entry(
        "model-download-verify",
        "模型下载、校验与自动登记",
        "installation",
        "IMPLEMENTED",
        "固定清单下载、SHA-256 校验、安装回执与复用已存在文件；"
        "未通过校验的文件不会被登记或替换。",
        sources=["backend/shine/comfyops/setup_preparation.py", "backend/shine/comfyops/setup_reconcile.py", "backend/shine/comfyops/installer.py"],
        apis=["POST /api/v1/setup/prepare/execute", "POST /api/v1/projects/{project_id}/support/actions/preview"],
        evidence=["backend/tests/test_weight_verification_receipts.py", "backend/tests/test_existing_installation.py"],
        tags=["下载", "校验", "登记", "回执", "模型"],
    ),
    _entry(
        "llm-routing",
        "LLM 多模型与角色路由",
        "llm",
        "IMPLEMENTED",
        "统一 LLM Gateway 与 ProviderProfile；角色路由显式，密钥不落 SQLite/Git/日志/前端。"
        "AI 管家使用可选 support_agent 角色，未配置时仍可离线诊断。",
        sources=["backend/shine/llm", "backend/shine/api_llm.py", "backend/shine/llm/role_catalog.py"],
        apis=["GET /api/v1/llm/providers", "GET /api/v1/llm/routes", "POST /api/v1/llm/routes"],
        evidence=["backend/tests/test_llm_multi_model_routing.py", "backend/tests/test_llm_gateway.py"],
        tags=["LLM", "模型", "Provider", "路由", "角色", "support_agent"],
    ),
    _entry(
        "production-qc-delivery",
        "生产、质检、返工与成片交付",
        "production",
        "IMPLEMENTED",
        "ProductionRun/Series 是真实制作单元；技术质检与语义审片分离，"
        "硬错误永不自动通过，低置信度需要人工审片；交付需人工验收。",
        sources=["backend/shine/production", "backend/shine/media", "backend/shine/api_delivery.py", "backend/shine/api_library.py"],
        apis=["GET /api/v1/projects/{project_id}/production-runs", "GET /api/v1/projects/{project_id}/deliveries"],
        evidence=["backend/tests/test_delivery_acceptance.py", "backend/tests/test_final_review.py"],
        verification="HARDWARE_REQUIRED",
        tags=["生产", "质检", "返工", "审片", "成片", "交付"],
    ),
    _entry(
        "windows-deployment",
        "Windows 部署与启动器",
        "launcher",
        "PARTIAL",
        "仓库内含独立 Rust+Slint 启动器工程与安装包脚本；启动器离线诊断与工程报告接口仍未实现，"
        "因此不能声称 Windows 实机全流程验收通过。",
        sources=["launcher", "shinemain.py", "scripts/build_test_package.ps1", "ShineLauncher-build-info.json"],
        apis=[],
        evidence=["backend/tests/test_start_shine_launcher.py", "backend/tests/test_migrations_windows.py"],
        verification="NOT_RUN",
        tags=["Windows", "启动器", "安装包", "部署", "离线诊断"],
        not_supported="启动器内与工作台一致的离线诊断/BLOCKER 报告尚未实现（验收 S29 仍 BLOCKED）。",
    ),
    _entry(
        "setup-verdict",
        "统一环境裁决与设置",
        "setup",
        "IMPLEMENTED",
        "环境是否就绪只认 /api/v1/setup/verdict 与能力摘要；"
        "工作台顶部、基础配置与 AI 管家读取同一个答案。",
        sources=["backend/shine/api_setup.py", "backend/shine/comfyops/setup_summary.py"],
        apis=["GET /api/v1/setup/verdict", "GET /api/v1/setup/runtime", "GET /api/v1/setup/summary"],
        evidence=["backend/tests/test_setup_capability_gates.py", "backend/tests/test_setup_one_stop_v41.py"],
        tags=["环境", "裁决", "就绪", "设置", "配置"],
    ),
    _entry(
        "support-agent",
        "AI 管家（本产品自身）",
        "support",
        "IMPLEMENTED",
        "预览→授权→原有执行者→回读裁决的受控链路；代码零修改、无 shell/git，"
        "工程问题只能生成脱敏 MD。",
        sources=["backend/shine/support", "backend/shine/api_support.py", "frontend/src/components/ShineCopilot.tsx"],
        apis=["GET /api/v1/support/capabilities", "POST /api/v1/support/actions/preview", "POST /api/v1/support/actions/{action_id}/approve"],
        evidence=["backend/tests/test_support_agent.py"],
        tags=["AI 管家", "诊断", "授权", "报告", "管家"],
    ),
)


def _content_digest() -> str:
    payload = json.dumps(PROJECT_ENTRIES, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")


def build_commit_from_env() -> str | None:
    """The build's own commit, when the package/bootstrapper injected it.

    ``SHINE_BUILD_COMMIT`` wins; otherwise the packaged ``BUILD_INFO.json`` at
    the package/repo root is read.  A source checkout without either is UNKNOWN,
    never "assume HEAD" (which would make the manifest self-referential).
    """
    value = (os.environ.get("SHINE_BUILD_COMMIT") or "").strip()
    if _COMMIT_RE.fullmatch(value):
        return value
    root = Path(__file__).resolve().parents[3]
    for candidate in (root / "BUILD_INFO.json", root.parent / "BUILD_INFO.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        commit = str(payload.get("source_commit") or "").strip()
        if _COMMIT_RE.fullmatch(commit):
            return commit
    return None


def _manifest(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def index_metadata(
    build_commit: str | None = None, manifest_path: Path | None = None
) -> dict[str, Any]:
    """Version binding between this knowledge and the running build."""
    manifest = _manifest(manifest_path)
    recorded = str(manifest.get("source_commit") or "") or None
    recorded_version = str(manifest.get("knowledge_version") or "") or None
    recorded_digest = str(manifest.get("content_sha256") or "") or None
    observed = build_commit if build_commit is not None else build_commit_from_env()
    digest = _content_digest()

    reasons: list[str] = []
    if not manifest:
        state = "UNKNOWN"
        reasons.append("知识清单文件缺失，无法证明与当前构建一致。")
    elif recorded_version != KNOWLEDGE_VERSION or recorded_digest != digest:
        state = "STALE"
        reasons.append("知识清单内容与运行版本不一致，已按过期资料处理。")
    elif not recorded:
        state = "UNKNOWN"
        reasons.append("知识清单未写入来源提交号（开发检出），无法证明知识版本与当前代码一致。")
    elif not observed:
        state = "UNKNOWN"
        reasons.append("当前构建未注入提交号，无法证明知识版本与当前代码一致。")
    elif observed != recorded:
        state = "STALE"
        reasons.append(f"知识清单与当前构建不一致（清单来自 {recorded[:8]}，当前构建为 {observed[:8]}）。")
    else:
        state = "FRESH"

    return {
        "knowledge_version": KNOWLEDGE_VERSION,
        "source_commit": recorded,
        "build_commit": observed,
        "content_sha256": digest,
        "state": state,
        "fresh": state == "FRESH",
        "reasons": reasons,
        "indexed_entry_count": len(PROJECT_ENTRIES),
        "manifest_path": "backend/shine/support/knowledge_manifest.json",
        "notice": (
            "知识索引与当前构建一致。"
            if state == "FRESH"
            else "；".join(reasons) or "无法证明知识索引与当前构建一致。"
        ),
        "evidence_level": "VERSIONED_PROJECT_DOCS",
    }


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}")


def _tokens(text: str) -> list[str]:
    lowered = (text or "").casefold()
    tokens = _TOKEN_RE.findall(lowered)
    # Chinese has no spaces: also match every 2-character window of a run.
    extra: list[str] = []
    for token in _TOKEN_RE.findall(text or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            extra.extend(token[i : i + 2] for i in range(0, len(token) - 1))
    return list(dict.fromkeys(tokens + extra))[:24]


def _score(entry: dict[str, Any], tokens: list[str]) -> int:
    fields = {
        "id": 4,
        "title": 6,
        "tags": 4,
        "summary": 2,
        "module": 3,
        "sources": 1,
        "apis": 1,
    }
    score = 0
    for token in tokens:
        for field, weight in fields.items():
            value = entry.get(field)
            haystack = " ".join(value) if isinstance(value, list) else str(value or "")
            if token and token in haystack.casefold():
                score += weight
    return score


def search(query: str, *, limit: int = 5, build_commit: str | None = None) -> dict[str, Any]:
    """Search the versioned catalogue; no network, no GPU, no worktree scan."""
    tokens = _tokens(query)
    scored = []
    for entry in PROJECT_ENTRIES:
        score = _score(entry, tokens) if tokens else 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda row: (-row[0], row[1]["id"]))
    metadata = index_metadata(build_commit)
    hits = [
        {
            **entry,
            "score": score,
            "citation": {
                "source_paths": entry["sources"],
                "source_commit": metadata.get("source_commit"),
                "knowledge_version": KNOWLEDGE_VERSION,
                "evidence_level": "VERSIONED_PROJECT_DOCS",
            },
        }
        for score, entry in scored[: max(1, min(limit, 10))]
    ]
    return {
        "query": (query or "")[:200],
        "metadata": metadata,
        "results": hits,
        "answered": bool(hits),
        "unknown_reason": None if hits else "项目知识索引中没有与问题匹配的条目。",
        "notice": metadata["notice"],
    }


def coverage() -> dict[str, Any]:
    """Counts per status for the acceptance report and the UI."""
    counts: dict[str, int] = {status: 0 for status in _STATUS_LABELS}
    for entry in PROJECT_ENTRIES:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "knowledge_version": KNOWLEDGE_VERSION,
        "total": len(PROJECT_ENTRIES),
        "by_status": counts,
        "status_labels": _STATUS_LABELS,
        "entries": [
            {
                "id": entry["id"],
                "title": entry["title"],
                "module": entry["module"],
                "status": entry["status"],
                "verification": entry["verification"],
                "sources": entry["sources"],
            }
            for entry in PROJECT_ENTRIES
        ],
    }


def answer(question: str, *, limit: int = 3, build_commit: str | None = None) -> dict[str, Any]:
    """A knowledge answer with citations and an explicit applicability note."""
    result = search(question, limit=limit, build_commit=build_commit)
    citations = [
        {
            "title": hit["title"],
            "status": hit["status_label"],
            "summary": hit["summary"],
            "sources": hit["sources"],
            "apis": hit["apis"],
            "verification": hit["verification_label"],
            "knowledge_version": hit["knowledge_version"],
        }
        for hit in result["results"]
    ]
    return {
        "intent": "KNOWLEDGE",
        "answered": bool(citations),
        "answer_lines": [
            f"【{row['status']}】{row['title']}：{row['summary']}" for row in citations
        ],
        "citations": citations,
        "metadata": result["metadata"],
        "unknown_reason": result["unknown_reason"],
        "notice": result["notice"],
        "next_step": (
            "如需操作，可以让管家先给出计划，再由你确认；需要真机验证的能力不会因为文档存在就被宣称可用。"
            if citations
            else "这个问题在当前版本的项目知识中没有依据；请换一种说法或生成工程报告。"
        ),
    }

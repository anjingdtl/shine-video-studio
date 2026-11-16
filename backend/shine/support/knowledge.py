"""Versioned local knowledge and optional official-source lookup.

Local entries are bound to a build and describe this product's own contracts.
They are evidence for explanations, never executable repair instructions.

Web retrieval is opt-in (default off) and has two layers:

* curated official references, and
* a GitHub Issues search limited to a small allow-list of upstream repositories.

Only a redacted, generic query leaves the machine: an error code, a topic word
from a fixed vocabulary and sanitised local version numbers.  Project text,
paths, prompts, models and keys are never part of it.  Results are references
with a source, an applicability hint and an uncertainty note; an issue body that
looks like an instruction is withheld instead of shown.  No embedding model and
no GPU are used, so a diagnosis never competes with a render for VRAM.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from shine.support.privacy import redact_text

KNOWLEDGE_VERSION = "shine-support-kb-2026.11.16"

OFFICIAL_HOSTS: tuple[str, ...] = (
    "docs.comfy.org",
    "github.com",
    "api.github.com",
    "pytorch.org",
    "docs.nvidia.com",
    "developer.nvidia.com",
)

#: Upstream repositories whose public issues may be searched.  Nothing else is
#: queried, and the repository list is not user-controlled.
GITHUB_ISSUE_REPOS: tuple[str, ...] = (
    "comfyanonymous/ComfyUI",
    "Comfy-Org/ComfyUI-Manager",
    "pytorch/pytorch",
)

#: Fixed vocabulary.  A user question never contributes free-form words to the
#: outbound query beyond these topics and a sanitised error code.
_TOPIC_WORDS: dict[str, str] = {
    "nodes": "node custom node",
    "cuda": "cuda driver",
    "pytorch": "pytorch torch cuda",
    "comfyui": "comfyui",
    "gpu": "out of memory vram",
}

_WEB_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_WEB_CACHE_TTL_SECONDS = 600.0
_WEB_CACHE_MAX = 32
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z._-]{0,15}$")
_ERROR_CODE_RE = re.compile(r"[^A-Z0-9_]+")

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


def _safe_error_code(error_code: str | None) -> str:
    code = _ERROR_CODE_RE.sub("_", (error_code or "").upper()).strip("_")
    return code[:40]


def _safe_version(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _VERSION_RE.match(text) else None


def _outbound_query(error_code: str | None, topic: str, versions: dict[str, str] | None) -> str:
    """Only allow-listed words leave the machine."""
    parts: list[str] = []
    code = _safe_error_code(error_code)
    if code:
        parts.append(code)
    parts.append(_TOPIC_WORDS.get(topic, _TOPIC_WORDS["comfyui"]))
    for value in (versions or {}).values():
        safe = _safe_version(value)
        if safe:
            parts.append(safe)
    return " ".join(dict.fromkeys(parts))[:160]


def _github_issue_url_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname != "github.com":
        return False
    path = parsed.path.strip("/").split("/")
    if len(path) < 4 or path[2] != "issues":
        return False
    repo = f"{path[0]}/{path[1]}"
    return repo in GITHUB_ISSUE_REPOS


def _applicability(title: str, excerpt: str, local_version: str | None) -> dict[str, str]:
    if not local_version:
        return {"state": "UNKNOWN", "reason": "本机版本未观测到，无法判断资料是否适用。"}
    found = re.findall(r"\b(?:v)?([0-9]+(?:\.[0-9]+){1,3})\b", f"{title} {excerpt}")
    if not found:
        return {"state": "UNKNOWN", "reason": "资料没有写明版本，仅可作为参考。"}
    if any(version == local_version or local_version.startswith(version + ".") for version in found):
        return {"state": "LIKELY_APPLICABLE", "reason": f"资料提到与本机一致的版本 {local_version}。"}
    return {
        "state": "POSSIBLY_NOT_APPLICABLE",
        "reason": f"资料提到 {found[0]}，本机观测到 {local_version}；请以上游说明为准。",
    }


def _search_github_issues(
    query: str,
    *,
    error_code: str | None,
    local_version: str | None,
    timeout_s: float,
) -> list[dict[str, Any]]:
    from shine.support.tools import forbidden_intent

    repo_query = " ".join(f"repo:{repo}" for repo in GITHUB_ISSUE_REPOS)
    url = (
        "https://api.github.com/search/issues?"
        + urllib.parse.urlencode(
            {"q": f"{query} is:issue {repo_query}", "per_page": "3", "sort": "updated"}
        )
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Shine-Copilot/1.0 (+local diagnosis)",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - allow-listed host
        payload = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
    results: list[dict[str, Any]] = []
    for item in (payload.get("items") or [])[:3]:
        if not isinstance(item, dict):
            continue
        url_value = str(item.get("html_url") or "")
        if not _github_issue_url_allowed(url_value):
            continue
        title = redact_text(item.get("title"), limit=160)
        body = redact_text(item.get("body"), limit=240)
        instruction_like = bool(forbidden_intent(body)) or bool(forbidden_intent(title))
        results.append(
            {
                "title": title,
                "url": url_value,
                "source": "GitHub Issues",
                "kind": "ISSUE",
                "state": item.get("state"),
                "updated_at": item.get("updated_at"),
                "excerpt": "" if instruction_like else body,
                "instruction_like": instruction_like,
                "applicability": _applicability(title, body, local_version),
                "availability": "REFERENCE_ONLY",
                "evidence_level": "EXTERNAL_REFERENCE",
            }
        )
    return results


def search_official_web(
    query: str,
    *,
    enabled: bool = False,
    error_code: str | None = None,
    versions: dict[str, str] | None = None,
    timeout_s: float = 4.0,
) -> dict[str, Any]:
    """Official-source references, disabled unless the person opted in.

    ``query`` selects a *topic*; it is never sent verbatim.  When the network is
    unavailable the curated pointers are still returned and the answer falls
    back to local diagnosis.
    """
    del query  # free-form text never leaves the machine
    topic = _topic_for(error_code or "")
    safe_query = _outbound_query(error_code, topic, versions)
    if not enabled:
        return {
            "enabled": False,
            "query": safe_query,
            "results": [],
            "offline": False,
            "notice": "联网检索默认关闭；打开后只查询官方文档与上游仓库。",
        }
    cache_key = f"{safe_query}|{sorted((versions or {}).items())}"
    cached = _WEB_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _WEB_CACHE_TTL_SECONDS:
        return cached[1]

    local_version = _safe_version((versions or {}).get("comfyui"))
    results: list[dict[str, Any]] = []
    offline = False
    try:
        results.extend(
            _search_github_issues(
                safe_query,
                error_code=error_code,
                local_version=local_version,
                timeout_s=timeout_s,
            )
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        offline = True

    # Curated pointers always travel with the answer; they cost no request.
    for reference in OFFICIAL_REFERENCES.get(topic, OFFICIAL_REFERENCES["comfyui"]):
        if not _allowed(reference["url"]):
            continue
        results.append(
            {
                **reference,
                "kind": "DOC",
                "availability": "REFERENCE_ONLY",
                "evidence_level": "EXTERNAL_REFERENCE",
                "applicability": {
                    "state": "UNKNOWN",
                    "reason": "官方文档入口，需按本机版本自行核对。",
                },
            }
        )
    payload = {
        "enabled": True,
        "query": safe_query,
        "results": results[:6],
        "offline": offline,
        "local_versions": {key: _safe_version(value) for key, value in (versions or {}).items()},
        "notice": "外部资料只作为参考证据，不能作为本机修复指令；断网时自动回到离线诊断。",
    }
    if len(_WEB_CACHE) >= _WEB_CACHE_MAX:
        _WEB_CACHE.pop(next(iter(_WEB_CACHE)))
    _WEB_CACHE[cache_key] = (now, payload)
    return payload


def local_version_facts(db, settings) -> dict[str, str]:
    """Observed local versions, from existing receipts only (never probed here)."""
    versions: dict[str, str] = {}
    try:
        from shine.comfyops.models import ComfyInstance
        from shine.comfyops.setup_summary import resolve_installation

        selected = resolve_installation(db, settings, discovered=[]).get("selected") or {}
        instance_id = selected.get("instance_id")
        if instance_id:
            instance = db.get(ComfyInstance, str(instance_id))
            payload = getattr(instance, "payload", {}) or {}
            service = payload.get("service_identity") or {}
            value = service.get("version") or payload.get("actual_version")
            safe = _safe_version(value)
            if safe:
                versions["comfyui"] = safe
    except Exception:  # noqa: BLE001 - an unobserved version stays unobserved
        pass
    return versions


def dump_local_index() -> str:
    return json.dumps(
        {"version": KNOWLEDGE_VERSION, "entries": LOCAL_ENTRIES},
        ensure_ascii=False,
        indent=2,
    )

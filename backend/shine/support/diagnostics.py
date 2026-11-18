"""Deterministic, evidence-first diagnosis.

Rules run before any model: the authoritative setup mode, runtime report, named
error code and task/attempt facts decide the category.  The LLM may later explain
or arrange a plan, but it cannot change this classification, promote a state to
READY, or invent an action that is not in the tool registry.
"""
from __future__ import annotations

from typing import Any

from shine.support.tools import ACTION_SPECS, forbidden_intent

_CATEGORY_LABELS = {
    "CONFIG": "本机配置尚未就绪",
    "ENVIRONMENT": "本机运行环境异常",
    "EXTERNAL": "外部服务或生成任务异常",
    "PROJECT_BUG": "疑似工程缺陷",
    "FEATURE_REQUEST": "当前版本没有这个能力",
    "UNDETERMINED": "暂时无法确定原因",
    "OK": "本机状态正常",
}

_CODE_RULES: dict[str, dict[str, str]] = {
    "WORKER_OFFLINE": {
        "category": "ENVIRONMENT",
        "symptom": "后台 Worker 未运行，制作任务不会继续推进。",
        # The same sentence the verdict shows, and the same executor: the work
        # stack already starts the Worker. Sending the person to the launcher
        # was a second instruction for one fact.
        "next_step": "在页面左下角「工作栈」点「启动 Worker」即可恢复；后台恢复后原任务会继续，不用重开启动器。",
    },
    "REAL_RUNTIME_NOT_ACTIVATED": {
        "category": "CONFIG",
        "symptom": "真实制作环境还没有完成配置。",
        "next_step": "先在基础配置里检查并补齐环境。",
    },
    "ACTIVE_RUNTIME_CONFIGURATION_CHANGED": {
        "category": "CONFIG",
        "symptom": "本机运行配置的固定文件或服务记录发生了变化，需要重新核对并激活运行合同。",
        "next_step": "在基础配置点击「核对运行合同」（或下方授权动作），由整备 Worker 重新生成并验证配置。",
    },
    "SERVICE_VALIDATION_REQUIRED": {
        "category": "ENVIRONMENT",
        "symptom": "本机服务或工作流发生了变化，需要重新验证。",
        "next_step": "重新核验服务与工作流合同，验证通过后再开始真实制作。",
    },
    "LEASE_RECONCILE_REQUIRED": {
        "category": "EXTERNAL",
        "symptom": "有生成任务的结果不确定，系统不会自动重试。",
        "next_step": "先按原流程核对该任务是否已经执行，禁止重复提交。",
    },
    "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK": {
        "category": "EXTERNAL",
        "symptom": "本机还有正在执行的工作或待核对的资源占用。",
        "next_step": "提交「核对运行合同」后，后台会等待资源可用再继续。可在「工作栈」查看等待项。",
    },
    "STOP_OWNED_INSTANCE_BEFORE_VALIDATION": {
        "category": "CONFIG",
        "symptom": "本机渲染器仍在运行，不能同时做工作流验证。",
        "next_step": "等渲染器空闲后再验证；不会强停正在服务的实例。",
    },
    "GENERATE_RUNTIME_CONFIGURATION_FIRST": {
        "category": "CONFIG",
        "symptom": "还没有为本机生成运行配置。",
        "next_step": "先在基础配置完成一次整备，再验证工作流。",
    },
    "HOST_NOT_QUALIFIED": {
        "category": "ENVIRONMENT",
        "symptom": "这台电脑没有通过所选 H3 方案的硬件准入。",
        "next_step": "可以选择匹配本机显存的方案；图片或审片能力不受影响。",
    },
    "RESOURCE_SNAPSHOT_CHANGED_OR_INELIGIBLE": {
        "category": "CONFIG",
        "symptom": "本机文件或模型清单已经变化，需要重新检测。",
        "next_step": "重新核验已存在的本机文件，再决定是否下载。",
    },
    "EXISTING_H3_MODEL_FILES_INCOMPLETE": {
        "category": "CONFIG",
        "symptom": "本机 H3 文件没有全部通过固定清单核验。",
        "next_step": "先核验差异，缺失文件按固定清单补齐；不会替换未通过校验的文件。",
    },
    "EXISTING_MODEL_HASH_REJECTIONS_PRESENT": {
        "category": "ENVIRONMENT",
        "symptom": "有模型文件未通过完整性校验。",
        "next_step": "保留原文件，由用户确认后按固定清单重新准备，不能伪造登记。",
    },
    "LLM_NOT_CONFIGURED": {
        "category": "EXTERNAL",
        "symptom": "AI 管家的文本模型还没有配置。",
        "next_step": "可以继续使用本地规则诊断；需要智能解释时再到高级设置选择模型。",
    },
    "LLM_AUTH": {
        "category": "EXTERNAL",
        "symptom": "文本模型认证失败（常见为密钥无效或欠费）。",
        "next_step": "检查模型服务账户与密钥；这不会阻断本机出图出片。",
    },
    "LLM_TIMEOUT": {
        "category": "EXTERNAL",
        "symptom": "文本模型响应超时。",
        "next_step": "本地诊断结果仍然有效；稍后可重试智能解释。",
    },
    "CUDA_OUT_OF_MEMORY": {
        "category": "ENVIRONMENT",
        "symptom": "显卡显存不足，生成在采样阶段失败。",
        "next_step": "等其它任务释放显存，或使用与显存匹配的 H3 变体。",
    },
    "OOM": {
        "category": "ENVIRONMENT",
        "symptom": "生成过程中显存不足。",
        "next_step": "确认没有其它任务占用显卡后再重试；不会强杀正在运行的任务。",
    },
    "NODE_MISSING": {
        "category": "CONFIG",
        "symptom": "ComfyUI 缺少工作流需要的节点。",
        "next_step": "按固定节点清单重新核验安装；不会修改节点源码。",
    },
    "STRATEGY_RENDERER_OBJECT_INFO_UNAVAILABLE": {
        "category": "ENVIRONMENT",
        "symptom": "提交前的渲染器预检连不上 ComfyUI（渲染器未启动或暂时不可达），不是缺少节点。",
        "next_step": "先确认受管渲染器可正常启动（基础配置或工作栈）；节点是否缺失以真实 /object_info 核验结果为准。",
    },
    "NODE_PACK_MISSING": {
        "category": "CONFIG",
        "symptom": "ComfyUI 缺少工作流需要的节点包。",
        "next_step": "按固定节点清单重新核验安装；不会修改节点源码。",
    },
    "WORKFLOW_PROFILE_MISMATCH": {
        "category": "CONFIG",
        "symptom": "当前工作流与本机模型或节点不匹配。",
        "next_step": "选择已验证的工作流，或先验证当前合同；不会静默换成别的视频模型。",
    },
    "DOWNLOAD_FAILED": {
        "category": "EXTERNAL",
        "symptom": "固定资源下载没有完成。",
        "next_step": "检查网络后重试同一下载任务；已完成的文件不会重复下载。",
    },
}

_KEYWORD_RULES: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (
        ("out of memory", "cuda", "显存", "oom"),
        _CODE_RULES["OOM"],
    ),
    (
        ("node", "object_info", "节点"),
        _CODE_RULES["NODE_MISSING"],
    ),
    (
        ("401", "429", "unauthorized", "quota", "欠费", "密钥"),
        _CODE_RULES["LLM_AUTH"],
    ),
    (
        ("timeout", "timed out", "超时"),
        _CODE_RULES["LLM_TIMEOUT"],
    ),
)


def _first_rule_code(snapshot: dict[str, Any]) -> str | None:
    task = snapshot.get("task") or {}
    if task:
        attempt = task.get("attempt") or {}
        for code in (attempt.get("failure_code"), task.get("failure_code"), snapshot["page_context"].get("error_code")):
            if code and code in _CODE_RULES:
                return str(code)
    code = (snapshot.get("page_context") or {}).get("error_code")
    if code in _CODE_RULES:
        return str(code)
    verdict_code = ((snapshot.get("setup") or {}).get("verdict") or {}).get("code")
    if verdict_code in _CODE_RULES:
        return str(verdict_code)
    return None


def _keyword_rule(text: str) -> dict[str, str] | None:
    lowered = (text or "").casefold()
    # "object_info" inside a preflight-unreachable failure code must not be
    # read as "the renderer answered and is missing nodes": that verdict needs
    # a real /object_info inventory.
    if "object_info_unavailable" in lowered:
        return _CODE_RULES["STRATEGY_RENDERER_OBJECT_INFO_UNAVAILABLE"]
    for words, rule in _KEYWORD_RULES:
        if any(word in lowered for word in words):
            return rule
    return None


def _facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    verdict = (snapshot.get("setup") or {}).get("verdict") or {}
    runtime = snapshot.get("runtime") or {}
    task = snapshot.get("task") or {}
    facts: list[dict[str, Any]] = [
        {
            "key": "生产裁决",
            "value": f"{verdict.get('label') or verdict.get('mode') or '未知'}"
            f"（{verdict.get('code') or '无错误码'}）",
            "source": "/api/v1/setup/verdict",
        },
        {
            "key": "运行配置",
            "value": f"{runtime.get('status') or '未知'}，服务核验"
            f"{'通过' if runtime.get('services_verified') else '未通过'}",
            "source": "/api/v1/setup/runtime",
        },
        {
            "key": "后台 Worker",
            "value": f"{len(runtime.get('workers') or [])} 个实例记录",
            "source": "worker_instances",
        },
    ]
    capabilities = ((snapshot.get("setup") or {}).get("summary") or {}).get("capabilities") or {}
    missing = [
        f"{row.get('label')}（缺 {row.get('missing_count')} 个文件"
        + ("，可选增强" if row.get("optional") else "")
        + "）"
        for row in capabilities.values()
        if isinstance(row, dict) and row.get("missing_count")
    ]
    if missing:
        facts.append({
            "key": "缺失资源",
            # Optional review capabilities never block a real shoot; saying so
            # here keeps the evidence list from reading as "you must install
            # 9.9 GiB before you can start".
            "value": "、".join(missing[:5]) + "；标了「可选增强」的缺件不影响出片。",
            "source": "/api/v1/setup/summary",
        })
    preparation = ((snapshot.get("setup") or {}).get("summary") or {}).get("preparation") or {}
    if isinstance(preparation, dict) and preparation.get("status") in {
        "FAILED",
        "RUNNING",
        "QUEUED",
        "DONE_WITH_WARNINGS",
    }:
        prep_parts = [str(preparation.get("status"))]
        if preparation.get("current"):
            prep_parts.append(f"步骤 {preparation.get('current')}")
        if preparation.get("error"):
            prep_parts.append(f"错误码 {preparation.get('error')}")
        elif preparation.get("message"):
            prep_parts.append(str(preparation.get("message"))[:120])
        facts.append(
            {
                "key": "整备任务",
                "value": " / ".join(prep_parts),
                "source": "/api/v1/setup/summary",
            }
        )
    idle_blockers = (snapshot.get("setup") or {}).get("idle_blockers") or []
    blocker_labels = [
        str(row.get("label") or row.get("id"))
        for row in idle_blockers[:5]
        if isinstance(row, dict) and (row.get("label") or row.get("id"))
    ]
    if blocker_labels:
        facts.append(
            {
                "key": "配置阻塞项",
                "value": "；".join(blocker_labels),
                "source": "idle_configuration_blockers",
            }
        )
    if task:
        attempt = task.get("attempt") or {}
        facts.append(
            {
                "key": "任务状态",
                "value": f"{task.get('status')} / 尝试 {attempt.get('status') or '无'}"
                f" / 进度 {attempt.get('progress', 0)}%",
                "source": "jobs / job_attempts",
            }
        )
        if task.get("failure_code") or attempt.get("failure_code"):
            facts.append(
                {
                    "key": "失败码",
                    "value": str(task.get("failure_code") or attempt.get("failure_code")),
                    "source": "jobs / job_attempts",
                }
            )
    return facts


def _repair_action(snapshot: dict[str, Any]) -> dict[str, Any]:
    capabilities = ((snapshot.get("setup") or {}).get("summary") or {}).get("capabilities") or {}
    has_missing = False
    wanted = [
        key
        for key, row in capabilities.items()
        if isinstance(row, dict) and row.get("missing_count") and not row.get("optional")
    ]
    if wanted:
        has_missing = True
    else:
        wanted = [
            key
            for key, row in capabilities.items()
            if isinstance(row, dict)
            and not row.get("optional")
            and ("state" in row or "facts" in row)
            and row.get("state") not in {"READY", "VERIFIED_RENDER"}
            and not (row.get("facts") or {}).get("PRODUCTION_READY")
            and not (row.get("facts") or {}).get("RUNTIME_SERVICES_VERIFIED")
        ]
    if not wanted:
        wanted = ["h3", "image"]
    params: dict[str, Any] = {"capabilities": wanted, "verify_runtime": True}
    project_id = (snapshot.get("page_context") or {}).get("project_id")
    if isinstance(project_id, str) and project_id.strip():
        params["project_id"] = project_id.strip()
    spec = ACTION_SPECS["setup_repair"]
    reason = (
        "补齐固定清单中的缺失文件；由统一安装器的持久任务执行，可断点续跑。"
        if has_missing
        else "重新生成受管运行配置并核验本机工作流合同；由统一整备 Worker 持久执行，已完成步骤不重跑。"
    )
    return {
        "kind": spec.kind,
        "label": spec.label,
        "reason": reason,
        "requires_authorization": True,
        "mutates": True,
        "params": params,
    }


def _apply_setup_diagnosis_detail(
    base: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    code: str | None,
) -> None:
    preparation = ((snapshot.get("setup") or {}).get("summary") or {}).get("preparation") or {}
    raw_blockers = ((snapshot.get("setup") or {}).get("idle_blockers"))
    blockers_unreadable = raw_blockers is None
    idle_blockers = [row for row in (raw_blockers or []) if isinstance(row, dict)]
    prep_status = preparation.get("status") if isinstance(preparation, dict) else None
    prep_failed = prep_status == "FAILED"
    prep_error = str(preparation.get("error") or "") if prep_failed else ""
    # Confirmed facts come from authoritative reads only; the blocker ledger is
    # an observation, not a code-level root cause, and an unreadable ledger is
    # reported as unreadable - never as "blockers cleared".
    confirmed_lines: list[str] = []
    observed_lines: list[str] = []

    if code == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED":
        confirmed_lines.append(
            "- 已确认运行配置状态为 `INVALID_REQUIRES_REVIEW`（`ACTIVE_RUNTIME_CONFIGURATION_CHANGED`）："
            "受管运行配方或固定文件指纹与上次激活记录不一致，需重新生成受管配置并核对运行合同。"
        )
    if prep_failed:
        prep_step = str(preparation.get("current") or "未知步骤")
        prep_msg = str(preparation.get("message") or prep_error or "未知错误")[:240]
        confirmed_lines.append(
            f"- 已确认最近一次整备任务在步骤 `{prep_step}` 停下（错误码 `{prep_error or '无'}`）：{prep_msg}"
        )
        if prep_error == "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK":
            if blockers_unreadable:
                base["symptom"] = (
                    f"{base['symptom']} 最近一次重配曾因在途任务或未核对记录停在"
                    "「生成受管运行配置」；当前阻塞台账无法读取，不能断定已清空。"
                )
                base["next_step"] = "先恢复本机台账读取（重启工作台后重试诊断），再继续「核对运行合同」。"
            elif idle_blockers:
                labels = "；".join(
                    str(row.get("label") or row.get("id")) for row in idle_blockers[:3]
                )
                base["symptom"] = f"{base['symptom']} 最近一次重配在「生成受管运行配置」被阻塞：{labels}。"
                base["next_step"] = "继续「核对运行合同」后，后台会等待上述工作收尾。"
            else:
                base["symptom"] = (
                    f"{base['symptom']} 最近一次重配曾因在途任务或未核对记录停在"
                    "「生成受管运行配置」，当前阻塞台账为空。"
                )
                base["next_step"] = (
                    "当前已无在途任务或租约阻塞，可直接点击「核对运行合同」（或授权下方动作）从断点继续完成配置与验证。"
                )
    if blockers_unreadable:
        observed_lines.append("- 阻塞台账：无法读取（本次快照未能核实，不代表已清空）")
    else:
        for row in idle_blockers[:5]:
            observed_lines.append(
                f"- 已观测阻塞台账项（`{row.get('kind')}`）：{row.get('label') or row.get('id')}"
            )
    lines = [*confirmed_lines, *observed_lines]
    if lines:
        base["root_cause"] = "\n".join(lines)
        base["root_cause_confirmed"] = bool(confirmed_lines) and not blockers_unreadable

    if prep_status in {"QUEUED", "RUNNING"}:
        base["outcome"] = "WAIT"
        base["next_step"] = str(preparation.get("message") or "整备已交给后台，打开基础配置即可查看进度。")
        return
    base["recommended_actions"] = [_repair_action(snapshot)]


def diagnose_snapshot(snapshot: dict[str, Any], *, question: str = "", db=None) -> dict[str, Any]:
    """Classify one snapshot.  No model is called here."""
    page = snapshot.get("page_context") or {}
    verdict = (snapshot.get("setup") or {}).get("verdict") or {}
    runtime = snapshot.get("runtime") or {}
    task = snapshot.get("task") or {}
    mode = str(verdict.get("mode") or "UNKNOWN")
    code = str(verdict.get("code") or "") or None
    facts = _facts(snapshot)
    evidence_id = snapshot.get("evidence_id")
    question = question or ""
    refused = forbidden_intent(question)
    text = " ".join(
        [
            question,
            page.get("error_code") or "",
            code or "",
            (task.get("failure_code") or ""),
            ((task.get("attempt") or {}).get("failure_code") or ""),
        ]
    )

    base: dict[str, Any] = {
        "evidence_id": evidence_id,
        "category": "UNDETERMINED",
        "category_label": _CATEGORY_LABELS["UNDETERMINED"],
        "severity": "MEDIUM",
        "symptom": "暂时没有足够证据判断具体原因。",
        "facts": facts,
        "hypotheses": [],
        "root_cause": None,
        "recommended_actions": [],
        "readiness_reference": {
            "source": "/api/v1/setup/verdict",
            "mode": mode,
            "label": verdict.get("label"),
            "code": code,
        },
        "outcome": "UNDETERMINED",
        "next_step": "请补充发生问题的页面或任务编号；也可以先生成报告交给开发者。",
        "confidence": "UNKNOWN",
        "policy_refusal": None,
        "report_kind": "BLOCKER",
        "llm": {"used": False, "status": "NOT_REQUESTED"},
    }

    if refused:
        base.update(
            {
                "category": "UNDETERMINED",
                "category_label": "已拒绝越权操作",
                "symptom": "请求中包含 AI 管家无权执行的操作。",
                "outcome": "REPORT_RECOMMENDED",
                "report_kind": "REQUIREMENT",
                "severity": "LOW",
                "next_step": "AI 管家不会执行命令、修改源码或绕过生产门禁；可以生成报告交给开发者处理。",
                "confidence": "HIGH",
                "policy_refusal": {"blocked": refused, "executed": False},
            }
        )
        return base

    # An unknown external outcome outranks every other explanation: nothing may
    # be resubmitted before the original reconcile path decides.
    attempt = task.get("attempt") or {}
    reconciliation = str(attempt.get("reconciliation_state") or "").upper()
    if mode == "RECONCILE_REQUIRED" or reconciliation in {"UNKNOWN", "REQUIRED", "RECONCILE_REQUIRED"}:
        base.update(
            {
                "category": "EXTERNAL",
                "category_label": "有任务结果不确定",
                "severity": "HIGH",
                "symptom": "有生成任务的外部结果未知，系统不会自动重试。",
                "outcome": "WAIT_FOR_RECONCILE",
                "next_step": "先按原流程核对远端是否已经执行；确认前禁止重新提交任务。",
                "confidence": "HIGH",
                "report_kind": "BLOCKER",
            }
        )
        return base

    if mode == "BLOCKED":
        rule = _CODE_RULES.get(code or "", {})
        base.update(
            {
                "category": "ENVIRONMENT",
                "category_label": _CATEGORY_LABELS["ENVIRONMENT"],
                "severity": "HIGH",
                "symptom": rule.get("symptom") or verdict.get("detail") or "真实制作环境被阻断。",
                "outcome": "CONFIG_ACTION_AVAILABLE",
                "next_step": rule.get("next_step") or "先重新核验本机环境，按权威裁决提示处理。",
                "confidence": "HIGH",
            }
        )
        _apply_setup_diagnosis_detail(base, snapshot, code=code)
        return base

    if mode in {"SETUP_REQUIRED", "VALIDATION_REQUIRED"}:
        rule = _CODE_RULES.get(code or "", {})
        base.update(
            {
                "category": "CONFIG" if mode == "SETUP_REQUIRED" else "ENVIRONMENT",
                "category_label": _CATEGORY_LABELS["CONFIG" if mode == "SETUP_REQUIRED" else "ENVIRONMENT"],
                "severity": "MEDIUM",
                "symptom": rule.get("symptom") or verdict.get("detail") or "真实制作尚未完成配置或验证。",
                "outcome": "CONFIG_ACTION_AVAILABLE",
                "next_step": rule.get("next_step") or "在基础配置里按提示补齐环境。",
                "confidence": "HIGH",
            }
        )
        _apply_setup_diagnosis_detail(base, snapshot, code=code)
        return base

    task_status = str(task.get("status") or "").upper()
    if task_status in {"QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING"}:
        base.update(
            {
                "category": "OK",
                "category_label": "任务正在正常推进",
                "severity": "INFO",
                "symptom": f"任务当前状态为 {task_status}，还没有失败。",
                "outcome": "WAIT",
                "next_step": "在原页面查看进度；关闭或切换页面不会中断后台任务。",
                "confidence": "HIGH",
            }
        )
        return base

    rule_code = _first_rule_code(snapshot)
    rule = _CODE_RULES.get(rule_code) if rule_code else _keyword_rule(text)
    if rule:
        base.update(
            {
                "category": rule["category"],
                "category_label": _CATEGORY_LABELS[rule["category"]],
                "severity": "HIGH" if rule["category"] in {"ENVIRONMENT", "EXTERNAL"} else "MEDIUM",
                "symptom": rule["symptom"],
                "next_step": rule["next_step"],
                "outcome": "CONFIG_ACTION_AVAILABLE"
                if rule["category"] == "CONFIG"
                else "REPORT_RECOMMENDED",
                "confidence": "MEDIUM",
                "report_kind": "BLOCKER" if rule["category"] == "ENVIRONMENT" else "BUG",
            }
        )
        if rule["category"] == "CONFIG":
            action = _repair_action(snapshot, db=db)
            if action:
                base["recommended_actions"] = [action]
        return base

    if task_status in {"FAILED", "CANCELLED"}:
        base.update(
            {
                "category": "UNDETERMINED",
                "severity": "HIGH",
                "symptom": "任务已经失败，但现有证据不足以确定根因。",
                "outcome": "REPORT_RECOMMENDED",
                "next_step": "保留现场并生成 BLOCKER 报告；不要用任意命令赌修复。",
                "confidence": "LOW",
                "report_kind": "BLOCKER",
            }
        )
        return base

    worker_offline = not (runtime.get("workers") or [])
    if worker_offline and task_status in {"QUEUED", "SUBMITTING"}:
        base.update(
            {
                "category": "ENVIRONMENT",
                "category_label": _CATEGORY_LABELS["ENVIRONMENT"],
                "severity": "HIGH",
                "symptom": "后台 Worker 不在线，排队中的任务不会推进。",
                "outcome": "REPORT_RECOMMENDED",
                "next_step": "用启动器重新打开工作台；这是启动器问题，AI 管家不会代为执行系统命令。",
                "confidence": "MEDIUM",
                "report_kind": "BLOCKER",
            }
        )
        return base

    if mode in {"READY", "RUNNING", "DEMO"}:
        base.update(
            {
                "category": "OK",
                "category_label": _CATEGORY_LABELS["OK"],
                "severity": "INFO",
                "symptom": verdict.get("detail") or "当前没有发现需要处理的环境问题。",
                "outcome": "OK",
                "next_step": "如果某一步仍然失败，请在该页面点击“交给 AI 管家诊断”，带上任务编号。",
                "confidence": "MEDIUM",
            }
        )
        return base

    return base


def suggested_questions(page_id: str) -> list[str]:
    common = ["帮我检查", "为什么失败", "生成问题报告"]
    by_page = {
        "setup": ["帮我检查环境", "模型为什么用不了", "生成问题报告"],
        "advanced": ["帮我检查模型路由", "为什么下载失败", "生成问题报告"],
        "run": ["为什么出片中断", "继续生产前要检查什么", "生成问题报告"],
        "materials": ["素材为什么生成失败", "缺哪个模型", "生成问题报告"],
        "deliver": ["成片为什么失败", "继续生产前要检查什么", "生成问题报告"],
    }
    return by_page.get(page_id, common)

"""Controlled engineering hand-off reports.

The only file the runtime copilot may create is a Markdown report under
``<data_dir>/support-reports``.  Callers cannot choose a path, filename or
extension.  Reports are redacted, fingerprinted for de-duplication and never
accompanied by a patch, a Git command or an Issue.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from shine.models import new_id, utcnow
from shine.support.models import SupportReport
from shine.support.privacy import private_roots_for, redact_payload, redact_text

REPORT_KINDS = ("BLOCKER", "BUG", "REQUIREMENT")
REPORT_DIRNAME = "support-reports"
_FILENAME_RE = re.compile(r"^SUPPORT-\d{8}-\d{6}-(BLOCKER|BUG|REQUIREMENT)-[0-9a-f]{8}\.md$")
_DEDUP_WINDOW = timedelta(hours=24)


def report_root(settings) -> Path:
    base = Path(settings.data_dir).resolve()
    root = base / REPORT_DIRNAME
    if root.is_symlink():
        raise ValueError("report directory must not be a symlink")
    resolved = root.resolve()
    if resolved.parent != base or resolved.name != REPORT_DIRNAME:
        raise ValueError("report directory escaped the controlled data dir")
    return resolved


def _fingerprint(kind: str, diagnosis: dict[str, Any], page: dict[str, Any]) -> str:
    payload = {
        "kind": kind,
        "category": diagnosis.get("category"),
        "symptom": diagnosis.get("symptom"),
        "mode": (diagnosis.get("readiness_reference") or {}).get("mode"),
        "code": (diagnosis.get("readiness_reference") or {}).get("code"),
        "screen": page.get("screen"),
        "job_id": page.get("job_id"),
        "error_code": page.get("error_code"),
    }
    import json

    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _md_escape(value: Any) -> str:
    text = redact_text(value)
    return text.replace("\r\n", "\n").strip()


def render_markdown(
    *,
    kind: str,
    title: str,
    report_id: str,
    diagnosis: dict[str, Any],
    snapshot: dict[str, Any],
    attempted_actions: list[dict[str, Any]],
    notes: str = "",
    investigation: dict[str, Any] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> str:
    page = snapshot.get("page_context") or {}
    verdict = (snapshot.get("setup") or {}).get("verdict") or {}
    runtime = snapshot.get("runtime") or {}
    task = snapshot.get("task") or {}
    attempt = task.get("attempt") or {}
    facts = diagnosis.get("facts") or []
    hypotheses = diagnosis.get("hypotheses") or []
    actions = diagnosis.get("recommended_actions") or []
    from shine.source_identity import source_identity

    identity = source_identity()

    lines: list[str] = [
        f"# [{kind}] {_md_escape(title)}",
        "",
        f"- 报告编号：{report_id}",
        f"- 生成时间：{snapshot.get('snapshot_at') or utcnow().isoformat()}",
        f"- 报告类型：{kind}",
        "- 产品：Shine Video Studio",
        "- 报告格式版本：support-report-v1",
        "- 本地知识库：shine-support-kb-2026.11.16",
        f"- 构建提交：{_md_escape(os.environ.get('SHINE_BUILD_COMMIT') or '本机构建（未注入提交号）')}",
        f"- 运行代码身份：{_md_escape(identity.get('display') or 'unknown')}"
        f"（{_md_escape(identity.get('kind') or 'unknown')}，digest {str(identity.get('digest') or '')[:12] or '无'}）",
        f"- 页面：{page.get('screen') or 'unknown'}",
        f"- 项目：{page.get('project_id') or '未指定'}",
        f"- 任务：{page.get('job_id') or task.get('job_id') or '未指定'}",
        f"- 尝试：{page.get('attempt_id') or attempt.get('attempt_id') or '未指定'}",
        f"- 失败阶段：{attempt.get('status') or task.get('status') or '未指定'}"
        f" / 外部核对状态 {attempt.get('reconciliation_state') or '无'}",
        "",
        "> 本报告由本机 AI 管家生成，仅用于向开发者说明问题；不包含自动修复、补丁或 Issue 提交。",
        "",
        "## 1. 问题摘要",
        "",
        f"- 结论：{_md_escape(diagnosis.get('category_label') or diagnosis.get('category'))}",
        f"- 现象：{_md_escape(diagnosis.get('symptom'))}",
        f"- 严重度：{_md_escape(diagnosis.get('severity'))}",
        f"- 置信度：{_md_escape(diagnosis.get('confidence'))}",
        f"- 当前生产裁决：{_md_escape(verdict.get('label') or verdict.get('mode'))}"
        f"（{_md_escape(verdict.get('code') or '无错误码')}）",
        "",
        "## 2. 复现步骤",
        "",
        f"1. 打开工作台页面「{_md_escape(page.get('screen') or 'unknown')}」。",
    ]
    if page.get("project_id"):
        lines.append(f"2. 选择项目 `{_md_escape(page.get('project_id'))}`。")
    else:
        lines.append("2. 不限定项目，仅观察基础配置/运行状态。")
    if page.get("job_id") or task:
        lines.append(
            f"3. 打开任务 `{_md_escape(page.get('job_id') or task.get('job_id'))}` 的详情或生产日志。"
        )
    else:
        lines.append("3. 触发页面上的「检查环境」或对应操作。")
    lines.append(f"4. 观察现象：{_md_escape(diagnosis.get('symptom'))}")
    lines.extend(
        [
            "",
            "## 3. 环境摘要",
            "",
            f"- 生产模式：`{_md_escape(verdict.get('mode'))}` / `{_md_escape(verdict.get('code'))}`",
            f"- 运行配置状态：`{_md_escape(runtime.get('status'))}`",
            f"- 服务核验：{'通过' if runtime.get('services_verified') else '未通过'}",
            f"- 后台 Worker 记录：{len(runtime.get('workers') or [])} 个",
            f"- 受管渲染服务：{len(runtime.get('managed_processes') or [])} 个",
        ]
    )
    preparation = ((snapshot.get("setup") or {}).get("summary") or {}).get("preparation") or {}
    if isinstance(preparation, dict) and preparation.get("status"):
        prep_line = f"- 最近整备任务：`{_md_escape(preparation.get('status'))}`"
        if preparation.get("current"):
            prep_line += f"（步骤 `{_md_escape(preparation.get('current'))}`）"
        if preparation.get("error"):
            prep_line += f" / 错误码 `{_md_escape(preparation.get('error'))}`"
        lines.append(prep_line)
    idle_blockers = (snapshot.get("setup") or {}).get("idle_blockers") or []
    if idle_blockers:
        lines.append(f"- 配置空闲门禁阻塞项：{len(idle_blockers)} 项")
        for row in idle_blockers[:5]:
            if isinstance(row, dict):
                lines.append(
                    f"  - `{_md_escape(row.get('kind'))}`：{_md_escape(row.get('label') or row.get('id'))}"
                )
    if task:
        attempt = task.get("attempt") or {}
        lines.extend(
            [
                f"- 任务状态：`{_md_escape(task.get('status'))}`",
                f"- 尝试：`{_md_escape(attempt.get('status'))}` / 进度 {attempt.get('progress', 0)}%",
                f"- 失败码：`{_md_escape(task.get('failure_code') or attempt.get('failure_code') or '无')}`",
                f"- 结果核对状态：`{_md_escape(attempt.get('reconciliation_state') or '无')}`",
            ]
        )
    lines.extend(["", "## 4. 证据（已脱敏）", "", "| 事实 | 值 | 来源 |", "| --- | --- | --- |"])
    for fact in facts:
        lines.append(
            f"| {_md_escape(fact.get('key'))} | {_md_escape(fact.get('value'))} | {_md_escape(fact.get('source'))} |"
        )
    if not facts:
        lines.append("| 无 | 无 | — |")
    lines.extend(
        [
            "",
            "权威来源：`/api/v1/setup/verdict`、`/api/v1/setup/runtime`、`worker_instances`、`jobs/job_attempts`。",
            "",
            "## 5. 已尝试的恢复操作",
            "",
        ]
    )
    if attempted_actions:
        for row in attempted_actions:
            verification = row.get("verification") or {}
            executor_state = (
                verification.get("executor_status") or verification.get("task_status") or "未知"
            )
            lines.append(
                f"- `{_md_escape(row.get('kind'))}`：授权动作状态 {_md_escape(row.get('status'))}"
                f"；原执行器任务 {_md_escape(row.get('original_task_id') or '无')}"
                f"；执行器状态 {_md_escape(executor_state)}"
                f"；复核结论 {_md_escape(verification.get('outcome') or '未完成')}"
                f"（权威 {_md_escape(verification.get('authority') or '未记录')}）"
            )
            if row.get("last_error"):
                lines.append(f"  - 当时的失败说明：{_md_escape(row.get('last_error'))}")
    else:
        lines.append("- 未执行任何自动化修复。")
    lines.extend(["", "## 6. 根因判断（已确认与疑似严格分开）", "", "### 已确认事实", ""])
    confirmed = diagnosis.get("root_cause") if diagnosis.get("root_cause_confirmed") else None
    if confirmed:
        lines.append(_md_escape(confirmed))
    else:
        lines.append(
            "- 现有证据只能确认上述「现象」与「环境事实」，尚不能确认代码级根因。"
        )
    lines.extend(["", "### 疑似原因（未经证实）", ""])
    if hypotheses:
        for row in hypotheses:
            lines.append(
                f"- {_md_escape(row.get('cause'))}（置信度 {_md_escape(row.get('confidence'))}，"
                "需要开发者用日志或复现确认）"
            )
    else:
        lines.append("- 没有足够证据提出疑似原因，请开发者结合证据区定位。")
    lines.extend(["", "## 7. 证据来源与等级", "", "| 工具 | 状态 | 等级 |", "| --- | --- | --- |"])
    steps = (investigation or {}).get("steps") or []
    if steps:
        for step in steps:
            lines.append(
                f"| {_md_escape(step.get('tool'))} | {_md_escape(step.get('status'))} "
                f"| {_md_escape(step.get('evidence_level') or 'LOCAL_LEDGER')} |"
            )
    else:
        lines.append("| read_support_context | OK | LOCAL_LEDGER |")
    if references:
        lines.extend(["", "外部参考（仅作证据，不是可执行修复指令）：", ""])
        for row in references[:5]:
            if not isinstance(row, dict):
                continue
            applicability = (row.get("applicability") or {}).get("state")
            lines.append(
                f"- {_md_escape(row.get('title'))}：{_md_escape(row.get('url'))}"
                f"（{_md_escape(applicability or 'UNKNOWN')}）"
            )
    lines.extend(
        [
            "",
            "## 8. 影响面",
            "",
            f"- 是否阻断真实生产：{'是' if verdict.get('mode') not in {'READY', 'RUNNING'} else '当前裁决未阻断'}",
            "- 是否涉及生产门禁：如涉及，AI 管家未修改任何门禁、镜头或人工确认资产。",
            "- 是否涉及用户素材/成片：报告不包含素材内容，仅包含脱敏状态事实。",
            "",
            "## 9. 开发验收建议",
            "",
        ]
    )
    for action in actions:
        lines.append(f"- 建议动作 `{_md_escape(action.get('kind'))}`：{_md_escape(action.get('reason'))}")
    lines.extend(
        [
            "- 修复后请在原权威裁决 `/api/v1/setup/verdict` 下复核，不接受由 AI 或前端自行宣布 READY。",
            "- 若修复涉及源码，请附回归测试与既有八分片回归结果。",
            "",
            "## 10. 隐私与附件",
            "",
            "- 密钥、令牌、用户目录与私人数据根路径已脱敏。",
            "- 外部资料仅作为参考证据，未被当作可执行修复指令。",
            "- 本报告不会自动修改代码、创建 Git 补丁、提交 Issue 或 PR。",
        ]
    )
    if notes:
        lines.extend(["", "## 附注", "", _md_escape(notes)])
    return "\n".join(lines).strip() + "\n"


def generate_report(
    db,
    settings,
    *,
    kind: str,
    title: str,
    diagnosis: dict[str, Any],
    snapshot: dict[str, Any],
    session_id: str | None = None,
    project_id: str | None = None,
    attempted_actions: list[dict[str, Any]] | None = None,
    notes: str = "",
    investigation: dict[str, Any] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> tuple[SupportReport, bool]:
    """Write (or de-duplicate) one report.  Returns (row, deduplicated)."""
    kind = (kind or "BLOCKER").upper()
    if kind not in REPORT_KINDS:
        raise ValueError("unsupported report kind")
    safe_title = redact_text(title or diagnosis.get("symptom") or "工程问题", limit=120)
    fingerprint = _fingerprint(kind, diagnosis, snapshot.get("page_context") or {})
    since = utcnow() - _DEDUP_WINDOW
    existing = db.scalar(
        select(SupportReport)
        .where(SupportReport.fingerprint == fingerprint, SupportReport.kind == kind)
        .order_by(SupportReport.created_at.desc())
    )
    if existing is not None and existing.created_at >= since:
        try:
            if Path(existing.path).is_file():
                return existing, True
        except OSError:
            pass

    report_id = new_id()
    filename = f"SUPPORT-{utcnow():%Y%m%d-%H%M%S}-{kind}-{report_id[:8]}.md"
    if not _FILENAME_RE.match(filename):
        raise ValueError("internal report filename invariant violated")
    root = report_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("report directory must not be a symlink")
    target = (root / filename).resolve()
    if target.parent != root:
        raise ValueError("report path escaped its controlled directory")

    body = render_markdown(
        kind=kind,
        title=safe_title,
        report_id=report_id,
        diagnosis=diagnosis,
        snapshot=snapshot,
        attempted_actions=attempted_actions or [],
        notes=notes,
        investigation=investigation,
        references=references,
    )
    tmp = root / f".{uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    row = SupportReport(
        id=report_id,
        session_id=session_id,
        project_id=project_id,
        kind=kind,
        title=safe_title,
        filename=filename,
        path=str(target),
        sha256=digest,
        fingerprint=fingerprint,
        status="DRAFT_FOR_DEVELOPER",
        payload=redact_payload(
            {
                "diagnosis": diagnosis,
                "page": snapshot.get("page_context") or {},
                "evidence_id": snapshot.get("evidence_id"),
                "attempted_actions": attempted_actions or [],
                "investigation": investigation or {},
                "external_references": references or [],
            },
            private_roots=private_roots_for(settings),
        ),
    )
    db.add(row)
    db.flush()
    return row, False


def read_report(settings, row: SupportReport) -> dict[str, Any]:
    root = report_root(settings)
    target = Path(row.path)
    if target.is_symlink():
        raise ValueError("report file must not be a symlink")
    resolved = target.resolve()
    if resolved.parent != root or not _FILENAME_RE.match(resolved.name):
        raise ValueError("report path rejected")
    content = resolved.read_text(encoding="utf-8") if resolved.is_file() else ""
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "filename": row.filename,
        "display_path": f"{REPORT_DIRNAME}/{row.filename}",
        "created_at": row.created_at.isoformat(),
        "sha256": row.sha256,
        "content": content,
        "status": row.status,
    }

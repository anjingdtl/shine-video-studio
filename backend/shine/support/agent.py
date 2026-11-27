"""The bounded support-agent turn (V4: LLM-native, no fake conversation).

A turn is: refresh read-only evidence → deterministic diagnosis → the
configured model investigates the real ledgers with read-only tools → answer
and, when a change is implied, a structured *proposal* for the person to
confirm.

There is no shell, no arbitrary file read, no code write and no second task
system.  Without a configured text model there is no conversation: the API
refuses a message with ``409 COPILOT_LLM_REQUIRED`` and the UI shows the
deterministic health check instead.  The keyword-intent planner, the keyword
Q&A templates and the no-LLM composed replies were retired in V4.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from shine.copilot.chat_investigator import CHAT_SYSTEM_PROMPT as SYSTEM_PROMPT  # noqa: F401 - public name
from shine.llm.base import LLMNotConfigured
from shine.models import new_id, utcnow
from shine.security import is_loopback_url
from shine.support.context import build_evidence_snapshot, normalise_page_id
from shine.support.diagnostics import diagnose_snapshot
from shine.support.investigation import investigate
from shine.support.knowledge import local_version_facts, search_local, search_official_web
from shine.support.memory import budget as memory_budget
from shine.support.memory import profile as memory_profile
from shine.support.memory import store as memory_store
from shine.support.models import SupportMessage, SupportSession
from shine.support.privacy import private_roots_for, redact_text
from shine.support.tools import forbidden_intent

SESSION_TTL = timedelta(days=30)
MAX_MESSAGE_CHARS = 4000

#: ``SYSTEM_PROMPT`` is the chat investigator's prompt: one source, imported
#: here so existing callers keep the same name.

POLICY_REFUSAL_ANSWER = (
    "【结论】已拒绝越权操作：AI 管家不会执行命令、修改源码或绕过生产门禁。\n"
    "【下一步】需要改代码或装依赖时，请生成问题报告交给开发者；项目内的问题可以在配置文本模型后"
    "让管家查证。"
)


def _message(row: SupportMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "role": row.role,
        "kind": row.kind,
        "content": row.content,
        "payload": row.payload,
        "evidence_ids": row.evidence_ids,
        "created_at": row.created_at.isoformat(),
    }


def session_payload(db, row: SupportSession) -> dict[str, Any]:
    messages = db.scalars(
        select(SupportMessage)
        .where(SupportMessage.session_id == row.id)
        .order_by(SupportMessage.created_at, SupportMessage.id)
    ).all()
    return {
        "id": row.id,
        "status": row.status,
        "page_id": row.page_id,
        "project_id": row.project_id,
        "episode_id": row.episode_id,
        "job_id": row.job_id,
        "attempt_id": row.attempt_id,
        "task_id": row.task_id,
        "error_code": row.error_code,
        "context": row.context_json,
        "messages": [_message(item) for item in messages],
        "expires_at": row.expires_at.isoformat(),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def create_session(
    db,
    settings,
    *,
    page_id: str | None = None,
    project_id: str | None = None,
    episode_id: str | None = None,
    job_id: str | None = None,
    attempt_id: str | None = None,
    task_id: str | None = None,
    error_code: str | None = None,
    actor: str = "local-user",
) -> SupportSession:
    snapshot = build_evidence_snapshot(
        db,
        settings,
        page_id=page_id,
        project_id=project_id,
        episode_id=episode_id,
        job_id=job_id,
        attempt_id=attempt_id,
        task_id=task_id,
        error_code=error_code,
    )
    profile = memory_profile.read_profile(db)
    row = SupportSession(
        id=new_id(),
        project_id=project_id,
        page_id=normalise_page_id(page_id),
        episode_id=episode_id[:80] if episode_id else None,
        job_id=job_id,
        attempt_id=attempt_id,
        task_id=task_id[:80] if task_id else None,
        error_code=error_code[:80] if error_code else None,
        status="OPEN",
        actor=actor[:64],
        context_json={
            "snapshot": snapshot,
            # The pre-read content is not copied into a second store: the
            # session only records which configuration revision it saw.
            "profile_revision": profile["config_revision"],
            "assistant_nickname": profile["assistant_nickname"],
        },
        expires_at=utcnow() + SESSION_TTL,
    )
    db.add(row)
    db.flush()
    db.commit()
    return row


def get_session(db, session_id: str) -> SupportSession | None:
    return db.get(SupportSession, session_id)


def _add_message(
    db,
    session: SupportSession,
    *,
    role: str,
    content: str,
    kind: str = "message",
    payload: dict[str, Any] | None = None,
    evidence_ids: list[str] | None = None,
) -> SupportMessage:
    row = SupportMessage(
        id=new_id(),
        session_id=session.id,
        role=role,
        kind=kind,
        content=redact_text(content, limit=8000),
        payload=payload or {},
        evidence_ids=evidence_ids or [],
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def web_search_enabled(db) -> bool:
    """The legacy key keeps its original meaning inside the unified profile."""
    return memory_profile.web_search_enabled(db)


def set_web_search(db, *, enabled: bool, actor: str = "local-user") -> dict[str, Any]:
    """One configuration path: the same profile update the settings page uses."""
    payload = memory_profile.update_preferences(
        db, updates={"web_search": bool(enabled)}, actor=actor
    )
    return {"web_search": bool(payload["web_search"])}


def _copilot_profile(db, gateway):
    """The copilot's model profile, with inheritance when the gateway offers it.

    Older/pluggable gateways only implement ``profile_for_role``; the copilot
    must keep working with them (and the remote-consent check must keep
    answering the same question), so the inheritance is used when present and
    the plain route lookup is kept as the compatible path.
    """
    resolver = getattr(gateway, "profile_for_role_or_inherit", None)
    if callable(resolver):
        return resolver(db, "support_agent")
    return gateway.profile_for_role(db, "support_agent"), "support_agent"


def llm_status(db, gateway) -> dict[str, Any]:
    """Whether the copilot has a usable text model, and which one.

    The one question every caller asks ("may this conversation turn run?")
    answered from the gateway's own route rule: an explicit ``support_agent``
    route wins, otherwise the configured text model is inherited.
    """
    if gateway is None:
        return {"configured": False, "status": "NOT_CONFIGURED", "reason": "GATEWAY_MISSING"}
    try:
        provider, source_role = _copilot_profile(db, gateway)
    except LLMNotConfigured as exc:
        return {"configured": False, "status": "NOT_CONFIGURED", "reason": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - a broken route is "not configured", by name
        return {"configured": False, "status": "UNAVAILABLE", "reason": type(exc).__name__}
    return {
        "configured": True,
        "status": "OK",
        "inherited": source_role != "support_agent",
        "source_role": source_role,
        "profile_name": getattr(provider, "name", None),
        "model": getattr(provider, "model", None),
        "kind": getattr(provider, "kind", None),
    }


def _remote_memory_context_withheld(db, gateway, profile: dict[str, Any]) -> bool:
    """Recalled memories need explicit consent before going to a remote LLM."""
    if gateway is None or profile.get("remote_context_consent"):
        return False
    try:
        provider, _source = _copilot_profile(db, gateway)
    except Exception:  # noqa: BLE001 - no route means no LLM call at all
        return False
    if provider.kind == "mock":
        return False
    return not is_loopback_url(provider.base_url)


def _needs_llm(question: str, diagnosis: dict[str, Any], gateway) -> bool:
    """Invoke the configured support_agent LLM for any real user question unless policy-refused."""
    if gateway is None or diagnosis.get("policy_refusal"):
        return False
    return bool((question or "").strip())


def handle_message(
    db,
    settings,
    session: SupportSession,
    content: str,
    *,
    actor: str = "local-user",
    gateway=None,
    knowledge_limit: int = 3,
) -> dict[str, Any]:
    """One bounded turn: evidence → deterministic diagnosis → LLM investigation.

    Raises :class:`~shine.llm.base.LLMNotConfigured` when no model is routed:
    there is deliberately no template reply any more.  A policy refusal
    (shell/git/source edits) is answered deterministically without a model.
    """
    from shine.copilot import chat_investigator
    from shine.support import project_knowledge

    content = redact_text(content, limit=MAX_MESSAGE_CHARS)
    policy_refusal = bool(forbidden_intent(content))
    if gateway is None and not policy_refusal:
        # No gateway at all: there is no conversation and nothing is stored.
        raise LLMNotConfigured("No text model is configured for the copilot")
    _add_message(db, session, role="user", content=content)
    session.status = "OPEN"
    session.updated_at = utcnow()

    # Configuration is read on every turn, so an edit on the settings page
    # applies to the next message without restarting the copilot.
    profile = memory_profile.read_profile(db)
    recall = memory_store.retrieve(
        db, project_id=session.project_id, query=content, limit=memory_store.MAX_RECALL
    )
    withheld = _remote_memory_context_withheld(db, gateway, profile)
    if withheld and recall.get("memories"):
        recall = {
            **recall,
            "state": "REMOTE_CONTEXT_WITHHELD",
            "memory_count_before_withhold": recall.get("count", 0),
            "memories": [],
            "count": 0,
            "reason": "当前模型在远端；未允许把召回记忆作为远端上下文，已只发送本机业务证据。",
        }
    if session.context_json is None or not isinstance(session.context_json, dict):
        session.context_json = {}
    session.context_json = {
        **session.context_json,
        "profile_revision": profile["config_revision"],
        "assistant_nickname": profile["assistant_nickname"],
    }

    snapshot = build_evidence_snapshot(
        db,
        settings,
        page_id=session.page_id,
        project_id=session.project_id,
        episode_id=session.episode_id,
        job_id=session.job_id,
        attempt_id=session.attempt_id,
        task_id=session.task_id,
        error_code=session.error_code,
    )
    diagnosis = diagnose_snapshot(snapshot, question=content, db=db)
    readiness_code = (diagnosis.get("readiness_reference") or {}).get("code")
    investigation = investigate(db, settings, snapshot, content, code=readiness_code)

    knowledge = search_local(content, limit=knowledge_limit)
    web: dict[str, Any] = {"enabled": False, "results": []}
    knowledge_answer: dict[str, Any] | None = None
    policy_refused = bool(diagnosis.get("policy_refusal"))
    if not policy_refused:
        try:
            knowledge_answer = project_knowledge.answer(content, limit=3)
        except Exception as exc:  # noqa: BLE001 - knowledge failure is reported, not fatal
            knowledge_answer = {
                "answered": False,
                "citations": [],
                "metadata": {"state": "UNKNOWN", "notice": type(exc).__name__},
                "unknown_reason": "项目百科读取失败。",
            }
    if web_search_enabled(db) and not policy_refused:
        web = search_official_web(
            content,
            enabled=True,
            error_code=readiness_code or (snapshot.get("page_context") or {}).get("error_code"),
            versions=local_version_facts(db, settings),
        )

    tool_trace: list[dict[str, Any]] = [
        {"tool": "read_setup_verdict", "status": "OK", "evidence_level": "LOCAL_AUTHORITY"},
        {
            "tool": "read_comfy_runtime",
            "status": "OK" if snapshot.get("runtime") else "UNAVAILABLE",
            "evidence_level": "LOCAL_LEDGER",
        },
        {"tool": "read_support_context", "status": "OK", "evidence_level": "LOCAL_LEDGER"},
        {
            "tool": "read_copilot_memory",
            "status": (
                "DISABLED"
                if not recall.get("enabled")
                else "OK" if recall.get("memories") else "UNAVAILABLE"
            ),
            "evidence_level": "LOCAL_DERIVED_MEMORY",
        },
    ]
    if knowledge_answer is not None:
        tool_trace.append(
            {
                "tool": "search_project_knowledge",
                "status": "OK" if knowledge_answer.get("citations") else "UNAVAILABLE",
                "evidence_level": "VERSIONED_PROJECT_DOCS",
            }
        )
    for step in investigation.get("steps") or []:
        tool_trace.append(
            {
                "tool": step.get("tool"),
                "status": step.get("status"),
                "evidence_level": step.get("evidence_level"),
                "ms": step.get("ms"),
            }
        )
    if web.get("enabled"):
        tool_trace.append(
            {
                "tool": "search_official_web",
                "status": "OK" if web.get("results") else "UNAVAILABLE",
                "evidence_level": "EXTERNAL_REFERENCE",
            }
        )
    # The trace is a bounded list; the same tool never appears twice.
    seen: set[str] = set()
    unique_trace: list[dict[str, Any]] = []
    for row in tool_trace:
        tool = str(row.get("tool"))
        if tool in seen:
            continue
        seen.add(tool)
        unique_trace.append(row)
    tool_trace = unique_trace[:8]

    llm_info: dict[str, Any] = {"used": False, "status": "NOT_CONFIGURED"}
    read_tools_trace: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    budget_evidence: dict[str, Any] | None = None
    answer = ""
    inherited_role: str | None = None

    if policy_refused:
        answer = POLICY_REFUSAL_ANSWER
        llm_info = {"used": False, "status": "POLICY_REFUSAL",
                    "blocked_instruction": diagnosis["policy_refusal"].get("blocked")}
    elif gateway is None:
        # Unreachable in practice (guarded above); kept as a named refusal.
        db.rollback()
        raise LLMNotConfigured("No text model is configured for the copilot")
    elif _needs_llm(content, diagnosis, gateway):
        base_prompt_payload = {
            "question": content,
            "page": snapshot.get("page_context"),
            "verdict": (snapshot.get("setup") or {}).get("verdict"),
            "runtime": {
                "status": (snapshot.get("runtime") or {}).get("status"),
                "services_verified": (snapshot.get("runtime") or {}).get("services_verified"),
            },
            "task": snapshot.get("task"),
            "diagnosis": {
                "category": diagnosis.get("category"),
                "symptom": diagnosis.get("symptom"),
                "facts": diagnosis.get("facts"),
                "next_step": diagnosis.get("next_step"),
            },
            "workflow_capabilities": snapshot.get("workflow_capabilities"),
            "project_knowledge": [
                {"title": row.get("title"), "status": row.get("status"), "summary": row.get("summary")}
                for row in ((knowledge_answer or {}).get("citations") or [])[:4]
            ],
            # The memory budget reads ``title`` off each row: rows, not titles.
            "local_knowledge": [{"title": row.get("title")} for row in knowledge],
            "official_references": [
                {"title": row.get("title"), "url": row.get("url"), "applicability": row.get("applicability")}
                for row in (web.get("results") or [])
            ],
        }
        try:
            copilot_profile, inherited_role = _copilot_profile(db, gateway)
            if hasattr(gateway, "pin_role"):
                gateway.pin_role("support_agent", copilot_profile)
        except Exception:  # noqa: BLE001 - the investigator reports the named refusal
            inherited_role = None
        try:
            # The SAME fitted context V2.2 defined (persona, startup memory,
            # recalled memories, business facts, knowledge) — bounded so the
            # investigator's fallback prompt still has room for evidence.
            prompt_payload, budget_evidence = memory_budget.fit_prompt_payload(
                base_prompt_payload,
                profile=profile,
                startup_memory=profile["startup_memory"],
                memories=recall.get("memories") or [],
                limit=chat_investigator.CONTEXT_BUDGET,
            )
            result = chat_investigator.investigate(
                db,
                settings,
                gateway,
                question=content,
                session=session,
                context={
                    **prompt_payload,
                    "deterministic_diagnosis": prompt_payload.get("diagnosis"),
                    "read_only_notice": "可用只读工具零副作用、无需授权；需要改动时只能用 propose_action / "
                                        "propose_project_grant 提出建议，由用户确认。",
                },
            )
            answer = result["answer"]
            read_tools_trace = result["tools"]
            proposals = result["proposals"]
            llm_info = {**result["llm"], "used": bool(answer)}
            blocked = forbidden_intent(answer)
            if blocked:
                # A model (or an injected document) never gains a tool.  Any
                # instruction-shaped prose is withheld rather than shown as
                # advice the person might copy into a terminal.
                answer = "模型输出包含越权操作指令，已隐藏；请以本机规则、证据和权威裁决为准。"
                llm_info["blocked_instruction"] = blocked
                proposals = []
        except LLMNotConfigured as exc:
            # The API refuses "no model at all" with 409 before storing a
            # message; a refusal reaching here is a secondary gate (for example
            # the local LLM stage must wait for the GPU).  It is reported by
            # name — never dressed up as an answer.
            llm_info = {
                "used": False,
                "status": "UNAVAILABLE",
                "error": "LLMNotConfigured",
                "message": redact_text(str(exc), limit=200),
            }
            answer = (
                f"【结论】文本模型本轮不可用（{redact_text(str(exc), limit=120)}）。\n"
                "【下一步】稍后重试；本机确定性体检仍可在「项目体检」中查看。"
            )
        except Exception as exc:  # noqa: BLE001 - a model outage is named, never faked
            llm_info = {
                "used": False,
                "status": "UNAVAILABLE",
                "error": type(exc).__name__,
                "message": redact_text(str(exc), limit=200),
            }
            answer = (
                f"【结论】文本模型本轮不可用（{type(exc).__name__}），无法给出智能回答。\n"
                "【下一步】稍后重试；本机确定性体检仍可在「项目体检」中查看。"
            )
        finally:
            # The pin exists for this turn only; the next turn resolves the
            # route again so a model change applies without a restart.
            if inherited_role is not None and hasattr(gateway, "clear_pins"):
                gateway.clear_pins()

    memory_evidence = {
        "enabled": bool(recall.get("enabled")),
        "state": recall.get("state"),
        "retrieved": int(recall.get("count") or 0),
        "retrieved_ids": [row.get("id") for row in recall.get("memories") or []],
        "project_id": session.project_id,
        "profile_revision": profile["config_revision"],
        "assistant_nickname": profile["assistant_nickname"],
        "user_salutation": profile["user_salutation"],
        "startup_memory_chars": len(profile["startup_memory"] or ""),
        "startup_memory_applied": bool(profile["startup_memory"]) and budget_evidence is not None,
        "remote_context_withheld": withheld,
        "reason": recall.get("reason"),
        "budget": budget_evidence,
    }
    diagnosis["llm"] = llm_info
    diagnosis["memory"] = memory_evidence
    diagnosis["investigation"] = {
        "steps_used": investigation.get("steps_used"),
        "max_steps": investigation.get("max_steps"),
        "elapsed_ms": investigation.get("elapsed_ms"),
        "truncated": investigation.get("truncated"),
        "evidence_levels": sorted(
            {
                str(step.get("evidence_level"))
                for step in investigation.get("steps") or []
                if step.get("evidence_level")
            }
        ),
    }
    payload = {
        "diagnosis": diagnosis,
        "investigation": {
            "steps": [
                {
                    "tool": step.get("tool"),
                    "status": step.get("status"),
                    "evidence_level": step.get("evidence_level"),
                    "ms": step.get("ms"),
                    "payload": step.get("payload"),
                }
                for step in investigation.get("steps") or []
            ],
            "steps_used": investigation.get("steps_used"),
            "truncated": investigation.get("truncated"),
            "elapsed_ms": investigation.get("elapsed_ms"),
        },
        "read_tools": read_tools_trace,
        "proposals": proposals,
        "knowledge": [
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "source": row.get("source"),
                "knowledge_version": row.get("knowledge_version"),
            }
            for row in knowledge
        ],
        "official_references": web.get("results") or [],
        "web_search_enabled": bool(web.get("enabled")),
        "tool_trace": tool_trace,
        "llm": llm_info,
        "memory": memory_evidence,
        "evidence_id": snapshot.get("evidence_id"),
        "page": snapshot.get("page_context"),
        "suggested_questions": [],
    }
    message = _add_message(
        db,
        session,
        role="assistant",
        kind="diagnosis",
        content=answer,
        payload=payload,
        evidence_ids=[snapshot.get("evidence_id")] if snapshot.get("evidence_id") else [],
    )
    session.context_json = {
        **(session.context_json if isinstance(session.context_json, dict) else {}),
        "snapshot": snapshot,
        "last_diagnosis": diagnosis,
        "last_evidence_id": snapshot.get("evidence_id"),
        "last_investigation": payload["investigation"],
        "last_read_tools": read_tools_trace,
        "last_proposals": proposals,
        "last_references": payload["official_references"],
        "last_knowledge": knowledge_answer,
        "last_memory": memory_evidence,
        "assistant_nickname": profile["assistant_nickname"],
        "profile_revision": profile["config_revision"],
    }
    session.updated_at = utcnow()
    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "SUPPORT_DIAGNOSIS_CREATED",
        "support_session",
        session.id,
        {
            "category": diagnosis.get("category"),
            "outcome": diagnosis.get("outcome"),
            "knowledge_hits": len((knowledge_answer or {}).get("citations") or []),
            "authority": "/api/v1/setup/verdict",
            "llm_status": llm_info.get("status"),
            "memory_state": memory_evidence["state"],
            "memory_retrieved": memory_evidence["retrieved"],
            "profile_revision": profile["config_revision"],
            "tools": [row["tool"] for row in tool_trace] + [row["tool"] for row in read_tools_trace],
            "proposals": len(proposals),
        },
        project_id=session.project_id,
    )
    db.commit()
    return {"session": session_payload(db, session), "message": _message(message), "diagnosis": diagnosis}


def latest_diagnosis(session: SupportSession) -> dict[str, Any]:
    context = session.context_json if isinstance(session.context_json, dict) else {}
    return context.get("last_diagnosis") or {}


def latest_snapshot(session: SupportSession) -> dict[str, Any]:
    context = session.context_json if isinstance(session.context_json, dict) else {}
    return context.get("snapshot") or {}


def latest_investigation(session: SupportSession) -> dict[str, Any]:
    context = session.context_json if isinstance(session.context_json, dict) else {}
    return context.get("last_investigation") or {}


def latest_references(session: SupportSession) -> list[dict[str, Any]]:
    context = session.context_json if isinstance(session.context_json, dict) else {}
    rows = context.get("last_references") or []
    return [row for row in rows if isinstance(row, dict)]


def attempt_actions(db, session_id: str) -> list[dict[str, Any]]:
    from shine.support.models import SupportAction

    rows = db.scalars(
        select(SupportAction)
        .where(SupportAction.session_id == session_id)
        .order_by(SupportAction.created_at)
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "original_task_id": row.original_task_id,
            "verification": row.verification,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def private_roots(settings) -> tuple[str, ...]:
    return private_roots_for(settings)

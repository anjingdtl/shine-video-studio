"""Bounded autonomous tool loop for one task grant (V3.1).

The loop is the *only* place a model's proposal becomes work, and it is
deliberately small:

    LLM proposal -> grant policy -> original SupportAction -> original executor
                 -> read the original receipt -> next proposal

Five properties are non-negotiable and encoded here rather than promised in a
prompt:

* **bounded** - a step budget, a wall-clock budget, a tool-call budget and a
  no-progress detector.  There is no ``while True``: the loop ends by name.
* **evidence-based** - "the model said it is done" is never success.  The
  server decides from the original action receipts, and a model that stops
  without one is paused for a human, not congratulated.
* **no replay of the unknown** - an ``UNKNOWN``/``NEEDS_REVIEW`` receipt stops
  the loop.  Re-submitting is a person's decision, not a retry.
* **bounded context** - the transcript is compressed as it grows, so a long
  task does not silently become a huge prompt.
* **durable** - the transcript, the step ledger and the pending original task
  live in the grant row itself.  A closed page, an API/Worker restart or a
  repeated click reconnects to the SAME state; a short driver lease keeps one
  executor at a time.  A delegated original task is waited on by readback,
  never by spinning model turns and never by resubmission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shine.copilot import task_grant as grant_contract
from shine.llm.base import LLMError, LLMNotConfigured
from shine.llm.tool_calls import (
    FALLBACK_SCHEMA,
    ToolSpec,
    ToolTurn,
    fallback_system_prompt,
    fallback_user_prompt,
    parse_fallback_reply,
    summarise_messages,
)
from shine.models import utcnow
from shine.support import grants as grant_service

#: Outcomes that are real evidence the executor finished this sub-action.
SATISFIED_OUTCOMES: frozenset[str] = frozenset(
    {"VERIFIED", "PLAN_FROZEN", "SUCCEEDED", "RELEASED", "EXECUTED"}
)

#: Outcomes that must stop the loop and keep the scene for a person.
STOP_OUTCOMES: frozenset[str] = frozenset(
    {"UNKNOWN", "NOT_VERIFIED", "REGISTERED_NOT_EXECUTABLE"}
)

MAX_TRANSCRIPT = 24
"""Messages kept verbatim; older ones are folded into one running summary."""


@dataclass
class LoopLimits:
    max_steps: int = 12
    max_wall_seconds: int = 1800
    max_no_progress: int = 3

    def public(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_wall_seconds": self.max_wall_seconds,
            "max_no_progress": self.max_no_progress,
        }


@dataclass
class LoopResult:
    status: str
    stop_reason: str
    final: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "final": self.final,
            "steps": self.steps,
            "llm": self.llm,
            "usage": self.usage,
            "verification": self.verification,
        }


def _satisfied(receipt: dict[str, Any]) -> bool:
    outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
    if outcome in SATISFIED_OUTCOMES:
        return True
    return str(receipt.get("status") or "") in {"SUCCEEDED", "EXECUTED"}


def _must_stop(receipt: dict[str, Any]) -> str | None:
    """A named stop reason, or None when the loop may continue."""
    outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
    status = str(receipt.get("status") or "")
    if outcome in STOP_OUTCOMES:
        return "PAUSED_UNKNOWN" if outcome == "UNKNOWN" else "PAUSED_USER_DECISION"
    if status == "NEEDS_REVIEW":
        return "PAUSED_UNKNOWN"
    if status in {"FAILED", "REFUSED", "SUPERSEDED"}:
        return "PAUSED_USER_DECISION"
    if status == "EXPIRED":
        return "PAUSED_CONTRACT"
    return None


def _is_pending(receipt: dict[str, Any]) -> bool:
    """A delegated original task that has not reached a terminal conclusion."""
    status = str(receipt.get("status") or "")
    outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
    if status in {"DELEGATED", "APPROVED"}:
        return True
    return status == "EXECUTED" and outcome in {"", "PENDING"}


def _state_summary(
    *,
    objective: str,
    grant: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """What the model is allowed to see as "current state".

    Server facts only: the remaining budget, the objective, and one line per
    executed step.  Raw receipts are bounded so a long task cannot flood the
    prompt with its own history.
    """
    return {
        "objective": objective,
        "grant_status": grant_contract.grant_status(grant, now=utcnow()),
        "remaining_budget": grant_contract.remaining(grant),
        # The REAL ids inside the frozen scope: a model that cannot see them
        # invents slugs ("shine-h3") and is refused by SCOPE_EXCEEDED.
        "scope": {
            "project_ids": list((grant.get("scope") or {}).get("project_ids") or []),
            "installation_ids": list((grant.get("scope") or {}).get("installation_ids") or []),
        },
        "permissions": {
            key: value
            for key, value in (grant.get("permissions") or {}).items()
            if value
        },
        "steps": [
            {
                "n": index + 1,
                "capability_id": step.get("capability_id"),
                "status": step.get("status"),
                "outcome": step.get("outcome"),
                "original_task_id": step.get("original_task_id"),
                "error_code": step.get("error_code"),
                "message": str(step.get("message") or "")[:240],
            }
            for index, step in enumerate(steps[-8:])
        ],
        "satisfied_count": sum(1 for step in steps if step.get("satisfied")),
    }


def _compress(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= MAX_TRANSCRIPT:
        return messages
    head = messages[:1]
    tail = messages[-(MAX_TRANSCRIPT - 2):]
    summary = {
        "role": "user",
        "content": "【较早步骤摘要】\n" + summarise_messages(messages[1:-(MAX_TRANSCRIPT - 2)]),
    }
    return [*head, summary, *tail]


def advance(
    db,
    settings,
    *,
    grant_id: str,
    objective: str,
    tools: list[ToolSpec],
    actor: str = "local-user",
    session_id: str | None = None,
    app=None,
    gateway=None,
    max_steps: int = 1,
    owner: str,
    lease_seconds: int = grant_service.LOOP_LEASE_SECONDS,
) -> LoopResult:
    """Claim the durable driver lease and advance the SAME task by a bounded burst.

    The Worker calls this once per tick (one model turn); the API calls it with
    a larger burst for immediate feedback.  A repeated click while another
    driver holds the lease reconnects to the current state instead of starting
    a second execution.
    """
    if not grant_service.claim_loop(db, grant_id, owner=owner, lease_seconds=lease_seconds):
        loaded = grant_service.load(db, grant_id)
        state = grant_service.loop_state(loaded.grant)
        return LoopResult(
            status=grant_contract.grant_status(loaded.grant, now=utcnow()),
            stop_reason="LOOP_ALREADY_RUNNING",
            final="本次授权正在由另一个执行者推进，已接回同一任务与预算。",
            steps=list(state.get("steps") or []),
            llm=dict(state.get("llm_info") or {}),
            usage=grant_contract.usage_of(loaded.grant),
        )
    return run(
        db,
        settings,
        grant_id=grant_id,
        objective=objective,
        tools=tools,
        actor=actor,
        session_id=session_id,
        app=app,
        gateway=gateway,
        limits=LoopLimits(max_steps=max_steps),
    )


def run(
    db,
    settings,
    *,
    grant_id: str,
    objective: str,
    tools: list[ToolSpec],
    actor: str = "local-user",
    session_id: str | None = None,
    app=None,
    gateway=None,
    limits: LoopLimits | None = None,
    max_completion_tokens: int = 1200,
) -> LoopResult:
    """Advance one authorized task until it is done or must stop.

    One model for the whole task: the explicitly routed ``support_agent``
    profile wins, otherwise the already-configured text model is inherited.
    The resolved profile is pinned for BOTH transports (native tool calling
    and the structured fallback), so a provider without native tools never
    drops the task into "not configured" while the native path was working.
    """
    inherited_from: str | None = None
    if gateway is not None and hasattr(gateway, "profile_for_role_or_inherit"):
        try:
            profile, source_role = gateway.profile_for_role_or_inherit(db, "support_agent")
            if hasattr(gateway, "pin_role"):
                gateway.pin_role("support_agent", profile)
            if source_role != "support_agent":
                inherited_from = source_role
        except LLMNotConfigured:
            pass  # _next_turn reports the named refusal per turn
    try:
        return _run_bounded(
            db,
            settings,
            grant_id=grant_id,
            objective=objective,
            tools=tools,
            actor=actor,
            session_id=session_id,
            app=app,
            gateway=gateway,
            limits=limits,
            max_completion_tokens=max_completion_tokens,
            inherited_from=inherited_from,
        )
    finally:
        if gateway is not None and hasattr(gateway, "pin_role"):
            gateway.pin_role("support_agent", None)


def _persist(db, grant_id: str, state: dict[str, Any]) -> None:
    try:
        grant_service.save_loop(db, grant_id, state)
    except grant_contract.GrantError:
        # A concurrent revoke/expiry wins over this write; the next iteration
        # reads the real status and stops by name.
        pass


def _run_bounded(
    db,
    settings,
    *,
    grant_id: str,
    objective: str,
    tools: list[ToolSpec],
    actor: str = "local-user",
    session_id: str | None = None,
    app=None,
    gateway=None,
    limits: LoopLimits | None = None,
    max_completion_tokens: int = 1200,
    inherited_from: str | None = None,
) -> LoopResult:
    """The bounded loop itself; every tool call becomes an original receipt."""
    from shine.copilot import read_tools
    from shine.support import actions as action_service

    config = limits or LoopLimits()
    spec_by_name = {spec.name: spec for spec in tools}
    steps: list[dict[str, Any]] = []
    no_progress = 0
    last_block_code: str | None = None
    llm_info: dict[str, Any] = {"native_tool_calling": None, "turns": 0, "model": None,
                                "inherited_from": inherited_from}
    final_text = ""

    loaded = grant_service.load(db, grant_id)
    granted_capabilities = sorted(
        name for name in spec_by_name
        if grant_contract.capability_allowed(loaded.grant, name)
    )
    usable_tools = [spec_by_name[name] for name in granted_capabilities]
    # Read-only investigation is offered whenever the grant may read at all;
    # it never creates a SupportAction and never starts a renderer.
    if (loaded.grant.get("permissions") or {}).get("read_project"):
        usable_tools = [*usable_tools, *read_tools.read_tool_specs()]
    if not usable_tools:
        grant_service.release_loop(db, grant_id)
        return LoopResult(
            status="PAUSED_SCOPE",
            stop_reason="NO_CAPABILITY_IN_GRANT",
            final="本次授权没有覆盖任何可执行的准备能力；请在授权时勾选需要的范围。",
            steps=steps,
            llm=llm_info,
        )

    # --- durable driver state: resume the SAME transcript and step ledger ---
    persisted = grant_service.loop_state(loaded.grant)
    messages: list[dict[str, Any]] = list(persisted.get("messages") or [])
    if not messages:
        messages = [
            {
                "role": "user",
                "content": (
                    f"任务目标：{objective}\n"
                    "请调用已注册的工具推进；每次只做一个操作，并在收到服务端回执后再决定下一步。"
                    "目标达成或必须停下时调用 finish。"
                ),
            }
        ]
    steps = list(persisted.get("steps") or [])
    no_progress = int(persisted.get("no_progress") or 0)
    # The tool-call ledger is the authority: a resumed driver restores its
    # successful-read count from there, never from a parallel counter.
    ok_reads = sum(
        1 for item in (loaded.grant.get("tool_calls") or [])
        if item.get("status") == "OK"
        and read_tools.is_read_tool(str(item.get("capability_id") or ""))
    )
    last_block_code = persisted.get("last_block_code")
    llm_info = {**llm_info, **(persisted.get("llm_info") or {})}
    llm_info["inherited_from"] = inherited_from
    pending_action_id: str | None = persisted.get("pending_action_id")
    started_at = str(persisted.get("started_at") or "") or utcnow().replace(microsecond=0).isoformat()

    def snapshot_state() -> dict[str, Any]:
        return {
            "messages": _compress(messages),
            "steps": steps,
            "no_progress": no_progress,
            "last_block_code": last_block_code,
            "llm_info": llm_info,
            "pending_action_id": pending_action_id,
            "started_at": started_at,
        }

    def finish(result: LoopResult) -> LoopResult:
        _persist(db, grant_id, snapshot_state())
        grant_service.release_loop(db, grant_id)
        return result

    for _step_index in range(config.max_steps):
        loaded = grant_service.load(db, grant_id)
        live_status = grant_contract.grant_status(loaded.grant, now=utcnow())
        if live_status not in grant_contract.AUTHORIZED_STATUSES | {"WAITING_EXECUTOR"}:
            return finish(LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="GRANT_NOT_RUNNING",
                final="本次授权已不在可执行状态，已停止后续自动操作。",
                steps=steps,
                llm=llm_info,
            ))
        try:
            grant_service.assert_tool_call_budget(loaded.grant)
        except grant_contract.GrantError as exc:
            return finish(LoopResult(
                status="PAUSED_BUDGET", stop_reason=exc.code,
                final=exc.message, steps=steps, llm=llm_info,
            ))
        try:
            elapsed = int((utcnow() - grant_contract.parse_iso(started_at)).total_seconds())
        except (ValueError, TypeError):
            elapsed = 0
        if elapsed > config.max_wall_seconds:
            return finish(LoopResult(
                status="PAUSED_BUDGET", stop_reason="WALL_CLOCK_EXHAUSTED",
                final="本次自动执行已达到时间上限，已停止。", steps=steps, llm=llm_info,
            ))

        # --- a delegated original task is waited on by READBACK, not by a new
        # model turn and never by resubmission ---
        if pending_action_id:
            try:
                readback = action_service.read_under_grant(
                    db, settings, action_id=pending_action_id, grant_id=grant_id
                )
            except Exception as exc:  # noqa: BLE001 - an unreadable task is a named stop
                return finish(LoopResult(
                    status="PAUSED_UNKNOWN", stop_reason="ORIGINAL_TASK_UNREADABLE",
                    final=f"原任务回读失败，已保留现场：{type(exc).__name__}",
                    steps=steps, llm=llm_info,
                ))
            verification = (readback.get("observed_facts") or {}).get("verification") or {}
            receipt = {
                "status": readback.get("status"),
                "action_id": pending_action_id,
                "original_task_id": readback.get("original_task_id"),
                "observed_facts": {
                    "verification_outcome": verification.get("outcome"),
                    "task_status": verification.get("task_status") or verification.get("run_status"),
                    # The full authority evidence (targets, next_step) so a
                    # resumed model sees WHY a completed task is not verified.
                    "verification": verification,
                },
                "error_code": readback.get("error_code"),
                "message": readback.get("message"),
            }
            if _is_pending(receipt):
                _persist(db, grant_id, snapshot_state())
                grant_service.release_loop(db, grant_id)
                return LoopResult(
                    status="WAITING_EXECUTOR",
                    stop_reason="ORIGINAL_TASK_PENDING",
                    final="原执行器任务仍在推进；已接回同一任务，完成后自动继续。",
                    steps=steps, llm=llm_info,
                    usage=grant_contract.usage_of(loaded.grant),
                )
            # The readback reached a terminal conclusion: the pending binding
            # is consumed whatever the outcome. Keeping it would re-read the
            # same NEEDS_REVIEW on every resume and never give the model a turn.
            pending_action_id = None
            stop = _must_stop(receipt)
            if stop:
                # Keep the readback receipt in the transcript: a resumed model
                # must see the named next step (e.g. WORKFLOW_NOT_VALIDATED)
                # instead of guessing why the original task paused.
                steps.append(
                    {
                        "capability_id": "read_original_task",
                        "status": receipt.get("status"),
                        "outcome": receipt["observed_facts"].get("verification_outcome"),
                        "error_code": receipt.get("error_code"),
                        "message": receipt.get("message"),
                        "action_id": pending_action_id,
                        "original_task_id": receipt.get("original_task_id"),
                        "satisfied": False,
                        "reused": True,
                    }
                )
                messages.append(
                    {"role": "user",
                     "content": "原任务最新回执（只读回读，需要核对）：" + _receipt_text(receipt)}
                )
                return finish(LoopResult(
                    status=stop,
                    stop_reason=str(receipt.get("error_code")
                                    or receipt["observed_facts"].get("verification_outcome")
                                    or receipt.get("status")),
                    final="原执行器给出了需要人工核对的结果，已停止自动推进并保留现场："
                    f"{receipt.get('message') or receipt.get('status')}",
                    steps=steps, llm=llm_info,
                    usage=grant_contract.usage_of(loaded.grant),
                ))
            steps.append(
                {
                    "capability_id": "read_original_task",
                    "status": receipt.get("status"),
                    "outcome": receipt["observed_facts"].get("verification_outcome"),
                    "error_code": receipt.get("error_code"),
                    "message": receipt.get("message"),
                    "action_id": pending_action_id,
                    "original_task_id": receipt.get("original_task_id"),
                    "satisfied": _satisfied(receipt),
                    "reused": True,
                }
            )
            messages.append(
                {"role": "user",
                 "content": "原任务最新回执（只读回读）：" + _receipt_text(receipt)}
            )
            pending_action_id = None
            no_progress = 0
            last_block_code = None
            if live_status == "WAITING_EXECUTOR":
                grant_service.set_status(db, grant_id, "RUNNING")
            _persist(db, grant_id, snapshot_state())
            continue

        if live_status == "WAITING_EXECUTOR":
            grant_service.set_status(db, grant_id, "RUNNING")
        elif live_status == "AUTHORIZED":
            grant_service.set_status(db, grant_id, "RUNNING")

        state = _state_summary(objective=objective, grant=loaded.grant, steps=steps)
        try:
            turn = _next_turn(
                db,
                gateway,
                system=_copilot_system_prompt(),
                messages=_compress(messages),
                tools=usable_tools,
                state=state,
                llm_info=llm_info,
                max_completion_tokens=max_completion_tokens,
            )
        except LLMNotConfigured as exc:
            return finish(LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="LLM_NOT_CONFIGURED",
                final=f"当前没有可用的文本模型，无法继续自动执行：{exc}",
                steps=steps,
                llm={**llm_info, "error": "LLM_NOT_CONFIGURED"},
            ))
        if turn is None:
            return finish(LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="LLM_UNAVAILABLE",
                final="当前没有可用的文本模型，无法继续自动执行；请先在设置中配置模型。",
                steps=steps,
                llm=llm_info,
            ))
        llm_info["turns"] = int(llm_info.get("turns") or 0) + 1
        llm_info["model"] = turn.model or llm_info.get("model")
        if turn.native:
            llm_info["native_tool_calling"] = True

        if not turn.wants_tools:
            final_text = turn.text or ""
            # The model stopped.  Whether the task *is* done is the server's
            # call, from the ORIGINAL authorities for each milestone the
            # objective requires — not from one satisfied sub-action.
            if not any(step.get("satisfied") for step in steps):
                # A model that NARRATES a tool call in prose ("调用 X，参数…")
                # instead of emitting it has not finished the task. When the
                # task already produced successful reads, give it one bounded
                # corrective continuation rather than pausing mid-investigation;
                # repeated narration still ends in the named pause.
                if ok_reads > 0 and no_progress < config.max_no_progress - 1:
                    no_progress += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": "你刚才没有发出工具调用就结束了，但目标尚未达成。"
                            "请继续调用工具推进（先只读调查弄清事实，再做写操作）；"
                            "不要只用文字叙述你要调用的工具。",
                        }
                    )
                    _persist(db, grant_id, snapshot_state())
                    continue
                return finish(LoopResult(
                    status="PAUSED_USER_DECISION",
                    stop_reason="NO_AUTHORITATIVE_EVIDENCE",
                    final=(
                        final_text
                        or "AI 认为可以结束，但没有产生任何可核验的原任务回执；"
                        "为避免误报完成，已停在待确认状态。"
                    ),
                    steps=steps,
                    llm=llm_info,
                    usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
                ))
            from shine.copilot import objective_verify

            loaded_now = grant_service.load(db, grant_id)
            verification = objective_verify.verify(
                db, settings, grant=loaded_now.grant, steps=steps
            )
            grant_service.save_objective_verification(db, grant_id, verification)
            if verification["satisfied"]:
                return finish(LoopResult(
                    status="SUCCEEDED",
                    stop_reason="MODEL_FINISHED_WITH_EVIDENCE",
                    final=final_text or "目标已在本机权威回执中达成。",
                    steps=steps,
                    llm=llm_info,
                    usage=grant_contract.usage_of(loaded_now.grant),
                    verification=verification,
                ))
            missing_labels = "、".join(verification["missing"]) or "未知里程碑"
            return finish(LoopResult(
                status="PAUSED_USER_DECISION",
                stop_reason="OBJECTIVE_NOT_VERIFIED",
                final=(
                    "已有原任务回执，但目标要求的权威证据尚未全部达成："
                    f"{missing_labels}。已停在待确认状态，不宣布完成。"
                ),
                steps=steps,
                llm=llm_info,
                usage=grant_contract.usage_of(loaded_now.grant),
                verification=verification,
            ))

        call = turn.tool_calls[0]
        tool_call_id = f"{grant_id}:{len(steps) + 1}:{call.id}"[:96]

        # --- read-only investigation: no SupportAction, no executor ---
        if read_tools.is_read_tool(call.name):
            try:
                receipt = read_tools.execute(
                    db, settings, name=call.name,
                    arguments=dict(call.arguments or {}), grant=loaded.grant,
                )
                receipt["tool_call_id"] = tool_call_id
                receipt["grant_id"] = grant_id
                grant_service.record_tool_call(
                    db, grant_id, tool_call_id=tool_call_id,
                    capability_id=call.name, status="OK",
                )
                ok_reads += 1
                no_progress = 0
                last_block_code = None
            except grant_contract.GrantError as exc:
                receipt = {
                    "status": "REFUSED", "error_code": exc.code,
                    "message": exc.message, "observed_facts": {},
                }
                grant_service.record_tool_call(
                    db, grant_id, tool_call_id=tool_call_id,
                    capability_id=call.name, status="REFUSED",
                )
                no_progress += 1
                if no_progress >= config.max_no_progress:
                    return finish(LoopResult(
                        status="FAILED", stop_reason=exc.code,
                        final=f"连续 {no_progress} 次没有取得进展，已停止：{exc.message}",
                        steps=steps, llm=llm_info,
                    ))
            messages.append(
                {"role": "assistant", "content": f"调用 {call.name}，参数 {call.arguments}"}
            )
            messages.append(
                {"role": "user", "content": "服务端回执（只读调查）：" + _receipt_text(receipt)}
            )
            _persist(db, grant_id, snapshot_state())
            continue

        if call.name not in spec_by_name:
            # A tool the model invented is a refusal, not an attempt.
            steps.append(
                {
                    "capability_id": call.name,
                    "status": "REFUSED",
                    "outcome": "UNKNOWN_TOOL",
                    "error_code": "TOOL_NOT_DECLARED",
                    "message": "模型提出了未注册的工具，已拒绝。",
                    "satisfied": False,
                }
            )
            no_progress += 1
            if no_progress >= config.max_no_progress:
                return finish(LoopResult(
                    status="FAILED", stop_reason="TOOL_NOT_DECLARED",
                    final="模型反复提出未注册的工具，已停止。", steps=steps, llm=llm_info,
                ))
            messages.append({"role": "assistant", "content": f"（请求了未注册工具 {call.name}）"})
            messages.append(
                {
                    "role": "user",
                    "content": "该工具未注册，不能执行。请只使用已提供的工具，或调用 finish。",
                }
            )
            _persist(db, grant_id, snapshot_state())
            continue

        receipt = action_service.execute_under_grant(
            db,
            settings,
            grant_id=grant_id,
            capability_id=call.name,
            params=dict(call.arguments or {}),
            tool_call_id=tool_call_id,
            actor=actor,
            session_id=session_id,
            expected_target=_expected_from(call.arguments),
            app=app,
            gateway=gateway,
        )
        outcome = str((receipt.get("observed_facts") or {}).get("verification_outcome") or "")
        steps.append(
            {
                "capability_id": call.name,
                "status": receipt.get("status"),
                "outcome": outcome,
                "error_code": receipt.get("error_code"),
                "message": receipt.get("message"),
                "action_id": receipt.get("action_id"),
                "original_task_id": receipt.get("original_task_id"),
                "satisfied": _satisfied(receipt),
                "reused": bool((receipt.get("observed_facts") or {}).get("reused_existing_action")),
            }
        )

        stop = _must_stop(receipt)
        if stop:
            return finish(LoopResult(
                status=stop,
                stop_reason=str(receipt.get("error_code") or outcome or receipt.get("status")),
                final=(
                    "原执行器给出了需要人工核对的结果，已停止自动推进并保留现场："
                    f"{receipt.get('message') or receipt.get('status')}"
                ),
                steps=steps,
                llm=llm_info,
                usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
            ))

        code = receipt.get("error_code")
        if not _satisfied(receipt):
            if code and code == last_block_code:
                no_progress += 1
            else:
                no_progress = 1 if code else 0
            last_block_code = code
            if no_progress >= config.max_no_progress:
                return finish(LoopResult(
                    status="FAILED" if code else "PAUSED_USER_DECISION",
                    stop_reason=str(code or "NO_PROGRESS"),
                    final=f"连续 {no_progress} 次没有取得进展，已停止：{receipt.get('message') or code}",
                    steps=steps, llm=llm_info,
                ))
        else:
            no_progress = 0
            last_block_code = None

        messages.append(
            {
                "role": "assistant",
                "content": f"调用 {call.name}，参数 {call.arguments}",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": "服务端回执（原执行者事实）："
                + _receipt_text(receipt),
            }
        )

        # A delegated original task owns the next while: persist the pending
        # binding and yield.  The driver (Worker tick or a renewed click)
        # reads it back; nothing is resubmitted.
        if _is_pending(receipt) and receipt.get("action_id"):
            pending_action_id = str(receipt["action_id"])
            _persist(db, grant_id, snapshot_state())
            grant_service.set_status(db, grant_id, "WAITING_EXECUTOR")
            grant_service.release_loop(db, grant_id)
            return LoopResult(
                status="WAITING_EXECUTOR",
                stop_reason="ORIGINAL_TASK_PENDING",
                final="已交给原执行器持久推进；关闭页面或重启不会中断，完成后自动继续。",
                steps=steps, llm=llm_info,
                usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
            )
        _persist(db, grant_id, snapshot_state())

    return finish(LoopResult(
        status="PAUSED_BUDGET",
        stop_reason="STEP_BUDGET_EXHAUSTED",
        final="本次自动执行已达到步数上限，已停止并保留全部已产生的回执。",
        steps=steps,
        llm=llm_info,
        usage=grant_contract.usage_of(grant_service.load(db, grant_id).grant),
    ))


def _expected_from(arguments: dict[str, Any]) -> dict[str, Any]:
    """Only declared expectation keys travel; nothing else is inferred."""
    expected: dict[str, Any] = {}
    for key in ("installation_id", "target_identity"):
        value = (arguments or {}).get(key)
        if isinstance(value, str) and value:
            expected[key] = value
    return expected


def _receipt_text(receipt: dict[str, Any]) -> str:
    facts = receipt.get("observed_facts") or {}
    import json

    return json.dumps(
        {
            "status": receipt.get("status"),
            "action_id": receipt.get("action_id"),
            "original_task_id": receipt.get("original_task_id"),
            "error_code": receipt.get("error_code"),
            "message": receipt.get("message"),
            "facts": facts,
            "next_available_actions": receipt.get("next_available_actions"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )[:2000]


def _copilot_system_prompt() -> str:
    return (
        "你是 Shine Video Studio 的本机 AI 管家，正在执行一次已经获得用户授权的任务。\n"
        "规则：\n"
        "1. 只能调用已提供的工具；不得编造工具名、参数或文件路径。\n"
        "2. 环境是否就绪只以 /api/v1/setup/verdict 为准；工作流是否可生产只以原验证回执为准。\n"
        "3. 不得修改源码、节点源码或内置模板，不得执行命令、脚本、pip 或 git。\n"
        "4. 收到 UNKNOWN / 需要核对 的回执时必须停止，不得重试提交。\n"
        "5. 目标达成后调用 finish 并简述依据；没有原任务回执时不要声称完成。\n"
        "6. 一次只提出一个工具调用。必须通过工具调用字段发出调用；"
        "绝不要在回答正文里叙述「调用 X，参数…」来代替真正的工具调用。\n"
        "7. 先用只读工具（inspect_environment / read_resource_inventory / read_node_inventory）"
        "弄清事实，再决定写操作；已交给后台的任务用 read_original_task 回读，不要重复提交。\n"
        "8. 工具参数里的 project_id / installation_id 必须使用状态摘要 scope 里给出的真实 ID；"
        "没有给出就不要传，绝不编造简称。\n"
    )


def _next_turn(
    db,
    gateway,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec],
    state: dict[str, Any],
    llm_info: dict[str, Any],
    max_completion_tokens: int,
) -> ToolTurn | None:
    """One model turn: native tool calling when available, validated intent otherwise."""
    if gateway is None:
        return None
    # Native first.  A provider that cannot do it returns None, and the
    # degradation is recorded once per task rather than per turn.
    if llm_info.get("native_tool_calling") is not False:
        try:
            turn = gateway.complete_tools_for_role(
                db,
                "support_agent",
                system=system,
                messages=_native_messages(messages),
                tools=tools,
                options={"max_completion_tokens": max_completion_tokens},
            )
        except LLMNotConfigured:
            raise
        except LLMError:
            turn = None
        if turn is not None:
            return turn
        llm_info["native_tool_calling"] = False
        llm_info["degraded_reason"] = "PROVIDER_WITHOUT_NATIVE_TOOL_CALLS"

    # Structured fallback through the ordinary, schema-validated path.
    try:
        response = gateway.complete_for_role(
            db,
            "support_agent",
            system=system + "\n\n" + fallback_system_prompt(tools),
            user=fallback_user_prompt(state),
            schema=FALLBACK_SCHEMA,
            budget={"completion_cap": max_completion_tokens},
        )
    except LLMNotConfigured:
        raise
    except LLMError as exc:
        llm_info["degraded_error"] = exc.code
        return None
    reply = response.parsed
    if reply is None:
        return None
    try:
        turn = parse_fallback_reply(reply, tool_names={spec.name for spec in tools})
    except LLMError as exc:
        llm_info["degraded_error"] = exc.code
        return None
    turn.model = response.model
    turn.latency_ms = response.latency_ms
    turn.usage = response.usage
    return turn


def _native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript is already in a protocol-neutral shape (role/content)."""
    return [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in messages
    ]

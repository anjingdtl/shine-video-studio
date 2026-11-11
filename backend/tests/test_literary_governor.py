"""The Call Governor: one compiled request, one budget, one bounded repair."""

from __future__ import annotations

import contextlib

import pytest

from shine.literary.adapters import SceneDraft
from shine.literary.budget import plan_call_budget
from shine.literary.governor import CallGovernor, UnitBudget, accepts_precompiled
from shine.literary.requests import compile_request
from shine.llm.base import (
    LLMFormatError,
    LLMResponse,
    LLMTimeout,
    ProviderConfig,
)

pytestmark = pytest.mark.unit


def _config(**overrides) -> ProviderConfig:
    base = dict(
        profile_id="p", name="n", kind="openai_compatible",
        base_url="https://api.example.com/v1", model="test-model", api_key="k",
        timeout_s=30, max_retries=1, temperature=1.0,
        generation_options={}, context_window_tokens=200_000,
        max_output_tokens=32_000, context_window_source="provider_declared",
    )
    base.update(overrides)
    return ProviderConfig(**base)


class ScriptedGateway:
    """A gateway that records exactly what it was asked to send."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent: list[dict] = []

    def config_for(self, profile):
        return profile

    def profile_for_role(self, db, role):
        return _config()

    def complete_for_role(self, db, role, *, system="", user="", schema=None,
                          on_progress=None, task_category="default", precompiled=None,
                          budget=None):
        # Mirror the real gateway: a compiled request is sent verbatim.
        if precompiled is not None:
            system, user, schema = precompiled.system, precompiled.user, precompiled.schema
        self.sent.append({"system": system, "user": user, "schema": schema,
                          "precompiled": precompiled, "budget": budget})
        for phase in ("WAITING_RESOURCE", "PREPARING_PROVIDER", "WAITING_PROVIDER"):
            if on_progress:
                on_progress(phase)
        answer = self.answers.pop(0) if self.answers else {"text": "默认正文"}
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, LLMResponse):
            return answer
        return LLMResponse(answer.get("text", ""), answer.get("parsed"), "test", 1,
                           usage=answer.get("usage") or {"completion_tokens": 100})


def _governor(answers, *, config=None, budget=None):
    gateway = ScriptedGateway(answers)
    return gateway, CallGovernor(
        gateway, config or _config(), role="four_stage_scene_script_run",
        unit_id="scene:v1:s1", budget=budget)


BASE_SYSTEM = "你是编剧。只输出 JSON。"
BASE_CONTEXT = {"section": {"section_id": "s1"}, "source_text": "德明出海。"}


def _schema_mismatch(text: str) -> LLMFormatError:
    return LLMFormatError("Model JSON does not match the required schema",
                          details={"status": 200, "path": "text",
                                   "validator": "minLength", "provider_text": text})


# ---------------------------------------------------------------------------
# Compilation and budget
# ---------------------------------------------------------------------------


def test_the_sent_user_message_is_exactly_the_compiled_one():
    gateway, governor = _governor([{"text": "正文", "continuity_out": "离开"}])
    governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                     schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                     purpose="scene", session_factory=contextlib.nullcontext)
    sent = gateway.sent[0]
    compiled = compile_request(system=BASE_SYSTEM,
                               user=sent["user"].split("\n\nReturn one JSON object")[0],
                               schema=SceneDraft.model_json_schema())
    # The schema is inside the user message already, and never twice.
    assert sent["user"].count("this JSON schema") == 1
    assert sent["precompiled"].user == sent["user"]
    assert sent["precompiled"].input_tokens == compiled.input_tokens


def test_the_request_is_not_sent_when_it_cannot_fit_the_window():
    config = _config(context_window_tokens=4_000, max_output_tokens=8_000)
    gateway, governor = _governor([{"text": "不该发出"}], config=config)
    outcome = governor.execute(
        system=BASE_SYSTEM, context={"source_text": "浪" * 20_000},
        schema=SceneDraft.model_json_schema(), contract=SceneDraft,
        purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.error_code == "LLM_CREATIVE_CONTEXT_OVERFLOW"
    assert outcome.external_outcome == "NOT_SENT"
    assert gateway.sent == []
    assert outcome.findings[0].retryable is True


def test_the_output_request_never_exceeds_the_sixty_percent_cap():
    budget = plan_call_budget(_config(max_output_tokens=32_000),
                              compile_request(system="s", user="u", schema={"type": "object"}),
                              purpose="scene", visible_goal_tokens=30_000)
    assert budget.output_cap_tokens == 19_200
    assert budget.output_request_tokens <= budget.output_cap_tokens


def test_a_small_task_asks_for_a_small_completion_not_the_ceiling():
    request = compile_request(system="s", user="u" * 900, schema={"type": "object"})
    budget = plan_call_budget(_config(), request, purpose="observation")
    assert budget.output_request_tokens < budget.output_cap_tokens
    assert "REASONING_RESERVE_CONSERVATIVE" in budget.warnings


def test_an_undeclared_window_is_planned_conservatively_and_never_called_1m():
    config = _config(context_window_tokens=None, context_window_source="none")
    budget = plan_call_budget(config, compile_request(system="s", user="u", schema=None))
    assert budget.window_verified is False
    assert budget.context_window_tokens != 1_000_000
    assert "CONTEXT_WINDOW_UNVERIFIED" in budget.warnings


# ---------------------------------------------------------------------------
# Answer preservation
# ---------------------------------------------------------------------------


def test_a_complete_answer_inside_a_wrong_envelope_is_read_locally(monkeypatch):
    """One wrong optional field must not buy a second request."""
    gateway, governor = _governor([
        _schema_mismatch('{"text": "德明站在船头。\\n德明：起风了。", "continuity_out": "登船"}'),
    ])
    outcome = governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                               schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                               purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.ok is True
    assert outcome.parsed["text"].startswith("德明站在船头")
    assert len(gateway.sent) == 1, "a locally readable answer is never requested twice"
    assert outcome.findings[0].reason_code == "SCHEMA_LOCAL_REPAIR"


def test_text_is_kept_when_the_contract_really_cannot_be_satisfied():
    gateway, governor = _governor([
        LLMFormatError("schema", details={"status": 200, "provider_text": '{"未闭合": '}),
        LLMFormatError("schema", details={"status": 200, "provider_text": '{"仍未闭合": '}),
    ])
    outcome = governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                               schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                               purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.ok is False
    assert outcome.error_code == "LLM_FORMAT"
    assert outcome.findings[0].severity in {"LOCAL_REVIEW", "HARD_BLOCK"}
    assert len(gateway.sent) == 2, "one correction, never a loop"


def test_a_single_correction_is_the_whole_repair_budget():
    gateway, governor = _governor([
        LLMFormatError("schema", details={"status": 200, "provider_text": "无正文"}),
        LLMFormatError("schema", details={"status": 200, "provider_text": "仍然无正文"}),
    ], budget=UnitBudget(max_calls=10, max_repair_rounds=1))
    governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                     schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                     purpose="scene", session_factory=contextlib.nullcontext)
    assert len(gateway.sent) == 2


def test_an_unknown_outcome_is_never_corrected_or_replayed():
    gateway, governor = _governor([
        LLMTimeout("request timed out", details={"external_outcome": "UNKNOWN"}),
    ])
    outcome = governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                               schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                               purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.uncertain is True
    assert outcome.error_code == "LLM_TIMEOUT"
    assert len(gateway.sent) == 1, "an unknown answer must never be requested again"
    assert outcome.findings[0].severity == "HARD_BLOCK"


def test_the_unit_call_budget_stops_the_lane_before_the_send():
    gateway, governor = _governor([{"text": "正文"}],
                                  budget=UnitBudget(max_calls=1, max_repair_rounds=1))
    governor.budget.calls = 1
    outcome = governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                               schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                               purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.error_code == "LLM_UNIT_CALL_BUDGET_EXHAUSTED"
    assert gateway.sent == []


def test_a_throttled_provider_is_a_named_refusal_not_an_unknown():
    from shine.llm.base import LLMRateLimit
    gateway, governor = _governor([
        LLMRateLimit("slow down", details={"status": 429}),
    ])
    outcome = governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                               schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                               purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.error_code == "LLM_RATE_LIMIT"
    assert outcome.uncertain is False
    assert outcome.findings[0].retryable is True


def test_the_attempt_record_carries_the_fingerprint_and_the_budget():
    records = []
    gateway = ScriptedGateway([{"text": "正文"}])
    governor = CallGovernor(gateway, _config(), role="r", unit_id="u",
                            record_attempt=records.append)
    governor.execute(system=BASE_SYSTEM, context=BASE_CONTEXT,
                     schema=SceneDraft.model_json_schema(), contract=SceneDraft,
                     purpose="scene", session_factory=contextlib.nullcontext)
    assert records[0]["request_fingerprint"]
    assert records[0]["output_request_tokens"] > 0
    assert records[0]["unit_budget"]["calls"] == 1


def test_the_gateway_double_detection_keeps_legacy_doubles_working():
    class Legacy:
        def complete_for_role(self, db, role, *, system, user, schema): ...

    assert accepts_precompiled(Legacy().complete_for_role) is False
    assert accepts_precompiled(ScriptedGateway([]).complete_for_role) is True

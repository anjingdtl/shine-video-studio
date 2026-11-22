"""P2: streamed answers, broken streams, the call ledger and reconciliation."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import select

from shine.literary import ledger
from shine.literary.models import LLMCallAttempt
from shine.llm.base import ProviderConfig
from shine.llm.openai_compatible import OpenAICompatibleProvider
from tests.fake_http import FakeOpenAIServer

pytestmark = pytest.mark.integration


def _config(server: FakeOpenAIServer, **overrides) -> ProviderConfig:
    base = dict(
        profile_id="p", name="n", kind="openai_compatible",
        base_url=f"{server.base_url}/v1", model="test-model", api_key="test-key",
        timeout_s=5, max_retries=0, temperature=0.2, generation_options={},
        context_window_tokens=100_000, max_output_tokens=8_000,
        context_window_source="provider_declared", streaming=True,
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_a_streamed_answer_carries_its_event_evidence():
    server = FakeOpenAIServer().start()
    try:
        server.stream_mode = "ok"
        server.response_content = '{"text": "德明出海了。"}'
        provider = OpenAICompatibleProvider(_config(server))
        response = provider.complete([], json_mode=True)
    finally:
        server.stop()
    assert response.parsed == {"text": "德明出海了。"}
    assert response.raw_meta["streamed"] is True
    assert response.raw_meta["first_event_ms"] is not None
    assert response.raw_meta["last_event_ms"] >= response.raw_meta["first_event_ms"]


def test_an_acknowledged_stream_without_events_stays_unknown():
    """A 200 response without events does not prove the request was not processed."""
    from shine.llm.base import LLMConnectionError
    server = FakeOpenAIServer().start()
    try:
        server.stream_mode = "empty"
        server.response_content = '{"text": "德明回来了。"}'
        provider = OpenAICompatibleProvider(_config(server))
        with pytest.raises(LLMConnectionError) as error:
            provider.complete([], json_mode=True)
    finally:
        server.stop()
    assert error.value.details["external_outcome"] == "UNKNOWN"
    assert len(server.requests) == 1


def test_a_broken_stream_keeps_the_visible_text_and_stays_unknown():
    from shine.llm.base import LLMConnectionError

    server = FakeOpenAIServer().start()
    try:
        server.stream_mode = "broken"
        server.response_content = '{"text": "德明站在船头，风很大。"}'
        provider = OpenAICompatibleProvider(_config(server))
        with pytest.raises(LLMConnectionError) as error:
            provider.complete([], json_mode=True)
    finally:
        server.stop()
    assert error.value.details["external_outcome"] == "UNKNOWN"
    assert error.value.details["provider_text"].startswith('{"text"')
    assert len(server.requests) == 1, "a broken answer is never replayed"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def test_an_attempt_is_recorded_before_and_after_the_call(api):
    _client, factory, _settings = api
    from tests.test_literary_low_blocking import _prepared  # reuse the fixture builder

    pid, plan, volume, sections, settings = _prepared(api)
    from shine.director import pipeline
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore

    def responder(messages, schema):
        return {"text": "德明：走。", "continuity_out": "离开"}

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    pipeline.run_pending_pipeline_task(factory, settings, "w-ledger", gateway)

    with factory() as db:
        rows = db.scalars(select(LLMCallAttempt).where(
            LLMCallAttempt.project_id == pid)).all()
    assert rows, "a physical call must leave a ledger row"
    assert all(row.status == "FINISHED" for row in rows)
    assert all(row.request_fingerprint for row in rows)
    assert any(row.external_outcome == "RESPONSE_RECEIVED" for row in rows)
    assert all(row.output_budget for row in rows)


def test_an_unknown_call_is_listed_and_only_a_person_may_free_it(api):
    _client, factory, _settings = api
    from tests.conftest import create_project

    pid = create_project(_client, "对账")
    with factory.begin() as db:
        ledger.record(db, project_id=pid, attempt={
            "phase": "SENDING", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
        })
        ledger.record(db, project_id=pid, attempt={
            "phase": "FINISHED", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
            "external_outcome": "UNKNOWN", "error_code": "LLM_TIMEOUT",
        })
    with factory() as db:
        unknown = ledger.unknown_attempts(db, pid)
        assert len(unknown) == 1
        assert unknown[0].external_outcome == "UNKNOWN"
        attempt_id = unknown[0].id
    with factory.begin() as db:
        decision = ledger.reconcile(db, attempt_id, outcome="safe_to_resend",
                                    evidence="服务商后台没有这次请求的记录")
        assert decision["previous_external_outcome"] == "UNKNOWN"
    with factory() as db:
        assert ledger.unknown_attempts(db, pid) == []
        assert ledger.public(db.get(LLMCallAttempt, attempt_id))["status"] == "RECONCILED"
        assert db.get(LLMCallAttempt, attempt_id).output_budget["reconciliation"]["outcome"] \
            == "safe_to_resend"


def test_an_authorized_retry_does_not_erase_the_failed_attempt(api):
    """The unknown outcome of the old request must survive the new request.

    A person who authorizes a new request for an unknown outcome gets a *new*
    physical request.  The two share a fingerprint, so a ledger that keyed on
    the fingerprint alone wrote the new outcome over the old evidence: the row
    then claimed the provider had answered while still carrying the old
    failure code, and the unknown-outcome record - the one thing a later reader
    needs - was gone.
    """
    _client, factory, _settings = api
    from tests.conftest import create_project

    pid = create_project(_client, "重试")
    with factory.begin() as db:
        ledger.record(db, project_id=pid, attempt={
            "phase": "SENDING", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
        }, task_id="task-old")
        ledger.record(db, project_id=pid, attempt={
            "phase": "FINISHED", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
            "external_outcome": "UNKNOWN", "error_code": "LLM_CONNECTION",
        }, task_id="task-old")
    # The authorized retry is a new physical request under a new task.
    with factory.begin() as db:
        ledger.record(db, project_id=pid, attempt={
            "phase": "SENDING", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
        }, task_id="task-new")
        ledger.record(db, project_id=pid, attempt={
            "phase": "FINISHED", "unit_id": "scene:v1:s1", "role": "r", "purpose": "scene",
            "attempt_no": 1, "request_fingerprint": "f" * 64,
            "external_outcome": "RESPONSE_RECEIVED", "http_status": 200,
            "completion_tokens": 4115,
        }, task_id="task-new")

    with factory() as db:
        rows = db.scalars(select(LLMCallAttempt).where(
            LLMCallAttempt.project_id == pid).order_by(LLMCallAttempt.started_at)).all()
        assert len(rows) == 2, "two physical requests, two rows"
        old, new = rows
        assert old.external_outcome == "UNKNOWN"
        assert old.error_code == "LLM_CONNECTION"
        assert new.external_outcome == "RESPONSE_RECEIVED"
        assert new.error_code is None, "a stale failure code must not survive on a answered row"
        assert new.output_tokens == 4115
        # The unknown attempt is still listed, so it can still be reconciled.
        assert [row.id for row in ledger.unknown_attempts(db, pid)] == [old.id]


def test_an_unknown_stream_is_never_asked_again_by_the_governor():
    from shine.literary.adapters import SceneDraft
    from shine.literary.governor import CallGovernor

    class BrokenStream:
        def __init__(self):
            self.calls = 0

        def complete_for_role(self, db, role, *, system="", user="", schema=None,
                              on_progress=None, task_category="default", precompiled=None,
                              budget=None):
            self.calls += 1
            from shine.llm.base import LLMConnectionError
            raise LLMConnectionError("stream broke", details={
                "external_outcome": "UNKNOWN", "provider_text": '{"text": "半场戏"'})

    gateway = BrokenStream()
    governor = CallGovernor(gateway, ProviderConfig(
        profile_id="p", name="n", kind="openai_compatible", base_url="http://127.0.0.1:1/v1",
        model="m", api_key=None, context_window_tokens=100_000, max_output_tokens=8_000,
    ), role="r", unit_id="u")
    outcome = governor.execute(
        system="s", context={"a": 1}, schema=SceneDraft.model_json_schema(),
        contract=SceneDraft, purpose="scene", session_factory=contextlib.nullcontext)
    assert outcome.uncertain is True
    assert gateway.calls == 1
    assert outcome.text, "the visible prefix is kept for diagnosis"

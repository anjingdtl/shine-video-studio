"""New wire coverage cannot be inferred from an overall positive verdict."""

import hashlib
import json

import httpx
import pytest

from shine.llm.base import ProviderConfig
from shine.quality.semantic import LocalVisionReviewer, ReceivedReviewError
from tests.review_fixtures import fixture_style_input_checks
from tests.test_semantic_qc import context


@pytest.mark.parametrize("case", ["missing", "incomplete", "duplicate", "conflict-passed",
                                 "uncertain-passed", "conflict-no-finding", "unknown-field",
                                 "consistent", "conflict-recorded"])
def test_wire_requires_complete_style_input_comparison(tmp_path, monkeypatch, case):
    data = dict(reviewer="fixture", is_mock=False, input_sha256="a" * 64,
                findings=[], verdict="passed", overall_confidence=1,
                raw_summary="Fixture only", style_input_checks=fixture_style_input_checks())
    if case == "missing":
        data.pop("style_input_checks")
    elif case == "incomplete":
        data["style_input_checks"].pop(1)  # The actual missed blocking criterion.
    elif case == "duplicate":
        data["style_input_checks"][1] = data["style_input_checks"][0]
    elif case == "unknown-field":
        data["style_input_checks"][1]["field"] = "unfrozen_override"
    elif case.startswith(("conflict", "uncertain")):
        data["style_input_checks"][1].update(
            status="uncertain" if case.startswith("uncertain") else "conflict",
            detail="Frozen blocking requires sitting, ShotSpec explicitly requests walking",
        )
        if case in {"conflict-no-finding", "conflict-recorded"}:
            data["verdict"] = "needs_review"
        if case == "conflict-recorded":
            data["findings"] = [dict(code="STYLE_INPUT_SHOT_BLOCKING", category="style",
                                      severity="uncertain", confidence=1,
                                      message="Sitting versus walking input conflict")]
    response = httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                       "message": {"content": json.dumps(data)}}]})
    original = httpx.Client
    requests = []

    def respond(request):
        requests.append(request)
        return response

    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig(profile_id="p", name="p", kind="openai_compatible",
                         base_url="http://127.0.0.1:8116/v1", model="fixture", api_key=None)
    if case in {"consistent", "conflict-recorded"}:
        result = LocalVisionReviewer(cfg).review(context(tmp_path))
        assert len(result.style_input_checks) == 7
        assert result.verdict == data["verdict"]
    else:
        with pytest.raises(ReceivedReviewError, match="VLM_RESPONSE_CONTRACT_INVALID") as error:
            LocalVisionReviewer(cfg).review(context(tmp_path))
        assert error.value.response_sha256 == hashlib.sha256(response.content).hexdigest()
    assert len(requests) == 1  # A received contract failure never resubmits inference.


def test_missing_style_coverage_is_failed_received_call_not_unknown_or_passed(api, tmp_path, monkeypatch):
    from sqlalchemy import select

    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.models import ResourceLease
    from shine.models import AgentTaskRecord
    from shine.schemas import SemanticReviewResult
    from tests.conftest import create_project

    client, factory, _ = api
    project = create_project(client)
    old_result = dict(reviewer="old", is_mock=False, input_sha256="a" * 64,
                      findings=[], verdict="passed", overall_confidence=1)
    # Immutable older records still load, but cannot impersonate new wire coverage.
    assert SemanticReviewResult.model_validate(old_result).style_input_checks is None
    response = httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                       "message": {"content": json.dumps(old_result)}}]})
    original, posts = httpx.Client, []

    def respond(request):
        if request.url.path == "/free":
            return httpx.Response(200, json={"released": True, "allocated_bytes": 0})
        posts.append(request)
        return response

    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig(profile_id="p", name="p", kind="openai_compatible",
                         base_url="http://127.0.0.1:8116/v1", model="fixture", api_key=None)
    with pytest.raises(ReceivedReviewError):
        TrackedProvider(LocalVisionReviewer(cfg), factory, project).review(context(tmp_path))
    assert len(posts) == 1
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:VLM:review"))
        assert task.status == "FAILED"
        assert db.get(ResourceLease, task.trace["resource_id"]).status == "RELEASED"
        assert task.trace["received_response"]["sha256"] == hashlib.sha256(response.content).hexdigest()
        assert task.trace["received_response"]["retryable"] is False

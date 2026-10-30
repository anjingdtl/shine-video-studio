"""New wire coverage cannot be inferred from an overall positive verdict."""

import hashlib
import json

import httpx
import pytest

from shine.llm.base import ProviderConfig
from shine.quality.semantic import LocalVisionReviewer, ReceivedReviewError
from shine.schemas import StyleBible
from tests.review_fixtures import fixture_style_input_checks
from tests.test_semantic_qc import context


@pytest.mark.parametrize("case", ["missing", "incomplete", "duplicate", "conflict-passed",
                                 "uncertain-passed", "conflict-no-finding", "unknown-field",
                                 "consistent", "conflict-recorded", "long-detail-secret", "unexpected-secret-key"])
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
    elif case == "long-detail-secret":
        data["style_input_checks"][1]["detail"] = "sk-private-provider-value" * 30
    elif case == "unexpected-secret-key":
        data["sk-private-provider-key"] = "sk-private-provider-value"
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
    elif case in {"missing", "incomplete", "duplicate", "conflict-passed", "uncertain-passed",
                  "conflict-no-finding"}:
        # The reviewer's own words are bound into usable evidence instead of
        # being thrown away: one round, one record, never a second answer.
        result = LocalVisionReviewer(cfg).review(context(tmp_path))
        assert result.verdict == "needs_review"
        assert len(result.style_input_checks) == 7
        assert {check.field for check in result.style_input_checks} == set(StyleBible.model_fields)
        assert "sk-private" not in json.dumps(result.model_dump(mode="json"))
        # The field the reviewer did not assess or declared is never passed,
        # and it is reported as a style finding.
        assert any(check.status != "consistent" for check in result.style_input_checks)
        if case in {"missing", "incomplete", "duplicate"}:
            assert any(check.status == "uncertain" for check in result.style_input_checks)
        assert any(finding.code == "STYLE_INPUT_SHOT_BLOCKING" for finding in result.findings)
        assert any(finding.category == "style" for finding in result.findings)
    else:
        # The answer does not even satisfy the schema: it is a definitive
        # failure of the review, never an unknown outcome and never a reason to
        # run the reviewer again.
        with pytest.raises(ReceivedReviewError, match="VLM_RESPONSE_CONTRACT_INVALID") as error:
            LocalVisionReviewer(cfg).review(context(tmp_path))
        assert error.value.response_sha256 == hashlib.sha256(response.content).hexdigest()
        diagnostic = error.value.validation
        assert diagnostic and "sk-private" not in json.dumps(diagnostic)
        assert diagnostic["code"] == "SCHEMA_VALIDATION_FAILED"
        expected_type = {"unknown-field": "literal_error", "long-detail-secret": "string_too_long",
                         "unexpected-secret-key": "extra_forbidden"}[case]
        assert diagnostic["issues"][0]["type"] == expected_type
        if case == "unexpected-secret-key":
            assert diagnostic["issues"][0]["path"] == ["?"]
    assert len(requests) == 1  # A received contract failure never resubmits inference.


def test_missing_style_coverage_is_recorded_as_an_unestablished_review(api, tmp_path, monkeypatch):
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
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "loaded": False, "asr_loaded": False,
                                            "allocated_bytes": 0, "reserved_bytes": 0})
        if request.url.path == "/free":
            return httpx.Response(200, json={"released": True, "allocated_bytes": 0})
        posts.append(request)
        return response

    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig(profile_id="p", name="p", kind="openai_compatible",
                         base_url="http://127.0.0.1:8116/v1", model="fixture", api_key=None)
    result = TrackedProvider(LocalVisionReviewer(cfg), factory, project).review(context(tmp_path))
    assert len(posts) == 1
    # The call completed and its answer is on record, so the review says
    # plainly that nothing was established.  It is not a pass, and it is not an
    # unknown outcome either: the human review that follows decides.
    assert result.verdict == "needs_review"
    assert result.overall_confidence == 0
    assert any(finding.code.startswith("STYLE_INPUT_") for finding in result.findings)
    assert all(check.status == "uncertain" for check in result.style_input_checks)
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:VLM:review"))
        assert task.status == "COMPLETED"
        assert db.get(ResourceLease, task.trace["resource_id"]).status == "RELEASED"

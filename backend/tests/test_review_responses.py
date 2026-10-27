"""Received response failures differ from unknown outcomes; no GPU/model inference."""

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from shine.comfyops.model_calls import TrackedProvider
from shine.comfyops.models import ResourceLease
from shine.llm.base import ProviderConfig
from shine.models import AgentTaskRecord
from shine.quality.semantic import LocalVisionReviewer, ReceivedReviewError
from tests.conftest import create_project
from tests.test_semantic_qc import context


@pytest.mark.parametrize("failure", ["truncated-json", "length", "schema", "timeout", "release", "release-timeout"])
def test_response_failure_ledger_keeps_unknown_outcomes_blocked(api, tmp_path, monkeypatch, failure):
    client, factory, _ = api
    project = create_project(client)
    releases, posts = [], []
    wire = {"choices": [{"finish_reason": "length" if failure == "length" else "stop",
                         "message": {"content": "{}" if failure in {"length", "schema"} else '{"findings":['}}]}
    encoded = json.dumps(wire).encode()

    def respond(request):
        if request.url.path == "/free":
            releases.append(1)
            if failure == "release-timeout" and len(releases) == 2:
                raise httpx.ReadTimeout("Release confirmation unavailable")
            bad = failure == "release" and len(releases) == 2
            return httpx.Response(200, json={"released": not bad, "allocated_bytes": int(bad)})
        posts.append(json.loads(request.content))
        if failure == "timeout":
            raise httpx.ReadTimeout("Response not received")
        return httpx.Response(200, content=encoded)

    original = httpx.Client
    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    cfg = ProviderConfig(profile_id="fixture-profile", name="fixture", kind="openai_compatible",
                         base_url="http://127.0.0.1:8116", model="fixture-only", api_key=None)
    reviewer = TrackedProvider(LocalVisionReviewer(cfg), factory, project)
    with pytest.raises((ValueError, httpx.ReadTimeout)) as caught:
        reviewer.review(context(tmp_path))
    assert len(posts) == 1, "No automatic inference retry"
    assert posts[0]["max_tokens"] == 2048
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:VLM:review"))
        lease = db.get(ResourceLease, task.trace["resource_id"])
        assert task.trace["review_provider"]["profile_id"] == "fixture-profile"
        if failure in {"timeout", "release", "release-timeout"}:
            assert task.status == "NEEDS_REVIEW"
            assert lease.status == ("RECONCILE_REQUIRED" if failure in {"release", "release-timeout"} else "RELEASED")
            assert not isinstance(caught.value, ReceivedReviewError)
            if failure in {"release", "release-timeout"}:
                assert task.trace["received_response"]["sha256"] == hashlib.sha256(encoded).hexdigest()
                assert task.trace["received_response"]["retryable"] is False
        else:
            assert isinstance(caught.value, ReceivedReviewError)
            assert task.status == "FAILED" and lease.status == "RELEASED"
            assert task.trace["received_response"]["sha256"] == hashlib.sha256(encoded).hexdigest()
            assert task.trace["received_response"]["retryable"] is False
            assert task.trace["received_response"]["finish_reason"] == ("length" if failure == "length" else None)


def test_local_review_output_budget_is_explicit_and_bounded():
    path = Path(__file__).resolve().parents[2] / "scripts/local_review_server.py"
    spec = importlib.util.spec_from_file_location("budget_test_review_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.review_token_limit({}) == 2048
    assert module.review_token_limit({"max_tokens": 4096}) == 4096
    for value in (None, True, 127, 4097, "2048"):
        with pytest.raises(ValueError, match="REVIEW_OUTPUT_BUDGET_INVALID"):
            module.review_token_limit({"max_tokens": value})

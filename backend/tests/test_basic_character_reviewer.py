"""Wire tests use synthetic HTTP responses; they are not real model evidence."""

import json
from dataclasses import replace

import httpx
import pytest

from shine.llm.base import ProviderConfig
from shine.quality.basic_character_reviewer import BasicCharacterReviewer
from shine.quality.basic_review import CharacterReviewResponse
from shine.quality.semantic import STRUCTURED_REVIEW_PROTOCOL, ReceivedReviewError, review_schema_hash
from tests.test_semantic_qc import context


@pytest.mark.parametrize(
    "damage", [None, "wrong_schema", "mock", "missing_character", "truncated", "no_identity", "server_error",
               "string_boolean"]
)
def test_basic_character_wire_requires_complete_pinned_local_evidence(tmp_path, monkeypatch, damage):
    ctx = context(tmp_path)
    ctx = replace(ctx, shot_spec=ctx.shot_spec.model_copy(update={"characters": ["A"]}))
    config = ProviderConfig(profile_id="p", name="local", kind="openai_compatible",
                            base_url="http://127.0.0.1:8116", model="model", api_key=None)
    reviewer = BasicCharacterReviewer(config)
    observed = {"character": "A", "status": "passed", "observation": "Visible", "reference_compared": False}
    if damage == "missing_character":
        observed["character"] = "B"
    if damage == "string_boolean":
        observed["reference_compared"] = "false"
    body = {
        "model": "model", "is_mock": damage == "mock", "choices": [{
            "finish_reason": "length" if damage == "truncated" else "stop",
            "message": {"content": json.dumps({"characters": [observed]})},
        }],
        "structured_output": {"protocol": STRUCTURED_REVIEW_PROTOCOL, "decoder_sha256": "d" * 64,
                              "schema_sha256": review_schema_hash(CharacterReviewResponse.model_json_schema())},
        "executor_identity": {"schema_version": "1", "model_id": "model", "runtime_sha256": "b" * 64,
                              "files_sha256": "c" * 64, "file_count": 1, "bytes": 1},
    }
    if damage == "wrong_schema":
        body["structured_output"]["schema_sha256"] = "0" * 64
    if damage == "no_identity":
        del body["executor_identity"]
    original = httpx.Client

    def respond(request):
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["schema"] == CharacterReviewResponse.model_json_schema()
        assert "dialogue" not in json.loads(payload["messages"][1]["content"][0]["text"])
        return httpx.Response(500 if damage == "server_error" else 200, json=body)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    if damage == "server_error":
        with pytest.raises(RuntimeError, match="OUTCOME_UNKNOWN"):
            reviewer.review(ctx)
    elif damage:
        with pytest.raises(ReceivedReviewError):
            reviewer.review(ctx)
    else:
        result = reviewer.review(ctx)
        assert result.input_sha256 == "a" * 64
        assert result.response["characters"][0]["status"] == "passed"
        assert result.is_mock is False

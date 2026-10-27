"""Structured output is explicit and pinned; grammar never replaces QC validation."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from shine.comfyops.model_calls import TrackedProvider
from shine.comfyops.models import ResourceLease
from shine.llm.base import LLMCapabilityError, ProviderConfig
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.models import AgentTaskRecord
from shine.quality.semantic import (
    STRUCTURED_REVIEW_PROTOCOL,
    LocalVisionReviewer,
    ReceivedReviewError,
    review_schema_hash,
    vision_review_schema,
)
from tests.conftest import create_project
from tests.review_fixtures import fixture_style_input_checks
from tests.test_semantic_qc import context


@pytest.fixture
def executor():
    path = Path(__file__).resolve().parents[2] / "scripts/local_review_server.py"
    spec = importlib.util.spec_from_file_location("structured_review_executor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_wire_bounds_findings_and_summary_without_rewriting_historical_records():
    from shine.schemas import SemanticFinding, SemanticReviewResult

    schema = vision_review_schema()
    assert schema["$defs"]["SemanticFinding"]["properties"]["message"]["maxLength"] == 240
    assert schema["properties"]["raw_summary"]["maxLength"] == 300
    # Historical database records retain their larger stored-model limits.
    assert SemanticFinding(code="fixture", category="fixture", severity="soft", confidence=1,
                           message="x" * 1000).message == "x" * 1000
    assert SemanticReviewResult(reviewer="fixture", is_mock=True, input_sha256="a" * 64,
                                overall_confidence=1, raw_summary="x" * 2000).raw_summary == "x" * 2000


def test_additional_schema_is_accepted_only_if_explicitly_pinned(executor):
    from shine.quality.basic_review import CharacterReviewResponse

    basic = CharacterReviewResponse.model_json_schema()
    strict_pin = review_schema_hash(vision_review_schema())
    basic_pin = review_schema_hash(basic)
    body = {"response_format": {"type": "json_schema", "json_schema": {"strict": True, "schema": basic}}}
    with pytest.raises(ValueError, match="REVIEW_JSON_SCHEMA_PIN_MISMATCH"):
        executor.review_response_schema(body, [strict_pin])
    assert executor.review_response_schema(body, [strict_pin, basic_pin]) == (basic, basic_pin)


def test_prefix_parser_receives_tokenizer_alphabet_before_construction(executor, monkeypatch):
    root = ModuleType("lmformatenforcer")
    char = ModuleType("lmformatenforcer.characterlevelparser")
    integrations = ModuleType("lmformatenforcer.integrations")
    transformers = ModuleType("lmformatenforcer.integrations.transformers")
    captured = {}

    def parser(schema, *, config):
        captured.update(schema=schema, alphabet=config.alphabet)
        return object()

    root.JsonSchemaParser = parser
    char.CharacterLevelParserConfig = lambda *, alphabet: SimpleNamespace(alphabet=alphabet)
    token_data = SimpleNamespace(tokenizer_alphabet="abc中文")
    transformers.build_token_enforcer_tokenizer_data = lambda tokenizer: token_data
    transformers.build_transformers_prefix_allowed_tokens_fn = lambda data, parser: lambda batch, tokens: [7]
    for name, module in (("lmformatenforcer", root), ("lmformatenforcer.characterlevelparser", char),
                         ("lmformatenforcer.integrations", integrations),
                         ("lmformatenforcer.integrations.transformers", transformers)):
        monkeypatch.setitem(sys.modules, name, module)
    callback = executor.review_prefix_function(object(), {"type": "object"})
    assert captured == {"schema": {"type": "object"}, "alphabet": "abc中文"}
    assert callback(0, []) == [7]


@pytest.mark.parametrize("damage", [None, "missing", "object-mode", "not-strict", "external", "oversized", "wrong-pin"])
def test_schema_admission_rejects_unsupported_requests_before_loading(executor, damage):
    schema = vision_review_schema()
    body = {"response_format": {"type": "json_schema", "json_schema": {"strict": True, "schema": schema}}}
    expected = review_schema_hash(schema)
    if damage == "missing":
        body = {}
    elif damage == "object-mode":
        body["response_format"] = {"type": "json_object"}
    elif damage == "not-strict":
        body["response_format"]["json_schema"]["strict"] = False
    elif damage == "external":
        schema["properties"]["untrusted"] = {"$ref": "https://invalid.example/schema"}
    elif damage == "oversized":
        schema["description"] = "x" * 33000
    elif damage == "wrong-pin":
        expected = "0" * 64
    if damage:
        with pytest.raises(ValueError, match="REVIEW_JSON_SCHEMA"):
            executor.review_response_schema(body, expected)
    else:
        assert executor.review_response_schema(body, expected) == (schema, expected)


@pytest.mark.parametrize("damage", [None, "changed", "missing", "unlisted", "escape", "version"])
def test_decoder_files_are_verified_before_import(executor, tmp_path, monkeypatch, damage):
    import importlib.metadata
    base = tmp_path / "runtime"
    packages = []
    for name, folder in [("lm-format-enforcer", "lmformatenforcer"), ("interegular", "interegular")]:
        path = base / folder / "__init__.py"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"# synthetic verification fixture\n")
        packages.append({"name": name, "version": "fixture", "roots": [folder], "files": [{
            "path": path.relative_to(base).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }]})
    lock = {"protocol": STRUCTURED_REVIEW_PROTOCOL, "review_schema_sha256": "a" * 64, "packages": packages}
    path = base / "lmformatenforcer/__init__.py"
    if damage == "changed":
        path.write_bytes(b"changed")
    elif damage == "missing":
        path.unlink()
    elif damage == "unlisted":
        (path.parent / "unexpected.py").write_bytes(b"pass")
    elif damage == "escape":
        packages[0]["files"][0]["path"] = "../escape.py"
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: SimpleNamespace(
        version="different" if damage == "version" else "fixture", locate_file=lambda entry: base / entry,
    ))
    source = tmp_path / "lock.json"
    source.write_text(json.dumps(lock), encoding="utf8")
    if damage:
        with pytest.raises(ValueError, match="REVIEW_DECODER"):
            executor.decoder_runtime_identity(source)
    else:
        proof = executor.decoder_runtime_identity(source)
        assert proof["protocol"] == STRUCTURED_REVIEW_PROTOCOL and len(proof["decoder_sha256"]) == 64
        assert str(tmp_path) not in json.dumps(proof)
        lock["additional_review_schema_sha256"] = ["b" * 64]
        source.write_text(json.dumps(lock), encoding="utf8")
        extended = executor.decoder_runtime_identity(source)
        assert extended["supported_schema_sha256"] == ["a" * 64, "b" * 64]
        assert extended["decoder_sha256"] != proof["decoder_sha256"]
        lock["additional_review_schema_sha256"] = ["b" * 64, "b" * 64]
        source.write_text(json.dumps(lock), encoding="utf8")
        with pytest.raises(ValueError, match="ADDITIONAL_SCHEMA_PINS_INVALID"):
            executor.decoder_runtime_identity(source)


@pytest.mark.parametrize("damage", [None, "missing", "protocol", "schema", "decoder"])
def test_structured_response_proof_cannot_be_assumed_or_retried(api, tmp_path, monkeypatch, damage):
    client, factory, _ = api
    project = create_project(client)
    proof = {"protocol": STRUCTURED_REVIEW_PROTOCOL, "schema_sha256": review_schema_hash(vision_review_schema()),
             "decoder_sha256": "d" * 64}
    if damage == "protocol":
        proof["protocol"] = "unsupported"
    elif damage == "schema":
        proof["schema_sha256"] = "e" * 64
    elif damage == "decoder":
        proof["decoder_sha256"] = "not-a-hash"
    body = {"is_mock": True, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "reviewer": "synthetic", "is_mock": True, "input_sha256": "a" * 64,
        "findings": [], "verdict": "passed", "style_input_checks": fixture_style_input_checks(),
        "overall_confidence": 1,
    })}}]}
    if damage != "missing":
        body["structured_output"] = proof
    posts = []
    def respond(request):
        if request.url.path == "/free":
            return httpx.Response(200, json={"released": True, "allocated_bytes": 0})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=body)
    original = httpx.Client
    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    provider = LocalVisionReviewer(ProviderConfig("p", "fixture", "openai_compatible",
                                                  "http://127.0.0.1:8116", "fixture", None),
                                   response_protocol=STRUCTURED_REVIEW_PROTOCOL)
    wrapped = TrackedProvider(provider, factory, project)
    if damage:
        with pytest.raises(ReceivedReviewError, match="STRUCTURED_RESPONSE_PROOF_INVALID"):
            wrapped.review(context(tmp_path))
    else:
        assert wrapped.review(context(tmp_path)).is_mock
    assert len(posts) == 1
    assert posts[0]["response_format"]["type"] == "json_schema"
    assert posts[0]["response_format"]["json_schema"]["strict"] is True
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        assert db.get(ResourceLease, task.trace["resource_id"]).status == "RELEASED"
        if damage:
            assert task.status == "FAILED" and task.trace["received_response"]["retryable"] is False
        else:
            assert task.status == "COMPLETED" and task.trace["structured_output"] == proof
            assert task.trace["is_mock"] is True


@pytest.mark.parametrize("mode", ["legacy", "valid", "unknown", "bad-hash"])
def test_capability_probe_never_silently_substitutes_protocol(monkeypatch, mode):
    body = {"data": [{"id": "fixture"}]}
    if mode != "legacy":
        body["capabilities"] = {"review_response_protocol": "unsupported" if mode == "unknown"
                                else STRUCTURED_REVIEW_PROTOCOL,
                                "review_decoder_sha256": "bad" if mode == "bad-hash" else "d" * 64}
    provider = OpenAICompatibleProvider(ProviderConfig(
        "p", "p", "openai_compatible", "http://localhost", "fixture", None,
    ))
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: httpx.Response(200, json=body))
    if mode in {"unknown", "bad-hash"}:
        with pytest.raises(LLMCapabilityError):
            provider.detect_capabilities()
    else:
        result = provider.detect_capabilities()
        assert result.get("review_response_protocol") == (None if mode == "legacy" else STRUCTURED_REVIEW_PROTOCOL)


def test_committed_decoder_lock_pins_current_wire_schema():
    from shine.quality.basic_review import CharacterReviewResponse

    path = Path(__file__).resolve().parents[2] / "config/review-decoder-lock.json"
    lock = json.loads(path.read_text(encoding="utf8"))
    assert lock["review_schema_sha256"] == review_schema_hash(vision_review_schema())
    assert lock["additional_review_schema_sha256"] == [review_schema_hash(CharacterReviewResponse.model_json_schema())]
    assert {p["license"] for p in lock["packages"]} == {"MIT"}


def test_parser_failure_never_logs_generated_prefix_or_leaves_logger_changes(executor, monkeypatch, caplog):
    import logging
    import sys
    from types import ModuleType
    parser_module = ModuleType("lmformatenforcer")
    parser_module.JsonSchemaParser = lambda _, config: object()
    character_module = ModuleType("lmformatenforcer.characterlevelparser")
    character_module.CharacterLevelParserConfig = lambda *, alphabet: SimpleNamespace(alphabet=alphabet)
    integration = ModuleType("lmformatenforcer.integrations.transformers")
    def unsafe(*_):
        logging.getLogger().handle(logging.LogRecord(
            "root", logging.ERROR, "/isolated/lmformatenforcer/tokenenforcer.py", 1,
            "sk-private generated prefix", (), None,
        ))
        raise ValueError("sk-private parser error")
    integration.build_transformers_prefix_allowed_tokens_fn = lambda *a: unsafe
    integration.build_token_enforcer_tokenizer_data = lambda _: SimpleNamespace(tokenizer_alphabet="abc")
    monkeypatch.setitem(sys.modules, "lmformatenforcer", parser_module)
    monkeypatch.setitem(sys.modules, "lmformatenforcer.characterlevelparser", character_module)
    monkeypatch.setitem(sys.modules, "lmformatenforcer.integrations", ModuleType("lmformatenforcer.integrations"))
    monkeypatch.setitem(sys.modules, "lmformatenforcer.integrations.transformers", integration)
    logger = logging.getLogger()
    before = (list(logger.handlers), list(logger.filters), logger.level)
    callback = executor.review_prefix_function(object(), {})
    with pytest.raises(ValueError, match="REVIEW_STRUCTURED_DECODING_FAILED") as error:
        callback(0, [])
    assert "sk-private" not in caplog.text and "sk-private" not in str(error.value)
    assert before == (list(logger.handlers), list(logger.filters), logger.level)

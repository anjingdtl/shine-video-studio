import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import local_review_isolated_server as supervisor  # noqa: E402

build_isolated_app = supervisor.build_isolated_app


def test_supervisor_returns_result_after_stage_and_exposes_distinct_identity():
    calls = []
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "a" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, args, *, timeout, return_evidence):
        _manifest, _packages, method, path, body, _content_type = args
        calls.append((method, path, body))
        payload = base if path == "/health" else {"runtime_sha256": "a" * 64}
        return ({"status": 200, "body": json.dumps(payload).encode(), "health": base,
                 "identity": {"runtime_sha256": "a" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "exitcode": 0, "joined": True})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    health = client.get("/health").json()
    assert health["runtime_sha256"] != "a" * 64
    assert client.post("/free", json={"unload_models": True}).json()["released"] is True
    result = client.post("/v1/chat/completions", json={"model": "model"})
    assert result.status_code == 200 and result.json()["runtime_sha256"] == health["runtime_sha256"]
    assert client.get("/health").json()["last_stage"]["joined"] is True
    assert len(calls) == 2  # bootstrap plus exactly one inference


def test_supervisor_never_replays_unknown_outcome():
    calls = []
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        if len(calls) > 1:
            raise RuntimeError("unknown")
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "exitcode": 0, "joined": True})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    response = client.post("/v1/chat/completions", json={"model": "model"})
    assert response.status_code == 503
    assert calls == ["/health", "/v1/chat/completions"]
    assert client.post("/free", json={"unload_models": True}).status_code == 503
    assert client.get("/health").status_code == 503


def test_supervisor_blocks_result_if_source_changes_during_stage(monkeypatch):
    source = ["original"]
    monkeypatch.setattr(supervisor, "_supervised_runtime", lambda _inner: source[0])
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "c" * 64,
            "verified_models": {"model": {"model_id": "model"}}}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        if len(calls) > 1:
            source[0] = "changed"
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "c" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "exitcode": 0, "joined": True})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    assert client.post("/v1/chat/completions", json={}).status_code == 503
    assert client.post("/free", json={"unload_models": True}).status_code == 503

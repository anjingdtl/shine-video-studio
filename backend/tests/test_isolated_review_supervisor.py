import json
import sys
from pathlib import Path

import pytest
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
                {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    health = client.get("/health").json()
    assert health["runtime_sha256"] != "a" * 64
    assert client.post("/free", json={"unload_models": True}).json()["released"] is True
    result = client.post("/v1/chat/completions", json={"model": "model"})
    assert result.status_code == 200 and result.json()["runtime_sha256"] == health["runtime_sha256"]
    receipt = result.json()["isolation_receipt"]
    assert receipt["joined"] is True and receipt["group_active_processes"] == 0
    assert receipt["path"] == "/v1/chat/completions" and receipt["request_sha256"]
    assert receipt["stage_id"] and receipt["response_sha256"]
    assert client.get("/health").json()["last_stage"] == receipt
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
                {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    response = client.post("/v1/chat/completions", json={"model": "model"})
    assert response.status_code == 503
    assert calls == ["/health", "/v1/chat/completions"]
    assert client.post("/free", json={"unload_models": True}).status_code == 503
    assert client.get("/health").status_code == 503


def test_cold_fence_blocks_unreserved_inference_until_bound_reservation(monkeypatch):
    from uuid import uuid4

    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    auth = {"X-Shine-Handoff-Token": "x" * 40}
    assert service.post("/handoff/cold-fence").status_code == 401
    fence_response = service.post("/handoff/cold-fence", headers=auth)
    assert fence_response.status_code == 200
    fence = fence_response.json()
    assert service.get("/handoff/cold-status", headers=auth).json() == fence
    assert service.post("/handoff/cold-fence", headers=auth).status_code == 409
    assert service.post("/v1/chat/completions", json={}).status_code == 409
    review_id = str(uuid4())
    request = {"review_id": review_id, "fence_id": fence["fence_id"]}
    assert service.post("/handoff/cold-reserve", headers=auth,
                        json={**request, "fence_id": str(uuid4())}).status_code == 409
    assert service.post("/handoff/cold-reserve", headers=auth, json=request).status_code == 200
    assert service.post("/handoff/cold-reserve", headers=auth, json=request).status_code == 409
    assert service.post("/v1/chat/completions", json={}).status_code == 409
    assert service.post("/v1/chat/completions", headers={**auth, "X-Shine-Review-Id": review_id},
                        json={}).status_code == 200
    assert service.post("/handoff/cold-fence", headers=auth).status_code == 409
    assert service.get("/handoff/cold-status", headers=auth).status_code == 409
    assert calls == ["/health", "/v1/chat/completions"]


def test_cold_fence_refuses_a_service_that_already_ran_inference(monkeypatch):
    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, _args, *, timeout, return_evidence):
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    assert service.post("/v1/chat/completions", json={}).status_code == 200
    assert service.post("/handoff/cold-fence", headers={"X-Shine-Handoff-Token": "x" * 40}).status_code == 409


def test_bad_inference_response_cannot_be_mistaken_for_pristine_start(monkeypatch):
    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, args, *, timeout, return_evidence):
        body = b"{}" if args[3] == "/health" else b"not-json"
        return ({"status": 200, "body": body, "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    assert service.post("/v1/chat/completions", json={}).status_code == 503
    assert service.post("/handoff/cold-fence", headers={"X-Shine-Handoff-Token": "x" * 40}).status_code == 503


def test_supervisor_rejects_unconfirmed_review_process_group():
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        proof = {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True, "group_members_waited": 0,
                 "group_active_processes": 0 if len(calls) == 1 else 1}
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}}, proof)

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    assert client.post("/v1/chat/completions", json={}).status_code == 503
    assert client.get("/health").status_code == 503
    assert calls == ["/health", "/v1/chat/completions"]


def test_seal_binds_last_receipt_and_blocks_further_review(monkeypatch):
    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        proof = {"pid": 123, "birth": "456", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0}
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}}, proof)

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    header = {"X-Shine-Handoff-Token": "x" * 40}
    assert client.get("/handoff/ready").status_code == 401
    assert client.get("/handoff/ready", headers={"X-Shine-Handoff-Token": "y" * 40}).status_code == 401
    assert client.get("/handoff/ready", headers=header).json()["ready"] is True
    receipt = client.post("/v1/chat/completions", json={}).json()["isolation_receipt"]
    assert client.post("/handoff/seal", headers=header, json={**receipt, "stage_id": "wrong"}).status_code == 409
    assert client.post("/handoff/seal", json=receipt).status_code == 401
    assert client.post("/handoff/seal", headers=header, json=receipt).json()["sealed"] is True
    assert client.get("/handoff/ready", headers=header).status_code == 409
    assert client.post("/handoff/seal", headers=header, json=receipt).json()["sealed"] is True
    assert client.post("/v1/chat/completions", json={}).status_code == 409
    assert calls == ["/health", "/v1/chat/completions"]


def test_reserved_review_rejects_other_clients_and_replay(monkeypatch):
    from uuid import uuid4

    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, _args, *, timeout, return_evidence):
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    identity = str(uuid4())
    auth = {"X-Shine-Handoff-Token": "x" * 40}
    old_receipt = client.post("/v1/chat/completions", json={}).json()["isolation_receipt"]
    assert client.post("/handoff/reserve", headers=auth, json={"review_id": identity}).status_code == 200
    assert client.post("/handoff/seal", headers=auth, json=old_receipt).status_code == 409
    assert client.post("/v1/chat/completions", json={}).status_code == 409
    assert client.post("/v1/chat/completions", headers={**auth, "X-Shine-Review-Id": str(uuid4())},
                       json={}).status_code == 409
    headers = {**auth, "X-Shine-Review-Id": identity}
    assert client.post("/v1/chat/completions", headers=headers, json={}).status_code == 200
    assert client.post("/v1/chat/completions", headers=headers, json={}).status_code == 409


def test_sealed_service_can_reserve_next_stage_only_with_exact_receipt(monkeypatch):
    from uuid import uuid4

    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, _args, *, timeout, return_evidence):
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    auth = {"X-Shine-Handoff-Token": "x" * 40}
    old = client.post("/v1/chat/completions", json={}).json()["isolation_receipt"]
    assert client.post("/handoff/seal", headers=auth, json=old).status_code == 200
    identity = str(uuid4())
    body = {"review_id": identity, "previous_receipt": old}
    assert client.post("/handoff/rearm-reserve", json=body).status_code == 401
    assert client.post("/handoff/rearm-reserve", headers=auth,
                       json={**body, "previous_receipt": {**old, "stage_id": str(uuid4())}}).status_code == 409
    assert client.post("/handoff/rearm-reserve", headers=auth, json=body).status_code == 200
    assert client.post("/handoff/rearm-reserve", headers=auth, json=body).status_code == 409
    assert client.post("/v1/chat/completions", json={}).status_code == 409
    response = client.post("/v1/chat/completions", headers={**auth, "X-Shine-Review-Id": identity}, json={})
    assert response.status_code == 200
    new = response.json()["isolation_receipt"]
    assert new != old
    assert client.post("/handoff/seal", headers=auth, json=old).status_code == 409
    assert client.post("/handoff/seal", headers=auth, json=new).status_code == 200
    assert client.post("/handoff/rearm-reserve", headers=auth,
                       json={"review_id": identity, "previous_receipt": new}).status_code == 409


def test_release_and_readmission_use_actual_isolated_service_protocol(monkeypatch):
    from uuid import uuid4

    from shine.quality import review_release

    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}

    def runner(_target, _args, *, timeout, return_evidence):
        return ({"status": 200, "body": b"{}", "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    runtime = service.get("/health").json()["runtime_sha256"]
    receipt = service.post("/v1/chat/completions", json={}).json()["isolation_receipt"]

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            from urllib.parse import urlsplit

            return service.get(urlsplit(url).path, **kwargs)

        def post(self, url, **kwargs):
            from urllib.parse import urlsplit

            return service.post(urlsplit(url).path, **kwargs)

    class OwnedGroup:
        def owns(self, pid, birth):
            return (pid, birth) == (777, "789")

    monkeypatch.setattr(review_release.httpx, "Client", Client)
    monkeypatch.setattr(review_release, "OwnedProcessGroup", OwnedGroup)
    monkeypatch.setattr(review_release, "windows_loopback_listener_pid", lambda _port: 777)
    monkeypatch.setattr(review_release, "process_identity", lambda pid: "789" if pid == 777 else None)
    monkeypatch.setattr(review_release, "probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    args = {"base_url": "http://127.0.0.1:8116/v1", "runtime_sha256": runtime,
            "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test",
            "min_ram_mb": 8000, "min_vram_mb": 14000, "handoff_token": "x" * 40,
            "owner_group": OwnedGroup()}
    assert review_release.verify_review_release(**args, receipt=receipt)["review_stage_id"] == receipt["stage_id"]
    next_id = str(uuid4())
    review_release.reserve_review_service(**args, review_id=next_id, previous_receipt=receipt)
    headers = {"X-Shine-Handoff-Token": "x" * 40, "X-Shine-Review-Id": next_id}
    result = service.post("/v1/chat/completions", headers=headers, json={})
    assert result.status_code == 200
    new_receipt = result.json()["isolation_receipt"]
    proof = review_release.verify_review_release(**args, receipt=new_receipt)
    assert proof["review_stage_id"] == new_receipt["stage_id"]

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    fence = service.post("/handoff/cold-fence", headers={"X-Shine-Handoff-Token": "x" * 40}).json()
    assert review_release.verify_cold_review_fence(**args, fence_id=fence["fence_id"])["memory"]["ready"]
    first_id = str(uuid4())
    review_release.reserve_review_service(**args, review_id=first_id, cold_fence_id=fence["fence_id"])
    headers = {"X-Shine-Handoff-Token": "x" * 40, "X-Shine-Review-Id": first_id}
    assert service.post("/v1/chat/completions", headers=headers, json={}).status_code == 200


def test_reserved_asr_client_reaches_isolated_server_once(monkeypatch, tmp_path):
    from uuid import uuid4

    import httpx

    from shine.llm.base import ProviderConfig
    from shine.quality.review_admission import CURRENT_REVIEW_ADMISSION
    from shine.quality.semantic import LocalASRReviewer

    monkeypatch.setenv("SHINE_REVIEW_HANDOFF_TOKEN", "x" * 40)
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
            "verified_models": {"model": {"model_id": "model"}}}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        body = {"text": "synthetic", "segments": [], "model": "model", "is_mock": False,
                "executor_identity": {"schema_version": "1", "model_id": "model", "files_sha256": "c" * 64,
                                      "runtime_sha256": "b" * 64, "file_count": 1, "bytes": 1}}
        return ({"status": 200, "body": json.dumps(body).encode(), "health": base,
                 "identity": {"runtime_sha256": "b" * 64, "verified_models": base["verified_models"]}},
                {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    service = TestClient(build_isolated_app({}, Path("."), runner=runner))
    identity = str(uuid4())
    auth = {"X-Shine-Handoff-Token": "x" * 40}
    assert service.post("/handoff/reserve", headers=auth, json={"review_id": identity}).status_code == 200

    def forward(request):
        response = service.request(request.method, request.url.path, headers=dict(request.headers),
                                   content=request.content)
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    original = httpx.Client
    monkeypatch.setattr("shine.quality.semantic.httpx.Client",
                        lambda **kwargs: original(transport=httpx.MockTransport(forward), **kwargs))
    url = "http://127.0.0.1:8116/v1"
    reviewer = LocalASRReviewer(ProviderConfig("p", "p", "openai_compatible", url, "model", None))
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"test")
    token = CURRENT_REVIEW_ADMISSION.set((url, "x" * 40, identity))
    try:
        assert reviewer.transcribe(path).text == "synthetic"
        with pytest.raises(RuntimeError, match="ASR request failed"):
            reviewer.transcribe(path)
    finally:
        CURRENT_REVIEW_ADMISSION.reset(token)
    assert calls == ["/health", "/v1/audio/transcriptions"]


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
                {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner))
    assert client.post("/v1/chat/completions", json={}).status_code == 503
    assert client.post("/free", json={"unload_models": True}).status_code == 503


def test_identity_rechecks_live_files_without_starting_a_model_process():
    decoder = {"protocol": "local-review-json-schema-v1", "decoder_sha256": "d" * 64}
    base = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
            "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "e" * 64,
            "verified_models": {"model": {"model_id": "model"}}, "structured_output": decoder}
    live = {k: base[k] for k in ("status", "is_mock", "runtime_sha256", "verified_models", "structured_output")}
    calls = []

    def runner(_target, args, *, timeout, return_evidence):
        calls.append(args[3])
        return ({"status": 200, "body": b"{}", "health": base, "identity": live},
                {"pid": 123, "birth": "fake-birth", "exitcode": 0, "joined": True,
                 "group_active_processes": 0, "group_members_waited": 0})

    client = TestClient(build_isolated_app({}, Path("."), runner=runner, identity_probe=lambda *_: live))
    response = client.get("/identity")
    assert response.status_code == 200 and response.json()["runtime_sha256"] != live["runtime_sha256"]
    assert client.get("/v1/models").json()["data"] == [{"id": "model"}]
    assert calls == ["/health"]
    live["verified_models"] = {"other": {}}
    assert client.get("/identity").status_code == 503
    assert client.post("/free", json={"unload_models": True}).status_code == 503

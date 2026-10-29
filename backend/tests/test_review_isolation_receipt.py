"""An isolated review receipt must bind the actual result and process proof."""

import copy
import hashlib
import json
from uuid import uuid4

import pytest

from shine.quality.isolation_receipt import validate_isolation_receipt


def response():
    body = {"is_mock": False, "model": "local-model", "text": "synthetic"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    body["isolation_receipt"] = {
        "stage_id": str(uuid4()), "method": "POST", "path": "/v1/audio/transcriptions",
        "request_sha256": "a" * 64, "response_sha256": hashlib.sha256(canonical).hexdigest(),
        "runtime_sha256": "b" * 64, "pid": 123, "birth": "456", "exitcode": 0, "joined": True,
        "group_active_processes": 0, "group_members_waited": 1,
    }
    return body


def test_receipt_binds_payload_and_requires_empty_exited_process_group():
    body = response()
    assert validate_isolation_receipt(body) == body["isolation_receipt"]
    assert validate_isolation_receipt({"text": "legacy"}) is None
    for key, value in (("pid", True), ("birth", ""), ("exitcode", False), ("joined", False),
                       ("group_active_processes", 1), ("group_members_waited", -1),
                       ("request_sha256", "bad"), ("stage_id", "not-a-uuid")):
        changed = copy.deepcopy(body)
        changed["isolation_receipt"][key] = value
        with pytest.raises(ValueError, match="ISOLATION_RECEIPT"):
            validate_isolation_receipt(changed)
    changed = copy.deepcopy(body)
    changed["text"] = "tampered"
    with pytest.raises(ValueError, match="ISOLATION_RECEIPT"):
        validate_isolation_receipt(changed)


def test_handoff_reviewer_checks_owned_ready_service_before_call(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import HandoffReviewProvider
    from shine.comfyops.model_calls import TrackedProvider

    calls = []

    class Provider:
        config = SimpleNamespace(base_url="http://127.0.0.1:8116/v1")

        def transcribe(self, _value):
            calls.append("submitted")

    @contextmanager
    def stage(_kind):
        calls.append("reserved")
        yield "intent"

    session = SimpleNamespace(job_id="job", min_ram_mb=1, min_vram_mb=1, review_stage=stage,
                              settings=SimpleNamespace(offline_simulation=True),
                              guard=SimpleNamespace(check=lambda: None), gpu_uuid="GPU-test")
    tracked = TrackedProvider(Provider(), None, "project", reviewer_kind="ASR", production_job_id="job")
    config = {"base_url": "http://127.0.0.1:8116/v1", "gpu_uuid": "GPU-test"}
    with pytest.raises(HTTPException, match="HANDOFF_REVIEW_BINDING_INVALID"):
        HandoffReviewProvider(session, tracked, "ASR", {**config, "base_url": "http://127.0.0.1:8117/v1"})
    wrapped = HandoffReviewProvider(session, tracked, "ASR", config)

    session.settings.offline_simulation = False
    with pytest.raises(HTTPException, match="HANDOFF_REVIEW_COLD_FENCE_REQUIRED"):
        wrapped.transcribe("input")
    assert not calls
    session.settings.offline_simulation = True

    def fail_ready(**_kwargs):
        raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")

    monkeypatch.setattr("shine.quality.review_release.verify_review_ready", fail_ready)
    with pytest.raises(ValueError, match="OWNERSHIP_UNVERIFIED"):
        wrapped.transcribe("input")
    assert not calls


def test_handoff_reviewer_binds_cold_fence_after_persisting_review_intent(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import HandoffReviewProvider
    from shine.comfyops.model_calls import TrackedProvider

    calls = []

    class Provider:
        config = SimpleNamespace(base_url="http://127.0.0.1:8116/v1")

        def transcribe(self, _value):
            raise AssertionError("model must not run when admission is unconfirmed")

    @contextmanager
    def stage(_kind):
        calls.append("intent")
        yield "review-id"

    session = SimpleNamespace(job_id="job", min_ram_mb=1, min_vram_mb=1, review_stage=stage,
                              settings=SimpleNamespace(offline_simulation=False),
                              guard=SimpleNamespace(check=lambda: None), gpu_uuid="GPU-test")
    tracked = TrackedProvider(Provider(), None, "project", reviewer_kind="ASR", production_job_id="job")
    wrapped = HandoffReviewProvider(session, tracked, "ASR",
                                   {"base_url": Provider.config.base_url, "gpu_uuid": "GPU-test"},
                                   cold_fence_id="fence-id")
    monkeypatch.setattr("shine.quality.review_release.verify_cold_review_fence",
                        lambda **kwargs: calls.append(("verify", kwargs["fence_id"])))

    def fail_reserve(**kwargs):
        calls.append(("reserve", kwargs["review_id"], kwargs["cold_fence_id"]))
        raise ValueError("RESERVATION_UNCERTAIN")

    monkeypatch.setattr("shine.quality.review_release.reserve_review_service", fail_reserve)
    with pytest.raises(ValueError, match="RESERVATION_UNCERTAIN"):
        wrapped.transcribe("input")
    assert calls == [("verify", "fence-id"), "intent", ("reserve", "review-id", "fence-id")]


def test_reserved_asr_request_sends_token_only_to_bound_loopback(monkeypatch, tmp_path):
    import httpx

    from shine.llm.base import ProviderConfig
    from shine.quality.review_admission import CURRENT_REVIEW_ADMISSION, review_admission_headers
    from shine.quality.semantic import LocalASRReviewer

    seen = []

    def respond(request):
        seen.append((request.headers.get("X-Shine-Handoff-Token"), request.headers.get("X-Shine-Review-Id")))
        return httpx.Response(200, json={"text": "test", "segments": []})

    original = httpx.Client
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr("shine.quality.semantic.httpx.Client", client)
    url = "http://127.0.0.1:8116/v1"
    config = ProviderConfig("p", "p", "openai_compatible", url, "model", None)
    path = tmp_path / "audio.wav"
    path.write_bytes(b"synthetic")
    token = CURRENT_REVIEW_ADMISSION.set((url, "x" * 40, "review-id"))
    try:
        assert LocalASRReviewer(config).transcribe(path).text == "test"
        with pytest.raises(ValueError, match="ENDPOINT_MISMATCH"):
            review_admission_headers("http://127.0.0.1:8117/v1")
    finally:
        CURRENT_REVIEW_ADMISSION.reset(token)
    assert seen == [("x" * 40, "review-id")]


def test_local_reviewer_binds_receipt_to_executor_and_clears_stale_result():
    from shine.llm.base import ProviderConfig
    from shine.quality.semantic import LocalASRReviewer, ReceivedReviewError

    reviewer = LocalASRReviewer(ProviderConfig(profile_id="p", name="local", kind="openai_compatible",
                                               base_url="http://127.0.0.1:8116", model="local-model", api_key=None))
    body = response()
    body["executor_identity"] = {"schema_version": "1", "model_id": "local-model", "files_sha256": "c" * 64,
                                 "runtime_sha256": "b" * 64, "file_count": 1, "bytes": 1}
    unsigned = {key: value for key, value in body.items() if key != "isolation_receipt"}
    body["isolation_receipt"]["response_sha256"] = hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    reviewer.record_executor_identity(body, "d" * 64)
    assert reviewer.last_isolation_receipt == body["isolation_receipt"]
    changed = copy.deepcopy(body)
    changed["isolation_receipt"]["runtime_sha256"] = "e" * 64
    with pytest.raises(ReceivedReviewError, match="ISOLATION_RECEIPT_INVALID"):
        reviewer.record_executor_identity(changed, "d" * 64)
    assert reviewer.last_isolation_receipt is None


def test_release_requires_matching_supervisor_child_exit_and_memory(monkeypatch):
    from shine.quality.review_release import reserve_review_service, verify_review_ready, verify_review_release

    receipt = response()["isolation_receipt"]
    health = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
              "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
              "last_stage": receipt, "handoff_sealed": True}
    seals = []

    class Response:
        status_code = 200

        def json(self):
            return health

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, _url, **_kwargs):
            if _url.endswith("/handoff/ready"):
                assert _kwargs["headers"]["X-Shine-Handoff-Token"] == "x" * 40

                class ReadyResponse:
                    status_code = 200

                    def json(self):
                        return {"ready": True, "runtime_sha256": "b" * 64}

                return ReadyResponse()
            return Response()

        def post(self, _url, *, headers, json):
            assert headers["X-Shine-Handoff-Token"] == "x" * 40
            if _url.endswith("/handoff/reserve"):
                class ReservationResponse:
                    status_code = 200

                    def json(self):
                        return {"reserved": True, "review_id": json["review_id"], "runtime_sha256": "b" * 64}

                return ReservationResponse()
            seals.append(json)

            class SealResponse:
                status_code = 200

                def json(self):
                    return {"sealed": True, "stage_id": receipt["stage_id"], "runtime_sha256": "b" * 64}

            return SealResponse()

    monkeypatch.setattr("shine.quality.review_release.httpx.Client", Client)
    monkeypatch.setattr("shine.quality.review_release.windows_loopback_listener_pid", lambda _port: 777)
    monkeypatch.setattr("shine.quality.review_release.process_identity", lambda pid: "789" if pid == 777 else None)
    monkeypatch.setattr("shine.quality.review_release.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    args = {"base_url": "http://127.0.0.1:8116/v1", "receipt": receipt, "runtime_sha256": "b" * 64,
            "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test",
            "min_ram_mb": 8000, "min_vram_mb": 14000, "handoff_token": "x" * 40}
    with pytest.raises(ValueError, match="REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED"):
        verify_review_release(**args)
    assert not seals

    class OwnedGroup:
        def owns(self, pid, birth):
            return (pid, birth) == (777, "789")

    monkeypatch.setattr("shine.quality.review_release.OwnedProcessGroup", OwnedGroup)
    args["owner_group"] = OwnedGroup()
    ready_args = {key: value for key, value in args.items() if key != "receipt"}
    with pytest.raises(ValueError, match="REVIEW_SUPERVISOR_NOT_READY"):
        verify_review_ready(**ready_args)  # Sealed services cannot accept a new stage.
    health["handoff_sealed"] = False
    assert verify_review_ready(**ready_args)["ready"] is True
    reserve_review_service(**ready_args, review_id=str(uuid4()))
    health["allocated_bytes"] = 1
    with pytest.raises(ValueError, match="REVIEW_SUPERVISOR_NOT_READY"):
        verify_review_ready(**ready_args)
    health["allocated_bytes"] = 0
    health["handoff_sealed"] = True
    assert verify_review_release(**args)["review_stage_id"] == receipt["stage_id"]
    assert seals == [receipt]
    monkeypatch.setattr("shine.quality.review_release.process_identity", lambda pid: "789" if pid == 777 else "456")
    with pytest.raises(ValueError, match="REVIEW_PROCESS_STILL_ACTIVE"):
        verify_review_release(**args)
    monkeypatch.setattr("shine.quality.review_release.process_identity", lambda pid: "789" if pid == 777 else None)
    health["last_stage"] = {**receipt, "stage_id": str(uuid4())}
    with pytest.raises(ValueError, match="REVIEW_RELEASE_RECEIPT_MISMATCH"):
        verify_review_release(**args)


def test_cold_fence_requires_live_owner_exited_initial_child_and_memory(monkeypatch):
    from uuid import uuid4

    from shine.quality.review_release import verify_cold_review_fence

    fence_id = str(uuid4())
    child = {"pid": 123, "birth": "12345", "exitcode": 0, "joined": True,
             "group_active_processes": 0, "group_members_waited": 0}
    status = {"fenced": True, "fence_id": fence_id, "runtime_sha256": "b" * 64, "initial_stage": child}
    health = {"status": "ok", "is_mock": False, "loaded": False, "asr_loaded": False,
              "allocated_bytes": 0, "reserved_bytes": 0, "runtime_sha256": "b" * 64,
              "last_stage": child, "handoff_sealed": True}

    class Response:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            assert kwargs["headers"]["X-Shine-Handoff-Token"] == "x" * 40
            return Response(status if url.endswith("/handoff/cold-status") else health)

    class OwnedGroup:
        def owns(self, pid, birth):
            return (pid, birth) == (777, "789")

    monkeypatch.setattr("shine.quality.review_release.httpx.Client", Client)
    monkeypatch.setattr("shine.quality.review_release.OwnedProcessGroup", OwnedGroup)
    monkeypatch.setattr("shine.quality.review_release.windows_loopback_listener_pid", lambda _port: 777)
    monkeypatch.setattr("shine.quality.review_release.process_identity", lambda pid: "789" if pid == 777 else None)
    monkeypatch.setattr("shine.quality.review_release.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    args = {"base_url": "http://127.0.0.1:8116/v1", "runtime_sha256": "b" * 64,
            "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test", "fence_id": fence_id,
            "min_ram_mb": 8000, "min_vram_mb": 14000, "handoff_token": "x" * 40,
            "owner_group": OwnedGroup()}
    assert verify_cold_review_fence(**args)["memory"]["ready"] is True
    health["allocated_bytes"] = 1
    with pytest.raises(ValueError, match="REVIEW_COLD_FENCE_UNCONFIRMED"):
        verify_cold_review_fence(**args)
    health["allocated_bytes"] = 0
    status["fence_id"] = str(uuid4())
    with pytest.raises(ValueError, match="REVIEW_COLD_FENCE_UNCONFIRMED"):
        verify_cold_review_fence(**args)
    status["fence_id"] = fence_id
    monkeypatch.setattr("shine.quality.review_release.process_identity", lambda pid: "789" if pid == 777 else "12345")
    with pytest.raises(ValueError, match="REVIEW_PROCESS_STILL_ACTIVE"):
        verify_cold_review_fence(**args)


def test_model_call_persists_isolation_receipt_for_later_reconciliation(api):
    from sqlalchemy import select

    from shine.comfyops.model_calls import TrackedProvider
    from shine.models import AgentTaskRecord
    from shine.quality.semantic import Transcript
    from tests.conftest import create_project

    client, factory, _ = api
    project = create_project(client)
    receipt = response()["isolation_receipt"]

    class FakeReviewer:
        is_mock = True

        def transcribe(self, _path):
            self.last_isolation_receipt = receipt
            return Transcript(text="synthetic", is_mock=True, model="fake")

    TrackedProvider(FakeReviewer(), factory, project).transcribe("synthetic-input")
    with factory() as db:
        row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "model_call:ASR:transcribe"))
        assert row.status == "COMPLETED" and row.trace["isolation_receipt"] == receipt
        assert row.trace["is_mock"] is True

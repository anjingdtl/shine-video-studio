from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException

from shine.llm.base import LLMNotConfigured, ProviderConfig
from shine.quality.runtime import configured_reviewers
from shine.quality.semantic import LocalASRReviewer, LocalVisionReviewer
from shine.worker import _transcript_review, run_configured_once
from tests.review_fixtures import fixture_style_input_checks
from tests.test_semantic_qc import context


def profile(client, endpoint, role):
    result = client.post(
        "/api/v1/provider-profiles",
        json={
            "name": role,
            "kind": "openai_compatible",
            "base_url": endpoint,
            "model": role,
        },
    )
    assert result.status_code == 201
    assert (
        client.put("/api/v1/agent-routes", json={"role": role, "provider_profile_id": result.json()["id"]}).status_code
        == 200
    )
    return result.json()


def test_normal_worker_uses_explicit_media_routes_and_never_text_qc_fallback(api, monkeypatch):
    client, factory, settings = api
    profile(client, "https://text.example/v1", "qc_reviewer")
    assert configured_reviewers(factory, settings) == (None, None)
    profile(client, "http://127.0.0.1:19001/v1", "vlm_reviewer")
    profile(client, "http://127.0.0.1:19002/v1", "asr_reviewer")
    seen = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        return "claimed"

    monkeypatch.setattr("shine.worker.run_one", run)
    assert run_configured_once(factory, object(), "worker", settings=settings) == "claimed"
    assert isinstance(seen["semantic_reviewer"], LocalVisionReviewer)
    assert isinstance(seen["asr_reviewer"], LocalASRReviewer)


def test_disabled_explicit_review_profile_does_not_fallback(api):
    client, factory, settings = api
    row = profile(client, "http://127.0.0.1:19001/v1", "vlm_reviewer")
    assert client.put("/api/v1/provider-profiles/" + row["id"], json={"enabled": False}).status_code == 200
    with pytest.raises(LLMNotConfigured):
        configured_reviewers(factory, settings)


def test_worker_blocks_remote_asr_before_opening_or_uploading_media(tmp_path):
    class Remote:
        requires_remote_consent = True

        def transcribe(self, path):
            raise AssertionError("No media bytes may leave this host")

    ctx = context(tmp_path)
    transcript, findings, error = _transcript_review(asr=Remote(), artifact_path=ctx.artifact_path, spec=ctx.shot_spec)
    assert transcript is None and findings is None
    assert error == "ASR_REMOTE_CONSENT_REQUIRED"


def test_local_vlm_needs_no_cloud_consent_and_preserves_fake_result(tmp_path, monkeypatch):
    import json

    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/free":
            return httpx.Response(200, json={"released": True, "allocated_bytes": 0})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "loaded": False, "asr_loaded": False,
                                            "allocated_bytes": 0, "reserved_bytes": 0})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reviewer": "fixture",
                                    "is_mock": True,
                                    "input_sha256": "a" * 64,
                                    "findings": [], "verdict": "passed",
                                    "style_input_checks": fixture_style_input_checks(),
                                    "overall_confidence": 1,
                                    "raw_summary": "Fixture only",
                                }
                            )
                        }
                    }
                ]
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "shine.quality.semantic.httpx.Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    cfg = ProviderConfig(
        profile_id="p",
        name="p",
        kind="openai_compatible",
        base_url="http://127.0.0.1:19001/v1",
        model="p",
        api_key=None,
    )
    reviewer = LocalVisionReviewer(cfg)
    assert reviewer.review(context(tmp_path, consent=False)).is_mock
    reviewer.release_models()
    assert [r.url.path for r in requests] == ["/v1/chat/completions", "/free", "/health"]
    with pytest.raises(HTTPException) as rejected:
        LocalVisionReviewer(replace(cfg, base_url="https://cloud.example/v1"))
    assert rejected.value.status_code == 422


def test_local_release_does_not_treat_acknowledgement_as_release(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(
        "shine.quality.semantic.httpx.Client",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"accepted": True})), **kw
        ),
    )
    cfg = ProviderConfig(
        profile_id="p",
        name="p",
        kind="openai_compatible",
        base_url="http://127.0.0.1:19001/v1",
        model="p",
        api_key=None,
    )
    with pytest.raises(ValueError, match="MODEL_RELEASE_UNCONFIRMED"):
        LocalASRReviewer(cfg).release_models()


@pytest.mark.parametrize("configured,expected", [(300, 30), (10, 10)])
def test_local_release_timeout_is_bounded_without_claiming_release(monkeypatch, configured, expected):
    seen = []
    original = httpx.Client

    def respond(request):
        raise httpx.ReadTimeout("No release proof")

    def client(**kwargs):
        seen.append(kwargs["timeout"])
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("shine.quality.semantic.httpx.Client", client)
    cfg = ProviderConfig("p", "p", "openai_compatible", "http://127.0.0.1:19001/v1", "p", None,
                         timeout_s=configured)
    with pytest.raises(httpx.ReadTimeout):
        LocalASRReviewer(cfg).release_models()
    assert seen == [expected]


def test_remote_reviewer_never_receives_local_reference_images(tmp_path, monkeypatch):
    import base64
    import json

    from shine.quality.semantic import OpenAICompatibleVisionReviewer

    captured = []
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"private-reference-that-must-stay-local")
    ctx = replace(context(tmp_path, consent=True), local_reference_frames=(("CHAR_TEST", reference),))

    def respond(request):
        captured.append(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reviewer": "claimed",
                                    "model": "claimed",
                                    "is_mock": False,
                                    "input_sha256": "a" * 64,
                                    "findings": [], "verdict": "passed",
                                    "style_input_checks": fixture_style_input_checks(),
                                    "overall_confidence": 0.9,
                                    "raw_summary": "test",
                                }
                            )
                        }
                    }
                ]
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "shine.quality.semantic.httpx.Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    cfg = ProviderConfig(
        profile_id="p",
        name="p",
        kind="openai_compatible",
        base_url="https://review.example/v1",
        model="explicit-model",
        api_key=None,
    )
    result = OpenAICompatibleVisionReviewer(cfg).review(ctx)
    assert result.model == "explicit-model"
    assert base64.b64encode(reference.read_bytes()) not in captured[0]
    assert b"ONLY violations" in captured[0]
    captured.clear()
    cfg = replace(cfg, base_url="http://127.0.0.1:19001/v1")
    LocalVisionReviewer(cfg).review(ctx)
    assert base64.b64encode(reference.read_bytes()) in captured[0]


def test_model_executor_refuses_unhashed_and_changed_weights(tmp_path):
    import hashlib
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "local_review_executor", Path(__file__).resolve().parents[2] / "scripts/local_review_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="INCOMPLETE_MODEL_HASH_MANIFEST"):
        module.verify_model_manifest({"model_root": str(tmp_path), "files": []})
    entries = []
    for name in ["config.json", "model.safetensors", "tokenizer.json"]:
        path = tmp_path / name
        path.write_bytes(b"synthetic-test-model-manifest-only")
        entries.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"model_root": str(tmp_path), "files": entries}
    assert module.verify_model_manifest(manifest) == tmp_path.resolve()
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="MODEL_HASH_CHANGED"):
        module.verify_model_manifest(manifest)


def test_local_release_requires_physical_counter_even_with_true_ack(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(
        "shine.quality.semantic.httpx.Client",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"released": True})), **kw
        ),
    )
    cfg = ProviderConfig(
        profile_id="p",
        name="p",
        kind="openai_compatible",
        base_url="http://127.0.0.1:19001/v1",
        model="p",
        api_key=None,
    )
    with pytest.raises(ValueError, match="MODEL_RELEASE_UNCONFIRMED"):
        LocalASRReviewer(cfg).release_models()


def test_semantic_frames_include_late_shot_evidence(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from shine.media.frames import extract_frames

    offsets = []
    source = tmp_path / "seven-seconds.mp4"
    source.write_bytes(b"explicit-synthetic-fixture")

    def run(command, **kwargs):
        offsets.append(float(command[command.index("-ss") + 1]))
        Path(command[-1]).write_bytes(b"synthetic-derived-frame")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("shine.media.frames.shutil.which", lambda _: "ffmpeg")
    monkeypatch.setattr("shine.media.frames.subprocess.run", run)
    assert len(extract_frames(source, tmp_path / "frames", count=4, duration_s=7)) == 4
    assert offsets[0] < 1 and offsets[-1] > 6
    assert all(0 <= value < 7 for value in offsets)


@pytest.mark.parametrize("missing", ["findings", "verdict", None])
def test_wire_review_requires_explicit_findings_without_rewriting_stored_schema(
    tmp_path, monkeypatch, missing,
):
    import hashlib
    import json

    from shine.quality.semantic import ReceivedReviewError

    data = {"is_mock": False, "overall_confidence": 1 if missing == "verdict" else 0,
            "raw_summary": "Observed story violations" if missing == "verdict" else "Uncertain review"}
    data.update(findings=[], verdict="needs_review", style_input_checks=fixture_style_input_checks())
    if missing:
        data.pop(missing)
    response = httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(data),
    }}]})
    original = httpx.Client
    monkeypatch.setattr(
        "shine.quality.semantic.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: response), **kw),
    )
    cfg = ProviderConfig(profile_id="p", name="p", kind="openai_compatible",
                         base_url="http://127.0.0.1:19001/v1", model="p", api_key=None)
    if missing is None:
        review = LocalVisionReviewer(cfg).review(context(tmp_path))
        assert review.findings == [] and review.overall_confidence == 0
    else:
        with pytest.raises(ReceivedReviewError, match="VLM_RESPONSE_CONTRACT_INVALID") as error:
            LocalVisionReviewer(cfg).review(context(tmp_path))
        assert error.value.response_sha256 == hashlib.sha256(response.content).hexdigest()

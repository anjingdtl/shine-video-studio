"""Voice-capability readiness in the new-machine setup flow.

The gate must distinguish a verified local design package with a working
runtime (enough to generate and freeze a voice master) from an unrouted,
unverified, capability-mismatched or runtime-less configuration, and must never
present a mock package as a ready real voice path.

It must also be honest about *why* a role is blocked: a package whose files are
already on disk but whose registry status was never advanced is a different
problem from a package that was never downloaded.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from shine.comfy.workflow import graph_hash
from shine.comfyops.llm_readiness import voice_readiness
from shine.comfyops.models import ModelPackage

LOCAL = {"X-Shine-Local-Action": "1"}


def _package(payload, status="FILES_VERIFIED_PENDING_RUNTIME"):
    return ModelPackage(id=payload["name"], payload=payload, sha256=graph_hash(payload), status=status)


def _design_payload(capabilities=("VOICE_DESIGN",), name="voice-design"):
    return {
        "name": name,
        "kind": "model",
        "revision": "rev1",
        "license_id": "Apache-2.0",
        "usage": "commercial",
        "family": "Qwen3-TTS",
        "capabilities": list(capabilities),
        "resource_measurement": "ESTIMATED",
        "resource_mb": 12000,
        "runtime_versions": {"qwen-tts": "0.1.1"},
        "files": [],
    }


def _role_runtime(tmp_path, versions=None):
    """A runtime spec that passes every check without running anything."""
    interpreter = tmp_path / "runtime-python.exe"
    interpreter.write_bytes(b"interpreter-bytes")
    overlay = tmp_path / "overlay"
    overlay.mkdir(exist_ok=True)
    return {
        "python": str(interpreter),
        "python_sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
        "packages_dir": str(overlay),
        "versions": versions or {"qwen-tts": "0.1.1", "transformers": "4.57.3", "torch": "2.10.0+cu130"},
        "timeout_s": 300,
        "budget_mb": 12000,
    }


def _write_config(settings, packages, role_runtimes):
    from shine.voiceops.runtime_config import VoiceRuntimeConfig, RoleRuntime, save_voice_config

    config = VoiceRuntimeConfig(
        packages=packages,
        role_runtimes={role: RoleRuntime.model_validate(spec) for role, spec in role_runtimes.items()},
    )
    save_voice_config(settings, config)


def test_voice_readiness_blocked_without_routing(studio):
    client, _factory, _root, settings = studio
    report = client.get("/api/v1/setup/voice-readiness").json()
    assert report["status"] == "BLOCKED"
    assert report["voice_master_capable"] is False
    assert all(row["code"] == "VOICE_PACKAGE_UNROUTED" for row in report["roles"])


def test_voice_readiness_ready_with_verified_design_package_and_runtime(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload())
        db.add(package)
        db.commit()
        package_id = package.id
    _write_config(settings, {"design": package_id}, {"design": _role_runtime(tmp_path)})

    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] is None
    assert by_role["design"]["runtime_configured"] is True
    assert by_role["base"]["code"] == "VOICE_PACKAGE_UNROUTED"
    # A voice master can be generated; the clone path still cannot.
    assert report["voice_master_capable"] is True
    assert report["voice_clone_capable"] is False
    assert report["verification"] == "PACKAGE_ROUTING_ONLY_NOT_SYNTHESIS"


def test_voice_readiness_requires_a_runtime_for_a_verified_package(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload())
        db.add(package)
        db.commit()
        package_id = package.id
    _write_config(settings, {"design": package_id}, {})

    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] == "VOICE_ROLE_RUNTIME_UNCONFIGURED"
    assert report["voice_master_capable"] is False


def test_voice_readiness_never_accepts_mock_or_wrong_capability(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        mock = _package(_design_payload(name="mock-design"), status="MOCK_AVAILABLE")
        wrong = _package(_design_payload(capabilities=("ASR",), name="wrong-cap"))
        db.add_all([mock, wrong])
        db.commit()
        mock_id, wrong_id = mock.id, wrong.id

    _write_config(settings, {"design": mock_id}, {"design": _role_runtime(tmp_path)})
    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] == "VOICE_PACKAGE_NOT_VERIFIED"
    assert report["voice_master_capable"] is False

    _write_config(settings, {"design": wrong_id}, {"design": _role_runtime(tmp_path)})
    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] == "VOICE_CAPABILITY_MISSING"
    assert report["voice_master_capable"] is False


def test_voice_readiness_reports_files_present_when_only_the_registry_lags(studio, tmp_path):
    """A package downloaded but never advanced must not look like a missing model."""
    client, factory, _root, settings = studio
    payload = _design_payload(name="lagging-design")
    payload["files"] = [{"relative_path": "model.safetensors", "sha256": "a" * 64, "size": 4}]
    with factory() as db:
        package = _package(payload, status="UNVERIFIED_ORIGIN")
        db.add(package)
        db.commit()
        package_id = package.id
    root = settings.comfyops_dir / "isolated-packages" / payload["name"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.safetensors").write_bytes(b"data")

    _write_config(settings, {"design": package_id}, {"design": _role_runtime(tmp_path)})
    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] == "VOICE_PACKAGE_NOT_VERIFIED"
    assert by_role["design"]["files_present"] is True
    assert report["voice_master_capable"] is False


def test_voice_readiness_rejects_a_runtime_whose_interpreter_changed(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload())
        db.add(package)
        db.commit()
        package_id = package.id
    runtime = _role_runtime(tmp_path)
    (tmp_path / "runtime-python.exe").write_bytes(b"changed-bytes")
    _write_config(settings, {"design": package_id}, {"design": runtime})

    report = client.get("/api/v1/setup/voice-readiness").json()
    by_role = {row["role"]: row for row in report["roles"]}
    assert by_role["design"]["code"] == "VOICE_RUNTIME_PYTHON_CHANGED"
    assert report["voice_master_capable"] is False


def test_voice_runtime_endpoint_rejects_an_unverified_package(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload(), status="UNVERIFIED_ORIGIN")
        db.add(package)
        db.commit()
        package_id = package.id

    response = client.post("/api/v1/setup/voice-runtime", json={
        "packages": {"design": package_id},
        "role_runtimes": {"design": _role_runtime(tmp_path)},
    }, headers=LOCAL)
    assert response.status_code == 409
    assert "VOICE_PACKAGE_NOT_VERIFIED" in response.text


def test_voice_runtime_endpoint_requires_a_runtime_per_routed_role(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload())
        db.add(package)
        db.commit()
        package_id = package.id

    response = client.post("/api/v1/setup/voice-runtime", json={
        "packages": {"design": package_id},
        "role_runtimes": {},
    }, headers=LOCAL)
    assert response.status_code == 409
    assert "VOICE_ROLE_RUNTIME_REQUIRED" in response.text


def test_voice_runtime_endpoint_persists_a_valid_configuration(studio, tmp_path):
    client, factory, _root, settings = studio
    with factory() as db:
        package = _package(_design_payload())
        db.add(package)
        db.commit()
        package_id = package.id

    response = client.post("/api/v1/setup/voice-runtime", json={
        "packages": {"design": package_id},
        "role_runtimes": {"design": _role_runtime(tmp_path)},
    }, headers=LOCAL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packages"] == {"design": package_id}
    assert body["readiness"]["voice_master_capable"] is True

    # The configuration is read back from disk, not from process memory.
    stored = json.loads((settings.comfyops_dir / "voice-runtime.json").read_text(encoding="utf-8"))
    assert stored["packages"] == {"design": package_id}
    assert stored["role_runtimes"]["design"]["versions"]["qwen-tts"] == "0.1.1"

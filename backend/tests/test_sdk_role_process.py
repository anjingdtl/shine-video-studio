"""Actual CPU subprocesses with explicitly Fake SDKs, never real GPU/voice acceptance."""

# ruff: noqa: F811
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from shine.comfyops.installer import process_identity
from shine.comfyops.model_calls import TrackedProvider
from shine.comfyops.models import ResourceLease
from shine.models import AgentTaskRecord
from shine.voiceops.local_sdk import QwenLocalVoiceProvider
from shine.voiceops.sdk_runtime import SDKProcessLoader
from tests.conftest import create_project
from tests.test_v13_instances import install_fixture
from tests.test_v13_preparation import (
    post,
    prep,  # noqa: F401
    rights,
)

NUMPY_FIXTURE = """
import math
from types import SimpleNamespace
float32 = 'test-only'
class Array(list):
    ndim = 1
def asarray(data, dtype=None): return Array(data)
def isfinite(data): return SimpleNamespace(all=lambda: all(math.isfinite(x) for x in data))
"""
TORCH_FIXTURE = """
from types import SimpleNamespace
bfloat16 = 'explicit-fake-bfloat16'
cuda = SimpleNamespace(is_available=lambda: True, mem_get_info=lambda: (16*1024**3, 16*1024**3))
"""
SDK_FIXTURE = """
from types import SimpleNamespace
class Model:
    is_mock = True
    @classmethod
    def from_pretrained(cls, path, **kwargs):
        import os
        assert 'SHINE_LOCAL_TEST_SECRET' not in os.environ
        assert kwargs['local_files_only'] and kwargs['device_map'] == 'cuda:0'
        assert kwargs['dtype'] == 'explicit-fake-bfloat16'
        return cls()
    def generate_voice_design(self, **kwargs): return [[0.1]*16000], 16000
    def generate_voice_clone(self, **kwargs):
        assert kwargs['ref_audio'][1] == 16000
        return [[0.1]*16000], 16000
    def transcribe(self, **kwargs): return [SimpleNamespace(text='你好', language='Chinese')]
    def align(self, **kwargs):
        return [[SimpleNamespace(text='你', start_time=0, end_time=.5),
                 SimpleNamespace(text='好', start_time=.5, end_time=1)]]
Qwen3TTSModel = Qwen3ASRModel = Qwen3ForcedAligner = Model
"""


def runtime_site(root, sdk, transformer_version):
    root.mkdir()
    for name, version in {
        sdk: "0.1.1" if sdk == "qwen-tts" else "0.0.6",
        "transformers": transformer_version,
        "torch": "2.10.0",
    }.items():
        info = root / (name.replace("-", "_") + "-" + version + ".dist-info")
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf8")
    (root / "numpy.py").write_text(NUMPY_FIXTURE, encoding="utf8")
    (root / "torch.py").write_text(TORCH_FIXTURE, encoding="utf8")
    (root / (sdk.replace("-", "_") + ".py")).write_text(SDK_FIXTURE, encoding="utf8")
    python = Path(sys._base_executable).resolve()
    return {
        "python": str(python),
        "python_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
        "packages_dir": str(root.resolve()),
        "versions": {
            sdk: "0.1.1" if sdk == "qwen-tts" else "0.0.6",
            "transformers": transformer_version,
            "torch": "2.10.0",
        },
    }


@pytest.fixture
def isolated_sdk(prep, tmp_path):
    client, factory, settings = prep
    tts = runtime_site(tmp_path / "tts-site", "qwen-tts", "4.57.3")
    asr = runtime_site(tmp_path / "asr-site", "qwen-asr", "4.57.6")
    packages = {}
    for role, cap in {
        "design": "VOICE_DESIGN",
        "base": "VOICE_CLONE",
        "asr": "ASR",
        "align": "FORCED_ALIGNMENT",
    }.items():
        sdk = "qwen-tts" if role in {"design", "base"} else "qwen-asr"
        packages[role] = install_fixture(
            prep,
            "process-sdk-" + role,
            {"fixture.bin": b"synthetic-not-a-model"},
            kind="model",
            capabilities=[cap],
            resource_mb=4000,
            runtime_versions={sdk: "0.1.1" if sdk == "qwen-tts" else "0.0.6"},
        )["id"]
    loader = SDKProcessLoader({"design": tts, "base": tts, "asr": asr, "align": asr})
    provider = QwenLocalVoiceProvider(factory, settings, packages, loader)
    project = create_project(client)
    return provider, loader, TrackedProvider(provider, factory, project), factory, project


def test_conflicting_sdks_run_sequentially_in_different_processes_and_keep_mock_provenance(isolated_sdk):
    provider, loader, tracked, factory, project = isolated_sdk
    voice = tracked.design("Explicit synthetic fixture", "你好", "Lin")
    assert voice["is_mock"] and provider.is_mock
    assert loader.release_models()["process_exit"]["returncode"] == 0
    digest = hashlib.sha256(voice["audio"]).hexdigest()
    tracked.prepare_conditioning(voice["audio"], digest, "你好")
    audio = tracked.synthesize("你好", "Lin", digest, "neutral", 1, 0)
    transcript = tracked.transcribe(audio["audio"])
    words = tracked.align(audio["audio"], "你好")
    assert transcript["speaker"] == "UNKNOWN" and transcript["confidence"] == 0 and transcript["is_mock"]
    assert "".join(w["text"] for w in words) == "你好"
    with factory() as db:
        tasks = db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project)).all()
        children = [t for t in tasks if t.trace.get("model_child_pid")]
        assert len(children) == 4
        assert {t.trace["runtime_versions"]["transformers"] for t in children} == {"4.57.3", "4.57.6"}
        assert all(t.trace["is_mock"] and t.status == "COMPLETED" for t in children)
        for task in children:
            assert task.trace["model_child_exit"]["returncode"] == 0
            assert process_identity(task.trace["model_child_pid"]) != task.trace["model_child_birth"]
        assert all(r.status == "RELEASED" for r in db.scalars(select(ResourceLease)))


def test_declared_role_budget_is_the_actual_serial_reservation(isolated_sdk):
    _, loader, tracked, factory, project = isolated_sdk
    loader.runtimes["design"].budget_mb = 8000
    assert tracked.design("synthetic 8GB reservation", "你好", "Lin")["is_mock"]
    with factory() as db:
        row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        assert row.trace["runtime_budget_mb"] == 8000
        assert db.get(ResourceLease, row.trace["resource_id"]).budget_mb == 8000


def test_child_keeps_user_identity_needed_by_torch_without_inheriting_secrets(isolated_sdk, monkeypatch):
    _, loader, tracked, _, _ = isolated_sdk
    for name in ("LOGNAME", "USER", "LNAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USERNAME", "synthetic-sdk-user")
    monkeypatch.setenv("SHINE_LOCAL_TEST_SECRET", "explicit-fake-secret-must-not-reach-child")
    site = loader.runtimes["design"].packages_dir / "qwen_tts.py"
    site.write_text("import getpass\nassert getpass.getuser() == 'synthetic-sdk-user'\n" + SDK_FIXTURE, encoding="utf8")
    result = tracked.design("synthetic user-context regression", "你好", "Lin")
    assert result["is_mock"] and loader.release_models()["process_exit"]["returncode"] == 0


def test_local_alignment_restores_unspoken_script_punctuation_without_changing_times(isolated_sdk):
    from shine.voiceops.providers import fake_audio

    provider, _, tracked, _, _ = isolated_sdk
    audio, _ = fake_audio("你好", "Lin")
    words = tracked.align(audio, "你好。")
    assert "".join(w["text"] for w in words) == "你好。"
    assert [(w["start_s"], w["end_s"]) for w in words] == [(0, 0.5), (0.5, 1)]
    assert provider.last_alignment_evidence["timing_modified"] is False
    assert "".join(w["text"] for w in provider.last_alignment_evidence["raw_words"]) == "你好"


def test_runtime_version_drift_fails_before_sdk_inference(isolated_sdk):
    _, loader, tracked, factory, project = isolated_sdk
    info = loader.runtimes["design"].packages_dir / "transformers-4.57.3.dist-info/METADATA"
    info.write_text("Metadata-Version: 2.1\nName: transformers\nVersion: 4.57.6\n", encoding="utf8")
    with pytest.raises(RuntimeError, match="SDK_VERSION_NOT_PINNED_OR_CHANGED"):
        tracked.design("synthetic fixture", "你好", "Lin")
    assert loader.release_models()["process_exit"]["returncode"] == 1
    with factory() as db:
        row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        assert row.status == "NEEDS_REVIEW"


def test_changed_interpreter_and_untracked_calls_never_start_model_process(isolated_sdk):
    _, loader, tracked, _, _ = isolated_sdk
    with pytest.raises(ValueError, match="TRACKED_SERIAL_STAGE"):
        loader.invoke("design", Path.cwd(), "generate_voice_design", {})
    loader.runtimes["design"].python_sha256 = "0" * 64
    with pytest.raises(ValueError, match="PYTHON_CHANGED"):
        tracked.design("synthetic fixture", "你好", "Lin")
    assert loader.current is None


def test_changed_runtime_binary_never_starts_model_process(isolated_sdk, tmp_path):
    _, loader, tracked, _, _ = isolated_sdk
    binary = tmp_path / "synthetic-sox.exe"
    binary.write_bytes(b"synthetic-binary-never-executed")
    loader.runtimes["design"].binaries = {str(binary): hashlib.sha256(binary.read_bytes()).hexdigest()}
    binary.write_bytes(b"changed")
    with pytest.raises(ValueError, match="RUNTIME_BINARY_CHANGED"):
        tracked.design("synthetic fixture", "你好", "Lin")
    assert loader.current is None


def test_redirecting_python_launcher_is_rejected_before_inference(isolated_sdk):
    _, loader, tracked, _, _ = isolated_sdk
    executable = Path(sys.executable).resolve()
    loader.runtimes["design"].python = executable
    loader.runtimes["design"].python_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    if os.name == "nt" and executable != Path(sys._base_executable).resolve():
        with pytest.raises(ValueError, match="REQUIRES_DIRECT_PYTHON"):
            tracked.design("synthetic redirector fixture", "你好", "Lin")
        loader.release_models()
    else:
        assert tracked.design("synthetic direct fixture", "你好", "Lin")["is_mock"]


def test_child_rechecks_model_bytes_before_loading_any_sdk(tmp_path, monkeypatch):
    from shine.voiceops.sdk_child import verify_request

    model = tmp_path / "model.safetensors"
    model.write_bytes(b"original-synthetic-weight")
    versions = {"qwen-tts": "0.1.1", "transformers": "4.57.3", "torch": "2.10.0"}
    body = {
        "role": "design",
        "root": str(tmp_path),
        "method": "generate_voice_design",
        "kwargs": {"text": "test", "language": "Chinese", "instruct": "test"},
        "versions": versions,
        "package": {
            "runtime_versions": {"qwen-tts": "0.1.1"},
            "files": [
                {
                    "relative_path": model.name,
                    "size": model.stat().st_size,
                    "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                }
            ],
        },
    }
    monkeypatch.setattr("shine.voiceops.sdk_child.importlib.metadata.version", lambda name: versions[name])
    model.write_bytes(b"tampered-synthetic-weight")
    with pytest.raises(ValueError, match="LOCAL_MODEL_HASH_CHANGED"):
        verify_request(body)


def test_child_timeout_waits_for_exit_and_never_replays(isolated_sdk):
    _, loader, tracked, factory, project = isolated_sdk
    site = loader.runtimes["design"].packages_dir
    (site / "qwen_tts.py").write_text("import time\ntime.sleep(10)\n", encoding="utf8")
    loader.runtimes["design"].timeout_s = 1
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        tracked.design("synthetic timed fixture", "你好", "Lin")
    assert loader.current.poll() is not None
    loader.release_models()
    with factory() as db:
        row = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        assert row.status == "NEEDS_REVIEW" and row.trace["model_child_exit"]["returncode"] is not None
    with pytest.raises(Exception, match="MODEL_CALL_RECONCILIATION_REQUIRED"):
        tracked.design("synthetic timed fixture", "你好", "Lin")


def test_normal_api_configures_role_process_provider_and_keeps_fake_candidate(isolated_sdk, prep, monkeypatch):
    provider, loader, _, factory, project = isolated_sdk
    loader.runtimes["design"].budget_mb = 8000
    client, _, _ = prep
    monkeypatch.setenv("SHINE_LOCAL_TEST_SECRET", "explicit-synthetic-env-secret")
    monkeypatch.setenv("SHINE_VOICE_SDK_PACKAGES", json.dumps(provider.packages))
    monkeypatch.setenv(
        "SHINE_VOICE_ROLE_RUNTIMES", json.dumps({k: v.model_dump(mode="json") for k, v in loader.runtimes.items()})
    )
    ar, vr = rights(client, project), rights(client, project, "voice_design")
    row = post(
        client,
        f"/projects/{project}/voice-profiles/design",
        {
            "character": "Lin",
            "description": "synthetic",
            "text": "你好",
            "rights_id": vr["id"],
            "asset_rights_id": ar["id"],
            "execution_mode": "local",
        },
    )
    assert row["payload"]["is_mock"] and client.app.state.voice_provider.name == "QwenRoleProcessSDK"
    with factory() as db:
        task = db.scalar(
            select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "model_call:TTS:design"
            )
        )
        assert task.trace["is_mock"] and task.trace["model_child_exit"]["returncode"] == 0
        assert db.get(ResourceLease, task.trace["resource_id"]).budget_mb == 8000


def test_library_clone_tracks_the_owned_sdk_child_in_its_attempt(isolated_sdk, prep, monkeypatch):
    from shine.asset_center.models import LibraryTaskAttempt, LibraryVersion
    from shine.asset_center.voice_execution import run_one_pending_job
    from shine.voiceops.local_sdk import encode_wav

    provider, loader, _, factory, _ = isolated_sdk
    client, _, settings = prep
    monkeypatch.setenv("SHINE_VOICE_SDK_PACKAGES", json.dumps(provider.packages))
    monkeypatch.setenv(
        "SHINE_VOICE_ROLE_RUNTIMES", json.dumps({k: v.model_dump(mode="json") for k, v in loader.runtimes.items()})
    )
    sample, _ = encode_wav([0.1] * 16000, 16000)
    media = client.post(
        "/api/v1/asset-center/uploads",
        data={"asset_kind": "audio", "purpose": "voice_sample"},
        files={"file": ("synthetic.wav", sample, "audio/wav")},
    )
    assert media.status_code == 201, media.text
    created = client.post("/api/v1/asset-center/voice-jobs", json={
        "idempotency_key": "isolated-library-sdk-clone",
        "mode": "AUTHORIZED_CLONE",
        "title": "Synthetic voice",
        "character": "Lin",
        "reference_media_id": media.json()["id"],
        "reference_text": "你好",
    })
    assert created.status_code == 202, created.text
    task_id = created.json()["item"]["id"]
    assert run_one_pending_job(factory, settings, "isolated-sdk-voice-worker") == 1
    task = client.get(f"/api/v1/asset-center/voice-jobs/{task_id}").json()
    assert task["status"] == "CANDIDATE_READY", task
    with factory() as db:
        attempt = db.scalar(select(LibraryTaskAttempt).where(LibraryTaskAttempt.task_id == task_id))
        assert attempt.payload["model_child_exit"]["returncode"] == 0
        assert attempt.payload["role_runtime"] == "base"
        assert db.get(ResourceLease, attempt.payload["lease_id"]).status == "RELEASED"
        assert db.get(LibraryVersion, task["result_version_id"]).is_mock


def test_live_model_child_blocks_human_abandon_without_consuming_grant(prep):
    from shine.comfyops.models import ApprovalGrant
    from shine.models import utcnow
    from tests.test_v13_preparation import HEADERS

    client, factory, _ = prep
    project = create_project(client)
    with factory.begin() as db:
        lease = ResourceLease(
            owner="synthetic-live-sdk",
            status="RECONCILE_REQUIRED",
            budget_mb=4000,
            exclusive=True,
            priority=10,
            expires_at=utcnow(),
        )
        db.add(lease)
        db.flush()
        row = AgentTaskRecord(
            project_id=project,
            role_id="model_call:TTS:design",
            input_hash="a" * 64,
            asset_manifest_hash="b" * 64,
            rulepack_id="test",
            rulepack_hash="c" * 64,
            model_profile_id="test",
            model_id="test",
            status="NEEDS_REVIEW",
            trace={
                "owner_pid": 999999999,
                "owner_birth": "dead-synthetic-owner",
                "is_mock": True,
                "model_child_pid": os.getpid(),
                "model_child_birth": process_identity(os.getpid()),
                "resource_id": lease.id,
            },
        )
        db.add(row)
        db.flush()
        identity, resource_id = row.id, lease.id
    grant = post(
        client, "/preparation/approval-grants", {"action": "model_reconcile", "target_id": identity}, operator=True
    )
    response = client.post(
        f"/api/v1/preparation/model-calls/{identity}/abandon",
        headers=HEADERS,
        json={
            "grant_id": grant["grant_id"],
            "snapshot": grant["snapshot"],
            "evidence": "Synthetic test human decision",
        },
    )
    assert response.status_code == 409 and response.json()["detail"] == "MODEL_CHILD_STILL_ALIVE_OR_UNVERIFIABLE"
    with factory() as db:
        assert db.get(ResourceLease, resource_id).status == "RECONCILE_REQUIRED"
        assert not db.get(ApprovalGrant, grant["grant_id"]).used

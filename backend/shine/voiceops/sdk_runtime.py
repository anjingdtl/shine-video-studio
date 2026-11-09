"""Private, one-shot SDK processes; no clone HTTP endpoint and no shell commands."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import wave
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import process_identity
from shine.comfyops.model_calls import CURRENT_MODEL_CALL
from shine.comfyops.scheduler import CURRENT_RESOURCE
from shine.models import AgentTaskRecord

CURRENT_LIBRARY_VOICE_ATTEMPT = ContextVar("current_library_voice_attempt", default=None)


def _record_child_evidence(fields):
    model_call = CURRENT_MODEL_CALL.get()
    if model_call is not None:
        factory, identity = model_call
        with factory.begin() as db:
            row = db.get(AgentTaskRecord, identity)
            row.trace = {**row.trace, **fields}
        return

    from shine.asset_center.models import LibraryTaskAttempt

    factory, identity = CURRENT_LIBRARY_VOICE_ATTEMPT.get()
    with factory.begin() as db:
        attempt = db.get(LibraryTaskAttempt, identity)
        attempt.payload = {**attempt.payload, **fields}


class RoleRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python: Path
    python_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    packages_dir: Path
    versions: dict[str, str] = Field(min_length=3)
    binaries: dict[str, str] = Field(default_factory=dict)
    timeout_s: int = Field(default=300, ge=1, le=900)
    budget_mb: int = Field(default=4000, ge=1, le=16384)


class SDKProcessLoader:
    """Only administrator environment configuration can select these runtimes."""

    is_mock = False
    is_external = True

    def __init__(self, runtimes):
        if not runtimes or set(runtimes) - {"design", "base", "asr", "align"}:
            raise ValueError("VOICE_ROLE_RUNTIME_MAPPING_INVALID")
        self.runtimes = {role: RoleRuntime.model_validate(spec) for role, spec in runtimes.items()}
        self.packages = {}
        self.current = None
        self.model_identity = None
        self.guard = threading.Lock()
        self.result_is_mock = False
        self.last_exit_proof = None

    def validate_package(self, role, package):
        runtime = self.runtimes.get(role)
        if runtime is None:
            raise ValueError("VOICE_ROLE_RUNTIME_MISSING")
        sdk = "qwen-tts" if role in {"design", "base"} else "qwen-asr"
        versions = runtime.versions
        if not {sdk, "transformers", "torch"}.issubset(versions):
            raise ValueError("VOICE_ROLE_RUNTIME_VERSIONS_INCOMPLETE")
        pinned = package.get("runtime_versions", {})
        if sdk not in pinned or any(versions.get(k) != v for k, v in pinned.items()):
            raise ValueError("VOICE_ROLE_RUNTIME_PACKAGE_MISMATCH")
        executable = runtime.python.resolve()
        if (
            not runtime.python.is_absolute()
            or not executable.is_file()
            or not runtime.packages_dir.is_absolute()
            or not runtime.packages_dir.is_dir()
        ):
            raise ValueError("VOICE_ROLE_RUNTIME_PATH_INVALID")
        with executable.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != runtime.python_sha256:
                raise ValueError("VOICE_ROLE_RUNTIME_PYTHON_CHANGED")
        for name, digest in runtime.binaries.items():
            binary = Path(name)
            if not binary.is_absolute() or not binary.is_file():
                raise ValueError("VOICE_ROLE_RUNTIME_BINARY_MISSING")
            with binary.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("VOICE_ROLE_RUNTIME_BINARY_CHANGED")
        self.packages[role] = package

    def __call__(self, role, path):
        if role not in self.packages:
            raise ValueError("VOICE_ROLE_RUNTIME_PACKAGE_NOT_VALIDATED")
        return SDKProxy(self, role, Path(path).resolve())

    def resource_budget_mb(self, role):
        if role not in self.runtimes:
            raise ValueError("VOICE_ROLE_RUNTIME_MISSING")
        return self.runtimes[role].budget_mb

    def release_models(self):
        if self.current is not None and self.current.poll() is None:
            raise ValueError("MODEL_CHILD_STILL_RUNNING")
        if self.model_identity and process_identity(self.model_identity[0]) == self.model_identity[1]:
            raise ValueError("MODEL_CHILD_STILL_RUNNING")
        # This process never imports the ML framework. The owned inference
        # process has exited, releasing its CPU/CUDA address space entirely.
        return {"released": True, "allocated_bytes": 0, "process_exit": self.last_exit_proof}

    def invoke(self, role, root, method, kwargs):
        tracked = CURRENT_MODEL_CALL.get() or CURRENT_LIBRARY_VOICE_ATTEMPT.get()
        if CURRENT_RESOURCE.get() is None or tracked is None:
            raise ValueError("VOICE_SDK_REQUIRES_TRACKED_SERIAL_STAGE")
        if not self.guard.acquire(blocking=False):
            raise ValueError("MODEL_CHILD_STILL_RUNNING")
        runtime = self.runtimes[role]
        try:
            self.validate_package(role, self.packages[role])
            request = {
                "role": role,
                "root": str(root),
                "method": method,
                "kwargs": kwargs,
                "versions": runtime.versions,
                "package": self.packages[role],
            }
            payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf8")
            if len(payload) > 32 * 1024**2:
                raise ValueError("SDK_REQUEST_TOO_LARGE")
            with tempfile.TemporaryDirectory(prefix="shine-sdk-") as folder:
                output = Path(folder) / "result.json"
                ready = Path(folder) / "ready.json"
                env = {
                    **{
                        key: value
                        for key, value in os.environ.items()
                        if key.upper()
                        in {
                            "SYSTEMROOT",
                            "WINDIR",
                            "TEMP",
                            "TMP",
                            "USERPROFILE",
                            "USERNAME",
                            "USER",
                            "LOGNAME",
                            "LNAME",
                            "APPDATA",
                            "LOCALAPPDATA",
                            "PROGRAMDATA",
                            "PATH",
                            "PATHEXT",
                            "COMSPEC",
                            "HOME",
                            "LANG",
                            "LC_ALL",
                            "LD_LIBRARY_PATH",
                            "DYLD_LIBRARY_PATH",
                        }
                        or key.upper().startswith(("CUDA_", "PYTORCH_"))
                    },
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "PYTHONNOUSERSITE": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                }
                if runtime.binaries:
                    env["PATH"] = os.pathsep.join(
                        [*(str(Path(p).parent) for p in runtime.binaries), env.get("PATH", "")]
                    )
                child = subprocess.Popen(
                    [
                        str(runtime.python),
                        "-s",
                        str(Path(__file__).with_name("sdk_child.py")),
                        "--packages",
                        str(runtime.packages_dir.resolve()),
                        "--output",
                        str(output),
                        "--ready",
                        str(ready),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self.current = child
                birth, model_pid = None, child.pid
                try:
                    deadline = time.monotonic() + min(runtime.timeout_s, 10)
                    while not ready.is_file() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if not ready.is_file():
                        raise ValueError("SDK_CHILD_STARTUP_UNCONFIRMED")
                    model_pid = json.loads(ready.read_text(encoding="utf8"))["pid"]
                    birth = process_identity(model_pid)
                    if not birth:
                        raise ValueError("MODEL_CHILD_IDENTITY_UNVERIFIABLE")
                    self.model_identity = model_pid, birth
                    _record_child_evidence({
                        "model_child_pid": model_pid,
                        "model_child_birth": birth,
                        "model_child_exit": None,
                        "role_runtime": role,
                        "runtime_versions": runtime.versions,
                        "runtime_python_sha256": runtime.python_sha256,
                        "runtime_budget_mb": runtime.budget_mb,
                        "sdk_adapter_sha256": hashlib.sha256(
                            Path(__file__).with_name("sdk_child.py").read_bytes()
                        ).hexdigest(),
                        "model_package_sha256": graph_hash(self.packages[role]),
                    })
                    if model_pid != child.pid:
                        raise ValueError("VOICE_RUNTIME_REQUIRES_DIRECT_PYTHON")
                    child.communicate(payload, timeout=runtime.timeout_s)
                except BaseException:
                    # Popen owns the original process handle on Windows; this
                    # never kills a process merely by a recycled numeric PID.
                    if child.stdin and not child.stdin.closed:
                        child.stdin.close()
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)
                    if birth and model_pid != child.pid:
                        deadline = time.monotonic() + 2
                        while process_identity(model_pid) == birth and time.monotonic() < deadline:
                            time.sleep(0.01)
                    raise
                finally:
                    self.last_exit_proof = {"pid": model_pid, "birth": birth, "returncode": child.poll()}
                    _record_child_evidence({"model_child_exit": self.last_exit_proof})
                if not output.is_file() or output.stat().st_size > 64 * 1024**2:
                    raise ValueError("SDK_CHILD_RESULT_MISSING_OR_OVERSIZED")
                result = json.loads(output.read_text(encoding="utf8"))
                if child.returncode != 0 or "error_code" in result:
                    raise RuntimeError("SDK_CHILD_FAILED:" + str(result.get("error_code", "NONZERO_EXIT"))[:80])
                self.result_is_mock = bool(result["is_mock"])
                return result
        finally:
            self.guard.release()


class SDKProxy:
    def __init__(self, loader, role, root):
        self.loader, self.role, self.root = loader, role, root

    def _call(self, method, kwargs):
        return self.loader.invoke(self.role, self.root, method, kwargs)

    def _voice(self, method, kwargs):
        result = self._call(method, kwargs)
        with wave.open(io.BytesIO(base64.b64decode(result["audio_pcm16_base64"], validate=True)), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getnframes() > audio.getframerate() * 120
            ):
                raise ValueError("SDK_AUDIO_CONTRACT_INVALID")
            import struct

            samples = [value[0] / 32767 for value in struct.iter_unpack("<h", audio.readframes(audio.getnframes()))]
            return [samples], audio.getframerate()

    def generate_voice_design(self, **kwargs):
        return self._voice("generate_voice_design", kwargs)

    def generate_voice_clone(self, **kwargs):
        return self._voice("generate_voice_clone", kwargs)

    def transcribe(self, **kwargs):
        result = self._call("transcribe", kwargs)
        return [SimpleNamespace(**result["transcript"])]

    def align(self, **kwargs):
        result = self._call("align", kwargs)
        return [[SimpleNamespace(**word) for word in result["words"]]]

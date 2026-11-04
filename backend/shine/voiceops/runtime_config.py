"""Local voice runtime configuration: which package serves which role, and how.

The setup capability writes this file; the voice provider reads it.  Environment
variables (``SHINE_VOICE_SDK_PACKAGES`` / ``SHINE_VOICE_ROLE_RUNTIMES``) keep
priority when they are present, so an administrator can still pin a runtime
without touching the file.

Nothing here starts a model or touches the network: the configuration only
names an already hash-verified local package and the interpreter that runs it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

VOICE_ROLES = ("design", "base", "asr", "align")
VOICE_ROLE_CAPABILITIES = {"design": "VOICE_DESIGN", "base": "VOICE_CLONE", "asr": "ASR", "align": "FORCED_ALIGNMENT"}
VOICE_ROLE_SDK = {"design": "qwen-tts", "base": "qwen-tts", "asr": "qwen-asr", "align": "qwen-asr"}


class RoleRuntime(BaseModel):
    """How one voice role is executed: an interpreter plus its pinned overlay."""

    model_config = ConfigDict(extra="forbid")

    python: Path
    python_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    packages_dir: Path
    versions: dict[str, str] = Field(min_length=3)
    binaries: dict[str, str] = Field(default_factory=dict)
    timeout_s: int = Field(default=300, ge=1, le=900)
    budget_mb: int = Field(default=4000, ge=1, le=16384)


class VoiceRuntimeConfig(BaseModel):
    """The whole local voice configuration of this machine."""

    model_config = ConfigDict(extra="forbid")

    packages: dict[str, str] = Field(default_factory=dict)
    role_runtimes: dict[str, RoleRuntime] = Field(default_factory=dict)

    def routing(self) -> dict[str, str]:
        return {role: identity for role, identity in self.packages.items() if role in VOICE_ROLES}

    def runtimes(self) -> dict[str, RoleRuntime]:
        return {role: spec for role, spec in self.role_runtimes.items() if role in VOICE_ROLES}


def config_path(settings) -> Path:
    return settings.comfyops_dir / "voice-runtime.json"


def _parse_mapping(raw: str, label: str) -> dict:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def load_voice_config(settings) -> VoiceRuntimeConfig:
    """Read the effective configuration: environment first, then the file.

    A malformed environment value is ignored rather than silently replaced by
    the file: an administrator's pinned runtime must not be overridden by a
    leftover file, and the caller reports the problem through readiness.
    """
    env_packages = os.getenv("SHINE_VOICE_SDK_PACKAGES", "").strip()
    env_runtimes = os.getenv("SHINE_VOICE_ROLE_RUNTIMES", "").strip()
    stored: dict = {}
    path = config_path(settings)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                stored = loaded
        except (OSError, ValueError):
            stored = {}
    packages = _parse_mapping(env_packages, "packages") if env_packages else stored.get("packages") or {}
    runtimes_raw = _parse_mapping(env_runtimes, "role_runtimes") if env_runtimes else stored.get("role_runtimes") or {}
    runtimes: dict[str, RoleRuntime] = {}
    for role, spec in runtimes_raw.items():
        if role not in VOICE_ROLES:
            continue
        try:
            runtimes[role] = RoleRuntime.model_validate(spec)
        except Exception:  # noqa: BLE001 - an unusable entry is simply not configured
            continue
    try:
        return VoiceRuntimeConfig(packages=packages, role_runtimes=runtimes)
    except Exception:  # noqa: BLE001 - fall back to an empty, honest configuration
        return VoiceRuntimeConfig()


def save_voice_config(settings, config: VoiceRuntimeConfig) -> Path:
    """Persist the configuration atomically so a crash cannot truncate it."""
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "packages": config.routing(),
            "role_runtimes": {role: spec.model_dump(mode="json") for role, spec in config.runtimes().items()},
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    return path


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_role_runtime(role: str, runtime: RoleRuntime, package: dict | None) -> list[str]:
    """Report why a role runtime is unusable; an empty list means it is ready.

    The checks mirror the runtime's own contract: an absolute interpreter with
    the pinned digest, an absolute overlay directory, and versions that match
    the package's pinned runtime versions.
    """
    codes: list[str] = []
    if not runtime.python.is_absolute():
        codes.append("VOICE_RUNTIME_PYTHON_NOT_ABSOLUTE")
    elif not runtime.python.is_file():
        codes.append("VOICE_RUNTIME_PYTHON_MISSING")
    else:
        try:
            if file_digest(runtime.python) != runtime.python_sha256:
                codes.append("VOICE_RUNTIME_PYTHON_CHANGED")
        except OSError:
            codes.append("VOICE_RUNTIME_PYTHON_UNREADABLE")
    if not runtime.packages_dir.is_absolute():
        codes.append("VOICE_RUNTIME_PACKAGES_DIR_NOT_ABSOLUTE")
    elif not runtime.packages_dir.is_dir():
        codes.append("VOICE_RUNTIME_PACKAGES_DIR_MISSING")
    sdk = VOICE_ROLE_SDK.get(role)
    if not sdk or not {sdk, "transformers", "torch"}.issubset(runtime.versions):
        codes.append("VOICE_RUNTIME_VERSIONS_INCOMPLETE")
    if package:
        pinned = package.get("runtime_versions") or {}
        if not pinned:
            codes.append("VOICE_PACKAGE_RUNTIME_VERSIONS_UNPINNED")
        elif any(runtime.versions.get(name) != version for name, version in pinned.items()):
            codes.append("VOICE_RUNTIME_PACKAGE_MISMATCH")
    return codes


def package_files_present(package: dict, root: Path) -> bool:
    """True when every declared file exists with the declared size.

    Presence is not proof: the digest check belongs to the install reconcile
    path.  This is a cheap readiness signal, never a verification claim.
    """
    files = package.get("files") or []
    if not files:
        return False
    for entry in files:
        target = root / entry.get("relative_path", "")
        try:
            if not target.is_file() or target.stat().st_size != entry.get("size"):
                return False
        except OSError:
            return False
    return True

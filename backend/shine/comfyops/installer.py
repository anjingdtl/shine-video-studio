"""Local controlled downloader/installer. Cloud APIs never select this executor.

Transport and subprocess ports are injectable so tests execute these exact
production algorithms against bytes and fake commands, without model downloads.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)


class PackageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    kind: str = Field(pattern=r"^(model|node)$")
    revision: str = Field(min_length=1, max_length=128)
    license_id: str = Field(min_length=1)
    usage: str = Field(pattern=r"^(research|commercial)$")
    family: str = "UNSPECIFIED"
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    resource_measurement: str = Field(default="UNKNOWN", pattern=r"^(UNKNOWN|ESTIMATED|KNOWN)$")
    ram_mb: int | None = Field(default=None, ge=0)
    license_url: str = ""
    precision: str = "UNKNOWN"
    quantization: str = "UNKNOWN"
    resource_mb: int | None = Field(default=None, ge=0)
    files: list[PackageFile] = Field(min_length=1, max_length=100)
    dependencies: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def pinned(self):
        if self.revision.lower() in {"main", "master", "latest", "head"}:
            raise ValueError("VERSION_LOCK_REQUIRED")
        if self.kind == "node" and not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise ValueError("NODE_COMMIT_REQUIRED")
        paths = [x.relative_path.casefold() for x in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("DUPLICATE_DESTINATION")
        return self


def safe_target(root: Path, relative: str) -> Path:
    windows = PureWindowsPath(relative)
    if (
        windows.is_absolute()
        or windows.drive
        or relative.startswith(("/", "\\"))
        or any(part in {"..", "."} for part in windows.parts)
        or ":" in relative
        or any(
            p.rstrip(". ") != p
            or p.split(".")[0].upper()
            in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            for p in windows.parts
        )
    ):
        raise ValueError("UNSAFE_PATH")
    target = root.joinpath(*windows.parts).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("UNSAFE_PATH")
    return target


def check_url(url: str, hosts: set[str]):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("UNTRUSTED_DOWNLOAD_URL")


class HTTPTransport:
    def stream(self, url, offset):
        # No redirects, including same-host redirects: each target needs its own approved manifest.
        return httpx.stream(
            "GET", url, headers={"Range": f"bytes={offset}-"} if offset else {}, follow_redirects=False, timeout=30
        )


@dataclass
class ControlledDownloader:
    hosts: set[str]
    transport: object
    max_bytes: int = 100 * 1024**3

    def download(self, spec: PackageFile, cache: Path):
        check_url(spec.url, self.hosts)
        if spec.size > self.max_bytes:
            raise ValueError("DOWNLOAD_BUDGET_EXCEEDED")
        cache.mkdir(parents=True, exist_ok=True)
        final = cache / spec.sha256
        if final.exists() and self.valid(final, spec):
            return final
        partial = cache / (spec.sha256 + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > spec.size:
            partial.unlink()
            offset = 0
        with self.transport.stream(spec.url, offset) as response:
            if response.status_code not in {200, 206}:
                raise ValueError("DOWNLOAD_HTTP_REJECTED")
            if response.status_code == 206:
                expected = f"bytes {offset}-{spec.size - 1}/{spec.size}"
                if response.headers.get("content-range") != expected:
                    raise ValueError("INVALID_RESUME_RANGE")
            else:
                offset = 0  # Server ignored Range; safely restart rather than append.
            with partial.open("ab" if offset else "wb") as output:
                size = offset
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > spec.size:
                        output.close()
                        partial.unlink(missing_ok=True)
                        raise ValueError("DOWNLOAD_SIZE_EXCEEDED")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if not self.valid(partial, spec):
            partial.unlink(missing_ok=True)
            raise ValueError("FILE_CHECKSUM_FAILED")
        os.replace(partial, final)
        return final

    @staticmethod
    def valid(path, spec):
        with path.open("rb") as stream:
            return path.stat().st_size == spec.size and hashlib.file_digest(stream, "sha256").hexdigest() == spec.sha256


class CommandExecutor:
    def run(self, args: list[str], *, cwd: Path):
        return subprocess.run(args, cwd=cwd, shell=False, check=True, capture_output=True, timeout=600)


class ComfyCLIAdapter:
    """Version-pinned executable, fixed command construction; no arbitrary shell."""

    def __init__(self, executable: Path, digest: str, executor=None):
        self.executable, self.digest = executable, digest
        self.executor = executor or CommandExecutor()

    def snapshot(self, workspace: Path):
        if hashlib.sha256(self.executable.read_bytes()).hexdigest() != self.digest:
            raise ValueError("CLI_HASH_CHANGED")
        return self.executor.run(
            [str(self.executable), "--workspace", str(workspace), "node", "save-snapshot"], cwd=workspace
        )


class ManagerAdapter:
    """Manager snapshots are auxiliary, never a promise of full environment rollback."""

    def __init__(self, python: Path, cli: Path, digest: str, executor=None):
        self.python, self.cli, self.digest = python, cli, digest
        self.executor = executor or CommandExecutor()

    def snapshot(self, workspace: Path, output: Path):
        if hashlib.sha256(self.cli.read_bytes()).hexdigest() != self.digest:
            raise ValueError("MANAGER_HASH_CHANGED")
        return self.executor.run(
            [str(self.python), str(self.cli), "save-snapshot", "--output", str(output)], cwd=workspace
        )


def extract_zip(archive: Path, target: Path, *, max_bytes=1024**3):
    with zipfile.ZipFile(archive) as zipped:
        total = 0
        for entry in zipped.infolist():
            destination = safe_target(target, entry.filename.rstrip("/"))
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP_SYMLINK_FORBIDDEN")
            total += entry.file_size
            if total > max_bytes:
                raise ValueError("ZIP_BUDGET_EXCEEDED")
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)


@contextmanager
def exclusive_file_lock(path):
    handle = path.open("x")
    try:
        yield
    finally:
        handle.close()  # Windows forbids unlinking an open file.
        path.unlink(missing_ok=True)


class LocalInstaller:
    """Atomic isolated package directory replacement with rollback journal.

    Does not run node setup.py/pip hooks; third-party dependency execution is a
    separate, higher-risk operation. Never mutates an existing production instance.
    """

    def __init__(self, root: Path, downloader: ControlledDownloader):
        self.root, self.downloader = root.resolve(), downloader

    def install(self, spec: PackageSpec):
        self.root.mkdir(parents=True, exist_ok=True)
        destination = safe_target(self.root, spec.name)
        stage = safe_target(self.root, spec.name + ".staging")
        backup = safe_target(self.root, spec.name + ".rollback")
        journal = safe_target(self.root, spec.name + ".journal")
        lock = safe_target(self.root, spec.name + ".lock")
        with exclusive_file_lock(lock):
            if journal.exists() or stage.exists() or backup.exists():
                raise ValueError("INSTALL_RECOVERY_REQUIRED")
            try:
                stage.mkdir()
                for file in spec.files:
                    source = self.downloader.download(file, self.root / ".cache")
                    target = safe_target(stage, file.relative_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                journal.write_text("STAGED", encoding="utf-8")
                if destination.exists():
                    os.replace(destination, backup)
                os.replace(stage, destination)
                journal.write_text("INSTALLED", encoding="utf-8")
                for file in spec.files:
                    if not self.downloader.valid(safe_target(destination, file.relative_path), file):
                        raise ValueError("INSTALL_VERIFY_FAILED")
                journal.unlink()
                if backup.exists():
                    shutil.rmtree(backup)
                return {"status": "AVAILABLE", "files": [f.model_dump() for f in spec.files]}
            except Exception:
                if journal.exists():
                    if backup.exists():
                        if destination.exists():
                            shutil.rmtree(destination)
                        os.replace(backup, destination)
                    elif not stage.exists() and destination.exists():
                        shutil.rmtree(destination)
                    journal.unlink()
                if stage.exists():
                    shutil.rmtree(stage)
                raise

    def recover(self, name: str):
        destination = safe_target(self.root, name)
        journal = safe_target(self.root, name + ".journal")
        backup = safe_target(self.root, name + ".rollback")
        if journal.exists():
            if backup.exists():
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(backup, destination)
            elif journal.read_text() == "INSTALLED" and destination.exists():
                shutil.rmtree(destination)
            journal.unlink()
        stage = safe_target(self.root, name + ".staging")
        if stage.exists():
            shutil.rmtree(stage)
        return {"status": "ROLLED_BACK"}

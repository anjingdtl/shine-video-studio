"""Local controlled downloader/installer. Cloud APIs never select this executor.

Transport and subprocess ports are injectable so tests execute these exact
production algorithms against bytes and fake commands, without model downloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

REDIRECT_POLICY_BY_ORIGIN = {
    "huggingface.co": {"us.aws.cdn.hf.co"},
    "github.com": {"codeload.github.com", "release-assets.githubusercontent.com"},
}

logger = logging.getLogger(__name__)


class PackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)
    redirect_hosts: list[
        Literal["huggingface.co", "us.aws.cdn.hf.co", "codeload.github.com", "release-assets.githubusercontent.com"]
    ] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def redirect_policy(self):
        if self.redirect_hosts:
            origin = urlsplit(self.url).hostname
            allowed = REDIRECT_POLICY_BY_ORIGIN.get(origin, set()) | {origin}
            if allowed is None or not set(self.redirect_hosts) <= allowed:
                raise ValueError("UNTRUSTED_DOWNLOAD_URL")
            if len(set(self.redirect_hosts)) != len(self.redirect_hosts):
                raise ValueError("DUPLICATE_REDIRECT_HOST")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        # Preserve the serialized bytes/hash contract of existing no-redirect plans.
        if not self.redirect_hosts:
            result.pop("redirect_hosts", None)
        return result


class SourceWheelBuild(BaseModel):
    """An explicitly approved source input, built without fetching build dependencies."""

    model_config = ConfigDict(extra="forbid")
    relative_path: str
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+!-]{0,99}$")
    backend: Literal["setuptools"] = "setuptools"
    license_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def known_license(self):
        if self.license_id.strip().lower() in {'', 'unknown', 'unverified'}:
            raise ValueError('SOURCE_LICENSE_NOT_APPROVED')
        return self


class PackageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    kind: str = Field(pattern=r"^(model|node|source|wheel)$")
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
    files: list[PackageFile] = Field(min_length=1, max_length=256)
    dependencies: dict[str, str] = Field(default_factory=dict)
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    source_builds: list[SourceWheelBuild] = Field(default_factory=list, max_length=10)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        if not self.source_builds:
            result.pop("source_builds", None)  # Preserve historical package/approval hashes.
        return result

    @model_validator(mode="after")
    def pinned(self):
        if self.revision.lower() in {"main", "master", "latest", "head"}:
            raise ValueError("VERSION_LOCK_REQUIRED")
        if self.kind == "node" and not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise ValueError("NODE_COMMIT_REQUIRED")
        paths = [x.relative_path.casefold() for x in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("DUPLICATE_DESTINATION")
        if self.source_builds:
            sources = [build.relative_path.casefold() for build in self.source_builds]
            if (self.kind != "wheel" or len(set(sources)) != len(sources)
                    or any(path not in paths or not path.endswith('.tar.gz') for path in sources)):
                raise ValueError("PINNED_SOURCE_BUILD_INVALID")
            if {path for path in paths if not path.endswith('.whl')} != set(sources):
                raise ValueError("UNDECLARED_SOURCE_BUILD")
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


def check_url(url: str, hosts: set[str], *, signed_query=False):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
        or (parsed.query and not signed_query)
    ):
        raise ValueError("UNTRUSTED_DOWNLOAD_URL")


# Deliberate, documented widening for the first supported new-machine deployment:
# source archives (codeload), GitHub release assets, PyPI wheel bytes and the
# official CUDA wheel index. Everything else stays refused.
TRUSTED_DOWNLOAD_HOSTS = frozenset(
    {
        "huggingface.co",
        "raw.githubusercontent.com",
        "github.com",
        "codeload.github.com",
        "release-assets.githubusercontent.com",
        "files.pythonhosted.org",
        "download.pytorch.org",
        "download-r2.pytorch.org",  # Exact host linked by the official CUDA wheel indexes.
        "hf-mirror.com",  # Byte-identical mirror used only when huggingface.co is unreachable.
        "cas-bridge.xethub.hf.co",  # HF's signed download endpoint linked by that mirror.
    }
)

# The mirror serves small files from its own cache and redirects large LFS files
# to the signed HF download endpoint. Only those two hosts may be followed; every
# byte is still verified against the pinned sha256 and size.
MIRROR_REDIRECT_HOSTS = frozenset({"hf-mirror.com", "cas-bridge.xethub.hf.co"})


def mirror_url(url: str) -> str | None:
    """Return the byte-identical mirror URL of a pinned HuggingFace model file.

    This is a transport alternative only. The caller still verifies the pinned
    sha256 and byte size, so a mirror that serves different bytes fails loudly
    instead of being accepted.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "huggingface.co" or parsed.query or parsed.fragment:
        return None
    parts = parsed.path.strip("/").split("/")
    # huggingface.co/<owner>/<repo>/resolve/<revision>/<relative path...>
    if len(parts) < 5 or parts[2] != "resolve":
        return None
    owner, repo, _, revision, *rest = parts
    if not owner or not repo or not rest:
        return None
    return f"https://hf-mirror.com/{owner}/{repo}/resolve/{revision}/{'/'.join(rest)}"


class DownloadStreamInterrupted(ValueError):
    """A fixed inbound GET stopped; only verified byte-range continuation is safe."""


class DownloadRetryable(ValueError):
    """The server asked for a bounded wait; the same immutable bytes may be asked again."""

    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"DOWNLOAD_RETRYABLE:{status}")
        self.status = status
        self.retry_after = retry_after


class InstallationInterrupted(ValueError):
    """The operator paused or cancelled; the install stops at a safe point."""


#: A server answer that asks for a bounded wait instead of a permanent refusal.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
#: A Retry-After header is honoured, but never without an upper bound.
MAX_RETRY_AFTER_SECONDS = 60.0
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


def _retry_after_seconds(response) -> float | None:
    """The Retry-After of a retryable answer, or None when it is unusable."""
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HTTPTransport:
    @contextmanager
    def stream(self, url, offset, *, end=None):
        # Retry only connection establishment and the response headers for this
        # immutable GET, before any response body byte is consumed.  A body that
        # stops halfway is never replayed here: the caller continues from the
        # confirmed bytes with a new Range.
        headers = {"Range": f"bytes={offset}-{end if end is not None else ''}"} if offset or end is not None else {}
        if end is not None:
            headers["Accept-Encoding"] = "identity"
        with ExitStack() as stack:
            for attempt in range(3):
                try:
                    response = stack.enter_context(httpx.stream(
                        "GET", url, headers=headers,
                        follow_redirects=False, timeout=httpx.Timeout(30, read=120),
                    ))
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                    # The request never produced a body byte, so asking again is
                    # safe.  A protocol error is not retried: the peer speaks a
                    # different protocol and repeating the request would only
                    # hide that.
                    if attempt == 2:
                        raise ValueError(f"DOWNLOAD_CONNECTION_FAILED:{type(exc).__name__}") from None
                except httpx.HTTPError as exc:
                    raise ValueError(f"DOWNLOAD_REQUEST_FAILED:{type(exc).__name__}") from None
            try:
                yield response
            except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                raise DownloadStreamInterrupted(f"DOWNLOAD_STREAM_FAILED:{type(exc).__name__}") from None
            except httpx.HTTPError as exc:
                # Keep transient signed URLs out of API responses and logs.
                raise ValueError(f"DOWNLOAD_STREAM_FAILED:{type(exc).__name__}") from None

    @contextmanager
    def stream_approved(self, spec: PackageFile, offset, *, end=None):
        """Follow only the per-file policy included in the human-approved plan.

        HF CDN signatures are transient: never persist their query strings or
        forward business credentials. Every hop uses only the original Range.
        """
        url = spec.url
        for hop in range(6):
            with self.stream(url, offset, end=end) as response:
                if response.status_code not in {301, 302, 303, 307, 308}:
                    yield response
                    return
                if hop == 5:
                    raise ValueError("DOWNLOAD_REDIRECT_LIMIT")
                location = response.headers.get("location")
                if not location:
                    raise ValueError("DOWNLOAD_REDIRECT_LOCATION_MISSING")
                target = urljoin(url, location)
                check_url(target, set(spec.redirect_hosts), signed_query=True)
                url = target

    def stream_range(self, spec: PackageFile, offset, end):
        if spec.redirect_hosts:
            return self.stream_approved(spec, offset, end=end)
        return self.stream(spec.url, offset, end=end)


@dataclass
class ControlledDownloader:
    hosts: set[str]
    transport: object
    max_bytes: int = 100 * 1024**3
    max_stream_attempts: int = 3
    segment_bytes: int = 64 * 1024**2
    # Once the primary origin is proven unreachable, later files in the same
    # installation skip the repeated connect timeouts and try the mirror directly.
    primary_unreachable: bool = False
    source_preference: str = "AUTO"
    # The wait before a retryable answer is asked again, and the jitter source.
    # Both are injectable so the production algorithm is testable without
    # spending real seconds.
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[], float] = random.random

    def download(self, spec: PackageFile, cache: Path, *, progress=None):
        check_url(spec.url, self.hosts)
        if self.source_preference not in {"AUTO", "MIRROR_FIRST", "OFFICIAL_FIRST"}:
            raise ValueError("DOWNLOAD_SOURCE_PREFERENCE_INVALID")
        if spec.size > self.max_bytes:
            raise ValueError("DOWNLOAD_BUDGET_EXCEEDED")
        if type(self.max_stream_attempts) is not int or not 1 <= self.max_stream_attempts <= 3:
            raise ValueError("DOWNLOAD_RESUME_BUDGET_INVALID")
        if type(self.segment_bytes) is not int or not 0 < self.segment_bytes <= 64 * 1024**2:
            raise ValueError("DOWNLOAD_SEGMENT_BUDGET_INVALID")
        cache.mkdir(parents=True, exist_ok=True)
        final = cache / spec.sha256
        if final.exists() and self.valid(final, spec):
            return final
        partial = cache / (spec.sha256 + ".part")
        if partial.exists() and partial.stat().st_size >= spec.size and not self.valid(partial, spec):
            partial.unlink()
        candidates = self._candidates(spec)
        candidate_index = 0
        retries = 0
        while not partial.exists() or partial.stat().st_size < spec.size:
            try:
                self._transfer(spec, partial, candidates, candidate_index, progress=progress)
            except DownloadRetryable as exc:
                # The server asked for a bounded wait.  The pinned bytes are
                # immutable, so the same request may be asked again later.
                retries += 1
                if retries >= self.max_stream_attempts:
                    raise ValueError(f"DOWNLOAD_HTTP_REJECTED:{exc.status}") from None
                self.sleep(self._backoff(retries, exc.retry_after))
            except DownloadStreamInterrupted:
                retries += 1
                if retries >= self.max_stream_attempts:
                    # A disconnected response may already contain the entire
                    # immutable file; only exact size AND hash can establish it.
                    if partial.exists() and self.valid(partial, spec):
                        break
                    raise
                # A broken transfer continues from the confirmed bytes against
                # the next candidate; the mirror serves the same pinned bytes
                # and the primary may be tried again after the mirror failed.
                candidate_index = (candidate_index + 1) % len(candidates)
            except ValueError as exc:
                if not str(exc).startswith("DOWNLOAD_CONNECTION_FAILED"):
                    raise
                retries += 1
                if candidates[candidate_index][0] == "primary":
                    self.primary_unreachable = True
                if candidate_index + 1 >= len(candidates) or retries >= self.max_stream_attempts:
                    raise
                candidate_index += 1
            else:
                # Reset only after a complete, range/length-checked segment.
                # Each success completes the next fixed block, bounding requests
                # by 3 * (ceil(file_size / segment_bytes) + 1).
                retries = 0
        if not self.valid(partial, spec):
            partial.unlink(missing_ok=True)
            raise ValueError("FILE_CHECKSUM_FAILED")
        os.replace(partial, final)
        return final

    def _candidates(self, spec: PackageFile):
        """The transport candidates of one pinned file, primary origin first.

        The mirror is only a transport alternative for the same pinned bytes:
        the size and the sha256 still decide whether the result is acceptable.
        """
        candidates = []
        mirror = mirror_url(spec.url)
        mirror_allowed = mirror is not None and urlsplit(mirror).hostname in self.hosts
        if self.source_preference == "MIRROR_FIRST" and mirror_allowed:
            return [("mirror", mirror), ("primary", spec.url)]
        if not (self.source_preference == "AUTO" and self.primary_unreachable and mirror_allowed):
            candidates.append(("primary", spec.url))
        if mirror_allowed:
            candidates.append(("mirror", mirror))
        return candidates

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with jitter, honouring a capped Retry-After."""
        if retry_after is not None:
            return max(0.0, min(float(retry_after), MAX_RETRY_AFTER_SECONDS))
        delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        return delay * (0.5 + self.jitter() / 2)

    @contextmanager
    def _open(self, spec: PackageFile, url: str, offset, end):
        """Open one candidate URL for the pinned file.

        Only the transport differs between candidates; every response is still
        subjected to the exact range, size and hash verification.
        """
        if url == spec.url:
            with self._open_primary(spec, offset, end) as response:
                yield response
            return
        logger.warning("installer_mirror_fallback file=%s host=%s", spec.relative_path, urlsplit(url).hostname)
        with self._stream_mirror(url, offset, end) as response:
            yield response

    @contextmanager
    def _stream_mirror(self, url, offset, end):
        # The mirror redirects cached files to itself and LFS objects to HF's
        # signed endpoint. Only those hosts may be followed, and the original
        # Range is preserved on every hop.
        for hop in range(6):
            with self.transport.stream(url, offset, end=end) as response:
                if response.status_code not in {301, 302, 303, 307, 308}:
                    yield response
                    return
                if hop == 5:
                    raise ValueError("DOWNLOAD_REDIRECT_LIMIT")
                location = response.headers.get("location")
                if not location:
                    raise ValueError("DOWNLOAD_REDIRECT_LOCATION_MISSING")
                target = urljoin(url, location)
                check_url(target, set(MIRROR_REDIRECT_HOSTS), signed_query=True)
                url = target

    def _open_primary(self, spec: PackageFile, offset, end):
        range_stream = getattr(self.transport, "stream_range", None)
        if end is not None:
            if range_stream is None:
                raise ValueError("TRANSPORT_REDIRECT_POLICY_UNSUPPORTED")
            return range_stream(spec, offset, end)
        if spec.redirect_hosts:
            approved_stream = getattr(self.transport, "stream_approved", None)
            if approved_stream is None:
                raise ValueError("TRANSPORT_REDIRECT_POLICY_UNSUPPORTED")
            return approved_stream(spec, offset)
        return self.transport.stream(spec.url, offset)

    def _transfer(self, spec: PackageFile, partial: Path, candidates, candidate_index, *, progress=None):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= spec.size:
            partial.unlink()
            offset = 0
        range_stream = getattr(self.transport, "stream_range", None)
        end = (
            min((offset // self.segment_bytes + 1) * self.segment_bytes, spec.size) - 1
            if range_stream and spec.size > self.segment_bytes else None
        )
        url = candidates[candidate_index][1]
        with self._open(spec, url, offset, end) as response:
            if response.status_code in RETRYABLE_STATUS:
                raise DownloadRetryable(response.status_code, _retry_after_seconds(response))
            if response.status_code not in {200, 206}:
                raise ValueError("DOWNLOAD_HTTP_REJECTED")
            if response.status_code == 206:
                expected = f"bytes {offset}-{end if end is not None else spec.size - 1}/{spec.size}"
                if response.headers.get("content-range") != expected:
                    raise ValueError("INVALID_RESUME_RANGE")
            else:
                # The server ignored the Range header.  The whole file is
                # rewritten instead of appending a full response to bytes that
                # are already on disk.
                offset = 0
                end = None
            expected_size = spec.size if end is None else end + 1
            reported = offset
            with partial.open("ab" if offset else "wb") as output:
                size = offset
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > expected_size:
                        output.close()
                        partial.unlink(missing_ok=True)
                        raise ValueError("DOWNLOAD_SIZE_EXCEEDED")
                    output.write(chunk)
                    if progress is not None and size - reported >= 4 * 1024**2:
                        reported = size
                        progress(partial.stat().st_size)
                output.flush()
                os.fsync(output.fileno())
            if progress is not None:
                progress(partial.stat().st_size)
            if size != expected_size:
                partial.unlink(missing_ok=True)
                raise ValueError("DOWNLOAD_SEGMENT_SIZE_MISMATCH" if end is not None else "FILE_CHECKSUM_FAILED")

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


def durable_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def process_identity(pid):
    """PID plus OS process birth identity; uncertainty is never interpreted as death."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x101000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return None
            raise ValueError("LOCK_OWNER_LIVENESS_UNKNOWN")
        try:
            # An exited Windows process remains queryable while another process
            # retains a handle. Birth time alone therefore does not prove liveness.
            state = kernel.WaitForSingleObject(handle, 0)
            if state == 0:  # signaled: terminated, even if the PID object still exists
                return None
            if state != 258:  # WAIT_TIMEOUT is the only verified live state
                raise ValueError("LOCK_OWNER_LIVENESS_UNKNOWN")
            values = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                raise ValueError("LOCK_OWNER_LIVENESS_UNKNOWN")
            return str(values[0].value)
        finally:
            kernel.CloseHandle(handle)
    try:
        # comm may contain spaces/parentheses; field 22 follows its closing parenthesis.
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return stat[19]
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("LOCK_OWNER_LIVENESS_UNKNOWN") from exc


@contextmanager
def exclusive_file_lock(path):
    """Stable OS guard serializes owner inspection, install and recovery across processes.

    The guard inode is never unlinked. A dead owner's marker is removed only while
    holding this guard and after comparing the OS birth identity (PID reuse safe).
    """
    guard = path.with_suffix(path.suffix + ".guard")
    with guard.open("a+b") as handle:
        if guard.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("INSTALL_OWNER_ACTIVE") from exc
        try:
            if path.exists():
                try:
                    owner = json.loads(path.read_text())
                    birth = process_identity(owner["pid"])
                    if birth is not None and birth == owner["birth"]:
                        raise ValueError("INSTALL_OWNER_ACTIVE")
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("LOCK_OWNER_UNVERIFIABLE") from exc
            durable_json(path, {"pid": os.getpid(), "birth": process_identity(os.getpid())})
            try:
                yield
            finally:
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LocalInstaller:
    """Durable isolated package swap; every crash window has an exclusive recovery path."""

    def __init__(self, root: Path, downloader: ControlledDownloader, checkpoint=None, *,
                 progress=None, should_stop=None):
        self.root, self.downloader = root.resolve(), downloader
        self.checkpoint = checkpoint or (lambda phase: None)
        # The progress callback receives the bytes that are really on disk.
        self.progress = progress or (lambda component, relative_path, confirmed, total: None)
        # The stop callback is asked between files; it returns a reason to stop
        # at that safe point, or None to continue.
        self.should_stop = should_stop or (lambda: None)
        self._current_component = None

    def install(self, spec: PackageSpec):
        self.root.mkdir(parents=True, exist_ok=True)
        self._current_component = spec.name
        destination = safe_target(self.root, spec.name)
        stage = safe_target(self.root, spec.name + ".staging")
        backup = safe_target(self.root, spec.name + ".rollback")
        journal = safe_target(self.root, spec.name + ".journal")
        lock = safe_target(self.root, spec.name + ".lock")
        with exclusive_file_lock(lock):
            if journal.exists() or stage.exists() or backup.exists():
                raise ValueError("INSTALL_RECOVERY_REQUIRED")
            state = {"schema": 1, "had_destination": destination.exists(), "spec": spec.model_dump()}

            def record(phase):
                state["phase"] = phase
                durable_json(journal, state)
                self.checkpoint(phase)

            try:
                record("DOWNLOADING")
                stage.mkdir()
                for file in spec.files:
                    reason = self.should_stop()
                    if reason:
                        # A safe point between files: the reusable .part files
                        # and the already verified files are kept.
                        raise InstallationInterrupted(reason)
                    source = self.downloader.download(
                        file,
                        self.root / ".cache",
                        progress=lambda confirmed, pinned=file: self._emit(pinned, confirmed),
                    )
                    target = safe_target(stage, file.relative_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    with target.open("r+b") as stream:
                        os.fsync(stream.fileno())
                record("STAGED")
                record("BACKUP_INTENT")
                if destination.exists():
                    os.replace(destination, backup)
                    sync_directory(self.root)
                record("BACKED_UP")
                record("SWAP_INTENT")
                os.replace(stage, destination)
                sync_directory(self.root)
                record("INSTALLED")
                for file in spec.files:
                    if not self.downloader.valid(safe_target(destination, file.relative_path), file):
                        raise ValueError("INSTALL_VERIFY_FAILED")
                record("VERIFIED")
                # VERIFIED is the durable commit point; cleanup can resume after any crash.
                if backup.exists():
                    shutil.rmtree(backup)
                record("CLEANED")
                journal.unlink()
                sync_directory(self.root)
                return {"status": "AVAILABLE", "files": [f.model_dump() for f in spec.files]}
            except Exception:
                self._recover(spec.name)
                raise

    def _emit(self, file, confirmed: int) -> None:
        """Report the bytes that are really on disk for one pinned file."""
        self.progress(self._current_component, file.relative_path, int(confirmed or 0), file.size)

    def _recover(self, name):
        destination = safe_target(self.root, name)
        journal = safe_target(self.root, name + ".journal")
        backup = safe_target(self.root, name + ".rollback")
        stage = safe_target(self.root, name + ".staging")
        status = "ROLLED_BACK"
        if journal.exists():
            try:
                state = json.loads(journal.read_text())
            except json.JSONDecodeError as exc:
                # Compatibility recovery for the old writer, only with its known phases.
                legacy = journal.read_text()
                if legacy not in {"STAGED", "INSTALLED"}:
                    raise ValueError("INSTALL_JOURNAL_UNVERIFIABLE") from exc
                state = {"phase": legacy, "had_destination": backup.exists()}
            if state["phase"] in {"VERIFIED", "CLEANED"}:
                spec = PackageSpec.model_validate(state["spec"])
                if not all(
                    ControlledDownloader.valid(safe_target(destination, f.relative_path), f) for f in spec.files
                ):
                    raise ValueError("INSTALL_COMMITTED_FILES_CHANGED")
                status = "AVAILABLE"
                if backup.exists():
                    shutil.rmtree(backup)
            elif backup.exists():
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(backup, destination)
            elif not state["had_destination"] and destination.exists():
                shutil.rmtree(destination)
            elif state["had_destination"] and not destination.exists():
                raise ValueError("INSTALL_BACKUP_MISSING")
            if stage.exists():
                shutil.rmtree(stage)
            journal.unlink()
            sync_directory(self.root)
        elif stage.exists() or backup.exists():
            raise ValueError("INSTALL_JOURNAL_MISSING")
        return {"status": status}

    def recover(self, name: str):
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(safe_target(self.root, name + ".lock")):
            return self._recover(name)

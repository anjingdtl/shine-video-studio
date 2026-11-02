"""Owned renderer drain and history snapshot primitive.

This component only acts on a live process group created by this supervisor.
It is not yet wired to the worker, restart or crash recovery, and does not
establish that arbitrary ComfyUI extensions cannot bypass the queue guard.
"""

import hashlib
import json
import os
import re
import socket
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from shine.comfyops.handoff_memory import evaluate_readiness, probe_memory
from shine.comfyops.installer import process_identity
from shine.security import is_loopback_url


def windows_port_listeners(port):
    """Read all Windows IPv4 listeners on this port, including wildcard binds."""
    if os.name != "nt" or type(port) is not int or not 0 < port < 65536:
        raise ValueError("COMFY_LISTENER_UNVERIFIED")
    import ctypes
    from ctypes import wintypes

    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]

    api = ctypes.WinDLL("iphlpapi", use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                        wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.GetExtendedTcpTable.restype = wintypes.DWORD
    size = wintypes.DWORD()
    code = api.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
    if code != 122 or size.value < ctypes.sizeof(wintypes.DWORD):
        raise ValueError("COMFY_LISTENER_UNVERIFIED")
    buffer = ctypes.create_string_buffer(size.value)
    code = api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0)
    if code != 0:
        raise ValueError("COMFY_LISTENER_UNVERIFIED")
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = ctypes.sizeof(wintypes.DWORD)
    if offset + count * ctypes.sizeof(Row) > size.value:
        raise ValueError("COMFY_LISTENER_UNVERIFIED")
    found = []
    for index in range(count):
        row = Row.from_buffer_copy(buffer, offset + index * ctypes.sizeof(Row))
        if socket.ntohs(row.local_port & 0xFFFF) == port:
            address = socket.inet_ntoa(struct.pack("=I", row.local_addr))
            found.append((address, row.pid))
    return found


def windows_loopback_listener_pid(port):
    """Accept exactly one IPv4 loopback listener; reject ambiguity."""
    found = windows_port_listeners(port)
    if len(found) != 1 or found[0][0] != "127.0.0.1" or not found[0][1]:
        raise ValueError("COMFY_LISTENER_UNVERIFIED")
    return found[0][1]


class RendererSupervisor:
    def __init__(self, group, base_url, token, adapter_sha256):
        parsed = urlsplit(base_url)
        if (not is_loopback_url(base_url) or parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or not parsed.port):
            raise ValueError("COMFY_SUPERVISOR_URL_INVALID")
        if (not isinstance(token, str) or len(token) < 32 or not isinstance(adapter_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", adapter_sha256)):
            raise ValueError("COMFY_SUPERVISOR_IDENTITY_INVALID")
        self._group, self._url = group, base_url.rstrip("/")
        self._port = parsed.port
        self._token, self._adapter_sha256 = token, adapter_sha256
        self._drain = None
        self._stopped = None
        self._restore_attempted = False
        self._replacement = None

    def require_closed_restart(self):
        if not self._group.starts_drained:
            raise ValueError("COMFY_CLOSED_START_REQUIRED")

    def verify_binding(self, instance_url, process):
        return self._verify_binding(instance_url, process, "accepting")

    def verify_drained_binding(self, instance_url, process):
        self.require_closed_restart()
        return self._verify_binding(instance_url, process, "drained")

    def _verify_binding(self, instance_url, process, expected_state):
        if (instance_url.rstrip("/") != self._url or not isinstance(process, dict)
                or process.get("pid") != self._group.pid or process.get("job_name") != self._group.job_name
                or not self._group.owns(process.get("pid"),
                                                                                 process.get("birth"))):
            raise ValueError("COMFY_HANDOFF_OWNERSHIP_UNVERIFIED")
        return self._control("GET", "/shine-handoff/status", expected_state)

    def _control(self, method, path, expected_state, expected_owner=None):
        listener = windows_loopback_listener_pid(self._port)
        birth = process_identity(listener)
        if not birth or not self._group.owns(listener, birth):
            raise ValueError("COMFY_LISTENER_OWNERSHIP_UNVERIFIED")
        before = {"pid": listener, "birth": birth}
        if expected_owner is not None and before != expected_owner:
            raise ValueError("COMFY_LISTENER_IDENTITY_MISMATCH")
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, self._url + path, headers={"Authorization": "Bearer " + self._token})
        if response.status_code != 200:
            raise ValueError("COMFY_DRAIN_UNCONFIRMED")
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("COMFY_DRAIN_UNCONFIRMED") from exc
        if (not isinstance(body, dict) or body.get("status") != expected_state
                or type(body.get("protocol")) is not int or body["protocol"] != 1
                or body.get("adapter_sha256") != self._adapter_sha256
                or type(body.get("pid")) is not int or body["pid"] <= 0):
            raise ValueError("COMFY_DRAIN_IDENTITY_UNVERIFIED")
        if windows_loopback_listener_pid(self._port) != body["pid"] or body["pid"] != listener:
            raise ValueError("COMFY_LISTENER_IDENTITY_MISMATCH")
        birth = process_identity(body["pid"])
        if not birth or not self._group.owns(body["pid"], birth):
            raise ValueError("COMFY_HANDOFF_OWNERSHIP_UNVERIFIED")
        owner = {"pid": body["pid"], "birth": birth}
        if expected_owner is not None and owner != expected_owner:
            raise ValueError("COMFY_HANDOFF_OWNERSHIP_UNVERIFIED")
        return owner

    def drain_and_archive(self, history_dir, attempt_id):
        """Close admission, retain full history, and return its immutable digest."""
        if self._drain is not None or not re.fullmatch(r"[0-9a-f-]{36}", attempt_id):
            raise ValueError("COMFY_DRAIN_INTENT_INVALID")
        owner = self._control("GET", "/shine-handoff/status", "accepting")
        self._control("POST", "/shine-handoff/drain", "drained", owner)
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = client.get(self._url + "/history")
        if response.status_code != 200:
            raise ValueError("COMFY_HISTORY_UNCONFIRMED")
        raw = response.content
        try:
            if not isinstance(json.loads(raw), dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("COMFY_HISTORY_UNCONFIRMED") from exc
        self._control("GET", "/shine-handoff/status", "drained", owner)
        directory = Path(history_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"history-{attempt_id}-{uuid4().hex}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        digest = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("COMFY_HISTORY_WRITE_UNCONFIRMED")
        self._drain = {"owner": owner, "history_path": path, "history_sha256": digest}
        return dict(self._drain)

    def stop_after_drain(self):
        """Stop only the verified owned group after rechecking the archive and drain."""
        if self._drain is None:
            raise ValueError("COMFY_DRAIN_REQUIRED")
        path = self._drain["history_path"]
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("COMFY_HISTORY_CHANGED") from exc
        if digest != self._drain["history_sha256"]:
            raise ValueError("COMFY_HISTORY_CHANGED")
        self._control("GET", "/shine-handoff/status", "drained", self._drain["owner"])
        result = self._group.terminate(timeout=10)
        if (not isinstance(result, dict) or result.get("pid") != self._group.pid
                or type(result.get("active_processes")) is not int or result["active_processes"] != 0):
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        self._stopped = dict(result)
        return dict(result)

    def stop_closed(self):
        """Stop the owned, closed renderer that no further shot will use.

        The drain archive already happened for the attempt this renderer served;
        this path only proves that the still-owned group is empty before the
        exclusive lease is released.  It never touches a process the supervisor
        does not own.
        """
        if self._stopped is not None:
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        self.require_closed_restart()
        result = self._group.terminate(timeout=10)
        if (not isinstance(result, dict) or result.get("pid") != self._group.pid
                or type(result.get("active_processes")) is not int or result["active_processes"] != 0):
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        self._stopped = dict(result)
        return dict(result)

    def check_memory(self, gpu_uuid, *, min_ram_mb, min_vram_mb, timeout=60):
        if self._stopped is None:
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        if windows_port_listeners(self._port):
            raise ValueError("COMFY_LISTENER_STILL_ACTIVE")
        # A stopped process releases its GPU memory asynchronously: the driver
        # reclaims it a moment after the process group is gone.  The baseline is
        # therefore measured once the release has actually happened, not on the
        # first sample, otherwise a correct release reads as insufficient.
        deadline = time.monotonic() + timeout
        while True:
            measured = probe_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
            if windows_port_listeners(self._port):
                raise ValueError("COMFY_LISTENER_STILL_ACTIVE")
            verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                          gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                          min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
            if measured.get("ready") is True and verified["ready"]:
                return verified
            if time.monotonic() >= deadline:
                raise ValueError("COMFY_HANDOFF_MEMORY_INSUFFICIENT")
            time.sleep(0.5)

    def restore_after_review(self, review_verification, *, min_ram_mb, min_vram_mb, timeout=120, gpu_uuid=None):
        """Restore the pinned owned recipe after a sealed review release.

        ``review_verification`` is ``None`` when the project uses advisory-only
        model review and no review stage was requested. The stopped renderer is
        then the whole physical boundary; memory recovery is still measured from
        ``gpu_uuid`` before the replacement may load the model again.
        """
        from shine.quality.review_release import verify_review_release

        if self._restore_attempted:
            raise ValueError("COMFY_RESTART_ALREADY_ATTEMPTED")
        if self._stopped is None:
            raise ValueError("COMFY_PROCESS_EXIT_UNCONFIRMED")
        self.require_closed_restart()
        if type(timeout) not in {int, float} or not 0 < timeout <= 120:
            raise ValueError("COMFY_RESTART_REQUEST_INVALID")
        if review_verification is None:
            if not isinstance(gpu_uuid, str) or not gpu_uuid:
                raise ValueError("COMFY_RESTART_REQUEST_INVALID")
            verified_review = {"review_stage_id": None, "model_reviewed": False}
            memory_uuid = gpu_uuid
        else:
            if not isinstance(review_verification, dict) or not review_verification.get("gpu_uuid"):
                raise ValueError("COMFY_RESTART_REQUEST_INVALID")
            verified_review = verify_review_release(**{
                **review_verification, "min_ram_mb": min_ram_mb, "min_vram_mb": min_vram_mb,
            })
            memory_uuid = review_verification["gpu_uuid"]
        self.check_memory(memory_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        self._restore_attempted = True  # Lost/failed launch results are never automatically retried.
        self._replacement = self._group.relaunch()
        replacement = RendererSupervisor(self._replacement, self._url, self._token, self._adapter_sha256)
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("COMFY_RESTART_OUTCOME_UNCONFIRMED")
            listeners = windows_port_listeners(self._port)
            if listeners:
                if len(listeners) != 1 or listeners[0][0] != "127.0.0.1":
                    raise ValueError("COMFY_RESTART_LISTENER_UNVERIFIED")
                listener_pid = listeners[0][1]
                listener_birth = process_identity(listener_pid)
                if not listener_birth or not self._replacement.owns(listener_pid, listener_birth):
                    raise ValueError("COMFY_RESTART_LISTENER_UNVERIFIED")
                try:
                    listener = replacement._control("GET", "/shine-handoff/status", "drained")
                except httpx.TransportError:
                    time.sleep(0.1)
                    continue
                birth = process_identity(self._replacement.pid)
                process = {"pid": self._replacement.pid, "birth": birth}
                if self._replacement.job_name:
                    process["job_name"] = self._replacement.job_name
                if not birth or not self._replacement.owns(process["pid"], birth):
                    raise ValueError("COMFY_RESTART_OUTCOME_UNCONFIRMED")
                return replacement, {"process": process, "listener": listener,
                                     "review_stage_id": verified_review["review_stage_id"],
                                     "model_reviewed": verified_review.get("model_reviewed", True)}
            time.sleep(0.1)

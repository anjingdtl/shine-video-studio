from __future__ import annotations

import base64
import ctypes
import os
import platform
import re
from ctypes import wintypes
from pathlib import Path

from shine.llm.base import LLMNotConfigured

_PROFILE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


class SecureStoreUnavailable(LLMNotConfigured):
    code = "LLM_SECURE_STORE_UNAVAILABLE"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_wrap(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "Shine local provider key",
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        raise SecureStoreUnavailable("Windows DPAPI encryption failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unwrap(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(blob_out),
    ):
        raise SecureStoreUnavailable("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SecretStore:
    """Stores provider keys outside SQLite, Git, audit logs, and API responses.

    Windows uses DPAPI (user-scoped). Other platforms fall back to a 0600 file
    and report that storage mode explicitly so operators can audit it.
    """

    def __init__(self, root: Path):
        self.root = root
        self.mode = "dpapi" if platform.system() == "Windows" else "file-0600"

    def _path(self, profile_id: str) -> Path:
        if not _PROFILE_ID.match(profile_id):
            raise SecureStoreUnavailable("Invalid provider profile id")
        return self.root / f"{profile_id}.secret"

    def store(self, profile_id: str, secret: str) -> str:
        if not secret:
            raise SecureStoreUnavailable("Empty provider key")
        path = self._path(profile_id)
        raw = secret.encode("utf-8")
        if self.mode == "dpapi":
            _write_private(path, _dpapi_wrap(raw))
            return f"dpapi:{path.name}"
        _write_private(path, base64.b64encode(raw))
        return f"file:{path.name}"

    def load(self, ref: str | None) -> str | None:
        if not ref:
            return None
        scheme, _, name = ref.partition(":")
        if scheme == "env":
            value = os.getenv(name)
            if not value:
                raise LLMNotConfigured(f"Environment variable {name} is not set")
            return value
        if scheme not in {"dpapi", "file"} or not name:
            raise SecureStoreUnavailable("Unsupported secret reference")
        path = self.root / name
        if not path.is_file():
            raise LLMNotConfigured("The configured provider key is no longer available")
        raw = path.read_bytes()
        if scheme == "dpapi":
            if self.mode != "dpapi":
                raise SecureStoreUnavailable("DPAPI secrets require Windows")
            return _dpapi_unwrap(raw).decode("utf-8")
        return base64.b64decode(raw).decode("utf-8")

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        scheme, _, name = ref.partition(":")
        if scheme in {"dpapi", "file"} and name:
            (self.root / name).unlink(missing_ok=True)

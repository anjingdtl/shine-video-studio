"""Mirrored ComfyUI HTTP client for Manual4 (from shine/comfy/client.py)."""

from __future__ import annotations

import json
import time
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

LOOPBACK_HOSTS = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class M4ComfyError(RuntimeError):
    code = "M4_COMFY_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class M4ComfyUnavailable(M4ComfyError):
    code = "M4_COMFY_UNAVAILABLE"


class M4ComfySubmissionUncertain(M4ComfyError):
    code = "M4_COMFY_SUBMISSION_OUTCOME_UNKNOWN"


class M4ComfyPromptRejected(M4ComfyError):
    code = "M4_COMFY_PROMPT_REJECTED"


class M4ComfyWorkflowError(M4ComfyError):
    code = "M4_COMFY_WORKFLOW_INVALID"


class M4ComfyClient:
    """Loopback-enforced ComfyUI HTTP client owned exclusively by Manual4."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 15.0,
        allow_remote: bool = False,
        transport: httpx.BaseTransport | None = None,
        client_id: str = "shine-manual4",
    ):
        self.base_url = base_url.rstrip("/")
        if not allow_remote and not is_loopback_url(self.base_url):
            raise M4ComfyError("Manual4 ComfyUI URL must remain loopback unless explicitly allowed")
        self.timeout_s = timeout_s
        self.client_id = client_id
        self.transport = transport
        self.trust_env = allow_remote

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.timeout_s,
            transport=self.transport,
            trust_env=self.trust_env,
        )

    def system_stats(self) -> dict[str, Any]:
        try:
            with self._client(timeout=min(self.timeout_s, 3.0)) as client:
                resp = client.get("/system_stats")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI unreachable: {exc}") from exc

    def object_info(self) -> dict[str, Any]:
        try:
            with self._client(timeout=max(self.timeout_s, 20.0)) as client:
                resp = client.get("/object_info")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI object_info unavailable: {exc}") from exc

    def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        upload_path: str = "/upload/image",
        field_name: str = "image",
        subfolder: str = "shine_m4",
        content_type: str = "application/octet-stream",
        style: str = "multipart",
    ) -> dict[str, Any]:
        _ = style
        return self.upload_image(
            filename,
            content,
            upload_path=upload_path,
            field_name=field_name,
            subfolder=subfolder,
            content_type=content_type,
        )

    def upload_image(
        self,
        filename: str,
        content: bytes,
        *,
        upload_path: str = "/upload/image",
        field_name: str = "image",
        subfolder: str = "shine_m4",
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        try:
            with self._client(timeout=max(self.timeout_s, 60.0)) as client:
                resp = client.post(
                    upload_path,
                    data={"subfolder": subfolder, "overwrite": "true"},
                    files={field_name: (filename, content, content_type)},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI upload failed: {exc}") from exc

    def queue_prompt(self, prompt: dict[str, Any], *, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt, "client_id": self.client_id}
        if extra_data:
            body["extra_data"] = extra_data
        try:
            with self._client(timeout=max(self.timeout_s, 30.0)) as client:
                resp = client.post("/prompt", json=body)
                if resp.status_code == 400:
                    raise M4ComfyPromptRejected(
                        "ComfyUI rejected the prompt",
                        details={"body": resp.text[:600]},
                    )
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("prompt_id"):
                    raise M4ComfySubmissionUncertain("ComfyUI /prompt response omitted prompt_id")
                return payload
        except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise M4ComfySubmissionUncertain(
                f"ComfyUI /prompt outcome uncertain: {type(exc).__name__}"
            ) from exc
        except httpx.ConnectError as exc:
            raise M4ComfyUnavailable(f"ComfyUI connect failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise M4ComfySubmissionUncertain(f"ComfyUI 5xx: {exc.response.status_code}") from exc
            raise M4ComfyPromptRejected(f"ComfyUI rejected prompt: {exc.response.status_code}") from exc

    def queue(self) -> dict[str, Any]:
        try:
            with self._client(timeout=min(self.timeout_s, 5.0)) as client:
                resp = client.get("/queue")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI /queue unavailable: {exc}") from exc

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        endpoint = f"/history/{prompt_id}" if prompt_id else "/history"
        try:
            with self._client(timeout=max(self.timeout_s, 15.0)) as client:
                resp = client.get(endpoint)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI /history unavailable: {exc}") from exc

    def view(self, filename: str, *, subfolder: str = "", file_type: str = "output") -> bytes:
        try:
            with self._client(timeout=max(self.timeout_s, 120.0)) as client:
                resp = client.get(
                    "/view",
                    params={"filename": filename, "subfolder": subfolder, "type": file_type},
                )
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI /view failed: {exc}") from exc

    def free_models(self, *, unload_models: bool = True, free_memory: bool = True) -> None:
        try:
            with self._client(timeout=30.0) as client:
                resp = client.post("/free", json={"unload_models": unload_models, "free_memory": free_memory})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise M4ComfyUnavailable(f"ComfyUI /free failed: {exc}") from exc

    def handoff_resume(self, token: str) -> dict[str, Any]:
        with self._client(timeout=10.0) as client:
            resp = client.post("/shine-handoff/resume", headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.json()

    def handoff_drain(self, token: str) -> dict[str, Any]:
        with self._client(timeout=30.0) as client:
            resp = client.post("/shine-handoff/drain", headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.json()

    def poll_until_done(
        self,
        prompt_id: str,
        *,
        timeout_s: float = 1800.0,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            hist = self.history(prompt_id).get(prompt_id)
            if isinstance(hist, dict):
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    raise M4ComfyError("ComfyUI 渲染失败", details={"status": status})
                return hist
            time.sleep(poll_interval)
        raise M4ComfySubmissionUncertain(f"ComfyUI prompt {prompt_id} exceeded poll timeout {timeout_s}s")


def _unused() -> None:
    _ = json.dumps({})

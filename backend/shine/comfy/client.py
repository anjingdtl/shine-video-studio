from __future__ import annotations

import time
from typing import Any

import httpx


class ComfyError(RuntimeError):
    code = "COMFY_ERROR"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ComfyUnavailable(ComfyError):
    code = "COMFY_UNAVAILABLE"
    retryable = True


class ComfyTimeout(ComfyError):
    code = "COMFY_TIMEOUT"
    retryable = True


class ComfySubmissionUncertain(ComfyError):
    """The /prompt request may have reached ComfyUI; never retry blindly."""

    code = "COMFY_SUBMISSION_OUTCOME_UNKNOWN"


class ComfyWorkflowError(ComfyError):
    code = "COMFY_WORKFLOW_INVALID"


class ComfyUIError(ComfyError):
    code = "COMFY_PROVIDER_ERROR"


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        client_id: str = "shine",
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.client_id = client_id
        self._transport = transport

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": self.timeout_s, "transport": self._transport}
        return httpx.Client(**kwargs)

    def _get(self, path: str, *, timeout: float | None = None) -> httpx.Response:
        last: ComfyError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._client() as client:
                    response = client.get(f"{self.base_url}{path}", timeout=timeout or self.timeout_s)
            except httpx.TimeoutException:
                last = ComfyTimeout(f"ComfyUI {path} timed out")
            except httpx.HTTPError as exc:
                last = ComfyUnavailable("ComfyUI is unreachable", details={"error": type(exc).__name__})
            else:
                if response.status_code < 400:
                    return response
                if response.status_code >= 500:
                    last = ComfyUnavailable("ComfyUI returned a server error", details={"status": response.status_code})
                else:
                    raise ComfyUIError(
                        f"ComfyUI rejected GET {path}",
                        details={"status": response.status_code, "body": response.text[:300]},
                    )
            if attempt < self.max_retries:
                time.sleep(min(1.5, 0.2 * (2**attempt)))
        raise last or ComfyUnavailable("ComfyUI request failed")

    def system_stats(self) -> dict[str, Any]:
        return self._get("/system_stats").json()

    def object_info(self) -> dict[str, Any]:
        return self._get("/object_info", timeout=max(self.timeout_s, 30)).json()

    def queue(self) -> dict[str, Any]:
        return self._get("/queue").json()

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        path = "/history" if prompt_id is None else f"/history/{prompt_id}"
        return self._get(path).json()

    def upload_image(
        self,
        filename: str,
        content: bytes,
        *,
        subfolder: str = "shine",
        content_type: str = "image/png",
    ) -> dict[str, Any]:
        last: ComfyError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._client() as client:
                    response = client.post(
                        f"{self.base_url}/upload/image",
                        files={"image": (filename, content, content_type)},
                        data={"overwrite": "true", "subfolder": subfolder},
                    )
            except httpx.TimeoutException:
                last = ComfyTimeout("ComfyUI upload timed out")
            except httpx.HTTPError as exc:
                last = ComfyUnavailable("ComfyUI upload failed", details={"error": type(exc).__name__})
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code >= 500:
                    last = ComfyUnavailable("ComfyUI upload server error")
                else:
                    raise ComfyUIError(
                        "ComfyUI rejected the upload",
                        details={"status": response.status_code, "body": response.text[:300]},
                    )
            if attempt < self.max_retries:
                time.sleep(min(1.5, 0.2 * (2**attempt)))
        raise last or ComfyUnavailable("ComfyUI upload failed")

    def queue_prompt(self, prompt: dict[str, Any], *, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"prompt": prompt, "client_id": self.client_id}
        if extra_data:
            payload["extra_data"] = extra_data
        try:
            with self._client() as client:
                response = client.post(f"{self.base_url}/prompt", json=payload)
        except httpx.TimeoutException as exc:
            raise ComfySubmissionUncertain(
                "The /prompt request timed out after it may have been accepted; reconcile before resubmitting"
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(
                "The /prompt request failed before a response was received; submission state is unknown",
                details={"error": type(exc).__name__},
            ) from exc
        if response.status_code >= 400:
            raise ComfyUIError(
                "ComfyUI rejected the prompt",
                details={"status": response.status_code, "body": response.text[:500]},
            )
        body = response.json()
        if body.get("node_errors"):
            raise ComfyWorkflowError("ComfyUI reported node validation errors", details=body["node_errors"])
        if not body.get("prompt_id"):
            raise ComfyWorkflowError("ComfyUI did not return a prompt_id")
        return body

    def view(self, filename: str, *, subfolder: str = "", file_type: str = "output") -> bytes:
        with self._client() as client:
            response = client.get(
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": file_type},
                timeout=max(self.timeout_s, 60),
            )
        if response.status_code >= 400:
            raise ComfyUIError("ComfyUI output download failed", details={"status": response.status_code})
        return response.content

    def interrupt(self) -> None:
        try:
            with self._client() as client:
                client.post(f"{self.base_url}/interrupt")
        except httpx.HTTPError as exc:
            raise ComfyUnavailable("ComfyUI interrupt failed", details={"error": type(exc).__name__}) from exc

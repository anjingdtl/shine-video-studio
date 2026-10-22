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


class ComfyNodeUnavailable(ComfyError):
    """A node class the workflow needs is not installed on this ComfyUI."""

    code = "COMFY_NODE_UNAVAILABLE"


class ComfyWorkflowVersionMismatch(ComfyError):
    """The installed node definition no longer matches the registered mapping."""

    code = "COMFY_WORKFLOW_VERSION_MISMATCH"


class ComfyUIError(ComfyError):
    code = "COMFY_PROVIDER_ERROR"


class ComfyPromptRejected(ComfyUIError):
    """ComfyUI explicitly refused the prompt; nothing was queued."""

    code = "COMFY_PROMPT_REJECTED"


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

    def free_models(self):
        queue = self.queue()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise ComfyUIError("INSTANCE_BUSY_CANNOT_UNLOAD")
        with self._client() as client:
            response = client.post(f"{self.base_url}/free", json={"unload_models": True, "free_memory": True})
            if response.status_code != 200:
                raise ComfyUIError("MODEL_RELEASE_UNCONFIRMED")

    def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        upload_path: str = "/upload/image",
        field_name: str = "image",
        subfolder: str = "shine",
        content_type: str = "application/octet-stream",
        style: str = "multipart",
    ) -> dict[str, Any]:
        """Upload one reference file using an explicit transport descriptor.

        ``style`` selects the wire format: ``multipart`` posts the file as a
        form field, ``binary`` posts the raw bytes with an explicit content
        type. Custom H3 nodes do not all accept the same upload endpoint, so
        the caller must name it.
        """
        if not upload_path.startswith("/"):
            raise ComfyUIError("ComfyUI upload path must be an absolute API path", details={"path": upload_path})
        last: ComfyError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._client() as client:
                    if style == "binary":
                        response = client.post(
                            f"{self.base_url}{upload_path}",
                            content=content,
                            headers={
                                "Content-Type": content_type,
                                "X-Shine-Filename": filename,
                                "X-Shine-Subfolder": subfolder,
                            },
                        )
                    else:
                        response = client.post(
                            f"{self.base_url}{upload_path}",
                            files={field_name: (filename, content, content_type)},
                            data={"overwrite": "true", "subfolder": subfolder},
                        )
            except httpx.TimeoutException:
                last = ComfyTimeout("ComfyUI upload timed out")
            except httpx.HTTPError as exc:
                last = ComfyUnavailable("ComfyUI upload failed", details={"error": type(exc).__name__})
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ComfyUIError(
                            "ComfyUI upload response was not valid JSON",
                            details={"status": response.status_code},
                        ) from exc
                if response.status_code >= 500:
                    last = ComfyUnavailable("ComfyUI upload server error", details={"status": response.status_code})
                else:
                    raise ComfyUIError(
                        "ComfyUI rejected the upload",
                        details={"status": response.status_code, "body": response.text[:300]},
                    )
            if attempt < self.max_retries:
                time.sleep(min(1.5, 0.2 * (2**attempt)))
        raise last or ComfyUnavailable("ComfyUI upload failed")

    def upload_image(
        self,
        filename: str,
        content: bytes,
        *,
        subfolder: str = "shine",
        content_type: str = "image/png",
    ) -> dict[str, Any]:
        return self.upload_file(
            filename,
            content,
            upload_path="/upload/image",
            field_name="image",
            subfolder=subfolder,
            content_type=content_type,
            style="multipart",
        )

    def queue_prompt(self, prompt: dict[str, Any], *, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"prompt": prompt, "client_id": self.client_id}
        if extra_data:
            payload["extra_data"] = extra_data
        try:
            with self._client() as client:
                response = client.post(f"{self.base_url}/prompt", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # The TCP connection never completed, so the request was not delivered.
            raise ComfyUnavailable(
                "ComfyUI could not be reached; the prompt was never sent",
                details={"error": type(exc).__name__},
            ) from exc
        except httpx.TimeoutException as exc:
            raise ComfySubmissionUncertain(
                "The /prompt request timed out after it may have been accepted; reconcile before resubmitting"
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfySubmissionUncertain(
                "The /prompt connection failed after the request may have been sent; reconcile before resubmitting",
                details={"error": type(exc).__name__},
            ) from exc
        if response.status_code >= 500:
            # A server error may have happened after the prompt was queued.
            raise ComfySubmissionUncertain(
                "ComfyUI returned a server error while queueing the prompt; reconcile before resubmitting",
                details={"status": response.status_code},
            )
        if response.status_code >= 400:
            raise ComfyPromptRejected(
                "ComfyUI rejected the prompt before queueing it",
                details={"status": response.status_code, "body": response.text[:500]},
            )
        body = response.json()
        if body.get("node_errors"):
            raise ComfyWorkflowError("ComfyUI reported node validation errors", details=body["node_errors"])
        if not body.get("prompt_id"):
            raise ComfySubmissionUncertain(
                "ComfyUI did not return a prompt_id; the submission state is unknown",
                details={"body": str(body)[:300]},
            )
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

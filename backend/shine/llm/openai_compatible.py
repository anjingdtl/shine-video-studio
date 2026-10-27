from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import jsonschema
from jsonschema import Draft202012Validator

from shine.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMCapabilityError,
    LLMConnectionError,
    LLMError,
    LLMFormatError,
    LLMModelNotFound,
    LLMRateLimit,
    LLMResponse,
    LLMServerError,
    LLMTimeout,
    ProviderConfig,
    parse_json_object,
)


def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/models"):
        return base
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completions with retries, timeouts, and JSON validation."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _request(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> httpx.Response:
        last_error: LLMError | None = None
        attempts = max(0, self.config.max_retries) + 1
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.config.timeout_s, follow_redirects=True) as client:
                    response = client.request(method, url, headers=self._headers(), json=payload)
            except httpx.TimeoutException:
                last_error = LLMTimeout("Provider request timed out", details={"attempt": attempt + 1})
            except httpx.HTTPError as exc:
                last_error = LLMConnectionError("Provider connection failed", details={"error": type(exc).__name__})
            else:
                if response.status_code < 400:
                    return response
                last_error = self._classify(response)
                if not last_error.retryable:
                    raise last_error
            if attempt + 1 < attempts:
                time.sleep(min(2.0, 0.25 * (2**attempt)))
        if last_error is None:
            raise LLMConnectionError("Provider request failed")
        raise last_error

    @staticmethod
    def _classify(response: httpx.Response) -> LLMError:
        text = response.text[:500]
        if response.status_code in {401, 403}:
            return LLMAuthError("Provider rejected the API key", details={"status": response.status_code})
        if response.status_code == 404:
            return LLMModelNotFound("Provider endpoint or model was not found", details={"status": 404})
        if response.status_code == 429:
            return LLMRateLimit("Provider rate limit reached", details={"status": 429})
        if response.status_code >= 500:
            return LLMServerError("Provider server error", details={"status": response.status_code})
        return LLMError(f"Provider rejected the request: {text}", details={"status": response.status_code})

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any] | None = None,
        json_mode: bool = True,
    ) -> LLMResponse:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.config.temperature,
            **self.config.generation_options,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = _chat_url(self.config.base_url)
        try:
            response = self._request("POST", url, payload=payload)
        except LLMError as exc:
            if json_mode and exc.details.get("status") in {400, 422}:
                payload.pop("response_format", None)
                response = self._request("POST", url, payload=payload)
            else:
                raise
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise LLMFormatError("Provider returned a non-JSON response") from exc
        choices = body.get("choices") or []
        if not choices:
            raise LLMFormatError("Provider returned no completions")
        if choices[0].get("finish_reason") in {"length", "max_tokens", "content_filter"}:
            raise LLMFormatError("Provider completion is truncated or filtered")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise LLMFormatError("Provider returned an empty completion")
        parsed = parse_json_object(content) if json_mode else None
        if parsed is not None and schema is not None:
            validator = Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(parsed), key=lambda error: list(error.path))
            if errors:
                first = errors[0]
                raise LLMFormatError(
                    "Model JSON does not match the required schema",
                    details={"path": "/".join(str(part) for part in first.path), "message": first.message},
                )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            text=content,
            parsed=parsed,
            model=body.get("model") or self.config.model,
            latency_ms=latency_ms,
            usage=body.get("usage") or {},
            raw_meta={"id": body.get("id"), "is_mock": bool(body.get("is_mock", False))},
        )

    def detect_capabilities(self) -> dict[str, Any]:
        response = self._request("GET", _models_url(self.config.base_url))
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}
        models = []
        for item in body.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        extra = body.get("capabilities") or {}
        protocol = extra.get("review_response_protocol") if isinstance(extra, dict) else None
        review = {}
        if protocol is not None:
            decoder = extra.get("review_decoder_sha256")
            if (protocol != "local-review-json-schema-v1" or not isinstance(decoder, str)
                    or not re.fullmatch("[a-f0-9]{64}", decoder)):
                raise LLMCapabilityError("Review response protocol declaration is invalid")
            review = {"review_response_protocol": protocol, "review_decoder_sha256": decoder}
        return {
            "available": True,
            "models": models[:200],
            "model_listed": (not models) or self.config.model in models,
            "json_mode": "assumed",
            "vision": None,
            "tools": None,
            **review,
        }


def validate_with_jsonschema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise LLMFormatError(
            "Model JSON does not match the required schema",
            details={"path": "/".join(str(part) for part in exc.absolute_path)},
        ) from exc

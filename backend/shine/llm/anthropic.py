from __future__ import annotations

import json
import time
from typing import Any

import httpx

from shine.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMConnectionError,
    LLMError,
    LLMFormatError,
    LLMRateLimit,
    LLMResponse,
    LLMServerError,
    LLMTimeout,
    ProviderConfig,
    parse_json_object,
)


class AnthropicMessagesProvider:
    """Native Anthropic Messages API adapter (no vendor SDK dependency)."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": self.config.api_key or "",
        }

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: LLMError | None = None
        attempts = max(0, self.config.max_retries) + 1
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.config.timeout_s) as client:
                    response = client.post(self._url(), headers=self._headers(), json=payload)
            except httpx.TimeoutException:
                last_error = LLMTimeout("Anthropic request timed out")
            except httpx.HTTPError as exc:
                last_error = LLMConnectionError("Anthropic connection failed", details={"error": type(exc).__name__})
            else:
                if response.status_code < 400:
                    return response
                if response.status_code in {401, 403}:
                    raise LLMAuthError("Anthropic rejected the API key")
                if response.status_code == 429:
                    last_error = LLMRateLimit("Anthropic rate limit reached")
                elif response.status_code >= 500:
                    last_error = LLMServerError("Anthropic server error")
                else:
                    raise LLMError(
                        "Anthropic rejected the request",
                        details={"status": response.status_code, "body": response.text[:300]},
                    )
            if attempt + 1 < attempts:
                time.sleep(min(2.0, 0.25 * (2**attempt)))
        raise last_error or LLMConnectionError("Anthropic request failed")

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any] | None = None,
        json_mode: bool = True,
    ) -> LLMResponse:
        started = time.perf_counter()
        system_parts = [message.content for message in messages if message.role == "system"]
        conversation = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        if json_mode:
            instruction = "Return only a single JSON object; do not wrap it in prose or markdown."
            if schema:
                instruction += "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
            system_parts.append(instruction)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 4096,
            "temperature": self.config.temperature,
            "messages": conversation,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        response = self._post(payload)
        body = response.json()
        blocks = body.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        if not text.strip():
            raise LLMFormatError("Anthropic returned an empty completion")
        parsed = parse_json_object(text) if json_mode else None
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=body.get("model") or self.config.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=body.get("usage") or {},
            raw_meta={"id": body.get("id")},
        )

    def detect_capabilities(self) -> dict[str, Any]:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        with httpx.Client(timeout=self.config.timeout_s) as client:
            response = client.get(url, headers=self._headers())
        if response.status_code >= 400:
            raise LLMError("Anthropic capability probe failed", details={"status": response.status_code})
        body = response.json()
        models = [str(item["id"]) for item in body.get("data") or [] if isinstance(item, dict) and item.get("id")]
        return {
            "available": True,
            "models": models,
            "model_listed": (not models) or self.config.model in models,
            "json_mode": "prompt-enforced",
            "vision": True,
            "tools": True,
        }

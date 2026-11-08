from __future__ import annotations

import json
import time
from typing import Any

import httpx

from shine.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMCatalogError,
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
from shine.llm.output_budget import ANTHROPIC_DEFAULT_MAX_TOKENS
from shine.llm.transport import build_client


def _max_tokens_for(config) -> int:
    """The request max_tokens: a configured option, bounded by the declared max.

    The adapter honors a ``max_tokens`` generation option (validated through
    the profile's connection/generation tests); without a configured value it
    keeps the historical 4,096 safety value.  A provider-declared maximum
    still bounds a configured value that claims more.
    """
    option = config.generation_options.get("max_tokens")
    if isinstance(option, bool) or not isinstance(option, int) or option <= 0:
        option = None
    declared = config.max_output_tokens
    if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
        declared = None
    if option:
        return min(option, declared) if declared else option
    return min(ANTHROPIC_DEFAULT_MAX_TOKENS, declared) if declared else ANTHROPIC_DEFAULT_MAX_TOKENS


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
                with build_client(self._url(), self.config.timeout_s) as client:
                    response = client.post(self._url(), headers=self._headers(), json=payload)
            except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # The connection was never established, so the provider never
                # saw the request and a replay cannot produce a second answer.
                last_error = LLMConnectionError(
                    "Anthropic connection timed out", details={
                        "error": type(exc).__name__, "external_outcome": "NOT_SENT",
                    })
            except httpx.ConnectError as exc:
                last_error = LLMConnectionError("Anthropic connection failed", details={
                    "error": type(exc).__name__, "external_outcome": "NOT_SENT",
                })
            except httpx.TimeoutException as exc:
                # The request was sent and the answer never arrived: the
                # external outcome is unknown, so the request is not replayed.
                raise LLMTimeout(
                    "Anthropic request timed out",
                    details={"error": type(exc).__name__, "replayed": False,
                             "external_outcome": "UNKNOWN"},
                ) from None
            except httpx.HTTPError as exc:
                # The connection broke after the request was sent, so the
                # outcome is unknown for the same reason as above.
                raise LLMConnectionError(
                    "Anthropic connection failed",
                    details={"error": type(exc).__name__, "replayed": False,
                             "external_outcome": "UNKNOWN"},
                ) from None
            else:
                if response.status_code < 400:
                    return response
                if response.status_code in {401, 403}:
                    raise LLMAuthError("Anthropic rejected the API key")
                if response.status_code == 429:
                    last_error = LLMRateLimit("Anthropic rate limit reached")
                elif response.status_code >= 500:
                    raise LLMServerError("Anthropic server error", details={
                        "status": response.status_code, "external_outcome": "UNKNOWN",
                    })
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
            # The adapter honors a configured max_tokens option, bounded by a
            # provider-declared maximum; without a configured value it keeps
            # the historical 4,096 safety value.
            "max_tokens": _max_tokens_for(config=self.config),
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
            # The provider answered; the body was unusable.  The status is the
            # durable evidence that a response really arrived.
            raise LLMFormatError("Anthropic returned an empty completion",
                                 details={"status": response.status_code})
        parsed = parse_json_object(text) if json_mode else None
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=body.get("model") or self.config.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=body.get("usage") or {},
            raw_meta={"id": body.get("id"), "is_mock": bool(body.get("is_mock", False)),
                      "http_status": response.status_code},
        )

    def detect_capabilities(self) -> dict[str, Any]:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        try:
            with build_client(url, self.config.timeout_s) as client:
                response = client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise LLMTimeout("Anthropic capability probe timed out", details={
                "error": type(exc).__name__, "external_outcome": "UNKNOWN",
            }) from None
        except httpx.HTTPError as exc:
            raise LLMConnectionError("Anthropic capability probe failed", details={
                "error": type(exc).__name__, "external_outcome": "UNKNOWN",
            }) from None
        if response.status_code in {401, 403}:
            raise LLMAuthError("Anthropic rejected the API key", details={"status": response.status_code})
        if response.status_code >= 400:
            raise LLMCatalogError("Anthropic capability probe failed", details={
                "status": response.status_code, "reachable": True, "auth_ok": True, "catalog_ok": False,
            })
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMCatalogError("Anthropic returned a non-JSON model catalog", details={
                "reachable": True, "auth_ok": True, "catalog_ok": False,
            }) from exc
        if not isinstance(body, dict):
            raise LLMCatalogError("Anthropic model catalog is not a JSON object", details={
                "reachable": True, "auth_ok": True, "catalog_ok": False,
            })
        data = body.get("data") if "data" in body else body.get("models")
        if not isinstance(data, list):
            raise LLMCatalogError("Anthropic model catalog has no model list", details={
                "reachable": True, "auth_ok": True, "catalog_ok": False,
            })
        models = [str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")]
        return {
            "available": True,
            "reachable": True,
            "auth_ok": True,
            "catalog_ok": True,
            "models": models,
            # Only an exact match confirms the configured model.
            "model_listed": bool(models) and self.config.model in models,
            "json_mode": "prompt-enforced",
            "vision": True,
            "tools": True,
        }

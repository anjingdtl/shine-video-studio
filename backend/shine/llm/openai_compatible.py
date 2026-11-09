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
    LLMCatalogError,
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
from shine.llm.context_window import model_context_from_capabilities, provider_model_matches
from shine.llm.transport import build_client


def _api_root(base_url: str) -> str:
    """The API root of a base URL.

    The chat endpoint and the model catalog must be derived from the same
    root, otherwise a base URL that already points at one of them produces a
    catalog URL that cannot exist.
    """
    base = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _chat_url(base_url: str) -> str:
    root = _api_root(base_url)
    if root.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/v1/chat/completions"


def _models_url(base_url: str) -> str:
    root = _api_root(base_url)
    if root.endswith("/v1"):
        return f"{root}/models"
    return f"{root}/v1/models"


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
                with build_client(url, self.config.timeout_s) as client:
                    response = client.request(method, url, headers=self._headers(), json=payload)
            except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # The connection was never established, so the provider never
                # saw the request.  No answer can exist, and a replay cannot
                # produce a second one.
                last_error = LLMConnectionError(
                    "Provider connection timed out", details={
                        "error": type(exc).__name__, "external_outcome": "NOT_SENT",
                    })
            except httpx.ConnectError as exc:
                last_error = LLMConnectionError(
                    "Provider connection failed", details={
                        "error": type(exc).__name__, "external_outcome": "NOT_SENT",
                    })
            except httpx.TimeoutException as exc:
                # The request was sent and the answer never arrived.  The
                # external outcome is unknown, so the request is NOT replayed:
                # one request must never buy two answers, and an unknown result
                # must never be dressed up as a fresh success.
                raise LLMTimeout(
                    "Provider request timed out",
                    details={"error": type(exc).__name__, "replayed": False,
                             "external_outcome": "UNKNOWN"},
                ) from None
            except httpx.HTTPError as exc:
                # The connection broke after the request was sent, so the
                # outcome is unknown for the same reason as above.
                raise LLMConnectionError(
                    "Provider connection failed",
                    details={"error": type(exc).__name__, "replayed": False,
                             "external_outcome": "UNKNOWN"},
                ) from None
            else:
                if response.status_code < 400:
                    return response
                last_error = self._classify(response)
                if (not last_error.retryable or isinstance(last_error, LLMServerError)
                        or (method == "POST" and isinstance(last_error, LLMRateLimit))):
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
            return LLMServerError("Provider server error", details={
                "status": response.status_code, "external_outcome": "UNKNOWN",
            })
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
            # The provider answered; the body was unusable.  The status is the
            # durable evidence that a response really arrived.
            raise LLMFormatError("Provider returned a non-JSON response",
                                 details={"status": response.status_code}) from exc
        if not isinstance(body, dict):
            raise LLMFormatError("Provider returned a malformed completion",
                                 details={"status": response.status_code})
        base_resp = body.get("base_resp")
        provider_code = base_resp.get("status_code") if isinstance(base_resp, dict) else None
        response_details = {"status": response.status_code}
        if isinstance(provider_code, int):
            response_details["provider_code"] = provider_code
        if isinstance(provider_code, int) and provider_code != 0:
            raise LLMError("Provider returned an error status", details=response_details)
        choices = body.get("choices") or []
        usage = body.get("usage") or {}
        usage_details = usage.get("completion_tokens_details")
        usage_details = usage_details if isinstance(usage_details, dict) else {}
        reasoning_tokens = usage_details.get("reasoning_tokens")
        # A reasoning model bills its thinking as completion. When the ceiling
        # is spent on reasoning, the visible answer is empty and only
        # finish_reason=length tells the two cases apart. Carry that evidence
        # with the error so the workbench can name a starved output budget
        # instead of reporting a generic format problem.
        format_details = {
            **response_details,
            "finish_reason": choices[0].get("finish_reason") if choices else None,
            "completion_tokens": usage.get("completion_tokens"),
            **({"reasoning_tokens": reasoning_tokens}
               if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool) else {}),
        }
        if not choices:
            raise LLMFormatError("Provider returned no completions", details=format_details)
        if choices[0].get("finish_reason") in {"length", "max_tokens", "content_filter"}:
            raise LLMFormatError("Provider completion is truncated or filtered", details=format_details)
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise LLMFormatError("Provider returned an empty completion", details=format_details)
        try:
            parsed = parse_json_object(content) if json_mode else None
        except LLMFormatError as exc:
            exc.details.update(response_details)
            raise
        if parsed is not None and schema is not None:
            validator = Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(parsed), key=lambda error: list(error.path))
            if errors:
                first = errors[0]
                raise LLMFormatError(
                    "Model JSON does not match the required schema",
                    details={**response_details, "path": "/".join(str(part) for part in first.path),
                             "validator": first.validator},
                )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            text=content,
            parsed=parsed,
            model=body.get("model") or self.config.model,
            latency_ms=latency_ms,
            usage=body.get("usage") or {},
            raw_meta={"id": body.get("id"), "is_mock": bool(body.get("is_mock", False)),
                      "http_status": response.status_code, "provider_code": provider_code},
        )

    def detect_capabilities(self) -> dict[str, Any]:
        """Read the model catalog and report what it can actually prove.

        Every failure mode of a catalog endpoint - a login page, an empty
        directory, a JSON array, a missing key - is reported distinctly. A
        missing catalog endpoint is not proof that the chat endpoint fails;
        the user must confirm the Model ID with the structured generation
        probe instead. Listing requires the configured identifier or a known
        spelling of that same model from its official provider.
        """
        url = _models_url(self.config.base_url)
        try:
            response = self._request("GET", url)
        except LLMModelNotFound as exc:
            if exc.details.get("status") == 404:
                return {
                    "available": True,
                    "reachable": True,
                    "auth_ok": None,
                    "catalog_ok": False,
                    "catalog_supported": False,
                    "catalog_error_code": "LLM_CATALOG_UNAVAILABLE",
                    "models": [],
                    "model_listed": None,
                    "json_mode": "unknown",
                    "vision": None,
                    "tools": None,
                }
            raise LLMCatalogError(
                "Provider does not expose a model catalog",
                details={**exc.details, "reachable": True, "auth_ok": True, "catalog_ok": False},
            ) from exc
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMCatalogError(
                "Provider returned a non-JSON model catalog",
                details={"reachable": True, "auth_ok": True, "catalog_ok": False,
                         "http_status": response.status_code},
            ) from exc
        if not isinstance(body, dict):
            raise LLMCatalogError(
                "Provider model catalog is not a JSON object",
                details={"reachable": True, "auth_ok": True, "catalog_ok": False,
                         "http_status": response.status_code},
            )
        data = body.get("data") if "data" in body else body.get("models")
        if data is None:
            raise LLMCatalogError(
                "Provider model catalog has no model list",
                details={"reachable": True, "auth_ok": True, "catalog_ok": False,
                         "http_status": response.status_code},
            )
        if not isinstance(data, list):
            raise LLMCatalogError(
                "Provider model catalog is not a list of models",
                details={"reachable": True, "auth_ok": True, "catalog_ok": False,
                         "http_status": response.status_code},
            )
        models = []
        model_metadata = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                model_id = str(item["id"])
                models.append(model_id)
                declared = {"id": model_id}
                context_window = item.get("context_window", item.get("context_window_tokens"))
                max_output = item.get("max_output_tokens")
                if isinstance(context_window, int) and not isinstance(context_window, bool) and context_window > 0:
                    declared["context_window_tokens"] = context_window
                if isinstance(max_output, int) and not isinstance(max_output, bool) and max_output > 0:
                    declared["max_output_tokens"] = max_output
                if len(declared) > 1:
                    model_metadata.append(declared)
        extra = body.get("capabilities") or {}
        protocol = extra.get("review_response_protocol") if isinstance(extra, dict) else None
        review = {}
        if protocol is not None:
            decoder = extra.get("review_decoder_sha256")
            if (protocol != "local-review-json-schema-v1" or not isinstance(decoder, str)
                    or not re.fullmatch("[a-f0-9]{64}", decoder)):
                raise LLMCapabilityError("Review response protocol declaration is invalid")
            review = {"review_response_protocol": protocol, "review_decoder_sha256": decoder}
        result = {
            "available": True,
            "reachable": True,
            "auth_ok": True,
            "catalog_ok": True,
            "catalog_supported": True,
            "models": models[:200],
            "model_listed": any(
                provider_model_matches(self.config.base_url, self.config.model, model) for model in models
            ),
            "json_mode": "assumed",
            "vision": None,
            "tools": None,
            **review,
        }
        if model_metadata:
            result["model_metadata"] = model_metadata[:200]
        model_context = model_context_from_capabilities(
            {"model_metadata": model_metadata}, self.config.base_url, self.config.model,
        )
        if model_context["context_window_tokens"] is not None or model_context["max_output_tokens"] is not None:
            result["model_context"] = model_context
        return result


def validate_with_jsonschema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise LLMFormatError(
            "Model JSON does not match the required schema",
            details={"path": "/".join(str(part) for part in exc.absolute_path)},
        ) from exc

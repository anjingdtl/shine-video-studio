from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Base class for classified provider failures."""

    code = "LLM_ERROR"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class LLMNotConfigured(LLMError):
    code = "LLM_NOT_CONFIGURED"


class LLMAuthError(LLMError):
    code = "LLM_AUTH"


class LLMModelNotFound(LLMError):
    code = "LLM_MODEL_NOT_FOUND"


class LLMRateLimit(LLMError):
    code = "LLM_RATE_LIMIT"
    retryable = True


class LLMTimeout(LLMError):
    code = "LLM_TIMEOUT"
    retryable = True


class LLMConnectionError(LLMError):
    code = "LLM_CONNECTION"
    retryable = True


class LLMServerError(LLMError):
    code = "LLM_SERVER"
    retryable = True


class LLMFormatError(LLMError):
    code = "LLM_FORMAT"


class LLMCapabilityError(LLMError):
    code = "LLM_CAPABILITY"


class LLMCatalogError(LLMError):
    """The provider answered, but its model catalog is unusable as evidence.

    A login page, an empty directory, a JSON array, or any other shape that
    cannot name the configured model must never be reported as "the model was
    confirmed".  The caller can tell the difference between "the service is
    unreachable" and "the service answered with something we cannot trust".
    """

    code = "LLM_CATALOG_INVALID"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ProviderConfig:
    profile_id: str
    name: str
    kind: str
    base_url: str
    model: str
    api_key: str | None
    timeout_s: float = 60.0
    max_retries: int = 2
    temperature: float = 0.2
    generation_options: dict[str, Any] = field(default_factory=dict)
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    context_window_source: str = "provider_did_not_declare_limits"
    streaming: bool = False
    """Whether this request may use the streaming transport.

    Off unless the operator switches ``SHINE_LLM_STREAMING`` on: a provider that
    was never probed must not receive a streaming shape just because the adapter
    supports SSE.
    """

    rate_limit_retry_attempts: int = 0
    """How often a throttled call may wait and ask again.

    A 429 is a refusal: the provider answered and produced no answer, so
    asking again after a real pause cannot buy a second copy of one answer.
    The budget is zero by default, so a plain probe still never posts again
    after a refusal; the agent lane raises it because a production step must
    survive a throttled provider instead of losing a whole scene.
    """


def text_provider_snapshot(profile) -> dict:
    options = getattr(profile, "generation_options", None) or {}
    return {
        "profile_id": profile.id, "kind": profile.kind, "model": profile.model,
        "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest(),
        **({"generation_options_sha256": hashlib.sha256(
            json.dumps(options, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()} if options else {}),
    }


@dataclass
class LLMResponse:
    text: str
    parsed: dict[str, Any] | None
    model: str | None
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    raw_meta: dict[str, Any] = field(default_factory=dict)


def parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise LLMFormatError("The model did not return a JSON object") from None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMFormatError("The model returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise LLMFormatError("Structured output must be a JSON object")
    return value


@runtime_checkable
class LLMProvider(Protocol):
    config: ProviderConfig

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any] | None = None,
        json_mode: bool = True,
    ) -> LLMResponse: ...

    def detect_capabilities(self) -> dict[str, Any]: ...

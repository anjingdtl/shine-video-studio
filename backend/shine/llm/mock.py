from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from shine.llm.base import ChatMessage, LLMFormatError, LLMResponse, ProviderConfig

Responder = Callable[[list[ChatMessage], dict[str, Any] | None], dict[str, Any] | str]


class MockLLMProvider:
    """Deterministic in-process model used for tests and offline UI previews."""

    def __init__(self, config: ProviderConfig, responder: Responder | None = None):
        self.config = config
        self._responder = responder
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any] | None = None,
        json_mode: bool = True,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if self._responder is None:
            value: dict[str, Any] | str = {"status": "mock", "summary": "Mock model response"}
        else:
            value = self._responder(messages, schema)
        if isinstance(value, str):
            text = value
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise LLMFormatError("Mock model returned malformed JSON") from exc
        else:
            parsed = value
            text = json.dumps(value, ensure_ascii=False)
        if not isinstance(parsed, dict):
            raise LLMFormatError("Mock model must return a JSON object")
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=self.config.model or "mock",
            latency_ms=1,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            raw_meta={"mock": True},
        )

    def detect_capabilities(self) -> dict[str, Any]:
        return {
            "available": True,
            "models": [self.config.model or "mock"],
            "model_listed": True,
            "json_mode": True,
            "vision": False,
            "tools": False,
            "mock": True,
        }

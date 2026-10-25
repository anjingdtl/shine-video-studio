from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.llm.anthropic import AnthropicMessagesProvider
from shine.llm.base import (
    ChatMessage,
    LLMError,
    LLMNotConfigured,
    LLMResponse,
    ProviderConfig,
)
from shine.llm.mock import MockLLMProvider, Responder
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute, ProviderProfile


def build_provider(
    config: ProviderConfig,
    *,
    mock_responder: Responder | None = None,
) -> Any:
    if config.kind == "openai_compatible":
        return OpenAICompatibleProvider(config)
    if config.kind == "ollama":
        return OpenAICompatibleProvider(config)
    if config.kind == "anthropic":
        return AnthropicMessagesProvider(config)
    if config.kind == "mock":
        return MockLLMProvider(config, responder=mock_responder)
    raise LLMNotConfigured(f"Unsupported provider kind: {config.kind}")


class LLMGateway:
    def __init__(
        self,
        secrets: SecretStore,
        *,
        mock_responder: Responder | None = None,
        provider_factory: Callable[[ProviderConfig, Responder | None], Any] | None = None,
    ):
        self.secrets = secrets
        self.mock_responder = mock_responder
        self.provider_factory = provider_factory or (
            lambda config, responder: build_provider(config, mock_responder=responder)
        )

    def config_for(self, profile: ProviderProfile, *, api_key_override: str | None = None) -> ProviderConfig:
        if not profile.enabled:
            raise LLMNotConfigured("Provider profile is disabled")
        key = api_key_override
        if key is None and profile.api_key_ref:
            key = self.secrets.load(profile.api_key_ref)
        return ProviderConfig(
            profile_id=profile.id,
            name=profile.name,
            kind=profile.kind,
            base_url=profile.base_url,
            model=profile.model,
            api_key=key,
            timeout_s=profile.timeout_s,
            max_retries=profile.max_retries,
            temperature=profile.temperature,
        )

    def provider_for(self, profile: ProviderProfile, *, api_key_override: str | None = None) -> Any:
        config = self.config_for(profile, api_key_override=api_key_override)
        return self.provider_factory(config, self.mock_responder)

    def profile_for_role(self, db: Session, role: str) -> ProviderProfile:
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
        if not route:
            raise LLMNotConfigured(f"No provider profile is routed to the {role} role")
        profile = db.get(ProviderProfile, route.provider_profile_id)
        if not profile:
            raise LLMNotConfigured(f"The provider profile routed to {role} no longer exists")
        return profile

    def complete_for_role(
        self,
        db: Session,
        role: str,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        profile = self.profile_for_role(db, role)
        provider = self.provider_for(profile)
        if schema is not None:
            # The provider validates the answer against this schema, so the model
            # has to be told the contract it must satisfy. The schema is used
            # for validation only otherwise, and an instruction such as "return
            # only the supplied JSON contract" then names a contract that was
            # never supplied. A mock provider answers from a canned payload and
            # hides the problem; a real provider answers from the context and
            # fails the schema check instead.
            user = (
                f"{user}\n\n"
                "Return one JSON object and nothing else. It must validate against "
                "this JSON schema:\n"
                f"{json.dumps(schema, ensure_ascii=False, indent=1)}"
            )
        return provider.complete(
            [ChatMessage("system", system), ChatMessage("user", user)],
            schema=schema,
            json_mode=True,
        )

    def test_profile(self, profile: ProviderProfile, *, api_key: str | None = None) -> dict[str, Any]:
        provider = self.provider_for(profile, api_key_override=api_key)
        capabilities = provider.detect_capabilities()
        if profile.kind in {"openai_compatible", "ollama"} and capabilities.get("model_listed") is False:
            capabilities["warning"] = "The configured model was not listed by the provider"
        return capabilities


class GatewayStructuredModel:
    """Agent-facing port that only exposes validated structured completion."""

    def __init__(self, gateway: LLMGateway, db: Session, role: str):
        self._gateway = gateway
        self._db = db
        self._role = role

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = self._gateway.complete_for_role(
            self._db,
            self._role,
            system=system,
            user=user,
            schema=schema,
        )
        if response.parsed is None:
            raise LLMError("Provider did not return structured JSON")
        return response.parsed

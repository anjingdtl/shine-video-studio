from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from functools import partial
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
from shine.llm.context_window import model_context_for_profile
from shine.llm.mock import MockLLMProvider, Responder
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute, ProviderProfile


def agent_output_config(config: ProviderConfig) -> ProviderConfig:
    """Limit each agent response without expanding a configured smaller budget."""
    limits = [100_000]
    configured = config.generation_options.get("max_completion_tokens")
    if configured:
        limits.append(configured)
    if config.max_output_tokens:
        limits.append(config.max_output_tokens)
    output_limit = min(limits)
    if config.kind == "openai_compatible":
        return replace(config, generation_options={
            **config.generation_options, "max_completion_tokens": output_limit,
        })
    if config.kind == "ollama":
        return replace(config, generation_options={
            **config.generation_options, "max_tokens": output_limit,
        })
    return config  # Anthropic adapter already uses 4096; mock has no external output.


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
        from shine.schemas import TextGenerationOptions
        options = TextGenerationOptions.model_validate(profile.generation_options or {}).model_dump(exclude_none=True)
        if options and profile.kind != "openai_compatible":
            raise LLMNotConfigured("Generation options require an OpenAI-compatible provider")
        if not profile.enabled:
            raise LLMNotConfigured("Provider profile is disabled")
        key = api_key_override
        if key is None and profile.api_key_ref:
            key = self.secrets.load(profile.api_key_ref)
        model_context = model_context_for_profile(profile)
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
            generation_options=options,
            context_window_tokens=model_context["context_window_tokens"],
            max_output_tokens=model_context["max_output_tokens"],
            context_window_source=model_context["source"],
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
        on_progress: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        profile = self.profile_for_role(db, role)
        config = agent_output_config(self.config_for(profile))
        provider = self.provider_factory(config, self.mock_responder)
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
        from shine.comfyops.scheduler import CURRENT_RESOURCE, resource_slot
        from shine.db import make_session_factory
        from shine.llm.lifecycle import release_text_model

        # All roles share the same stage admission as H3/VLM/ASR. A textual
        # provider never silently runs beside a local GPU model.
        if CURRENT_RESOURCE.get() is not None:
            raise LLMNotConfigured("AGENT_STAGE_REQUIRES_PREVIOUS_MODEL_RELEASE")
        if on_progress:
            on_progress("WAITING_RESOURCE")
        with resource_slot(
            make_session_factory(db.get_bind()),
            "AGENT",
            1,
            release_models=partial(release_text_model, config),
            wait_seconds=0 if profile.kind == "mock" else 300,
            on_acquired=(lambda _identity: on_progress("PREPARING_PROVIDER")) if on_progress else None,
        ):
            if on_progress:
                on_progress("WAITING_PROVIDER")
            response = provider.complete(
                [ChatMessage("system", system), ChatMessage("user", user)],
                schema=schema,
                json_mode=True,
            )
            if on_progress:
                on_progress("RELEASING_PROVIDER")
        if on_progress:
            on_progress("VALIDATING_RESULT")
        return response

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

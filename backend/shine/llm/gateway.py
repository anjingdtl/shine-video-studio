from __future__ import annotations

import json
import time
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
from shine.llm.context_window import model_context_for_profile, provider_model_matches
from shine.llm.mock import MockLLMProvider, Responder
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.output_budget import (
    apply_output_budget,
    context_overflow,
    creative_completion_cap,
    estimate_input_tokens,
)
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute, ProviderProfile

#: The generation probe is fixed, synthetic and empty of project material.
#: It carries no script, no asset, no character image and no voice: it only
#: proves that the configured provider can answer with structured JSON.
GENERATION_PROBE_SYSTEM = "You are a connectivity probe. Answer with one JSON object only."
GENERATION_PROBE_USER = 'Answer with one JSON object: {"ok": true}'
GENERATION_PROBE_SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


DEFAULT_AGENT_OUTPUT_TOKENS = 16_384
"""The output ceiling for an ordinary agent call.

A reasoning model decides how long to think, and a 131k ceiling lets it decide
"very long": a live structured extraction ran past fifteen minutes with nothing
placeholder-shaped to show for it.  16k is ample for a structured role answer
(outline part, scene preparation, one shot breakdown) while bounding the worst
case to minutes.  Creative scene writing keeps its own measured budget in
``shine.literary.budget``, so this ceiling never truncates a scene.
"""
"""The completion ceiling one typed agent reply is budgeted against.

A current 1M-context reasoning model bills its thinking as completion, so a
16K ceiling is spent entirely on reasoning and the visible JSON comes back
empty with ``finish_reason=length``. 128K is the ceiling the routed provider
accepts for this class of model, so the default is a real budget instead of a
guess about the model maximum. The value is a ceiling, never a demand: a
profile that declares or configures something smaller still wins through the
min chain below.
"""

AGENT_RATE_LIMIT_RETRY_ATTEMPTS = 2
"""How often an agent call waits out a throttled provider and asks again.

A 429 is a refusal: the provider answered and produced no answer, so a later
attempt cannot buy a second copy of one answer.  The agent lane may wait for
the provider instead of losing a whole scene to a momentary throttle; the
plain connectivity probe keeps its own stricter budget.
"""


def agent_output_config(config: ProviderConfig) -> ProviderConfig:
    """Limit each agent response without expanding a configured smaller budget."""
    configured = config.generation_options.get("max_completion_tokens")
    if configured:
        # An explicit operator choice is respected: this function only bounds
        # the default, it never overrules a stated budget.
        output_limit = int(configured)
    elif config.max_output_tokens:
        output_limit = min(DEFAULT_AGENT_OUTPUT_TOKENS, int(config.max_output_tokens))
    else:
        output_limit = DEFAULT_AGENT_OUTPUT_TOKENS
    if config.kind == "openai_compatible":
        options = {**config.generation_options, "max_completion_tokens": output_limit}
        # A structured role reads and answers; it is not asked to deliberate for
        # minutes.  Measured on the live provider: the same request with
        # ``reasoning_effort=low`` spent 17 reasoning tokens instead of 152 and
        # returned twice as fast, and a 44k-character outline read that used to
        # spend 300s timing out now fits the same wall clock.  Creative writing
        # (``task_category="creative"``) does NOT pass through here and keeps the
        # model's own deliberation budget.
        options.setdefault("reasoning_effort", "low")
        return replace(config, generation_options=options)
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
        # A queued task carries the model it was queued with.  A worker tick
        # pins that profile here so every internal role lookup of the unit -
        # including the consent fingerprint - resolves to the same identity
        # instead of whatever the route happens to point at now (J05).
        self._pinned_roles: dict[str, ProviderProfile] = {}

    def config_for(self, profile: ProviderProfile, *, api_key_override: str | None = None) -> ProviderConfig:
        from shine.schemas import TextGenerationOptions
        options = TextGenerationOptions.model_validate(profile.generation_options or {}).model_dump(exclude_none=True)
        if options and profile.kind == "anthropic":
            # Anthropic profiles may set one option: the request max_tokens.
            # Everything else stays an OpenAI-compatible-only feature.
            disallowed = set(options) - {"max_tokens"}
            if disallowed:
                raise LLMNotConfigured(
                    "Anthropic profiles support only the max_tokens generation option")
            options = {"max_tokens": options["max_tokens"]}
        elif options and profile.kind != "openai_compatible":
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

    def pin_role(self, role: str, profile: ProviderProfile | None) -> None:
        """Run this unit with the task's own profile, not the current route."""
        if profile is None:
            self._pinned_roles.pop(role, None)
        else:
            self._pinned_roles[role] = profile

    def clear_pins(self) -> None:
        self._pinned_roles.clear()

    def profile_for_role(self, db: Session, role: str) -> ProviderProfile:
        pinned = self._pinned_roles.get(role)
        if pinned is not None:
            return pinned
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
        system: str = "",
        user: str = "",
        schema: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
        task_category: str = "default",
        precompiled: Any | None = None,
        budget: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """One role call, either planned here or compiled by the caller.

        ``precompiled`` is the Call Governor's rendered request.  It is sent
        verbatim: the schema was already rendered into the user message and the
        budget was already approved against those exact bytes, so appending it
        again would send a request larger than the one that was approved.  That
        single-appendix rule is what makes "追加 Schema 不改变已检验预算"
        observable.
        """
        if precompiled is not None:
            system, user, schema = precompiled.system, precompiled.user, precompiled.schema
        profile = self.profile_for_role(db, role)
        config = self.config_for(profile)
        budget_evidence = None
        if precompiled is None:
            if task_category == "creative":
                input_estimate = estimate_input_tokens(system) + estimate_input_tokens(user)
                overflow = context_overflow(config, input_estimate)
                if overflow is not None:
                    # The request provably never left the workbench: the refusal
                    # is a named, local fact, not an unknown provider outcome.
                    raise LLMError("Creative request exceeds the shared context window",
                                   details=dict(overflow))
                budget_evidence = creative_completion_cap(
                    config, estimated_input_tokens=input_estimate)
                config = apply_output_budget(config, budget_evidence)
            else:
                config = agent_output_config(config)
        elif budget is not None:
            config = apply_output_budget(config, budget)
            budget_evidence = budget
        # An agent step must survive a throttled provider: a 429 is a refusal
        # that produced no answer, so waiting and asking again cannot buy a
        # second copy of one answer.  The plain connectivity probe keeps its
        # own stricter budget and never posts again after a refusal.
        config = replace(config, rate_limit_retry_attempts=AGENT_RATE_LIMIT_RETRY_ATTEMPTS)
        provider = self.provider_factory(config, self.mock_responder)
        if schema is not None and precompiled is None:
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
                f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
            )
        from shine.comfyops.scheduler import CURRENT_RESOURCE, resource_slot
        from shine.db import make_session_factory
        from shine.llm.lifecycle import release_text_model
        from shine.security import is_loopback_url

        def send() -> LLMResponse:
            if on_progress:
                on_progress("WAITING_PROVIDER")
            answer = provider.complete(
                [ChatMessage("system", system), ChatMessage("user", user)],
                schema=schema,
                json_mode=True,
            )
            if on_progress:
                on_progress("RELEASING_PROVIDER")
            return answer

        # A remote text provider costs no local VRAM, so it must not queue behind
        # the local renderer's GPU admission.  Sharing that single queue meant one
        # slow worker call held the slot for its whole timeout, and every other
        # request died with GPU_BUSY after waiting five minutes - an outage caused
        # purely by bookkeeping, observed live with two scenes of one volume.
        # Local providers (ollama, a local OpenAI-compatible server) still take
        # the slot, because they do use the same GPU as the renderer.
        needs_local_gpu = profile.kind != "mock" and (
            profile.kind == "ollama" or is_loopback_url(config.base_url))
        if CURRENT_RESOURCE.get() is not None and needs_local_gpu:
            raise LLMNotConfigured("AGENT_STAGE_REQUIRES_PREVIOUS_MODEL_RELEASE")
        if not needs_local_gpu:
            response = send()
        else:
            if on_progress:
                on_progress("WAITING_RESOURCE")
            with resource_slot(
                make_session_factory(db.get_bind()),
                "AGENT",
                1,
                release_models=partial(release_text_model, config),
                wait_seconds=300,
                on_acquired=(lambda _identity: on_progress("PREPARING_PROVIDER")) if on_progress else None,
            ):
                response = send()
        if on_progress:
            on_progress("VALIDATING_RESULT")
        if budget_evidence is not None:
            response.raw_meta["output_budget"] = budget_evidence
            if precompiled is not None:
                response.raw_meta["precompiled_request"] = precompiled.fingerprint
        return response

    def test_profile(self, profile: ProviderProfile, *, api_key: str | None = None) -> dict[str, Any]:
        provider = self.provider_for(profile, api_key_override=api_key)
        capabilities = provider.detect_capabilities()
        if profile.kind in {"openai_compatible", "ollama"} and capabilities.get("model_listed") is False:
            capabilities["warning"] = "The configured model was not listed by the provider"
        return capabilities

    def test_generation(self, profile: ProviderProfile, *, api_key: str | None = None) -> dict[str, Any]:
        """Run one minimal structured generation against the configured provider.

        The probe proves the generation endpoint, the model id, the
        authentication and the structured-output path.  It never claims
        anything about the quality of an answer.
        """
        if profile.kind == "mock":
            return {
                "generation_ok": False,
                "structured_output_ok": None,
                "mock": True,
                "model": None,
                "model_matches_config": None,
                "latency_ms": None,
                "error_code": "LLM_MOCK_PROVIDER",
                "message": "A mock provider is not a generation acceptance",
            }
        started = time.perf_counter()
        try:
            config = agent_output_config(self.config_for(profile))
            provider = self.provider_factory(config, self.mock_responder)
            response = provider.complete(
                [
                    ChatMessage("system", GENERATION_PROBE_SYSTEM),
                    ChatMessage("user", GENERATION_PROBE_USER),
                ],
                schema=GENERATION_PROBE_SCHEMA,
                json_mode=True,
            )
        except LLMError as exc:
            return {
                "generation_ok": False,
                "structured_output_ok": False,
                "mock": False,
                "model": None,
                "model_matches_config": None,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error_code": exc.code,
                "message": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 - no provider failure may escape unclassified
            return {
                "generation_ok": False,
                "structured_output_ok": False,
                "mock": False,
                "model": None,
                "model_matches_config": None,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error_code": "LLM_ERROR",
                "message": f"{type(exc).__name__}: {exc}",
            }
        is_mock = bool(response.raw_meta.get("is_mock"))
        return {
            "generation_ok": not is_mock,
            "structured_output_ok": response.parsed is not None,
            "mock": is_mock,
            "model": response.model,
            "model_matches_config": provider_model_matches(config.base_url, config.model, response.model),
            "latency_ms": response.latency_ms,
            "error_code": "LLM_MOCK_PROVIDER" if is_mock else None,
            "message": (
                "A mock provider is not a generation acceptance"
                if is_mock
                else "Generation and structured output succeeded"
            ),
            # The creative budget evidence: what the provider declares (or
            # fails to declare) and the 60% cap the workbench would send.
            "creative_output_budget": creative_completion_cap(config),
        }


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

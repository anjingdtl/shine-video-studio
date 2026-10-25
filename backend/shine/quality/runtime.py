"""Explicit production reviewer routes, shared with the normal Worker entrypoints."""

from __future__ import annotations

from sqlalchemy import select

from shine.llm.base import LLMNotConfigured
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentRoute
from shine.quality.semantic import (
    LocalASRReviewer,
    LocalVisionReviewer,
    OpenAICompatibleASRReviewer,
    OpenAICompatibleVisionReviewer,
)
from shine.security import is_loopback_url


def configured_reviewers(factory, settings):
    """No default provider and no fallback to the textual qc_reviewer role.

    An absent route means missing coverage. An invalid explicit route is an
    operator configuration error, never permission to send bytes elsewhere.
    """
    gateway = LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))
    reviewers = []
    with factory() as db:
        for role, local, remote in (
            ("vlm_reviewer", LocalVisionReviewer, OpenAICompatibleVisionReviewer),
            ("asr_reviewer", LocalASRReviewer, OpenAICompatibleASRReviewer),
        ):
            if not db.scalar(select(AgentRoute).where(AgentRoute.role == role)):
                reviewers.append(None)
                continue
            config = gateway.config_for(gateway.profile_for_role(db, role))
            if config.kind not in {"openai_compatible", "ollama"}:
                raise LLMNotConfigured(f"{role} requires an explicitly routed OpenAI-compatible media endpoint")
            reviewers.append((local if is_loopback_url(config.base_url) else remote)(config))
    return tuple(reviewers)

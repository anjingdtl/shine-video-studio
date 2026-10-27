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


def _native_budget(db, config, category, minimum):
    """Use checked native declarations for admission, never as production approval."""
    from shine.comfyops.models import WorkflowRevision
    from shine.comfyops.native import native_errors

    budgets = [minimum]
    rows = db.scalars(select(WorkflowRevision).where(
        WorkflowRevision.status.in_(["STATIC_VALIDATED", "REAL_TESTED", "PRODUCTION_APPROVED"])
    ))
    for row in rows:
        binding = row.payload.get("native_provider") or {}
        if (row.payload.get("category") == category and binding.get("profile_id") == config.profile_id
                and binding.get("model") == config.model
                and not native_errors(db, row.payload, workflow_hash=row.sha256)):
            budgets.append(row.payload["resource_mb"])
    return max(budgets)


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
            profile = gateway.profile_for_role(db, role)
            config = gateway.config_for(profile)
            if config.kind not in {"openai_compatible", "ollama"}:
                raise LLMNotConfigured(f"{role} requires an explicitly routed OpenAI-compatible media endpoint")
            if is_loopback_url(config.base_url):
                category, minimum = ("VLM_REVIEW", 8000) if role == "vlm_reviewer" else ("ASR", 4000)
                options = ({"response_protocol": (profile.capabilities or {}).get("review_response_protocol")}
                           if role == "vlm_reviewer" else {})
                reviewers.append(local(config, resource_mb=_native_budget(db, config, category, minimum), **options))
            else:
                reviewers.append(remote(config))
    return tuple(reviewers)

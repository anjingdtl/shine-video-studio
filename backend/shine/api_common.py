from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from shine.config import Settings
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import ProviderProfile
from shine.schemas import ProviderKind, ProviderProfilePublic


def session_dependency(request: Request) -> Generator[Session, None, None]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DBSession = Annotated[Session, Depends(session_dependency)]


def app_settings(request: Request) -> Settings:
    return request.app.state.settings


def secret_store(request: Request) -> SecretStore:
    settings: Settings = request.app.state.settings
    return SecretStore(settings.secret_store_dir or settings.data_dir / "secrets")


def llm_gateway(request: Request, secrets: Annotated[SecretStore, Depends(secret_store)]) -> LLMGateway:
    return LLMGateway(secrets, mock_responder=getattr(request.app.state, "mock_llm_responder", None))


def public_profile(profile: ProviderProfile) -> ProviderProfilePublic:
    return ProviderProfilePublic(
        id=profile.id,
        name=profile.name,
        kind=ProviderKind(profile.kind),
        base_url=profile.base_url,
        model=profile.model,
        has_api_key=bool(profile.api_key_ref),
        capabilities=profile.capabilities or {},
        timeout_s=profile.timeout_s,
        max_retries=profile.max_retries,
        temperature=profile.temperature,
        enabled=profile.enabled,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )

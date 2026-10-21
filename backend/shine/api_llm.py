from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select

from shine.api_common import DBSession, public_profile, secret_store
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore, SecureStoreUnavailable
from shine.models import AgentRoute, ProviderProfile
from shine.schemas import (
    AgentRoutePublic,
    AgentRouteUpdate,
    ProviderKeyUpdate,
    ProviderProfileCreate,
    ProviderProfilePublic,
    ProviderProfileUpdate,
    ProviderTestResult,
)
from shine.services import add_audit

router = APIRouter(prefix="/api/v1", tags=["llm"])


def _gateway(secrets: SecretStore) -> LLMGateway:
    return LLMGateway(secrets)


@router.get("/provider-profiles", response_model=list[ProviderProfilePublic])
def list_profiles(db: DBSession) -> list[ProviderProfilePublic]:
    rows = db.scalars(select(ProviderProfile).order_by(ProviderProfile.created_at)).all()
    return [public_profile(row) for row in rows]


@router.post("/provider-profiles", response_model=ProviderProfilePublic, status_code=201)
def create_profile(
    body: ProviderProfileCreate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    if db.scalar(select(ProviderProfile).where(ProviderProfile.name == body.name)):
        raise HTTPException(status_code=409, detail="A provider profile with this name already exists")
    profile = ProviderProfile(
        name=body.name,
        kind=body.kind.value,
        base_url=body.base_url.rstrip("/"),
        model=body.model,
        timeout_s=body.timeout_s,
        max_retries=body.max_retries,
        temperature=body.temperature,
        enabled=body.enabled,
        capabilities={},
    )
    db.add(profile)
    db.flush()
    if body.api_key:
        try:
            profile.api_key_ref = secrets.store(profile.id, body.api_key)
        except SecureStoreUnavailable as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    add_audit(
        db,
        "user",
        "PROVIDER_PROFILE_CREATED",
        "provider_profile",
        profile.id,
        {"kind": profile.kind, "base_url": profile.base_url, "model": profile.model},
    )
    db.commit()
    db.refresh(profile)
    return public_profile(profile)


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfilePublic)
def update_profile(
    profile_id: str,
    body: ProviderProfileUpdate,
    db: DBSession,
) -> ProviderProfilePublic:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if "kind" in updates:
        updates["kind"] = str(updates["kind"])
    if "base_url" in updates:
        updates["base_url"] = str(updates["base_url"]).rstrip("/")
    if "name" in updates:
        duplicate = db.scalar(
            select(ProviderProfile).where(ProviderProfile.name == updates["name"], ProviderProfile.id != profile.id)
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A provider profile with this name already exists")
    for key, value in updates.items():
        setattr(profile, key, value)
    add_audit(
        db,
        "user",
        "PROVIDER_PROFILE_UPDATED",
        "provider_profile",
        profile.id,
        {"fields": sorted(updates)},
    )
    db.commit()
    db.refresh(profile)
    return public_profile(profile)


@router.delete("/provider-profiles/{profile_id}", status_code=204, response_class=Response)
def delete_profile(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> Response:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    routes = db.scalars(select(AgentRoute).where(AgentRoute.provider_profile_id == profile_id)).all()
    if routes:
        raise HTTPException(status_code=409, detail="Remove agent role routes before deleting this profile")
    secrets.delete(profile.api_key_ref)
    add_audit(db, "user", "PROVIDER_PROFILE_DELETED", "provider_profile", profile.id)
    db.delete(profile)
    db.commit()
    return Response(status_code=204)


@router.put("/provider-profiles/{profile_id}/key", response_model=ProviderProfilePublic)
def set_profile_key(
    profile_id: str,
    body: ProviderKeyUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    secrets.delete(profile.api_key_ref)
    try:
        profile.api_key_ref = secrets.store(profile.id, body.api_key)
    except SecureStoreUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    add_audit(db, "user", "PROVIDER_KEY_UPDATED", "provider_profile", profile.id, {"storage": secrets.mode})
    db.commit()
    db.refresh(profile)
    return public_profile(profile)


@router.delete("/provider-profiles/{profile_id}/key", response_model=ProviderProfilePublic)
def clear_profile_key(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    secrets.delete(profile.api_key_ref)
    profile.api_key_ref = None
    add_audit(db, "user", "PROVIDER_KEY_CLEARED", "provider_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return public_profile(profile)


@router.post("/provider-profiles/{profile_id}/test", response_model=ProviderTestResult)
def test_profile(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderTestResult:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    try:
        capabilities = _gateway(secrets).test_profile(profile)
    except LLMError as exc:
        return ProviderTestResult(ok=False, error_code=exc.code, message=str(exc))
    profile.capabilities = capabilities
    add_audit(
        db,
        "user",
        "PROVIDER_CONNECTION_TESTED",
        "provider_profile",
        profile.id,
        {"available": capabilities.get("available", False)},
    )
    db.commit()
    return ProviderTestResult(
        ok=bool(capabilities.get("available")),
        message="Connection succeeded",
        capabilities=capabilities,
    )


@router.get("/agent-routes", response_model=list[AgentRoutePublic])
def list_routes(db: DBSession) -> list[AgentRoutePublic]:
    rows = db.execute(
        select(AgentRoute, ProviderProfile)
        .join(ProviderProfile, AgentRoute.provider_profile_id == ProviderProfile.id)
        .order_by(AgentRoute.role)
    ).all()
    return [
        AgentRoutePublic(role=route.role, provider_profile_id=route.provider_profile_id, profile_name=profile.name)
        for route, profile in rows
    ]


@router.put("/agent-routes", response_model=AgentRoutePublic)
def upsert_route(body: AgentRouteUpdate, db: DBSession) -> AgentRoutePublic:
    profile = db.get(ProviderProfile, body.provider_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == body.role.value))
    if route is None:
        route = AgentRoute(role=body.role.value, provider_profile_id=profile.id)
        db.add(route)
    else:
        route.provider_profile_id = profile.id
    add_audit(
        db,
        "user",
        "AGENT_ROUTE_UPDATED",
        "agent_route",
        route.id or profile.id,
        {"role": body.role.value, "profile_id": profile.id},
    )
    db.commit()
    return AgentRoutePublic(
        role=body.role.value, provider_profile_id=profile.id, profile_name=profile.name
    )


@router.delete("/agent-routes/{role}", status_code=204, response_class=Response)
def delete_route(role: str, db: DBSession) -> Response:
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
    if not route:
        raise HTTPException(status_code=404, detail="Agent route not found")
    add_audit(db, "user", "AGENT_ROUTE_DELETED", "agent_route", route.id, {"role": role})
    db.delete(route)
    db.commit()
    return Response(status_code=204)


@router.get("/config/llm")
def llm_status(db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]) -> dict[str, Any]:
    profiles = db.scalars(select(ProviderProfile)).all()
    routes = db.scalars(select(AgentRoute)).all()
    return {
        "secret_storage": secrets.mode,
        "profile_count": len(profiles),
        "enabled_profile_count": sum(1 for profile in profiles if profile.enabled),
        "routed_roles": sorted(route.role for route in routes),
    }

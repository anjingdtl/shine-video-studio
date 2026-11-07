from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select

from shine.api_common import DBSession, public_profile, secret_store
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore, SecureStoreUnavailable
from shine.models import AgentRoute, ProviderProfile
from shine.schemas import (
    AgentRouteBulkUpdate,
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

#: Providers that talk to an HTTP service and therefore need a real endpoint.
REMOTE_KINDS = frozenset({"openai_compatible", "anthropic", "ollama"})


def _gateway(secrets: SecretStore) -> LLMGateway:
    return LLMGateway(secrets)


def _validate_profile_fields(
    *, kind: str, base_url: str, model: str, generation_options: dict[str, Any]
) -> None:
    """The cross-field contract shared by create and update.

    A profile that was accepted on create must stay acceptable on update,
    otherwise the update path becomes a way around the create contract.
    """
    if generation_options and kind != "openai_compatible":
        raise HTTPException(422, "Generation options currently require an OpenAI-compatible provider")
    if kind in REMOTE_KINDS:
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(422, "base_url must start with http:// or https://")
        if not model.strip():
            raise HTTPException(422, "model is required for remote providers")


def _profile_public(profile: ProviderProfile, secrets: SecretStore) -> ProviderProfilePublic:
    return public_profile(profile, secret_status=secrets.status(profile.api_key_ref))


def _rotate_key(profile: ProviderProfile, secret: str, secrets: SecretStore) -> str:
    """Write a new secret version, read it back, and return its reference.

    The current key file is deliberately not touched, so a database failure
    after this call leaves a working configuration behind.
    """
    new_ref: str | None = None
    try:
        new_ref = secrets.store(profile.id, secret)
        if not secrets.verify(new_ref, secret):
            raise SecureStoreUnavailable("The stored provider key could not be read back")
    except SecureStoreUnavailable:
        if new_ref:
            secrets.delete(new_ref)
        raise
    except Exception as exc:  # noqa: BLE001 - any write failure is a storage failure
        if new_ref:
            secrets.delete(new_ref)
        raise SecureStoreUnavailable(
            f"Provider key could not be stored: {type(exc).__name__}"
        ) from exc
    return new_ref


def _discard_old_secret(
    db, secrets: SecretStore, profile_id: str, old_ref: str | None, new_ref: str | None
) -> None:
    """Remove the superseded secret file after the switch-over succeeded.

    A failure here is recoverable and never invalidates the new key.
    """
    if not old_ref or old_ref == new_ref:
        return
    try:
        secrets.delete(old_ref)
    except Exception as exc:  # noqa: BLE001 - the new key is already active
        try:
            add_audit(
                db,
                "user",
                "PROVIDER_KEY_CLEANUP_PENDING",
                "provider_profile",
                profile_id,
                {"error": type(exc).__name__},
            )
            db.commit()
        except Exception:  # noqa: BLE001 - never fail the request over cleanup
            db.rollback()


def _connection_evidence(
    capabilities: dict[str, Any], *, error: LLMError | None
) -> dict[str, Any]:
    """Classify what a connection test actually proved.

    "The service answered" and "the configured model was confirmed" are two
    different facts, and a failure has to say which one is missing.
    """
    if error is None:
        return {
            "reachable": bool(capabilities.get("reachable", True)),
            "auth_ok": bool(capabilities.get("auth_ok", True)),
            "catalog_ok": bool(capabilities.get("catalog_ok", True)),
            "model_listed": capabilities.get("model_listed"),
        }
    code = error.code
    reachable: bool | None = None
    auth_ok: bool | None = None
    catalog_ok: bool | None = None
    model_listed: bool | None = None
    if code == "LLM_AUTH":
        reachable, auth_ok = True, False
    elif code == "LLM_CATALOG_INVALID":
        reachable, auth_ok, catalog_ok = True, True, False
    elif code == "LLM_MODEL_NOT_FOUND":
        reachable, auth_ok, catalog_ok = True, True, False
    elif code in {"LLM_CONNECTION", "LLM_TIMEOUT"}:
        reachable = False
    return {
        "reachable": reachable,
        "auth_ok": auth_ok,
        "catalog_ok": catalog_ok,
        "model_listed": model_listed,
    }


@router.get("/provider-profiles", response_model=list[ProviderProfilePublic])
def list_profiles(
    db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]
) -> list[ProviderProfilePublic]:
    rows = db.scalars(select(ProviderProfile).order_by(ProviderProfile.created_at)).all()
    return [_profile_public(row, secrets) for row in rows]


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
        generation_options=body.generation_options.model_dump(exclude_none=True),
    )
    db.add(profile)
    db.flush()
    new_ref: str | None = None
    if body.api_key:
        try:
            new_ref = _rotate_key(profile, body.api_key, secrets)
        except SecureStoreUnavailable as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Provider key could not be stored: {exc}") from exc
        profile.api_key_ref = new_ref
    add_audit(
        db,
        "user",
        "PROVIDER_PROFILE_CREATED",
        "provider_profile",
        profile.id,
        {"kind": profile.kind, "base_url": profile.base_url, "model": profile.model},
    )
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the profile must not exist without its key
        db.rollback()
        if new_ref:
            secrets.delete(new_ref)
        raise HTTPException(status_code=500, detail="Provider profile was not created") from exc
    db.refresh(profile)
    return _profile_public(profile, secrets)


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfilePublic)
def update_profile(
    profile_id: str,
    body: ProviderProfileUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if "generation_options" in updates:
        updates["generation_options"] = body.generation_options.model_dump(exclude_none=True)
    merged_kind = str(updates.get("kind", profile.kind))
    merged_base_url = str(updates.get("base_url", profile.base_url)).rstrip("/")
    merged_model = str(updates.get("model", profile.model))
    merged_options = updates.get("generation_options") or profile.generation_options or {}
    _validate_profile_fields(
        kind=merged_kind,
        base_url=merged_base_url,
        model=merged_model,
        generation_options=merged_options,
    )
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
    if set(updates) - {"name"}:
        # Any change of the provider contract invalidates the recorded evidence.
        profile.capabilities = {}
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
    return _profile_public(profile, secrets)


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
    old_ref = profile.api_key_ref
    add_audit(db, "user", "PROVIDER_PROFILE_DELETED", "provider_profile", profile.id)
    db.delete(profile)
    db.commit()
    if old_ref:
        secrets.delete(old_ref)
    return Response(status_code=204)


@router.put("/provider-profiles/{profile_id}/key", response_model=ProviderProfilePublic)
def set_profile_key(
    profile_id: str,
    body: ProviderKeyUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    """Rotate the provider key without ever leaving the profile keyless.

    The new key is written and read back first, the database switches over in
    one commit, and only then is the superseded file removed.  A failure at
    any step leaves the previous key usable.
    """
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    try:
        new_ref = _rotate_key(profile, body.api_key, secrets)
    except SecureStoreUnavailable as exc:
        raise HTTPException(status_code=500, detail=f"Provider key could not be stored: {exc}") from exc
    old_ref = profile.api_key_ref
    profile.api_key_ref = new_ref
    profile.capabilities = {}
    add_audit(db, "user", "PROVIDER_KEY_UPDATED", "provider_profile", profile.id, {"storage": secrets.mode})
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the previous key stays in use
        db.rollback()
        secrets.delete(new_ref)
        raise HTTPException(
            status_code=500,
            detail="Provider key was not switched over; the previous key is still in use",
        ) from exc
    _discard_old_secret(db, secrets, profile.id, old_ref, new_ref)
    db.refresh(profile)
    return _profile_public(profile, secrets)


@router.delete("/provider-profiles/{profile_id}/key", response_model=ProviderProfilePublic)
def clear_profile_key(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    old_ref = profile.api_key_ref
    profile.api_key_ref = None
    profile.capabilities = {}
    add_audit(db, "user", "PROVIDER_KEY_CLEARED", "provider_profile", profile.id)
    db.commit()
    if old_ref:
        secrets.delete(old_ref)
    db.refresh(profile)
    return _profile_public(profile, secrets)


@router.post("/provider-profiles/{profile_id}/test", response_model=ProviderTestResult)
def test_profile(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderTestResult:
    """Prove the connection and the model catalog.

    "The service answered" is not "the configured model exists".  The result
    reports both, and a missing model makes the test fail.
    """
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    from shine.comfyops.llm_readiness import profile_identity

    checked_at = datetime.now(UTC)
    identity = profile_identity(profile)
    try:
        capabilities = _gateway(secrets).test_profile(profile)
    except LLMError as exc:
        capabilities = {"available": False, "error_code": exc.code, "message": str(exc)}
        evidence = _connection_evidence(capabilities, error=exc)
        profile.capabilities = {**capabilities, "setup_config_sha256": identity}
        add_audit(
            db,
            "user",
            "PROVIDER_CONNECTION_TESTED",
            "provider_profile",
            profile.id,
            {"available": False, "error_code": exc.code},
        )
        db.commit()
        return ProviderTestResult(
            ok=False,
            level="connection",
            error_code=exc.code,
            message=str(exc),
            capabilities=profile.capabilities,
            checked_config_sha256=identity,
            checked_at=checked_at,
            **evidence,
        )
    evidence = _connection_evidence(capabilities, error=None)
    model_listed = capabilities.get("model_listed")
    ok = bool(capabilities.get("available")) and model_listed is not False
    error_code = None
    message = "连接检查通过：服务可达且模型目录列出了配置的 Model ID"
    if not ok:
        error_code = "LLM_MODEL_NOT_LISTED"
        message = "服务可连接，但模型目录中没有配置的 Model ID；请从候选 Model ID 中明确选择"
    capabilities = {**capabilities, "setup_config_sha256": identity}
    profile.capabilities = capabilities
    add_audit(
        db,
        "user",
        "PROVIDER_CONNECTION_TESTED",
        "provider_profile",
        profile.id,
        {"available": bool(capabilities.get("available")), "model_listed": model_listed},
    )
    db.commit()
    return ProviderTestResult(
        ok=ok,
        level="connection",
        error_code=error_code,
        message=message,
        capabilities=capabilities,
        checked_config_sha256=identity,
        checked_at=checked_at,
        **evidence,
    )


@router.post("/provider-profiles/{profile_id}/test-generation", response_model=ProviderTestResult)
def test_generation(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderTestResult:
    """Run one minimal structured generation against the configured provider.

    The probe carries no project material: no script, no asset, no character
    image and no voice.  It proves the generation endpoint, the model id, the
    authentication and the structured-output path - never answer quality.
    """
    profile = db.get(ProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    from shine.comfyops.llm_readiness import profile_identity

    checked_at = datetime.now(UTC)
    identity = profile_identity(profile)
    evidence = _gateway(secrets).test_generation(profile)
    generation = {
        "generation_ok": bool(evidence.get("generation_ok")),
        "structured_output_ok": evidence.get("structured_output_ok"),
        "model": evidence.get("model"),
        "model_matches_config": evidence.get("model_matches_config"),
        "latency_ms": evidence.get("latency_ms"),
        "error_code": evidence.get("error_code"),
        "mock": bool(evidence.get("mock")),
        "config_sha256": identity,
        "checked_at": checked_at.isoformat(),
    }
    existing = dict(profile.capabilities or {})
    existing["generation"] = generation
    profile.capabilities = existing
    add_audit(
        db,
        "user",
        "PROVIDER_GENERATION_TESTED",
        "provider_profile",
        profile.id,
        {"generation_ok": generation["generation_ok"], "error_code": generation["error_code"]},
    )
    db.commit()
    ok = bool(generation["generation_ok"])
    message = "生成与结构化输出测试通过"
    if not ok:
        message = "生成测试未通过；连接可用不等于模型可以完成结构化生成"
    return ProviderTestResult(
        ok=ok,
        level="generation",
        error_code=generation["error_code"],
        message=message,
        capabilities=existing,
        checked_config_sha256=identity,
        checked_at=checked_at,
        generation_ok=generation["generation_ok"],
        structured_output_ok=generation["structured_output_ok"],
        model=generation["model"],
        latency_ms=generation["latency_ms"],
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


@router.put("/agent-routes/bulk")
def bulk_route(body: AgentRouteBulkUpdate, db: DBSession) -> dict[str, Any]:
    """Bind every text role in one transaction.

    A half-bound routing table silently sends some roles to a different
    model, so either every role is changed or none is.
    """
    profile = db.get(ProviderProfile, body.provider_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    if profile.kind == "mock":
        raise HTTPException(status_code=422, detail="A mock provider cannot take over the text roles")
    if not profile.enabled:
        raise HTTPException(status_code=422, detail="The selected provider profile is disabled")
    roles = sorted({role.value for role in body.roles})
    if not roles:
        raise HTTPException(status_code=422, detail="At least one role is required")
    try:
        for role in roles:
            route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
            if route is None:
                db.add(AgentRoute(role=role, provider_profile_id=profile.id))
            else:
                route.provider_profile_id = profile.id
        add_audit(
            db,
            "user",
            "AGENT_ROUTES_BOUND",
            "provider_profile",
            profile.id,
            {"roles": roles},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - no route may be half changed
        db.rollback()
        raise HTTPException(
            status_code=500, detail="The text roles were not bound; no route was changed"
        ) from exc
    from shine.comfyops.llm_readiness import llm_readiness

    readiness = llm_readiness(db)
    rows = db.execute(
        select(AgentRoute, ProviderProfile)
        .join(ProviderProfile, AgentRoute.provider_profile_id == ProviderProfile.id)
        .order_by(AgentRoute.role)
    ).all()
    return {
        "roles": [
            AgentRoutePublic(role=route.role, provider_profile_id=route.provider_profile_id, profile_name=profile.name)
            for route, profile in rows
        ],
        "bound_roles": roles,
        "readiness": readiness,
    }


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

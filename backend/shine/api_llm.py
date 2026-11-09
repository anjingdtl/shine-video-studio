from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, text

from shine.api_common import DBSession, public_profile, secret_store
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.llm.role_catalog import TEXT_ROLES
from shine.llm.secrets import SecretStore, SecureStoreUnavailable
from shine.models import AgentRoute, AgentTaskRecord, ProviderProfile
from shine.schemas import (
    AgentRouteBatchUpdate,
    AgentRouteBulkUpdate,
    AgentRoutePreviewRequest,
    AgentRoutePublic,
    AgentRouteUpdate,
    ProviderKeyUpdate,
    ProviderProfileCreate,
    ProviderProfileDuplicate,
    ProviderProfilePublic,
    ProviderProfileUpdate,
    ProviderTestResult,
)
from shine.services import add_audit

router = APIRouter(prefix="/api/v1", tags=["llm"])

#: Providers that talk to an HTTP service and therefore need a real endpoint.
REMOTE_KINDS = frozenset({"openai_compatible", "anthropic", "ollama"})
LIVE_ROUTE_CHANGE_STATUSES = frozenset({"QUEUED", "RUNNING", "NEEDS_REVIEW"})


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
    still_referenced = db.scalar(
        select(ProviderProfile.id).where(
            ProviderProfile.api_key_ref == old_ref,
            ProviderProfile.id != profile_id,
        ).limit(1)
    )
    if still_referenced:
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


def _active_task_dict(task: AgentTaskRecord) -> dict[str, str]:
    """Expose only task identity and pinned model metadata in impact previews."""
    return {
        "task_id": task.id,
        "role": task.role_id,
        "status": task.status,
        "model_profile_id": task.model_profile_id,
        "model_id": task.model_id,
    }


def _live_tasks_for_roles(db, roles: set[str]) -> list[AgentTaskRecord]:
    if not roles:
        return []
    from shine.llm.role_catalog import task_roles_for

    return list(db.scalars(
        select(AgentTaskRecord)
        .where(
            AgentTaskRecord.role_id.in_(task_roles_for(roles)),
            AgentTaskRecord.status.in_(sorted(LIVE_ROUTE_CHANGE_STATUSES)),
        )
        .order_by(AgentTaskRecord.created_at, AgentTaskRecord.id)
    ).all())


def _split_pinned_tasks(tasks: list[AgentTaskRecord]) -> tuple[list[AgentTaskRecord], list[AgentTaskRecord]]:
    """Split live tasks into ones that continue on their snapshot and blockers.

    A queued task with a durable ``model_profile_id`` is not broken by a route
    change: the worker pins that profile for the unit (J05).  Only a task
    without a usable snapshot makes a mid-flight reassignment unsafe.
    """
    from shine.llm.role_catalog import TASK_ROLE_CONSUMERS

    pinned, blocking = [], []
    for task in tasks:
        pinned_task = task.role_id in TASK_ROLE_CONSUMERS and bool(task.model_profile_id)
        (pinned if pinned_task else blocking).append(task)
    return pinned, blocking


def _live_tasks_for_profile(db, profile_id: str) -> list[AgentTaskRecord]:
    return list(db.scalars(
        select(AgentTaskRecord)
        .where(
            AgentTaskRecord.model_profile_id == profile_id,
            AgentTaskRecord.status.in_(sorted(LIVE_ROUTE_CHANGE_STATUSES)),
        )
        .order_by(AgentTaskRecord.created_at, AgentTaskRecord.id)
    ).all())


def _route_revision(db) -> str:
    rows = db.scalars(select(AgentRoute).order_by(AgentRoute.role)).all()
    canonical = [(row.role, row.provider_profile_id) for row in rows]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _route_rows(db) -> list[AgentRoutePublic]:
    rows = db.execute(
        select(AgentRoute, ProviderProfile)
        .join(ProviderProfile, AgentRoute.provider_profile_id == ProviderProfile.id)
        .order_by(AgentRoute.role)
    ).all()
    return [
        AgentRoutePublic(role=route.role, provider_profile_id=route.provider_profile_id,
                         profile_name=profile.name)
        for route, profile in rows
    ]


def _route_preview(db, assignments: list[dict[str, Any]], secrets: SecretStore) -> dict[str, Any]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    current_routes = {
        row.role: row.provider_profile_id for row in db.scalars(select(AgentRoute))
    }
    changes: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    changed_roles: set[str] = set()

    for item in assignments:
        role = str(item["role"])
        profile_id = item.get("provider_profile_id")
        if role not in TEXT_ROLES:
            raise HTTPException(
                status_code=422,
                detail={"code": "TEXT_AGENT_ROLE_REQUIRED", "message": "请选择文本 Agent 角色。"},
            )
        if role in seen:
            raise HTTPException(
                status_code=422,
                detail={"code": "DUPLICATE_AGENT_ROLE", "message": "同一批次不能重复选择同一个角色。"},
            )
        seen.add(role)
        normalized.append({"role": role, "provider_profile_id": profile_id})
        current_profile_id = current_routes.get(role)
        if current_profile_id == profile_id:
            changes.append({
                "role": role,
                "action": "unchanged",
                "from_profile_id": current_profile_id,
                "to_profile_id": profile_id,
                "from_profile_name": None,
                "to_profile_name": None,
            })
            continue

        changed_roles.add(role)
        current_profile = db.get(ProviderProfile, current_profile_id) if current_profile_id else None
        target_profile = db.get(ProviderProfile, profile_id) if profile_id else None
        action = "added" if current_profile_id is None else "removed" if profile_id is None else "reassigned"
        changes.append({
            "role": role,
            "action": action,
            "from_profile_id": current_profile_id,
            "to_profile_id": profile_id,
            "from_profile_name": current_profile.name if current_profile else None,
            "to_profile_name": target_profile.name if target_profile else None,
        })
        if profile_id is None:
            continue
        if target_profile is None:
            blockers.append({
                "role": role,
                "code": "PROVIDER_PROFILE_NOT_FOUND",
                "message": "所选模型已经不存在，请刷新模型列表。",
            })
            continue
        if target_profile.kind == "mock":
            blockers.append({
                "role": role,
                "code": "REAL_TEXT_MODEL_REQUIRED",
                "message": "开发测试模型不能用于真实文本工种。",
            })
        elif not target_profile.enabled:
            blockers.append({
                "role": role,
                "code": "PROVIDER_PROFILE_DISABLED",
                "message": "所选模型已停用，请先启用或选择其他模型。",
            })
        elif not target_profile.model.strip():
            blockers.append({
                "role": role,
                "code": "PROVIDER_MODEL_ID_REQUIRED",
                "message": "所选模型缺少 Model ID。",
            })
        else:
            secret_status = secrets.status(target_profile.api_key_ref)
            if secret_status in {"missing", "unreadable"}:
                blockers.append({
                    "role": role,
                    "code": "PROVIDER_CREDENTIAL_UNAVAILABLE",
                    "message": "所选模型的密钥不可读取，请重新保存密钥。",
                })

    from shine.llm.role_catalog import TASK_ROLE_CONSUMERS

    affected = _live_tasks_for_roles(db, changed_roles)
    active_tasks = []
    blocking_tasks = []
    for task in affected:
        pinned = task.role_id in TASK_ROLE_CONSUMERS and bool(task.model_profile_id) and bool(task.model_profile_id)
        row = {
            **_active_task_dict(task),
            # A task with a durable model snapshot continues on it; only the
            # next task follows the new route.  A task without one cannot be
            # handed a different provider mid-flight.
            "continuity": "PINNED_MODEL" if pinned else "BLOCKING_ROUTE",
            "consumes_role": TASK_ROLE_CONSUMERS.get(task.role_id, task.role_id),
        }
        active_tasks.append(row)
        if not pinned:
            blocking_tasks.append(row)
    return {
        "expected_revision": _route_revision(db),
        "updates": normalized,
        "changes": changes,
        "changed_role_count": len(changed_roles),
        "affected_tasks": active_tasks,
        "blocking_tasks": blocking_tasks,
        "blockers": blockers,
        "can_apply": bool(changed_roles) and not blocking_tasks and not blockers,
    }


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
            "auth_ok": capabilities.get("auth_ok", True),
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


@router.get("/llm/roles")
def list_text_roles(
    db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]
) -> dict[str, Any]:
    from shine.comfyops.llm_readiness import llm_readiness

    report = llm_readiness(db, secrets=secrets)
    return {"roles": report["roles"], "route_revision": _route_revision(db)}


@router.get("/llm/readiness")
def workflow_llm_readiness(
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
    workflow: str | None = None,
) -> dict[str, Any]:
    from shine.comfyops.llm_readiness import llm_readiness

    try:
        return llm_readiness(db, workflow=workflow, secrets=secrets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/llm/capabilities")
def llm_capabilities(
    db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]
) -> dict[str, Any]:
    from shine.comfyops.llm_readiness import multimodal_capability_status

    return {"multimodal": multimodal_capability_status(db, secrets=secrets)}


@router.get("/llm/overview")
def llm_overview(
    db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]
) -> dict[str, Any]:
    from shine.comfyops.llm_readiness import (
        llm_readiness,
        multimodal_capability_status,
        profile_evidence,
    )

    profiles = db.scalars(select(ProviderProfile).order_by(ProviderProfile.created_at)).all()
    readiness = llm_readiness(db, secrets=secrets)
    return {
        "contract": "llm-management-v1",
        "profiles": [
            {
                **_profile_public(row, secrets).model_dump(mode="json"),
                "evidence": profile_evidence(row, secrets.status(row.api_key_ref)),
            }
            for row in profiles
        ],
        "routes": _route_rows(db),
        "route_revision": _route_revision(db),
        "roles": readiness["roles"],
        "readiness": readiness,
        "multimodal_capabilities": multimodal_capability_status(db, secrets=secrets),
        "workflows": [
            "script_and_storyboard", "four_stage", "film_crew", "real_production", "all_text",
        ],
    }


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


@router.post("/provider-profiles/{profile_id}/duplicate", response_model=ProviderProfilePublic, status_code=201)
def duplicate_profile(
    profile_id: str,
    body: ProviderProfileDuplicate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    """Clone non-secret settings and, only when asked, re-encrypt a key.

    The new profile always receives its own SecretStore reference.  The
    source key is read only inside the service and never enters a response or
    audit payload.
    """
    source = db.get(ProviderProfile, profile_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    if db.scalar(select(ProviderProfile).where(ProviderProfile.name == body.name)):
        raise HTTPException(status_code=409, detail="A provider profile with this name already exists")
    if body.copy_credential and not source.api_key_ref:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVIDER_CREDENTIAL_NOT_AVAILABLE",
                "message": "原模型没有可复制的密钥；请新建后单独填写。",
            },
        )

    options = (
        body.generation_options.model_dump(exclude_none=True)
        if body.generation_options is not None
        else dict(source.generation_options or {})
    )
    _validate_profile_fields(
        kind=source.kind,
        base_url=source.base_url,
        model=body.model,
        generation_options=options,
    )
    duplicate = ProviderProfile(
        name=body.name,
        kind=source.kind,
        base_url=source.base_url,
        model=body.model,
        timeout_s=source.timeout_s,
        max_retries=source.max_retries,
        temperature=source.temperature,
        enabled=source.enabled,
        capabilities={},
        generation_options=options,
    )
    db.add(duplicate)
    db.flush()
    new_ref: str | None = None
    try:
        if body.copy_credential:
            try:
                credential = secrets.load(source.api_key_ref)
            except Exception as exc:  # noqa: BLE001 - do not return storage details or secret data
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PROVIDER_CREDENTIAL_UNAVAILABLE",
                        "message": "原模型的密钥不可读取；请新建后重新保存密钥。",
                    },
                ) from exc
            if not credential:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PROVIDER_CREDENTIAL_UNAVAILABLE",
                        "message": "原模型的密钥不可读取；请新建后重新保存密钥。",
                    },
                )
            try:
                new_ref = _rotate_key(duplicate, credential, secrets)
            except SecureStoreUnavailable as exc:
                raise HTTPException(
                    status_code=500,
                    detail="新模型的密钥副本无法安全保存；原模型配置未变更。",
                ) from exc
            duplicate.api_key_ref = new_ref

        add_audit(
            db,
            "user",
            "PROVIDER_PROFILE_DUPLICATED",
            "provider_profile",
            duplicate.id,
            {
                "source_profile_id": source.id,
                "model": duplicate.model,
                "credential_copied": body.copy_credential,
            },
        )
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001 - remove the unreferenced secret on rollback
            db.rollback()
            if new_ref:
                secrets.delete(new_ref)
            raise HTTPException(status_code=500, detail="Provider profile was not duplicated") from exc
    except HTTPException:
        db.rollback()
        if new_ref:
            secrets.delete(new_ref)
        raise
    db.refresh(duplicate)
    return _profile_public(duplicate, secrets)


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfilePublic)
def update_profile(
    profile_id: str,
    body: ProviderProfileUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderProfilePublic:
    db.execute(text("BEGIN IMMEDIATE"))
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
    updates = {key: value for key, value in updates.items() if value != getattr(profile, key)}
    execution_changes = set(updates) - {"name"}
    if execution_changes:
        active_tasks = _live_tasks_for_profile(db, profile.id)
        if execution_changes == {"timeout_s"}:
            # A waiting limit can be changed before an explicit retry without
            # changing what the previous (possibly sent) request contained.
            active_tasks = [task for task in active_tasks if task.status != "NEEDS_REVIEW"]
        if active_tasks:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROVIDER_PROFILE_HAS_IN_FLIGHT_TASKS",
                    "message": "此模型仍被排队、运行或待核对任务使用；请先完成或对账这些任务，再修改模型配置。",
                    "active_tasks": [_active_task_dict(task) for task in active_tasks],
                },
            )
        # Keep the previous evidence with its old fingerprint.  Readers can
        # label it expired instead of confusing a changed model with a never-
        # tested model.
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
    active_tasks = _live_tasks_for_profile(db, profile_id)
    if routes or active_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVIDER_PROFILE_IN_USE",
                "message": "此模型仍被 Agent 或未结束任务使用；请先改派角色并完成或对账任务。",
                "roles_in_use": sorted(route.role for route in routes),
                "active_tasks": [_active_task_dict(task) for task in active_tasks],
            },
        )
    old_ref = profile.api_key_ref
    if old_ref:
        shared = db.scalar(
            select(ProviderProfile.id).where(
                ProviderProfile.api_key_ref == old_ref,
                ProviderProfile.id != profile_id,
            ).limit(1)
        )
        if shared:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROVIDER_CREDENTIAL_SHARED",
                    "message": "此密钥引用仍被其他模型使用；为避免破坏其他配置，请先为模型分别轮换密钥。",
                },
            )
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
    active_tasks = _live_tasks_for_profile(db, profile.id)
    if active_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVIDER_PROFILE_HAS_IN_FLIGHT_TASKS",
                "message": "此模型仍被排队、运行或待核对任务使用；请先完成或对账这些任务，再轮换密钥。",
                "active_tasks": [_active_task_dict(task) for task in active_tasks],
            },
        )
    try:
        new_ref = _rotate_key(profile, body.api_key, secrets)
    except SecureStoreUnavailable as exc:
        raise HTTPException(status_code=500, detail=f"Provider key could not be stored: {exc}") from exc
    old_ref = profile.api_key_ref
    profile.api_key_ref = new_ref
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
    active_tasks = _live_tasks_for_profile(db, profile.id)
    if active_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVIDER_PROFILE_HAS_IN_FLIGHT_TASKS",
                "message": "此模型仍被排队、运行或待核对任务使用；请先完成或对账任务，再移除密钥。",
                "active_tasks": [_active_task_dict(task) for task in active_tasks],
            },
        )
    old_ref = profile.api_key_ref
    profile.api_key_ref = None
    add_audit(db, "user", "PROVIDER_KEY_CLEARED", "provider_profile", profile.id)
    db.commit()
    if old_ref:
        _discard_old_secret(db, secrets, profile.id, old_ref, None)
    db.refresh(profile)
    return _profile_public(profile, secrets)


@router.post("/provider-profiles/{profile_id}/test", response_model=ProviderTestResult)
def test_profile(
    profile_id: str,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> ProviderTestResult:
    """Check reachability and, when supported, the provider's model catalog.

    An exact catalog miss fails the check. A provider without a catalog can
    still be reachable; in that case its Model ID and authentication stay
    unverified until the separate structured-generation probe succeeds.
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
        profile.capabilities = {
            **dict(profile.capabilities or {}),
            **capabilities,
            **evidence,
            "setup_config_sha256": identity,
            "setup_checked_at": checked_at.isoformat(),
        }
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
    if model_listed is True:
        message = "连接检查通过：服务可达，模型目录列出了配置的 Model ID。"
    elif ok:
        message = "服务已连接，但模型目录不可验证；请确认 Model ID，并进行结构化生成检查。"
    else:
        error_code = "LLM_MODEL_NOT_LISTED"
        message = "服务可连接，但模型目录中没有配置的 Model ID；请从候选 Model ID 中明确选择"
    capabilities = {
        **capabilities,
        **evidence,
        "setup_config_sha256": identity,
        "setup_checked_at": checked_at.isoformat(),
    }
    profile.capabilities = {**dict(profile.capabilities or {}), **capabilities}
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
    if (
        generation["generation_ok"]
        and generation["structured_output_ok"]
        and generation["model_matches_config"] is True
    ):
        existing["reachable"] = True
        existing["auth_ok"] = True
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
    ok = bool(
        generation["generation_ok"]
        and generation["structured_output_ok"]
        and generation["model_matches_config"] is True
    )
    message = "生成与结构化输出测试通过"
    if not ok:
        message = "生成测试未通过；连接可用不等于模型可以完成结构化生成"
    elif existing.get("model_listed") is False:
        message = "结构化生成通过，但模型目录没有列出该 Model ID；请核对服务商命名。"
    elif existing.get("model_listed") is None:
        message = "结构化生成通过，但模型目录不可验证；Model ID 未由目录确认。"
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
    return _route_rows(db)


@router.post("/agent-routes/preview")
def preview_agent_routes(
    body: AgentRoutePreviewRequest,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> dict[str, Any]:
    return _route_preview(
        db,
        [
            {"role": item.role.value, "provider_profile_id": item.provider_profile_id}
            for item in body.updates
        ],
        secrets,
    )


@router.put("/agent-routes/batch")
def batch_agent_routes(
    body: AgentRouteBatchUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> dict[str, Any]:
    """Apply heterogeneous text-role changes in one revision-checked commit."""
    assignments = [
        {"role": item.role.value, "provider_profile_id": item.provider_profile_id}
        for item in body.updates
    ]
    preview = _route_preview(db, assignments, secrets)
    if preview["expected_revision"] != body.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_ROUTE_REVISION_CONFLICT",
                "message": "Agent 分配已被其他页面更新；请刷新后重新预览。",
                "current_revision": preview["expected_revision"],
            },
        )
    if preview["changed_role_count"] == 0:
        from shine.comfyops.llm_readiness import llm_readiness

        return {
            "routes": _route_rows(db),
            "route_revision": _route_revision(db),
            "readiness": llm_readiness(db, secrets=secrets),
            "changed_roles": [],
        }
    if not preview["can_apply"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_ROUTE_PREVIEW_BLOCKED",
                "message": (
                    "部分 Agent 仍有未结束任务，或所选模型当前不可用于真实文本任务。"
                    "请按下方提示处理后重新预览。"
                ),
                "preview": preview,
            },
        )

    roles = {item["role"] for item in assignments}
    current = {
        row.role: row for row in db.scalars(select(AgentRoute).where(AgentRoute.role.in_(roles)))
    }
    try:
        for item in assignments:
            role = item["role"]
            profile_id = item["provider_profile_id"]
            route = current.get(role)
            if profile_id is None:
                if route is not None:
                    db.delete(route)
            elif route is None:
                route = AgentRoute(role=role, provider_profile_id=profile_id)
                db.add(route)
            else:
                route.provider_profile_id = profile_id
        changed = [
            {"role": item["role"], "provider_profile_id": item["provider_profile_id"]}
            for item in assignments
            if next((change["action"] for change in preview["changes"]
                     if change["role"] == item["role"]), "unchanged") != "unchanged"
        ]
        add_audit(
            db,
            "user",
            "AGENT_ROUTES_BATCH_UPDATED",
            "agent_routes",
            "text",
            {"updates": changed, "expected_revision": body.expected_revision},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - route table changes are all-or-nothing
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Agent 分配未保存；服务端已回滚本次批量变更。",
        ) from exc

    from shine.comfyops.llm_readiness import llm_readiness

    return {
        "routes": _route_rows(db),
        "route_revision": _route_revision(db),
        "readiness": llm_readiness(db, secrets=secrets),
        "changed_roles": changed,
    }


@router.put("/agent-routes/bulk")
def bulk_route(
    body: AgentRouteBulkUpdate,
    db: DBSession,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> dict[str, Any]:
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
    existing = {
        row.role: row.provider_profile_id
        for row in db.scalars(select(AgentRoute).where(AgentRoute.role.in_(roles)))
    }
    changed_roles = {role for role in roles if existing.get(role) != profile.id}
    pinned_tasks, blocking_tasks = _split_pinned_tasks(_live_tasks_for_roles(db, changed_roles))
    if blocking_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_ROUTE_HAS_IN_FLIGHT_TASKS",
                "message": "受影响角色仍有无法按原模型继续的任务；请先完成或对账，再批量改派。",
                "active_tasks": [_active_task_dict(task) for task in blocking_tasks],
                "pinned_tasks": [_active_task_dict(task) for task in pinned_tasks],
            },
        )
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

    readiness = llm_readiness(db, secrets=secrets)
    return {
        "roles": _route_rows(db),
        "bound_roles": roles,
        "readiness": readiness,
    }


@router.put("/agent-routes", response_model=AgentRoutePublic)
def upsert_route(body: AgentRouteUpdate, db: DBSession) -> AgentRoutePublic:
    profile = db.get(ProviderProfile, body.provider_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Provider profile not found")
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == body.role.value))
    if (route.provider_profile_id if route else None) != profile.id:
        pinned_tasks, blocking_tasks = _split_pinned_tasks(
            _live_tasks_for_roles(db, {body.role.value})
        )
        if blocking_tasks:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LLM_ROUTE_HAS_IN_FLIGHT_TASKS",
                    "message": "该角色仍有无法按原模型继续的任务；请先完成或对账后再改派。",
                    "active_tasks": [_active_task_dict(task) for task in blocking_tasks],
                    "pinned_tasks": [_active_task_dict(task) for task in pinned_tasks],
                },
            )
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
    active_tasks = _live_tasks_for_roles(db, {role})
    if active_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_ROUTE_HAS_IN_FLIGHT_TASKS",
                "message": "该角色仍有排队、运行或待核对任务；请先完成或对账后再移除路由。",
                "active_tasks": [_active_task_dict(task) for task in active_tasks],
            },
        )
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

"""One-shot live validation of the installed renderer and review services.

The setup wizard calls this once after the isolated instance and workflow
profiles exist and before the runtime is activated.  It starts the installed
renderer closed, reads its real ``object_info``, starts the two isolated review
services long enough to pin their protocol, then stops every process.  The
result is a sealed evidence record; it is not model inference and never claims
quality acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from shine.comfy.workflow import graph_hash, validate_against_object_info
from shine.comfyops.managed_recipe import (
    build_renderer_launch,
    build_review_launch,
    load_managed_recipe,
)
from shine.comfyops.models import ComfyInstance
from shine.comfyops.owned_process import OwnedProcessGroup
from shine.comfyops.runtime_configuration import (
    ACTIVE_KEY,
    CANDIDATE_KEY,
    configuration_identity,
    record_service_evidence,
    require_idle_configuration,
    workflow_contract_sha256,
)
from shine.config import Settings
from shine.models import AgentRoute, AppSetting, ProviderProfile, WorkerInstance, WorkflowProfile
from shine.services import add_audit, require_project

REVIEW_PROFILE_NAMES = {
    "VLM": "local-vlm-hash-verified",
    "ASR": "local-asr-hash-verified",
}
REVIEW_ROLES = {
    "VLM": "vlm_reviewer",
    "ASR": "asr_reviewer",
}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _json_get(url: str, *, timeout: float = 30.0, headers: dict | None = None) -> dict:
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.get(url, headers=headers or {})
    if response.status_code != 200:
        raise HTTPException(409, f"SERVICE_VALIDATION_HTTP_{response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(409, "SERVICE_VALIDATION_INVALID_JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(409, "SERVICE_VALIDATION_INVALID_JSON")
    return body


def _wait_json(url: str, *, timeout_s: float, predicate, headers: dict | None = None) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            body = _json_get(url, timeout=10.0, headers=headers)
            if predicate(body):
                return body
            last_error = "SERVICE_VALIDATION_UNEXPECTED_HEALTH"
        except HTTPException as exc:
            last_error = str(exc.detail)
        except (httpx.HTTPError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(1)
    raise HTTPException(409, last_error or "SERVICE_VALIDATION_TIMEOUT")


def _stop_group(group) -> None:
    if group is None:
        return
    try:
        group.terminate(timeout=20)
    except Exception as exc:
        raise HTTPException(409, "SERVICE_VALIDATION_CLEANUP_FAILED") from exc


def _probe_renderer(recipe: dict) -> dict:
    argv, cwd, env, pins, token, port = build_renderer_launch(recipe["renderer"])
    if not _port_free(port):
        raise HTTPException(409, f"SERVICE_VALIDATION_PORT_IN_USE:{port}")
    group = OwnedProcessGroup.launch(
        argv, cwd=cwd, env=env, pinned_files=pins,
        job_name="Local\\ShineHandoff-" + uuid4().hex,
    )
    try:
        object_info = _wait_json(
            f"http://127.0.0.1:{port}/object_info",
            timeout_s=180,
            predicate=lambda body: len(body) >= 1,
        )
        handoff = _json_get(
            f"http://127.0.0.1:{port}/shine-handoff/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        if (handoff.get("status") != "drained"
                or handoff.get("adapter_sha256") != recipe["renderer"]["adapter_sha256"]):
            raise HTTPException(409, "SERVICE_VALIDATION_RENDERER_NOT_CLOSED")
        result = {"object_info": object_info, "port": port}
    except BaseException:
        try:
            _stop_group(group)
        except HTTPException:
            pass
        raise
    _stop_group(group)
    return result


def _review_model(manifest: dict, role: str) -> str:
    if role == "ASR":
        nested = manifest.get("asr")
        if isinstance(nested, dict) and isinstance(nested.get("model_id"), str):
            return nested["model_id"]
    model = manifest.get("model_id")
    if not isinstance(model, str) or not model:
        raise HTTPException(409, "SERVICE_VALIDATION_REVIEW_MODEL_MISSING")
    return model


def _probe_review(role: str, spec: dict) -> dict:
    argv, cwd, env, pins, _token = build_review_launch(spec)
    port = int(spec["port"])
    if not _port_free(port):
        raise HTTPException(409, f"SERVICE_VALIDATION_PORT_IN_USE:{port}")
    manifest = json.loads(Path(spec["manifest"]).read_text(encoding="utf-8"))
    model = _review_model(manifest, role)
    group = OwnedProcessGroup.launch(
        argv, cwd=cwd, env=env, pinned_files=pins,
        job_name="Local\\ShineHandoff-" + uuid4().hex,
    )
    try:
        health = _wait_json(
            f"http://127.0.0.1:{port}/health",
            timeout_s=300,
            predicate=lambda body: body.get("status") == "ok" and body.get("is_mock") is False,
        )
        models = _json_get(f"http://127.0.0.1:{port}/v1/models", timeout=30)
        ids = {item.get("id") for item in (models.get("data") or []) if isinstance(item, dict)}
        capabilities = models.get("capabilities") or {}
        if (model not in ids or models.get("is_mock") is not False
                or capabilities.get("review_response_protocol") != "local-review-json-schema-v1"
                or not isinstance(capabilities.get("review_decoder_sha256"), str)
                or len(capabilities["review_decoder_sha256"]) != 64):
            raise HTTPException(409, f"SERVICE_VALIDATION_REVIEW_IDENTITY_INVALID:{role}")
        result = {
            "role": role,
            "model": model,
            "base_url": f"http://127.0.0.1:{port}/v1",
            "port": port,
            "runtime_sha256": health.get("runtime_sha256"),
            "manifest_sha256": health.get("manifest_sha256"),
            "capabilities": {
                "available": True,
                "is_mock": False,
                "model_listed": True,
                **capabilities,
            },
        }
    except BaseException:
        try:
            _stop_group(group)
        except HTTPException:
            pass
        raise
    _stop_group(group)
    return result


def _workflow_contracts(db: Session, project_id: str) -> tuple[dict, dict]:
    from shine.comfyops import deployment

    files = {}
    for path in deployment.PROFILE_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = raw.get("name")
        if name:
            files[name] = raw
    profiles = db.scalars(select(WorkflowProfile).where(WorkflowProfile.project_id == project_id)).all()
    by_name = {profile.name: profile for profile in profiles}
    contracts = {}
    for name in deployment.load_default_catalog()["workflow_profiles"]:
        profile = by_name.get(name)
        raw = files.get(name)
        if profile is None or raw is None:
            raise HTTPException(409, f"REGISTER_WORKFLOW_PROFILES_FIRST:{name}")
        if (profile.graph_sha256 != graph_hash(raw["graph"])
                or profile.parameter_map != raw["parameter_map"]
                or profile.media_transfers != raw.get("media_transfers", {})
                or profile.required_nodes != raw["required_nodes"]):
            raise HTTPException(409, f"WORKFLOW_PROFILE_CONTENT_CHANGED:{name}")
        contracts[name] = profile
    return files, contracts


def _validate_workflows_against_object_info(contracts: dict, object_info: dict) -> None:
    for name, profile in contracts.items():
        validation = validate_against_object_info(profile.graph, profile.parameter_map, object_info)
        if validation["missing_nodes"] or validation["missing_inputs"]:
            raise HTTPException(409, {
                "code": "LIVE_WORKFLOW_CONTRACT_FAILED",
                "profile": name,
                "missing_nodes": validation["missing_nodes"],
                "missing_inputs": validation["missing_inputs"],
            })


def _worker_online(db: Session, settings: Settings) -> bool:
    from datetime import timedelta

    from shine.models import utcnow

    cutoff = utcnow() - timedelta(seconds=settings.worker_offline_after_seconds)
    return db.scalar(select(WorkerInstance.worker_id).where(
        WorkerInstance.last_heartbeat_at >= cutoff,
        WorkerInstance.status != "stopped",
    ).limit(1)) is not None


def _upsert_reviewer(db: Session, probe: dict) -> tuple[ProviderProfile, AgentRoute]:
    role = probe["role"]
    name = REVIEW_PROFILE_NAMES[role]
    profile = db.scalar(select(ProviderProfile).where(ProviderProfile.name == name))
    if profile is None:
        profile = ProviderProfile(
            name=name,
            kind="openai_compatible",
            base_url=probe["base_url"],
            model=probe["model"],
            timeout_s=600,
            max_retries=0,
            temperature=0.0,
            enabled=True,
            capabilities=probe["capabilities"],
        )
        db.add(profile)
        db.flush()
        add_audit(db, "setup-wizard", "REVIEW_PROVIDER_CREATED", "provider_profile", profile.id,
                  {"role": role, "model": probe["model"], "base_url": probe["base_url"]})
    else:
        # This name is owned by the setup wizard. A stale 4B label from the
        # previous seed script must not survive an 8B installation.
        if profile.base_url != probe["base_url"]:
            raise HTTPException(409, f"REVIEW_PROFILE_ENDPOINT_CONFLICT:{role}")
        profile.kind = "openai_compatible"
        profile.model = probe["model"]
        profile.capabilities = probe["capabilities"]
        profile.enabled = True
        profile.timeout_s = 600
        profile.max_retries = 0
        profile.temperature = 0.0
        add_audit(db, "setup-wizard", "REVIEW_PROVIDER_REBOUND", "provider_profile", profile.id,
                  {"role": role, "model": probe["model"], "base_url": probe["base_url"]})
    route_role = REVIEW_ROLES[role]
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == route_role))
    if route is None:
        route = AgentRoute(role=route_role, provider_profile_id=profile.id)
        db.add(route)
        db.flush()
        add_audit(db, "setup-wizard", "REVIEW_ROUTE_CREATED", "agent_route", route.id,
                  {"role": route_role, "profile_id": profile.id})
    elif route.provider_profile_id != profile.id:
        raise HTTPException(409, f"REVIEW_ROUTE_CONFLICT:{route_role}")
    return profile, route


def validate_services(db: Session, settings: Settings, project_id: str, recipe_sha256: str,
                      *, actor: str) -> dict:
    """Validate installed services and persist binding evidence.

    This endpoint starts only installed local binaries, refuses to run beside an
    active runtime with a live Worker, and never loads H3 or review weights.
    """
    if os.name != "nt":
        raise HTTPException(409, "SERVICE_VALIDATION_WINDOWS_ONLY")
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    if candidate is None or candidate.value.get("project_id") != project_id:
        raise HTTPException(409, "GENERATE_RUNTIME_CONFIGURATION_FIRST")
    if candidate.value.get("identity", {}).get("recipe_sha256") != recipe_sha256:
        raise HTTPException(409, "RUNTIME_CONFIRMATION_HASH_CHANGED")
    identity = configuration_identity(db, settings, candidate.value["identity"]["recipe"])
    if graph_hash(identity) != graph_hash(candidate.value["identity"]):
        raise HTTPException(409, "RUNTIME_CONFIGURATION_CHANGED_REGENERATE")
    require_project(db, project_id)
    require_idle_configuration(db)
    if db.get(AppSetting, ACTIVE_KEY) is not None and _worker_online(db, settings):
        raise HTTPException(409, "STOP_WORKER_BEFORE_SERVICE_VALIDATION")
    instance = db.get(ComfyInstance, identity["instance_id"])
    if instance is None or instance.status != "STOPPED" or instance.payload.get("process"):
        raise HTTPException(409, "STOP_OWNED_INSTANCE_BEFORE_VALIDATION")
    _files, contracts = _workflow_contracts(db, project_id)
    recipe = load_managed_recipe(identity["recipe"], expected_sha256=identity["recipe_sha256"])
    renderer = _probe_renderer(recipe)
    object_info = renderer["object_info"]
    _validate_workflows_against_object_info(contracts, object_info)
    if renderer["port"] != recipe["renderer"].get("port"):
        raise HTTPException(409, "SERVICE_VALIDATION_RENDERER_PORT_CHANGED")

    reviews = {}
    for role, spec in recipe["reviews"].items():
        reviews[role] = _probe_review(role, spec)

    db.execute(text("BEGIN IMMEDIATE"))
    current = db.get(AppSetting, CANDIDATE_KEY)
    if not current or current.value.get("identity", {}).get("recipe_sha256") != recipe_sha256:
        raise HTTPException(409, "RUNTIME_CONFIGURATION_CHANGED_REGENERATE")
    if configuration_identity(db, settings, current.value["identity"]["recipe"]) != identity:
        raise HTTPException(409, "RUNTIME_CONFIGURATION_CHANGED_REGENERATE")
    profiles = {}
    for probe in reviews.values():
        profile, route = _upsert_reviewer(db, probe)
        profiles[probe["role"]] = {
            "id": profile.id,
            "route_id": route.id,
            "model": probe["model"],
            "base_url": probe["base_url"],
            "capabilities_sha256": graph_hash(probe["capabilities"]),
        }
    profiles_inventory = {
        name: {
            "id": profile.id,
            "contract_sha256": workflow_contract_sha256(profile),
        }
        for name, profile in contracts.items()
    }
    object_info_payload = json.dumps(object_info, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    evidence = record_service_evidence(db, identity, {
        "project_id": project_id,
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "node_count": len(object_info),
        "node_inventory_sha256": hashlib.sha256(object_info_payload.encode("utf-8")).hexdigest(),
        "workflow_profiles": profiles_inventory,
        "review_profiles": profiles,
        "review_runtime": {role: probe.get("runtime_sha256") for role, probe in reviews.items()},
        "renderer_port": renderer["port"],
        "services_stopped": True,
    })
    add_audit(db, actor, "SETUP_SERVICES_VALIDATED", "comfy_instance", instance.id, {
        "configuration_sha256": graph_hash(identity),
        "recipe_sha256": recipe_sha256,
        "node_count": len(object_info),
        "workflows": sorted(contracts),
        "review_models": {role: probe["model"] for role, probe in reviews.items()},
        "services_stopped": True,
    }, project_id=project_id)
    db.commit()
    return {
        "status": "READY",
        "configuration_sha256": graph_hash(identity),
        "recipe_sha256": recipe_sha256,
        "node_count": len(object_info),
        "workflow_profiles": sorted(contracts),
        "review_models": {role: probe["model"] for role, probe in reviews.items()},
        "review_runtime": {role: probe.get("runtime_sha256") for role, probe in reviews.items()},
        "services_stopped": True,
        "evidence_sha256": evidence["sha256"],
    }

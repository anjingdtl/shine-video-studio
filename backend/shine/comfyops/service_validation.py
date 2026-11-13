"""One-shot live validation of the installed renderer and optional review services.

The setup wizard calls this once after the isolated instance and workflow
profiles exist and before the runtime is activated.  It starts the installed
renderer closed and reads its real ``object_info``. Review services are probed
only when their resources are already installed; a missing or unavailable
reviewer is recorded as advisory and does not prevent H3 production.
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
from shine.comfyops.mode_capabilities import auto_register_missing_baseline_profiles
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
        if response.status_code == 503:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, str) and detail in {
                "REVIEW_EXECUTOR_IMPORT_FAILED:ImportError", "ISOLATED_SUPERVISOR_SOURCE_CHANGED",
            }:
                raise HTTPException(409, detail)
        raise HTTPException(409, f"SERVICE_VALIDATION_HTTP_{response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(409, "SERVICE_VALIDATION_INVALID_JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(409, "SERVICE_VALIDATION_INVALID_JSON")
    return body


def _wait_json(url: str, *, timeout_s: float, predicate, headers: dict | None = None,
               owned_group=None) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            body = _json_get(url, timeout=10.0, headers=headers)
            if predicate(body):
                return body
            last_error = "SERVICE_VALIDATION_UNEXPECTED_HEALTH"
        except HTTPException as exc:
            if isinstance(exc.detail, str) and exc.detail in {
                "REVIEW_EXECUTOR_IMPORT_FAILED:ImportError", "ISOLATED_SUPERVISOR_SOURCE_CHANGED",
            }:
                raise
            last_error = str(exc.detail)
        except (httpx.HTTPError, OSError) as exc:
            last_error = type(exc).__name__
        if owned_group is not None:
            exit_code = owned_group.exit_code_if_stopped()
            if exit_code is not None:
                raise HTTPException(409, {
                    "code": "SERVICE_VALIDATION_PROCESS_EXITED",
                    "message": (
                        "本机验证服务启动后提前退出，系统已停止等待并清理本次受管进程。"
                        "请查看缺少项或专业日志后再重试。"
                    ),
                    "exit_code": exit_code,
                    "last_connection_error": last_error,
                })
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
            owned_group=group,
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
        except HTTPException as cleanup_error:
            raise cleanup_error
        raise
    _stop_group(group)
    return result


def validate_registered_workflow(db: Session, settings: Settings, profile_id: str) -> dict:
    """Probe one registered graph against a fenced, weight-free renderer startup."""
    from shine.comfyops.runtime_configuration import _current_identity
    from shine.comfyops.scheduler import resource_slot
    from shine.db import make_session_factory

    if os.name != "nt":
        raise HTTPException(409, "SERVICE_VALIDATION_WINDOWS_ONLY")
    profile = db.get(WorkflowProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Workflow profile not found")
    expected_contract = workflow_contract_sha256(profile)
    identity = _current_identity(db, settings)
    active = db.get(AppSetting, ACTIVE_KEY)
    if not active or active.value.get("identity") != identity:
        raise HTTPException(409, "ACTIVE_RUNTIME_CONFIGURATION_CHANGED")
    require_idle_configuration(db)
    db.commit()
    clean = {"confirmed": True}

    def require_clean_stop():
        if not clean["confirmed"]:
            raise RuntimeError("SERVICE_VALIDATION_CLEANUP_FAILED")

    with resource_slot(make_session_factory(db.get_bind()), "WORKFLOW_VALIDATION", 1,
                       release_models=require_clean_stop):
        db.expire_all()
        if _current_identity(db, settings) != identity:
            raise HTTPException(409, "ACTIVE_RUNTIME_CONFIGURATION_CHANGED")
        instance = db.get(ComfyInstance, identity["instance_id"])
        if instance is None or instance.status != "STOPPED" or instance.payload.get("process"):
            raise HTTPException(409, "STOP_OWNED_INSTANCE_BEFORE_VALIDATION")
        recipe = load_managed_recipe(identity["recipe"], expected_sha256=identity["recipe_sha256"])
        try:
            probe = _probe_renderer(recipe)  # returns only after the owned process has stopped
        except HTTPException as exc:
            if "SERVICE_VALIDATION_CLEANUP_FAILED" in str(exc.detail):
                clean["confirmed"] = False
            raise
        db.expire_all()
        profile = db.get(WorkflowProfile, profile_id)
        if profile is None or workflow_contract_sha256(profile) != expected_contract:
            raise HTTPException(409, "WORKFLOW_PROFILE_CHANGED_DURING_VALIDATION")
        validation = validate_against_object_info(profile.graph or {}, profile.parameter_map, probe["object_info"])
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    profile = db.get(WorkflowProfile, profile_id)
    if profile is None or workflow_contract_sha256(profile) != expected_contract:
        raise HTTPException(409, "WORKFLOW_PROFILE_CHANGED_DURING_VALIDATION")
    if _current_identity(db, settings) != identity:
        raise HTTPException(409, "ACTIVE_RUNTIME_CONFIGURATION_CHANGED")
    profile.validated_against = {
        **validation, "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "managed_live_object_info", "generation_verified": False,
        "services_stopped": True,
    }
    add_audit(db, "local-user", "WORKFLOW_PROFILE_VALIDATED", "workflow_profile", profile.id,
              {"missing_nodes": validation["missing_nodes"],
               "missing_input_count": len(validation["missing_inputs"]), "services_stopped": True},
              project_id=profile.project_id)
    db.commit()
    return profile.validated_against


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
            owned_group=group,
        )
        # Import-only readiness: a reviewer whose Python cannot import its
        # executor must not be reported as READY and must not enter the handoff.
        _wait_json(
            f"http://127.0.0.1:{port}/ready",
            timeout_s=300,  # importing torch/transformers can take tens of seconds
            predicate=lambda body: body.get("status") == "ok",
            owned_group=group,
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
        except HTTPException as cleanup_error:
            # A failed optional probe is advisory only after we have proved
            # its owned process stopped. Otherwise it may still hold the GPU
            # needed by the real renderer and must enter reconciliation.
            raise cleanup_error
        raise
    _stop_group(group)
    return result


def _workflow_contracts(db: Session, project_id: str) -> tuple[dict, dict, dict]:
    """Classify baseline templates; drift and gaps are advisory, not a block.

    The mode-level contract replaced the byte-exact startup check: a renderer
    is valid for production when the core H3 node classes of the baseline
    Ref2VA mode are present (see :func:`_validate_core_mode_nodes`).  A
    historical template that drifted from its repository file, or one the
    project never registered, only means "this template needs re-validation";
    it must not lock the whole Ref2VA mode on this machine.
    """
    from shine.comfyops.mode_capabilities import classify_baseline_profiles

    state = classify_baseline_profiles(db, project_id)
    return state["templates"], state["contracts"], {
        "drifted": state["drifted"],
        "missing": state["missing"],
    }


def _validate_core_mode_nodes(object_info: dict) -> None:
    """Refuse READY when the renderer lacks the baseline mode's core nodes."""
    from shine.comfyops.mode_capabilities import core_mode_node_types

    required = core_mode_node_types()
    if not required:
        # No repository template at all means the mode has no audited recipe;
        # an empty pass here would be a fabricated READY.
        raise HTTPException(409, "BASELINE_MODE_TEMPLATES_UNAVAILABLE")
    missing = sorted(name for name in required if name not in object_info)
    if missing:
        raise HTTPException(409, {
            "code": "CORE_MODE_NODES_MISSING",
            "missing_nodes": missing,
        })


def _review_resource_blockers(recipe: dict) -> list[dict]:
    """Find plainly missing review files before starting a renderer or model.

    This inexpensive existence/size check avoids long health timeouts for a
    reviewer whose fixed model package is absent. The isolated reviewer still
    verifies all model and runtime hashes before it can become ready.
    """
    blockers = []
    lock_path = Path(__file__).resolve().parents[3] / "config" / "review-decoder-lock.json"
    try:
        decoder_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        decoder_lock = {"packages": []}
    for role, spec in sorted(recipe.get("reviews", {}).items()):
        try:
            manifest = json.loads(Path(spec["manifest"]).read_text(encoding="utf-8"))
        except (OSError, KeyError, TypeError, ValueError):
            blockers.append({"role": role, "code": "REVIEW_MANIFEST_UNAVAILABLE"})
            continue
        manifests = [manifest]
        if role == "ASR" and isinstance(manifest.get("asr"), dict):
            manifests.append(manifest["asr"])
        missing_by_model = []
        for model_manifest in manifests:
            model_id = model_manifest.get("model_id", "unknown")
            root_value = model_manifest.get("model_root")
            entries = model_manifest.get("files")
            missing = 0
            try:
                root = Path(root_value).resolve(strict=True)
                if not root.is_dir() or not isinstance(entries, list):
                    raise ValueError("MANIFEST_INVALID")
                for entry in entries:
                    relative = Path(entry["path"])
                    target = (root / relative).resolve()
                    if (relative.is_absolute() or ".." in relative.parts
                            or not target.is_relative_to(root) or not target.is_file()
                            or target.stat().st_size != entry.get("bytes")):
                        missing += 1
            except (OSError, KeyError, TypeError, ValueError):
                missing = len(entries) if isinstance(entries, list) and entries else 1
            if missing:
                missing_by_model.append({"model_id": model_id, "missing_files": missing})
        if missing_by_model:
            blockers.append({"role": role, "code": "REVIEW_MODEL_FILES_MISSING", "models": missing_by_model})

        package_root = Path(spec.get("runtime_packages", ""))
        required_roots = []
        if role == "VLM":
            required_roots = [root for package in decoder_lock.get("packages", [])
                              for root in package.get("roots", [])]
        elif role == "ASR":
            required_roots = ["qwen_asr", "qwen_asr-0.0.6.dist-info"]
        missing_roots = [name for name in required_roots if not (package_root / name).is_dir()]
        if missing_roots:
            blockers.append({"role": role, "code": "REVIEW_RUNTIME_PACKAGES_MISSING",
                             "packages": sorted({name.split("-")[0] for name in missing_roots})})
    return blockers


def _advisory_review_status(blockers: list[dict]) -> dict:
    """Summarize optional reviewer readiness without turning it into a gate."""
    by_role: dict[str, list[dict]] = {"VLM": [], "ASR": []}
    for blocker in blockers:
        role = blocker.get("role")
        if role in by_role:
            by_role[role].append(blocker)
    return {
        role: {
            "status": "NOT_READY" if role_blockers else "READY",
            "advisory": True,
            "codes": sorted({str(item.get("code", "REVIEW_RESOURCE_UNAVAILABLE")) for item in role_blockers}),
            "details": role_blockers,
        }
        for role, role_blockers in by_role.items()
    }


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


def _retire_local_reviewer(db: Session, role: str, recipe: dict, status: dict):
    """Drop a setup-owned local route whose executor cannot import.

    Only the exact loopback endpoint generated from this recipe is touched; an
    operator-configured remote route is never disabled by a probe failure.
    """
    profile = db.scalar(select(ProviderProfile).where(ProviderProfile.name == REVIEW_PROFILE_NAMES[role]))
    spec = (recipe.get("reviews") or {}).get(role) or {}
    expected = f"http://127.0.0.1:{spec.get('port')}/v1"
    if profile is None or profile.base_url != expected:
        return
    profile.enabled = False
    route_role = REVIEW_ROLES[role]
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == route_role))
    if route is not None and route.provider_profile_id == profile.id:
        db.delete(route)
        add_audit(db, "setup-wizard", "REVIEW_ROUTE_RETIRED", "agent_route", route.id,
                  {"role": role, "reason": status.get("codes") or status.get("code") or "UNAVAILABLE"})


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
    _files, contracts, template_advisory = _workflow_contracts(db, project_id)
    auto_registered = auto_register_missing_baseline_profiles(db, project_id, actor=actor)
    if auto_registered:
        _files, contracts, template_advisory = _workflow_contracts(db, project_id)
    recipe = load_managed_recipe(identity["recipe"], expected_sha256=identity["recipe_sha256"])
    missing_resources = _review_resource_blockers(recipe)
    advisory_reviews = _advisory_review_status(missing_resources)
    renderer = _probe_renderer(recipe)
    object_info = renderer["object_info"]
    _validate_workflows_against_object_info(contracts, object_info)
    _validate_core_mode_nodes(object_info)
    if renderer["port"] != recipe["renderer"].get("port"):
        raise HTTPException(409, "SERVICE_VALIDATION_RENDERER_PORT_CHANGED")

    reviews = {}
    for role, spec in recipe["reviews"].items():
        if advisory_reviews.get(role, {}).get("status") != "READY":
            continue
        try:
            reviews[role] = _probe_review(role, spec)
        except HTTPException as exc:
            detail = exc.detail
            code = detail.get("code") if isinstance(detail, dict) else detail
            code = str(code) if isinstance(code, str) and code.startswith("SERVICE_VALIDATION_CLEANUP_FAILED") else None
            if code:
                # An unconfirmed process stop can still own the GPU. Keep the
                # existing reconciliation boundary; only a cleanly stopped,
                # optional reviewer failure is advisory.
                raise
            advisory_reviews[role] = {
                "status": "UNAVAILABLE", "advisory": True,
                "codes": ["REVIEW_SERVICE_UNAVAILABLE"],
                "detail_code": (str(detail)[:96] if isinstance(detail, str) else "REVIEW_SERVICE_UNAVAILABLE"),
            }
        except Exception as exc:  # a failed optional probe is evidence, not a production gate
            advisory_reviews[role] = {
                "status": "UNAVAILABLE", "advisory": True,
                "codes": ["REVIEW_SERVICE_UNAVAILABLE"],
                "detail_code": type(exc).__name__,
            }
    for role, probe in reviews.items():
        advisory_reviews[role] = {
            "status": "READY", "advisory": True, "model": probe["model"],
            "runtime_sha256": probe.get("runtime_sha256"), "codes": [],
        }

    db.execute(text("BEGIN IMMEDIATE"))
    current = db.get(AppSetting, CANDIDATE_KEY)
    if not current or current.value.get("identity", {}).get("recipe_sha256") != recipe_sha256:
        raise HTTPException(409, "RUNTIME_CONFIGURATION_CHANGED_REGENERATE")
    if configuration_identity(db, settings, current.value["identity"]["recipe"]) != identity:
        raise HTTPException(409, "RUNTIME_CONFIGURATION_CHANGED_REGENERATE")
    # A reviewer that cannot import its executor must not stay routed into a
    # production handoff; its optional failure is recorded, not retried.
    for role, status in advisory_reviews.items():
        if status.get("status") != "READY":
            _retire_local_reviewer(db, role, recipe, status)
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
    from shine.comfyops.mode_capabilities import core_mode_node_types
    evidence = record_service_evidence(db, identity, {
        "project_id": project_id,
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "node_count": len(object_info),
        "node_inventory_sha256": hashlib.sha256(object_info_payload.encode("utf-8")).hexdigest(),
        "workflow_profiles": profiles_inventory,
        "mode_capabilities": {
            "required_nodes": sorted(core_mode_node_types()),
        },
        "template_advisory": template_advisory,
        "auto_registered_profiles": auto_registered,
        "review_profiles": profiles,
        "advisory_reviews": advisory_reviews,
        "review_runtime": {role: probe.get("runtime_sha256") for role, probe in reviews.items()},
        "renderer_port": renderer["port"],
        "services_stopped": True,
    })
    add_audit(db, actor, "SETUP_SERVICES_VALIDATED", "comfy_instance", instance.id, {
        "configuration_sha256": graph_hash(identity),
        "recipe_sha256": recipe_sha256,
        "node_count": len(object_info),
        "workflows": sorted(contracts),
        "template_advisory": template_advisory,
        "auto_registered_profiles": auto_registered,
        "review_models": {role: probe["model"] for role, probe in reviews.items()},
        "advisory_reviews": advisory_reviews,
        "services_stopped": True,
    }, project_id=project_id)
    db.commit()
    return {
        "status": "READY",
        "configuration_sha256": graph_hash(identity),
        "recipe_sha256": recipe_sha256,
        "node_count": len(object_info),
        "workflow_profiles": sorted(contracts),
        "template_advisory": template_advisory,
        "auto_registered_profiles": auto_registered,
        "review_models": {role: probe["model"] for role, probe in reviews.items()},
        "review_runtime": {role: probe.get("runtime_sha256") for role, probe in reviews.items()},
        "advisory_reviews": advisory_reviews,
        "services_stopped": True,
        "evidence_sha256": evidence["sha256"],
    }

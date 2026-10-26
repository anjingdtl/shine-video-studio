"""Native local media capability identity; never a synthetic ComfyUI graph."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ModelPackage, ResourceLease
from shine.models import AgentRoute, AgentTaskRecord, ProviderProfile
from shine.quality.identity import ExecutorIdentity
from shine.security import is_loopback_url

ROLES = {"VLM_REVIEW": "vlm_reviewer", "ASR": "asr_reviewer"}


class NativeProviderBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = Field(min_length=1)
    kind: str = Field(pattern=r"^(openai_compatible|ollama)$")
    model: str = Field(min_length=1)
    base_url_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_identity: ExecutorIdentity
    model_package_id: str = Field(min_length=1)
    runtime_files: list[str] = Field(min_length=3, max_length=100)

    @model_validator(mode="after")
    def exact_model(self):
        if self.model != self.executor_identity.model_id:
            raise ValueError("EXECUTOR_MODEL_IDENTITY_MISMATCH")
        if len(set(self.runtime_files)) != len(self.runtime_files):
            raise ValueError("DUPLICATE_RUNTIME_FILE")
        files = set(self.runtime_files)
        if not {"config.json", "tokenizer.json"} <= files or not (
            "model.safetensors" in files or "model.safetensors.index.json" in files
        ):
            raise ValueError("INCOMPLETE_MODEL_HASH_MANIFEST")
        if "model.safetensors.index.json" in files and (
            "model.safetensors" in files or not any(name.endswith(".safetensors") for name in files)
        ):
            raise ValueError("MODEL_SHARD_INDEX_INVALID")
        return self

    def provider_snapshot(self):
        return self.model_dump(include={"profile_id", "kind", "model", "base_url_sha256"})


def native_errors(db, payload, *, live=False, require_evidence=False, workflow_hash=None):
    declared = payload.get("native_provider")
    if not declared:
        return []
    if workflow_hash is not None and graph_hash(payload) != workflow_hash:
        return ["NATIVE_WORKFLOW_SNAPSHOT_CHANGED"]
    try:
        binding = NativeProviderBinding.model_validate(declared)
    except ValueError:
        return ["NATIVE_PROVIDER_BINDING_INVALID"]
    errors = []
    category = payload["category"]
    profile = db.get(ProviderProfile, binding.profile_id)
    try:
        url = urlsplit(profile.base_url) if profile else None
    except ValueError:
        url = None
    safe_url = bool(
        url and url.scheme in {"http", "https"} and not (url.username or url.password or url.query or url.fragment)
    )
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == ROLES.get(category, "unsupported")))
    if (
        category not in ROLES
        or not profile
        or not profile.enabled
        or not route
        or route.provider_profile_id != binding.profile_id
        or profile.kind != binding.kind
        or profile.model != binding.model
        or not safe_url
        or not is_loopback_url(profile.base_url)
        or hashlib.sha256(profile.base_url.encode()).hexdigest() != binding.base_url_sha256
    ):
        errors.append("NATIVE_PROVIDER_BINDING_CHANGED")
    package = db.get(ModelPackage, binding.model_package_id)
    if (
        payload.get("model_ids") != [binding.model_package_id]
        or not package
        or package.sha256 != payload.get("package_hashes", {}).get(binding.model_package_id)
        or graph_hash(package.payload) != package.sha256
        or category not in package.payload.get("capabilities", [])
    ):
        errors.append("NATIVE_MODEL_PACKAGE_MISMATCH")
    else:
        files = {f["relative_path"]: f for f in package.payload["files"]}
        if not set(binding.runtime_files) <= set(files):
            errors.append("NATIVE_MODEL_FILES_MISMATCH")
        else:
            actual = sorted(
                [
                    {"path": name, "sha256": files[name]["sha256"], "bytes": files[name]["size"]}
                    for name in binding.runtime_files
                ],
                key=lambda f: f["path"],
            )
            digest = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            identity = binding.executor_identity
            if (
                digest != identity.files_sha256
                or len(actual) != identity.file_count
                or sum(f["bytes"] for f in actual) != identity.bytes
            ):
                errors.append("NATIVE_MODEL_FILES_MISMATCH")
    if category in ROLES:
        from shine.comfyops.model_calls import MODEL_METHODS

        method = "review" if category == "VLM_REVIEW" else "transcribe"
        minimum = MODEL_METHODS[method][1]
        budget = package.payload.get("resource_mb") if package else None
        if (
            type(budget) is not int
            or not minimum <= budget <= 16384
            or payload.get("resource_mb") != budget
            or package.payload.get("resource_measurement") not in {"KNOWN", "ESTIMATED"}
        ):
            errors.append("NATIVE_RESOURCE_BUDGET_MISMATCH")
    if (live or require_evidence) and package and package.status not in {"AVAILABLE", "FILES_VERIFIED_PENDING_RUNTIME"}:
        errors.append("MODEL_NOT_VERIFIED")
    if live and not errors:
        endpoint = profile.base_url.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.get(endpoint + "/identity")
            response.raise_for_status()
            health = response.json()
            actual = {
                "schema_version": "1",
                "runtime_sha256": health.get("runtime_sha256"),
                **health.get("verified_models", {}).get(binding.model, {}),
            }
            if (
                health.get("status") != "ok"
                or health.get("is_mock") is not False
                or ExecutorIdentity.model_validate(actual) != binding.executor_identity
            ):
                errors.append("NATIVE_EXECUTOR_IDENTITY_CHANGED")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            errors.append("NATIVE_EXECUTOR_IDENTITY_UNAVAILABLE")
    if require_evidence:
        proof = payload.get("template_audit", {}).get("native_provider_evidence")
        if not proof or not proof.get("samples"):
            errors.append("NATIVE_EXECUTION_EVIDENCE_MISSING")
        else:
            for sample in proof["samples"]:
                if category == "VLM_REVIEW" and sample.get("style_input_contract") != "seven-field-v1":
                    errors.append("NATIVE_STYLE_INPUT_EVIDENCE_OUTDATED")
                call = db.get(AgentTaskRecord, sample["call_id"])
                if (
                    not valid_call(db, call, category, binding, proof["project_id"], budget_mb=payload["resource_mb"])
                    or call.input_hash != sample["input_hash"]
                    or call.trace.get("result_sha256") != sample["result_sha256"]
                ):
                    errors.append("NATIVE_EXECUTION_EVIDENCE_CHANGED")
    return errors


def valid_call(db, call, category, binding, project_id, *, budget_mb):
    role = {"VLM_REVIEW": "model_call:VLM:review", "ASR": "model_call:ASR:transcribe"}.get(category)
    if (
        not call
        or call.project_id != project_id
        or call.role_id != role
        or call.status != "COMPLETED"
        or call.trace.get("is_mock") is not False
        or call.trace.get("provider_is_mock") is not False
        or call.trace.get("result_is_mock") is not False
        or call.trace.get("review_provider") != binding.provider_snapshot()
        or call.trace.get("executor_identity") != binding.executor_identity.model_dump()
    ):
        return False
    resource_id = call.trace.get("resource_id")
    lease = db.get(ResourceLease, resource_id) if resource_id else None
    return bool(lease and lease.status == "RELEASED" and lease.budget_mb >= budget_mb)


def production_review_binding(db, project_id, job_id, kind, provider, *, live):
    """Bind the actual QC subcall to its job's frozen manifest, including after H3."""
    from fastapi import HTTPException

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.preproduction import current_plan
    from shine.models import Job

    job = db.get(Job, job_id)
    if not job or job.project_id != project_id:
        raise HTTPException(409, "PRODUCTION_REVIEW_JOB_MISMATCH")
    plan = current_plan(db, project_id)
    if not plan and not job.preproduction_manifest_hash:
        return None  # Existing unanalyzed projects keep their legacy contract.
    if job.provider == "mock":
        return None  # Mock jobs never become real evidence.
    manifest = plan.payload.get("manifest") if plan else None
    if not plan or plan.status != "FROZEN" or not manifest or graph_hash(manifest) != job.preproduction_manifest_hash:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    if bool(plan.payload.get("offline")) != bool(manifest.get("offline")):
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    if manifest.get("offline"):
        return None  # A matching explicitly frozen simulation stays Fake.
    category = {"VLM": "VLM_REVIEW", "ASR": "ASR"}[kind]
    from shine.comfyops.models import WorkflowRevision

    matches = []
    for pin in manifest["workflows"]:
        row = db.get(WorkflowRevision, pin["id"])
        if row and row.payload["category"] == category:
            matches.append((pin, row))
    if len(matches) != 1:
        raise HTTPException(409, "PRODUCTION_REVIEW_WORKFLOW_NOT_FROZEN")
    pin, row = matches[0]
    if row.sha256 != pin["hash"] or row.status != "PRODUCTION_APPROVED" or not row.payload.get("native_provider"):
        raise HTTPException(409, "PRODUCTION_REVIEW_WORKFLOW_OUTDATED")
    errors = native_errors(db, row.payload, live=live, require_evidence=True, workflow_hash=row.sha256)
    if errors:
        raise HTTPException(409, "PRODUCTION_REVIEW_PROVIDER_OUTDATED")
    binding = NativeProviderBinding.model_validate(row.payload["native_provider"])
    config = getattr(provider, "config", None)
    actual = (
        {
            "profile_id": config.profile_id,
            "kind": config.kind,
            "model": config.model,
            "base_url_sha256": hashlib.sha256(config.base_url.encode()).hexdigest(),
        }
        if config
        else None
    )
    if actual != binding.provider_snapshot() or provider.is_mock:
        raise HTTPException(409, "PRODUCTION_REVIEW_PROVIDER_OUTDATED")
    from shine.comfyops.scheduler import CURRENT_RESOURCE

    lease_id = CURRENT_RESOURCE.get()
    lease = db.get(ResourceLease, lease_id) if lease_id else None
    if not lease or lease.status != "ACTIVE" or lease.budget_mb < row.payload["resource_mb"]:
        raise HTTPException(409, "PRODUCTION_REVIEW_RESOURCE_BUDGET_MISMATCH")
    return binding.executor_identity.model_dump()

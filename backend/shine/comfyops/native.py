"""Native local media capability identity; never a synthetic ComfyUI graph."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ModelPackage, ResourceLease
from shine.models import AgentRoute, AgentTaskRecord, ProviderProfile
from shine.quality.client_identity import review_client_identity
from shine.quality.identity import ExecutorIdentity
from shine.security import is_loopback_url

ROLES = {"VLM_REVIEW": "vlm_reviewer", "ASR": "asr_reviewer"}


def review_schema_for_binding(binding):
    if binding.review_scope == "basic_character":
        from shine.quality.basic_review import CharacterReviewResponse

        return CharacterReviewResponse.model_json_schema()
    from shine.quality.semantic import vision_review_schema

    return vision_review_schema()


class NativeProviderBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = Field(min_length=1)
    kind: str = Field(pattern=r"^(openai_compatible|ollama)$")
    model: str = Field(min_length=1)
    base_url_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_identity: ExecutorIdentity
    model_package_id: str = Field(min_length=1)
    runtime_files: list[str] = Field(min_length=3, max_length=100)
    response_protocol: Literal["local-review-json-schema-v1"] | None = None
    decoder_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    input_assessment_required: bool = False
    input_evidence_protocol: Literal["text-assessment-compact-v2"] | None = None
    client_review_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    review_scope: Literal["basic_character"] | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        if self.review_scope is None:
            result.pop("review_scope", None)
        return result

    @model_validator(mode="after")
    def exact_model(self):
        if bool(self.response_protocol) != bool(self.decoder_sha256):
            raise ValueError("NATIVE_REVIEW_PROTOCOL_PIN_INCOMPLETE")
        if self.input_assessment_required and not self.response_protocol:
            raise ValueError("NATIVE_INPUT_ASSESSMENT_REQUIRES_STRUCTURED_REVIEW")
        if self.input_assessment_required != bool(self.input_evidence_protocol):
            raise ValueError("NATIVE_INPUT_EVIDENCE_PROTOCOL_PIN_INCOMPLETE")
        if bool(self.response_protocol) != bool(self.client_review_sha256):
            raise ValueError("NATIVE_REVIEW_CLIENT_PIN_INCOMPLETE")
        if self.review_scope and not self.response_protocol:
            raise ValueError("NATIVE_BASIC_REVIEW_REQUIRES_STRUCTURED_OUTPUT")
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
    if binding.response_protocol and category != "VLM_REVIEW":
        errors.append("NATIVE_REVIEW_PROTOCOL_CATEGORY_MISMATCH")
    if binding.review_scope == "basic_character":
        from shine.quality.basic_review import CharacterReviewResponse

        if payload.get("output_schema") != CharacterReviewResponse.model_json_schema():
            errors.append("NATIVE_BASIC_REVIEW_SCHEMA_CHANGED")
    if binding.client_review_sha256:
        try:
            if review_client_identity() != binding.client_review_sha256:
                errors.append("NATIVE_REVIEW_CLIENT_CHANGED")
        except ValueError:
            errors.append("NATIVE_REVIEW_CLIENT_CHANGED")
    profile = db.get(ProviderProfile, binding.profile_id)
    try:
        url = urlsplit(profile.base_url) if profile else None
    except ValueError:
        url = None
    safe_url = bool(
        url and url.scheme in {"http", "https"} and not (url.username or url.password or url.query or url.fragment)
    )
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == ROLES.get(category, "unsupported")))
    if category == "VLM_REVIEW" and profile:
        declared_protocol = (profile.capabilities or {}).get("review_response_protocol")
        if declared_protocol and binding.response_protocol != declared_protocol:
            errors.append("NATIVE_REVIEW_PROTOCOL_UNPINNED")
        if binding.response_protocol and (
            declared_protocol != binding.response_protocol
            or (profile.capabilities or {}).get("review_decoder_sha256") != binding.decoder_sha256
        ):
            errors.append("NATIVE_REVIEW_DECODER_CHANGED")
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
            if binding.response_protocol and (
                (health.get("structured_output") or {}).get("protocol") != binding.response_protocol
                or (health.get("structured_output") or {}).get("decoder_sha256") != binding.decoder_sha256
            ):
                errors.append("NATIVE_REVIEW_DECODER_CHANGED")
            if binding.review_scope == "basic_character":
                from shine.quality.semantic import review_schema_hash

                supported = (health.get("structured_output") or {}).get("supported_schema_sha256", [])
                if (not isinstance(supported, list)
                        or review_schema_hash(review_schema_for_binding(binding)) not in supported):
                    errors.append("NATIVE_BASIC_REVIEW_SCHEMA_UNSUPPORTED")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            errors.append("NATIVE_EXECUTOR_IDENTITY_UNAVAILABLE")
    if require_evidence:
        proof = payload.get("template_audit", {}).get("native_provider_evidence")
        if not proof or not proof.get("samples"):
            errors.append("NATIVE_EXECUTION_EVIDENCE_MISSING")
        else:
            for sample in proof["samples"]:
                if category == "VLM_REVIEW":
                    if binding.review_scope == "basic_character":
                        if sample.get("character_input_contract") != "basic-character-v1":
                            errors.append("NATIVE_CHARACTER_EVIDENCE_OUTDATED")
                    elif sample.get("style_input_contract") != "seven-field-v1":
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
    if bool(call.trace.get("input_assessment_required")) != binding.input_assessment_required:
        return False
    if binding.input_assessment_required and (
        not call.trace.get("independent_input") or not call.trace.get("logical_review_hash")
    ):
        return False
    if call.trace.get("input_evidence_protocol") != binding.input_evidence_protocol:
        return False
    if call.trace.get("client_review_sha256") != binding.client_review_sha256:
        return False
    if binding.input_assessment_required:
        from fastapi import HTTPException

        from shine.quality.input_assessment import evidence_for_call

        try:
            evidence_for_call(db, call)
        except (HTTPException, ValueError, KeyError, TypeError):
            return False
    if binding.response_protocol:
        from shine.quality.semantic import review_schema_hash
        proof = call.trace.get("structured_output") or {}
        if (proof.get("protocol") != binding.response_protocol or proof.get("decoder_sha256") != binding.decoder_sha256
                or proof.get("schema_sha256") != review_schema_hash(review_schema_for_binding(binding))):
            return False
    lease = db.get(ResourceLease, resource_id) if resource_id else None
    return bool(lease and lease.status == "RELEASED" and lease.budget_mb >= budget_mb)


def production_review_binding(db, project_id, job_id, kind, provider, *, live, input_assessment_required=False,
                              input_evidence_protocol=None):
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
        if getattr(provider, "review_scope", None) == "basic_character":
            raise HTTPException(409, "BASIC_REVIEW_REQUIRES_FROZEN_NATIVE_CAPABILITY")
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
    if binding.input_assessment_required != input_assessment_required:
        raise HTTPException(409, "PRODUCTION_REVIEW_INPUT_POLICY_MISMATCH")
    if binding.input_evidence_protocol != input_evidence_protocol:
        raise HTTPException(409, "PRODUCTION_REVIEW_INPUT_PROTOCOL_MISMATCH")
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
    if binding.response_protocol and getattr(provider, "response_protocol", None) != binding.response_protocol:
        raise HTTPException(409, "PRODUCTION_REVIEW_PROTOCOL_OUTDATED")
    if getattr(provider, "review_scope", None) != binding.review_scope:
        raise HTTPException(409, "PRODUCTION_REVIEW_SCOPE_OUTDATED")
    if binding.response_protocol and not live:
        from shine.quality.semantic import review_schema_hash
        proof = getattr(provider, "last_structured_output", None) or {}
        if (proof.get("protocol") != binding.response_protocol or proof.get("decoder_sha256") != binding.decoder_sha256
                or proof.get("schema_sha256") != review_schema_hash(review_schema_for_binding(binding))):
            raise HTTPException(409, "PRODUCTION_REVIEW_STRUCTURED_PROOF_OUTDATED")
    from shine.comfyops.scheduler import CURRENT_RESOURCE

    lease_id = CURRENT_RESOURCE.get()
    lease = db.get(ResourceLease, lease_id) if lease_id else None
    if not lease or lease.status != "ACTIVE" or lease.budget_mb < row.payload["resource_mb"]:
        raise HTTPException(409, "PRODUCTION_REVIEW_RESOURCE_BUDGET_MISMATCH")
    return binding.executor_identity.model_dump()


def frozen_input_policy(db, project_id, *, job_id=None, evidence_present=False):
    """Read only hash-pinned workflow policy; never infer it from a mutable route."""
    from fastapi import HTTPException

    from shine.comfyops.models import WorkflowRevision
    from shine.comfyops.preproduction import current_plan
    from shine.models import Job

    plan = current_plan(db, project_id)
    if not plan:
        return False
    manifest = plan.payload.get("manifest") or {}
    if job_id:
        job = db.get(Job, job_id)
        if not job or job.project_id != project_id:
            raise HTTPException(409, "PRODUCTION_REVIEW_JOB_MISMATCH")
        if job.provider == "mock":
            return False
        if plan.status != "FROZEN" or graph_hash(manifest) != job.preproduction_manifest_hash:
            raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    if manifest.get("offline"):
        return False
    for pin in manifest.get("workflows", []):
        row = db.get(WorkflowRevision, pin["id"])
        if not row or row.sha256 != pin["hash"] or graph_hash(row.payload) != row.sha256:
            raise HTTPException(409, "PREPRODUCTION_OUTDATED")
        native = row.payload.get("native_provider")
        if row.payload.get("category") == "VLM_REVIEW" and native:
            required = native.get("input_assessment_required") is True
            if evidence_present and not required:
                raise HTTPException(409, "PRODUCTION_REVIEW_INPUT_POLICY_MISMATCH")
            if required:
                return True
    return False

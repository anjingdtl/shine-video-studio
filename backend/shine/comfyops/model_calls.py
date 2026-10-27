"""Every inference subcall has durable intent and shared GPU admission.

This proxy is used by API tools and production/recovery QC. It preserves provider
interfaces while the existing AgentTaskRecord/ResourceLease ledgers remain authoritative.
"""

from __future__ import annotations

import gc
import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ResourceLease
from shine.comfyops.scheduler import CURRENT_RESOURCE, resource_slot
from shine.models import AgentTaskRecord, Project, ProjectRuleLock
from shine.services import add_audit

CURRENT_MODEL_CALL = ContextVar("current_model_call", default=None)


def record_external_submission(provider, prompt_id, graph):
    """Commit acknowledgement before polling. Missing acknowledgement remains uncertain."""
    active = CURRENT_MODEL_CALL.get()
    if active is None:
        return
    factory, identity = active
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, identity)
        row.trace = {
            **row.trace,
            "external_provider": provider,
            "external_id": prompt_id,
            "graph_sha256": graph_hash(graph),
        }


IN_FLIGHT = set()
IN_FLIGHT_LOCK = Lock()


MODEL_METHODS = {
    "generate": ("IMAGE", 12000),
    "design": ("TTS", 4000),
    "clone": ("TTS", 4000),
    "prepare_conditioning": ("TTS", 4000),
    "synthesize": ("TTS", 4000),
    "transcribe": ("ASR", 4000),
    "align": ("ALIGNMENT", 4000),
    "review": ("VLM", 8000),
}


def digest_input(value):
    if is_dataclass(value) and not isinstance(value, type):
        return digest_input(asdict(value))
    if isinstance(value, Path):
        if value.is_file():
            with value.open("rb") as stream:
                return {"file_sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        return {"missing_path_sha256": hashlib.sha256(str(value).encode()).hexdigest()}
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, list | tuple):
        return [digest_input(x) for x in value]
    if isinstance(value, dict):
        return {str(k): digest_input(v) for k, v in value.items()}
    return hashlib.sha256(str(value).encode()).hexdigest()


@contextmanager
def model_step(provider, factory, identity):
    release = getattr(provider, "release_models", None)
    try:
        if release:
            release()
        gc.collect()
        yield
    finally:
        try:
            if release:
                release()
            gc.collect()
        except Exception:
            with factory.begin() as db:
                db.get(ResourceLease, identity).status = "RECONCILE_REQUIRED"
            raise


class TrackedProvider:
    def __init__(self, provider, factory, project_id, *, reviewer_kind=None, production_job_id=None):
        self.provider, self.factory, self.project_id = provider, factory, project_id
        self.reviewer_kind = reviewer_kind
        self.production_job_id = production_job_id

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        if name not in MODEL_METHODS:
            return method

        def call(*args, **kwargs):
            kind, budget = MODEL_METHODS[name]
            budget_for = getattr(self.provider, "resource_budget_mb", None)
            if callable(budget_for):
                budget = budget_for(name)
                if type(budget) is not int or not 0 < budget <= 16384:
                    raise HTTPException(409, "MODEL_RESOURCE_BUDGET_INVALID")
            if self.reviewer_kind:
                kind = self.reviewer_kind
            role = "model_call:" + kind + ":" + name
            fingerprint = graph_hash(digest_input([args, kwargs]))
            with self.factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                uncertain = db.scalar(
                    select(AgentTaskRecord).where(
                        AgentTaskRecord.project_id == self.project_id,
                        AgentTaskRecord.role_id == role,
                        AgentTaskRecord.input_hash == fingerprint,
                        AgentTaskRecord.status.in_(["RUNNING", "NEEDS_REVIEW", "RECONCILING"]),
                    )
                )
                if uncertain:
                    raise HTTPException(409, "MODEL_CALL_RECONCILIATION_REQUIRED")
                project = db.get(Project, self.project_id)
                lock = db.get(ProjectRuleLock, self.project_id)
                config = getattr(self.provider, "config", None)
                review_binding = None
                if config and kind in {"VLM", "ASR"}:
                    review_binding = {
                        "profile_id": config.profile_id,
                        "kind": config.kind,
                        "model": config.model,
                        "base_url_sha256": hashlib.sha256(config.base_url.encode()).hexdigest(),
                    }
                from shine.comfyops.installer import process_identity
                from shine.comfyops.tasks import CURRENT_PREPARATION

                task = AgentTaskRecord(
                    project_id=self.project_id,
                    role_id=role,
                    input_hash=fingerprint,
                    style_version_id=project.frozen_style_version_id,
                    asset_manifest_hash=graph_hash({}),
                    rulepack_id=lock.rulepack_id if lock else "PREPRODUCTION_UNFROZEN",
                    rulepack_hash=lock.rulepack_hash if lock else graph_hash({}),
                    model_profile_id="shared-local-model-gate",
                    model_id=type(self.provider).__name__,
                    schema_version="1.3",
                    status="RUNNING",
                    trace={
                        "method": name,
                        "kind": kind,
                        "is_mock": bool(self.provider.is_mock),
                        "provider_is_mock": bool(self.provider.is_mock),
                        "resource_id": CURRENT_RESOURCE.get(),
                        "replay": "NEVER_AUTOMATIC",
                        "parent_task_id": CURRENT_PREPARATION.get(),
                        "owner_pid": os.getpid(),
                        "owner_birth": process_identity(os.getpid()),
                        "review_provider": review_binding,
                    },
                )
                db.add(task)
                db.flush()
                identity = task.id
            try:
                current = CURRENT_RESOURCE.get()
                if current:
                    with self.factory() as db:
                        lease = db.get(ResourceLease, current)
                        if not lease or lease.status != "ACTIVE" or lease.budget_mb < budget:
                            raise HTTPException(409, "RESOURCE_LEASE_LOST_OR_BUDGET_EXCEEDED")
                    context = model_step(self.provider, self.factory, current)
                else:

                    def bind_resource(resource_id):
                        # Persist before preflight unload: even a cleanup failure
                        # must retain the lease identity for reconciliation.
                        with self.factory.begin() as db:
                            row = db.get(AgentTaskRecord, identity)
                            row.trace = {**row.trace, "resource_id": resource_id}

                    context = resource_slot(
                        self.factory,
                        kind,
                        budget,
                        release_models=getattr(self.provider, "release_models", None),
                        on_acquired=bind_resource,
                        wait_seconds=0 if self.provider.is_mock else 300,
                    )
                with context:
                    expected_executor = None
                    if self.production_job_id and kind in {"VLM", "ASR"}:
                        from shine.comfyops.native import production_review_binding

                        with self.factory() as db:
                            expected_executor = production_review_binding(
                                db, self.project_id, self.production_job_id, kind, self.provider, live=True
                            )
                    with self.factory.begin() as db:
                        row = db.get(AgentTaskRecord, identity)
                        row.trace = {
                            **row.trace, "resource_id": CURRENT_RESOURCE.get(), "submitted": True,
                            **({"production_review": {"job_id": self.production_job_id,
                                                      "expected_executor": expected_executor}}
                               if self.production_job_id else {}),
                        }
                    with IN_FLIGHT_LOCK:
                        IN_FLIGHT.add(identity)
                    token = CURRENT_MODEL_CALL.set((self.factory, identity))
                    try:
                        result = method(*args, **kwargs)
                        if expected_executor is not None:
                            # A long render or inference may span a provider restart.
                            # Keep the received digest even if the post-call pin fails.
                            result_mock = (result.get("is_mock") if isinstance(result, dict)
                                           else getattr(result, "is_mock", None))
                            with self.factory.begin() as db:
                                row = db.get(AgentTaskRecord, identity)
                                row.trace = {**row.trace,
                                             "received_result_sha256": graph_hash(digest_input(result)),
                                             "received_executor_identity": getattr(
                                                 self.provider, "last_executor_identity", None),
                                             "result_is_mock": result_mock}
                            with self.factory() as db:
                                current_executor = production_review_binding(
                                    db, self.project_id, self.production_job_id, kind, self.provider, live=False
                                )
                            if (current_executor != expected_executor or result_mock is not False
                                    or getattr(self.provider, "last_executor_identity", None) != expected_executor):
                                raise HTTPException(409, "PRODUCTION_REVIEW_RESPONSE_IDENTITY_MISMATCH")
                    finally:
                        CURRENT_MODEL_CALL.reset(token)
                        with IN_FLIGHT_LOCK:
                            IN_FLIGHT.discard(identity)
            except Exception as exc:
                from shine.quality.semantic import ReceivedReviewError

                with self.factory.begin() as db:
                    row = db.get(AgentTaskRecord, identity)
                    row.status = (
                        "FAILED"
                        if (
                            not row.trace.get("submitted") or (isinstance(exc, HTTPException) and exc.status_code < 500)
                            or isinstance(exc, ReceivedReviewError)
                        )
                        else "NEEDS_REVIEW"
                    )
                    row.error_code = type(exc).__name__
                    if isinstance(exc, ReceivedReviewError):
                        row.trace = {**row.trace, "received_response": {
                            "sha256": exc.response_sha256, "finish_reason": exc.finish_reason,
                            "error_code": str(exc), "retryable": False,
                            **({"validation": exc.validation} if exc.validation is not None else {}),
                        }}
                raise
            with self.factory.begin() as db:
                row = db.get(AgentTaskRecord, identity)
                row.status = "COMPLETED"
                result_mock = result.get("is_mock") if isinstance(result, dict) else getattr(result, "is_mock", None)
                effective_mock = bool(self.provider.is_mock) or bool(result_mock)
                row.trace = {
                    **row.trace,
                    "executor_identity": getattr(self.provider, "last_executor_identity", None),
                    "result_sha256": graph_hash(digest_input(result)),
                    **({"structured_output": self.provider.last_structured_output}
                       if getattr(self.provider, "last_structured_output", None) else {}),
                    "result_is_mock": result_mock,
                    "is_mock": effective_mock,
                }
                add_audit(
                    db,
                    "model-tool",
                    "MODEL_CALL_COMPLETED",
                    "agent_task",
                    identity,
                    {"kind": kind, "is_mock": effective_mock, "result_is_mock": result_mock},
                    project_id=self.project_id,
                )
            return result

        return call

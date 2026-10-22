"""Every inference subcall has durable intent and shared GPU admission.

This proxy is used by API tools and production/recovery QC. It preserves provider
interfaces while the existing AgentTaskRecord/ResourceLease ledgers remain authoritative.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import nullcontext
from contextvars import ContextVar
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
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, list | tuple):
        return [digest_input(x) for x in value]
    if isinstance(value, dict):
        return {str(k): digest_input(v) for k, v in value.items()}
    return hashlib.sha256(str(value).encode()).hexdigest()


class TrackedProvider:
    def __init__(self, provider, factory, project_id, *, reviewer_kind=None):
        self.provider, self.factory, self.project_id = provider, factory, project_id
        self.reviewer_kind = reviewer_kind

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        if name not in MODEL_METHODS:
            return method

        def call(*args, **kwargs):
            kind, budget = MODEL_METHODS[name]
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
                        "resource_id": CURRENT_RESOURCE.get(),
                        "replay": "NEVER_AUTOMATIC",
                        "parent_task_id": CURRENT_PREPARATION.get(),
                        "owner_pid": os.getpid(),
                        "owner_birth": process_identity(os.getpid()),
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
                    context = nullcontext()
                else:
                    context = resource_slot(
                        self.factory, kind, budget, release_models=getattr(self.provider, "release_models", None)
                    )
                with context:
                    with self.factory.begin() as db:
                        row = db.get(AgentTaskRecord, identity)
                        row.trace = {**row.trace, "resource_id": CURRENT_RESOURCE.get(), "submitted": True}
                    with IN_FLIGHT_LOCK:
                        IN_FLIGHT.add(identity)
                    token = CURRENT_MODEL_CALL.set((self.factory, identity))
                    try:
                        result = method(*args, **kwargs)
                    finally:
                        CURRENT_MODEL_CALL.reset(token)
                        with IN_FLIGHT_LOCK:
                            IN_FLIGHT.discard(identity)
            except Exception as exc:
                with self.factory.begin() as db:
                    row = db.get(AgentTaskRecord, identity)
                    row.status = (
                        "FAILED" if isinstance(exc, HTTPException) and exc.status_code < 500 else "NEEDS_REVIEW"
                    )
                    row.error_code = type(exc).__name__
                raise
            with self.factory.begin() as db:
                row = db.get(AgentTaskRecord, identity)
                row.status = "COMPLETED"
                row.trace = {**row.trace, "result_sha256": graph_hash(digest_input(result))}
                add_audit(
                    db,
                    "model-tool",
                    "MODEL_CALL_COMPLETED",
                    "agent_task",
                    identity,
                    {"kind": kind, "is_mock": bool(self.provider.is_mock)},
                    project_id=self.project_id,
                )
            return result

        return call

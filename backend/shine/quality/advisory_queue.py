"""Durable advisory review queue, decoupled from H3 production.

The H3 render is the production path: once a shot's technical media check
passes and its bytes are persisted, the next shot may start.  The local VLM/ASR
reviews are suggestions for the human who follows, so they are queued as their
own durable work items and only executed while no H3 render owns the GPU.

A queued review is bound to the exact artifact bytes, the shot revision and the
attempt it was produced from.  A changed artifact, a newer shot revision or a
stale job supersedes it instead of overwriting a newer review.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfyops.handoff_memory import evaluate_readiness, probe_memory
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ResourceLease
from shine.comfyops.scheduler import ResourceScheduler, resource_slot
from shine.media.artifacts import safe_asset_path
from shine.models import (
    AgentRoute,
    AgentTaskRecord,
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    Project,
    ProviderProfile,
    QCReport,
    QualityGate,
    Shot,
    utcnow,
)
from shine.quality.gates import (
    COVERAGE_FAILED,
    STATUS_UNAVAILABLE,
    QualityPolicy,
    evaluate_gates,
)
from shine.schemas import RenderRequest
from shine.services import add_audit

ROLE_VLM = "advisory_review_vlm"
ROLE_ASR = "advisory_review_asr"
ROLES = (ROLE_VLM, ROLE_ASR)
KIND_ROLE = {"VLM": ROLE_VLM, "ASR": ROLE_ASR}
ROLE_KIND = {ROLE_VLM: "VLM", ROLE_ASR: "ASR"}
H3_ACTIVE_JOB_STATUSES = ("QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING")
H3_ACTIVE_SERIES_STATUSES = ("QUEUED", "CLAIMED", "RUNNING", "PAUSED", "RECONCILING")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _route_identity(db, kind):
    role = "vlm_reviewer" if kind == "VLM" else "asr_reviewer"
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
    if route is None:
        return None
    profile = db.get(ProviderProfile, route.provider_profile_id)
    if profile is None:
        return None
    return {
        "profile_id": profile.id,
        "model": profile.model,
        "kind": profile.kind,
        "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest(),
    }


def requested_kinds(request: RenderRequest) -> list[str]:
    """Which advisory reviewers this shot asked for, independently of availability."""
    kinds: list[str] = []
    if request.self_review_scope == "full":
        kinds.append("VLM")
    elif request.self_review_scope == "basic" and request.shot_spec.characters:
        kinds.append("VLM")
    if request.shot_spec.dialogue and request.self_review_scope in {"basic", "full"}:
        kinds.append("ASR")
    return kinds


def enqueue_advisory_reviews(db, job: Job, attempt: JobAttempt, artifact: MediaArtifact | None,
                             policy: QualityPolicy) -> list[str]:
    """Persist one queue item per requested reviewer for a passed technical render."""
    if artifact is None or not policy.advisory_model_reviews or not artifact.sha256:
        return []
    try:
        request = RenderRequest.model_validate(job.request or {})
    except Exception:
        return []
    kinds = requested_kinds(request)
    if not kinds:
        return []
    shot = db.get(Shot, job.shot_id)
    asset_hash = _digest([item.model_dump(mode="json") for item in request.reference_manifest])
    created = []
    for kind in kinds:
        role = KIND_ROLE[kind]
        input_hash = hashlib.sha256(
            f"{kind}:{artifact.sha256}:{job.shot_revision}:{request.self_review_scope}".encode()
        ).hexdigest()
        existing = db.scalar(
            select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == job.project_id,
                AgentTaskRecord.role_id == role,
                AgentTaskRecord.input_hash == input_hash,
            ).limit(1)
        )
        if existing is not None:
            continue
        identity = _route_identity(db, kind)
        task = AgentTaskRecord(
            project_id=job.project_id,
            role_id=role,
            input_hash=input_hash,
            style_version_id=job.style_version_id,
            asset_manifest_hash=asset_hash,
            rulepack_id=str((job.request or {}).get("rulepack_id") or "ADVISORY_REVIEW"),
            rulepack_hash=str((job.request or {}).get("rulepack_hash") or _digest({"kind": kind})),
            model_profile_id=(identity or {}).get("profile_id") or "UNCONFIGURED",
            model_id=(identity or {}).get("model") or "UNCONFIGURED",
            schema_version="1.3",
            status="QUEUED",
            trace={
                "kind": kind,
                "job_id": job.id,
                "attempt_id": attempt.id,
                "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256,
                "artifact_bytes": artifact.byte_size,
                "shot_id": shot.id if shot else job.shot_id,
                "shot_revision": job.shot_revision,
                "self_review_scope": request.self_review_scope,
                "review_revision": None,
                "enqueued_at": utcnow().isoformat(),
                "route_identity": identity,
                "replay": "NEVER_AUTOMATIC",
                "advisory": True,
            },
        )
        db.add(task)
        db.flush()
        add_audit(
            db,
            "comfy-worker",
            "ADVISORY_REVIEW_QUEUED",
            "agent_task",
            task.id,
            {
                "job_id": job.id,
                "kind": kind,
                "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256,
                "shot_revision": job.shot_revision,
                "model_id": task.model_id,
            },
            project_id=job.project_id,
        )
        created.append(task.id)
    return created


def _h3_busy(factory) -> bool:
    with factory() as db:
        if db.scalar(
            select(ProductionSeries.id).where(ProductionSeries.status.in_(H3_ACTIVE_SERIES_STATUSES)).limit(1)
        ):
            return True
        if db.scalar(
            select(Job.id).where(
                Job.provider == "comfyui",
                Job.status.in_(H3_ACTIVE_JOB_STATUSES),
                Job.stale.is_(False),
            ).limit(1)
        ):
            return True
        return bool(db.scalar(
            select(ResourceLease.id).where(
                ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]),
                ResourceLease.owner.like("h3:%"),
            ).limit(1)
        ))


def reconcile_review_leases(factory, settings) -> int:
    """Release a blocked review lease only after the GPU is proven free again."""
    released = 0
    gpu_uuid = _gpu_uuid(settings)
    with factory.begin() as db:
        rows = db.scalars(
            select(ResourceLease).where(
                ResourceLease.owner.like("review:%"),
                ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]),
            )
        ).all()
    for lease in rows:
        stale_active = lease.status == "ACTIVE" and lease.expires_at <= utcnow()
        if lease.status == "ACTIVE" and not stale_active:
            continue
        if not _physical_memory_ready(gpu_uuid):
            continue
        try:
            with factory.begin() as db:
                current = db.get(ResourceLease, lease.id)
                if current is None or current.owner != lease.owner:
                    continue
                if current.status not in {"ACTIVE", "RECONCILE_REQUIRED"}:
                    continue
                ResourceScheduler().release(db, current.id, current.owner, reconciled=True)
                add_audit(
                    db,
                    "comfy-worker",
                    "ADVISORY_REVIEW_LEASE_RECONCILED",
                    "resource_lease",
                    current.id,
                    {"verified_free_memory": True},
                )
                released += 1
        except HTTPException:
            continue
    return released


def reconcile_running_advisory_reviews(factory, *, timeout_s: int = 900) -> int:
    """An interrupted review is unknown, never blindly replayed."""
    reconciled = 0
    cutoff = utcnow() - timedelta(seconds=timeout_s)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        tasks = db.scalars(
            select(AgentTaskRecord).where(
                AgentTaskRecord.role_id.in_(ROLES),
                AgentTaskRecord.status == "RUNNING",
            )
        ).all()
        for task in tasks:
            started = task.trace.get("started_at") if isinstance(task.trace, dict) else None
            try:
                from datetime import datetime

                started_at = datetime.fromisoformat(started) if started else None
            except ValueError:
                started_at = None
            if started_at is None or started_at > cutoff:
                continue
            task.status = "NEEDS_REVIEW"
            task.error_code = "ADVISORY_REVIEW_OUTCOME_UNKNOWN"
            task.trace = {**(task.trace or {}), "reconciled_at": utcnow().isoformat(), "replay": "NEVER_AUTOMATIC"}
            add_audit(
                db,
                "comfy-worker",
                "ADVISORY_REVIEW_RECONCILED_UNKNOWN",
                "agent_task",
                task.id,
                {"replay": "NEVER_AUTOMATIC"},
                project_id=task.project_id,
            )
            reconciled += 1
    return reconciled


def _gpu_uuid(settings) -> str:
    path = getattr(settings, "managed_recipe_path", None) or os.getenv("SHINE_MANAGED_RECIPE", "").strip()
    if not path:
        return ""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf8"))
        return str((payload.get("renderer") or {}).get("gpu_uuid") or "")
    except (OSError, ValueError, TypeError):
        return ""


def _physical_memory_ready(gpu_uuid: str) -> bool:
    if not gpu_uuid:
        return True
    measured = probe_memory(gpu_uuid, min_ram_mb=4096, min_vram_mb=8000)
    if measured.get("ready") is not True:
        return False
    return evaluate_readiness(
        available_ram_bytes=measured.get("available_ram_bytes"),
        gpu=measured.get("gpu"),
        min_ram_mb=4096,
        min_vram_mb=8000,
        expected_gpu=gpu_uuid,
    )["ready"]


def _claim_next(factory, worker_id: str) -> str | None:
    """Claim one queued review, marking any superseded item first."""
    while True:
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            task = db.scalar(
                select(AgentTaskRecord)
                .where(AgentTaskRecord.role_id.in_(ROLES), AgentTaskRecord.status == "QUEUED")
                .order_by(AgentTaskRecord.created_at, AgentTaskRecord.id)
                .limit(1)
            )
            if task is None:
                return None
            reason = _superseded_reason(db, task)
            if reason:
                task.status = "SUPERSEDED"
                task.error_code = reason
                task.trace = {**(task.trace or {}), "superseded_at": utcnow().isoformat(), "reason": reason}
                add_audit(
                    db,
                    "comfy-worker",
                    "ADVISORY_REVIEW_SUPERSEDED",
                    "agent_task",
                    task.id,
                    {"reason": reason},
                    project_id=task.project_id,
                )
                continue
            task.status = "RUNNING"
            task.error_code = None
            task.trace = {
                **(task.trace or {}),
                "started_at": utcnow().isoformat(),
                "worker_id": worker_id,
                "owner_pid": os.getpid(),
                "owner_birth": process_identity(os.getpid()),
            }
            add_audit(
                db,
                "comfy-worker",
                "ADVISORY_REVIEW_STARTED",
                "agent_task",
                task.id,
                {"kind": (task.trace or {}).get("kind"), "worker_id": worker_id},
                project_id=task.project_id,
            )
            return task.id


def _superseded_reason(db, task: AgentTaskRecord) -> str | None:
    trace = task.trace or {}
    job = db.get(Job, trace.get("job_id")) if trace.get("job_id") else None
    artifact = db.get(MediaArtifact, trace.get("artifact_id")) if trace.get("artifact_id") else None
    shot = db.get(Shot, trace.get("shot_id")) if trace.get("shot_id") else None
    if job is None or artifact is None or shot is None:
        return "ADVISORY_REVIEW_SOURCE_MISSING"
    if job.stale:
        return "ADVISORY_REVIEW_JOB_STALE"
    if job.artifact_id != artifact.id or artifact.job_id != job.id or artifact.sha256 != trace.get("artifact_sha256"):
        return "ADVISORY_REVIEW_ARTIFACT_REPLACED"
    if job.shot_revision != trace.get("shot_revision") or shot.revision != job.shot_revision:
        return "ADVISORY_REVIEW_SHOT_REVISION_CHANGED"
    return None


def _load_context(settings, factory, task_id):
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None or task.status != "RUNNING":
            return None
        trace = dict(task.trace or {})
        job = db.get(Job, trace.get("job_id")) if trace.get("job_id") else None
        artifact = db.get(MediaArtifact, trace.get("artifact_id")) if trace.get("artifact_id") else None
        project = db.get(Project, task.project_id)
        if job is None or artifact is None or project is None:
            context = None
        else:
            path = safe_asset_path(Path(settings.data_dir), artifact.storage_path)
            try:
                if path.stat().st_size != artifact.byte_size:
                    raise ValueError
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != artifact.sha256:
                        raise ValueError
            except (OSError, ValueError):
                _mark_task(factory, task_id, "SUPERSEDED", "ADVISORY_REVIEW_ARTIFACT_CHANGED")
                return None
            from shine.services import project_terminology_hint

            context = {
                "task_id": task_id,
                "kind": trace.get("kind"),
                "role_id": task.role_id,
                "project_id": job.project_id,
                "job_id": job.id,
                "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256,
                "attempt_id": artifact.attempt_id,
                "shot_revision": job.shot_revision,
                "request": RenderRequest.model_validate(job.request or {}),
                "path": path,
                "policy": QualityPolicy.from_dict(project.quality_policy),
                "remote_consent": bool(project.allow_remote_review),
                "terminology": project_terminology_hint(db, project.id),
            }
    if context is None:
        _mark_task(factory, task_id, "UNAVAILABLE", "ADVISORY_REVIEW_SOURCE_MISSING")
    return context


def _mark_task(factory, task_id: str, status: str, error_code: str | None, outcome=None, detail=None):
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        task = db.get(AgentTaskRecord, task_id)
        if task is None or task.status == "SUPERSEDED":
            return
        task.status = status
        task.error_code = error_code[:64] if error_code else None
        task.trace = {
            **(task.trace or {}),
            "finished_at": utcnow().isoformat(),
            "outcome": outcome,
            **({"error_detail": detail[:500]} if detail else {}),
        }
        add_audit(
            db,
            "comfy-worker",
            "ADVISORY_REVIEW_COMPLETED" if status == "COMPLETED" else "ADVISORY_REVIEW_UNAVAILABLE",
            "agent_task",
            task.id,
            {"status": status, "error_code": task.error_code, "render_resubmission": False},
            project_id=task.project_id,
        )


def _review_error_code(kind: str, error: str) -> str:
    prefix = error.split(":", 1)[0]
    if "TIMEOUT" in prefix:
        return "VLM_TIMEOUT" if kind == "VLM" else "ASR_TIMEOUT"
    if "CONTRACT" in prefix or "INVALID_JSON" in prefix or "INCOMPLETE" in prefix:
        return "VLM_RESPONSE_UNUSABLE" if kind == "VLM" else "ASR_RESPONSE_UNUSABLE"
    if kind == "VLM":
        return "VLM_UNAVAILABLE"
    return "ASR_UNAVAILABLE"


def _record_async_gates(factory, context, task_id: str, *, kind: str, gates, fused, transcript,
                        semantic_results, semantic_error, asr_error, error_detail=None):
    job_id, project_id = context["job_id"], context["project_id"]
    artifact_id, artifact_sha256 = context["artifact_id"], context["artifact_sha256"]
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        task = db.get(AgentTaskRecord, task_id)
        if task is None or task.status == "SUPERSEDED":
            return
        if task.trace.get("artifact_sha256") != artifact_sha256:
            task.status = "SUPERSEDED"
            task.error_code = "ADVISORY_REVIEW_ARTIFACT_REPLACED"
            return
        attempt_id = context["attempt_id"]
        if attempt_id is None or db.get(JobAttempt, attempt_id) is None:
            task.status = "UNAVAILABLE"
            task.error_code = "ADVISORY_REVIEW_ATTEMPT_MISSING"
            return
        report = {
            "advisory": True,
            "async": True,
            "review_task_id": task_id,
            "kind": kind,
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            "shot_revision": task.trace.get("shot_revision"),
            "reviewers": [
                {
                    "reviewer": item.reviewer,
                    "model": item.model,
                    "is_mock": item.is_mock,
                    "verdict": item.verdict,
                    "overall_confidence": item.overall_confidence,
                    "finding_codes": sorted({finding.code for finding in item.findings}),
                    "raw_summary": item.raw_summary,
                }
                for item in semantic_results
            ],
            "semantic_error": semantic_error,
            "asr_error": asr_error,
            "transcript": (
                {"text": transcript.text, "is_mock": transcript.is_mock, "model": transcript.model}
                if transcript is not None else None
            ),
            "coverage": fused.coverage,
            "result": fused.result,
        }
        db.add(
            QCReport(
                job_id=job_id,
                attempt_id=attempt_id,
                artifact_id=artifact_id,
                result=fused.result,
                is_mock=any(item.is_mock for item in semantic_results) or bool(transcript and transcript.is_mock),
                reviewer="advisory-review-queue",
                report=report,
            )
        )
        allowed = {"semantic", "style"} if kind == "VLM" else {"audio"}
        for gate in gates:
            if gate.gate_name not in allowed:
                continue
            db.add(
                QualityGate(
                    job_id=job_id,
                    attempt_id=attempt_id,
                    artifact_id=artifact_id,
                    gate_name=gate.gate_name,
                    status=gate.status,
                    required=False,
                    is_mock=gate.is_mock,
                    model=gate.model,
                    confidence=gate.confidence,
                    error_code=(gate.error_code[:64] if gate.error_code else None),
                    coverage=gate.coverage,
                    evidence={
                        **gate.evidence,
                        "advisory": True,
                        "async": True,
                        "review_task_id": task_id,
                        "artifact_sha256": artifact_sha256,
                    },
                )
            )
        task.status = "COMPLETED"
        task.error_code = None
        task.trace = {
            **(task.trace or {}),
            "finished_at": utcnow().isoformat(),
            "outcome": fused.result,
            "artifact_sha256": artifact_sha256,
            "is_mock": any(item.is_mock for item in semantic_results) or bool(transcript and transcript.is_mock),
            "semantic_error": semantic_error,
            "asr_error": asr_error,
            "error_detail": error_detail,
            "render_resubmission": False,
        }
        add_audit(
            db,
            "comfy-worker",
            "ADVISORY_REVIEW_COMPLETED",
            "agent_task",
            task.id,
            {
                "kind": kind,
                "outcome": fused.result,
                "artifact_sha256": artifact_sha256,
                "gates": {gate.gate_name: gate.status for gate in gates if gate.gate_name in allowed},
                "render_resubmission": False,
            },
            project_id=project_id,
        )


def _record_unavailable(factory, context, task_id: str, *, kind: str, code: str, detail: str):
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        task = db.get(AgentTaskRecord, task_id)
        if task is None or task.status == "SUPERSEDED":
            return
        job_id, project_id = context["job_id"], context["project_id"]
        artifact_id, artifact_sha256 = context["artifact_id"], context["artifact_sha256"]
        attempt_id = context["attempt_id"]
        if attempt_id and db.get(JobAttempt, attempt_id) is not None:
            gate_name = "semantic" if kind == "VLM" else "audio"
            db.add(
                QualityGate(
                    job_id=job_id,
                    attempt_id=attempt_id,
                    artifact_id=artifact_id,
                    gate_name=gate_name,
                    status=STATUS_UNAVAILABLE,
                    required=False,
                    is_mock=True,
                    error_code=code[:64],
                    coverage=COVERAGE_FAILED,
                    evidence={
                        "advisory": True,
                        "async": True,
                        "review_task_id": task_id,
                        "detail": detail[:500],
                        "artifact_sha256": artifact_sha256,
                    },
                )
            )
        task.status = "UNAVAILABLE"
        task.error_code = code[:64]
        task.trace = {
            **(task.trace or {}),
            "finished_at": utcnow().isoformat(),
            "outcome": "unavailable",
            "error_detail": detail[:500],
            "render_resubmission": False,
        }
        add_audit(
            db,
            "comfy-worker",
            "ADVISORY_REVIEW_UNAVAILABLE",
            "agent_task",
            task.id,
            {
                "kind": kind,
                "error_code": task.error_code,
                "artifact_sha256": artifact_sha256,
                "render_resubmission": False,
            },
            project_id=project_id,
        )


def _bind_execution_identity(factory, task_id: str, reviewer) -> None:
    """Record the model that really executed, not only the enqueue-time route.

    A reviewer route can be configured (or corrected) after a task was queued;
    the durable task must still carry the identity that produced the evidence.
    """
    config = getattr(reviewer, "config", None)
    model = getattr(config, "model", None) or getattr(reviewer, "name", None)
    base_url = getattr(config, "base_url", None)
    if not model and not base_url:
        return
    try:
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            task = db.get(AgentTaskRecord, task_id)
            if task is None or task.status != "RUNNING":
                return
            if model:
                task.model_id = str(model)[:120]
            trace = dict(task.trace or {})
            trace["resolved_model"] = model
            trace["model_identity_source"] = "reviewer-config"
            if base_url:
                trace["review_base_url_sha256"] = hashlib.sha256(str(base_url).encode()).hexdigest()
            task.trace = trace
    except Exception:  # noqa: BLE001 - identity binding never blocks the review
        return


def _review_one(factory, settings, task_id: str, reviewers, used_kinds: set[str]) -> int:
    context = _load_context(settings, factory, task_id)
    if context is None:
        return 1
    kind = context["kind"]
    reviewer = reviewers.get(kind)
    if kind not in {"VLM", "ASR"}:
        _record_unavailable(factory, context, task_id, kind="VLM", code="ADVISORY_REVIEW_KIND_INVALID",
                            detail="The queued review kind is not supported.")
        return 1
    if reviewer is None:
        _record_unavailable(
            factory, context, task_id, kind=kind,
            code=("VLM_ROUTE_NOT_CONFIGURED" if kind == "VLM" else "ASR_ROUTE_NOT_CONFIGURED"),
            detail="No explicit reviewer route is configured, so this advisory review stays unavailable.",
        )
        return 1
    _bind_execution_identity(factory, task_id, reviewer)
    # A guided shot pins an independent text assessment. The reviewer only
    # accepts evidence rebuilt from that database pin, so the advisory call
    # goes through the same tracked adapter the production path uses instead
    # of handing the raw pin to the model client.
    call_reviewer = reviewer
    if kind == "VLM" and context["request"].style_input_assessment_id:
        from shine.comfyops.model_calls import TrackedProvider

        call_reviewer = TrackedProvider(reviewer, factory, context["project_id"], reviewer_kind="VLM")

    # The queue task itself is the durable call ledger for this advisory
    # review: it is already bound to the artifact, shot revision and provider
    # identity, and it is never replayed after an uncertain outcome.  The
    # production review ledger is intentionally not reused here, because its
    # serial handoff admission would let an advisory timeout block other
    # advisory work and the H3 render path.
    from shine.worker import _collect_comfy_reviews

    used_kinds.add(kind)
    try:
        reviewed = _collect_comfy_reviews(
            request=context["request"],
            settings=settings,
            artifact_path=context["path"],
            digest=context["artifact_sha256"],
            job_id=context["job_id"],
            project_id=context["project_id"],
            remote_consent=context["remote_consent"],
            semantic_reviewer=call_reviewer if kind == "VLM" else None,
            asr_reviewer=call_reviewer if kind == "ASR" else None,
            review_revision=None,
            terminology_hint=context["terminology"],
        )
    except Exception as exc:  # pragma: no cover - defensive
        _record_unavailable(
            factory, context, task_id, kind=kind,
            code=("VLM_UNAVAILABLE" if kind == "VLM" else "ASR_UNAVAILABLE"),
            detail=f"{type(exc).__name__}",
        )
        return 1
    technical, transcript, transcript_findings, asr_error, semantic_results, semantic_error = reviewed
    error = semantic_error if kind == "VLM" else asr_error
    if error:
        _record_unavailable(
            factory, context, task_id, kind=kind,
            code=_review_error_code(kind, error),
            detail=error,
        )
        return 1

    request = context["request"]
    gates, fused = evaluate_gates(
        policy=context["policy"],
        shot_spec=request.shot_spec,
        technical=technical,
        semantic_results=semantic_results,
        semantic_error=semantic_error,
        transcript_findings=transcript_findings,
        asr_error=asr_error,
        transcript_model=transcript.model if transcript else None,
        transcript_is_mock=bool(transcript.is_mock) if transcript else False,
        style_version_id=request.style_version_id,
        style_hash=request.style_hash,
        reference_count=len(request.reference_manifest),
    )
    _record_async_gates(
        factory, context, task_id, kind=kind, gates=gates, fused=fused, transcript=transcript,
        semantic_results=semantic_results, semantic_error=semantic_error, asr_error=asr_error,
    )
    return 1


def _release_callback(reviewers, used_kinds: set[str], gpu_uuid: str):
    errors: list[str] = []

    def release():
        for kind in sorted(used_kinds):
            reviewer = reviewers.get(kind)
            target = getattr(reviewer, "provider", reviewer)
            release_method = getattr(target, "release_models", None)
            if not callable(release_method):
                continue
            try:
                release_method()
            except Exception as exc:  # noqa: BLE001 - the physical check below decides
                errors.append(f"{kind}:{type(exc).__name__}")
        if errors and not _physical_memory_ready(gpu_uuid):
            raise ValueError("ADVISORY_REVIEW_RELEASE_UNCONFIRMED")

    return release, errors


def _defer_task(factory, task_id: str, reason: str) -> None:
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        task = db.get(AgentTaskRecord, task_id)
        if task is None or task.status != "RUNNING":
            return
        task.status = "QUEUED"
        task.error_code = None
        task.trace = {**(task.trace or {}), "deferred_at": utcnow().isoformat(), "deferred_reason": reason[:64]}
        add_audit(
            db, "comfy-worker", "ADVISORY_REVIEW_DEFERRED", "agent_task", task.id,
            {"reason": reason[:64]}, project_id=task.project_id,
        )


def run_pending_advisory_reviews(factory, worker_id: str, *, settings, semantic_reviewer=None,
                                 asr_reviewer=None, limit: int = 3) -> int:
    """Run bounded advisory reviews only while no H3 render owns the GPU."""
    timeout_s = max(600, int(os.getenv("SHINE_ADVISORY_REVIEW_TIMEOUT_S", "900")))
    reconcile_running_advisory_reviews(factory, timeout_s=timeout_s)
    if _h3_busy(factory):
        return 0
    reviewers = {"VLM": semantic_reviewer, "ASR": asr_reviewer}
    gpu_uuid = _gpu_uuid(settings)
    processed = 0
    while processed < limit:
        task_id = _claim_next(factory, worker_id)
        if task_id is None:
            break
        with factory() as db:
            task = db.get(AgentTaskRecord, task_id)
            kind = ROLE_KIND.get(task.role_id, "VLM") if task else "VLM"
        reviewer = reviewers.get(kind)
        budget = 8000
        if reviewer is not None:
            budget_for = getattr(reviewer, "resource_budget_mb", None)
            if callable(budget_for):
                try:
                    candidate = budget_for("review" if kind == "VLM" else "transcribe")
                    if type(candidate) is int and 0 < candidate <= 16384:
                        budget = candidate
                except Exception:  # noqa: BLE001 - keep the conservative default
                    pass
        used_kinds: set[str] = set()
        release, release_errors = _release_callback(reviewers, used_kinds, gpu_uuid)
        lease_id = None
        try:
            with resource_slot(factory, "review", budget, release_models=release) as _lease_id:
                lease_id = _lease_id
                processed += _review_one(factory, settings, task_id, reviewers, used_kinds)
                # Continue the batch only while no H3 work is waiting and the
                # same reviewer process can be reused without reloading weights.
                while processed < limit and not _h3_busy(factory):
                    next_id = _claim_next(factory, worker_id)
                    if next_id is None:
                        break
                    processed += _review_one(factory, settings, next_id, reviewers, used_kinds)
        except HTTPException as exc:
            _defer_task(factory, task_id, str(exc.detail))
            break
        except Exception as exc:  # noqa: BLE001 - a review failure never stops H3
            _defer_task(factory, task_id, type(exc).__name__)
            break
        if release_errors:
            # An advisory reviewer that cannot release its weights is evidence,
            # not a reason to stop the Worker: this write used to pass a null
            # entity id, which aborted the whole tick (and the Worker) with a
            # NOT NULL constraint right after a shot had rendered.
            try:
                with factory.begin() as db:
                    add_audit(
                        db, "comfy-worker", "ADVISORY_REVIEW_RELEASE_FALLBACK", "agent_task",
                        task_id, {"errors": release_errors[:4], "lease_id": lease_id},
                    )
            except Exception:  # noqa: BLE001 - recording the failure must not fail the tick
                pass
        # If the same physical reviewer process is not needed for another
        # queued item, release its weights before returning to H3 production.
        with factory() as db:
            pending = db.scalar(
                select(AgentTaskRecord.id)
                .where(AgentTaskRecord.role_id.in_(ROLES), AgentTaskRecord.status == "QUEUED")
                .limit(1)
            )
        if pending is None:
            break
        if _h3_busy(factory):
            break
    return processed


def advisory_reviews_for_job(db, project_id: str, job_id: str) -> list[dict]:
    rows = db.scalars(
        select(AgentTaskRecord)
        .where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id.in_(ROLES),
            AgentTaskRecord.trace["job_id"].as_string() == job_id,
        )
        .order_by(AgentTaskRecord.created_at)
    ).all()
    return [
        {
            "id": row.id,
            "kind": (row.trace or {}).get("kind"),
            "status": row.status,
            "error_code": row.error_code,
            "model_id": row.model_id,
            "artifact_id": (row.trace or {}).get("artifact_id"),
            "artifact_sha256": (row.trace or {}).get("artifact_sha256"),
            "job_id": (row.trace or {}).get("job_id"),
            "started_at": (row.trace or {}).get("started_at"),
            "finished_at": (row.trace or {}).get("finished_at"),
            "outcome": (row.trace or {}).get("outcome"),
            "is_mock": (row.trace or {}).get("is_mock"),
        }
        for row in rows
        if (row.trace or {}).get("job_id") == job_id
    ]

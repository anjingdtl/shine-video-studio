from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from shine.api_agent import router as agent_router
from shine.api_comfy import router as comfy_router
from shine.api_delivery import router as delivery_router
from shine.api_film import router as film_router
from shine.api_instances import router as instances_router
from shine.api_llm import router as llm_router
from shine.api_operator import Operator
from shine.api_operator import router as operator_router
from shine.api_preparation import Decision
from shine.api_review import router as review_router
from shine.api_setup import router as setup_router
from shine.api_subtitles import router as subtitles_router
from shine.comfyops.runtime_configuration import resolve_runtime
from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.h3.spoken_language import SPOKEN_LANGUAGES, canonical_spoken_language
from shine.media.artifacts import safe_asset_path
from shine.models import (
    AgentArtifact,
    Asset,
    AuditEvent,
    Job,
    JobAttempt,
    MediaArtifact,
    ProductionSeries,
    Project,
    ProjectTerminology,
    QCReport,
    QualityGate,
    RepairRecord,
    Shot,
    StyleVersion,
    WorkerInstance,
    utcnow,
)
from shine.quality.advisory_queue import advisory_reviews_for_job
from shine.quality.gates import DEFAULT_PRIORITY_THRESHOLDS, HARD_RULES, QualityPolicy
from shine.quality.repair import RepairEngine
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import (
    AssetMetadataUpdate,
    AuditEventItem,
    AuditPage,
    CreateJob,
    CreateProductionSeries,
    CreateProject,
    CreateShot,
    FusedQCResult,
    HardFailure,
    ProjectSettings,
    QualityPolicyUpdate,
    RenderProvider,
    RepairPlan,
    ReplaceBindings,
    ShotSpec,
    StyleBible,
    StyleFreezeRequest,
    SystemStatus,
    TerminologyBody,
    WorkerStatus,
)
from shine.security import (
    is_loopback_peer,
    probe_upload,
    read_verified_upload,
    sha256_bytes,
    validate_asset_role,
    validate_tags,
)
from shine.services import (
    add_audit,
    create_render_job,
    create_shot_record,
    freeze_style,
    make_render_request,
    require_project,
    require_shot,
    revise_shot_record,
    set_shot_bindings,
    store_asset_file,
    transition,
    update_style_draft,
)

logger = logging.getLogger("shine")


def _session(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DBSession = Annotated[Session, Depends(_session)]


def _canonical_language(value: str) -> str:
    """The project language in the one form the H3 prompt may use.

    The prompt is written in English and states the spoken language in English,
    so the language is normalised here.  A name written in another script is
    refused with a clear message instead of silently breaking the rule pack
    later, when the job is created.
    """
    canonical = canonical_spoken_language(value)
    if canonical is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Project language must be written in English so it can be stated in the H3 prompt; "
                f"allowed values are {', '.join(SPOKEN_LANGUAGES)}"
            ),
        )
    return canonical


def _project_data(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "language": project.language,
        "created_at": project.created_at,
        "frozen_style_version_id": project.frozen_style_version_id,
        "style_draft": project.style_draft,
        "allow_remote_review": project.allow_remote_review,
        "auto_repair_enabled": project.auto_repair_enabled,
        "quality_policy": project.quality_policy,
    }


def _asset_data(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "filename": asset.filename,
        "kind": asset.kind,
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "byte_size": asset.byte_size,
        "role": asset.role,
        "entity_id": asset.entity_id,
        "tags": asset.tags or [],
        "duration_s": asset.duration_s,
        "width": asset.width,
        "height": asset.height,
        "derived_from_asset_id": asset.derived_from_asset_id,
        "provenance": asset.provenance,
        "approved": asset.approved,
        "locked": asset.locked,
        "approved_at": asset.approved_at,
        "locked_at": asset.locked_at,
        "created_at": asset.created_at,
    }


def _style_data(style: StyleVersion) -> dict[str, Any]:
    return {
        "id": style.id,
        "project_id": style.project_id,
        "version": style.version,
        "snapshot": style.snapshot,
        "sha256": style.sha256,
        "approved_by": style.approved_by,
        "approval_note": style.approval_note,
        "frozen_at": style.frozen_at,
    }


def _terminology_data(row: ProjectTerminology) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "revision": row.revision,
        "terms": row.terms,
        "notes": row.notes,
        "sha256": row.sha256,
        "source": row.source,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _shot_data(db: Session, shot: Shot) -> dict[str, Any]:
    bindings = []
    asset_ids = {row.asset_id for row in shot.bindings}
    assets = (
        {asset.id: asset for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()}
        if asset_ids
        else {}
    )
    for row in shot.bindings:
        asset = assets.get(row.asset_id)
        bindings.append(
            {
                "id": row.id,
                "asset_id": row.asset_id,
                "filename": asset.filename if asset else "",
                "kind": asset.kind if asset else "",
                "use_as": row.use_as,
                "role": row.role,
                "frame_role": row.frame_role,
                "ordinal": row.ordinal,
                "sha256": asset.sha256 if asset else "",
            }
        )
    return {
        "id": shot.id,
        "project_id": shot.project_id,
        "name": shot.name,
        "revision": shot.revision,
        "spec": shot.spec,
        "bindings": bindings,
        "created_at": shot.created_at,
    }


def _acceptance_status(db: Session, job: Job) -> dict[str, Any]:
    """Separate production completion from final human/model acceptance.

    A PASSED production job may still carry advisory review findings.  It is
    only "accepted" when the latest final gate really passed; exposing the two
    states explicitly prevents a pending review from being reported as a clean
    five-gate pass.
    """
    if job.status == "FAILED":
        return {"status": "rejected", "final_gate": None,
                "note": "The production attempt failed; it cannot be accepted."}
    if job.status in {"REPAIR_PENDING", "NEEDS_REVIEW"}:
        return {"status": "pending_review", "final_gate": None,
                "note": "The production attempt is not final-passed."}
    if job.status != "PASSED":
        return {"status": "not_run", "final_gate": None,
                "note": "The production attempt has not completed."}
    if job.provider == "mock" or job.quality_verification == "mock_verified":
        return {"status": "mock", "final_gate": None,
                "note": "Mock evidence is not film acceptance."}
    final = db.scalar(
        select(QualityGate)
        .where(QualityGate.job_id == job.id, QualityGate.gate_name == "final")
        .order_by(QualityGate.created_at.desc(), literal_column("quality_gates.rowid").desc())
        .limit(1)
    )
    if final is None:
        return {"status": "not_run", "final_gate": None,
                "note": "No final gate was recorded for this production attempt."}
    if final.status == "passed" and not final.is_mock:
        return {"status": "accepted", "final_gate": final.status,
                "note": "The latest final gate passed."}
    return {"status": "pending_review", "final_gate": final.status,
            "note": "The production attempt is complete, but the final acceptance gate is still pending."}


def _job_data(db: Session, job: Job, *, include_prompt: bool = False) -> dict[str, Any]:
    attempts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no)).all()
    shot = db.get(Shot, job.shot_id)
    repairs = db.scalars(
        select(RepairRecord).where(RepairRecord.job_id == job.id).order_by(RepairRecord.created_at)
    ).all()
    data: dict[str, Any] = {
        "id": job.id,
        "project_id": job.project_id,
        "shot_id": job.shot_id,
        "shot_name": shot.name if shot else "",
        "shot_revision": job.shot_revision,
        "status": job.status,
        "state_version": job.state_version,
        "preproduction_manifest_hash": job.preproduction_manifest_hash,
        "provider": job.provider,
        "simulated": job.provider == "mock",
        "video_created": bool(job.artifact_id),
        "paused": job.paused,
        "stale": job.stale,
        "failure_code": job.failure_code,
        "provider_job_id": job.provider_job_id,
        "semantic_retries": job.semantic_retries,
        "max_semantic_retries": job.max_semantic_retries,
        "request_revision": job.request_revision,
        "quality_verification": job.quality_verification,
        **{f"acceptance_{key}": value for key, value in _acceptance_status(db, job).items()},
        "style_version_id": job.style_version_id,
        "generation_mode": job.generation_mode,
        "workflow_profile_id": job.workflow_profile_id,
        "compiled_prompt_sha256": job.compiled_prompt_sha256,
        "artifact_id": job.artifact_id,
        "last_qc_result": job.last_qc_result,
        "last_repair_plan": job.last_repair_plan,
        "advisory_reviews": advisory_reviews_for_job(db, job.project_id, job.id),
        "request": {
            "idempotency_key": job.request.get("idempotency_key"),
            "shot_id": job.request.get("shot_id"),
            "shot_revision": job.request.get("shot_revision"),
            "style_version_id": job.request.get("style_version_id"),
            "style_hash": job.request.get("style_hash"),
            "generation_mode": job.request.get("generation_mode"),
            "provider": job.request.get("provider"),
            "reference_manifest": job.request.get("reference_manifest", []),
            "compiled_prompt_sha256": job.request.get("prompt_sha256"),
            "compiler_version": job.request.get("compiler_version"),
            "rule_version": job.request.get("rule_version"),
            "basic_review_resolution": job.request.get("basic_review_resolution"),
            "self_review_scope": job.request.get("self_review_scope", "full"),
        },
        "attempts": [
            {
                "id": attempt.id,
                "attempt_no": attempt.attempt_no,
                "status": attempt.status,
                "provider_job_id": attempt.provider_job_id,
                "failure_code": attempt.failure_code,
                "submit_intent_at": attempt.submit_intent_at,
                "submit_ack_at": attempt.submit_ack_at,
                "request_fingerprint": attempt.request_fingerprint,
                "attempt_kind": attempt.attempt_kind,
                "parent_attempt_id": attempt.parent_attempt_id,
                "prompt_sha256": attempt.prompt_sha256,
                "parameter_sha256": attempt.parameter_sha256,
                "manifest_sha256": attempt.manifest_sha256,
                "prompt_graph_sha256": attempt.prompt_graph_sha256,
                "repair_reason": attempt.repair_reason,
                "progress": attempt.progress,
                "output_sha256": attempt.output_sha256,
                "output_bytes": attempt.output_bytes,
                "reconciliation_state": attempt.reconciliation_state,
                "reconciled_at": attempt.reconciled_at,
            }
            for attempt in attempts
        ],
        "repairs": [
            {
                "id": record.id,
                "action": record.action,
                "root_cause": record.root_cause,
                "changes": record.changes,
                "outcome": record.outcome,
                "created_at": record.created_at,
            }
            for record in repairs
        ],
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if include_prompt:
        data["compiled_prompt"] = job.request.get("compiled_prompt")
        data["compiled_prompt_sections"] = job.request.get("compiled_prompt_sections", {})
    return data


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _worker_status(instance: WorkerInstance, *, now: datetime, offline_after: int) -> WorkerStatus:
    online = instance.last_heartbeat_at >= now - timedelta(seconds=offline_after) and instance.status != "stopped"
    return WorkerStatus(
        worker_id=instance.worker_id,
        online=online,
        version=instance.version,
        pid=instance.pid,
        status=instance.status,
        current_job_id=instance.current_job_id,
        current_phase=instance.current_phase,
        last_heartbeat_at=instance.last_heartbeat_at,
        last_error=instance.last_error,
        processed_count=instance.processed_count,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    app.state.engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    database_url: str | None = None,
    asset_root: Path | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(database_url or settings.database_url)
    session_factory = make_session_factory(engine)
    actual_asset_root = asset_root or settings.data_dir
    actual_asset_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Shine 视频工作台 API",
        version="0.2.0",
        description=(
            "本地制片工作台。Mock 流程只模拟调度与 QC，不生成视频；"
            "真实 MiniMax-H3 渲染必须通过本机 ComfyUI 并完成真机验收。"
        ),
        lifespan=_lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.asset_root = actual_asset_root
    app.state.mock_llm_responder = None

    app.include_router(llm_router)
    app.include_router(agent_router)
    app.include_router(comfy_router)
    app.include_router(film_router)
    app.include_router(review_router)
    app.include_router(delivery_router)
    app.include_router(subtitles_router)
    app.include_router(operator_router)
    app.include_router(instances_router)
    app.include_router(setup_router)
    from shine.api_preparation import router as preparation_router

    app.include_router(preparation_router)

    @app.middleware("http")
    async def _loopback_only(request: Request, call_next):
        """Refuse non-loopback clients until the API has real authentication.

        The ordinary project API is unauthenticated, so it may only answer a
        client on this machine. Binding the socket to loopback is not enough:
        the application itself refuses a request that arrives from anywhere
        else. The in-process test transport has no peer address and is allowed.
        """
        if not settings.allow_non_loopback_api:
            client = request.client
            host = (client.host if client is not None else "") or ""
            if host and host != "testclient" and not is_loopback_peer(host):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={"detail": "The Shine API only answers clients on this machine."},
                )
        return await call_next(request)

    @app.get("/health")
    def health(db: DBSession) -> dict[str, str | bool]:
        from shine.comfyops.runtime_configuration import runtime_report
        runtime = runtime_report(db, settings)
        return {
            "status": "ok",
            "service": "shine-api",
            "render_provider": runtime['render_provider'],
            # Cheap configured-state flag; the live connection check lives in
            # /api/v1/system/status so health stays answerable without I/O.
            "h3_connected": False,
            "connection_probe": "not_run_use_system_status",
        }

    def _h3_connected(effective) -> bool:
        """True only when the configured real renderer answers right now."""
        if effective is None or effective.render_provider != "comfyui":
            return False
        try:
            from shine.comfy.client import ComfyUIClient

            client = ComfyUIClient(effective.comfyui_base_url, timeout_s=2.0, trust_env=effective.allow_remote_comfyui)
            client.system_stats()
            return True
        except Exception:
            return False

    @app.get("/api/v1/system/status", response_model=SystemStatus)
    def system_status(db: DBSession) -> SystemStatus:
        runtime_error = None
        try:
            effective = resolve_runtime(db, settings)
        except HTTPException as exc:
            effective, runtime_error = None, str(exc.detail)
        now = utcnow()
        instances = db.scalars(select(WorkerInstance).order_by(WorkerInstance.started_at)).all()
        workers = [
            _worker_status(instance, now=now, offline_after=settings.worker_offline_after_seconds)
            for instance in instances
        ]
        pending = db.scalar(
            select(func.count(Job.id)).where(Job.status.in_(["PLANNED", "WAITING_ASSET", "READY", "QUEUED"]))
        )
        running = db.scalar(select(func.count(Job.id)).where(Job.status.in_(["SUBMITTING", "RUNNING", "QC_PENDING"])))
        failed = db.scalar(select(func.count(Job.id)).where(Job.status == "FAILED"))
        blocked = db.scalar(select(func.count(Job.id)).where(Job.status == "NEEDS_REVIEW"))
        last_error = next((worker.last_error for worker in reversed(workers) if worker.last_error), None)
        worker_online = any(worker.online for worker in workers)
        h3_connected = _h3_connected(effective)
        from shine.production_mode import summarize_production_mode
        return SystemStatus(
            worker_online=worker_online,
            mock_provider_available=True,
            h3_connected=h3_connected,
            render_mode=effective.render_provider if effective else 'unavailable',
            pending_jobs=pending or 0,
            running_jobs=running or 0,
            failed_jobs=failed or 0,
            blocked_jobs=blocked or 0,
            workers=workers,
            last_error=runtime_error or last_error,
            production_mode=summarize_production_mode(
                db, settings,
                effective=effective, runtime_error=runtime_error,
                worker_online=worker_online, renderer_online=h3_connected,
            ),
        )

    @app.get("/api/v1/workers", response_model=list[WorkerStatus])
    def list_workers(db: DBSession) -> list[WorkerStatus]:
        now = utcnow()
        return [
            _worker_status(instance, now=now, offline_after=settings.worker_offline_after_seconds)
            for instance in db.scalars(select(WorkerInstance).order_by(WorkerInstance.started_at)).all()
        ]

    @app.get("/api/v1/projects")
    def list_projects(db: DBSession) -> list[dict[str, Any]]:
        projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return jsonable_encoder([_project_data(project) for project in projects])

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: CreateProject, db: DBSession) -> dict[str, Any]:
        project = Project(title=body.title.strip(), description=body.description,
                          language=_canonical_language(body.language))
        if not project.title:
            raise HTTPException(status_code=422, detail="Project title cannot be blank")
        db.add(project)
        db.flush()
        add_audit(db, "user", "PROJECT_CREATED", "project", project.id, project_id=project.id)
        db.commit()
        db.refresh(project)
        logger.info("project_created id=%s", project.id)
        return jsonable_encoder(_project_data(project))

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, db: DBSession) -> dict[str, Any]:
        return jsonable_encoder(_project_data(require_project(db, project_id)))

    @app.patch("/api/v1/projects/{project_id}/settings")
    def update_project_settings(project_id: str, body: ProjectSettings, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if "language" in updates:
            updates["language"] = _canonical_language(updates["language"])
        for key, value in updates.items():
            setattr(project, key, value)
        add_audit(
            db,
            "user",
            "PROJECT_SETTINGS_UPDATED",
            "project",
            project.id,
            {"fields": sorted(updates)},
            project_id=project.id,
        )
        db.commit()
        db.refresh(project)
        return jsonable_encoder(_project_data(project))

    @app.post("/api/v1/projects/{project_id}/assets", status_code=201)
    async def upload_asset(
        project_id: str,
        db: DBSession,
        asset_kind: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        role: Annotated[str | None, Form()] = None,
        entity_id: Annotated[str | None, Form()] = None,
        tags: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        project = require_project(db, project_id)
        filename, content, content_type = await read_verified_upload(file, asset_kind, settings.max_upload_bytes)
        digest = sha256_bytes(content)
        from shine.comfyops.models import CandidateAsset

        known_candidates = db.scalars(
            select(CandidateAsset).where(CandidateAsset.payload["content_sha256"].as_string() == digest)
        ).all()
        if any(
            c.project_id == project_id or c.payload.get("provenance", {}).get("is_mock", False)
            for c in known_candidates
        ):
            raise HTTPException(409, "CANDIDATE_REQUIRES_AUTHENTICATED_PROMOTION")
        duplicate = db.scalar(select(Asset).where(Asset.project_id == project.id, Asset.sha256 == digest))
        if duplicate:
            return jsonable_encoder(_asset_data(duplicate))
        media = probe_upload(asset_kind, content, content_type)
        storage_key = store_asset_file(actual_asset_root, digest, content)
        asset = Asset(
            project_id=project.id,
            filename=filename,
            kind=asset_kind,
            content_type=content_type,
            sha256=digest,
            storage_path=storage_key,
            byte_size=len(content),
            role=validate_asset_role(role),
            entity_id=(entity_id or "").strip()[:80] or None,
            tags=validate_tags([part.strip() for part in (tags or "").split(",") if part.strip()]),
            duration_s=media.duration_s,
            width=media.width,
            height=media.height,
            approved=False,
            locked=False,
        )
        db.add(asset)
        db.flush()
        add_audit(
            db,
            "user",
            "ASSET_UPLOADED",
            "asset",
            asset.id,
            {
                "kind": asset.kind,
                "role": asset.role,
                "sha256": digest,
                "byte_size": len(content),
                "duration_s": media.duration_s,
            },
            project_id=project.id,
        )
        db.commit()
        db.refresh(asset)
        logger.info("asset_uploaded id=%s project_id=%s", asset.id, project.id)
        return jsonable_encoder(_asset_data(asset))

    @app.get("/api/v1/projects/{project_id}/assets")
    def list_assets(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        assets = db.scalars(select(Asset).where(Asset.project_id == project_id).order_by(Asset.created_at)).all()
        return jsonable_encoder([_asset_data(asset) for asset in assets])

    @app.patch("/api/v1/assets/{asset_id}/metadata")
    def update_asset_metadata(asset_id: str, body: AssetMetadataUpdate, db: DBSession) -> dict[str, Any]:
        asset = db.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if (asset.provenance or {}).get("candidate_id"):
            raise HTTPException(409, "PROMOTED_ASSET_IMMUTABLE_CREATE_NEW_VERSION")
        updates = body.model_dump(exclude_unset=True)
        if "role" in updates:
            asset.role = validate_asset_role(updates["role"])
        if "entity_id" in updates:
            asset.entity_id = (updates["entity_id"] or "").strip()[:80] or None
        if "tags" in updates and updates["tags"] is not None:
            asset.tags = validate_tags(updates["tags"])
        add_audit(
            db,
            "user",
            "ASSET_METADATA_UPDATED",
            "asset",
            asset.id,
            {"fields": sorted(updates)},
            project_id=asset.project_id,
        )
        db.commit()
        db.refresh(asset)
        return jsonable_encoder(_asset_data(asset))

    def decide_asset(asset_id, body, actor, db, *, locking=False):
        from shine.api_preparation import locked
        from shine.comfyops.assets import approve_master

        locked(db)
        asset = db.get(Asset, asset_id)
        if not asset:
            raise HTTPException(404, "Asset not found")
        approve_master(db, app.state.settings, asset, actor, body, locking=locking, asset_root=actual_asset_root)
        db.commit()
        return jsonable_encoder(_asset_data(asset))

    @app.post("/api/v1/assets/{asset_id}/approve")
    def approve_asset(asset_id: str, body: Decision, actor: Operator, db: DBSession) -> dict[str, Any]:
        return decide_asset(asset_id, body, actor, db)

    @app.post("/api/v1/assets/{asset_id}/lock")
    def lock_asset(asset_id: str, body: Decision, actor: Operator, db: DBSession) -> dict[str, Any]:
        return decide_asset(asset_id, body, actor, db, locking=True)

    @app.put("/api/v1/projects/{project_id}/style/draft")
    def save_style_draft(project_id: str, body: StyleBible, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        update_style_draft(db, project, body)
        db.commit()
        return {"project_id": project.id, "style_draft": project.style_draft, "frozen": False}

    @app.post("/api/v1/projects/{project_id}/style/freeze", status_code=201)
    def freeze_project_style(
        project_id: str,
        request: Request,
        db: DBSession,
        body: StyleFreezeRequest | None = None,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from shine.api_operator import operator
        from shine.comfyops import approval
        from shine.comfyops.preproduction import current_plan

        require_project(db, project_id)
        protected = bool(current_plan(db, project_id)) or bool(body and body.grant_id)
        actor = "user"
        if protected:
            actor = operator(request, request.app.state.settings, request.headers.get("authorization", ""))
            if not body or not body.grant_id or not body.snapshot:
                raise HTTPException(409, "STYLE_APPROVAL_GRANT_REQUIRED")
            if body.expected_style is None:
                raise HTTPException(409, "STYLE_APPROVAL_CONTENT_REQUIRED")
        if protected or (body and body.expected_style is not None):
            # Compare the displayed content and consume its grant under the same
            # write lock as freezing. A grant alone only binds the server draft
            # at issuance, which may already differ from the approving page.
            db.rollback()
            db.execute(text("BEGIN IMMEDIATE"))
            project = require_project(db, project_id)
            if body and body.expected_style is not None:
                if project.style_draft != body.expected_style.model_dump(mode="json"):
                    raise HTTPException(409, "STYLE_APPROVAL_CONTENT_CHANGED")
        if protected:
            approval.consume(db, actor, "style_freeze", require_project(db, project_id), body.grant_id, body.snapshot)
        project = require_project(db, project_id)
        style, impact = freeze_style(db, project, approval_note=(body.approval_note if body else ""), actor=actor)
        db.commit()
        db.refresh(style)
        data = jsonable_encoder(_style_data(style))
        data["impact"] = jsonable_encoder(impact)
        return data

    @app.get("/api/v1/projects/{project_id}/styles")
    def list_styles(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        styles = db.scalars(
            select(StyleVersion).where(StyleVersion.project_id == project_id).order_by(StyleVersion.version)
        ).all()
        return jsonable_encoder([_style_data(style) for style in styles])

    @app.get("/api/v1/projects/{project_id}/terminology")
    def list_terminology(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        """Every revision of the script's terminology, newest first."""
        require_project(db, project_id)
        rows = db.scalars(
            select(ProjectTerminology)
            .where(ProjectTerminology.project_id == project_id)
            .order_by(ProjectTerminology.revision.desc())
        ).all()
        return jsonable_encoder([_terminology_data(row) for row in rows])

    @app.post("/api/v1/projects/{project_id}/terminology")
    def create_terminology(project_id: str, body: TerminologyBody, db: DBSession) -> dict[str, Any]:
        """Store a new revision of the script's terminology.

        The table is handed to the ASR model before it transcribes, so a proper
        name is read with the characters the script uses.  It never rewrites a
        transcript, and an earlier revision is never changed.
        """
        from shine.services import save_project_terminology

        project = require_project(db, project_id)
        row = save_project_terminology(
            db, project, terms=body.terms, notes=body.notes, created_by=body.created_by or "operator",
            source=body.source or "human",
        )
        add_audit(db, body.created_by or "operator", "PROJECT_TERMINOLOGY_SAVED", "project_terminology",
                  row.id, {"revision": row.revision, "terms": len(row.terms or [])}, project_id=project_id)
        db.commit()
        db.refresh(row)
        return jsonable_encoder(_terminology_data(row))

    @app.get("/api/v1/projects/{project_id}/style/impact")
    def style_impact(project_id: str, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        if not project.frozen_style_version_id:
            raise HTTPException(status_code=409, detail="No frozen style version exists")
        shots = db.scalars(select(Shot).where(Shot.project_id == project_id)).all()
        jobs = db.scalars(
            select(Job).where(
                Job.project_id == project_id,
                Job.style_version_id != project.frozen_style_version_id,
            )
        ).all()
        return {
            "current_style_version_id": project.frozen_style_version_id,
            "superseded_shot_ids": sorted(
                shot.id for shot in shots if shot.spec.get("style_version_id") != project.frozen_style_version_id
            ),
            "unstarted_superseded_job_ids": sorted(
                job.id for job in jobs if job.status in {"PLANNED", "READY", "QUEUED", "WAITING_ASSET"}
            ),
        }

    @app.post("/api/v1/projects/{project_id}/shots", status_code=201)
    def create_shot(project_id: str, body: CreateShot, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        shot = create_shot_record(
            db,
            project,
            name=body.name.strip() or "未命名镜头",
            spec=body.spec,
            bindings=body.bindings or None,
        )
        db.commit()
        db.refresh(shot)
        return jsonable_encoder(_shot_data(db, shot))

    @app.get("/api/v1/projects/{project_id}/shots")
    def list_shots(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        shots = db.scalars(select(Shot).where(Shot.project_id == project_id).order_by(Shot.created_at)).all()
        return jsonable_encoder([_shot_data(db, shot) for shot in shots])

    @app.post("/api/v1/shots/{shot_id}/revise")
    def revise_shot(shot_id: str, body: ShotSpec, db: DBSession) -> dict[str, Any]:
        shot = db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        project = require_project(db, shot.project_id)
        result = revise_shot_record(db, project, shot, spec=body)
        db.commit()
        return jsonable_encoder(result)

    @app.get("/api/v1/shots/{shot_id}/bindings")
    def get_bindings(shot_id: str, db: DBSession) -> list[dict[str, Any]]:
        shot = db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        return jsonable_encoder(_shot_data(db, shot)["bindings"])

    @app.put("/api/v1/shots/{shot_id}/bindings")
    def replace_bindings(shot_id: str, body: ReplaceBindings, db: DBSession) -> list[dict[str, Any]]:
        shot = db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        project = require_project(db, shot.project_id)
        set_shot_bindings(db, project, shot, body.bindings)
        db.commit()
        db.refresh(shot)
        return jsonable_encoder(_shot_data(db, shot)["bindings"])

    @app.post("/api/v1/shots/{shot_id}/compile")
    def compile_shot_prompt(shot_id: str, db: DBSession) -> dict[str, Any]:
        shot = db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        project = require_project(db, shot.project_id)
        preview = CreateJob(shot_id=shot.id)
        request, _assets, compiler_meta = make_render_request(db, project, shot, preview)
        db.rollback()
        return {
            "shot_id": shot.id,
            "shot_revision": shot.revision,
            "mode": request.generation_mode.value,
            "compiled_prompt": request.compiled_prompt,
            "sections": request.compiled_prompt_sections,
            "prompt_sha256": request.prompt_sha256,
            "compiler_version": request.compiler_version,
            "rule_version": request.rule_version,
            "labels": compiler_meta.get("labels", {}),
            "warnings": compiler_meta.get("warnings", []),
            "reference_manifest": jsonable_encoder(request.reference_manifest),
            "style_version_id": request.style_version_id,
            "style_hash": request.style_hash,
        }

    @app.post("/api/v1/projects/{project_id}/jobs", status_code=201)
    def create_job(project_id: str, body: CreateJob, request: Request, db: DBSession) -> dict[str, Any]:
        from shine.api_preparation import locked

        locked(db)
        project = require_project(db, project_id)
        shot = require_shot(db, project_id, body.shot_id)
        job, compiler_meta = create_render_job(
            db, project, shot, body,
            settings=resolve_runtime(db, request.app.state.settings),
            offline=request.app.state.settings.offline_simulation,
        )
        db.commit()
        db.refresh(job)
        logger.info(
            "job_created id=%s project_id=%s provider=%s status=%s",
            job.id,
            project_id,
            job.provider,
            job.status,
        )
        return jsonable_encoder(_job_data(db, job))

    @app.get("/api/v1/projects/{project_id}/jobs")
    def list_jobs(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        jobs = db.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())).all()
        return jsonable_encoder([_job_data(db, job) for job in jobs])

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str, db: DBSession) -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return jsonable_encoder(_job_data(db, job, include_prompt=True))

    @app.post("/api/v1/projects/{project_id}/production-series", status_code=201)
    def create_production_series(
        project_id: str, body: CreateProductionSeries, request: Request, db: DBSession
    ) -> dict[str, Any]:
        """Queue one ordered, unattended real-H3 production run.

        The resident Worker claims the series and drives every shot of it
        behind one managed renderer.  The person in the browser never has to
        run a command for the run to continue.
        """
        from shine.api_preparation import locked
        from shine.comfyops.production_series import create_series, series_public

        locked(db)
        project = require_project(db, project_id)
        if body.provider != RenderProvider.COMFYUI:
            raise HTTPException(422, "A production series needs a real H3 provider")
        shots = [require_shot(db, project_id, shot_id) for shot_id in body.shot_ids]
        job_body = CreateJob(
            shot_id=shots[0].id,
            mock_scenario=body.mock_scenario,
            provider=body.provider,
            generation_mode=body.generation_mode,
            workflow_profile_id=body.workflow_profile_id,
            self_review_scope=body.self_review_scope,
            basic_review_resolution=body.basic_review_resolution,
        )
        series, job = create_series(
            db, project, shots, body=job_body,
            settings=resolve_runtime(db, request.app.state.settings),
            offline=request.app.state.settings.offline_simulation,
        )
        if body.edit_intent_artifact_id:
            artifact = db.get(AgentArtifact, body.edit_intent_artifact_id)
            if (
                not artifact
                or artifact.project_id != project.id
                or artifact.kind != "EditIntentPlan"
                or artifact.stale
            ):
                db.rollback()
                raise HTTPException(
                    422, "EDIT_INTENT_ARTIFACT_INVALID"
                )
            series.edit_intent_artifact_id = artifact.id
            add_audit(
                db, "user", "SERIES_EDIT_INTENT_BOUND", "production_series", series.id,
                {"artifact_id": artifact.id, "sha256": artifact.sha256},
                project_id=project.id,
            )
        add_audit(
            db, "user", "PRODUCTION_SERIES_CREATED", "production_series", series.id,
            {"shot_count": len(shots), "first_job_id": job.id,
             "workflow_profile_id": series.workflow_profile_id},
            project_id=project.id,
        )
        db.commit()
        db.refresh(series)
        return jsonable_encoder(series_public(db, series))

    @app.get("/api/v1/projects/{project_id}/production-series")
    def list_production_series(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        from shine.comfyops.production_series import series_public

        require_project(db, project_id)
        rows = db.scalars(
            select(ProductionSeries)
            .where(ProductionSeries.project_id == project_id)
            .order_by(ProductionSeries.created_at.desc())
        ).all()
        return jsonable_encoder([series_public(db, row) for row in rows])

    @app.get("/api/v1/production-series/{series_id}")
    def get_production_series(series_id: str, db: DBSession) -> dict[str, Any]:
        from shine.comfyops.production_series import series_public

        series = db.get(ProductionSeries, series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Production series not found")
        return jsonable_encoder(series_public(db, series))

    @app.post("/api/v1/production-series/{series_id}/cancel")
    def cancel_production_series(series_id: str, db: DBSession) -> dict[str, Any]:
        from shine.comfyops.production_series import cancel_series_in_session, series_public

        series = db.get(ProductionSeries, series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Production series not found")
        cancel_series_in_session(db, series_id)
        add_audit(db, "user", "PRODUCTION_SERIES_CANCELLED", "production_series", series_id,
                  {}, project_id=series.project_id)
        db.commit()
        db.refresh(series)
        return jsonable_encoder(series_public(db, series))

    @app.post("/api/v1/production-series/{series_id}/pause")
    def pause_production_series(series_id: str, db: DBSession) -> dict[str, Any]:
        from shine.comfyops.production_series import series_public

        series = db.get(ProductionSeries, series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Production series not found")
        if series.status not in {"QUEUED", "RUNNING"}:
            raise HTTPException(409, "This production series cannot be paused")
        series.status = "PAUSE_REQUESTED" if series.status == "RUNNING" else "PAUSED"
        series.state_version += 1
        add_audit(db, "user", "PRODUCTION_SERIES_PAUSED", "production_series", series_id,
                  {}, project_id=series.project_id)
        db.commit()
        db.refresh(series)
        return jsonable_encoder(series_public(db, series))

    @app.post("/api/v1/production-series/{series_id}/resume")
    def resume_production_series(series_id: str, db: DBSession) -> dict[str, Any]:
        from shine.comfyops.production_series import series_public, utcnow

        series = db.get(ProductionSeries, series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Production series not found")
        if series.status not in {"PAUSED", "PAUSE_REQUESTED"}:
            raise HTTPException(409, "This production series is not paused")
        # A series whose owner still holds an unexpired lease is still alive:
        # its renderer may be running.  Handing it to another worker would let
        # two workers decide the same series, so the owner keeps it and simply
        # continues.
        owner_is_alive = bool(
            series.worker_id
            and series.lease_expires_at is not None
            and series.lease_expires_at > utcnow()
        )
        if owner_is_alive:
            series.status = "RUNNING"
        else:
            series.status = "QUEUED"
            series.worker_id = None
            series.lease_expires_at = None
        series.state_version += 1
        add_audit(db, "user", "PRODUCTION_SERIES_RESUMED", "production_series", series_id,
                  {"kept_owner": owner_is_alive}, project_id=series.project_id)
        db.commit()
        db.refresh(series)
        return jsonable_encoder(series_public(db, series))

    @app.post("/api/v1/jobs/{job_id}/pause")
    def pause_job(job_id: str, db: DBSession) -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"PLANNED", "READY", "QUEUED"}:
            raise HTTPException(status_code=409, detail="Only unsubmitted jobs can be paused")
        job.paused = True
        add_audit(db, "user", "JOB_PAUSED", "job", job.id, project_id=job.project_id)
        db.commit()
        return jsonable_encoder(_job_data(db, job))

    @app.post("/api/v1/jobs/{job_id}/resume")
    def resume_job(job_id: str, db: DBSession) -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.stale or job.status not in {"PLANNED", "READY", "QUEUED"}:
            raise HTTPException(status_code=409, detail="This job cannot be resumed")
        job.paused = False
        add_audit(db, "user", "JOB_RESUMED", "job", job.id, project_id=job.project_id)
        db.commit()
        return jsonable_encoder(_job_data(db, job))

    @app.post("/api/v1/jobs/{job_id}/repair")
    def repair_job(job_id: str, plan: RepairPlan, db: DBSession, mock_scenario: str = "success") -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "REPAIR_PENDING":
            raise HTTPException(status_code=409, detail="A failed attempt must be diagnosed before repair")
        if job.semantic_retries >= min(job.max_semantic_retries, plan.max_additional_attempts):
            raise HTTPException(status_code=409, detail="Semantic repair budget is exhausted")
        if plan.human_review_required or plan.action.value == "wait_for_human":
            job.last_repair_plan = plan.model_dump(mode="json")
            transition(job, "NEEDS_REVIEW")
            job.failure_code = "HUMAN_REVIEW_REQUIRED"
            db.add(
                RepairRecord(
                    job_id=job.id,
                    attempt_id=None,
                    action=plan.action.value,
                    root_cause=plan.root_cause,
                    changes=list(plan.changes),
                    outcome="escalated",
                )
            )
            add_audit(
                db,
                "user",
                "REPAIR_ESCALATED",
                "job",
                job.id,
                {"action": plan.action.value, "category": plan.error_category},
                project_id=job.project_id,
            )
            db.commit()
            return jsonable_encoder(_job_data(db, job))
        if mock_scenario not in {"success", "delay", "fail", "uncertain", "duplicate"}:
            raise HTTPException(status_code=422, detail="Unknown mock scenario")
        job.semantic_retries += 1
        job.last_repair_plan = plan.model_dump(mode="json")
        job.mock_scenario = mock_scenario
        job.failure_code = None
        transition(job, "READY")
        transition(job, "QUEUED")
        db.add(
            RepairRecord(
                job_id=job.id,
                attempt_id=None,
                action=plan.action.value,
                root_cause=plan.root_cause,
                changes=list(plan.changes),
                outcome="manual_queued",
            )
        )
        add_audit(
            db,
            "user",
            "MOCK_REPAIR_QUEUED",
            "job",
            job.id,
            {"semantic_retry": job.semantic_retries, "action": plan.action.value},
            project_id=job.project_id,
        )
        db.commit()
        return jsonable_encoder(_job_data(db, job))

    @app.post("/api/v1/jobs/{job_id}/repair/suggest")
    def suggest_repair(job_id: str, db: DBSession) -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        report = db.scalar(select(QCReport).where(QCReport.job_id == job.id).order_by(QCReport.created_at.desc()))
        hard_failures: list[HardFailure] = []
        result = "needs_review"
        confidence = 0.5
        if report:
            raw = report.report or {}
            hard_failures = [HardFailure.model_validate(item) for item in raw.get("hard_failures", [])][:20]
            result = report.result
            confidence = float(raw.get("review_confidence", 0.5))
        fused = FusedQCResult(
            result=result,
            hard_failures=hard_failures,
            review_confidence=confidence,
            human_review_required=result != "passed",
            is_mock=bool(report.is_mock) if report else True,
            coverage={},
        )
        project = require_project(db, job.project_id)
        decision = RepairEngine().plan(
            fused,
            retries_left=max(0, job.max_semantic_retries - job.semantic_retries),
            auto_repair_enabled=project.auto_repair_enabled,
        )
        add_audit(
            db,
            "agent",
            "REPAIR_PLAN_SUGGESTED",
            "job",
            job.id,
            {"action": decision.plan.action.value, "category": decision.error_category},
            project_id=job.project_id,
        )
        db.commit()
        return jsonable_encoder(
            {
                "job_id": job.id,
                "plan": decision.plan.model_dump(mode="json"),
                "auto_apply": decision.auto_apply,
                "error_category": decision.error_category,
            }
        )

    @app.get("/api/v1/jobs/{job_id}/qc")
    def get_job_qc(job_id: str, db: DBSession) -> list[dict[str, Any]]:
        if not db.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        reports = db.scalars(select(QCReport).where(QCReport.job_id == job_id).order_by(QCReport.created_at)).all()
        return jsonable_encoder(
            [
                {
                    "id": report.id,
                    "job_id": report.job_id,
                    "attempt_id": report.attempt_id,
                    "artifact_id": report.artifact_id,
                    "result": report.result,
                    "is_mock": report.is_mock,
                    "reviewer": report.reviewer,
                    "report": report.report,
                    "created_at": report.created_at,
                }
                for report in reports
            ]
        )

    @app.get("/api/v1/jobs/{job_id}/artifacts")
    def list_job_artifacts(job_id: str, db: DBSession) -> list[dict[str, Any]]:
        if not db.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        rows = db.scalars(select(MediaArtifact).where(MediaArtifact.job_id == job_id)).all()
        return jsonable_encoder(
            [
                {
                    "id": row.id,
                    "job_id": row.job_id,
                    "artifact_kind": row.artifact_kind,
                    "sha256": row.sha256,
                    "byte_size": row.byte_size,
                    "content_type": row.content_type,
                    "is_mock": row.is_mock,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )

    @app.post("/api/v1/artifacts/{artifact_id}/technical-qc")
    def run_artifact_technical_qc(artifact_id: str, db: DBSession) -> dict[str, Any]:
        artifact = db.get(MediaArtifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if not artifact.job_id:
            raise HTTPException(status_code=409, detail="Artifact is not attached to a job")
        job = db.get(Job, artifact.job_id)
        if not job:
            raise HTTPException(status_code=409, detail="Artifact job no longer exists")
        attempt = db.get(JobAttempt, artifact.attempt_id) if artifact.attempt_id else None
        if attempt is None:
            raise HTTPException(status_code=409, detail="Artifact has no attempt record for QC")
        path = safe_asset_path(actual_asset_root, artifact.storage_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is missing on disk")
        spec = ShotSpec.model_validate(job.request.get("shot_spec", {}))
        result = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
            path,
            expectations=TechnicalExpectations(
                duration_s=request_duration(job),
                expect_audio=bool(spec.dialogue),
            ),
        )
        db.add(
            QCReport(
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_id=artifact.id,
                result=result.result,
                is_mock=False,
                reviewer="technical-qc",
                report=result.model_dump(mode="json"),
            )
        )
        job.last_qc_result = result.result
        add_audit(
            db,
            "user",
            "TECHNICAL_QC_RUN",
            "job",
            job.id,
            {"artifact_id": artifact.id, "result": result.result, "tool": result.tool},
            project_id=job.project_id,
        )
        db.commit()
        return jsonable_encoder(result.model_dump(mode="json"))

    @app.get("/api/v1/projects/{project_id}/quality-policy")
    def get_quality_policy(project_id: str, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        return {
            "project_id": project.id,
            "policy": QualityPolicy.from_dict(project.quality_policy).to_dict(),
            "configured": project.quality_policy is not None,
        }

    @app.put("/api/v1/projects/{project_id}/quality-policy")
    def update_quality_policy(project_id: str, body: QualityPolicyUpdate, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        payload = body.model_dump()
        if not payload.get("priority_thresholds"):
            payload["priority_thresholds"] = dict(DEFAULT_PRIORITY_THRESHOLDS)
        if not payload.get("hard_rules"):
            payload["hard_rules"] = list(HARD_RULES)
        project.quality_policy = payload
        add_audit(
            db,
            "user",
            "QUALITY_POLICY_UPDATED",
            "project",
            project.id,
            {
                "require_semantic_review": payload["require_semantic_review"],
                "require_audio_review": payload["require_audio_review"],
                "require_style_review": payload["require_style_review"],
                "min_confidence": payload["min_confidence"],
                "accept_mock_review": payload["accept_mock_review"],
                "allow_unverified_technical": payload["allow_unverified_technical"],
                "priority_thresholds": payload["priority_thresholds"],
            },
            project_id=project.id,
        )
        db.commit()
        db.refresh(project)
        return {
            "project_id": project.id,
            "policy": QualityPolicy.from_dict(project.quality_policy).to_dict(),
            "configured": True,
        }

    @app.get("/api/v1/jobs/{job_id}/quality-gates")
    def get_job_quality_gates(job_id: str, db: DBSession) -> list[dict[str, Any]]:
        if not db.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        rows = db.scalars(
            select(QualityGate)
            .where(QualityGate.job_id == job_id)
            .order_by(QualityGate.created_at, literal_column("quality_gates.rowid"))
        ).all()
        return jsonable_encoder(
            [
                {
                    "id": row.id,
                    "job_id": row.job_id,
                    "attempt_id": row.attempt_id,
                    "artifact_id": row.artifact_id,
                    "gate_name": row.gate_name,
                    "status": row.status,
                    "required": row.required,
                    "is_mock": row.is_mock,
                    "model": row.model,
                    "confidence": row.confidence,
                    "error_code": row.error_code,
                    "coverage": row.coverage,
                    "evidence": row.evidence,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )

    @app.get("/api/v1/projects/{project_id}/events", response_model=AuditPage)
    def list_events(
        project_id: str,
        db: DBSession,
        entity_type: str | None = Query(default=None, max_length=32),
        entity_id: str | None = Query(default=None, max_length=36),
        event_type: str | None = Query(default=None, max_length=64),
        actor: str | None = Query(default=None, max_length=32),
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> AuditPage:
        require_project(db, project_id)
        filters = [AuditEvent.project_id == project_id]
        if entity_type:
            filters.append(AuditEvent.entity_type == entity_type)
        if entity_id:
            filters.append(AuditEvent.entity_id == entity_id)
        if event_type:
            filters.append(AuditEvent.event_type == event_type)
        if actor:
            filters.append(AuditEvent.actor == actor)
        if since:
            filters.append(AuditEvent.created_at >= _naive_utc(since))
        if until:
            filters.append(AuditEvent.created_at <= _naive_utc(until))
        total = db.scalar(select(func.count(AuditEvent.id)).where(*filters)) or 0
        rows = db.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc(), literal_column("audit_events.rowid").desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return AuditPage(
            items=[
                AuditEventItem(
                    id=row.id,
                    project_id=row.project_id,
                    actor=row.actor,
                    event_type=row.event_type,
                    entity_type=row.entity_type,
                    entity_id=row.entity_id,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    return app


def request_duration(job: Job) -> float | None:
    value = job.request.get("expected_duration_s")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


app = create_app()

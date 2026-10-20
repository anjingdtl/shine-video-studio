from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.models import (
    Asset,
    AuditEvent,
    Job,
    JobAttempt,
    JobDependency,
    Project,
    QCReport,
    Shot,
    StyleVersion,
)
from shine.schemas import CreateJob, CreateProject, CreateShot, RepairPlan, ShotSpec, StyleBible
from shine.security import read_verified_upload, sha256_bytes
from shine.services import (
    add_audit,
    dependencies_for_new_job,
    freeze_style,
    make_render_request,
    mark_stale_descendants,
    require_project,
    store_asset_file,
    transition,
    update_style_draft,
    validate_shot_style,
)

logger = logging.getLogger("shine")


def _session(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DBSession = Annotated[Session, Depends(_session)]


def _project_data(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "created_at": project.created_at,
        "frozen_style_version_id": project.frozen_style_version_id,
        "style_draft": project.style_draft,
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
        "approved": asset.approved,
        "locked": asset.locked,
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
        "frozen_at": style.frozen_at,
    }


def _job_data(db: Session, job: Job) -> dict[str, Any]:
    attempts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no)).all()
    shot = db.get(Shot, job.shot_id)
    return {
        "id": job.id,
        "project_id": job.project_id,
        "shot_id": job.shot_id,
        "shot_name": shot.name if shot else "",
        "shot_revision": job.shot_revision,
        "status": job.status,
        "provider": job.provider,
        "simulated": job.provider == "mock",
        "video_created": False,
        "paused": job.paused,
        "stale": job.stale,
        "failure_code": job.failure_code,
        "provider_job_id": job.provider_job_id,
        "semantic_retries": job.semantic_retries,
        "max_semantic_retries": job.max_semantic_retries,
        "style_version_id": job.style_version_id,
        "request": job.request,
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
            }
            for attempt in attempts
        ],
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


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
        version="0.1.0",
        description="本地制片工作台。当前任务执行仅提供明确标注的 Mock 流程，不生成视频。",
        lifespan=_lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.asset_root = actual_asset_root

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "service": "shine-api",
            "render_provider": "mock-only",
            "h3_connected": False,
        }

    @app.get("/api/v1/projects")
    def list_projects(db: DBSession) -> list[dict[str, Any]]:
        projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return jsonable_encoder([_project_data(project) for project in projects])

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: CreateProject, db: DBSession) -> dict[str, Any]:
        project = Project(title=body.title.strip(), description=body.description)
        if not project.title:
            raise HTTPException(status_code=422, detail="Project title cannot be blank")
        db.add(project)
        db.flush()
        add_audit(db, "user", "PROJECT_CREATED", "project", project.id)
        db.commit()
        db.refresh(project)
        logger.info("project_created id=%s", project.id)
        return jsonable_encoder(_project_data(project))

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, db: DBSession) -> dict[str, Any]:
        return jsonable_encoder(_project_data(require_project(db, project_id)))

    @app.post("/api/v1/projects/{project_id}/assets", status_code=201)
    async def upload_asset(
        project_id: str,
        db: DBSession,
        asset_kind: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        project = require_project(db, project_id)
        filename, content, content_type = await read_verified_upload(file, asset_kind, settings.max_upload_bytes)
        digest = sha256_bytes(content)
        duplicate = db.scalar(select(Asset).where(Asset.project_id == project.id, Asset.sha256 == digest))
        if duplicate:
            return jsonable_encoder(_asset_data(duplicate))
        storage_key = store_asset_file(actual_asset_root, digest, content)
        asset = Asset(
            project_id=project.id,
            filename=filename,
            kind=asset_kind,
            content_type=content_type,
            sha256=digest,
            storage_path=storage_key,
            byte_size=len(content),
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
            {"kind": asset.kind, "sha256": digest, "byte_size": len(content)},
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

    @app.post("/api/v1/assets/{asset_id}/approve")
    def approve_asset(asset_id: str, db: DBSession) -> dict[str, Any]:
        asset = db.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        asset.approved = True
        add_audit(db, "user", "ASSET_APPROVED", "asset", asset.id, {"sha256": asset.sha256})
        db.commit()
        db.refresh(asset)
        return jsonable_encoder(_asset_data(asset))

    @app.post("/api/v1/assets/{asset_id}/lock")
    def lock_asset(asset_id: str, db: DBSession) -> dict[str, Any]:
        asset = db.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if not asset.approved:
            raise HTTPException(status_code=409, detail="A user must approve an asset before locking it")
        asset.locked = True
        add_audit(db, "user", "ASSET_LOCKED", "asset", asset.id, {"sha256": asset.sha256})
        db.commit()
        db.refresh(asset)
        return jsonable_encoder(_asset_data(asset))

    @app.put("/api/v1/projects/{project_id}/style/draft")
    def save_style_draft(project_id: str, body: StyleBible, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        update_style_draft(db, project, body)
        db.commit()
        return {"project_id": project.id, "style_draft": project.style_draft, "frozen": False}

    @app.post("/api/v1/projects/{project_id}/style/freeze", status_code=201)
    def freeze_project_style(project_id: str, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        style = freeze_style(db, project)
        db.commit()
        db.refresh(style)
        return jsonable_encoder(_style_data(style))

    @app.get("/api/v1/projects/{project_id}/styles")
    def list_styles(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        styles = db.scalars(
            select(StyleVersion).where(StyleVersion.project_id == project_id).order_by(StyleVersion.version)
        ).all()
        return jsonable_encoder([_style_data(style) for style in styles])

    @app.post("/api/v1/projects/{project_id}/shots", status_code=201)
    def create_shot(project_id: str, body: CreateShot, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        if not project.frozen_style_version_id:
            raise HTTPException(status_code=409, detail="Freeze the Style Bible before creating production shots")
        validate_shot_style(db, project_id, body.spec)
        if body.spec.required_asset_ids:
            required = list(
                db.scalars(
                    select(Asset).where(
                        Asset.project_id == project_id,
                        Asset.id.in_(body.spec.required_asset_ids),
                        Asset.approved.is_(True),
                        Asset.locked.is_(True),
                    )
                ).all()
            )
            if len(required) != len(set(body.spec.required_asset_ids)):
                raise HTTPException(status_code=409, detail="Shot requires user-approved, locked assets")
        shot = Shot(project_id=project_id, name=body.name, spec=body.spec.model_dump(mode="json"))
        db.add(shot)
        db.flush()
        add_audit(db, "user", "SHOT_CREATED", "shot", shot.id, {"revision": shot.revision})
        db.commit()
        return {
            "id": shot.id,
            "project_id": shot.project_id,
            "name": shot.name,
            "revision": shot.revision,
            "spec": shot.spec,
        }

    @app.get("/api/v1/projects/{project_id}/shots")
    def list_shots(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        shots = db.scalars(select(Shot).where(Shot.project_id == project_id).order_by(Shot.created_at)).all()
        return [
            {
                "id": shot.id,
                "project_id": shot.project_id,
                "name": shot.name,
                "revision": shot.revision,
                "spec": shot.spec,
            }
            for shot in shots
        ]

    @app.post("/api/v1/shots/{shot_id}/revise")
    def revise_shot(shot_id: str, body: ShotSpec, db: DBSession) -> dict[str, Any]:
        shot = db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        project = require_project(db, shot.project_id)
        validate_shot_style(db, project.id, body)
        if body.required_asset_ids:
            approved_count = len(
                db.scalars(
                    select(Asset.id).where(
                        Asset.project_id == project.id,
                        Asset.id.in_(body.required_asset_ids),
                        Asset.approved.is_(True),
                        Asset.locked.is_(True),
                    )
                ).all()
            )
            if approved_count != len(set(body.required_asset_ids)):
                raise HTTPException(status_code=409, detail="Shot requires user-approved, locked assets")
        old_jobs = db.scalars(select(Job).where(Job.shot_id == shot.id)).all()
        old_ids = [job.id for job in old_jobs]
        shot.revision += 1
        shot.spec = body.model_dump(mode="json")
        for job in old_jobs:
            job.stale = True
            job.failure_code = "SHOT_REVISION_REPLACED"
            if job.status not in {"FAILED", "NEEDS_REVIEW"}:
                job.status = "NEEDS_REVIEW"
        affected = mark_stale_descendants(db, old_ids)
        add_audit(db, "user", "SHOT_REVISED", "shot", shot.id, {"revision": shot.revision})
        db.commit()
        return {
            "id": shot.id,
            "revision": shot.revision,
            "invalidated_job_ids": sorted(affected),
            "stale_source_job_ids": old_ids,
            "spec": shot.spec,
        }

    @app.post("/api/v1/projects/{project_id}/jobs", status_code=201)
    def create_job(project_id: str, body: CreateJob, db: DBSession) -> dict[str, Any]:
        project = require_project(db, project_id)
        shot = db.get(Shot, body.shot_id)
        if not shot or shot.project_id != project_id:
            raise HTTPException(status_code=404, detail="Shot not found in this project")
        request_data, _assets = make_render_request(db, project, shot, body)
        parents, initial_status = dependencies_for_new_job(db, project_id, body.depends_on)
        job = Job(
            project_id=project_id,
            shot_id=shot.id,
            shot_revision=shot.revision,
            style_version_id=request_data.style_version_id,
            request=request_data.model_dump(mode="json"),
            idempotency_key=request_data.idempotency_key,
            status=initial_status,
            provider="mock",
            mock_scenario=body.mock_scenario.value,
        )
        db.add(job)
        db.flush()
        for parent in parents:
            db.add(JobDependency(parent_job_id=parent.id, child_job_id=job.id))
        add_audit(
            db,
            "user",
            "MOCK_JOB_CREATED",
            "job",
            job.id,
            {
                "provider": "mock",
                "dependency_count": len(parents),
                "style_hash": request_data.style_hash,
            },
        )
        db.commit()
        db.refresh(job)
        logger.info("mock_job_created id=%s project_id=%s status=%s", job.id, project_id, job.status)
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
        return jsonable_encoder(_job_data(db, job))

    @app.post("/api/v1/jobs/{job_id}/pause")
    def pause_job(job_id: str, db: DBSession) -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"PLANNED", "READY", "QUEUED"}:
            raise HTTPException(status_code=409, detail="Only unsubmitted jobs can be paused")
        job.paused = True
        add_audit(db, "user", "JOB_PAUSED", "job", job.id)
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
        add_audit(db, "user", "JOB_RESUMED", "job", job.id)
        db.commit()
        return jsonable_encoder(_job_data(db, job))

    @app.post("/api/v1/jobs/{job_id}/repair")
    def repair_job(job_id: str, plan: RepairPlan, db: DBSession, mock_scenario: str = "success") -> dict[str, Any]:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "REPAIR_PENDING":
            raise HTTPException(status_code=409, detail="A failed mock attempt must be diagnosed before repair")
        if job.semantic_retries >= min(job.max_semantic_retries, plan.max_additional_attempts):
            raise HTTPException(status_code=409, detail="Semantic repair budget is exhausted")
        if plan.human_review_required or plan.action.value == "wait_for_human":
            job.last_repair_plan = plan.model_dump(mode="json")
            transition(job, "NEEDS_REVIEW")
            job.failure_code = "HUMAN_REVIEW_REQUIRED"
            add_audit(db, "user", "REPAIR_ESCALATED", "job", job.id, {"action": plan.action.value})
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
        add_audit(
            db,
            "user",
            "MOCK_REPAIR_QUEUED",
            "job",
            job.id,
            {"semantic_retry": job.semantic_retries, "action": plan.action.value},
        )
        db.commit()
        return jsonable_encoder(_job_data(db, job))

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
                    "result": report.result,
                    "is_mock": report.is_mock,
                    "report": report.report,
                    "created_at": report.created_at,
                }
                for report in reports
            ]
        )

    @app.get("/api/v1/projects/{project_id}/events")
    def list_events(project_id: str, db: DBSession) -> list[dict[str, Any]]:
        require_project(db, project_id)
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_id == project_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(100)
        ).all()
        return jsonable_encoder(
            [
                {
                    "id": row.id,
                    "actor": row.actor,
                    "event_type": row.event_type,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )

    return app


app = create_app()

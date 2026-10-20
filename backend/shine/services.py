from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.models import Asset, AuditEvent, Job, JobDependency, Project, Shot, StyleVersion
from shine.schemas import CreateJob, ReferenceManifestItem, RenderRequest, ShotSpec, StyleBible
from shine.security import MAX_REFERENCE_COUNTS, sha256_bytes

JOB_TRANSITIONS = {
    "PLANNED": {"QUEUED", "NEEDS_REVIEW"},
    "WAITING_ASSET": {"PLANNED", "NEEDS_REVIEW"},
    "READY": {"QUEUED", "NEEDS_REVIEW"},
    "QUEUED": {"SUBMITTING", "NEEDS_REVIEW"},
    "SUBMITTING": {"RUNNING", "FAILED", "REPAIR_PENDING", "NEEDS_REVIEW"},
    "RUNNING": {"QC_PENDING", "FAILED", "NEEDS_REVIEW"},
    "QC_PENDING": {"PASSED", "REPAIR_PENDING", "NEEDS_REVIEW", "FAILED"},
    "PASSED": {"NEEDS_REVIEW"},
    "REPAIR_PENDING": {"READY", "FAILED", "NEEDS_REVIEW"},
    "NEEDS_REVIEW": set(),
    "FAILED": set(),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def add_audit(
    db: Session,
    actor: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor=actor,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )
    )


def transition(job: Job, next_status: str) -> None:
    if next_status not in JOB_TRANSITIONS.get(job.status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Illegal job transition: {job.status} -> {next_status}",
        )
    job.status = next_status


def require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def store_asset_file(root: Path, digest: str, content: bytes) -> str:
    relative = Path("assets") / digest[:2] / digest
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.partial")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return relative.as_posix()


def update_style_draft(db: Session, project: Project, style: StyleBible) -> None:
    project.style_draft = style.model_dump(mode="json")
    add_audit(db, "user", "STYLE_DRAFT_UPDATED", "project", project.id)


def freeze_style(db: Session, project: Project) -> StyleVersion:
    if not project.style_draft:
        raise HTTPException(status_code=409, detail="Save all seven style dimensions before freezing")
    snapshot = StyleBible.model_validate(project.style_draft).model_dump(mode="json")
    digest = sha256_bytes(canonical_json(snapshot).encode("utf-8"))
    version_number = max((row.version for row in project.style_versions), default=0) + 1
    frozen = StyleVersion(
        project_id=project.id,
        version=version_number,
        snapshot=snapshot,
        sha256=digest,
        approved_by="user",
    )
    db.add(frozen)
    db.flush()
    project.frozen_style_version_id = frozen.id
    add_audit(db, "user", "STYLE_FROZEN", "style_version", frozen.id, {"sha256": digest})
    return frozen


def validate_shot_style(db: Session, project_id: str, spec: ShotSpec) -> StyleVersion:
    style = db.get(StyleVersion, spec.style_version_id)
    if not style or style.project_id != project_id:
        raise HTTPException(status_code=422, detail="Shot must reference a frozen style version in this project")
    return style


def make_render_request(
    db: Session,
    project: Project,
    shot: Shot,
    body: CreateJob,
) -> tuple[RenderRequest, list[Asset]]:
    if not project.frozen_style_version_id:
        raise HTTPException(status_code=409, detail="Freeze the Style Bible before creating a render job")
    spec = ShotSpec.model_validate(shot.spec)
    style = validate_shot_style(db, project.id, spec)
    if len({ref.asset_id for ref in body.references}) != len(body.references):
        raise HTTPException(status_code=422, detail="Each asset may appear only once in a reference manifest")

    assets = (
        {
            asset.id: asset
            for asset in db.scalars(
                select(Asset).where(
                    Asset.project_id == project.id,
                    Asset.id.in_([r.asset_id for r in body.references]),
                )
            ).all()
        }
        if body.references
        else {}
    )
    if len(assets) != len(body.references):
        raise HTTPException(status_code=422, detail="Every render reference must belong to this project")
    if not set(spec.required_asset_ids).issubset(assets):
        raise HTTPException(status_code=422, detail="Render references are missing assets required by the shot")

    counts = {kind: 0 for kind in MAX_REFERENCE_COUNTS}
    label_counts = {"subject": 0, "picture": 0, "video": 0, "audio": 0}
    manifest: list[ReferenceManifestItem] = []
    for ref in body.references:
        asset = assets[ref.asset_id]
        if not asset.approved or not asset.locked:
            raise HTTPException(status_code=409, detail="Render references must be user-approved and locked")
        expected_kind = "image" if ref.use_as.value in {"subject", "picture"} else ref.use_as.value
        if asset.kind != expected_kind:
            raise HTTPException(status_code=422, detail=f"Reference {ref.asset_id} has an incompatible media type")
        counts[asset.kind] += 1
        label_counts[ref.use_as.value] += 1
        limit = MAX_REFERENCE_COUNTS[asset.kind]
        if counts[asset.kind] > limit:
            raise HTTPException(status_code=422, detail=f"H3 input limit exceeded for {asset.kind} references")
        label_name = {
            "subject": "Subject",
            "picture": "Picture",
            "video": "Video",
            "audio": "Audio",
        }[ref.use_as.value]
        manifest.append(
            ReferenceManifestItem(
                label=f"<{label_name} {label_counts[ref.use_as.value]}>",
                asset_id=asset.id,
                sha256=asset.sha256,
                storage_key=asset.storage_path,
                content_type=asset.content_type,
                use_as=ref.use_as,
            )
        )

    idempotency_key = uuid4().hex
    render_request = RenderRequest(
        idempotency_key=idempotency_key,
        shot_id=shot.id,
        shot_revision=shot.revision,
        style_version_id=style.id,
        style_hash=style.sha256,
        reference_manifest=manifest,
        shot_spec=spec,
        style_snapshot=StyleBible.model_validate(style.snapshot),
        expected_duration_s=spec.duration_target_s,
    )
    return render_request, list(assets.values())


def dependencies_for_new_job(db: Session, project_id: str, dependency_ids: list[str]) -> tuple[list[Job], str]:
    if len(set(dependency_ids)) != len(dependency_ids):
        raise HTTPException(status_code=422, detail="Job dependencies must be unique")
    parents = list(db.scalars(select(Job).where(Job.id.in_(dependency_ids))).all()) if dependency_ids else []
    if len(parents) != len(dependency_ids) or any(parent.project_id != project_id for parent in parents):
        raise HTTPException(status_code=422, detail="Job dependencies must belong to this project")
    if any(parent.status in {"FAILED", "NEEDS_REVIEW"} or parent.stale for parent in parents):
        raise HTTPException(status_code=409, detail="A dependency is blocked or stale")
    status = "QUEUED" if all(parent.status == "PASSED" for parent in parents) else "PLANNED"
    return parents, status


def mark_stale_descendants(db: Session, starting_job_ids: list[str]) -> set[str]:
    """Mark only jobs that consume revised upstream work; independent shots remain untouched."""
    affected: set[str] = set()
    frontier = list(starting_job_ids)
    while frontier:
        parent_id = frontier.pop()
        child_ids = db.scalars(select(JobDependency.child_job_id).where(JobDependency.parent_job_id == parent_id)).all()
        for child_id in child_ids:
            if child_id in affected:
                continue
            affected.add(child_id)
            frontier.append(child_id)
    for job_id in affected:
        job = db.get(Job, job_id)
        if not job:
            continue
        job.stale = True
        job.failure_code = "DEPENDENCY_INVALIDATED"
        if job.status in {"PLANNED", "READY", "QUEUED", "PASSED", "REPAIR_PENDING"}:
            job.status = "NEEDS_REVIEW"
        add_audit(db, "orchestrator", "JOB_DEPENDENCY_INVALIDATED", "job", job.id)
    return affected


def job_fingerprint(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()

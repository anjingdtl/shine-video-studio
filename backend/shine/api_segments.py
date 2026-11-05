"""Explicit re-takes of real H3 outputs on the existing series/job ledger."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import literal_column, select, text

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.comfyops.models import ResourceLease
from shine.comfyops.production_series import create_series
from shine.comfyops.runtime_configuration import resolve_runtime
from shine.h3.rulebook import digest
from shine.models import (
    AgentTaskRecord,
    AppSetting,
    Job,
    JobDependency,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    QualityGate,
    Shot,
    utcnow,
)
from shine.schemas import APIModel, CreateJob, RenderRequest
from shine.services import add_audit, mark_stale_descendants, require_project

router = APIRouter(prefix="/api/v1", tags=["generation segments"])
ROLE = "segment_regenerate"
ASSEMBLY_ROLE = "segment_assembly"


class SegmentAction(APIModel):
    source_job_id: str
    expected_shot_revision: int = Field(ge=1)


def _records(db, project_id, role=ROLE):
    return list(db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == role)))


def _root(db, project_id, series_id=None):
    excluded = {r.trace.get("series_id") for role in (ROLE, ASSEMBLY_ROLE)
                for r in _records(db, project_id, role)}
    roots = list(db.scalars(select(ProductionSeries).where(ProductionSeries.project_id == project_id)
                           .order_by(ProductionSeries.created_at.desc())))
    root = next((r for r in roots if r.id not in excluded and (not series_id or r.id == series_id)), None)
    if root is None:
        raise HTTPException(404, "GENERATION_SERIES_NOT_FOUND")
    return root


def _selection(db, root):
    row = db.get(AppSetting, f"assembly-selection:{root.id}")
    return dict(row.value) if row else {i.shot_id: i.job_id for i in root.items}


def accepted_job(db, job, data_dir=None):
    """The one acceptance definition: latest final gate really passed.

    Shared by the assembly selection and the long-script section continuity
    state, so "the previous section really ended like this" is never a weaker
    claim than "this media may be assembled".
    """
    if not job or job.status != "PASSED" or job.stale or job.provider != "comfyui" or not job.artifact_id:
        return False
    final = db.scalar(select(QualityGate).where(QualityGate.job_id == job.id, QualityGate.gate_name == "final")
                      .order_by(QualityGate.created_at.desc(), literal_column("quality_gates.rowid").desc()).limit(1))
    artifact = db.get(MediaArtifact, job.artifact_id)
    latest = max(job.attempts, key=lambda a: a.attempt_no, default=None)
    valid = bool(final and final.status == "passed" and not final.is_mock and latest
                and final.attempt_id == latest.id and artifact and not artifact.is_mock
                and final.artifact_id == artifact.id and artifact.job_id == job.id
                and artifact.project_id == job.project_id and artifact.attempt_id == latest.id
                and artifact.sha256 == latest.output_sha256)
    if valid and data_dir:
        import hashlib

        from shine.media.artifacts import safe_asset_path
        try:
            content = safe_asset_path(data_dir, artifact.storage_path).read_bytes()
            valid = hashlib.sha256(content).hexdigest() == artifact.sha256
        except (OSError, ValueError):
            valid = False
    return valid


def _impact(db, source_job_id):
    seen, todo = set(), [source_job_id]
    while todo:
        children = db.scalars(select(JobDependency.child_job_id).where(JobDependency.parent_job_id == todo.pop()))
        for child in children:
            if child not in seen:
                seen.add(child)
                todo.append(child)
    return sorted(seen)


def _blocker(db, job, shot):
    if not job:
        return "GENERATION_NOT_STARTED"
    if not shot or job.shot_revision != shot.revision or job.stale:
        return "SHOT_REVISION_REPLACED"
    if job.status != "PASSED":
        return "GENERATION_RUNNING_OR_RECONCILIATION_REQUIRED"
    if db.scalar(select(Job.id).where(Job.shot_id == job.shot_id, Job.id != job.id,
                    Job.status.in_(["QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING", "NEEDS_REVIEW"]),
                    Job.stale.is_(False))):
        return "GENERATION_RUNNING_OR_RECONCILIATION_REQUIRED"
    if db.scalar(select(ResourceLease.id).where(ResourceLease.status == "RECONCILE_REQUIRED")):
        return "GPU_LEASE_RECONCILIATION_REQUIRED"
    for series in db.scalars(select(ProductionSeries).join(ProductionSeriesItem).where(
            ProductionSeriesItem.job_id == job.id)):
        lease = db.get(ResourceLease, series.lease_id) if series.lease_id else None
        if lease and lease.status != "RELEASED":
            return "GPU_LEASE_RECONCILIATION_REQUIRED"
    return None


def _editorial_cuts(db, shot_id):
    from shine.models import AgentArtifact

    for receipt in db.scalars(select(AppSetting).where(AppSetting.key.like("film-apply:%"))):
        ids = receipt.value.get("shot_ids", [])
        if shot_id not in ids:
            continue
        camera = db.get(AgentArtifact, receipt.value.get("source_artifact_id"))
        if not camera or camera.sha256 != digest(camera.payload_json):
            continue
        index = ids.index(shot_id)
        camera_shots = camera.payload_json.get("shots", [])
        if index >= len(camera_shots):
            continue
        intent_id = camera_shots[index].get("intent_id")
        for parent_id in camera.input_revision_ids:
            direction = db.get(AgentArtifact, parent_id)
            if direction and direction.kind == "DirectorialPlan" and direction.sha256 == digest(direction.payload_json):
                intent = next((s for s in direction.payload_json.get("shots", []) if s.get("id") == intent_id), None)
                return intent.get("cuts", []) if intent else []
    return []


@router.get("/projects/{project_id}/generation-segments")
def segments(project_id: str, db: DBSession, series_id: str | None = None):
    require_project(db, project_id)
    try:
        root = _root(db, project_id, series_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"series_id": None, "segments": []}
        raise
    chosen = _selection(db, root)
    records = _records(db, project_id)
    rows = []
    for item in root.items:
        shot = db.get(Shot, item.shot_id)
        job = db.get(Job, chosen.get(item.shot_id)) if chosen.get(item.shot_id) else None
        replacement = next((r for r in records if job and r.trace.get("source_job_id") == job.id), None)
        pending = db.get(Job, replacement.trace["job_id"]) if replacement else None
        attempt = max(job.attempts, key=lambda a: a.attempt_no, default=None) if job else None
        spec = job.request.get("shot_spec", {}) if job else (shot.spec if shot else {})
        reason = _blocker(db, job, shot)
        if replacement:
            reason = "RETAKE_AWAITS_SELECTION_OR_REVIEW"
        rows.append({"shot_id": item.shot_id, "position": item.position + 1,
                     "story_beat": spec.get("story_beat"), "duration_s": spec.get("duration_target_s"),
                     "editorial_cuts": _editorial_cuts(db, item.shot_id),
                     "shot_revision": shot.revision if shot else None,
                     "job_id": job.id if job else None, "attempt_no": attempt.attempt_no if attempt else None,
                     "status": job.status if job else item.status, "artifact_id": job.artifact_id if job else None,
                     "blocker": reason, "can_regenerate": reason is None,
                     "dependent_job_ids": _impact(db, job.id) if job else [],
                     "replacement_job_id": pending.id if pending else None,
                     "replacement_status": pending.status if pending else None,
                     "can_select_replacement": accepted_job(db, pending)})
    return {"series_id": root.id, "segments": rows}


def _record(db, project_id, role, identity, payload):
    row = AgentTaskRecord(project_id=project_id, role_id=role, input_hash=identity, request_identity=identity,
                          asset_manifest_hash=identity, rulepack_id="local-production-action", rulepack_hash=identity,
                          model_profile_id="local-production-service", model_id="H3PromptCompiler",
                          schema_version="1.0", status="COMPLETED", trace=payload)
    db.add(row)
    return row


@router.post("/projects/{project_id}/generation-segments/{shot_id}/regenerate", status_code=202)
def regenerate(project_id: str, shot_id: str, body: SegmentAction, actor: Operator, request: Request, db: DBSession):
    db.execute(text("BEGIN IMMEDIATE"))
    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    source = db.get(Job, body.source_job_id)
    identity = digest({"project": project_id, "shot": shot_id, "source": body.source_job_id,
                       "revision": body.expected_shot_revision, "action": ROLE})
    prior = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
    if prior:
        return {**prior.trace, "replayed": True}
    if not source or source.project_id != project_id or source.shot_id != shot_id or source.provider != "comfyui":
        raise HTTPException(409, "GENERATION_SOURCE_INVALID")
    if not shot or shot.revision != body.expected_shot_revision:
        raise HTTPException(409, "SHOT_REVISION_REPLACED")
    reason = _blocker(db, source, shot)
    if reason:
        raise HTTPException(409, reason)
    spec = RenderRequest.model_validate(source.request)
    output = {"width": spec.output_width, "height": spec.output_height, "fps": 24} if spec.output_width else None
    series, job = create_series(db, project, [shot], body=CreateJob(
        shot_id=shot.id, provider="comfyui", workflow_profile_id=source.workflow_profile_id,
        generation_mode=source.generation_mode, self_review_scope=spec.self_review_scope,
        basic_review_resolution=spec.basic_review_resolution, output=output,
    ), settings=resolve_runtime(db, request.app.state.settings), offline=request.app.state.settings.offline_simulation)
    binding_keys = ("style_version_id", "style_hash", "rulepack_id", "rulepack_hash", "prompt_skill_hash",
                    "workflow_profile_hash", "model_checkpoint_id", "reference_manifest", "shot_spec")
    if any(source.request.get(k) != job.request.get(k) for k in binding_keys):
        raise HTTPException(409, "GENERATION_RENDER_BINDINGS_CHANGED")
    job.request_revision = source.request_revision + 1
    payload = {"source_job_id": source.id, "job_id": job.id, "series_id": series.id,
               "shot_revision": shot.revision, "dependent_job_ids": _impact(db, source.id),
               "automatic_assembly": False}
    _record(db, project_id, ROLE, identity, payload)
    add_audit(db, actor, "GENERATION_SEGMENT_RETAKE_REQUESTED", "job", job.id, payload, project_id=project_id)
    db.commit()
    return {**payload, "replayed": False}


class SelectSegment(APIModel):
    source_job_id: str
    replacement_job_id: str


@router.post("/projects/{project_id}/generation-segments/{series_id}/select", status_code=202)
def select_replacement(project_id: str, series_id: str, body: SelectSegment, actor: Operator,
                       request: Request, db: DBSession):
    db.execute(text("BEGIN IMMEDIATE"))
    root = _root(db, project_id, series_id)
    identity = digest({"root": root.id, "source": body.source_job_id, "replacement": body.replacement_job_id})
    prior = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
    if prior:
        return {**prior.trace, "replayed": True}
    replacement = db.get(Job, body.replacement_job_id)
    recorded = next((r for r in _records(db, project_id) if r.trace.get("source_job_id") == body.source_job_id
                     and r.trace.get("job_id") == body.replacement_job_id), None)
    data_dir = request.app.state.settings.data_dir
    if not recorded or not accepted_job(db, replacement, data_dir):
        raise HTTPException(409, "GENERATION_REPLACEMENT_NOT_ACCEPTED")
    chosen = _selection(db, root)
    if chosen.get(replacement.shot_id) != body.source_job_id:
        raise HTTPException(409, "GENERATION_ASSEMBLY_SELECTION_STALE")
    chosen[replacement.shot_id] = replacement.id
    affected = sorted(mark_stale_descendants(db, [body.source_job_id], project_id=project_id))
    key = f"assembly-selection:{root.id}"
    setting = db.get(AppSetting, key)
    if setting:
        setting.value = chosen
    else:
        db.add(AppSetting(key=key, value=chosen))
    ready = all(accepted_job(db, db.get(Job, chosen.get(i.shot_id)), data_dir) for i in root.items)
    assembly_id = None
    if ready:
        assembled = ProductionSeries(project_id=project_id, status="COMPLETED", provider="comfyui",
            shot_count=len(root.items), completed_count=len(root.items), finalization_state="RELEASED",
            workflow_profile_id=root.workflow_profile_id, generation_mode=root.generation_mode,
            output_spec=root.output_spec, edit_intent_artifact_id=root.edit_intent_artifact_id, finished_at=utcnow())
        db.add(assembled)
        db.flush()
        for original in root.items:
            db.add(ProductionSeriesItem(
                series_id=assembled.id, shot_id=original.shot_id, position=original.position,
                job_id=chosen[original.shot_id], status="PASSED", continuity_mode=original.continuity_mode))
        db.flush()
        from shine.media.delivery_engine import enqueue_auto_delivery
        order = enqueue_auto_delivery(db, assembled)
        if order:
            order.origin = "segment_revision"
            order.version = 2 + sum(r.trace.get("root_series_id") == root.id
                                    for r in _records(db, project_id, ASSEMBLY_ROLE))
        assembly_id = assembled.id
    payload = {"series_id": assembly_id, "root_series_id": root.id, "selection": chosen,
               "invalidated_job_ids": affected, "assembly_pending_review": not ready}
    _record(db, project_id, ASSEMBLY_ROLE, identity, payload)
    add_audit(db, actor, "GENERATION_ASSEMBLY_VERSION_SELECTED", "production_series", root.id,
              payload, project_id=project_id)
    db.commit()
    return {**payload, "replayed": False}

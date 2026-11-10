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
    AuditEvent,
    ExternalJobReconciliation,
    Job,
    JobAttempt,
    JobDependency,
    MediaArtifact,
    ProductionSeries,
    ProductionSeriesItem,
    QualityGate,
    Shot,
    utcnow,
)
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
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


def _proven_pre_submit_mapping_failure(db, job):
    """A mapping rejection is ignorable only with the same proof used by Run recovery."""
    if (job.status != "NEEDS_REVIEW" or job.provider != "comfyui"
            or job.failure_code != "PENDING_WORKFLOW_MAPPING" or job.artifact_id or job.provider_job_id):
        return False
    attempts = list(db.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id)).all())
    if not attempts or any(
        attempt.status != "FAILED" or attempt.failure_code != "PENDING_WORKFLOW_MAPPING"
        or attempt.provider_job_id or attempt.submit_ack_at or attempt.prompt_graph_sha256
        or attempt.output_path or attempt.output_sha256 or attempt.output_bytes
        or db.get(AppSetting, "renderer-activation:" + attempt.id) is not None
        for attempt in attempts
    ):
        return False
    mapping_proof = db.scalar(select(AuditEvent.id).where(
        AuditEvent.project_id == job.project_id,
        AuditEvent.entity_type == "job",
        AuditEvent.entity_id == job.id,
        AuditEvent.event_type == "COMFY_WORKFLOW_MAPPING_PENDING",
    ).limit(1))
    submitted = db.scalar(select(AuditEvent.id).where(
        AuditEvent.project_id == job.project_id,
        AuditEvent.entity_type == "job",
        AuditEvent.entity_id == job.id,
        AuditEvent.event_type == "COMFY_SUBMIT_INTENT",
    ).limit(1))
    artifact = db.scalar(select(MediaArtifact.id).where(MediaArtifact.job_id == job.id).limit(1))
    leases = list(db.scalars(select(ResourceLease).where(ResourceLease.owner == "h3:" + job.id)).all())
    return bool(mapping_proof and not submitted and not artifact
                and all(lease.status in {"RELEASED", "WAITING"} for lease in leases))


def _blocker(db, job, shot):
    if not job:
        return "GENERATION_NOT_STARTED"
    if not shot or job.shot_revision != shot.revision or job.stale:
        return "SHOT_REVISION_REPLACED"
    if job.status != "PASSED":
        return "GENERATION_RUNNING_OR_RECONCILIATION_REQUIRED"
    unresolved = db.scalars(select(Job).where(
        Job.shot_id == job.shot_id,
        Job.id != job.id,
        Job.status.in_(["QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING", "NEEDS_REVIEW"]),
        Job.stale.is_(False),
    )).all()
    if any(not _proven_pre_submit_mapping_failure(db, candidate) for candidate in unresolved):
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


def _segment_row(db, project_id, shot_id, position, *, root=None, episode_position=None,
                 episode_title=None, batch_status=None):
    shot = db.get(Shot, shot_id)
    item = next((row for row in root.items if row.shot_id == shot_id), None) if root else None
    chosen = _selection(db, root) if root else {}
    job = db.get(Job, chosen.get(shot_id)) if chosen.get(shot_id) else None
    records = _records(db, project_id)
    replacement = next((r for r in records if job and r.trace.get("source_job_id") == job.id), None)
    pending = db.get(Job, replacement.trace["job_id"]) if replacement and replacement.trace.get("job_id") else None
    attempt = max(job.attempts, key=lambda a: a.attempt_no, default=None) if job else None
    spec = job.request.get("shot_spec", {}) if job else (shot.spec if shot else {})
    reason = _blocker(db, job, shot) if job else ("GENERATION_NOT_STARTED" if shot else "SHOT_NOT_FOUND")
    if replacement:
        reason = "RETAKE_AWAITS_SELECTION_OR_REVIEW"
    return {
        "shot_id": shot_id,
        "position": position,
        "episode_position": episode_position,
        "episode_title": episode_title,
        "series_id": root.id if root else None,
        "story_beat": spec.get("story_beat"),
        "duration_s": spec.get("duration_target_s"),
        "editorial_cuts": _editorial_cuts(db, shot_id),
        "shot_revision": shot.revision if shot else None,
        "job_id": job.id if job else None,
        "attempt_no": attempt.attempt_no if attempt else None,
        "status": job.status if job else (item.status if item else batch_status or "PLANNED"),
        "artifact_id": job.artifact_id if job else None,
        "blocker": reason,
        "can_regenerate": reason is None,
        "dependent_job_ids": _impact(db, job.id) if job else [],
        "replacement_job_id": pending.id if pending else None,
        "replacement_status": pending.status if pending else None,
        "can_select_replacement": accepted_job(db, pending),
    }


def _run_segments(db, run):
    episodes = list(db.scalars(select(ProductionRunEpisode).where(
        ProductionRunEpisode.run_id == run.id).order_by(ProductionRunEpisode.position)).all())
    batches = list(db.scalars(select(ProductionRunBatch).where(
        ProductionRunBatch.run_id == run.id).order_by(ProductionRunBatch.position)).all())
    batch_for_shot = {}
    for batch in batches:
        for shot_id in batch.shot_ids:
            batch_for_shot.setdefault(shot_id, batch)
    series_by_id = {series.id: series for series in db.scalars(select(ProductionSeries).where(
        ProductionSeries.id.in_([batch.series_id for batch in batches if batch.series_id]))).all()}

    rows = []
    for episode_position, episode in enumerate(episodes, start=1):
        for shot_id in episode.shot_ids:
            batch = batch_for_shot.get(shot_id)
            root = series_by_id.get(batch.series_id) if batch and batch.series_id else None
            rows.append(_segment_row(db, run.project_id, shot_id, len(rows) + 1, root=root,
                episode_position=episode_position, episode_title=episode.title,
                batch_status=batch.status if batch else ("QUEUED" if run.status in {
                    "QUEUED", "RUNNING", "PAUSED", "PAUSE_REQUESTED", "RECONCILING"} else "PLANNED")))
    return rows


@router.get("/projects/{project_id}/generation-segments")
def segments(project_id: str, db: DBSession, series_id: str | None = None, run_id: str | None = None):
    require_project(db, project_id)
    if series_id:
        try:
            root = _root(db, project_id, series_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"series_id": None, "run_id": None, "segments": []}
            raise
        rows = [_segment_row(db, project_id, item.shot_id, item.position + 1, root=root,
                             batch_status=item.status) for item in root.items]
        return {"series_id": root.id, "run_id": None, "segments": rows}

    if run_id:
        run = db.get(ProductionRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(404, "PRODUCTION_RUN_NOT_FOUND")
    else:
        run = db.scalars(select(ProductionRun).where(ProductionRun.project_id == project_id)
                         .order_by(ProductionRun.created_at.desc()).limit(1)).first()
    if run:
        return {"series_id": None, "run_id": run.id, "segments": _run_segments(db, run)}

    try:
        root = _root(db, project_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"series_id": None, "run_id": None, "segments": []}
        raise
    rows = [_segment_row(db, project_id, item.shot_id, item.position + 1, root=root,
                         batch_status=item.status) for item in root.items]
    return {"series_id": root.id, "run_id": None, "segments": rows}


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
    prior_unresolved = db.scalars(select(Job).where(
        Job.shot_id == shot_id,
        Job.id != source.id,
        Job.status.in_(["QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING", "NEEDS_REVIEW"]),
        Job.stale.is_(False),
    )).all()
    for historical in prior_unresolved:
        if not _proven_pre_submit_mapping_failure(db, historical):
            continue
        reconciled = db.scalar(select(ExternalJobReconciliation.id).where(
            ExternalJobReconciliation.job_id == historical.id,
            ExternalJobReconciliation.result == "NOT_SENT_PROVEN",
        ).limit(1))
        if reconciled:
            continue
        evidence = {
            "failure_code": "PENDING_WORKFLOW_MAPPING",
            "attempt_ids": [attempt.id for attempt in historical.attempts],
            "mapping_event": "COMFY_WORKFLOW_MAPPING_PENDING",
            "comfy_submit_intent": False,
            "external_submission": "NOT_SENT_PROVEN",
            "resubmitted": False,
        }
        db.add(ExternalJobReconciliation(
            job_id=historical.id,
            provider_job_id=None,
            action="pre_submit_workflow_mapping",
            result="NOT_SENT_PROVEN",
            evidence=evidence,
        ))
        add_audit(db, actor, "JOB_EXTERNAL_OUTCOME_NOT_SENT_PROVEN", "job", historical.id,
                  evidence, project_id=project_id)
    spec = RenderRequest.model_validate(source.request)
    output = {"width": spec.output_width, "height": spec.output_height, "fps": 24} if spec.output_width else None
    series, job = create_series(db, project, [shot], body=CreateJob(
        shot_id=shot.id, provider="comfyui", workflow_profile_id=source.workflow_profile_id,
        generation_mode=source.generation_mode, self_review_scope=spec.self_review_scope,
        basic_review_resolution=spec.basic_review_resolution, output=output,
    ), settings=resolve_runtime(db, request.app.state.settings), offline=request.app.state.settings.offline_simulation)
    binding_keys = ("style_version_id", "style_hash", "rulepack_id", "rulepack_hash", "prompt_skill_hash",
                    "workflow_profile_hash", "model_checkpoint_id", "reference_manifest", "shot_spec")
    if job is not None:
        if any(source.request.get(k) != job.request.get(k) for k in binding_keys):
            raise HTTPException(409, "GENERATION_RENDER_BINDINGS_CHANGED")
        job.request_revision = source.request_revision + 1
    else:
        # A retake of a continuation shot waits for its source media, so the
        # Worker creates the job.  The masters it will use are compared here.
        source_masters = [r for r in (source.request.get("reference_manifest") or [])
                          if r.get("role") != "continuity_derivative"]
        if {(r.get("asset_id"), r.get("use_as")) for r in source_masters} != {
            (row.asset_id, row.use_as) for row in shot.bindings
        }:
            raise HTTPException(409, "GENERATION_RENDER_BINDINGS_CHANGED")
    payload = {"source_job_id": source.id, "job_id": job.id if job else None,
               "pending_job": job is None, "series_id": series.id,
               "shot_revision": shot.revision, "dependent_job_ids": _impact(db, source.id),
               "automatic_assembly": False}
    _record(db, project_id, ROLE, identity, payload)
    add_audit(db, actor, "GENERATION_SEGMENT_RETAKE_REQUESTED", "job",
               job.id if job else series.id, payload, project_id=project_id)
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
    from shine.media.video_layout import move_adoption, revert_file_moves

    # Move the files before the selection is recorded: the old adopted take
    # goes to history and the replacement into the adopted folder.  A locked
    # or unverifiable file leaves the bytes where they are; the ledger
    # selection still proceeds, so production is never blocked by a cosmetic
    # folder move.
    asset_root = request.app.state.asset_root
    moves: list[tuple[str, str]] = []
    try:
        moves = move_adoption(db, asset_root, shot_id=replacement.shot_id,
                              replacement_job_id=replacement.id)
    except (OSError, ValueError):
        moves = []
    try:
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
                output_spec=root.output_spec, edit_intent_artifact_id=root.edit_intent_artifact_id,
                finished_at=utcnow())
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
    except Exception:
        db.rollback()
        revert_file_moves(asset_root, moves)
        raise
    return {**payload, "replayed": False}
